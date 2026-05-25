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


def _node_hit(
    node_id: str,
    text: str,
    score: float = 1.0,
    *,
    modality: str = "text",
    relationships: dict | None = None,
) -> SearchHit:
    return SearchHit(
        point_id=node_id,
        node_id=node_id,
        text=text,
        score=score,
        score_vector=score,
        doc_id="doc1",
        page=1,
        modality=modality,
        metadata={"source": "s.md", "chunk_index": 0, "modality": modality},
        relationships=relationships or {},
    )


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


def test_hybrid_retriever_auto_expands_related_modality_for_normal_plan() -> None:
    seed = _node_hit(
        "text:1",
        "seed text",
        relationships={"related_table_node_ids": ["table:1"]},
    )
    table = _node_hit("table:1", "table markdown", 0.2, modality="table")

    class StoreWithRelated:
        def scroll_hits(self, *args, **kwargs):
            return [seed, table]

    settings = Settings(
        bm25_enabled=False,
        rel_expand_explicit_relationships=True,
        rel_expand_related_modality_enabled=True,
        rel_expand_context_text_enabled=False,
        rel_expand_min_seed_composite_score=0.0,
    )
    retriever = HybridRetriever(
        settings=settings,
        store=StoreWithRelated(),
        vector_search_fn=lambda query_vector, filters=None: [seed],
    )
    plan = RetrievalPlan(question="q", tasks=[RetrievalTask(channel="vector")])

    result = retriever.retrieve(query_text="q", query_vector=[0.1], plan=plan)

    expanded = {hit.node_id: hit for hit in result.expanded_hits}
    assert "table:1" in expanded
    assert all(hit.node_id != "table:1" for hit in result.hits)
    assert expanded["table:1"].metadata["retrieval_expanded_from_node_id"] == "text:1"
    assert expanded["table:1"].metadata["retrieval_expansion_relation"] == "related_table_node_ids"
    assert expanded["table:1"].metadata["retrieval_candidate_pool"] == "related_modality"


def test_hybrid_retriever_auto_related_modality_can_be_disabled() -> None:
    seed = _node_hit(
        "text:1",
        "seed text",
        relationships={"related_table_node_ids": ["table:1"]},
    )
    table = _node_hit("table:1", "table markdown", 0.2, modality="table")

    class StoreWithRelated:
        def scroll_hits(self, *args, **kwargs):
            return [seed, table]

    settings = Settings(
        bm25_enabled=False,
        rel_expand_explicit_relationships=True,
        rel_expand_related_modality_enabled=False,
        rel_expand_context_text_enabled=False,
        rel_expand_min_seed_composite_score=0.0,
    )
    retriever = HybridRetriever(
        settings=settings,
        store=StoreWithRelated(),
        vector_search_fn=lambda query_vector, filters=None: [seed],
    )
    plan = RetrievalPlan(question="q", tasks=[RetrievalTask(channel="vector")])

    result = retriever.retrieve(query_text="q", query_vector=[0.1], plan=plan)

    assert all(hit.node_id != "table:1" for hit in result.expanded_hits)


def test_hybrid_retriever_explicit_relationship_switch_disables_auto_expansion(monkeypatch) -> None:
    seed = _node_hit(
        "text:1",
        "seed text",
        relationships={"related_table_node_ids": ["table:1"]},
    )
    scroll_calls = 0

    class StoreWithRelated:
        def scroll_hits(self, *args, **kwargs):
            nonlocal scroll_calls
            scroll_calls += 1
            return [seed, _node_hit("table:1", "table markdown", 0.2, modality="table")]

    settings = Settings(
        bm25_enabled=False,
        rel_expand_explicit_relationships=False,
        rel_expand_related_modality_enabled=True,
        rel_expand_context_text_enabled=True,
    )
    retriever = HybridRetriever(
        settings=settings,
        store=StoreWithRelated(),
        vector_search_fn=lambda query_vector, filters=None: [seed],
    )
    plan = RetrievalPlan(question="q", tasks=[RetrievalTask(channel="vector")])

    result = retriever.retrieve(query_text="q", query_vector=[0.1], plan=plan)

    assert scroll_calls == 0
    assert [hit.node_id for hit in result.expanded_hits] == ["text:1"]


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


def test_hybrid_retriever_weak_keyword_adds_table_channel(monkeypatch) -> None:
    settings = Settings(
        _env_file=None,
        bm25_enabled=False,
        enable_named_vectors=False,
        retrieval_auto_table_channel_enabled=True,
        retrieval_auto_formula_channel_enabled=False,
        retrieval_table_trigger_keywords="表格,列表",
    )
    calls: list[str] = []
    retriever = HybridRetriever(
        settings=settings,
        store=DummyStore(),
        vector_search_fn=lambda query_vector, filters=None: calls.append("vector") or [_hit("v1", "vector")],
    )
    monkeypatch.setattr(
        retriever.table,
        "retrieve",
        lambda query_text, filters=None, top_k=None: calls.append("table") or [_hit("t1", "table", 0.6)],
    )

    result = retriever.retrieve(query_text="查看表格", query_vector=[0.1])

    assert calls == ["vector", "table"]
    assert "table" in result.route_hits
