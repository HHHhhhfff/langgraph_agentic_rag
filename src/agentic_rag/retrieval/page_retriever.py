from __future__ import annotations

from collections import defaultdict
from typing import Any

from agentic_rag.retrieval.bm25_index import BM25Index
from agentic_rag.schemas import SearchHit
from agentic_rag.store.qdrant_store import QdrantStore


class PageRetriever:
    """Page-level retrieval by grouping chunks within the same page."""

    def __init__(self, store: QdrantStore):
        self.store = store
        self._index: BM25Index | None = None

    def _load_index(self) -> BM25Index:
        if self._index is None:
            docs = self.store.scroll_hits(limit=5000)
            page_hits: list[SearchHit] = []
            grouped: dict[tuple[str | None, int | None], list[SearchHit]] = defaultdict(list)
            for hit in docs:
                grouped[(hit.doc_id, hit.page)].append(hit)
            for (_, page), hits in grouped.items():
                text = "\n".join(h.text for h in hits if h.text).strip()
                base = hits[0].model_copy(deep=True)
                base.text = text
                base.channel = "page"
                base.score = max((h.score for h in hits), default=0.0)
                page_hits.append(base)
            self._index = BM25Index.build(page_hits)
        return self._index

    def retrieve(
        self,
        query_text: str,
        *,
        page: int | None = None,
        doc_id: str | None = None,
        top_k: int = 8,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        final_filters = dict(filters or {})
        if page is not None:
            final_filters["page"] = page
        if doc_id is not None:
            final_filters["doc_id"] = doc_id
        hits = self._load_index().search(query_text, top_k=top_k, filters=final_filters)
        for hit in hits:
            hit.channel = "page"
        return hits

