from __future__ import annotations

from collections import defaultdict

from agentic_rag.config import Settings
from agentic_rag.retrieval.scoring import score_value
from agentic_rag.schemas import SearchHit


class RelationshipExpander:
    """Expand seed hits by page order and relationship graph."""

    SINGLE_RELATION_KEYS = (
        "parent_id",
        "prev_id",
        "next_id",
        "context_prev_node_id",
        "context_next_node_id",
        "doc_prev_node_id",
        "doc_next_node_id",
    )
    LIST_RELATION_KEYS = (
        "child_ids",
        "context_node_ids",
        "same_page_node_ids",
        "related_table_node_ids",
        "related_formula_node_ids",
    )

    def __init__(self, all_hits: list[SearchHit], settings: Settings | None = None):
        self.settings = settings
        self.all_hits = list(all_hits)
        self.by_node: dict[str, SearchHit] = {}
        self.by_doc_page: dict[tuple[str | None, int | None], list[SearchHit]] = defaultdict(list)
        for hit in self.all_hits:
            key = hit.node_id or hit.point_id
            self.by_node[key] = hit
            self.by_doc_page[(hit.doc_id, hit.page)].append(hit)

    def expand(self, seed_hits: list[SearchHit], *, steps: int = 1, page_window: int = 1) -> list[SearchHit]:
        results: dict[str, SearchHit] = {}
        related_total = 0

        def add(
            hit: SearchHit,
            *,
            seed: SearchHit | None = None,
            relation: str | None = None,
            candidate_pool: str = "primary",
        ) -> bool:
            nonlocal related_total
            key = hit.node_id or hit.point_id
            if key not in results:
                copy = hit.model_copy(deep=True)
                if seed is not None and relation is not None:
                    self._apply_inherited_score(copy, seed=seed, relation=relation, candidate_pool=candidate_pool)
                    if candidate_pool == "related_modality":
                        related_total += 1
                results[key] = copy
                return True
            return False

        frontier = [(hit, index) for index, hit in enumerate(seed_hits, start=1)]
        for seed in seed_hits:
            add(seed)

        for _ in range(max(1, steps)):
            next_frontier: list[tuple[SearchHit, int]] = []
            for seed, seed_rank in frontier:
                doc_id = seed.doc_id
                page = seed.page
                if page is not None and page_window > 0:
                    for delta in range(-page_window, page_window + 1):
                        for neighbor in self.by_doc_page.get((doc_id, page + delta), []):
                            key = neighbor.node_id or neighbor.point_id
                            if key not in results:
                                if add(neighbor, seed=seed, relation="page_window", candidate_pool="context"):
                                    next_frontier.append((neighbor, seed_rank))
                if self.settings is not None and not self.settings.rel_expand_explicit_relationships:
                    continue
                per_seed_related = 0
                per_seed_context = 0
                for rid, relation in self._relationship_targets(seed.relationships or {}):
                    if isinstance(rid, str) and rid in self.by_node:
                        neighbor = self.by_node[rid]
                        key = neighbor.node_id or neighbor.point_id
                        if key not in results:
                            if not self._allow_relation(
                                seed=seed,
                                neighbor=neighbor,
                                relation=relation,
                                seed_rank=seed_rank,
                                per_seed_related=per_seed_related,
                                per_seed_context=per_seed_context,
                                related_total=related_total,
                            ):
                                continue
                            candidate_pool = self._candidate_pool(relation)
                            if add(neighbor, seed=seed, relation=relation, candidate_pool=candidate_pool):
                                if candidate_pool == "related_modality":
                                    per_seed_related += 1
                                if candidate_pool == "context":
                                    per_seed_context += 1
                                next_frontier.append((neighbor, seed_rank))
            frontier = next_frontier

        expanded = list(results.values())
        expanded.sort(key=lambda h: (score_value(h), h.score_rrf or 0.0, h.score), reverse=True)
        return expanded

    @classmethod
    def _relationship_targets(cls, relationships: dict) -> list[tuple[str, str]]:
        ids: list[tuple[str, str]] = []
        for key_name in cls.SINGLE_RELATION_KEYS:
            raw = relationships.get(key_name)
            if isinstance(raw, str):
                ids.append((raw, key_name))
            elif isinstance(raw, list):
                ids.extend((item, key_name) for item in raw if isinstance(item, str))
        for key_name in cls.LIST_RELATION_KEYS:
            raw = relationships.get(key_name, [])
            if isinstance(raw, str):
                ids.append((raw, key_name))
            elif isinstance(raw, list):
                ids.extend((item, key_name) for item in raw if isinstance(item, str))
        return ids

    @classmethod
    def _relationship_ids(cls, relationships: dict) -> list[str]:
        return [target for target, _relation in cls._relationship_targets(relationships)]

    def _allow_relation(
        self,
        *,
        seed: SearchHit,
        neighbor: SearchHit,
        relation: str,
        seed_rank: int,
        per_seed_related: int,
        per_seed_context: int,
        related_total: int,
    ) -> bool:
        if self.settings is None:
            return True
        seed_score = score_value(seed)
        if relation == "same_page_node_ids" and not self.settings.rel_expand_same_page_relationships:
            return False
        if relation in {"related_table_node_ids", "related_formula_node_ids"}:
            if not self.settings.rel_expand_related_modality_enabled:
                return False
            if seed_rank > self.settings.rel_expand_seed_top_m:
                return False
            if seed_score < self.settings.rel_expand_min_seed_composite_score:
                return False
            if related_total >= self.settings.retrieval_related_evidence_max_total:
                return False
            if per_seed_related >= self.settings.retrieval_related_evidence_max_per_seed:
                return False
            if relation == "related_table_node_ids" and per_seed_related >= self.settings.rel_expand_max_related_tables:
                return False
            if relation == "related_formula_node_ids" and per_seed_related >= self.settings.rel_expand_max_related_formulas:
                return False
            return True
        if relation in {"context_node_ids", "context_prev_node_id", "context_next_node_id", "prev_id", "next_id"}:
            if not self.settings.rel_expand_context_text_enabled:
                return False
            if seed_rank > self.settings.rel_expand_context_text_seed_top_m:
                return False
            if seed_score < self.settings.rel_expand_context_text_min_seed_score:
                return False
            if per_seed_context >= self.settings.rel_expand_context_text_max_per_seed:
                return False
            if (neighbor.modality or neighbor.metadata.get("modality")) not in {"text", "", None}:
                return relation in {"prev_id", "next_id"}
            return True
        return True

    def _apply_inherited_score(
        self,
        hit: SearchHit,
        *,
        seed: SearchHit,
        relation: str,
        candidate_pool: str,
    ) -> None:
        seed_score = score_value(seed)
        weight = self._relation_weight(relation)
        inherited = max(0.0, min(1.0, seed_score * weight))
        existing = hit.metadata.get("score_composite")
        if isinstance(existing, (int, float)):
            inherited = max(inherited, float(existing) * 0.2 + inherited * 0.8)
        hit.score = inherited
        hit.metadata["score_composite"] = inherited
        hit.metadata["score_stage"] = "relationship_expand"
        hit.metadata["score_policy"] = "inherited_relationship_v1"
        hit.metadata["retrieval_expanded_from_node_id"] = seed.node_id or seed.point_id
        hit.metadata["retrieval_expansion_relation"] = relation
        hit.metadata["retrieval_seed_score_composite"] = seed_score
        hit.metadata["retrieval_relation_weight"] = weight
        hit.metadata["retrieval_inherited_score"] = inherited
        hit.metadata["retrieval_candidate_pool"] = candidate_pool

    def _relation_weight(self, relation: str) -> float:
        if self.settings is None:
            return 1.0
        if relation == "related_table_node_ids":
            return self.settings.rel_expand_related_table_weight
        if relation == "related_formula_node_ids":
            return self.settings.rel_expand_related_formula_weight
        if relation in {"context_node_ids", "context_prev_node_id", "context_next_node_id"}:
            return self.settings.rel_expand_context_text_weight
        if relation in {"prev_id", "next_id", "doc_prev_node_id", "doc_next_node_id"}:
            return 0.50
        if relation in {"same_page_node_ids", "page_window"}:
            return 0.30
        return 0.60

    @staticmethod
    def _candidate_pool(relation: str) -> str:
        if relation in {"related_table_node_ids", "related_formula_node_ids"}:
            return "related_modality"
        if relation in {"context_node_ids", "context_prev_node_id", "context_next_node_id", "prev_id", "next_id", "page_window"}:
            return "context"
        return "relationship"
