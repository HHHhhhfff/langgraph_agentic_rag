from __future__ import annotations

from typing import Any

from agentic_rag.config import Settings
from agentic_rag.schemas import SearchHit


STAGE_INITIAL = "initial_retrieval"
STAGE_RERANK = "rerank"
STAGE_FINAL = "final_after_retry"


def compute_composite_scores(
    hits: list[SearchHit],
    *,
    stage: str,
    settings: Settings,
    update_score: bool = True,
) -> list[SearchHit]:
    """Attach an interpretable weighted score to hits while preserving raw scores."""

    if not hits:
        return hits

    vector_norm = _norm_by_clip([hit.score_vector if hit.score_vector is not None else hit.score for hit in hits])
    bm25_norm = _norm_by_minmax([hit.score_bm25 for hit in hits])
    rrf_norm = _norm_by_minmax([hit.score_rrf for hit in hits])
    rerank_norm = _norm_by_rerank([_rerank_score(hit) for hit in hits])
    relationship_norm = _norm_by_clip([_metadata_float(hit, "retrieval_inherited_score") for hit in hits])
    agent_relevance_norm = _norm_by_clip([_metadata_float(hit, "agent_relevance_score") for hit in hits])

    for index, hit in enumerate(hits):
        components = {
            "vector_norm": vector_norm[index],
            "bm25_norm": bm25_norm[index],
            "rrf_norm": rrf_norm[index],
            "rerank_norm": rerank_norm[index],
            "relationship_norm": relationship_norm[index],
            "agent_relevance_norm": agent_relevance_norm[index],
        }
        weights = _stage_weights(settings=settings, stage=stage, hit=hit)
        if _preserve_inherited_score(hit, components):
            score = float(_metadata_float(hit, "retrieval_inherited_score") or 0.0)
            hit.metadata.setdefault("score_raw_before_composite", hit.score)
            hit.metadata["score_composite"] = score
            hit.metadata["score_stage"] = stage
            hit.metadata["score_policy"] = "inherited_relationship_v1"
            hit.metadata["score_components"] = components
            hit.metadata["score_weights"] = weights
            if stage != STAGE_RERANK:
                hit.metadata["score_composite_prior"] = score
            if update_score:
                hit.score = score
            continue
        score = _weighted_score(components, weights)
        if stage == STAGE_RERANK and getattr(settings, "retrieval_rerank_require_prior_score", True):
            prior = _metadata_float(hit, "score_composite_prior")
            if prior is not None and prior < settings.retrieval_rerank_prior_min_composite_score:
                score *= settings.retrieval_rerank_prior_low_score_penalty
                hit.metadata["score_prior_penalized"] = True

        hit.metadata.setdefault("score_raw_before_composite", hit.score)
        hit.metadata["score_composite"] = score
        hit.metadata["score_stage"] = stage
        hit.metadata["score_policy"] = "weighted_v1"
        hit.metadata["score_components"] = components
        hit.metadata["score_weights"] = weights
        if stage != STAGE_RERANK:
            hit.metadata["score_composite_prior"] = score
        if update_score:
            hit.score = score
    return hits


def filter_by_stage_threshold(
    hits: list[SearchHit],
    *,
    stage: str,
    settings: Settings,
) -> list[SearchHit]:
    threshold = _threshold_for_stage(settings, stage)
    kept: list[SearchHit] = []
    for hit in hits:
        score = _metadata_float(hit, "score_composite")
        passed = score is None or score >= threshold
        hit.metadata["score_threshold"] = threshold
        hit.metadata["score_threshold_stage"] = stage
        hit.metadata["score_threshold_passed"] = passed
        if passed:
            kept.append(hit)
    return kept


def score_value(hit: SearchHit) -> float:
    value = _metadata_float(hit, "score_composite")
    if value is not None:
        return value
    return float(hit.score or 0.0)


def _stage_weights(*, settings: Settings, stage: str, hit: SearchHit) -> dict[str, float]:
    weights = {
        "vector": settings.retrieval_score_vector_weight,
        "bm25": settings.retrieval_score_bm25_weight,
        "rrf": settings.retrieval_score_rrf_weight,
        "rerank": settings.retrieval_score_rerank_weight if stage in {STAGE_RERANK, STAGE_FINAL} else 0.0,
        "relationship": settings.retrieval_score_relationship_weight,
        "agent_relevance": settings.retrieval_score_agent_relevance_weight,
    }
    return weights


def _preserve_inherited_score(hit: SearchHit, components: dict[str, float | None]) -> bool:
    if hit.metadata.get("score_policy") != "inherited_relationship_v1":
        return False
    if _metadata_float(hit, "retrieval_inherited_score") is None:
        return False
    return (
        components.get("vector_norm") in {None, 0.0}
        and components.get("bm25_norm") is None
        and components.get("rrf_norm") is None
        and components.get("rerank_norm") is None
    )


def _weighted_score(components: dict[str, float | None], weights: dict[str, float]) -> float:
    pairs = (
        ("vector_norm", "vector"),
        ("bm25_norm", "bm25"),
        ("rrf_norm", "rrf"),
        ("rerank_norm", "rerank"),
        ("relationship_norm", "relationship"),
        ("agent_relevance_norm", "agent_relevance"),
    )
    numerator = 0.0
    denominator = 0.0
    for component_key, weight_key in pairs:
        value = components.get(component_key)
        weight = max(0.0, float(weights.get(weight_key, 0.0) or 0.0))
        if value is None or weight <= 0:
            continue
        numerator += float(value) * weight
        denominator += weight
    if denominator <= 0:
        return 0.0
    return max(0.0, min(1.0, numerator / denominator))


def _threshold_for_stage(settings: Settings, stage: str) -> float:
    if stage == STAGE_INITIAL:
        return settings.retrieval_initial_min_composite_score
    if stage == STAGE_RERANK:
        return settings.retrieval_rerank_min_composite_score
    if stage == STAGE_FINAL:
        return settings.retrieval_final_min_composite_score
    if stage.startswith("retry") and "rerank" in stage:
        return settings.retrieval_rerank_min_composite_score
    return settings.retrieval_initial_min_composite_score


def _norm_by_clip(values: list[Any]) -> list[float | None]:
    out: list[float | None] = []
    for value in values:
        numeric = _coerce_float(value)
        out.append(None if numeric is None else max(0.0, min(1.0, numeric)))
    return out


def _norm_by_rerank(values: list[Any]) -> list[float | None]:
    clipped = _norm_by_clip(values)
    if any(value is not None and value > 0 for value in clipped):
        return clipped
    return _norm_by_minmax(values)


def _norm_by_minmax(values: list[Any]) -> list[float | None]:
    numbers = [_coerce_float(value) for value in values]
    present = [value for value in numbers if value is not None]
    if not present:
        return [None for _ in values]
    low = min(present)
    high = max(present)
    if high <= low:
        return [1.0 if value is not None and value > 0 else (0.0 if value is not None else None) for value in numbers]
    return [None if value is None else max(0.0, min(1.0, (value - low) / (high - low))) for value in numbers]


def _rerank_score(hit: SearchHit) -> float | None:
    return _metadata_float(hit, "rerank_score")


def _metadata_float(hit: SearchHit, key: str) -> float | None:
    return _coerce_float((hit.metadata or {}).get(key))


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
