from __future__ import annotations

from typing import Any, Literal


NodeModality = Literal["text", "image", "table", "formula"]

from pydantic import BaseModel, Field


class NodeMetadata(BaseModel):
    """Normalized metadata for multimodal ingestion nodes."""

    source: str
    doc_id: str
    page: int | None = None
    chunk_index: int
    title: str | None = None
    section: str | None = None
    modality: NodeModality
    parser_name: str | None = None
    bbox: list[float] | None = None
    bbox_items: list[list[float]] | None = None
    pages: list[int] | None = None
    bbox_by_page: dict[str, list[list[float]]] | None = None
    page_spans: list[dict[str, Any]] | None = None
    bbox_coordinate_system: str | None = None
    bbox_source: str | None = None
    bbox_merge_policy: str | None = None


class Node(BaseModel):
    """Unified multimodal node schema."""

    node_id: str
    modality: NodeModality
    text: str | None = None
    image_path: str | None = None
    table_markdown: str | None = None
    formula_latex: str | None = None
    metadata: NodeMetadata
    relationships: dict[str, Any] = Field(default_factory=dict)


class IngestionFailure(BaseModel):
    """Failure record for per-file ingestion errors."""

    source: str
    error: str


class MultimodalIngestionResult(BaseModel):
    """Aggregate result for multimodal ingestion run."""

    nodes: list[Node] = Field(default_factory=list)
    failures: list[IngestionFailure] = Field(default_factory=list)
