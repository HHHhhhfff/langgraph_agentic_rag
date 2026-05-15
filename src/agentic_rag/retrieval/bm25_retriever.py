from __future__ import annotations

from typing import Any

from agentic_rag.config import Settings
from agentic_rag.retrieval.bm25_index import BM25Index
from agentic_rag.retrieval.index_persistence import load_or_build_bm25_index
from agentic_rag.schemas import SearchHit
from agentic_rag.store.qdrant_store import QdrantStore


class BM25Retriever:
    """Keyword retriever backed by in-memory BM25 index."""

    def __init__(self, settings: Settings, store: QdrantStore):
        self.settings = settings
        self.store = store
        self._index: BM25Index | None = None

    def _load_index(self) -> BM25Index:
        if self._index is None:
            self._index = load_or_build_bm25_index(
                self.settings,
                self.store,
                "bm25",
                stage_logger=getattr(self.store, "stage_logger", None),
            )
        return self._index

    def retrieve(
        self,
        query_text: str,
        filters: dict[str, Any] | None = None,
        top_k: int | None = None,
    ) -> list[SearchHit]:
        top_n = top_k or self.settings.bm25_top_k
        hits = self._load_index().search(query_text, top_k=top_n, filters=filters)
        for hit in hits:
            hit.channel = "bm25"
        return hits

