from __future__ import annotations

from agentic_rag.config import Settings
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
