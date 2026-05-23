from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agentic_rag.config import Settings
from agentic_rag.ingestion.adapters.image_vlm_client import ImageVLMDescription
from agentic_rag.ingestion.adapters.llamaindex_adapter import LlamaIndexAdapter
from agentic_rag.ingestion.adapters.llamaparse_adapter import LlamaParseAdapter
from agentic_rag.ingestion.adapters.mineru_adapter import MinerUAdapter
from agentic_rag.ingestion.node_schema import MultimodalIngestionResult
from agentic_rag.ingestion.table_extractor import extract_table_blocks, html_table_to_markdown


def test_extract_table_blocks_supports_markdown_and_html() -> None:
    text = """hello

| a | b |
| --- | --- |
| 1 | 2 |

<table><tr><td>x</td><td>y</td></tr></table>
"""
    blocks = extract_table_blocks(text)
    assert len(blocks) == 2
    assert blocks[0].kind == "markdown"
    assert blocks[1].kind == "html"
    assert "a" in blocks[0].markdown
    assert "x" in blocks[1].markdown or html_table_to_markdown("<table><tr><td>x</td><td>y</td></tr></table>")


def test_image_vlm_scene_type_normalization() -> None:
    desc = ImageVLMDescription(
        caption="cap",
        scene_type="\u7f51\u9875\u622a\u56fe",
        visible_text_summary="text",
    )
    assert desc.scene_type == "screenshot"


def test_llamaindex_adapter_extracts_table_nodes(tmp_path: Path) -> None:
    path = tmp_path / "table.md"
    path.write_text(
        """title\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\ntext body with $x^2$.\n""",
        encoding="utf-8",
    )
    adapter = LlamaIndexAdapter(Settings())

    result = adapter.parse_file(path)

    assert any(node.modality == "table" for node in result.nodes)
    assert any(node.modality == "text" for node in result.nodes)
    assert all(node.modality != "formula" for node in result.nodes if node.modality == "table")


def test_mineru_adapter_extracts_html_table_nodes(tmp_path: Path) -> None:
    path = tmp_path / "docx.md"
    path.write_text("plain text", encoding="utf-8")
    adapter = object.__new__(MinerUAdapter)
    settings = Settings()
    adapter.settings = settings
    adapter.stage_logger = None
    adapter.run_id = ""
    from agentic_rag.ingestion.node_normalizer import NodeNormalizer
    from agentic_rag.ingestion.chunk_strategies import TableChunker, build_text_chunk_strategy

    adapter.normalizer = NodeNormalizer()
    adapter.text_strategy = build_text_chunk_strategy(settings)
    adapter.table_chunker = TableChunker()

    structured_content = [{"type": "table", "table_body": "<table><tr><td>a</td><td>b</td></tr></table>"}]
    nodes = adapter._build_nodes_from_markdown(path.read_text(encoding="utf-8"), path, structured_content=structured_content)  # noqa: SLF001

    assert any(node.modality == "table" for node in nodes)
    assert any(node.modality == "text" for node in nodes)


def test_mineru_adapter_keeps_markdown_table_when_text_nodes_exist(tmp_path: Path) -> None:
    path = tmp_path / "docx.md"
    path.write_text(
        """# Section

plain text

| metric | value |
| --- | --- |
| accuracy | 95 |
""",
        encoding="utf-8",
    )
    adapter = object.__new__(MinerUAdapter)
    settings = Settings()
    adapter.settings = settings
    adapter.stage_logger = None
    adapter.run_id = ""
    from agentic_rag.ingestion.chunk_strategies import TableChunker, build_text_chunk_strategy
    from agentic_rag.ingestion.node_normalizer import NodeNormalizer

    adapter.normalizer = NodeNormalizer()
    adapter.text_strategy = build_text_chunk_strategy(settings)
    adapter.table_chunker = TableChunker()

    nodes = adapter._build_nodes_from_markdown(path.read_text(encoding="utf-8"), path, structured_content=[])  # noqa: SLF001

    assert any(node.modality == "text" for node in nodes)
    table_nodes = [node for node in nodes if node.modality == "table"]
    assert len(table_nodes) == 1
    assert "accuracy" in (table_nodes[0].table_markdown or "")


def test_mineru_adapter_preserves_structured_page_and_section_metadata(tmp_path: Path) -> None:
    path = tmp_path / "docx.md"
    path.write_text("# Intro\n\nTaskGraph text evidence.\n", encoding="utf-8")
    adapter = object.__new__(MinerUAdapter)
    settings = Settings()
    adapter.settings = settings
    adapter.stage_logger = None
    adapter.run_id = ""
    from agentic_rag.ingestion.chunk_strategies import TableChunker, build_text_chunk_strategy
    from agentic_rag.ingestion.node_normalizer import NodeNormalizer

    adapter.normalizer = NodeNormalizer()
    adapter.text_strategy = build_text_chunk_strategy(settings)
    adapter.table_chunker = TableChunker()

    structured_content = [
        {"type": "text", "text": "TaskGraph text evidence.", "page_idx": 1, "section_title": "Intro"},
        {
            "type": "table",
            "table_body": "<table><tr><td>a</td><td>b</td></tr></table>",
            "page_number": 3,
            "section_title": "Tables",
        },
    ]

    nodes = adapter._build_nodes_from_markdown(path.read_text(encoding="utf-8"), path, structured_content=structured_content)  # noqa: SLF001
    text_node = next(node for node in nodes if node.modality == "text")
    table_node = next(node for node in nodes if node.modality == "table")

    assert text_node.metadata.page == 2
    assert text_node.metadata.section == "Intro"
    assert text_node.relationships["source_parser"] == "mineru"
    assert text_node.relationships["page_node_id"] == "page:2"
    assert table_node.metadata.page == 3
    assert table_node.metadata.section == "Tables"


def test_llamaparse_adapter_preserves_doc_metadata_page_and_section(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.4")
    adapter = LlamaParseAdapter(Settings())
    monkeypatch.setattr(
        adapter,
        "_parse_pdf_once",
        lambda _: [
            SimpleNamespace(
                text="TaskGraph page text",
                metadata={"page_label": "7", "section_title": "Research"},
            )
        ],
    )

    result = adapter.parse_file(path)

    assert result.failures == []
    assert result.nodes
    assert all(node.metadata.page == 7 for node in result.nodes)
    assert all(node.metadata.section == "Research" for node in result.nodes)
