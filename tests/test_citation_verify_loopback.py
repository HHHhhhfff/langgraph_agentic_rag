from __future__ import annotations

from types import SimpleNamespace

from agentic_rag.config import Settings
from agentic_rag.generation.prompt_builder import PromptBuilder
from agentic_rag.graph.task_graph import TaskGraphRAG
from agentic_rag.schemas import SearchHit


class DummyEmbedding:
    def embed_texts(self, texts):
        return [[0.1, 0.2] for _ in texts]


class NoCitationLLM:
    def __init__(self):
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        return "答案但没有引用"


class StableRetriever:
    def __init__(self):
        self.top_k_by_call: list[dict[str, int]] = []

    def retrieve(self, *, query_text, query_vector, filters=None, plan=None):
        channels = [task.channel for task in plan.tasks] if plan else ["vector"]
        self.top_k_by_call.append({task.channel: task.top_k for task in plan.tasks} if plan else {})
        hit = SearchHit(
            point_id="p1",
            node_id="n1",
            text="证据内容",
            score=0.9,
            doc_id="d1",
            page=2,
            metadata={"source": "doc.md", "title": "Doc", "chunk_index": 0, "modality": "text"},
        )
        return SimpleNamespace(
            hits=[hit],
            route_hits={"vector": [hit], "bm25": [], "page": [], "table": []},
            expanded_hits=[hit],
            executed_channels=channels,
        )


def test_citation_verify_loopback_until_retry_limit() -> None:
    settings = Settings(
        tg_max_retries=1,
        tg_min_evidence_hits=1,
        tg_min_coverage_ratio=0.0,
        tg_citation_strict=True,
    )
    llm = NoCitationLLM()
    retriever = StableRetriever()
    graph = TaskGraphRAG(
        settings=settings,
        embedding_provider=DummyEmbedding(),
        retriever=retriever,
        llm_client=llm,
        prompt_builder=PromptBuilder(settings),
    )

    result = graph.invoke("请回答")
    assert result.debug.get("citation_ok") is False
    assert result.debug.get("retry_count") == 1
    assert llm.calls >= 1
    assert len(retriever.top_k_by_call) == 2
    assert retriever.top_k_by_call[1]["vector"] > retriever.top_k_by_call[0]["vector"]

