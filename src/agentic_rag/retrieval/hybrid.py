from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentic_rag.config import Settings
from agentic_rag.retrieval.bm25_retriever import BM25Retriever
from agentic_rag.retrieval.fusion import rrf_fuse
from agentic_rag.retrieval.page_retriever import PageRetriever
from agentic_rag.retrieval.query_variants import build_query_variants
from agentic_rag.retrieval.relationship_expander import RelationshipExpander
from agentic_rag.retrieval.scoring import STAGE_INITIAL, compute_composite_scores, filter_by_stage_threshold_with_removed, score_value
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
    removed_hits_by_stage: dict[str, list[SearchHit]] = field(default_factory=dict)


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
        relationship_expansion_mode = "routed"
        relationship_expansion_reason: str | None = None

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
                    hits = self._bm25_retrieve_with_variants(
                        query_text=task_query,
                        filters=task_filters or None,
                        top_k=task.top_k,
                    )
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
                    hits = self._bm25_retrieve_with_variants(
                        query_text=task_query,
                        filters=modality_filters,
                        top_k=task.top_k,
                    )
                    for hit in hits:
                        hit.channel = task.channel
            elif task.channel == "formula":
                if self.settings.enable_named_vectors:
                    hits = []
                else:
                    modality_filters = dict(task_filters)
                    modality_filters["modality"] = "formula"
                    hits = self._bm25_retrieve_with_variants(
                        query_text=task_query,
                        filters=modality_filters,
                        top_k=task.top_k,
                    )
                    for hit in hits:
                        hit.channel = task.channel
            elif task.channel == "relationship":
                relationship_expansion_mode = str(task.metadata.get("expansion_mode") or relationship_expansion_mode)
                relationship_expansion_reason = (
                    str(task.metadata.get("expansion_reason")) if task.metadata.get("expansion_reason") else None
                )
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
        fused = self._apply_query_variant_boost(fused)
        initial_filter = filter_by_stage_threshold_with_removed(fused, stage=STAGE_INITIAL, settings=self.settings)
        fused = initial_filter.kept
        explicit_relationship = "relationship" in route_hits
        auto_related_expansion = (
            self.settings.rel_expand_related_modality_enabled
            or self.settings.rel_expand_context_text_enabled
        )
        should_expand = self.settings.rel_expand_explicit_relationships and (
            explicit_relationship or auto_related_expansion
        )
        if should_expand:
            expander = RelationshipExpander(self.store.scroll_hits(limit=5000), settings=self.settings)
            expansion_mode = relationship_expansion_mode if explicit_relationship else "auto"
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
                mode=expansion_mode if expansion_mode in {"auto", "routed", "retry"} else "routed",
                expansion_reason=relationship_expansion_reason,
            )
        else:
            expanded = fused
        expanded = self._add_image_context_hits(expanded)
        return HybridRetrievalResult(
            hits=fused,
            route_hits=route_hits,
            expanded_hits=expanded,
            executed_channels=executed_channels,
            removed_hits_by_stage={"initial_retrieval": initial_filter.removed},
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
        if self.settings.retrieval_auto_image_channel_enabled and _contains_any_keyword(
            query_text, self.settings.retrieval_image_trigger_keywords
        ):
            default_channels.append("image")
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

    def _bm25_retrieve_with_variants(
        self,
        *,
        query_text: str,
        filters: dict[str, Any] | None,
        top_k: int | None,
    ) -> list[SearchHit]:
        variants = build_query_variants(query_text, self.settings)
        if not variants:
            return []
        by_key: dict[str, SearchHit] = {}
        for variant_index, variant in enumerate(variants, start=1):
            hits = self.bm25.retrieve(query_text=variant, filters=filters, top_k=top_k)
            for rank, hit in enumerate(hits, start=1):
                key = _hit_key(hit)
                existing = by_key.get(key)
                if existing is None:
                    copy = hit.model_copy(deep=True)
                    copy.metadata["query_variant"] = variant
                    copy.metadata["query_variant_rank"] = rank
                    copy.metadata["query_variant_index"] = variant_index
                    copy.metadata["query_variant_queries"] = [variant]
                    copy.metadata["query_variant_hit_count"] = 1
                    by_key[key] = copy
                    continue
                queries = existing.metadata.setdefault("query_variant_queries", [])
                if isinstance(queries, list) and variant not in queries:
                    queries.append(variant)
                count = existing.metadata.get("query_variant_hit_count", 1)
                existing.metadata["query_variant_hit_count"] = int(count or 1) + 1
                if hit.score > existing.score:
                    existing.score = hit.score
                    existing.score_bm25 = hit.score_bm25
                    existing.metadata["query_variant"] = variant
                    existing.metadata["query_variant_rank"] = rank
                    existing.metadata["query_variant_index"] = variant_index
                    existing.metadata["score_bm25"] = hit.metadata.get("score_bm25", hit.score_bm25)
                    continue
        return sorted(by_key.values(), key=lambda hit: hit.score, reverse=True)

    def _apply_query_variant_boost(self, hits: list[SearchHit]) -> list[SearchHit]:
        if not self.settings.query_variants_enabled:
            return hits
        for hit in hits:
            count = int(hit.metadata.get("query_variant_hit_count", 1) or 1)
            if count <= 1:
                continue
            boost = min(
                self.settings.query_variant_max_boost,
                (count - 1) * self.settings.query_variant_boost_per_hit,
            )
            if boost <= 0:
                continue
            before = score_value(hit)
            after = min(1.0, before + boost)
            hit.score = after
            hit.metadata["score_composite"] = after
            hit.metadata["query_variant_boost"] = boost
            hit.metadata["query_variant_score_before"] = before
            hit.metadata["query_variant_score_after"] = after
        return sorted(hits, key=score_value, reverse=True)

    def _add_image_context_hits(self, hits: list[SearchHit]) -> list[SearchHit]:
        if not self.settings.image_query_context_expand_enabled:
            return hits
        image_hits = [
            hit
            for hit in hits
            if (hit.modality or hit.metadata.get("modality")) == "image"
        ]
        if not image_hits:
            return hits
        existing = {_hit_key(hit) for hit in hits}
        try:
            pool = self.store.scroll_hits(limit=5000)
        except Exception:
            return hits
        additions: list[SearchHit] = []
        for image_hit in image_hits:
            doc_id = image_hit.doc_id or image_hit.metadata.get("doc_id")
            page = image_hit.page or image_hit.metadata.get("page")
            candidates = [
                candidate
                for candidate in pool
                if (candidate.modality or candidate.metadata.get("modality")) == "text"
                and (candidate.doc_id or candidate.metadata.get("doc_id")) == doc_id
                and (candidate.page or candidate.metadata.get("page")) == page
            ]
            candidates.sort(key=lambda item: int(item.metadata.get("chunk_index", 0) or 0))
            for candidate in candidates[: self.settings.image_query_context_max_text_hits]:
                key = _hit_key(candidate)
                if key in existing:
                    continue
                copy = candidate.model_copy(deep=True)
                inherited = max(0.0, min(1.0, score_value(image_hit) * self.settings.image_query_context_weight))
                copy.score = inherited
                copy.channel = "image_context"
                copy.metadata["score_composite"] = inherited
                copy.metadata["score_stage"] = "image_query_context"
                copy.metadata["score_policy"] = "image_query_context_inherited_v1"
                copy.metadata["retrieval_candidate_pool"] = "image_query_context"
                copy.metadata["retrieval_expanded_from_node_id"] = image_hit.node_id or image_hit.point_id
                copy.metadata["retrieval_expansion_relation"] = "image_same_page_text"
                copy.metadata["retrieval_relation_weight"] = self.settings.image_query_context_weight
                additions.append(copy)
                existing.add(key)
        if not additions:
            return hits
        return sorted([*hits, *additions], key=score_value, reverse=True)


def _contains_any_keyword(text: str, keywords: str) -> bool:
    raw = (text or "").lower()
    for keyword in (part.strip().lower() for part in (keywords or "").split(",")):
        if keyword and keyword in raw:
            return True
    return False


def _hit_key(hit: SearchHit) -> str:
    return hit.node_id or hit.point_id or f"{hit.doc_id}:{hit.metadata.get('chunk_index')}"

