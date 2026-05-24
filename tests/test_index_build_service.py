from __future__ import annotations

from types import SimpleNamespace

from agentic_rag.config import Settings
from agentic_rag.ingestion import index_build_service
from agentic_rag.ingestion.index_builder import IndexBuildSummary
from agentic_rag.ingestion.index_build_service import IndexBuildOptions, build_index_from_nodes
from agentic_rag.ingestion.node_normalizer import NodeNormalizer


def test_build_index_from_nodes_applies_options(monkeypatch) -> None:
    node = NodeNormalizer().normalize(
        source="doc.md",
        parser_name="test",
        chunk_index=0,
        modality="text",
        text="hello",
    )
    captured = {}
    summary = IndexBuildSummary(
        documents=1,
        chunks=1,
        vectors=1,
        upserted=1,
        vector_size=3,
    )

    class DummyBuilder:
        def __init__(self, settings):
            self.settings = settings

        def build_from_nodes(self, nodes, *, source):
            captured["qdrant_recreate_collection"] = self.settings.qdrant_recreate_collection
            captured["retrieval_index_persist_enabled"] = self.settings.retrieval_index_persist_enabled
            captured["nodes"] = len(nodes)
            captured["source"] = source
            return summary

    monkeypatch.setattr(
        index_build_service,
        "_build_index_builder",
        lambda settings, stage_logger, run_id: DummyBuilder(settings),
    )
    monkeypatch.setattr(index_build_service, "_build_service_stage_logger", lambda *args, **kwargs: None)

    result = build_index_from_nodes(
        [node],
        source="inspect",
        settings=Settings(_env_file=None, retrieval_index_persist_enabled=True),
        options=IndexBuildOptions(
            recreate_collection=True,
            write_local_index=False,
            emit_logs=False,
            source_label="inspect_ingestion",
        ),
    )

    assert captured == {
        "qdrant_recreate_collection": True,
        "retrieval_index_persist_enabled": False,
        "nodes": 1,
        "source": "inspect",
    }
    assert result.upserted_point_count == 1
    assert result.recreated_collection is True
    assert result.wrote_local_index is False


def test_print_index_build_summary_handles_named_vectors(capsys) -> None:
    index_build_service.print_index_build_summary(
        SimpleNamespace(
            documents=1,
            chunks=2,
            vectors=2,
            upserted=2,
            vector_size=3,
            failed_files=0,
            named_vectors_enabled=True,
            named_vector_counts={"text": 1, "table": 1},
        )
    )

    out = capsys.readouterr().out
    assert "Index build completed" in out
    assert "  - table: 1" in out
