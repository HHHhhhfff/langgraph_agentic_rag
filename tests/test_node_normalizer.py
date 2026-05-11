from __future__ import annotations

from agentic_rag.ingestion.node_normalizer import NodeNormalizer


def test_node_normalizer_multimodal_schema() -> None:
    normalizer = NodeNormalizer()
    node = normalizer.normalize(
        source="docs/a.pdf",
        parser_name="llamaparse",
        chunk_index=3,
        modality="table",
        text="|a|b|\n|---|---|\n|1|2|",
        table_markdown="|a|b|\n|---|---|\n|1|2|",
        page=2,
        title="A",
        section="S1",
        relationships={"parent": "x"},
    )

    assert node.modality == "table"
    assert node.table_markdown is not None
    assert node.metadata.source == "docs/a.pdf"
    assert node.metadata.chunk_index == 3
    assert node.metadata.modality == "table"
    assert node.relationships["parent"] == "x"
