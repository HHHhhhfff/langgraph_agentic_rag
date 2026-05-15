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
    def retrieve(self, *, query_text, query_vector, filters=None, plan=None):
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
            executed_channels=[task.channel for task in plan.tasks] if plan else ["vector"],
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
    assert "vector" in result.debug.get("executed_channels", [])
    assert result.debug.get("support_level") in {"partial", "strong"}
    assert isinstance(result.debug.get("support_score"), float)
    assert result.debug.get("gate_decision") == "pass"
    assert isinstance(result.debug.get("conflict_reasons"), list)


class RetryAwareRetriever:
    def __init__(self):
        self.channels_by_call: list[list[str]] = []

    def retrieve(self, *, query_text, query_vector, filters=None, plan=None):
        channels = [task.channel for task in plan.tasks] if plan else []
        self.channels_by_call.append(channels)
        if len(self.channels_by_call) == 1:
            return SimpleNamespace(hits=[], route_hits={}, expanded_hits=[], executed_channels=channels)
        hit = SearchHit(
            point_id="p2",
            node_id="n2",
            text="retry evidence",
            score=0.9,
            metadata={"source": "retry.md", "title": "Retry", "chunk_index": 0, "modality": "text"},
        )
        return SimpleNamespace(
            hits=[hit],
            route_hits={"vector": [hit]},
            expanded_hits=[hit],
            executed_channels=channels,
        )


def test_task_graph_local_retry_uses_updated_plan() -> None:
    settings = Settings(
        tg_max_retries=1,
        tg_min_evidence_hits=1,
        tg_min_coverage_ratio=0.0,
        tg_citation_strict=False,
    )
    retriever = RetryAwareRetriever()
    graph = TaskGraphRAG(
        settings=settings,
        embedding_provider=DummyEmbedding(),
        retriever=retriever,
        llm_client=DummyLLM(),
        prompt_builder=PromptBuilder(settings),
    )

    result = graph.invoke("retry question")

    assert len(retriever.channels_by_call) == 2
    assert "page" not in retriever.channels_by_call[0]
    assert "page" in retriever.channels_by_call[1]
    assert result.debug.get("retry_count") == 1
    assert result.debug.get("retry_actions")
    assert result.debug.get("retry_history")
    assert result.debug.get("rewritten_query_text")


def test_task_graph_local_retry_adds_table_from_missing_slot() -> None:
    settings = Settings(tg_max_retries=1)
    graph = TaskGraphRAG(
        settings=settings,
        embedding_provider=DummyEmbedding(),
        retriever=DummyRetriever(),
        llm_client=DummyLLM(),
        prompt_builder=PromptBuilder(settings),
    )
    state = {
        "question": "accuracy",
        "retrieval_plan": {
            "question": "accuracy",
            "query_text": "accuracy",
            "tasks": [{"channel": "vector", "query_text": "accuracy", "top_k": 3, "filters": {}}],
        },
        "retry_count": 0,
        "missing_slots": ["modality:table"],
        "gate_reasons": ["missing_modality:table"],
        "expanded_hits": [],
        "fused_hits": [],
    }

    updated = graph._local_retry_node(state)  # noqa: SLF001 - intentional node-level integration test

    channels = [task["channel"] for task in updated["retrieval_plan"]["tasks"]]
    assert "table" in channels
    assert updated["retry_actions"]


def test_task_graph_local_retry_page_slot_sets_page_window() -> None:
    settings = Settings(tg_max_retries=1, rel_expand_pages=1, tg_retry_max_page_window=3)
    graph = TaskGraphRAG(
        settings=settings,
        embedding_provider=DummyEmbedding(),
        retriever=DummyRetriever(),
        llm_client=DummyLLM(),
        prompt_builder=PromptBuilder(settings),
    )
    state = {
        "question": "page 3",
        "retrieval_plan": {
            "question": "page 3",
            "query_text": "page 3",
            "tasks": [{"channel": "vector", "query_text": "page 3", "top_k": 3, "filters": {}}],
        },
        "retry_count": 0,
        "missing_slots": ["page"],
        "gate_reasons": ["missing_page"],
        "expanded_hits": [],
        "fused_hits": [],
    }

    updated = graph._local_retry_node(state)  # noqa: SLF001 - intentional node-level integration test

    channels = [task["channel"] for task in updated["retrieval_plan"]["tasks"]]
    assert "page" in channels
    assert updated["retrieval_plan"]["page_window"] == 2
    assert updated["page_window"] == 2

