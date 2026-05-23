from __future__ import annotations

from pathlib import Path

from agentic_rag.config import Settings
from agentic_rag.cli.inspect_ingestion import _inspect_input
from agentic_rag.ingestion.inspection import (
    IngestionInspectionRecorder,
    build_qdrant_payload_preview,
    build_retrieval_index_preview,
    render_pdf_pages_with_pymupdf,
    serialize_embedding_preview,
    serialize_node,
)
from agentic_rag.ingestion.node_normalizer import NodeNormalizer
from agentic_rag.ingestion.node_schema import MultimodalIngestionResult


def _node():
    return NodeNormalizer().normalize(
        source="data/demo_docs/doc.pdf",
        parser_name="mineru",
        chunk_index=2,
        modality="table",
        text="| a | b |\n| --- | --- |\n| 1 | 2 |",
        table_markdown="| a | b |\n| --- | --- |\n| 1 | 2 |",
        page=3,
        title="doc",
        section="Tables",
        relationships={"source_parser": "mineru", "mineru_block_type": "table", "page_node_id": "page:3"},
    )


def test_serialize_node_preserves_metadata_and_relationships() -> None:
    data = serialize_node(_node())

    assert data["node_id"]
    assert data["metadata"]["page"] == 3
    assert data["metadata"]["section"] == "Tables"
    assert data["relationships"]["source_parser"] == "mineru"
    assert data["table_markdown"].startswith("| a")


def test_qdrant_payload_preview_omits_full_vectors() -> None:
    settings = Settings(_env_file=None, enable_named_vectors=True, embedding_dimensions=1024)
    preview = build_qdrant_payload_preview(_node(), settings=settings)

    assert preview["point_id"]
    assert preview["vector_names"] == [settings.named_vector_table_name]
    assert preview["vector_dims"][settings.named_vector_table_name] == 1024
    assert "vector" not in preview
    assert preview["payload"]["metadata"]["source_parser"] == "mineru"


def test_embedding_preview_contains_excerpt_not_vector() -> None:
    settings = Settings(_env_file=None, embedding_dimensions=1536)
    preview = serialize_embedding_preview(_node(), settings=settings)

    assert preview["vector_dim"] == 1536
    assert preview["embedding_text_excerpt"].startswith("| a")
    assert "embedding" not in preview


def test_retrieval_index_preview_counts_table_and_page() -> None:
    preview = build_retrieval_index_preview([_node()])

    assert preview["bm25"]["count"] == 1
    assert preview["page"]["count"] == 1
    assert preview["page"]["pages"] == [3]
    assert preview["table"]["count"] == 1


def test_recorder_writes_expected_artifacts(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        ingestion_inspect_include_mineru_raw=True,
        ingestion_inspect_include_embedding_preview=True,
        embedding_dimensions=1024,
    )
    recorder = IngestionInspectionRecorder(settings=settings, output_dir=tmp_path, run_id="run1")
    result = MultimodalIngestionResult(nodes=[_node()], failures=[])

    run_dir = recorder.write_report(
        input_path="data/demo_docs/doc.pdf",
        result=result,
        parser="mineru",
        render_pages=False,
        mineru_raw={
            "raw_markdown": "# Raw",
            "parsed_markdown": "# Parsed",
            "structured_content": [{"type": "table", "page_idx": 2, "bbox": [1, 2, 3, 4]}],
            "raw_result_manifest": {"task_id": "t1"},
        },
    )

    assert (run_dir / "manifest.json").exists()
    assert (run_dir / "nodes.jsonl").exists()
    assert (run_dir / "chunks.html").exists()
    assert (run_dir / "document_map.html").exists()
    assert (run_dir / "mineru_raw" / "structured_content.json").exists()
    assert (run_dir / "previews" / "qdrant_payload_preview.jsonl").exists()
    assert (run_dir / "previews" / "retrieval_index_preview.json").exists()
    assert (run_dir / "previews" / "embedding_preview.jsonl").exists()
    assert "page=null" not in (run_dir / "chunks.html").read_text(encoding="utf-8")


def test_render_pdf_pages_without_pymupdf_does_not_raise(tmp_path: Path, monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "fitz":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    rendered, available, warnings = render_pdf_pages_with_pymupdf(tmp_path / "x.pdf", tmp_path / "pages")

    assert rendered == []
    assert available is False
    assert warnings


def test_inspect_input_single_markdown_uses_orchestrator_without_qdrant(tmp_path: Path) -> None:
    doc = tmp_path / "doc.md"
    doc.write_text("# Title\n\nbody text", encoding="utf-8")

    result, parser, raw = _inspect_input(
        doc,
        Settings(_env_file=None, ingestion_engine="multimodal", multimodal_enabled=True),
    )

    assert result.nodes
    assert result.failures == []
    assert parser
    assert raw is None
