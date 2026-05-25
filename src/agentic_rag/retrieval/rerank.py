from __future__ import annotations

from dataclasses import dataclass

from agentic_rag.config import Settings
from agentic_rag.models.providers import Reranker
from agentic_rag.retrieval.scoring import STAGE_RERANK, compute_composite_scores, filter_by_stage_threshold
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
            scoped = hits[: self.settings.context_top_n]
            scoped = compute_composite_scores(scoped, stage=STAGE_RERANK, settings=self.settings)
            scoped = filter_by_stage_threshold(scoped, stage=STAGE_RERANK, settings=self.settings)
            return RerankResult(hits=scoped, used_rerank=False)

        top_n = min(self.settings.rerank_top_n, len(hits))
        try:
            if hasattr(self.reranker, "rerank_hits"):
                rows = self.reranker.rerank_hits(query=query, hits=hits, top_n=top_n)
            else:
                docs = [_hit_rerank_text(h) for h in hits]
                rows = self.reranker.rerank(query=query, documents=docs, top_n=top_n)
        except Exception as exc:
            fallback = hits[: self.settings.context_top_n]
            fallback = compute_composite_scores(fallback, stage=STAGE_RERANK, settings=self.settings)
            fallback = filter_by_stage_threshold(fallback, stage=STAGE_RERANK, settings=self.settings)
            return RerankResult(
                hits=fallback,
                used_rerank=False,
                fallback_reason=f"rerank_failed:{type(exc).__name__}: {_safe_excerpt(str(exc))}",
            )

        picked: list[SearchHit] = []
        for rank, row in enumerate(rows, start=1):
            idx = row.get("index")
            score = row.get("score")
            if not isinstance(idx, int) or idx < 0 or idx >= len(hits):
                continue
            hit = hits[idx]
            if isinstance(score, (int, float)):
                hit.metadata["rerank_score"] = float(score)
            hit.metadata["rerank_rank"] = rank
            picked.append(hit)

        if not picked:
            fallback = hits[: self.settings.context_top_n]
            fallback = compute_composite_scores(fallback, stage=STAGE_RERANK, settings=self.settings)
            fallback = filter_by_stage_threshold(fallback, stage=STAGE_RERANK, settings=self.settings)
            return RerankResult(
                hits=fallback,
                used_rerank=False,
                fallback_reason="rerank_empty",
            )

        picked = compute_composite_scores(picked, stage=STAGE_RERANK, settings=self.settings)
        picked = filter_by_stage_threshold(picked, stage=STAGE_RERANK, settings=self.settings)
        return RerankResult(hits=picked[: self.settings.context_top_n], used_rerank=True)


def _hit_rerank_text(hit: SearchHit) -> str:
    parts = [
        hit.text or "",
        hit.table_markdown or "",
        hit.formula_latex or "",
        hit.caption or "",
        hit.ocr_text or "",
        hit.object_description or "",
    ]
    return "\n".join(part for part in parts if part).strip()


def _safe_excerpt(text: str, max_chars: int = 300) -> str:
    return " ".join((text or "").strip().split())[:max_chars]
