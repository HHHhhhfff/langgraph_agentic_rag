from __future__ import annotations

from agentic_rag.config import Settings
from agentic_rag.retrieval.hybrid import HybridRetriever
from agentic_rag.retrieval.retrieval_plan import RetrievalPlan, RetrievalTask
from agentic_rag.schemas import SearchHit


class DummyStore:
    def scroll_hits(self, *args, **kwargs):
        return []


def _hit(point_id: str, score: float = 0.9) -> SearchHit:
    return SearchHit(point_id=point_id, text=point_id, score=score, metadata={"source": "s.md", "chunk_index": 0})


def test_hybrid_named_vectors_routes_vector_table_image_formula() -> None:
    settings = Settings(enable_named_vectors=True, bm25_enabled=True)
    calls: list[dict] = []

    def vector_search_fn(query_vector, filters=None, vector_name=None, top_k=None):
        calls.append({"filters": filters, "vector_name": vector_name, "top_k": top_k})
        return [_hit(f"{vector_name}-{len(calls)}")]

    retriever = HybridRetriever(settings=settings, store=DummyStore(), vector_search_fn=vector_search_fn)

    def table_should_not_run(*args, **kwargs):
        raise AssertionError("TableRetriever should not run when named vectors are enabled")

    retriever.table.retrieve = table_should_not_run  # type: ignore[method-assign]
    plan = RetrievalPlan(
        question="q",
        tasks=[
            RetrievalTask(channel="vector", top_k=3),
            RetrievalTask(channel="table", top_k=4),
            RetrievalTask(channel="image", top_k=5),
            RetrievalTask(channel="formula", top_k=6),
        ],
    )

    result = retriever.retrieve(query_text="q", query_vector=[0.1], plan=plan)

    assert [call["vector_name"] for call in calls] == ["text", "table", "image", "text"]
    assert calls[0]["top_k"] == 3
    assert calls[1]["filters"]["modality"] == "table"
    assert calls[2]["filters"]["modality"] == "image"
    assert calls[3]["filters"]["modality"] == "formula"
    assert result.executed_channels == ["vector", "table", "image", "formula"]
    assert set(result.route_hits) == {"vector", "table", "image", "formula"}
    assert all(hit.metadata.get("vector_name") for hits in result.route_hits.values() for hit in hits)


def test_hybrid_named_vectors_respects_explicit_task_vector_name() -> None:
    settings = Settings(enable_named_vectors=True)
    calls: list[str | None] = []

    def vector_search_fn(query_vector, filters=None, vector_name=None, top_k=None):
        calls.append(vector_name)
        return [_hit("custom")]

    retriever = HybridRetriever(settings=settings, store=DummyStore(), vector_search_fn=vector_search_fn)
    plan = RetrievalPlan(
        question="q",
        tasks=[RetrievalTask(channel="vector", metadata={"vector_name": "custom_text"})],
    )

    retriever.retrieve(query_text="q", query_vector=[0.1], plan=plan)

    assert calls == ["custom_text"]


def test_hybrid_without_named_vectors_uses_table_retriever() -> None:
    settings = Settings(enable_named_vectors=False)
    vector_calls: list[str] = []
    table_calls: list[str] = []
    retriever = HybridRetriever(
        settings=settings,
        store=DummyStore(),
        vector_search_fn=lambda query_vector, filters=None, vector_name=None, top_k=None: vector_calls.append("vector") or [],
    )
    retriever.table.retrieve = lambda query_text, filters=None, top_k=None: table_calls.append("table") or [_hit("table")]  # type: ignore[method-assign]
    plan = RetrievalPlan(question="q", tasks=[RetrievalTask(channel="table")])

    result = retriever.retrieve(query_text="q", query_vector=[0.1], plan=plan)

    assert vector_calls == []
    assert table_calls == ["table"]
    assert result.executed_channels == ["table"]


def test_hybrid_without_named_vectors_image_formula_use_bm25_fallback() -> None:
    settings = Settings(enable_named_vectors=False)
    bm25_filters: list[dict] = []
    retriever = HybridRetriever(
        settings=settings,
        store=DummyStore(),
        vector_search_fn=lambda query_vector, filters=None, vector_name=None, top_k=None: [],
    )
    retriever.bm25.retrieve = lambda query_text, filters=None, top_k=None: bm25_filters.append(filters or {}) or [_hit("b")]  # type: ignore[method-assign]
    plan = RetrievalPlan(question="q", tasks=[RetrievalTask(channel="image"), RetrievalTask(channel="formula")])

    result = retriever.retrieve(query_text="q", query_vector=[0.1], plan=plan)

    assert bm25_filters == [{"modality": "image"}, {"modality": "formula"}]
    assert result.executed_channels == ["image", "formula"]
