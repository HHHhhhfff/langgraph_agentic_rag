from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


RetrievalChannel = Literal["vector", "bm25", "page", "table", "relationship"]


class RetrievalTask(BaseModel):
    """Single retrieval subtask for future TaskGraph routing."""

    channel: RetrievalChannel
    query_text: str | None = None
    top_k: int = 8
    filters: dict[str, object] = Field(default_factory=dict)


class RetrievalPlan(BaseModel):
    """Planned retrieval fanout prepared for TaskGraph execution."""

    question: str
    intent: str = "general"
    target_modality: str = "text"
    need_cross_doc: bool = False
    need_page_level: bool = False
    tasks: list[RetrievalTask] = Field(default_factory=list)

