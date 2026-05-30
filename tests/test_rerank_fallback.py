from __future__ import annotations

import sys
from types import SimpleNamespace

from agentic_rag.config import Settings
from agentic_rag.models.providers import DashScopeReranker
from agentic_rag.retrieval.rerank import RerankService
from agentic_rag.schemas import SearchHit


class FailingReranker:
    def rerank(self, query: str, documents: list[str], top_n: int):
        raise RuntimeError("upstream rerank timeout")


def test_rerank_fallback_when_provider_fails() -> None:
    settings = Settings(
        rerank_enabled=True,
        context_top_n=2,
    )
    hits = [
        SearchHit(point_id="1", text="d1", score=0.8, metadata={}),
        SearchHit(point_id="2", text="d2", score=0.7, metadata={}),
        SearchHit(point_id="3", text="d3", score=0.6, metadata={}),
    ]

    service = RerankService(settings=settings, reranker=FailingReranker())
    result = service.rerank("q", hits)

    assert result.used_rerank is False
    assert len(result.hits) == 2
    assert result.fallback_reason is not None
    assert "rerank_failed" in result.fallback_reason


class RecordingReranker:
    def __init__(self):
        self.calls = []

    def rerank_hits(self, query: str, hits: list[SearchHit], top_n: int):
        self.calls.append((query, hits, top_n))
        return [{"index": 1, "score": 0.95}, {"index": 0, "score": 0.75}]

    def rerank(self, query: str, documents: list[str], top_n: int):
        raise AssertionError("rerank_hits should be preferred")


def test_rerank_service_prefers_hit_level_rerank_and_sets_metadata() -> None:
    reranker = RecordingReranker()
    service = RerankService(Settings(rerank_enabled=True, context_top_n=2), reranker=reranker)
    hits = [
        SearchHit(point_id="1", text="d1", score=0.8, metadata={}),
        SearchHit(point_id="2", text="d2", score=0.7, metadata={}),
    ]

    result = service.rerank("q", hits)

    assert result.used_rerank is True
    assert [hit.point_id for hit in result.hits] == ["2", "1"]
    assert result.hits[0].metadata["rerank_score"] == 0.95
    assert result.hits[0].metadata["rerank_rank"] == 1
    assert reranker.calls


def test_rerank_service_records_reranker_not_selected_removed_hit() -> None:
    reranker = RecordingReranker()
    service = RerankService(Settings(_env_file=None, rerank_enabled=True, context_top_n=2), reranker=reranker)
    hits = [
        SearchHit(point_id="1", text="d1", score=0.8, metadata={}),
        SearchHit(point_id="2", text="d2", score=0.7, metadata={}),
        SearchHit(point_id="3", text="d3", score=0.6, metadata={}),
    ]

    result = service.rerank("q", hits)

    assert [hit.point_id for hit in result.hits] == ["2", "1"]
    assert [hit.point_id for hit in result.removed_hits] == ["3"]
    removed = result.removed_hits[0]
    assert removed.metadata["removed_reason"] == "reranker_not_selected"
    assert removed.metadata["retrieval_removed_by"] == "reranker_not_selected"
    assert removed.metadata["retrieval_previous_rank"] == 3


class ThreeRowReranker:
    def rerank_hits(self, query: str, hits: list[SearchHit], top_n: int):
        return [{"index": 0, "score": 0.9}, {"index": 1, "score": 0.8}, {"index": 2, "score": 0.7}]


def test_rerank_service_records_context_top_n_removed_hit() -> None:
    service = RerankService(
        Settings(_env_file=None, rerank_enabled=True, rerank_top_n=3, context_top_n=2),
        reranker=ThreeRowReranker(),
    )
    hits = [
        SearchHit(point_id="1", text="d1", score=0.8, metadata={}),
        SearchHit(point_id="2", text="d2", score=0.7, metadata={}),
        SearchHit(point_id="3", text="d3", score=0.6, metadata={}),
    ]

    result = service.rerank("q", hits)

    assert [hit.point_id for hit in result.hits] == ["1", "2"]
    assert [hit.point_id for hit in result.removed_hits] == ["3"]
    removed = result.removed_hits[0]
    assert removed.metadata["removed_reason"] == "context_top_n_limit"
    assert removed.metadata["retrieval_removed_by"] == "context_top_n"
    assert removed.metadata["retrieval_removed_limit"] == 2


class OneRowReranker:
    def rerank_hits(self, query: str, hits: list[SearchHit], top_n: int):
        return [{"index": 0, "score": 0.99}]


def test_rerank_guardrail_keeps_exact_anchor_hit_not_selected_by_reranker() -> None:
    settings = Settings(
        _env_file=None,
        rerank_enabled=True,
        rerank_top_n=1,
        context_top_n=2,
        rerank_guardrail_enabled=True,
        rerank_guardrail_min_anchor_score=0.2,
        retrieval_rerank_min_composite_score=0.0,
    )
    hits = [
        SearchHit(point_id="1", text="generic background", score=0.9, metadata={"score_composite": 0.9}),
        SearchHit(
            point_id="2",
            text="Ivan Terekhov was financially supported by the program.",
            score=0.6,
            metadata={"score_composite": 0.6},
        ),
    ]

    result = RerankService(settings=settings, reranker=OneRowReranker()).rerank(
        "Which program financially supported Ivan Terekhov?",
        hits,
    )

    assert "2" in [hit.point_id for hit in result.hits]
    protected = next(hit for hit in result.hits if hit.point_id == "2")
    assert protected.metadata["rerank_guardrail_protected"] is True
    assert "2" not in [hit.point_id for hit in result.removed_hits]


def test_dashscope_text_reranker_uses_string_documents(monkeypatch) -> None:
    calls = {}

    class FakeTextReRank:
        @staticmethod
        def call(**kwargs):
            calls.update(kwargs)
            return SimpleNamespace(status_code=200, output={"results": [{"index": 0, "relevance_score": 0.9}]})

    monkeypatch.setitem(sys.modules, "dashscope", SimpleNamespace(TextReRank=FakeTextReRank, api_key=""))
    reranker = DashScopeReranker(Settings(rerank_model="qwen3-rerank", rerank_enable_multimodal=False))

    rows = reranker.rerank("query", ["doc1", "doc2"], 1)

    assert rows == [{"index": 0, "score": 0.9, "document": None}]
    assert calls["query"] == "query"
    assert calls["documents"] == ["doc1", "doc2"]
    assert calls["instruct"]


def test_dashscope_vl_reranker_uses_multimodal_documents(monkeypatch) -> None:
    calls = {}

    class FakeTextReRank:
        @staticmethod
        def call(**kwargs):
            calls.update(kwargs)
            return SimpleNamespace(status_code=200, output={"results": [{"index": 1, "score": 0.88}]})

    monkeypatch.setitem(sys.modules, "dashscope", SimpleNamespace(TextReRank=FakeTextReRank, api_key=""))
    reranker = DashScopeReranker(Settings(rerank_model="qwen3-vl-rerank"))
    hits = [
        SearchHit(point_id="1", text="text doc", score=0.8, metadata={}),
        SearchHit(
            point_id="2",
            text="",
            score=0.7,
            modality="image",
            image_path="local.png",
            caption="image caption",
            metadata={},
        ),
    ]

    rows = reranker.rerank_hits("query", hits, 1)

    assert rows == [{"index": 1, "score": 0.88, "document": None}]
    assert calls["query"] == {"text": "query"}
    assert calls["documents"] == [{"text": "text doc"}, {"text": "image caption"}]
    assert "instruct" not in calls
