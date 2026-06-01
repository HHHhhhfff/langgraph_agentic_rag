from __future__ import annotations

from agentic_rag.config import Settings
from agentic_rag.ingestion.node_schema import Node, NodeMetadata
from agentic_rag.ingestion.structural_splitter import split_oversized_text_nodes


def _node(node_id: str, text: str, modality: str = "text", relationships: dict | None = None) -> Node:
    return Node(
        node_id=node_id,
        modality=modality,  # type: ignore[arg-type]
        text=text,
        metadata=NodeMetadata(
            source="doc.pdf",
            doc_id="doc",
            page=1,
            chunk_index=1,
            modality=modality,  # type: ignore[arg-type]
        ),
        relationships=relationships or {},
    )


def test_structural_splitter_splits_oversized_text_and_rewrites_relationship_refs() -> None:
    settings = Settings(
        _env_file=None,
        chunk_structural_split_enabled=True,
        chunk_hard_max_chars=120,
        chunk_structural_split_min_part_chars=40,
        chunk_structural_split_overlap=10,
    )
    text = ("Sentence one. Sentence two. Sentence three. " * 8).strip()
    text_node = _node("text:1", text)
    formula = _node("formula:1", "x=1", modality="formula", relationships={"context_node_ids": ["text:1"]})

    result = split_oversized_text_nodes([text_node, formula], settings)

    part_ids = [node.node_id for node in result if node.node_id.startswith("text:1:part:")]
    assert len(part_ids) > 1
    formula_result = next(node for node in result if node.node_id == "formula:1")
    assert formula_result.relationships["context_node_ids"] == part_ids
    first_part = next(node for node in result if node.node_id == part_ids[0])
    assert first_part.relationships["chunk_original_node_id"] == "text:1"


def test_structural_splitter_drops_parent_union_bbox_for_multi_bbox_parts() -> None:
    node = _node("text:1", "alpha. beta. gamma. delta.")
    node = node.model_copy(
        deep=True,
        update={
            "metadata": node.metadata.model_copy(
                update={
                    "bbox": [0, 0, 200, 200],
                    "bbox_items": [[0, 0, 50, 50], [100, 100, 200, 200]],
                    "bbox_coordinate_system": "mineru_content_list_1000",
                    "bbox_source": "content_list",
                    "bbox_merge_policy": "union",
                }
            )
        },
    )
    settings = Settings(
        _env_file=None,
        chunk_structural_split_enabled=True,
        chunk_hard_max_chars=8,
        chunk_structural_split_min_part_chars=4,
        chunk_structural_split_overlap=1,
    )

    parts = split_oversized_text_nodes([node], settings)

    assert len(parts) > 1
    assert all(part.metadata.bbox is None for part in parts)
    assert all(part.metadata.bbox_items is None for part in parts)
    assert all(part.relationships["bbox_dropped_on_structural_split"] is True for part in parts)
