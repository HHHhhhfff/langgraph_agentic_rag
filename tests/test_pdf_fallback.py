from __future__ import annotations

from pathlib import Path

from agentic_rag.config import Settings
from agentic_rag.ingestion.adapters.llamaparse_adapter import LlamaParseAdapter
from agentic_rag.ingestion.node_schema import MultimodalIngestionResult


def test_llamaparse_fallback_to_unstructured(monkeypatch, tmp_path: Path) -> None:
    pdf = tmp_path / "a.pdf"
    pdf.write_text("fake pdf", encoding="utf-8")

    settings = Settings(
        ingestion_engine="multimodal",
        multimodal_enabled=True,
        pdf_parser="llamaparse",
        llamaparse_fallback_to_unstructured=True,
    )

    adapter = LlamaParseAdapter(settings)

    def fail_parse(_):
        raise RuntimeError("llamaparse unavailable")

    monkeypatch.setattr(adapter, "_parse_pdf_once", fail_parse)

    class DummyFallback:
        def parse_file(self, path):
            return MultimodalIngestionResult(nodes=[], failures=[])

    adapter.fallback = DummyFallback()

    result = adapter.parse_file(pdf)
    assert isinstance(result, MultimodalIngestionResult)
    assert result.failures == []
