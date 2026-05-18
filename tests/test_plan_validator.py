from __future__ import annotations

from types import SimpleNamespace

from agentic_rag.config import Settings
from agentic_rag.graph.agent_planner import AgentRetrievalPlanDecision, AgentRetrievalTask, AgentRouteDecision
from agentic_rag.graph.agent_retry import RetryAdvice
from agentic_rag.graph.plan_validator import PlanValidator
from agentic_rag.retrieval.retrieval_plan import RetrievalPlan, RetrievalTask


def _rule_plan() -> RetrievalPlan:
    return RetrievalPlan(
        question="q",
        query_text="q",
        tasks=[
            RetrievalTask(channel="vector", query_text="q", top_k=4, filters={}),
            RetrievalTask(channel="bm25", query_text="q", top_k=4, filters={}),
        ],
    )


def test_merge_agent_route_accepts_valid_decision() -> None:
    validator = PlanValidator(Settings(tg_agent_min_route_confidence=0.5))
    rule = {"route": "text_first", "target_modalities": ["text"], "need_cross_doc": False}
    decision = AgentRouteDecision(
        intent="image_qa",
        target_modalities=["image"],
        route="image_first",
        confidence=0.9,
        need_page_level=True,
        reasoning_summary="image question",
    )

    result = validator.merge_agent_route(rule, decision)

    assert result.used is True
    assert result.state["route"] == "image_first"
    assert set(result.state["target_modalities"]) == {"text", "image"}
    assert result.state["need_page_level"] is True


def test_merge_agent_route_rejects_low_confidence() -> None:
    validator = PlanValidator(Settings(tg_agent_min_route_confidence=0.8))
    rule = {"route": "text_first", "target_modalities": ["text"]}
    decision = AgentRouteDecision(intent="table_qa", route="table_first", confidence=0.2, reasoning_summary="")

    result = validator.merge_agent_route(rule, decision)

    assert result.used is False
    assert result.state["route"] == "text_first"
    assert "low_route_confidence" in result.errors


def test_merge_agent_plan_adds_valid_channels_and_clamps_top_k() -> None:
    validator = PlanValidator(Settings(tg_retry_max_top_k=10))
    decision = AgentRetrievalPlanDecision(
        tasks=[
            AgentRetrievalTask(channel="table", query_text="table q", top_k=99, filters={"modality": "table"}),
            AgentRetrievalTask(channel="page", query_text="page q", top_k=3, filters={"page": 3}),
        ],
        page_window=99,
    )

    result = validator.merge_agent_plan(_rule_plan(), decision)

    assert result.used is True
    assert "table" in result.plan.channels()
    assert "page" in result.plan.channels()
    assert result.plan.page_window == 3
    table_task = [task for task in result.plan.tasks if task.channel == "table"][0]
    assert table_task.top_k == 10


def test_merge_agent_plan_rejects_invalid_filters() -> None:
    validator = PlanValidator(Settings())
    decision = AgentRetrievalPlanDecision(
        tasks=[AgentRetrievalTask(channel="table", query_text="q", top_k=3, filters={"hack": "x"})]
    )

    result = validator.merge_agent_plan(_rule_plan(), decision)

    assert result.used is False
    assert result.errors
    assert "table" not in result.plan.channels()


def test_merge_retry_advice_applies_rewrite_and_limits() -> None:
    validator = PlanValidator(Settings(tg_retry_max_top_k=8, tg_retry_max_page_window=2))
    advice = RetryAdvice(
        rewritten_query_text="new q",
        add_channels=["relationship", "image"],
        increase_top_k_channels=["vector"],
        page_window=99,
        reasons=["need more evidence"],
    )

    result = validator.merge_retry_advice(_rule_plan(), advice)

    assert result.used is True
    assert result.plan.query_text == "new q"
    assert "relationship" in result.plan.channels()
    assert "image" in result.plan.channels()
    assert result.plan.page_window == 2
    vector_task = [task for task in result.plan.tasks if task.channel == "vector"][0]
    assert vector_task.top_k <= 8
