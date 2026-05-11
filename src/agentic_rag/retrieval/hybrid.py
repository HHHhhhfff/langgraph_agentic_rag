from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agentic_rag.schemas import SearchHit


class RetrievalBackend(ABC):
    """Pluggable retrieval backend interface."""

    @abstractmethod
    def retrieve(self, query_vector: list[float], filters: dict[str, Any] | None = None) -> list[SearchHit]:
        raise NotImplementedError


class HybridRetriever:
    """Hybrid retrieval interface shell for future keyword/vector fusion."""

    def __init__(self, vector_backend: RetrievalBackend):
        self.vector_backend = vector_backend

    def retrieve(
        self,
        query_vector: list[float],
        filters: dict[str, Any] | None = None,
        query_text: str | None = None,
    ) -> list[SearchHit]:
        # Current baseline: vector-only retrieval.
        # Future: add keyword backend and score fusion with query_text.
        _ = query_text
        return self.vector_backend.retrieve(query_vector=query_vector, filters=filters)
