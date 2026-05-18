from __future__ import annotations

from agentic_rag.config import Settings
from agentic_rag.graph.agent_planner import AgentRetrievalPlanner, AgentRouteAnalyzer
from agentic_rag.retrieval.retrieval_plan import RetrievalPlan, RetrievalTask


class DummyLLM:
    def __init__(self, output: str):
        self.output = output

    def generate(self, prompt: str) -> str:
        self.prompt = prompt
        return self.output


def test_agent_route_analyzer_parses_llm_json() -> None:
    llm = DummyLLM(
        '{"intent":"image_qa","target_modalities":["image","text"],'
        '"need_cross_doc":false,"need_page_level":true,"route":"image_first",'
        '"reasoning_summary":"image request","confidence":0.9}'
    )
    analyzer = AgentRouteAnalyzer(Settings(), llm)

    decision = analyzer.analyze(question="图中有什么？", filters=None, rule_state={})

    assert decision.route == "image_first"
    assert "image" in decision.target_modalities


def test_agent_retrieval_planner_parses_llm_json() -> None:
    llm = DummyLLM(
        '{"tasks":[{"channel":"table","query_text":"accuracy table","top_k":8,'
        '"filters":{"modality":"table"},"reason":"table question"}],'
        '"page_window":2,"risk_flags":[],"reasoning_summary":"need table"}'
    )
    planner = AgentRetrievalPlanner(Settings(), llm)
    rule_plan = RetrievalPlan(
        question="q",
        tasks=[RetrievalTask(channel="vector", query_text="q", top_k=4, filters={})],
    )

    decision = planner.plan(question="q", filters=None, state={}, rule_plan=rule_plan)

    assert decision.tasks[0].channel == "table"
    assert decision.page_window == 2
