from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


RetrievalChannel = Literal["vector", "bm25", "page", "table", "relationship", "formula", "image"]
VectorName = Literal["text", "table", "image"]


class RetrievalTask(BaseModel):
    """Single retrieval subtask for future TaskGraph routing."""

    channel: RetrievalChannel
    query_text: str | None = None
    top_k: int = 8
    filters: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)


class RetrievalPlan(BaseModel):
    """Planned retrieval fanout prepared for TaskGraph execution."""

    question: str
    intent: str = "general"
    target_modalities: list[str] = Field(default_factory=lambda: ["text"])
    need_cross_doc: bool = False
    need_page_level: bool = False
    route: str = "text_first"
    retry_count: int = 0
    max_retries: int = 2
    budget_tokens: int = 12000
    budget_ms: int = 30000
    original_query: str | None = None
    query_text: str | None = None
    rewritten_query_text: str | None = None
    retry_actions: list[str] = Field(default_factory=list)
    retry_history: list[dict[str, object]] = Field(default_factory=list)
    page_window: int | None = None
    tasks: list[RetrievalTask] = Field(default_factory=list)

    def channels(self) -> list[RetrievalChannel]:
        """Return channels in execution order without duplicates."""

        seen: set[str] = set()
        channels: list[RetrievalChannel] = []
        for task in self.tasks:
            if task.channel in seen:
                continue
            seen.add(task.channel)
            channels.append(task.channel)
        return channels


def resolve_vector_name(channel: RetrievalChannel, metadata: dict[str, object] | None, settings: Any) -> str | None:
    """Resolve Qdrant named-vector key for a retrieval channel."""

    metadata = metadata or {}
    explicit = metadata.get("vector_name")
    if explicit:
        return str(explicit)
    if channel == "table":
        return str(getattr(settings, "named_vector_table_name", "table"))
    if channel == "image":
        return str(getattr(settings, "named_vector_image_name", "image"))
    if channel in {"vector", "formula"}:
        return str(getattr(settings, "named_vector_text_name", "text"))
    if bool(getattr(settings, "named_vector_fallback_to_text", True)):
        return str(getattr(settings, "named_vector_text_name", "text"))
    return None
