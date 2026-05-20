from __future__ import annotations

import base64
from http import HTTPStatus
import importlib
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from agentic_rag.config import Settings
from agentic_rag.ingestion.token_splitter import TokenHardSplitter, TokenSplitConfig, looks_like_markdown_table
from agentic_rag.observability.stage_logger import StageLogger, StageTimer
from agentic_rag.schemas import SearchHit


class ProviderError(RuntimeError):
    """Raised when remote model provider call fails."""


@dataclass(slots=True)
class OpenAICompatibleClient:
    """Thin OpenAI-compatible HTTP client wrapper."""

    base_url: str
    api_key: str
    timeout: float

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _safe_response_excerpt(text: str, max_chars: int = 400) -> str:
        compact = " ".join(text.strip().split())
        return compact[:max_chars]

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, headers=self._headers(), json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            excerpt = self._safe_response_excerpt(exc.response.text)
            raise ProviderError(
                f"HTTP {exc.response.status_code} calling {url}: {excerpt}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"HTTP error calling {url}: {exc}") from exc
        except ValueError as exc:
            raise ProviderError(f"Invalid JSON from {url}: {exc}") from exc


class EmbeddingProvider(ABC):
    """Abstract embedding provider."""

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class ImageEmbeddingProvider(ABC):
    """Abstract image embedding provider."""

    @abstractmethod
    def embed_images(self, image_paths: list[str]) -> list[list[float]]:
        raise NotImplementedError


class Reranker(ABC):
    """Abstract reranker provider."""

    @abstractmethod
    def rerank(self, query: str, documents: list[str], top_n: int) -> list[dict[str, Any]]:
        raise NotImplementedError

    def rerank_hits(self, query: str, hits: list[SearchHit], top_n: int) -> list[dict[str, Any]]:
        documents = [_hit_rerank_text(hit) for hit in hits]
        return self.rerank(query=query, documents=documents, top_n=top_n)


class LLMClient(ABC):
    """Abstract text generation provider."""

    @abstractmethod
    def generate(self, prompt: str) -> str:
        raise NotImplementedError


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """Embedding provider using /embeddings endpoint."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.stage_logger: StageLogger | None = None
        self._hard_splitter = TokenHardSplitter(
            TokenSplitConfig(
                max_input_tokens=settings.embedding_input_max_tokens,
                safety_margin_tokens=settings.embedding_input_safety_margin_tokens,
            )
        )
        self.client = OpenAICompatibleClient(
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key,
            timeout=settings.embedding_timeout_sec,
        )

    def set_stage_logger(self, stage_logger: StageLogger) -> None:
        self.stage_logger = stage_logger

    def _embed_batch_once(self, texts: list[str]) -> list[list[float]]:
        payload: dict[str, Any] = {
            "model": self.settings.embedding_model,
            "input": texts,
        }
        if self.settings.embedding_dimensions:
            payload["dimensions"] = self.settings.embedding_dimensions
        data = self.client.post("embeddings", payload)
        rows = data.get("data")
        if not isinstance(rows, list):
            raise ProviderError("Embedding response missing data list")
        vectors: list[list[float]] = []
        for item in rows:
            emb = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(emb, list):
                raise ProviderError("Embedding response item missing embedding")
            vectors.append([float(x) for x in emb])
        return vectors

    @staticmethod
    def _is_input_too_long_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        if "maximum input length" in msg:
            return True
        if "invalid 'input" in msg and "length" in msg:
            return True
        if "input length" in msg and "token" in msg:
            return True
        return bool(re.search(r"\b8192\b.*token", msg))

    @staticmethod
    def _merge_vectors(vectors: list[list[float]], weights: list[int]) -> list[float]:
        if not vectors:
            raise ProviderError("cannot merge empty vectors")
        dim = len(vectors[0])
        if dim == 0:
            raise ProviderError("cannot merge zero-dimension vectors")
        if len(vectors) != len(weights):
            raise ProviderError("merge vector/weight count mismatch")
        acc = [0.0] * dim
        total = 0.0
        for vector, weight in zip(vectors, weights):
            if len(vector) != dim:
                raise ProviderError("merge vector dimension mismatch")
            w = float(max(1, weight))
            total += w
            for i, value in enumerate(vector):
                acc[i] += value * w
        return [x / total for x in acc]

    def _embed_single_with_recovery(
        self,
        text: str,
        *,
        retry_call,
        batch_index: int,
        depth: int = 0,
    ) -> list[float]:
        if self._hard_splitter.count_tokens(text) > self._hard_splitter.config.hard_limit:
            segments = self._hard_splitter.split_hard(
                text,
                prefer_table=looks_like_markdown_table(text),
            )
            if len(segments) > 1:
                if self.stage_logger:
                    self.stage_logger.log_counter(
                        "embedding_pre_split",
                        parser="embedding_provider",
                        batch_index=batch_index,
                        depth=depth,
                        split_count=len(segments),
                        fallback=True,
                    )
                seg_vectors: list[list[float]] = []
                seg_weights: list[int] = []
                for segment in segments:
                    vector = self._embed_single_with_recovery(
                        segment,
                        retry_call=retry_call,
                        batch_index=batch_index,
                        depth=depth + 1,
                    )
                    seg_vectors.append(vector)
                    seg_weights.append(max(1, self._hard_splitter.count_tokens(segment)))
                return self._merge_vectors(seg_vectors, seg_weights)
        try:
            vectors = retry_call([text])
            if not vectors:
                raise ProviderError("embedding provider returned empty vector list")
            return vectors[0]
        except Exception as exc:
            if not self._is_input_too_long_error(exc):
                raise
            if depth >= 5:
                raise ProviderError("embedding recursive split depth exceeded") from exc

            segments = self._hard_splitter.split_hard(
                text,
                prefer_table=looks_like_markdown_table(text),
            )
            if len(segments) <= 1:
                raise

            if self.stage_logger:
                self.stage_logger.log_counter(
                    "embedding_auto_resplit",
                    parser="embedding_provider",
                    batch_index=batch_index,
                    depth=depth,
                    split_count=len(segments),
                    fallback=True,
                    error_type=type(exc).__name__,
                    error_msg=str(exc),
                )

            seg_vectors: list[list[float]] = []
            seg_weights: list[int] = []
            for segment in segments:
                vector = self._embed_single_with_recovery(
                    segment,
                    retry_call=retry_call,
                    batch_index=batch_index,
                    depth=depth + 1,
                )
                seg_vectors.append(vector)
                seg_weights.append(max(1, self._hard_splitter.count_tokens(segment)))
            return self._merge_vectors(seg_vectors, seg_weights)

    def _embed_batch_with_recovery(self, batch: list[str], *, retry_call, batch_index: int) -> list[list[float]]:
        if any(self._hard_splitter.count_tokens(text) > self._hard_splitter.config.hard_limit for text in batch):
            recovered = [
                self._embed_single_with_recovery(
                    text,
                    retry_call=retry_call,
                    batch_index=batch_index,
                )
                for text in batch
            ]
            if self.stage_logger:
                self.stage_logger.log_counter(
                    "embedding_batch_presplit_recover",
                    parser="embedding_provider",
                    batch_index=batch_index,
                    chunk_count=len(batch),
                    fallback=True,
                )
            return recovered
        try:
            return retry_call(batch)
        except Exception as exc:
            if not self._is_input_too_long_error(exc):
                raise
            if self.stage_logger:
                self.stage_logger.log_counter(
                    "embedding_overlength_recover",
                    parser="embedding_provider",
                    batch_index=batch_index,
                    chunk_count=len(batch),
                    fallback=True,
                    error_type=type(exc).__name__,
                    error_msg=str(exc),
                )
            recovered: list[list[float]] = []
            for text in batch:
                recovered.append(
                    self._embed_single_with_recovery(
                        text,
                        retry_call=retry_call,
                        batch_index=batch_index,
                    )
                )
            return recovered

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        safe_texts = [t.strip() if isinstance(t, str) else "" for t in texts]
        safe_texts = [t if t else "[empty-chunk]" for t in safe_texts]

        retry_call = retry(
            reraise=True,
            stop=stop_after_attempt(self.settings.embedding_max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type(ProviderError),
        )(self._embed_batch_once)
        batch_size = self.settings.embedding_batch_size
        vectors: list[list[float]] = []
        for i in range(0, len(safe_texts), batch_size):
            batch = safe_texts[i : i + batch_size]
            timer = StageTimer.start_now()
            if self.stage_logger:
                self.stage_logger.log_stage_start(
                    "embedding_batch",
                    parser="embedding_provider",
                    chunk_count=len(batch),
                    batch_index=i,
                )
            try:
                out = self._embed_batch_with_recovery(batch, retry_call=retry_call, batch_index=i)
            except Exception as exc:
                if self.stage_logger:
                    self.stage_logger.log_stage_error(
                        "embedding_batch",
                        exc,
                        parser="embedding_provider",
                        batch_index=i,
                    )
                raise
            vectors.extend(out)
            if self.stage_logger:
                self.stage_logger.log_stage_end(
                    "embedding_batch",
                    latency_ms=timer.elapsed_ms(),
                    parser="embedding_provider",
                    chunk_count=len(batch),
                    vector_count=len(out),
                    batch_index=i,
                )
        return vectors


class OpenAICompatibleImageEmbeddingProvider(ImageEmbeddingProvider):
    """Image embedding provider using OpenAI-compatible /embeddings endpoint."""

    MIME_MAP = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".gif": "image/gif",
    }

    def __init__(self, settings: Settings):
        self.settings = settings
        self.stage_logger: StageLogger | None = None
        self.client = OpenAICompatibleClient(
            base_url=settings.image_embed_base_url,
            api_key=settings.image_embed_api_key,
            timeout=settings.image_embed_timeout_sec,
        )

    def set_stage_logger(self, stage_logger: StageLogger) -> None:
        self.stage_logger = stage_logger

    def _image_to_data_url(self, image_path: str) -> str:
        path = Path(image_path)
        if self.settings.image_embed_require_file_exists and not path.exists():
            raise ProviderError(f"Image file not found: {path}")
        if not path.exists():
            raise ProviderError(f"Image file not accessible: {path}")
        mime = self.MIME_MAP.get(path.suffix.lower(), "application/octet-stream")
        try:
            content = path.read_bytes()
        except Exception as exc:
            raise ProviderError(f"Failed to read image file {path}: {exc}") from exc
        encoded = base64.b64encode(content).decode("utf-8")
        return f"data:{mime};base64,{encoded}"

    @staticmethod
    def _parse_vectors(data: dict[str, Any]) -> list[list[float]]:
        rows = data.get("data")
        if not isinstance(rows, list):
            raise ProviderError("Image embedding response missing data list")
        vectors: list[list[float]] = []
        for row in rows:
            emb = row.get("embedding") if isinstance(row, dict) else None
            if not isinstance(emb, list):
                raise ProviderError("Image embedding response item missing embedding")
            vectors.append([float(x) for x in emb])
        return vectors

    def _embed_batch_once(self, image_paths: list[str]) -> list[list[float]]:
        data_urls = [self._image_to_data_url(path) for path in image_paths]

        primary_payload: dict[str, Any] = {
            "model": self.settings.image_embed_model,
            "input": [
                {
                    "type": "input_image",
                    "image_url": data_url,
                }
                for data_url in data_urls
            ],
        }
        try:
            data = self.client.post("embeddings", primary_payload)
            vectors = self._parse_vectors(data)
            if len(vectors) == len(image_paths):
                return vectors
        except ProviderError:
            pass

        # Some OpenAI-compatible providers only accept the chat-style image_url object.
        fallback_payload: dict[str, Any] = {
            "model": self.settings.image_embed_model,
            "input": [
                {
                    "type": "image_url",
                    "image_url": {"url": data_url},
                }
                for data_url in data_urls
            ],
        }
        data = self.client.post("embeddings", fallback_payload)
        vectors = self._parse_vectors(data)
        if len(vectors) != len(image_paths):
            raise ProviderError(
                f"Image embedding count mismatch: expected={len(image_paths)}, got={len(vectors)}"
            )
        return vectors

    def embed_images(self, image_paths: list[str]) -> list[list[float]]:
        if not image_paths:
            return []
        retry_call = retry(
            reraise=True,
            stop=stop_after_attempt(self.settings.image_embed_max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type(ProviderError),
        )(self._embed_batch_once)

        batch_size = self.settings.image_embed_batch_size
        vectors: list[list[float]] = []
        for i in range(0, len(image_paths), batch_size):
            batch = image_paths[i : i + batch_size]
            timer = StageTimer.start_now()
            if self.stage_logger:
                self.stage_logger.log_stage_start(
                    "image_embedding_batch",
                    parser="image_embedding_provider",
                    modality="image",
                    image_count=len(batch),
                    batch_index=i,
                )
            try:
                out = retry_call(batch)
            except Exception as exc:
                if self.stage_logger:
                    self.stage_logger.log_stage_error(
                        "image_embedding_batch",
                        exc,
                        parser="image_embedding_provider",
                        modality="image",
                        image_count=len(batch),
                        batch_index=i,
                    )
                raise
            vectors.extend(out)
            if self.stage_logger:
                self.stage_logger.log_stage_end(
                    "image_embedding_batch",
                    latency_ms=timer.elapsed_ms(),
                    parser="image_embedding_provider",
                    modality="image",
                    image_count=len(batch),
                    vector_count=len(out),
                    batch_index=i,
                )
        return vectors


class DashScopeMultimodalEmbeddingProvider(OpenAICompatibleEmbeddingProvider):
    """Text embedding provider using DashScope native MultiModalEmbedding SDK."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.stage_logger: StageLogger | None = None
        self._hard_splitter = TokenHardSplitter(
            TokenSplitConfig(
                max_input_tokens=settings.embedding_input_max_tokens,
                safety_margin_tokens=settings.embedding_input_safety_margin_tokens,
            )
        )
        self.dashscope = _load_dashscope_module()

    def _embed_batch_once(self, texts: list[str]) -> list[list[float]]:
        payload = [{"text": text} for text in texts]
        return _dashscope_multimodal_call(
            dashscope_module=self.dashscope,
            settings=self.settings,
            input_payload=payload,
            expected_count=len(texts),
            api_key=_dashscope_api_key(self.settings, image=False),
            model=_dashscope_model(self.settings, image=False),
            dimension=_dashscope_dimension(self.settings),
        )


class DashScopeMultimodalImageEmbeddingProvider(ImageEmbeddingProvider):
    """Image embedding provider using DashScope native MultiModalEmbedding SDK."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.stage_logger: StageLogger | None = None
        self.dashscope = _load_dashscope_module()

    def set_stage_logger(self, stage_logger: StageLogger) -> None:
        self.stage_logger = stage_logger

    def _image_input(self, image_path: str) -> str:
        if image_path.startswith(("http://", "https://", "data:")):
            return image_path
        path = Path(image_path)
        if self.settings.image_embed_require_file_exists and not path.exists():
            raise ProviderError(
                f"Image file not found for DashScope multimodal embedding: {path}. "
                "Use a valid local path or an accessible image URL."
            )
        if not path.exists():
            raise ProviderError(
                f"Image file not accessible for DashScope multimodal embedding: {path}. "
                "Use a valid local path or an accessible image URL."
            )
        return str(path.resolve())

    def _embed_batch_once(self, image_paths: list[str]) -> list[list[float]]:
        payload = [{"image": self._image_input(path)} for path in image_paths]
        return _dashscope_multimodal_call(
            dashscope_module=self.dashscope,
            settings=self.settings,
            input_payload=payload,
            expected_count=len(image_paths),
            api_key=_dashscope_api_key(self.settings, image=True),
            model=_dashscope_model(self.settings, image=True),
            dimension=_dashscope_dimension(self.settings),
        )

    def embed_images(self, image_paths: list[str]) -> list[list[float]]:
        if not image_paths:
            return []
        retry_call = retry(
            reraise=True,
            stop=stop_after_attempt(self.settings.image_embed_max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type(ProviderError),
        )(self._embed_batch_once)

        batch_size = self.settings.image_embed_batch_size
        vectors: list[list[float]] = []
        for i in range(0, len(image_paths), batch_size):
            batch = image_paths[i : i + batch_size]
            timer = StageTimer.start_now()
            if self.stage_logger:
                self.stage_logger.log_stage_start(
                    "image_embedding_batch",
                    parser="dashscope_multimodal_embedding_provider",
                    modality="image",
                    image_count=len(batch),
                    batch_index=i,
                )
            try:
                out = retry_call(batch)
            except Exception as exc:
                if self.stage_logger:
                    self.stage_logger.log_stage_error(
                        "image_embedding_batch",
                        exc,
                        parser="dashscope_multimodal_embedding_provider",
                        modality="image",
                        image_count=len(batch),
                        batch_index=i,
                    )
                raise
            vectors.extend(out)
            if self.stage_logger:
                self.stage_logger.log_stage_end(
                    "image_embedding_batch",
                    latency_ms=timer.elapsed_ms(),
                    parser="dashscope_multimodal_embedding_provider",
                    modality="image",
                    image_count=len(batch),
                    vector_count=len(out),
                    batch_index=i,
                )
        return vectors


def _load_dashscope_module():
    try:
        return importlib.import_module("dashscope")
    except ImportError as exc:
        raise ProviderError(
            "dashscope package is required for dashscope_multimodal provider. "
            "Install with: pip install dashscope"
        ) from exc


def _dashscope_api_key(settings: Settings, *, image: bool) -> str:
    key = settings.dashscope_api_key or (settings.image_embed_api_key if image else settings.embedding_api_key)
    key = key or os.getenv("DASHSCOPE_API_KEY", "")
    if not key:
        raise ProviderError(
            "DashScope API key is required for dashscope_multimodal provider. "
            "Set DASHSCOPE_API_KEY or the corresponding provider API key."
        )
    return key


def _dashscope_model(settings: Settings, *, image: bool) -> str:
    model = settings.dashscope_embedding_model or (settings.image_embed_model if image else settings.embedding_model)
    if not model:
        raise ProviderError("DashScope embedding model is empty")
    return model


def _dashscope_dimension(settings: Settings) -> int | None:
    return settings.dashscope_embedding_dimension or settings.embedding_dimensions


def _dashscope_multimodal_call(
    *,
    dashscope_module,
    settings: Settings,
    input_payload: list[dict[str, str]],
    expected_count: int,
    api_key: str,
    model: str,
    dimension: int | None,
) -> list[list[float]]:
    kwargs: dict[str, Any] = {
        "api_key": api_key,
        "model": model,
        "input": input_payload,
    }
    if dimension is not None:
        kwargs["dimension"] = dimension
    try:
        response = dashscope_module.MultiModalEmbedding.call(**kwargs)
    except Exception as exc:
        raise ProviderError(f"DashScope multimodal embedding call failed: {exc}") from exc

    _validate_dashscope_response(response)
    output = _dashscope_response_value(response, "output")
    vectors = _parse_dashscope_vectors(output)
    if len(vectors) != expected_count:
        raise ProviderError(
            f"DashScope embedding count mismatch: expected={expected_count}, got={len(vectors)}"
        )
    return vectors


def _validate_dashscope_response(response: Any) -> None:
    status = _dashscope_response_value(response, "status_code")
    if status == HTTPStatus.OK or status == HTTPStatus.OK.value or str(status) == str(HTTPStatus.OK.value):
        return
    message = _dashscope_response_value(response, "message") or _dashscope_response_value(response, "code") or ""
    request_id = _dashscope_response_value(response, "request_id") or ""
    raise ProviderError(
        "DashScope multimodal embedding failed: "
        f"status_code={status}, request_id={request_id}, message={_safe_excerpt(str(message))}"
    )


def _dashscope_response_value(response: Any, key: str) -> Any:
    if isinstance(response, dict):
        return response.get(key)
    return getattr(response, key, None)


def _parse_dashscope_vectors(output: Any) -> list[list[float]]:
    if isinstance(output, dict):
        for key in ("embeddings", "embedding", "data"):
            if key in output:
                try:
                    return _coerce_embedding_rows(output[key])
                except ProviderError:
                    continue
    if isinstance(output, list):
        return _coerce_embedding_rows(output)
    raise ProviderError(f"DashScope embedding response missing embeddings: {_safe_excerpt(str(output))}")


def _coerce_embedding_rows(value: Any) -> list[list[float]]:
    if _is_number_list(value):
        return [[float(x) for x in value]]
    if not isinstance(value, list):
        raise ProviderError("DashScope embedding field is not a list")
    vectors: list[list[float]] = []
    for row in value:
        if _is_number_list(row):
            vectors.append([float(x) for x in row])
            continue
        if isinstance(row, dict):
            embedding = row.get("embedding") or row.get("embeddings") or row.get("vector")
            if _is_number_list(embedding):
                vectors.append([float(x) for x in embedding])
                continue
        raise ProviderError(f"DashScope embedding row missing embedding: {_safe_excerpt(str(row))}")
    return vectors


def _is_number_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(x, (int, float)) for x in value)


def _safe_excerpt(text: str, max_chars: int = 400) -> str:
    return " ".join((text or "").strip().split())[:max_chars]


def _hit_rerank_text(hit: SearchHit) -> str:
    parts = [
        hit.text or "",
        hit.table_markdown or "",
        hit.formula_latex or "",
        hit.caption or "",
        hit.ocr_text or "",
        hit.object_description or "",
    ]
    return "\n".join(part for part in parts if part).strip()


def _is_http_url(value: str | None) -> bool:
    return bool(value and re.match(r"^https?://", value.strip(), flags=re.I))


def _hit_multimodal_rerank_document(hit: SearchHit) -> dict[str, str]:
    if hit.modality == "image":
        image_url = (
            hit.metadata.get("image_url")
            or hit.metadata.get("url")
            or hit.metadata.get("source_url")
            or hit.image_path
        )
        if isinstance(image_url, str) and _is_http_url(image_url):
            return {"image": image_url}
    text = _hit_rerank_text(hit)
    return {"text": text or hit.point_id}


def _object_to_plain_data(value: Any) -> Any:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return value
    if hasattr(value, "to_dict"):
        try:
            return value.to_dict()
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return {k: v for k, v in vars(value).items() if not k.startswith("_")}
    return value


def _dig_value(data: Any, path: tuple[str, ...]) -> Any:
    current = data
    for key in path:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            current = getattr(current, key, None)
        if current is None:
            return None
    return current


def _parse_dashscope_rerank_response(resp: Any) -> list[dict[str, Any]]:
    data = _object_to_plain_data(resp)
    candidates = [
        _dig_value(resp, ("output", "results")),
        _dig_value(data, ("output", "results")),
        _dig_value(resp, ("data",)),
        _dig_value(data, ("data",)),
        _dig_value(resp, ("results",)),
        _dig_value(data, ("results",)),
    ]
    rows = next((row for row in candidates if isinstance(row, list)), None)
    if rows is None:
        raise ProviderError(f"DashScope rerank response missing results: {_safe_excerpt(str(resp))}")
    parsed: list[dict[str, Any]] = []
    for row in rows:
        row_data = _object_to_plain_data(row)
        if not isinstance(row_data, dict):
            continue
        index = row_data.get("index")
        score = row_data.get("relevance_score", row_data.get("score"))
        if isinstance(index, int) and isinstance(score, (int, float)):
            parsed.append(
                {
                    "index": index,
                    "score": float(score),
                    "document": row_data.get("document"),
                }
            )
    return parsed


class OpenAICompatibleReranker(Reranker):
    """Reranker using OpenAI-compatible /rerank endpoint."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.stage_logger: StageLogger | None = None
        self.client = OpenAICompatibleClient(
            base_url=settings.rerank_base_url,
            api_key=settings.rerank_api_key,
            timeout=settings.rerank_timeout_sec,
        )

    def set_stage_logger(self, stage_logger: StageLogger) -> None:
        self.stage_logger = stage_logger

    def _rerank_once(self, query: str, documents: list[str], top_n: int) -> list[dict[str, Any]]:
        payload = {
            "model": self.settings.rerank_model,
            "query": query,
            "documents": documents,
            "top_n": top_n,
        }
        data = self.client.post("services/rerank/text-rerank/text-rerank", payload)  # rerank请求URL后缀格式
        rows = data.get("data")
        if not isinstance(rows, list):
            raise ProviderError("Rerank response missing data list")
        parsed: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            index = row.get("index")
            score = row.get("relevance_score")
            if isinstance(index, int) and isinstance(score, (int, float)):
                parsed.append({"index": index, "score": float(score)})
        return parsed

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[dict[str, Any]]:
        timer = StageTimer.start_now()
        if self.stage_logger:
            self.stage_logger.log_stage_start(
                "rerank_call",
                parser="reranker_provider",
                chunk_count=len(documents),
            )
        retry_call = retry(
            reraise=True,
            stop=stop_after_attempt(self.settings.rerank_max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=4),
            retry=retry_if_exception_type(ProviderError),
        )(self._rerank_once)
        try:
            out = retry_call(query, documents, top_n)
        except Exception as exc:
            if self.stage_logger:
                self.stage_logger.log_stage_error("rerank_call", exc, parser="reranker_provider")
            raise
        if self.stage_logger:
            self.stage_logger.log_stage_end(
                "rerank_call",
                latency_ms=timer.elapsed_ms(),
                parser="reranker_provider",
                chunk_count=len(documents),
            )
        return out


class DashScopeReranker(Reranker):
    """Reranker using DashScope TextReRank SDK."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.stage_logger: StageLogger | None = None

    def set_stage_logger(self, stage_logger: StageLogger) -> None:
        self.stage_logger = stage_logger

    def _dashscope_module(self):
        try:
            return importlib.import_module("dashscope")
        except ImportError as exc:
            raise ProviderError("dashscope package is required for RERANK_PROVIDER=dashscope") from exc

    def _is_multimodal(self) -> bool:
        return bool(self.settings.rerank_enable_multimodal) or "vl-rerank" in self.settings.rerank_model.lower()

    def _call_dashscope(self, *, query: Any, documents: list[Any], top_n: int, multimodal: bool) -> list[dict[str, Any]]:
        dashscope = self._dashscope_module()
        api_key = self.settings.rerank_api_key or os.getenv("DASHSCOPE_API_KEY", "")
        if api_key:
            dashscope.api_key = api_key
        kwargs: dict[str, Any] = {
            "model": self.settings.rerank_model,
            "query": query,
            "documents": documents,
            "top_n": top_n,
            "return_documents": self.settings.rerank_return_documents,
        }
        if not multimodal and self.settings.rerank_instruct:
            kwargs["instruct"] = self.settings.rerank_instruct
        resp = dashscope.TextReRank.call(**kwargs)
        status_code = getattr(resp, "status_code", None)
        if status_code is not None and status_code != HTTPStatus.OK:
            raise ProviderError(f"DashScope rerank failed status={status_code}: {_safe_excerpt(str(resp))}")
        return _parse_dashscope_rerank_response(resp)

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[dict[str, Any]]:
        timer = StageTimer.start_now()
        multimodal = self._is_multimodal()
        payload_query: Any = {"text": query} if multimodal else query
        payload_docs: list[Any] = [{"text": doc} for doc in documents] if multimodal else documents
        if self.stage_logger:
            self.stage_logger.log_stage_start(
                "rerank_call",
                parser="reranker_provider",
                provider="dashscope",
                model=self.settings.rerank_model,
                document_count=len(documents),
                top_n=top_n,
                multimodal=multimodal,
            )
        retry_call = retry(
            reraise=True,
            stop=stop_after_attempt(self.settings.rerank_max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=4),
            retry=retry_if_exception_type(ProviderError),
        )(self._call_dashscope)
        try:
            out = retry_call(query=payload_query, documents=payload_docs, top_n=top_n, multimodal=multimodal)
        except Exception as exc:
            if self.stage_logger:
                self.stage_logger.log_stage_error("rerank_call", exc, parser="reranker_provider", provider="dashscope")
            raise
        if self.stage_logger:
            self.stage_logger.log_stage_end(
                "rerank_call",
                latency_ms=timer.elapsed_ms(),
                parser="reranker_provider",
                provider="dashscope",
                model=self.settings.rerank_model,
                document_count=len(documents),
                top_n=top_n,
                multimodal=multimodal,
            )
        return out

    def rerank_hits(self, query: str, hits: list[SearchHit], top_n: int) -> list[dict[str, Any]]:
        if not self._is_multimodal():
            return self.rerank(query=query, documents=[_hit_rerank_text(hit) for hit in hits], top_n=top_n)
        timer = StageTimer.start_now()
        documents = [_hit_multimodal_rerank_document(hit) for hit in hits]
        if self.stage_logger:
            self.stage_logger.log_stage_start(
                "rerank_call",
                parser="reranker_provider",
                provider="dashscope",
                model=self.settings.rerank_model,
                document_count=len(documents),
                top_n=top_n,
                multimodal=True,
            )
        retry_call = retry(
            reraise=True,
            stop=stop_after_attempt(self.settings.rerank_max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=4),
            retry=retry_if_exception_type(ProviderError),
        )(self._call_dashscope)
        try:
            out = retry_call(query={"text": query}, documents=documents, top_n=top_n, multimodal=True)
        except Exception as exc:
            if self.stage_logger:
                self.stage_logger.log_stage_error("rerank_call", exc, parser="reranker_provider", provider="dashscope")
            raise
        if self.stage_logger:
            self.stage_logger.log_stage_end(
                "rerank_call",
                latency_ms=timer.elapsed_ms(),
                parser="reranker_provider",
                provider="dashscope",
                model=self.settings.rerank_model,
                document_count=len(documents),
                top_n=top_n,
                multimodal=True,
            )
        return out


class OpenAICompatibleLLMClient(LLMClient):
    """LLM client using /chat/completions endpoint."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.stage_logger: StageLogger | None = None
        self.client = OpenAICompatibleClient(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout=settings.llm_timeout_sec,
        )

    def set_stage_logger(self, stage_logger: StageLogger) -> None:
        self.stage_logger = stage_logger

    def _generate_once(self, prompt: str) -> str:
        payload = {
            "model": self.settings.llm_model,
            "temperature": self.settings.llm_temperature,
            "max_tokens": self.settings.llm_max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a rigorous RAG assistant. Follow the provided instructions exactly.",
                },
                {"role": "user", "content": prompt},
            ],
        }
        data = self.client.post("chat/completions", payload)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError("LLM response missing choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise ProviderError("Invalid LLM choice format")
        message = first.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ProviderError("LLM response missing message content")
        return message["content"].strip()

    def generate(self, prompt: str) -> str:
        retry_call = retry(
            reraise=True,
            stop=stop_after_attempt(self.settings.llm_max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=4),
            retry=retry_if_exception_type(ProviderError),
        )(self._generate_once)
        return retry_call(prompt)


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Factory for embedding provider."""

    if settings.embedding_provider_type == "dashscope_multimodal":
        return DashScopeMultimodalEmbeddingProvider(settings)
    return OpenAICompatibleEmbeddingProvider(settings)


def build_image_embedding_provider(settings: Settings) -> ImageEmbeddingProvider:
    """Factory for image embedding provider."""

    if settings.image_embed_provider_type == "dashscope_multimodal":
        return DashScopeMultimodalImageEmbeddingProvider(settings)
    return OpenAICompatibleImageEmbeddingProvider(settings)


def build_reranker(settings: Settings) -> Reranker:
    """Factory for reranker provider."""

    provider = settings.rerank_provider
    model = settings.rerank_model.lower()
    if not provider and model in {"qwen3-rerank", "qwen3-vl-rerank"}:
        provider = "dashscope"
    if provider == "dashscope":
        return DashScopeReranker(settings)
    if provider == "openai_compatible" or not provider:
        return OpenAICompatibleReranker(settings)
    raise ProviderError(f"Unsupported rerank provider: {settings.rerank_provider}")


def build_llm_client(settings: Settings) -> LLMClient:
    """Factory for generation client."""

    return OpenAICompatibleLLMClient(settings)

