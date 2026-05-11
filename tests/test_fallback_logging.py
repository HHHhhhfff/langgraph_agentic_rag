from __future__ import annotations

import json
from pathlib import Path

from agentic_rag.config import Settings
from agentic_rag.ingestion.adapters.llamaparse_adapter import LlamaParseAdapter
from agentic_rag.ingestion.node_schema import MultimodalIngestionResult
from agentic_rag.observability.stage_logger import build_stage_logger


def test_llamaparse_fallback_log_fields(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "a.pdf"
    pdf.write_text("fake", encoding="utf-8")
    log_path = tmp_path / "stage.jsonl"

    settings = Settings(
        enable_stage_log=True,
        log_file_path=str(log_path),
        log_format="json",
        ingestion_engine="multimodal",
        multimodal_enabled=True,
        llamaparse_fallback_to_unstructured=True,
    )
    stage_logger = build_stage_logger(settings, run_id="run-fallback")
    adapter = LlamaParseAdapter(settings, stage_logger=stage_logger, run_id="run-fallback")

    def fail_parse(_):
        raise RuntimeError("boom")

    monkeypatch.setattr(adapter, "_parse_pdf_once", fail_parse)

    class DummyFallback:
        def parse_file(self, path):
            return MultimodalIngestionResult(nodes=[], failures=[])

    adapter.fallback = DummyFallback()
    _ = adapter.parse_file(pdf)

    lines = [ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    rows = [json.loads(ln) for ln in lines]
    matched = [r for r in rows if r.get("stage") == "llamaparse_fallback"]

    assert matched
    row = matched[-1]
    assert row.get("fallback") is True
    assert row.get("fallback_from") == "llamaparse"
    assert row.get("fallback_to") == "unstructured"
    assert row.get("run_id") == "run-fallback"
