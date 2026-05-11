from __future__ import annotations

from collections import defaultdict

from agentic_rag.schemas import SearchHit


class RelationshipExpander:
    """Expand seed hits by page order and relationship graph."""

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
                rel = seed.relationships or {}
                for key_name in ("parent_id", "prev_id", "next_id"):
                    raw = rel.get(key_name)
                    rel_ids = raw if isinstance(raw, list) else [raw]
                    for rid in rel_ids:
                        if isinstance(rid, str) and rid in self.by_node:
                            neighbor = self.by_node[rid]
                            key = neighbor.node_id or neighbor.point_id
                            if key not in results:
                                add(neighbor)
                                next_frontier.append(neighbor)
                child_ids = rel.get("child_ids", [])
                if not isinstance(child_ids, list):
                    child_ids = [child_ids]
                for rid in child_ids:
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
