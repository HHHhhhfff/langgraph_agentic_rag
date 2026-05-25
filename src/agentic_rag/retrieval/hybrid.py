from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentic_rag.config import Settings
from agentic_rag.retrieval.bm25_retriever import BM25Retriever
from agentic_rag.retrieval.fusion import rrf_fuse
from agentic_rag.retrieval.page_retriever import PageRetriever
from agentic_rag.retrieval.relationship_expander import RelationshipExpander
from agentic_rag.retrieval.scoring import STAGE_INITIAL, compute_composite_scores, filter_by_stage_threshold
from agentic_rag.retrieval.table_retriever import TableRetriever
from agentic_rag.retrieval.retrieval_plan import RetrievalChannel, RetrievalPlan, RetrievalTask, resolve_vector_name
from agentic_rag.schemas import SearchHit
from agentic_rag.store.qdrant_store import QdrantStore


@dataclass(slots=True)
class HybridRetrievalResult:
    hits: list[SearchHit]
    route_hits: dict[str, list[SearchHit]]
    expanded_hits: list[SearchHit]
    executed_channels: list[str]


class HybridRetriever:
    """Multi-channel retrieval orchestrator with RRF fusion."""

    def __init__(self, settings: Settings, store: QdrantStore, vector_search_fn):
        self.settings = settings
        self.store = store
        self.vector_search_fn = vector_search_fn
        self.bm25 = BM25Retriever(settings, store)
        self.page = PageRetriever(store, settings=settings)
        self.table = TableRetriever(store, settings=settings)

    def retrieve(
        self,
        *,
        query_text: str,
        query_vector: list[float],
        filters: dict[str, Any] | None = None,
        plan: RetrievalPlan | dict[str, Any] | None = None,
        channels: list[RetrievalChannel] | None = None,
    ) -> HybridRetrievalResult:
        plan_obj = RetrievalPlan.model_validate(plan) if isinstance(plan, dict) else plan
        tasks = self._resolve_tasks(
            query_text=query_text,
            filters=filters,
            plan=plan_obj,
            channels=channels,
        )
        route_hits: dict[str, list[SearchHit]] = {}
        executed_channels: list[str] = []

        for task in tasks:
            if task.channel in route_hits:
                continue
            task_query = task.query_text or query_text
            task_filters = dict(filters or {})
            task_filters.update(task.filters or {})
            vector_name = resolve_vector_name(task.channel, task.metadata, self.settings)

            if task.channel == "vector":
                hits = self._vector_search(
                    query_vector=query_vector,
                    filters=task_filters or None,
                    vector_name=vector_name,
                    top_k=task.top_k,
                )
                for hit in hits:
                    hit.channel = "vector"
                    hit.score_vector = hit.score
                    hit.metadata["vector_name"] = vector_name
            elif task.channel == "bm25":
                if not self.settings.bm25_enabled:
                    hits = []
                else:
                    hits = self.bm25.retrieve(query_text=task_query, filters=task_filters or None, top_k=task.top_k)
            elif task.channel == "page":
                hits = self.page.retrieve(query_text=task_query, filters=task_filters or None, top_k=task.top_k)
            elif task.channel in {"table", "image", "formula"} and self.settings.enable_named_vectors:
                modality_filters = dict(task_filters)
                modality_filters["modality"] = task.channel if task.channel != "formula" else "formula"
                hits = self._vector_search(
                    query_vector=query_vector,
                    filters=modality_filters or None,
                    vector_name=vector_name,
                    top_k=task.top_k,
                )
                for hit in hits:
                    hit.channel = task.channel
                    hit.score_vector = hit.score
                    hit.metadata["vector_name"] = vector_name
            elif task.channel == "table":
                hits = self.table.retrieve(query_text=task_query, filters=task_filters or None, top_k=task.top_k)
            elif task.channel == "image":
                if self.settings.enable_named_vectors:
                    hits = []
                else:
                    modality_filters = dict(task_filters)
                    modality_filters["modality"] = "image"
                    hits = self.bm25.retrieve(query_text=task_query, filters=modality_filters, top_k=task.top_k)
                    for hit in hits:
                        hit.channel = task.channel
            elif task.channel == "formula":
                if self.settings.enable_named_vectors:
                    hits = []
                else:
                    modality_filters = dict(task_filters)
                    modality_filters["modality"] = "formula"
                    hits = self.bm25.retrieve(query_text=task_query, filters=modality_filters, top_k=task.top_k)
                    for hit in hits:
                        hit.channel = task.channel
            elif task.channel == "relationship":
                route_hits["relationship"] = []
                executed_channels.append("relationship")
                continue
            else:
                hits = []

            route_hits[task.channel] = hits
            executed_channels.append(task.channel)

        fused = rrf_fuse(
            [hits for channel, hits in route_hits.items() if channel != "relationship"],
            k=self.settings.rrf_k,
            top_k=self.settings.rrf_top_k,
        )
        fused = compute_composite_scores(fused, stage=STAGE_INITIAL, settings=self.settings)
        fused = filter_by_stage_threshold(fused, stage=STAGE_INITIAL, settings=self.settings)
        explicit_relationship = "relationship" in route_hits or plan_obj is None
        auto_related_expansion = (
            self.settings.rel_expand_related_modality_enabled
            or self.settings.rel_expand_context_text_enabled
        )
        should_expand = self.settings.rel_expand_explicit_relationships and (
            explicit_relationship or auto_related_expansion
        )
        if should_expand:
            expander = RelationshipExpander(self.store.scroll_hits(limit=5000), settings=self.settings)
            page_window = (
                plan_obj.page_window
                if explicit_relationship and plan_obj is not None and plan_obj.page_window is not None
                else self.settings.rel_expand_pages
                if explicit_relationship
                else 0
            )
            expanded = expander.expand(
                fused,
                steps=self.settings.rel_expand_steps,
                page_window=page_window,
            )
        else:
            expanded = fused
        return HybridRetrievalResult(
            hits=fused,
            route_hits=route_hits,
            expanded_hits=expanded,
            executed_channels=executed_channels,
        )

    def _vector_search(
        self,
        *,
        query_vector: list[float],
        filters: dict[str, Any] | None,
        vector_name: str | None,
        top_k: int | None,
    ) -> list[SearchHit]:
        try:
            return self.vector_search_fn(query_vector=query_vector, filters=filters, vector_name=vector_name, top_k=top_k)
        except TypeError:
            try:
                return self.vector_search_fn(query_vector=query_vector, filters=filters, vector_name=vector_name)
            except TypeError:
                return self.vector_search_fn(query_vector=query_vector, filters=filters)

    def _resolve_tasks(
        self,
        *,
        query_text: str,
        filters: dict[str, Any] | None,
        plan: RetrievalPlan | None,
        channels: list[RetrievalChannel] | None,
    ) -> list[RetrievalTask]:
        if plan is not None:
            return list(plan.tasks)
        if channels is not None:
            return [
                RetrievalTask(
                    channel=channel,
                    query_text=query_text,
                    top_k=self._default_top_k(channel),
                    filters=dict(filters or {}),
                )
                for channel in channels
            ]

        default_channels: list[RetrievalChannel] = ["vector"]
        if self.settings.bm25_enabled:
            default_channels.append("bm25")
        if self.settings.retrieval_auto_table_channel_enabled and _contains_any_keyword(
            query_text, self.settings.retrieval_table_trigger_keywords
        ):
            default_channels.append("table")
        if self.settings.retrieval_auto_formula_channel_enabled and _contains_any_keyword(
            query_text, self.settings.retrieval_formula_trigger_keywords
        ):
            default_channels.append("formula")
        return [
            RetrievalTask(
                channel=channel,
                query_text=query_text,
                top_k=self._default_top_k(channel),
                filters=dict(filters or {}),
            )
            for channel in default_channels
        ]

    def _default_top_k(self, channel: RetrievalChannel) -> int:
        if channel == "vector":
            return self.settings.retrieval_top_k
        if channel == "bm25":
            return self.settings.bm25_top_k
        if channel == "page":
            return self.settings.page_top_k
        if channel == "table":
            return self.settings.table_top_k
        return self.settings.rrf_top_k


def _contains_any_keyword(text: str, keywords: str) -> bool:
    raw = (text or "").lower()
    for keyword in (part.strip().lower() for part in (keywords or "").split(",")):
        if keyword and keyword in raw:
            return True
    return False

