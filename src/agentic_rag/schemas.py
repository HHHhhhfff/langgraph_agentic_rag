from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DocumentChunk(BaseModel):
    """Chunked document unit used for embedding and storage."""

    chunk_id: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchHit(BaseModel):
    """Retriever output item with payload and vector score."""

    point_id: str
    text: str
    score: float
    modality: str = "text"
    image_path: str | None = None
    table_markdown: str | None = None
    relationships: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    """Answer citation item for traceability."""

    index: int
    source: str
    title: str
    chunk_index: int
    score: float
    tags: list[str] = Field(default_factory=list)


class RAGResult(BaseModel):
    """Final RAG response object."""

    answer: str
    citations: list[Citation] = Field(default_factory=list)
    retrieved_count: int = 0
    used_rerank: bool = False
    fallback_used: bool = False
    debug: dict[str, Any] = Field(default_factory=dict)
