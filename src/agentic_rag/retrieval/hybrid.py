from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentic_rag.config import Settings
from agentic_rag.retrieval.bm25_retriever import BM25Retriever
from agentic_rag.retrieval.fusion import rrf_fuse
from agentic_rag.retrieval.page_retriever import PageRetriever
from agentic_rag.retrieval.relationship_expander import RelationshipExpander
from agentic_rag.retrieval.table_retriever import TableRetriever
from agentic_rag.schemas import SearchHit
from agentic_rag.store.qdrant_store import QdrantStore


@dataclass(slots=True)
class HybridRetrievalResult:
    hits: list[SearchHit]
    route_hits: dict[str, list[SearchHit]]
    expanded_hits: list[SearchHit]


class HybridRetriever:
    """Multi-channel retrieval orchestrator with RRF fusion."""

    def __init__(self, settings: Settings, store: QdrantStore, vector_search_fn):
        self.settings = settings
        self.store = store
        self.vector_search_fn = vector_search_fn
        self.bm25 = BM25Retriever(settings, store)
        self.page = PageRetriever(store)
        self.table = TableRetriever(store)

    def retrieve(
        self,
        *,
        query_text: str,
        query_vector: list[float],
        filters: dict[str, Any] | None = None,
    ) -> HybridRetrievalResult:
        route_hits: dict[str, list[SearchHit]] = {}

        vector_hits = self.vector_search_fn(query_vector=query_vector, filters=filters)
        for hit in vector_hits:
            hit.channel = "vector"
            hit.score_vector = hit.score
        route_hits["vector"] = vector_hits

        bm25_hits: list[SearchHit] = []
        if self.settings.bm25_enabled:
            bm25_hits = self.bm25.retrieve(query_text=query_text, filters=filters, top_k=self.settings.bm25_top_k)
        route_hits["bm25"] = bm25_hits

        page_hits = self.page.retrieve(query_text=query_text, filters=filters, top_k=self.settings.page_top_k)
        route_hits["page"] = page_hits

        table_hits = self.table.retrieve(query_text=query_text, filters=filters, top_k=self.settings.table_top_k)
        route_hits["table"] = table_hits

        fused = rrf_fuse(
            [route_hits["vector"], route_hits["bm25"], route_hits["page"], route_hits["table"]],
            k=self.settings.rrf_k,
            top_k=self.settings.rrf_top_k,
        )
        expander = RelationshipExpander(self.store.scroll_hits(limit=5000))
        expanded = expander.expand(
            fused,
            steps=self.settings.rel_expand_steps,
            page_window=self.settings.rel_expand_pages,
        )
        return HybridRetrievalResult(
            hits=fused,
            route_hits=route_hits,
            expanded_hits=expanded,
        )

