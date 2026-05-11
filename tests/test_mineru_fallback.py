from __future__ import annotations

from pathlib import Path

from agentic_rag.config import Settings
from agentic_rag.ingestion.multimodal_orchestrator import MultiModalOrchestrator
from agentic_rag.ingestion.node_schema import IngestionFailure, MultimodalIngestionResult


class DummySuccessMineru:
    def parse_file(self, path):
        return MultimodalIngestionResult(nodes=[], failures=[IngestionFailure(source=str(path), error="mineru down")])


class DummyFallbackAdapter:
    def parse_file(self, path):
        return MultimodalIngestionResult(nodes=[], failures=[])


class DummyUnstructuredAdapter:
    def parse_file(self, path):
        return MultimodalIngestionResult(nodes=[], failures=[])


def test_mineru_fallback_to_existing(monkeypatch, tmp_path: Path):
    file_path = tmp_path / "a.pdf"
    file_path.write_bytes(b"pdf")

    settings = Settings(
        ingestion_engine="multimodal",
        multimodal_enabled=True,
        enable_mineru=True,
        mineru_fallback_to_existing=True,
    )
    orchestrator = MultiModalOrchestrator(settings)

    orchestrator.mineru_adapter = DummySuccessMineru()
    orchestrator.unstructured_adapter = DummyUnstructuredAdapter()

    result = orchestrator.parse_directory(tmp_path)
    assert result.failures == []


def test_mineru_no_fallback(monkeypatch, tmp_path: Path):
    file_path = tmp_path / "a.pdf"
    file_path.write_bytes(b"pdf")

    settings = Settings(
        ingestion_engine="multimodal",
        multimodal_enabled=True,
        enable_mineru=True,
        mineru_fallback_to_existing=False,
    )
    orchestrator = MultiModalOrchestrator(settings)

    orchestrator.mineru_adapter = DummySuccessMineru()
    orchestrator.unstructured_adapter = DummyUnstructuredAdapter()

    result = orchestrator.parse_directory(tmp_path)
    assert len(result.failures) == 1
