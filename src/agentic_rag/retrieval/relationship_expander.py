from __future__ import annotations

from collections import defaultdict

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

    def __init__(self, all_hits: list[SearchHit]):
        self.all_hits = list(all_hits)
        self.by_node: dict[str, SearchHit] = {}
        self.by_doc_page: dict[tuple[str | None, int | None], list[SearchHit]] = defaultdict(list)
        for hit in self.all_hits:
            key = hit.node_id or hit.point_id
            self.by_node[key] = hit
            self.by_doc_page[(hit.doc_id, hit.page)].append(hit)

    def expand(self, seed_hits: list[SearchHit], *, steps: int = 1, page_window: int = 1) -> list[SearchHit]:
        results: dict[str, SearchHit] = {}

        def add(hit: SearchHit) -> None:
            key = hit.node_id or hit.point_id
            if key not in results:
                results[key] = hit.model_copy(deep=True)

        frontier = list(seed_hits)
        for seed in seed_hits:
            add(seed)

        for _ in range(max(1, steps)):
            next_frontier: list[SearchHit] = []
            for seed in frontier:
                doc_id = seed.doc_id
                page = seed.page
                if page is not None:
                    for delta in range(-page_window, page_window + 1):
                        for neighbor in self.by_doc_page.get((doc_id, page + delta), []):
                            key = neighbor.node_id or neighbor.point_id
                            if key not in results:
                                add(neighbor)
                                next_frontier.append(neighbor)
                for rid in self._relationship_ids(seed.relationships or {}):
                    if isinstance(rid, str) and rid in self.by_node:
                        neighbor = self.by_node[rid]
                        key = neighbor.node_id or neighbor.point_id
                        if key not in results:
                            add(neighbor)
                            next_frontier.append(neighbor)
            frontier = next_frontier

        expanded = list(results.values())
        expanded.sort(key=lambda h: (h.score_rrf or h.score, h.score), reverse=True)
        return expanded

    @classmethod
    def _relationship_ids(cls, relationships: dict) -> list[str]:
        ids: list[str] = []
        for key_name in cls.SINGLE_RELATION_KEYS:
            raw = relationships.get(key_name)
            if isinstance(raw, str):
                ids.append(raw)
            elif isinstance(raw, list):
                ids.extend(item for item in raw if isinstance(item, str))
        for key_name in cls.LIST_RELATION_KEYS:
            raw = relationships.get(key_name, [])
            if isinstance(raw, str):
                ids.append(raw)
            elif isinstance(raw, list):
                ids.extend(item for item in raw if isinstance(item, str))
        return ids
