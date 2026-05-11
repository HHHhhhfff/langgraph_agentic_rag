from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentic_rag.config import Settings
from agentic_rag.retrieval.hybrid import RetrievalBackend
from agentic_rag.schemas import SearchHit
from agentic_rag.store.qdrant_store import QdrantStore, QdrantStoreError


class RetrievalError(RuntimeError):
    """Raised for retrieval stage errors."""


@dataclass(slots=True)
class RetrievalResult:
    """Retrieval output with fallback flag."""

    hits: list[SearchHit]
    fallback_used: bool = False


class VectorRetriever(RetrievalBackend):
    """Vector retriever with metadata filter fallback support."""

    def __init__(self, settings: Settings, store: QdrantStore):
        self.settings = settings
        self.store = store

    def retrieve(
        self,
        query_vector: list[float],
        filters: dict[str, Any] | None = None,
    ) -> RetrievalResult:
        normalized_filters = dict(filters or {})
        if "modality" in normalized_filters and normalized_filters.get("modality") is None:
            normalized_filters.pop("modality", None)
        try:
            hits = self.store.search(
                query_vector=query_vector,
                top_k=self.settings.retrieval_top_k,
                filters=normalized_filters or None,
            )
        except QdrantStoreError as exc:
            raise RetrievalError(f"Qdrant retrieval failed: {exc}") from exc

        filtered_hits = [h for h in hits if h.score >= self.settings.retrieval_min_score]
        if filtered_hits:
            return RetrievalResult(hits=filtered_hits, fallback_used=False)

        if normalized_filters and self.settings.retrieval_filter_fallback:
            try:
                fallback_hits = self.store.search(
                    query_vector=query_vector,
                    top_k=self.settings.retrieval_top_k,
                    filters=None,
                )
            except QdrantStoreError as exc:
                raise RetrievalError(f"Fallback retrieval failed: {exc}") from exc
            fallback_filtered = [h for h in fallback_hits if h.score >= self.settings.retrieval_min_score]
            return RetrievalResult(hits=fallback_filtered, fallback_used=True)

        return RetrievalResult(hits=[], fallback_used=False)
