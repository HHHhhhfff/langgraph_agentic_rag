from __future__ import annotations

from typing import Any

from agentic_rag.ingestion.node_schema import Node, NodeMetadata
from agentic_rag.ingestion.utils import build_doc_id, guess_title


class NodeNormalizer:
    """Normalize raw parsed elements into unified Node schema."""

    def normalize(
        self,
        *,
        source: str,
        parser_name: str,
        chunk_index: int,
        modality: str,
        text: str | None = None,
        image_path: str | None = None,
        table_markdown: str | None = None,
        formula_latex: str | None = None,
        page: int | None = None,
        title: str | None = None,
        section: str | None = None,
        bbox: list[float] | None = None,
        bbox_items: list[list[float]] | None = None,
        bbox_coordinate_system: str | None = None,
        bbox_source: str | None = None,
        bbox_merge_policy: str | None = None,
        relationships: dict[str, Any] | None = None,
    ) -> Node:
        normalized_modality = modality if modality in {"text", "image", "table", "formula"} else "text"
        doc_id = build_doc_id(source)
        resolved_title = title or guess_title(source)

        node_id = f"{doc_id}:{normalized_modality}:{chunk_index}"
        metadata = NodeMetadata(
            source=source,
            doc_id=doc_id,
            page=page,
            chunk_index=chunk_index,
            title=resolved_title,
            section=section,
            modality=normalized_modality,
            parser_name=parser_name,
            bbox=bbox,
            bbox_items=bbox_items,
            bbox_coordinate_system=bbox_coordinate_system,
            bbox_source=bbox_source,
            bbox_merge_policy=bbox_merge_policy,
        )

        return Node(
            node_id=node_id,
            modality=normalized_modality,
            text=text,
            image_path=image_path,
            table_markdown=table_markdown,
            formula_latex=formula_latex,
            metadata=metadata,
            relationships=relationships or {},
        )
