from __future__ import annotations

from dataclasses import dataclass

from agentic_rag.config import Settings
from agentic_rag.models.providers import Reranker
from agentic_rag.schemas import SearchHit


@dataclass(slots=True)
class RerankResult:
    """Rerank output and fallback information."""

    hits: list[SearchHit]
    used_rerank: bool
    fallback_reason: str | None = None


class RerankService:
    """Apply rerank and gracefully fall back on failure."""

    def __init__(self, settings: Settings, reranker: Reranker):
        self.settings = settings
        self.reranker = reranker

    def rerank(self, query: str, hits: list[SearchHit]) -> RerankResult:
        if not hits:
            return RerankResult(hits=[], used_rerank=False)
        if not self.settings.rerank_enabled:
            return RerankResult(hits=hits[: self.settings.context_top_n], used_rerank=False)

        docs = [h.text for h in hits]
        top_n = min(self.settings.rerank_top_n, len(hits))
        try:
            rows = self.reranker.rerank(query=query, documents=docs, top_n=top_n)
        except Exception as exc:
            return RerankResult(
                hits=hits[: self.settings.context_top_n],
                used_rerank=False,
                fallback_reason=f"rerank_failed: {exc}",
            )

        picked: list[SearchHit] = []
        for row in rows:
            idx = row.get("index")
            score = row.get("score")
            if not isinstance(idx, int) or idx < 0 or idx >= len(hits):
                continue
            hit = hits[idx]
            hit.score = float(score) if isinstance(score, (int, float)) else hit.score
            picked.append(hit)

        if not picked:
            return RerankResult(
                hits=hits[: self.settings.context_top_n],
                used_rerank=False,
                fallback_reason="rerank_empty",
            )

        return RerankResult(hits=picked[: self.settings.context_top_n], used_rerank=True)
