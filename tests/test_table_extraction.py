from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agentic_rag.config import Settings
from agentic_rag.ingestion.adapters.image_vlm_client import ImageVLMDescription
from agentic_rag.ingestion.adapters.llamaindex_adapter import LlamaIndexAdapter
from agentic_rag.ingestion.adapters.llamaparse_adapter import LlamaParseAdapter
from agentic_rag.ingestion.adapters.mineru_adapter import (
    MinerUAdapter,
    _coalesce_duplicate_table_nodes,
    _merge_heading_only_chunks_with_following,
    _split_large_structured_text,
)
from agentic_rag.ingestion.node_schema import MultimodalIngestionResult, Node, NodeMetadata
from agentic_rag.ingestion.table_extractor import extract_table_blocks, html_table_to_markdown


def _make_mineru_adapter(settings: Settings | None = None) -> MinerUAdapter:
    adapter = object.__new__(MinerUAdapter)
    adapter.settings = settings or Settings(_env_file=None)
    adapter.stage_logger = None
    adapter.run_id = ""
    from agentic_rag.ingestion.chunk_strategies import TableChunker, build_text_chunk_strategy
    from agentic_rag.ingestion.node_normalizer import NodeNormalizer

    adapter.normalizer = NodeNormalizer()
    adapter.text_strategy = build_text_chunk_strategy(adapter.settings)
    adapter.table_chunker = TableChunker()
    return adapter


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


def test_mineru_adapter_preserves_content_list_bbox_in_node_metadata(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    path.write_text("plain text", encoding="utf-8")
    adapter = _make_mineru_adapter(Settings(_env_file=None))
    structured_content = [
        {
            "type": "table",
            "table_body": "<table><tr><td>a</td><td>b</td></tr></table>",
            "page_idx": 0,
            "bbox": [100, 200, 300, 400],
        }
    ]

    nodes = adapter._build_nodes_from_markdown("", path, structured_content=structured_content)  # noqa: SLF001

    table = next(node for node in nodes if node.modality == "table")
    assert table.metadata.bbox == [100.0, 200.0, 300.0, 400.0]
    assert table.metadata.bbox_items == [[100.0, 200.0, 300.0, 400.0]]
    assert table.metadata.bbox_coordinate_system == "mineru_content_list_1000"
    assert table.metadata.bbox_source == "content_list"
    assert table.relationships["bbox"] == [100.0, 200.0, 300.0, 400.0]
    assert table.relationships["bbox_items"] == [[100.0, 200.0, 300.0, 400.0]]


def test_mineru_adapter_does_not_treat_table_caption_or_footnote_as_table(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    path.write_text("plain text", encoding="utf-8")
    adapter = _make_mineru_adapter(Settings(_env_file=None))
    structured_content = [
        {"type": "table_caption", "text": "TABLE I. Caption text.", "page_idx": 0, "bbox": [0, 0, 10, 10]},
        {"type": "table_footnote", "text": "a Footnote text.", "page_idx": 0, "bbox": [0, 20, 10, 30]},
        {
            "type": "table",
            "table_body": "<table><tr><td>a</td><td>b</td></tr></table>",
            "page_idx": 0,
            "bbox": [100, 200, 300, 400],
        },
    ]

    nodes = adapter._build_nodes_from_markdown("", path, structured_content=structured_content)  # noqa: SLF001

    table_nodes = [node for node in nodes if node.modality == "table"]
    text_nodes = [node for node in nodes if node.modality == "text"]
    assert len(table_nodes) == 1
    assert table_nodes[0].metadata.page == 1
    assert "Caption text" in " ".join(node.text or "" for node in text_nodes)
    assert "Footnote text" in " ".join(node.text or "" for node in text_nodes)


def test_mineru_adapter_prefers_paged_content_list_tables_over_unpaged_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    path.write_text("plain text", encoding="utf-8")
    adapter = _make_mineru_adapter(Settings(_env_file=None))
    table_html = "<table><tr><td>a</td><td>b</td></tr></table>"
    structured_content = [
        {"source": "layout.json", "structured_content": [{"type": "table", "table_body": table_html}]},
        {"source": "content_list.json", "structured_content": [{"type": "table", "table_body": table_html, "page_idx": 2}]},
    ]

    nodes = adapter._build_nodes_from_markdown("", path, structured_content=structured_content)  # noqa: SLF001

    table_nodes = [node for node in nodes if node.modality == "table"]
    assert len(table_nodes) == 1
    assert table_nodes[0].metadata.page == 3
    assert table_nodes[0].relationships["mineru_source_kind"] == "content_list"


def test_mineru_adapter_preserves_multiple_bbox_items_for_single_chunk(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    path.write_text("plain text", encoding="utf-8")
    adapter = _make_mineru_adapter(Settings(_env_file=None))
    structured_content = [
        {"type": "text", "text": "alpha", "page_idx": 0, "bbox": [10, 10, 50, 50]},
        {"type": "text", "text": "beta", "page_idx": 0, "bbox": [100, 100, 200, 200]},
    ]

    nodes = adapter._build_nodes_from_markdown("alpha beta", path, structured_content=structured_content)  # noqa: SLF001

    text = next(node for node in nodes if node.modality == "text")
    assert text.metadata.bbox == [10.0, 10.0, 200.0, 200.0]
    assert text.metadata.bbox_items == [[10.0, 10.0, 50.0, 50.0], [100.0, 100.0, 200.0, 200.0]]
    assert text.metadata.bbox_merge_policy == "union"


def test_mineru_adapter_block_aware_text_does_not_merge_across_pages(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    path.write_text("plain text", encoding="utf-8")
    adapter = _make_mineru_adapter(Settings(_env_file=None))
    structured_content = [
        {"type": "text", "text": "alpha beta", "page_idx": 0, "bbox": [10, 10, 50, 50]},
        {"type": "text", "text": "alpha", "page_idx": 1, "bbox": [100, 100, 200, 200]},
    ]

    nodes = adapter._build_nodes_from_markdown("alpha beta alpha", path, structured_content=structured_content)  # noqa: SLF001

    text_nodes = [node for node in nodes if node.modality == "text"]
    assert [node.metadata.page for node in text_nodes] == [1, 2]
    assert text_nodes[0].metadata.bbox == [10.0, 10.0, 50.0, 50.0]
    assert text_nodes[1].metadata.bbox == [100.0, 100.0, 200.0, 200.0]
    assert all(node.relationships["mineru_text_chunk_policy"] == "block_aware_v1" for node in text_nodes)
    assert all(node.relationships["mineru_text_cross_page"] is False for node in text_nodes)


def test_mineru_adapter_block_aware_text_splits_large_single_block_with_inherited_bbox(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    path.write_text("plain text", encoding="utf-8")
    adapter = _make_mineru_adapter(
        Settings(_env_file=None, chunk_size=80, chunk_overlap=10, chunk_hard_max_chars=120)
    )
    large_text = "Sentence one. Sentence two. Sentence three. Sentence four. " * 4
    structured_content = [
        {"type": "text", "text": large_text, "page_idx": 0, "bbox": [10, 10, 300, 300]},
    ]

    nodes = adapter._build_nodes_from_markdown(large_text, path, structured_content=structured_content)  # noqa: SLF001

    text_nodes = [node for node in nodes if node.modality == "text"]
    assert len(text_nodes) > 1
    assert all(node.metadata.page == 1 for node in text_nodes)
    assert all(node.metadata.bbox == [10.0, 10.0, 300.0, 300.0] for node in text_nodes)
    assert all(
        node.relationships["mineru_text_chunk_policy"] == "block_aware_split_large_block_v1"
        for node in text_nodes
    )
    assert [node.relationships["chunk_part_index"] for node in text_nodes] == list(range(len(text_nodes)))


def test_mineru_adapter_large_text_overlap_starts_on_sentence_boundary(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    path.write_text("plain text", encoding="utf-8")
    adapter = _make_mineru_adapter(
        Settings(_env_file=None, chunk_size=80, chunk_overlap=30, chunk_hard_max_chars=120)
    )
    large_text = (
        "Sentence one has enough detail for the first part. "
        "Sentence two has enough detail for the overlap region. "
        "Sentence three has enough detail for the next part. "
        "Sentence four has enough detail for the final part. "
    ) * 3
    structured_content = [
        {"type": "text", "text": large_text, "page_idx": 0, "bbox": [10, 10, 300, 300]},
    ]

    nodes = adapter._build_nodes_from_markdown(large_text, path, structured_content=structured_content)  # noqa: SLF001

    text_nodes = [node for node in nodes if node.modality == "text"]
    assert len(text_nodes) > 1
    assert all(node.text.startswith("Sentence ") for node in text_nodes)


def test_mineru_large_text_uses_semantic_chunks_before_boundary_repair() -> None:
    class FakeStrategy:
        def chunk_text(self, text: str) -> list[str]:
            return [
                "Intro sentence. Semantic marker A.",
                "marker A. Semantic marker B. Final sentence.",
            ]

    text = "Intro sentence. Semantic marker A. Semantic marker B. Final sentence."

    chunks = _split_large_structured_text(text, FakeStrategy(), max_chars=200, overlap=30)

    assert chunks == [
        "Intro sentence. Semantic marker A.",
        "Semantic marker B. Final sentence.",
    ]


def test_mineru_adapter_keeps_medium_single_structured_block_intact_until_hard_max(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    path.write_text("plain text", encoding="utf-8")
    adapter = _make_mineru_adapter(
        Settings(_env_file=None, chunk_size=80, chunk_overlap=10, chunk_hard_max_chars=500)
    )
    medium_text = "Sentence one. Sentence two. Sentence three. Sentence four. " * 4
    structured_content = [
        {"type": "text", "text": medium_text, "page_idx": 0, "bbox": [10, 10, 300, 300]},
    ]

    nodes = adapter._build_nodes_from_markdown(medium_text, path, structured_content=structured_content)  # noqa: SLF001

    text_nodes = [node for node in nodes if node.modality == "text"]
    assert len(text_nodes) == 1
    assert text_nodes[0].text == medium_text.strip()
    assert text_nodes[0].relationships["mineru_text_chunk_policy"] == "block_aware_v1"


def test_mineru_adapter_heading_starts_new_structured_text_group(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    path.write_text("plain text", encoding="utf-8")
    adapter = _make_mineru_adapter(Settings(_env_file=None, chunk_size=500, chunk_hard_max_chars=1000))
    structured_content = [
        {"type": "text", "text": "Previous section final paragraph.", "page_idx": 0, "bbox": [10, 10, 300, 80]},
        {"type": "title", "text": "III. RESULTS AND DISCUSSION", "page_idx": 0, "bbox": [50, 100, 250, 120]},
        {"type": "text", "text": "Three new bound states were observed.", "page_idx": 0, "bbox": [10, 140, 300, 200]},
    ]

    nodes = adapter._build_nodes_from_markdown(
        "Previous section final paragraph.\n\nIII. RESULTS AND DISCUSSION\n\nThree new bound states were observed.",
        path,
        structured_content=structured_content,
    )  # noqa: SLF001

    text_nodes = [node for node in nodes if node.modality == "text"]
    assert [node.text for node in text_nodes] == [
        "Previous section final paragraph.",
        "# III. RESULTS AND DISCUSSION\n\nThree new bound states were observed.",
    ]


def test_mineru_adapter_text_level_heading_starts_new_structured_text_group(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    path.write_text("plain text", encoding="utf-8")
    adapter = _make_mineru_adapter(Settings(_env_file=None, chunk_size=500, chunk_hard_max_chars=1000))
    structured_content = [
        {"type": "text", "text": "Previous section final paragraph.", "page_idx": 0, "bbox": [10, 10, 300, 80]},
        {"type": "text", "text": "III. RESULTS AND DISCUSSION", "text_level": 1, "page_idx": 0, "bbox": [50, 100, 250, 120]},
        {"type": "text", "text": "Three new bound states were observed.", "page_idx": 0, "bbox": [10, 140, 300, 200]},
    ]

    nodes = adapter._build_nodes_from_markdown(
        "Previous section final paragraph.\n\nIII. RESULTS AND DISCUSSION\n\nThree new bound states were observed.",
        path,
        structured_content=structured_content,
    )  # noqa: SLF001

    text_nodes = [node for node in nodes if node.modality == "text"]
    assert [node.text for node in text_nodes] == [
        "Previous section final paragraph.",
        "# III. RESULTS AND DISCUSSION\n\nThree new bound states were observed.",
    ]
    assert text_nodes[1].relationships["mineru_text_block_types"] == ["heading", "text"]
    assert text_nodes[1].relationships["mineru_text_raw_types"] == ["text_level_1", "text"]


def test_mineru_adapter_title_regions_do_not_mix_previous_body_with_next_heading(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    path.write_text("plain text", encoding="utf-8")
    adapter = _make_mineru_adapter(Settings(_env_file=None, chunk_size=2000, chunk_hard_max_chars=3000))
    structured_content = [
        {"type": "text", "text": "Previous section final paragraph.", "page_idx": 0, "bbox": [10, 10, 300, 80]},
        {"type": "text", "text": "III. RESULTS", "text_level": 1, "page_idx": 0, "bbox": [50, 100, 250, 120]},
        {"type": "text", "text": "AND DISCUSSION", "text_level": 2, "page_idx": 0, "bbox": [50, 125, 250, 145]},
        {"type": "text", "text": "Three new bound states were observed.", "page_idx": 0, "bbox": [10, 160, 300, 220]},
        {"type": "title", "text": "IV. CONCLUSION", "page_idx": 0, "bbox": [50, 250, 250, 270]},
        {"type": "text", "text": "The conclusion summarizes the result.", "page_idx": 0, "bbox": [10, 290, 300, 350]},
    ]

    nodes = adapter._build_nodes_from_markdown(
        "\n\n".join(item["text"] for item in structured_content),
        path,
        structured_content=structured_content,
    )  # noqa: SLF001

    text_nodes = [node for node in nodes if node.modality == "text"]
    assert [node.text for node in text_nodes] == [
        "Previous section final paragraph.",
        "# III. RESULTS\n\n# AND DISCUSSION\n\nThree new bound states were observed.",
        "# IV. CONCLUSION\n\nThe conclusion summarizes the result.",
    ]
    assert text_nodes[0].relationships.get("mineru_title_region_id") is None
    assert text_nodes[1].relationships["mineru_title_region_id"] == 1
    assert text_nodes[2].relationships["mineru_title_region_id"] == 2
    assert all(not node.relationships.get("mineru_text_cross_title_region") for node in text_nodes)


def test_mineru_adapter_large_split_attaches_heading_to_first_body_chunk(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    path.write_text("plain text", encoding="utf-8")
    adapter = _make_mineru_adapter(Settings(_env_file=None, chunk_size=200, chunk_overlap=20, chunk_hard_max_chars=100))
    adapter.text_strategy = SimpleNamespace(
        chunk_text=lambda text: [
            "# II. EXPERIMENTAL METHODS",
            text.split("\n\n", 1)[1],
        ]
    )
    body = " ".join(["This section describes the experimental method in detail."] * 8)
    structured_content = [
        {"type": "text", "text": "II. EXPERIMENTAL METHODS", "text_level": 1, "page_idx": 0, "bbox": [50, 100, 250, 120]},
        {"type": "text", "text": body, "page_idx": 0, "bbox": [10, 140, 300, 700]},
    ]

    nodes = adapter._build_nodes_from_markdown(
        f"II. EXPERIMENTAL METHODS\n\n{body}",
        path,
        structured_content=structured_content,
    )  # noqa: SLF001

    text_nodes = [node for node in nodes if node.modality == "text"]
    assert text_nodes
    assert text_nodes[0].text.startswith("# II. EXPERIMENTAL METHODS\n\nThis section describes")
    assert not any(node.text.strip() == "# II. EXPERIMENTAL METHODS" for node in text_nodes)
    assert text_nodes[0].relationships["mineru_text_block_types"][:2] == ["heading", "text"]


def test_merge_heading_only_chunks_with_following_body() -> None:
    chunks = ["# A\n\n# B", "Body sentence. More body.", "# C"]

    assert _merge_heading_only_chunks_with_following(chunks) == [
        "# A\n\n# B\n\nBody sentence. More body.",
        "# C",
    ]


def test_mineru_adapter_merges_cross_page_text_when_markdown_same_paragraph(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    path.write_text("plain text", encoding="utf-8")
    adapter = _make_mineru_adapter(Settings(_env_file=None, chunk_size=800, chunk_hard_max_chars=1200))
    left = "Laser cooling enables experiments at low temperatures and cycling transitions"
    right = "between these states are uncommon in molecular anions."
    structured_content = [
        {"type": "text", "text": left, "page_idx": 0, "bbox": [0.08, 0.86, 0.92, 0.94]},
        {"type": "page_number", "text": "1", "page_idx": 0, "bbox": [0.50, 0.96, 0.52, 0.98]},
        {"type": "text", "text": right, "page_idx": 1, "bbox": [0.08, 0.05, 0.92, 0.14]},
    ]
    markdown = f"{left} {right}"

    nodes = adapter._build_nodes_from_markdown(markdown, path, structured_content=structured_content)  # noqa: SLF001

    text_nodes = [node for node in nodes if node.modality == "text"]
    assert len(text_nodes) == 1
    node = text_nodes[0]
    assert left in (node.text or "")
    assert right in (node.text or "")
    assert node.metadata.page == 1
    assert node.metadata.pages == [1, 2]
    assert node.metadata.bbox is None
    assert node.metadata.bbox_merge_policy == "multi_page_by_page"
    assert sorted((node.metadata.bbox_by_page or {}).keys()) == ["1", "2"]
    assert len(node.metadata.page_spans or []) == 2
    assert node.relationships["mineru_cross_page_merge"] is True
    assert node.relationships["mineru_cross_page_merge_reason"] == "markdown_same_paragraph_page_boundary"


def test_mineru_adapter_does_not_merge_cross_page_text_when_markdown_separate_paragraphs(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    path.write_text("plain text", encoding="utf-8")
    adapter = _make_mineru_adapter(Settings(_env_file=None, chunk_size=800, chunk_hard_max_chars=1200))
    left = "This paragraph ends on the first page."
    right = "A new paragraph starts on the next page."
    structured_content = [
        {"type": "text", "text": left, "page_idx": 0, "bbox": [0.08, 0.86, 0.92, 0.94]},
        {"type": "page_number", "text": "1", "page_idx": 0, "bbox": [0.50, 0.96, 0.52, 0.98]},
        {"type": "text", "text": right, "page_idx": 1, "bbox": [0.08, 0.05, 0.92, 0.14]},
    ]
    markdown = f"{left}\n\n{right}"

    nodes = adapter._build_nodes_from_markdown(markdown, path, structured_content=structured_content)  # noqa: SLF001

    text_nodes = [node for node in nodes if node.modality == "text"]
    assert len(text_nodes) == 2
    assert [node.metadata.page for node in text_nodes] == [1, 2]
    assert all(node.metadata.pages is None for node in text_nodes)
    assert all(not node.relationships.get("mineru_cross_page_merge") for node in text_nodes)


def test_mineru_adapter_skips_chart_text_and_markdown_table_fallback_when_structured_blocks_exist(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    markdown = """Intro paragraph.

| x | y |
| --- | --- |
| Peak 1 | Peak 2 |
"""
    path.write_text(markdown, encoding="utf-8")
    adapter = _make_mineru_adapter(Settings(_env_file=None, mineru_table_markdown_fallback=True))
    structured_content = [
        {"type": "text", "text": "Intro paragraph.", "page_idx": 0, "bbox": [0, 0, 10, 10]},
        {"type": "chart", "text": markdown, "page_idx": 0, "bbox": [20, 20, 100, 100]},
    ]

    nodes = adapter._build_nodes_from_markdown(markdown, path, structured_content=structured_content)  # noqa: SLF001

    assert not any(node.modality == "table" for node in nodes)
    text_nodes = [node for node in nodes if node.modality == "text"]
    assert len(text_nodes) == 1
    assert text_nodes[0].text == "Intro paragraph."


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


def test_mineru_adapter_skips_markdown_table_covered_by_structured_table(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    markdown = """Intro

| 月份 | 销售额(万元) |
| --- | --- |
| 1月 | 100 |
| 2月 | 120 |
"""
    path.write_text(markdown, encoding="utf-8")
    adapter = object.__new__(MinerUAdapter)
    settings = Settings(_env_file=None, mineru_table_second_pass_enabled=False)
    adapter.settings = settings
    adapter.stage_logger = None
    adapter.run_id = ""
    from agentic_rag.ingestion.chunk_strategies import TableChunker, build_text_chunk_strategy
    from agentic_rag.ingestion.node_normalizer import NodeNormalizer

    adapter.normalizer = NodeNormalizer()
    adapter.text_strategy = build_text_chunk_strategy(settings)
    adapter.table_chunker = TableChunker()

    structured_content = [
        {
            "type": "table",
            "table_body": "<table><tr><td>月份</td><td>销售额(万元)</td></tr><tr><td>1月</td><td>100</td></tr><tr><td>2月</td><td>120</td></tr></table>",
            "page_idx": 0,
        }
    ]

    nodes = adapter._build_nodes_from_markdown(markdown, path, structured_content=structured_content)  # noqa: SLF001
    table_nodes = [node for node in nodes if node.modality == "table"]

    assert len(table_nodes) == 1
    assert table_nodes[0].metadata.page == 1
    assert table_nodes[0].relationships["mineru_block_type"] == "table"


def test_mineru_adapter_coalesces_duplicate_structured_tables(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    markdown = "Intro text."
    path.write_text(markdown, encoding="utf-8")
    adapter = _make_mineru_adapter(Settings(_env_file=None))
    table_html = (
        "<table>"
        "<tr><td>month</td><td>sales</td></tr>"
        "<tr><td>Jan</td><td>100</td></tr>"
        "<tr><td>Feb</td><td>120</td></tr>"
        "</table>"
    )
    structured_content = [
        {"type": "table", "table_body": table_html, "page_idx": 0},
        {"type": "table", "content": {"html": table_html, "table_type": "simple_table"}},
    ]

    nodes = adapter._build_nodes_from_markdown(markdown, path, structured_content=structured_content)  # noqa: SLF001
    table_nodes = [node for node in nodes if node.modality == "table"]

    assert len(table_nodes) == 1
    assert table_nodes[0].metadata.page == 1
    assert table_nodes[0].relationships["mineru_source_kind"] == "content_list"
    assert table_nodes[0].relationships["mineru_raw_type"] == "table"
    assert table_nodes[0].relationships["page_source"] == "structured"


def test_mineru_adapter_coalesces_page_null_duplicate_from_any_structured_source(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    markdown = "Intro text."
    path.write_text(markdown, encoding="utf-8")
    adapter = _make_mineru_adapter(Settings(_env_file=None))
    table_html = (
        "<table>"
        "<tr><td>month</td><td>sales</td></tr>"
        "<tr><td>Jan</td><td>100</td></tr>"
        "</table>"
    )
    structured_content = [
        {"source": "content_list.json", "structured_content": [{"type": "table", "table_body": table_html, "page_idx": 0}]},
        {"source": "layout.json", "structured_content": [{"type": "table", "content": {"html": table_html}}]},
    ]

    nodes = adapter._build_nodes_from_markdown(markdown, path, structured_content=structured_content)  # noqa: SLF001
    table_nodes = [node for node in nodes if node.modality == "table"]

    assert len(table_nodes) == 1
    assert table_nodes[0].metadata.page == 1
    assert table_nodes[0].relationships["mineru_source_kind"] == "content_list"


def test_mineru_adapter_can_disable_structured_table_coalescing(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    markdown = "Intro text."
    path.write_text(markdown, encoding="utf-8")
    adapter = _make_mineru_adapter(
        Settings(
            _env_file=None,
            mineru_structured_table_coalesce_enabled=False,
            mineru_table_exact_content_coalesce_enabled=False,
        )
    )
    table_html = (
        "<table>"
        "<tr><td>month</td><td>sales</td></tr>"
        "<tr><td>Jan</td><td>100</td></tr>"
        "</table>"
    )
    structured_content = [
        {"type": "table", "table_body": table_html, "page_idx": 0},
        {"type": "table", "content": {"html": table_html}},
    ]

    nodes = adapter._build_nodes_from_markdown(markdown, path, structured_content=structured_content)  # noqa: SLF001

    assert len([node for node in nodes if node.modality == "table"]) == 2


def test_mineru_adapter_coalesces_exact_duplicate_table_nodes(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    markdown = "Intro text."
    path.write_text(markdown, encoding="utf-8")
    adapter = _make_mineru_adapter(Settings(_env_file=None))
    table_html = (
        "<table>"
        "<tr><td>month</td><td>sales</td></tr>"
        "<tr><td>Jan</td><td>100</td></tr>"
        "</table>"
    )
    structured_content = [
        {"type": "table", "table_body": table_html, "page_idx": 0},
        {"type": "table", "table_body": table_html, "page_idx": 0},
    ]

    nodes = adapter._build_nodes_from_markdown(markdown, path, structured_content=structured_content)  # noqa: SLF001
    table_nodes = [node for node in nodes if node.modality == "table"]

    assert len(table_nodes) == 1
    assert table_nodes[0].metadata.page == 1
    assert table_nodes[0].relationships["table_duplicate_count"] == 2
    assert table_nodes[0].relationships["merged_table_node_ids"]
    assert table_nodes[0].relationships["merged_table_occurrences"][0]["page"] == 1


def test_mineru_adapter_can_disable_exact_duplicate_table_node_coalescing(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    markdown = "Intro text."
    path.write_text(markdown, encoding="utf-8")
    adapter = _make_mineru_adapter(Settings(_env_file=None, mineru_table_exact_content_coalesce_enabled=False))
    table_html = (
        "<table>"
        "<tr><td>month</td><td>sales</td></tr>"
        "<tr><td>Jan</td><td>100</td></tr>"
        "</table>"
    )
    structured_content = [
        {"type": "table", "table_body": table_html, "page_idx": 0},
        {"type": "table", "table_body": table_html, "page_idx": 0},
    ]

    nodes = adapter._build_nodes_from_markdown(markdown, path, structured_content=structured_content)  # noqa: SLF001

    assert len([node for node in nodes if node.modality == "table"]) == 2


def test_mineru_adapter_keeps_chart_table_like_structured_blocks(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    markdown = "Intro text."
    path.write_text(markdown, encoding="utf-8")
    adapter = _make_mineru_adapter(
        Settings(_env_file=None, mineru_table_exact_content_coalesce_keep_chart_separate=True)
    )
    table_html = (
        "<table>"
        "<tr><td>month</td><td>sales</td></tr>"
        "<tr><td>Jan</td><td>100</td></tr>"
        "</table>"
    )
    structured_content = [
        {"type": "table", "table_body": table_html, "page_idx": 0},
        {"type": "chart_table", "content": {"html": table_html}, "page_idx": 1},
    ]

    nodes = adapter._build_nodes_from_markdown(markdown, path, structured_content=structured_content)  # noqa: SLF001
    table_nodes = [node for node in nodes if node.modality == "table"]

    assert len(table_nodes) == 2
    assert {node.metadata.page for node in table_nodes} == {1, 2}


def test_mineru_adapter_keeps_same_page_tables_with_different_values(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    markdown = "Intro text."
    path.write_text(markdown, encoding="utf-8")
    adapter = _make_mineru_adapter(Settings(_env_file=None))
    first_html = (
        "<table>"
        "<tr><td>month</td><td>sales</td></tr>"
        "<tr><td>Jan</td><td>100</td></tr>"
        "</table>"
    )
    second_html = (
        "<table>"
        "<tr><td>month</td><td>sales</td></tr>"
        "<tr><td>Jan</td><td>900</td></tr>"
        "</table>"
    )
    structured_content = [
        {"type": "table", "table_body": first_html, "page_idx": 0, "bbox": [0, 0, 100, 100]},
        {"type": "table", "table_body": second_html, "page_idx": 0, "bbox": [120, 0, 220, 100]},
    ]

    nodes = adapter._build_nodes_from_markdown(markdown, path, structured_content=structured_content)  # noqa: SLF001
    table_nodes = [node for node in nodes if node.modality == "table"]

    assert len(table_nodes) == 2
    assert len({node.table_markdown for node in table_nodes if node.table_markdown}) == 2


def test_mineru_adapter_keeps_same_content_tables_with_different_bboxes(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    markdown = "Intro text."
    path.write_text(markdown, encoding="utf-8")
    adapter = _make_mineru_adapter(Settings(_env_file=None, mineru_table_exact_content_coalesce_enabled=False))
    table_html = (
        "<table>"
        "<tr><td>month</td><td>sales</td></tr>"
        "<tr><td>Jan</td><td>100</td></tr>"
        "</table>"
    )
    structured_content = [
        {"type": "table", "table_body": table_html, "page_idx": 0, "bbox": [0, 0, 100, 100]},
        {"type": "table", "table_body": table_html, "page_idx": 0, "bbox": [200, 0, 300, 100]},
    ]

    nodes = adapter._build_nodes_from_markdown(markdown, path, structured_content=structured_content)  # noqa: SLF001
    table_nodes = [node for node in nodes if node.modality == "table"]

    assert len(table_nodes) == 2


def test_duplicate_table_node_coalescing_does_not_cross_documents() -> None:
    table = "| month | sales |\n| --- | --- |\n| Jan | 100 |"
    first = Node(
        node_id="doc-a:table:0",
        modality="table",
        text=table,
        table_markdown=table,
        metadata=NodeMetadata(
            source="a.pdf",
            doc_id="doc-a",
            page=1,
            chunk_index=0,
            modality="table",
            parser_name="mineru",
        ),
        relationships={"source_parser": "mineru", "mineru_block_type": "table"},
    )
    second = Node(
        node_id="doc-b:table:0",
        modality="table",
        text=table,
        table_markdown=table,
        metadata=NodeMetadata(
            source="b.pdf",
            doc_id="doc-b",
            page=1,
            chunk_index=0,
            modality="table",
            parser_name="mineru",
        ),
        relationships={"source_parser": "mineru", "mineru_block_type": "table"},
    )

    nodes = _coalesce_duplicate_table_nodes([first, second], keep_chart_separate=False)

    assert len(nodes) == 2


def test_mineru_adapter_formula_filters_and_groups_display_formulas(tmp_path: Path) -> None:
    path = tmp_path / "formula.md"
    markdown = r"""A. Author $^{1,2,*}$ cites $[12]$ and inline $E=mc^2$.

$$
a=b
$$

$$
b=c
$$
"""
    path.write_text(markdown, encoding="utf-8")
    adapter = object.__new__(MinerUAdapter)
    settings = Settings(_env_file=None)
    adapter.settings = settings
    adapter.stage_logger = None
    adapter.run_id = ""
    from agentic_rag.ingestion.chunk_strategies import TableChunker, build_text_chunk_strategy
    from agentic_rag.ingestion.node_normalizer import NodeNormalizer

    adapter.normalizer = NodeNormalizer()
    adapter.text_strategy = build_text_chunk_strategy(settings)
    adapter.table_chunker = TableChunker()

    nodes = adapter._build_nodes_from_markdown(markdown, path, structured_content=[])  # noqa: SLF001
    formula_nodes = [node for node in nodes if node.modality == "formula"]

    assert len(formula_nodes) == 1
    assert formula_nodes[0].formula_latex == "a=b\n\nb=c"
    assert formula_nodes[0].relationships["formula_group"] is True
    assert formula_nodes[0].relationships["formula_count"] == 2


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
    assert text_node.relationships["page_node_id"] == f"{text_node.metadata.doc_id}:page:2"
    assert table_node.metadata.page == 3
    assert table_node.metadata.section == "Tables"


def test_mineru_adapter_links_context_relationships(tmp_path: Path) -> None:
    path = tmp_path / "docx.md"
    path.write_text(
        """Before table explanation.

| metric | value |
| --- | --- |
| accuracy | 95 |

After table explanation.

$$
E=mc^2
$$
""",
        encoding="utf-8",
    )
    adapter = object.__new__(MinerUAdapter)
    settings = Settings(_env_file=None, mineru_context_link_enabled=True)
    adapter.settings = settings
    adapter.stage_logger = None
    adapter.run_id = ""
    from agentic_rag.ingestion.chunk_strategies import TableChunker, build_text_chunk_strategy
    from agentic_rag.ingestion.node_normalizer import NodeNormalizer

    adapter.normalizer = NodeNormalizer()
    adapter.text_strategy = build_text_chunk_strategy(settings)
    adapter.table_chunker = TableChunker()

    nodes = adapter._build_nodes_from_markdown(path.read_text(encoding="utf-8"), path, structured_content=[])  # noqa: SLF001
    table_node = next(node for node in nodes if node.modality == "table")
    formula_node = next(node for node in nodes if node.modality == "formula")
    text_nodes = [node for node in nodes if node.modality == "text"]

    assert table_node.relationships.get("context_node_ids")
    assert table_node.relationships.get("prev_id") or table_node.relationships.get("next_id")
    assert formula_node.relationships.get("context_node_ids")
    assert any(table_node.node_id in node.relationships.get("related_table_node_ids", []) for node in text_nodes)
    assert any(formula_node.node_id in node.relationships.get("related_formula_node_ids", []) for node in text_nodes)


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
