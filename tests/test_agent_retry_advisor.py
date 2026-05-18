from __future__ import annotations

from agentic_rag.config import Settings
from agentic_rag.graph.agent_retry import AgentRetryAdvisor
from agentic_rag.retrieval.retrieval_plan import RetrievalPlan, RetrievalTask


class DummyLLM:
    def generate(self, prompt: str) -> str:
        return (
            '{"rewritten_query_text":"table accuracy","add_channels":["table","page"],'
            '"increase_top_k_channels":["vector"],"page_window":2,'
            '"filters":{"modality":"table"},"reasons":["missing table evidence"]}'
        )


def test_agent_retry_advisor_parses_json() -> None:
    advisor = AgentRetryAdvisor(Settings(), DummyLLM())
    plan = RetrievalPlan(question="q", tasks=[RetrievalTask(channel="vector", query_text="q", top_k=4)])

    advice = advisor.advise(state={"question": "q"}, rule_plan=plan)

    assert advice.rewritten_query_text == "table accuracy"
    assert "table" in advice.add_channels
