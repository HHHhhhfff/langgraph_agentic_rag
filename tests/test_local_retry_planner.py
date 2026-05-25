from __future__ import annotations

from agentic_rag.config import Settings
from agentic_rag.graph.local_retry import LocalRetryDecision, LocalRetryPlanner
from agentic_rag.retrieval.retrieval_plan import RetrievalPlan, RetrievalTask
from agentic_rag.schemas import SearchHit


def _plan(*channels: str, top_k: int = 4) -> RetrievalPlan:
    return RetrievalPlan(
        question="请介绍下 accuracy 90%",
        query_text="请介绍下 accuracy 90%",
        tasks=[RetrievalTask(channel=channel, query_text="请介绍下 accuracy 90%", top_k=top_k) for channel in channels],
    )


def _state(plan: RetrievalPlan, **kwargs):
    state = {
        "question": plan.question,
        "retrieval_plan": plan.model_dump(),
        "retry_count": 0,
        "expanded_hits": [],
        "fused_hits": [],
    }
    state.update(kwargs)
    return state


def _channels(plan: RetrievalPlan) -> list[str]:
    return [task.channel for task in plan.tasks]


def test_low_keyword_coverage_adds_bm25_and_rewrites_query() -> None:
    planner = LocalRetryPlanner(Settings(tg_retry_max_top_k=20))

    decision = planner.plan_retry(_state(_plan("vector"), evidence_gaps=["low_keyword_coverage"]))

    assert "bm25" in _channels(decision.plan)
    assert "add_bm25" in decision.retry_actions
    assert decision.rewritten_query_text
    assert "介绍下" not in decision.rewritten_query_text


def test_insufficient_hits_adds_bm25_page_and_increases_top_k() -> None:
    planner = LocalRetryPlanner(Settings(tg_retry_max_top_k=20, tg_retry_top_k_multiplier=2.0))

    decision = planner.plan_retry(_state(_plan("vector", top_k=4), evidence_gaps=["insufficient_hits"]))

    assert {"vector", "bm25", "page"}.issubset(set(_channels(decision.plan)))
    vector_task = next(task for task in decision.plan.tasks if task.channel == "vector")
    assert vector_task.top_k == 8
    assert "increase_top_k" in decision.retry_actions


def test_missing_table_formula_image_adds_modality_channels() -> None:
    planner = LocalRetryPlanner(Settings())

    decision = planner.plan_retry(
        _state(
            _plan("vector"),
            missing_slots=["modality:table", "modality:formula", "modality:image"],
        )
    )

    assert {"table", "formula", "image"}.issubset(set(_channels(decision.plan)))
    table_task = next(task for task in decision.plan.tasks if task.channel == "table")
    assert table_task.query_text == decision.rewritten_query_text


def test_missing_page_adds_page_and_expands_window() -> None:
    planner = LocalRetryPlanner(Settings(rel_expand_pages=1, tg_retry_max_page_window=3))

    decision = planner.plan_retry(_state(_plan("vector"), missing_slots=["page"]))

    assert "page" in _channels(decision.plan)
    assert decision.plan.page_window == 2
    assert "expand_page_window" in decision.retry_actions


def test_numeric_missing_adds_table_and_bm25() -> None:
    planner = LocalRetryPlanner(Settings())

    decision = planner.plan_retry(_state(_plan("vector"), missing_slots=["numeric"]))

    assert {"table", "bm25"}.issubset(set(_channels(decision.plan)))
    assert "数值" in (decision.rewritten_query_text or "")


def test_conflict_adds_relationship_and_expands_window() -> None:
    planner = LocalRetryPlanner(Settings(rel_expand_pages=1, tg_retry_max_page_window=3))

    decision = planner.plan_retry(
        _state(
            _plan("vector"),
            conflict_level="medium",
            conflict_reasons=["numeric_value_conflict"],
            gate_reasons=["possible_conflict"],
        )
    )

    assert "relationship" in _channels(decision.plan)
    assert decision.plan.page_window == 2
    relationship_task = next(task for task in decision.plan.tasks if task.channel == "relationship")
    assert relationship_task.metadata["expansion_mode"] == "routed"
    assert relationship_task.metadata["expansion_reason"] == "numeric_value_conflict"


def test_existing_channel_is_updated_not_duplicated_and_top_k_is_capped() -> None:
    planner = LocalRetryPlanner(Settings(tg_retry_max_top_k=5, tg_retry_top_k_multiplier=2.0))

    decision = planner.plan_retry(
        _state(
            _plan("vector", "bm25", top_k=4),
            evidence_gaps=["low_keyword_coverage", "insufficient_hits"],
        )
    )

    assert _channels(decision.plan).count("bm25") == 1
    assert max(task.top_k for task in decision.plan.tasks) <= 5


def test_retry_history_is_serializable() -> None:
    planner = LocalRetryPlanner(Settings())

    decision = planner.plan_retry(_state(_plan("vector"), evidence_gaps=["insufficient_hits"]))
    loaded = LocalRetryDecision.model_validate(decision.model_dump())

    assert loaded.retry_count == 1
    assert loaded.plan.retry_history


def test_evidence_gain_uses_new_fused_ids() -> None:
    planner = LocalRetryPlanner(Settings())
    old = SearchHit(point_id="old", node_id="old", text="old", score=0.1)
    new = SearchHit(point_id="new", node_id="new", text="new", score=0.2)

    decision = planner.plan_retry(
        _state(
            _plan("vector"),
            evidence_gaps=["insufficient_hits"],
            expanded_hits=[old],
            fused_hits=[old, new],
        )
    )

    assert decision.evidence_gain == 0.5


def test_agent_context_expansion_adds_relationship_for_high_score_seed() -> None:
    planner = LocalRetryPlanner(
        Settings(
            _env_file=None,
            tg_agent_context_expansion_enabled=True,
            tg_agent_context_expansion_min_seed_score=0.5,
            tg_agent_context_expansion_seed_top_m=2,
        )
    )
    seed = SearchHit(point_id="seed", node_id="seed", text="seed", score=0.8, metadata={"score_composite": 0.8})

    decision = planner.plan_retry(
        _state(
            _plan("vector"),
            evidence_gaps=["low_support_score"],
            expanded_hits=[seed],
        )
    )

    assert "relationship" in decision.plan.channels()
    assert "agent_context_expand" in decision.retry_actions
    relationship_task = next(task for task in decision.plan.tasks if task.channel == "relationship")
    assert relationship_task.metadata["expansion_mode"] == "retry"
    assert relationship_task.metadata["expansion_reason"] == "agent_context_expand"
