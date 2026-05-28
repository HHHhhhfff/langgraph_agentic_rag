from __future__ import annotations

from types import SimpleNamespace

from agentic_rag.config import Settings
from agentic_rag.generation.prompt_builder import PromptBuilder
from agentic_rag.graph.task_graph import TaskGraphRAG
from agentic_rag.schemas import SearchHit


class DummyEmbedding:
    def embed_texts(self, texts):
        return [[0.1, 0.2] for _ in texts]


class SequencedLLM:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.outputs:
            return self.outputs.pop(0)
        return "answer [1]"


class RecordingRetriever:
    def __init__(self, hits_by_call=None):
        self.plans = []
        self.hits_by_call = hits_by_call or []

    def retrieve(self, *, query_text, query_vector, filters=None, plan=None):
        self.plans.append(plan)
        index = len(self.plans) - 1
        hits = self.hits_by_call[index] if index < len(self.hits_by_call) else []
        channels = [task.channel for task in plan.tasks] if plan else []
        route_hits = {channel: [] for channel in channels}
        for hit in hits:
            route_hits.setdefault(hit.channel, []).append(hit)
        return SimpleNamespace(hits=hits, route_hits=route_hits, expanded_hits=hits, executed_channels=channels)


def _hit(text="TaskGraph evidence", channel="vector", modality="text") -> SearchHit:
    return SearchHit(
        point_id=f"p-{channel}",
        text=text,
        score=0.9,
        channel=channel,
        modality=modality,
        metadata={"source": "doc.md", "title": "Doc", "chunk_index": 0, "modality": modality},
    )


def _graph(settings: Settings, llm, retriever) -> TaskGraphRAG:
    return TaskGraphRAG(
        settings=settings,
        embedding_provider=DummyEmbedding(),
        retriever=retriever,
        llm_client=llm,
        prompt_builder=PromptBuilder(settings),
    )


def test_task_graph_agent_route_and_plan_debug() -> None:
    llm = SequencedLLM(
        [
            '{"intent":"image_qa","target_modalities":["image","text"],"need_cross_doc":false,'
            '"need_page_level":true,"route":"image_first","reasoning_summary":"image route","confidence":0.9}',
            '{"tasks":[{"channel":"image","query_text":"image trend","top_k":6,"filters":{"modality":"image"},'
            '"reason":"image evidence"}],"page_window":2,"risk_flags":[],"reasoning_summary":"add image"}',
            "answer [1]",
        ]
    )
    retriever = RecordingRetriever(hits_by_call=[[_hit("image evidence TaskGraph", "image", "image")]])
    graph = _graph(
        Settings(
            tg_agent_route_enabled=True,
            tg_agent_retrieval_planner_enabled=True,
            tg_agent_chunk_grading_enabled=False,
            tg_min_evidence_hits=1,
            tg_min_coverage_ratio=0.0,
            tg_citation_strict=False,
        ),
        llm,
        retriever,
    )

    result = graph.invoke("图中展示了什么？")

    assert result.debug["agent_route_used"] is True
    assert result.debug["agent_plan_used"] is True
    assert result.debug["route"] == "image_first"
    assert "image" in retriever.plans[0].channels()


def test_task_graph_agent_evidence_conservative_retry() -> None:
    llm = SequencedLLM(
        [
            '{"claim_supported":false,"support_level":"weak","support_score":0.2,'
            '"missing_slots":["numeric"],"unsupported_claims":["number missing"],'
            '"conflict_detected":false,"conflict_level":"none","conflict_reasons":[],'
            '"recommended_retry_actions":["missing_numeric"],"gate_decision":"retry",'
            '"reasoning_summary":"need numeric"}'
        ]
    )
    retriever = RecordingRetriever(hits_by_call=[[_hit("TaskGraph evidence")]])
    graph = _graph(
        Settings(
            tg_agent_route_enabled=False,
            tg_route_llm_enabled=False,
            tg_agent_retrieval_planner_enabled=False,
            tg_agent_evidence_critic_enabled=True,
            tg_agent_chunk_grading_enabled=False,
            tg_agent_retry_advisor_enabled=False,
            tg_max_retries=1,
            tg_min_evidence_hits=1,
            tg_min_coverage_ratio=0.0,
            tg_min_gain_threshold=2.0,
        ),
        llm,
        retriever,
    )

    result = graph.invoke("TaskGraph")

    assert result.debug["agent_evidence_used"] is True
    assert result.debug["gate_decision"] == "retry"
    assert "number missing" in result.debug["unsupported_claims"]


def test_task_graph_agent_retry_updates_next_plan() -> None:
    llm = SequencedLLM(
        [
            '{"rewritten_query_text":"accuracy table","add_channels":["table"],'
            '"increase_top_k_channels":["vector"],"page_window":2,"filters":{"modality":"table"},'
            '"reasons":["missing table"]}',
            "answer [1]",
        ]
    )
    retriever = RecordingRetriever(hits_by_call=[[], [_hit("accuracy table evidence", "table", "table")]])
    graph = _graph(
        Settings(
            tg_agent_route_enabled=False,
            tg_route_llm_enabled=False,
            tg_agent_retrieval_planner_enabled=False,
            tg_agent_evidence_critic_enabled=False,
            tg_agent_chunk_grading_enabled=False,
            tg_agent_retry_advisor_enabled=True,
            tg_max_retries=1,
            tg_min_evidence_hits=1,
            tg_min_coverage_ratio=0.0,
            tg_citation_strict=False,
        ),
        llm,
        retriever,
    )

    result = graph.invoke("accuracy?")

    assert len(retriever.plans) == 2
    assert "table" in retriever.plans[1].channels()
    assert result.debug["agent_retry_used"] is True


def test_task_graph_agent_disabled_keeps_rule_path() -> None:
    llm = SequencedLLM(["answer [1]"])
    retriever = RecordingRetriever(hits_by_call=[[_hit("TaskGraph evidence")]])
    graph = _graph(
        Settings(tg_agent_chunk_grading_enabled=False, tg_min_evidence_hits=1, tg_min_coverage_ratio=0.0, tg_citation_strict=False),
        llm,
        retriever,
    )

    result = graph.invoke("TaskGraph")

    assert result.debug["agent_route_used"] is False
    assert result.debug["agent_plan_used"] is False
    assert result.debug["agent_evidence_used"] is False
    assert result.debug["agent_retry_used"] is False
