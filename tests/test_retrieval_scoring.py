from __future__ import annotations

from agentic_rag.config import Settings
from agentic_rag.retrieval.scoring import STAGE_FINAL, STAGE_INITIAL, STAGE_RERANK, compute_composite_scores, filter_by_stage_threshold
from agentic_rag.schemas import SearchHit


def test_composite_score_combines_raw_scores_and_updates_score() -> None:
    settings = Settings(_env_file=None)
    hit = SearchHit(point_id="1", text="a", score=0.02, score_vector=0.8, score_rrf=0.02)

    compute_composite_scores([hit], stage=STAGE_INITIAL, settings=settings)

    assert hit.metadata["score_policy"] == "weighted_v1"
    assert hit.metadata["score_composite"] == hit.score
    assert hit.metadata["score_components"]["vector_norm"] == 0.8
    assert hit.metadata["score_components"]["rrf_norm"] == 1.0


def test_rerank_prior_penalty_reduces_low_prior_hit() -> None:
    settings = Settings(
        _env_file=None,
        retrieval_rerank_require_prior_score=True,
        retrieval_rerank_prior_min_composite_score=0.5,
        retrieval_rerank_prior_low_score_penalty=0.5,
    )
    hit = SearchHit(
        point_id="1",
        text="a",
        score=0.1,
        score_vector=0.1,
        score_rrf=0.01,
        metadata={"rerank_score": 1.0, "score_composite_prior": 0.1},
    )

    compute_composite_scores([hit], stage=STAGE_RERANK, settings=settings)

    assert hit.metadata["score_prior_penalized"] is True
    assert hit.metadata["score_composite"] < 1.0


def test_stage_threshold_filters_low_composite_hit() -> None:
    settings = Settings(_env_file=None, retrieval_final_min_composite_score=0.5)
    low = SearchHit(point_id="low", text="low", score=0.1, metadata={"score_composite": 0.2})
    high = SearchHit(point_id="high", text="high", score=0.8, metadata={"score_composite": 0.8})

    kept = filter_by_stage_threshold([low, high], stage="final_after_retry", settings=settings)

    assert [hit.point_id for hit in kept] == ["high"]
    assert low.metadata["score_threshold_passed"] is False
    assert high.metadata["score_threshold_passed"] is True


def test_relationship_weight_uses_configured_value() -> None:
    settings = Settings(_env_file=None, retrieval_score_relationship_weight=0.3)
    hit = SearchHit(
        point_id="1",
        text="related",
        score=0.5,
        metadata={"retrieval_inherited_score": 0.5},
    )

    compute_composite_scores([hit], stage=STAGE_INITIAL, settings=settings)

    assert hit.metadata["score_weights"]["relationship"] == 0.3


def test_inherited_only_final_score_is_preserved() -> None:
    settings = Settings(_env_file=None, retrieval_score_relationship_weight=0.3)
    hit = SearchHit(
        point_id="1",
        text="related",
        score=0.42,
        score_vector=0.0,
        metadata={
            "score_composite": 0.42,
            "score_policy": "inherited_relationship_v1",
            "retrieval_inherited_score": 0.42,
        },
    )

    compute_composite_scores([hit], stage=STAGE_FINAL, settings=settings)

    assert hit.score == 0.42
    assert hit.metadata["score_composite"] == 0.42
    assert hit.metadata["score_policy"] == "inherited_relationship_v1"
    assert hit.metadata["score_weights"]["relationship"] == 0.3


def test_agent_relevance_score_participates_in_composite() -> None:
    settings = Settings(
        _env_file=None,
        retrieval_score_vector_weight=0.0,
        retrieval_score_bm25_weight=0.0,
        retrieval_score_rrf_weight=0.0,
        retrieval_score_agent_relevance_weight=0.5,
    )
    hit = SearchHit(
        point_id="1",
        text="agent relevant",
        score=0.1,
        score_vector=None,
        metadata={"agent_relevance_score": 0.8},
    )

    compute_composite_scores([hit], stage=STAGE_INITIAL, settings=settings)

    assert hit.metadata["score_components"]["agent_relevance_norm"] == 0.8
    assert hit.metadata["score_weights"]["agent_relevance"] == 0.5
    assert hit.score == 0.8
