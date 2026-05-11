from __future__ import annotations

from collections import defaultdict

from agentic_rag.schemas import SearchHit


def _hit_key(hit: SearchHit) -> str:
    if hit.node_id:
        return hit.node_id
    if hit.point_id:
        return hit.point_id
    return f"{hit.doc_id}:{hit.page}:{hit.text[:64]}"


def rrf_fuse(result_sets: list[list[SearchHit]], k: int = 60, top_k: int = 12) -> list[SearchHit]:
    """Reciprocal rank fusion across multiple retrieval result sets."""

    scores: dict[str, float] = defaultdict(float)
    best_hits: dict[str, SearchHit] = {}
    for hits in result_sets:
        for rank, hit in enumerate(hits, start=1):
            key = _hit_key(hit)
            scores[key] += 1.0 / (k + rank)
            best_hits.setdefault(key, hit)
            best = best_hits[key]
            best.score_rrf = scores[key]
            if hit.score_vector is not None and best.score_vector is None:
                best.score_vector = hit.score_vector
            if hit.score_bm25 is not None and best.score_bm25 is None:
                best.score_bm25 = hit.score_bm25
            if hit.channel and best.channel == "vector":
                best.channel = hit.channel

    fused = list(best_hits.values())
    for hit in fused:
        hit.score = hit.score_rrf or hit.score
    fused.sort(key=lambda x: (x.score_rrf or 0.0, x.score), reverse=True)
    return fused[:top_k]

