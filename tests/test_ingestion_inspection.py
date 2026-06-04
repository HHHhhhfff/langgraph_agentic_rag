from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agentic_rag.config import Settings
from agentic_rag.cli import inspect_ingestion
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
            "assets": {"images/a.jpg": b"\xff\xd8\xffdemo"},
        },
    )

    assert (run_dir / "manifest.json").exists()
    assert (run_dir / "nodes.jsonl").exists()
    assert (run_dir / "chunks.html").exists()
    assert (run_dir / "document_map.html").exists()
    assert (run_dir / "mineru_raw" / "structured_content.json").exists()
    assert (run_dir / "mineru_raw" / "bbox_summary.json").exists()
    assert (run_dir / "mineru_raw" / "structured_bbox_blocks.jsonl").exists()
    assert (run_dir / "mineru_raw" / "images" / "a.jpg").read_bytes() == b"\xff\xd8\xffdemo"
    assert (run_dir / "previews" / "qdrant_payload_preview.jsonl").exists()
    assert (run_dir / "previews" / "retrieval_index_preview.json").exists()
    assert (run_dir / "previews" / "embedding_preview.jsonl").exists()
    assert "page=null" not in (run_dir / "chunks.html").read_text(encoding="utf-8")

    import json

    bbox_summary = json.loads((run_dir / "mineru_raw" / "bbox_summary.json").read_text(encoding="utf-8"))
    assert bbox_summary["blocks_with_bbox"] == 1
    assert bbox_summary["by_type"]["table"]["with_bbox"] == 1


def test_recorder_updates_manifest_after_write(tmp_path: Path) -> None:
    recorder = IngestionInspectionRecorder(
        settings=Settings(_env_file=None),
        output_dir=tmp_path,
        run_id="run-write",
    )
    result = MultimodalIngestionResult(nodes=[_node()], failures=[])
    run_dir = recorder.write_report(
        input_path="data/demo_docs/doc.pdf",
        result=result,
        parser="mineru",
        render_pages=False,
    )

    recorder.update_write_status(
        write_qdrant_requested=True,
        wrote_qdrant=True,
        write_local_index_requested=True,
        wrote_local_index=True,
        recreate_collection_requested=True,
        recreated_collection=True,
        qdrant_collection="docs",
        embedded_count=1,
        upserted_point_count=1,
        build_index_log_mode="synced",
    )

    import json

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["mode"] == "inspect_then_build_index"
    assert manifest["wrote_qdrant"] is True
    assert manifest["wrote_local_index"] is True
    assert manifest["recreated_collection"] is True
    assert manifest["upserted_point_count"] == 1


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


def test_inspect_cli_default_does_not_write_qdrant(tmp_path: Path, monkeypatch, capsys) -> None:
    doc = tmp_path / "doc.md"
    doc.write_text("# Title\n\nbody text", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        ingestion_engine="multimodal",
        multimodal_enabled=True,
        ingestion_inspect_output_dir=str(tmp_path / "inspect"),
        ingestion_inspect_include_mineru_raw=False,
        ingestion_inspect_include_embedding_preview=False,
    )
    calls = {"write": 0}

    monkeypatch.setattr(inspect_ingestion, "get_settings", lambda: settings)
    monkeypatch.setattr(
        inspect_ingestion,
        "build_index_from_nodes",
        lambda *args, **kwargs: calls.__setitem__("write", calls["write"] + 1),
    )
    monkeypatch.setattr("sys.argv", ["inspect_ingestion", str(doc)])

    assert inspect_ingestion.main() == 0
    assert calls["write"] == 0
    assert "- wrote_qdrant: false" in capsys.readouterr().out


def test_inspect_cli_write_qdrant_updates_manifest(tmp_path: Path, monkeypatch) -> None:
    doc = tmp_path / "doc.md"
    doc.write_text("# Title\n\nbody text", encoding="utf-8")
    out_dir = tmp_path / "inspect"
    settings = Settings(
        _env_file=None,
        ingestion_engine="multimodal",
        multimodal_enabled=True,
        ingestion_inspect_output_dir=str(out_dir),
        ingestion_inspect_include_mineru_raw=False,
        ingestion_inspect_include_embedding_preview=False,
    )
    captured = {}

    summary = SimpleNamespace(
        documents=1,
        chunks=1,
        vectors=1,
        upserted=1,
        vector_size=3,
        failed_files=0,
        named_vectors_enabled=False,
        named_vector_counts={},
    )
    build_result = SimpleNamespace(
        summary=summary,
        node_count=1,
        embedded_count=1,
        upserted_point_count=1,
        wrote_qdrant=True,
        wrote_local_index=False,
        recreated_collection=True,
        qdrant_collection="agentic_rag_docs",
        elapsed_ms=12,
        run_id="rid",
    )

    def fake_build_index_from_nodes(nodes, *, source, settings, options):
        captured["node_count"] = len(nodes)
        captured["recreate_collection"] = options.recreate_collection
        captured["write_local_index"] = options.write_local_index
        captured["emit_logs"] = options.emit_logs
        return build_result

    monkeypatch.setattr(inspect_ingestion, "get_settings", lambda: settings)
    monkeypatch.setattr(inspect_ingestion, "build_index_from_nodes", fake_build_index_from_nodes)
    monkeypatch.setattr(
        "sys.argv",
        [
            "inspect_ingestion",
            str(doc),
            "--write-qdrant",
            "--recreate-collection",
            "--no-write-local-index",
            "--no-sync-build-index-logs",
        ],
    )

    assert inspect_ingestion.main() == 0
    assert captured == {
        "node_count": 1,
        "recreate_collection": True,
        "write_local_index": False,
        "emit_logs": False,
    }

    import json

    manifest_path = next((out_dir / "runs").glob("*/manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["mode"] == "inspect_then_build_index"
    assert manifest["wrote_qdrant"] is True
    assert manifest["write_local_index_requested"] is False
    assert manifest["recreated_collection"] is True
    assert (manifest_path.parent / "previews" / "build_index_summary.json").exists()
