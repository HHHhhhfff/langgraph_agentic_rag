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
        return "ok [1]"


class EmptyRetriever:
    def retrieve(self, *, query_text, query_vector, filters=None):
        return SimpleNamespace(hits=[], route_hits={"vector": [], "bm25": [], "page": [], "table": []}, expanded_hits=[])


class ConflictRetriever:
    def retrieve(self, *, query_text, query_vector, filters=None):
        hit = SearchHit(
            point_id="p1",
            node_id="n1",
            text="This is not true, yes it is",
            score=0.7,
            doc_id="d1",
            page=1,
            metadata={"source": "x.md", "title": "X", "chunk_index": 0, "modality": "text"},
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


def test_evidence_gate_insufficient_hits_causes_non_ok() -> None:
    settings = Settings(
        tg_max_retries=1,
        tg_min_evidence_hits=2,
        tg_min_coverage_ratio=0.1,
    )
    graph = _build_graph(settings, EmptyRetriever())
    result = graph.invoke("简单问题")
    assert result.debug.get("evidence_ok") is False
    assert "insufficient_hits" in result.debug.get("evidence_gaps", [])


def test_evidence_gate_conflict_can_refuse() -> None:
    settings = Settings(
        tg_max_retries=1,
        tg_min_evidence_hits=1,
        tg_min_coverage_ratio=0.0,
        tg_allow_refusal=True,
    )
    graph = _build_graph(settings, ConflictRetriever())
    result = graph.invoke("是否成立")
    assert result.debug.get("refusal") is True
    assert result.debug.get("refusal_reason") == "evidence_conflict"

