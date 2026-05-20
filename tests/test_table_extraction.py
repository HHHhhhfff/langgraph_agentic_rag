from __future__ import annotations

from pathlib import Path

from agentic_rag.config import Settings
from agentic_rag.ingestion.adapters.image_vlm_client import ImageVLMDescription
from agentic_rag.ingestion.adapters.llamaindex_adapter import LlamaIndexAdapter
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
