from __future__ import annotations

from typing import Any

from agentic_rag.retrieval.bm25_index import BM25Index
from agentic_rag.schemas import SearchHit
from agentic_rag.store.qdrant_store import QdrantStore


class TableRetriever:
    """Table-only retrieval using table markdown content."""

    def __init__(self, store: QdrantStore):
        self.store = store
        self._index: BM25Index | None = None

    def _load_index(self) -> BM25Index:
        if self._index is None:
            docs = [h for h in self.store.scroll_hits(limit=5000) if h.modality == "table"]
            self._index = BM25Index.build(docs)
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

