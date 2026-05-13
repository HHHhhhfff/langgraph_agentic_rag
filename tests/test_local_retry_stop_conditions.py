from __future__ import annotations

from types import SimpleNamespace

from agentic_rag.config import Settings
from agentic_rag.generation.prompt_builder import PromptBuilder
from agentic_rag.graph.task_graph import TaskGraphRAG
from agentic_rag.schemas import SearchHit


class DummyEmbedding:
    def embed_texts(self, texts):
        return [[0.1, 0.2] for _ in texts]


class DummyLLM:
    def generate(self, prompt: str) -> str:
        return "answer [1]"


class NoEvidenceRetriever:
    def retrieve(self, *, query_text, query_vector, filters=None):
        return SimpleNamespace(hits=[], route_hits={"vector": [], "bm25": [], "page": [], "table": []}, expanded_hits=[])


class VerboseEvidenceRetriever:
    def retrieve(self, *, query_text, query_vector, filters=None):
        long_text = "A" * 12000
        hit = SearchHit(
            point_id="p1",
            node_id="n1",
            text=long_text,
            score=0.9,
            doc_id="d1",
            page=1,
            metadata={"source": "big.md", "title": "Big", "chunk_index": 0, "modality": "text"},
        )
        return SimpleNamespace(
            hits=[hit],
            route_hits={"vector": [hit], "bm25": [], "page": [], "table": []},
            expanded_hits=[hit],
        )


def _build_graph(settings: Settings, retriever) -> TaskGraphRAG:
    return TaskGraphRAG(
        settings=settings,
        embedding_provider=DummyEmbedding(),
        retriever=retriever,
        llm_client=DummyLLM(),
        prompt_builder=PromptBuilder(settings),
    )


def test_retry_stops_at_max_retries() -> None:
    settings = Settings(
        tg_max_retries=1,
        tg_min_evidence_hits=2,
        tg_min_coverage_ratio=0.1,
    )
    graph = _build_graph(settings, NoEvidenceRetriever())
    result = graph.invoke("问题")
    assert result.debug.get("retry_count") == 1
    assert result.debug.get("evidence_ok") is False


def test_retry_stops_on_token_budget() -> None:
    settings = Settings(
        tg_max_retries=3,
        tg_budget_tokens=100,
        tg_min_evidence_hits=2,
        tg_min_coverage_ratio=0.9,
    )
    graph = _build_graph(settings, VerboseEvidenceRetriever())
    result = graph.invoke("问题")
    assert result.debug.get("retry_count") == 0
    assert result.debug.get("evidence_ok") is False

