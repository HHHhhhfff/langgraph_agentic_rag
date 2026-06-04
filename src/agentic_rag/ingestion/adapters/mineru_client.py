from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from agentic_rag.config import Settings


class MinerUClientError(RuntimeError):
    """Base error for MinerU client."""


class MinerURetryableError(MinerUClientError):
    """Retryable MinerU client error."""


@dataclass(slots=True)
class MinerUParseResult:
    """Normalized MinerU parse result."""

    task_id: str
    state: str
    markdown_content: str
    source_url: str | None = None
    raw: dict[str, Any] | None = None
    structured_content: list[Any] | None = None
    assets: dict[str, bytes] | None = None


class MinerUClient:
    """MinerU API client supporting precise and agent modes."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = settings.mineru_base_url.rstrip("/")
        self.timeout = max(10.0, settings.ingestion_timeout_sec)

    def parse_local_file(self, path: str | Path) -> MinerUParseResult:
        file_path = Path(path)
        if not file_path.exists():
            raise MinerUClientError(f"file not found: {file_path}")

        if self.settings.mineru_mode == "agent":
            task_id, upload_url = self._agent_create_file_task(file_path)
            self._upload_file(upload_url, file_path)
            final = self._poll_agent_task(task_id)
            return self._agent_download_markdown(task_id, final)

        task_id, batch_id, upload_url = self._precise_create_upload_task(file_path)
        self._upload_file(upload_url, file_path)
        final = self._poll_precise_batch(batch_id, file_path.name)
        return self._precise_download_result(task_id, final)

    def get_agent_task_state(self, task_id: str) -> str:
        body = self._get_json(f"/api/v1/agent/parse/{task_id}", include_auth=False)
        return str((body.get("data") or {}).get("state") or "")

    def get_precise_batch_state(self, batch_id: str, file_name: str) -> str:
        body = self._get_json(f"/api/v4/extract-results/batch/{batch_id}", include_auth=True)
        return self._extract_precise_state(body, file_name)

    def _headers(self, include_auth: bool = True) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if include_auth and self.settings.mineru_mode == "precise":
            token = self.settings.mineru_api_token.strip()
            if not token:
                raise MinerUClientError("MinerU precise mode requires MINERU_API_TOKEN")
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    def _safe_excerpt(text: str, max_chars: int = 400) -> str:
        return " ".join(text.strip().split())[:max_chars]

    def _post_json(self, path: str, payload: dict[str, Any], include_auth: bool = True) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, headers=self._headers(include_auth=include_auth), json=payload)
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPStatusError as exc:
            raise MinerURetryableError(
                f"HTTP {exc.response.status_code} calling {url}: {self._safe_excerpt(exc.response.text)}"
            ) from exc
        except httpx.HTTPError as exc:
            raise MinerURetryableError(f"HTTP error calling {url}: {exc}") from exc
        except ValueError as exc:
            raise MinerUClientError(f"Invalid JSON from {url}: {exc}") from exc

        code = body.get("code")
        if code not in (0, "0", None):
            msg = body.get("msg") or "unknown error"
            raise MinerUClientError(f"MinerU API error code={code}, msg={msg}, path={path}")
        return body

    def _get_json(self, path: str, include_auth: bool = True) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url, headers=self._headers(include_auth=include_auth))
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPStatusError as exc:
            raise MinerURetryableError(
                f"HTTP {exc.response.status_code} calling {url}: {self._safe_excerpt(exc.response.text)}"
            ) from exc
        except httpx.HTTPError as exc:
            raise MinerURetryableError(f"HTTP error calling {url}: {exc}") from exc
        except ValueError as exc:
            raise MinerUClientError(f"Invalid JSON from {url}: {exc}") from exc

        code = body.get("code")
        if code not in (0, "0", None):
            msg = body.get("msg") or "unknown error"
            raise MinerUClientError(f"MinerU API error code={code}, msg={msg}, path={path}")
        return body

    def _build_precise_payload(self, file_name: str) -> dict[str, Any]:
        file_payload: dict[str, Any] = {"name": file_name}
        if self.settings.mineru_page_range.strip():
            file_payload["page_ranges"] = self.settings.mineru_page_range.strip()
        if self.settings.mineru_is_ocr:
            file_payload["is_ocr"] = True

        payload: dict[str, Any] = {
            "files": [file_payload],
            "model_version": self.settings.mineru_model_version,
            "enable_table": self.settings.mineru_enable_table,
            "enable_formula": self.settings.mineru_enable_formula,
            "language": self.settings.mineru_language,
        }

        extra_formats = [x.strip() for x in self.settings.mineru_extra_formats.split(",") if x.strip()]
        if extra_formats:
            payload["extra_formats"] = extra_formats
        return payload

    def _agent_create_file_task(self, file_path: Path) -> tuple[str, str]:
        payload: dict[str, Any] = {
            "file_name": file_path.name,
            "language": self.settings.mineru_language,
            "enable_table": self.settings.mineru_enable_table,
            "is_ocr": self.settings.mineru_is_ocr,
            "enable_formula": self.settings.mineru_enable_formula,
        }
        if self.settings.mineru_page_range.strip():
            payload["page_range"] = self.settings.mineru_page_range.strip()

        body = self._post_json("/api/v1/agent/parse/file", payload, include_auth=False)
        data = body.get("data") or {}
        task_id = str(data.get("task_id") or "").strip()
        file_url = str(data.get("file_url") or "").strip()
        if not task_id or not file_url:
            raise MinerUClientError("MinerU agent create task missing task_id/file_url")
        return task_id, file_url

    def _precise_create_upload_task(self, file_path: Path) -> tuple[str, str, str]:
        payload = self._build_precise_payload(file_path.name)
        body = self._post_json("/api/v4/file-urls/batch", payload, include_auth=True)
        data = body.get("data") or {}
        batch_id = str(data.get("batch_id") or "").strip()
        urls = data.get("file_urls") or []
        if not batch_id or not isinstance(urls, list) or not urls:
            raise MinerUClientError("MinerU precise create upload task missing batch_id/file_urls")

        upload_url = str(urls[0]).strip()
        task_id = f"batch:{batch_id}:{file_path.name}"
        return task_id, batch_id, upload_url

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=6),
        retry=retry_if_exception_type(MinerURetryableError),
    )
    def _upload_file(self, upload_url: str, file_path: Path) -> None:
        try:
            with file_path.open("rb") as f, httpx.Client(timeout=self.timeout) as client:
                resp = client.put(upload_url, content=f.read(), headers={})
            if resp.status_code not in (200, 201):
                raise MinerURetryableError(
                    f"MinerU upload failed status={resp.status_code}, body={self._safe_excerpt(resp.text)}"
                )
        except httpx.HTTPError as exc:
            raise MinerURetryableError(f"MinerU upload HTTP error: {exc}") from exc

    def _poll_agent_task(self, task_id: str) -> dict[str, Any]:
        return self._poll(
            fetch=lambda: self._get_json(f"/api/v1/agent/parse/{task_id}", include_auth=False),
            extract_state=lambda body: str((body.get("data") or {}).get("state") or ""),
            done_states={"done", "failed"},
            timeout_sec=self.settings.mineru_poll_timeout_sec,
            interval_sec=self.settings.mineru_poll_interval_sec,
            task_label=f"agent:{task_id}",
        )

    def _poll_precise_batch(self, batch_id: str, file_name: str) -> dict[str, Any]:
        final = self._poll(
            fetch=lambda: self._get_json(f"/api/v4/extract-results/batch/{batch_id}", include_auth=True),
            extract_state=lambda body: self._extract_precise_state(body, file_name),
            done_states={"done", "failed"},
            timeout_sec=self.settings.mineru_poll_timeout_sec,
            interval_sec=self.settings.mineru_poll_interval_sec,
            task_label=f"precise:{batch_id}:{file_name}",
        )
        return final

    def _extract_precise_state(self, body: dict[str, Any], file_name: str) -> str:
        rows = (body.get("data") or {}).get("extract_result")
        if not isinstance(rows, list) or not rows:
            return "pending"
        target = self._find_precise_result(rows, file_name)
        return str(target.get("state") or "pending")

    @staticmethod
    def _find_precise_result(rows: list[dict[str, Any]], file_name: str) -> dict[str, Any]:
        for row in rows:
            if str(row.get("file_name") or "").strip().lower() == file_name.lower():
                return row
        return rows[0]

    def _poll(
        self,
        *,
        fetch,
        extract_state,
        done_states: set[str],
        timeout_sec: int,
        interval_sec: int,
        task_label: str,
    ) -> dict[str, Any]:
        start = time.perf_counter()
        last_body: dict[str, Any] | None = None
        while self.settings.mineru_poll_wait_forever or (time.perf_counter() - start) < timeout_sec:
            body = fetch()
            last_body = body
            state = extract_state(body)
            if state in done_states:
                return body
            self._on_poll_state(task_label, state, body)
            time.sleep(interval_sec)
        if last_body is None:
            raise MinerUClientError(f"MinerU poll timeout with no response: {task_label}")
        raise MinerUClientError(f"MinerU poll timeout after {timeout_sec}s: {task_label}")

    def _on_poll_state(self, task_label: str, state: str, body: dict[str, Any]) -> None:
        """Hook for adapter-side observability, intentionally no-op in client."""
        _ = (task_label, state, body)

    def _agent_download_markdown(self, task_id: str, body: dict[str, Any]) -> MinerUParseResult:
        data = body.get("data") or {}
        state = str(data.get("state") or "")
        if state != "done":
            err_code = data.get("err_code")
            err_msg = data.get("err_msg") or "unknown"
            raise MinerUClientError(f"MinerU agent failed err_code={err_code}, err_msg={err_msg}")

        markdown_url = str(data.get("markdown_url") or "").strip()
        if not markdown_url:
            raise MinerUClientError("MinerU agent missing markdown_url")

        markdown = self._download_text(markdown_url)
        structured = self._extract_structured_content(data, markdown)
        return MinerUParseResult(
            task_id=task_id,
            state=state,
            markdown_content=markdown,
            source_url=markdown_url,
            raw=body,
            structured_content=structured,
            assets=None,
        )

    def _precise_download_result(self, task_id: str, body: dict[str, Any]) -> MinerUParseResult:
        data = body.get("data") or {}
        rows = data.get("extract_result") or []
        if not isinstance(rows, list) or not rows:
            raise MinerUClientError("MinerU precise result missing extract_result")

        source_name = task_id.split(":")[-1] if ":" in task_id else ""
        row = self._find_precise_result(rows, source_name) if source_name else rows[0]
        state = str(row.get("state") or "")
        if state != "done":
            err_msg = row.get("err_msg") or "unknown"
            raise MinerUClientError(f"MinerU precise failed state={state}, err_msg={err_msg}")

        zip_url = str(row.get("full_zip_url") or "").strip()
        if not zip_url:
            raise MinerUClientError("MinerU precise result missing full_zip_url")

        markdown, structured, assets = self._download_markdown_structured_and_assets_from_zip(zip_url)
        return MinerUParseResult(
            task_id=task_id,
            state=state,
            markdown_content=markdown,
            source_url=zip_url,
            raw=body,
            structured_content=structured,
            assets=assets,
        )

    def _download_text(self, url: str) -> str:
        return self._download_with_retry(lambda: self._download_text_once(url))

    def _download_text_once(self, url: str) -> str:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url)
            resp.raise_for_status()
            return resp.text
        except httpx.HTTPStatusError as exc:
            raise MinerURetryableError(
                f"HTTP {exc.response.status_code} downloading {url}: {self._safe_excerpt(exc.response.text)}"
            ) from exc
        except httpx.HTTPError as exc:
            raise MinerURetryableError(f"HTTP error downloading {url}: {exc}") from exc

    def _download_markdown_structured_and_assets_from_zip(self, url: str) -> tuple[str, list[Any], dict[str, bytes]]:
        content = self._download_with_retry(lambda: self._download_zip_bytes_once(url))
        return self._extract_markdown_and_structured_from_zip_bytes(content)

    def _download_zip_bytes_once(self, url: str) -> bytes:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url)
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPStatusError as exc:
            raise MinerURetryableError(
                f"HTTP {exc.response.status_code} downloading {url}: {self._safe_excerpt(exc.response.text)}"
            ) from exc
        except httpx.HTTPError as exc:
            raise MinerURetryableError(f"HTTP error downloading {url}: {exc}") from exc

    def _download_with_retry(self, download_fn):
        attempts = 0
        while True:
            attempts += 1
            try:
                return download_fn()
            except MinerURetryableError:
                if not self.settings.mineru_download_wait_forever and attempts >= self.settings.mineru_download_max_retries:
                    raise
                time.sleep(self.settings.mineru_download_retry_interval_sec)

    @staticmethod
    def _extract_markdown_and_structured_from_zip_bytes(content: bytes) -> tuple[str, list[Any], dict[str, bytes]]:
        import io
        import zipfile

        try:
            with zipfile.ZipFile(io.BytesIO(content), "r") as zf:
                md_candidates = [name for name in zf.namelist() if name.lower().endswith("full.md")]
                if not md_candidates:
                    md_candidates = [name for name in zf.namelist() if name.lower().endswith(".md")]
                if not md_candidates:
                    raise MinerUClientError("MinerU zip result missing markdown file")
                with zf.open(md_candidates[0]) as f:
                    data = f.read()
                markdown = data.decode("utf-8", errors="ignore")
                structured: list[Any] = []
                assets: dict[str, bytes] = {}
                for name in zf.namelist():
                    lower = name.lower()
                    if lower.endswith(".json"):
                        try:
                            structured.append(json.loads(zf.read(name).decode("utf-8", errors="ignore")))
                        except Exception:
                            continue
                        continue
                    if lower.endswith(".md"):
                        if name == md_candidates[0]:
                            continue
                        continue
                    if lower.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff")):
                        try:
                            assets[name] = zf.read(name)
                        except Exception:
                            continue
                return markdown, structured, assets
        except zipfile.BadZipFile as exc:
            raise MinerUClientError(f"MinerU zip parse failed: {exc}") from exc

    def _extract_structured_content(self, data: dict[str, Any], markdown: str) -> list[Any]:
        structured: list[Any] = []
        for key in ("content_list", "content_list_v2", "model", "layout"):
            value = data.get(key)
            if value is not None:
                structured.append(value)
        if not structured and markdown:
            structured.append(markdown)
        return structured


def dump_json(data: dict[str, Any]) -> str:
    """Debug helper for structured json dump."""

    return json.dumps(data, ensure_ascii=False, sort_keys=True)
