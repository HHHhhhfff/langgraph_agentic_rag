from __future__ import annotations

from typing import Any

from agentic_rag.config import Settings
from agentic_rag.retrieval.bm25_index import BM25Index
from agentic_rag.retrieval.index_persistence import load_or_build_bm25_index
from agentic_rag.schemas import SearchHit
from agentic_rag.store.qdrant_store import QdrantStore


class TableRetriever:
    """Table-only retrieval using table markdown content."""

    def __init__(self, store: QdrantStore, settings: Settings | None = None):
        self.store = store
        self.settings = settings or getattr(store, "settings", None) or Settings(retrieval_index_persist_enabled=False)
        self._index: BM25Index | None = None

    def _load_index(self) -> BM25Index:
        if self._index is None:
            self._index = load_or_build_bm25_index(
                self.settings,
                self.store,
                "table",
                stage_logger=getattr(self.store, "stage_logger", None),
            )
        return self._index

    def retrieve(
        self,
        query_text: str,
        *,
        filters: dict[str, Any] | None = None,
        top_k: int = 8,
    ) -> list[SearchHit]:
        final_filters = dict(filters or {})
        final_filters["modality"] = "table"
        hits = self._load_index().search(query_text, top_k=top_k, filters=final_filters)
        for hit in hits:
            hit.channel = "table"
        return hits

