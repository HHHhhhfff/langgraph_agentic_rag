from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from agentic_rag.config import Settings
from agentic_rag.ingestion.adapters.mineru_client import MinerUClient, MinerUClientError


class DummyResp:
    def __init__(self, *, status_code=200, json_data=None, text="", content=b""):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        if self._json is None:
            raise ValueError("invalid json")
        return self._json


class DummyClient:
    def __init__(self, responses):
        self.responses = responses

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, *args, **kwargs):
        return self.responses.pop(0)

    def get(self, *args, **kwargs):
        if not self.responses:
            return DummyResp(json_data={"code": 0, "data": {"state": "running"}})
        return self.responses.pop(0)

    def put(self, *args, **kwargs):
        return self.responses.pop(0)


def _zip_with_full_md(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("full.md", text)
    return buf.getvalue()


def test_mineru_precise_success(monkeypatch, tmp_path: Path):
    settings = Settings(
        enable_mineru=True,
        mineru_mode="precise",
        mineru_api_token="token",
        mineru_poll_timeout_sec=3,
        mineru_poll_interval_sec=1,
    )
    client = MinerUClient(settings)

    file_path = tmp_path / "a.pdf"
    file_path.write_bytes(b"pdf")

    zip_bytes = _zip_with_full_md("hello from mineru")

    responses = [
        DummyResp(json_data={"code": 0, "data": {"batch_id": "b1", "file_urls": ["https://upload"]}}),
        DummyResp(status_code=200, text="ok"),
        DummyResp(json_data={"code": 0, "data": {"extract_result": [{"file_name": "a.pdf", "state": "done", "full_zip_url": "https://cdn/full.zip"}]}}),
        DummyResp(status_code=200, content=zip_bytes),
    ]

    monkeypatch.setattr("httpx.Client", lambda *args, **kwargs: DummyClient(responses))

    result = client.parse_local_file(file_path)
    assert result.state == "done"
    assert "hello from mineru" in result.markdown_content


def test_mineru_agent_success(monkeypatch, tmp_path: Path):
    settings = Settings(
        enable_mineru=True,
        mineru_mode="agent",
        mineru_poll_timeout_sec=3,
        mineru_poll_interval_sec=1,
    )
    client = MinerUClient(settings)

    file_path = tmp_path / "a.pdf"
    file_path.write_bytes(b"pdf")

    responses = [
        DummyResp(json_data={"code": 0, "data": {"task_id": "t1", "file_url": "https://upload"}}),
        DummyResp(status_code=200, text="ok"),
        DummyResp(json_data={"code": 0, "data": {"state": "done", "markdown_url": "https://cdn/full.md"}}),
        DummyResp(status_code=200, text="# title\ncontent"),
    ]

    monkeypatch.setattr("httpx.Client", lambda *args, **kwargs: DummyClient(responses))

    result = client.parse_local_file(file_path)
    assert result.state == "done"
    assert "content" in result.markdown_content


def test_mineru_poll_timeout(monkeypatch, tmp_path: Path):
    settings = Settings(
        enable_mineru=True,
        mineru_mode="agent",
        mineru_poll_timeout_sec=1,
        mineru_poll_interval_sec=1,
    )
    client = MinerUClient(settings)

    file_path = tmp_path / "a.pdf"
    file_path.write_bytes(b"pdf")

    responses = [
        DummyResp(json_data={"code": 0, "data": {"task_id": "t1", "file_url": "https://upload"}}),
        DummyResp(status_code=200, text="ok"),
        DummyResp(json_data={"code": 0, "data": {"state": "running"}}),
        DummyResp(json_data={"code": 0, "data": {"state": "running"}}),
        DummyResp(json_data={"code": 0, "data": {"state": "running"}}),
    ]

    monkeypatch.setattr("httpx.Client", lambda *args, **kwargs: DummyClient(responses))
    monkeypatch.setattr("time.sleep", lambda *_: None)

    with pytest.raises(MinerUClientError):
        client.parse_local_file(file_path)
