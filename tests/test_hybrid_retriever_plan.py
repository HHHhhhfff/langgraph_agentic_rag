from __future__ import annotations

from agentic_rag.config import Settings
from agentic_rag.retrieval.hybrid import HybridRetriever
from agentic_rag.retrieval.retrieval_plan import RetrievalPlan, RetrievalTask
from agentic_rag.schemas import SearchHit


class DummyStore:
    def scroll_hits(self, *args, **kwargs):
        return []


def _hit(point_id: str, text: str, score: float = 1.0) -> SearchHit:
    return SearchHit(point_id=point_id, text=text, score=score, metadata={"source": "s.md", "chunk_index": 0})


def test_hybrid_retriever_executes_only_plan_channels(monkeypatch) -> None:
    settings = Settings(bm25_enabled=True)
    calls: list[str] = []

    retriever = HybridRetriever(
        settings=settings,
        store=DummyStore(),
        vector_search_fn=lambda query_vector, filters=None: calls.append("vector") or [_hit("v1", "vector")],
    )
    monkeypatch.setattr(
        retriever.bm25,
        "retrieve",
        lambda query_text, filters=None, top_k=None: calls.append("bm25") or [_hit("b1", "bm25", 0.8)],
    )
    monkeypatch.setattr(
        retriever.page,
        "retrieve",
        lambda query_text, **kwargs: calls.append("page") or [_hit("p1", "page", 0.7)],
    )
    monkeypatch.setattr(
        retriever.table,
        "retrieve",
        lambda query_text, **kwargs: calls.append("table") or [_hit("t1", "table", 0.6)],
    )

    plan = RetrievalPlan(
        question="q",
        tasks=[
            RetrievalTask(channel="vector", query_text="q", top_k=3),
            RetrievalTask(channel="bm25", query_text="q", top_k=3),
        ],
    )
    result = retriever.retrieve(query_text="q", query_vector=[0.1], plan=plan)

    assert calls == ["vector", "bm25"]
    assert set(result.route_hits) == {"vector", "bm25"}
    assert result.executed_channels == ["vector", "bm25"]
    assert all(hit.point_id != "p1" for hit in result.hits)


def test_hybrid_retriever_relationship_expands_only_when_planned(monkeypatch) -> None:
    settings = Settings(bm25_enabled=False)
    store = DummyStore()
    retriever = HybridRetriever(
        settings=settings,
        store=store,
        vector_search_fn=lambda query_vector, filters=None: [_hit("v1", "vector")],
    )
    scroll_calls = 0

    def scroll_hits(*args, **kwargs):
        nonlocal scroll_calls
        scroll_calls += 1
        return []

    monkeypatch.setattr(store, "scroll_hits", scroll_hits)
    plan = RetrievalPlan(question="q", tasks=[RetrievalTask(channel="vector"), RetrievalTask(channel="relationship")])
    result = retriever.retrieve(query_text="q", query_vector=[0.1], plan=plan)

    assert scroll_calls == 1
    assert "relationship" in result.route_hits
    assert result.executed_channels == ["vector", "relationship"]


def test_hybrid_retriever_uses_plan_page_window_for_relationship_expansion() -> None:
    settings = Settings(bm25_enabled=False, rel_expand_pages=1)
    seed = SearchHit(
        point_id="seed",
        node_id="seed",
        text="seed",
        score=1.0,
        doc_id="d1",
        page=1,
        metadata={"source": "d1.md", "chunk_index": 0},
    )
    far_page = SearchHit(
        point_id="far",
        node_id="far",
        text="far page",
        score=0.5,
        doc_id="d1",
        page=3,
        metadata={"source": "d1.md", "chunk_index": 1},
    )

    class StoreWithPages:
        def scroll_hits(self, *args, **kwargs):
            return [seed, far_page]

    retriever = HybridRetriever(
        settings=settings,
        store=StoreWithPages(),
        vector_search_fn=lambda query_vector, filters=None: [seed],
    )
    plan = RetrievalPlan(
        question="q",
        page_window=2,
        tasks=[RetrievalTask(channel="vector"), RetrievalTask(channel="relationship")],
    )

    result = retriever.retrieve(query_text="q", query_vector=[0.1], plan=plan)

    assert any(hit.point_id == "far" for hit in result.expanded_hits)
