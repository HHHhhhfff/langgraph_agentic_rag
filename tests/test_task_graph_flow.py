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
        return "这是答案 [1]"


class DummyRetriever:
    def retrieve(self, *, query_text, query_vector, filters=None):
        hit = SearchHit(
            point_id="p1",
            node_id="n1",
            text="这是证据文本",
            score=0.95,
            doc_id="d1",
            page=1,
            metadata={"source": "doc.md", "title": "Doc", "chunk_index": 0, "modality": "text"},
        )
        return SimpleNamespace(
            hits=[hit],
            route_hits={"vector": [hit], "bm25": [], "page": [], "table": []},
            expanded_hits=[hit],
        )


def test_task_graph_happy_path() -> None:
    settings = Settings(
        tg_max_retries=2,
        tg_min_evidence_hits=1,
        tg_min_coverage_ratio=0.0,
        tg_citation_strict=True,
        tg_allow_refusal=True,
    )
    graph = TaskGraphRAG(
        settings=settings,
        embedding_provider=DummyEmbedding(),
        retriever=DummyRetriever(),
        llm_client=DummyLLM(),
        prompt_builder=PromptBuilder(settings),
    )

    result = graph.invoke("请给出答案")

    assert "答案" in result.answer
    assert result.retrieved_count >= 1
    assert result.debug.get("evidence_ok") is True
    assert result.debug.get("citation_ok") is True

