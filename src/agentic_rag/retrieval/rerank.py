from __future__ import annotations

from dataclasses import dataclass, field

from agentic_rag.config import Settings
from agentic_rag.models.providers import Reranker
from agentic_rag.retrieval.query_variants import has_query_anchor_overlap
from agentic_rag.retrieval.scoring import (
    STAGE_RERANK,
    compute_composite_scores,
    filter_by_stage_threshold_with_removed,
    mark_removed_hit,
    score_value,
)
from agentic_rag.schemas import SearchHit


@dataclass(slots=True)
class RerankResult:
    """Rerank output and fallback information."""

    hits: list[SearchHit]
    used_rerank: bool
    fallback_reason: str | None = None
    removed_hits: list[SearchHit] = field(default_factory=list)


class RerankService:
    """Apply rerank and gracefully fall back on failure."""

    def __init__(self, settings: Settings, reranker: Reranker):
        self.settings = settings
        self.reranker = reranker

    def rerank(self, query: str, hits: list[SearchHit]) -> RerankResult:
        if not hits:
            return RerankResult(hits=[], used_rerank=False)
        if not self.settings.rerank_enabled:
            scoped, top_removed = self._split_context_top_n(hits, reason_prefix="rerank_disabled")
            scoped = compute_composite_scores(scoped, stage=STAGE_RERANK, settings=self.settings)
            filtered = filter_by_stage_threshold_with_removed(scoped, stage=STAGE_RERANK, settings=self.settings)
            return RerankResult(hits=filtered.kept, used_rerank=False, removed_hits=[*filtered.removed, *top_removed])

        top_n = min(self.settings.rerank_top_n, len(hits))
        try:
            if hasattr(self.reranker, "rerank_hits"):
                rows = self.reranker.rerank_hits(query=query, hits=hits, top_n=top_n)
            else:
                docs = [_hit_rerank_text(h) for h in hits]
                rows = self.reranker.rerank(query=query, documents=docs, top_n=top_n)
        except Exception as exc:
            fallback, top_removed = self._split_context_top_n(hits, reason_prefix="rerank_failed_fallback")
            fallback = compute_composite_scores(fallback, stage=STAGE_RERANK, settings=self.settings)
            filtered = filter_by_stage_threshold_with_removed(fallback, stage=STAGE_RERANK, settings=self.settings)
            return RerankResult(
                hits=filtered.kept,
                used_rerank=False,
                fallback_reason=f"rerank_failed:{type(exc).__name__}: {_safe_excerpt(str(exc))}",
                removed_hits=[*filtered.removed, *top_removed],
            )

        picked: list[SearchHit] = []
        picked_ids: set[str] = set()
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
            picked_ids.add(_hit_key(hit))

        if not picked:
            fallback, top_removed = self._split_context_top_n(hits, reason_prefix="rerank_empty_fallback")
            fallback = compute_composite_scores(fallback, stage=STAGE_RERANK, settings=self.settings)
            filtered = filter_by_stage_threshold_with_removed(fallback, stage=STAGE_RERANK, settings=self.settings)
            return RerankResult(
                hits=filtered.kept,
                used_rerank=False,
                fallback_reason="rerank_empty",
                removed_hits=[*filtered.removed, *top_removed],
            )

        guarded = self._guardrail_anchor_hits(query=query, hits=hits, picked_ids=picked_ids)
        if guarded:
            insert_at = max(0, min(len(picked), self.settings.context_top_n - len(guarded)))
            for guarded_hit in guarded:
                picked.insert(insert_at, guarded_hit)
                insert_at += 1
                picked_ids.add(_hit_key(guarded_hit))

        removed: list[SearchHit] = []
        for input_rank, hit in enumerate(hits, start=1):
            if _hit_key(hit) in picked_ids:
                continue
            removed.append(
                mark_removed_hit(
                    hit.model_copy(deep=True),
                    stage=STAGE_RERANK,
                    reason="reranker_not_selected",
                    detail=f"input_rank={input_rank} not returned by reranker top_n={top_n}",
                    previous_rank=input_rank,
                    extra={
                        "retrieval_removed_by": "reranker_not_selected",
                        "retrieval_removed_limit": top_n,
                        "retrieval_previous_rank": input_rank,
                    },
                )
            )
        picked = compute_composite_scores(picked, stage=STAGE_RERANK, settings=self.settings)
        filtered = filter_by_stage_threshold_with_removed(picked, stage=STAGE_RERANK, settings=self.settings)
        kept, top_removed = self._split_context_top_n(filtered.kept, reason_prefix="context")
        return RerankResult(hits=kept, used_rerank=True, removed_hits=[*removed, *filtered.removed, *top_removed])

    def _guardrail_anchor_hits(
        self,
        *,
        query: str,
        hits: list[SearchHit],
        picked_ids: set[str],
    ) -> list[SearchHit]:
        if not self.settings.rerank_guardrail_enabled:
            return []
        candidates: list[tuple[int, SearchHit]] = []
        for input_rank, hit in enumerate(hits, start=1):
            if _hit_key(hit) in picked_ids:
                continue
            if score_value(hit) < self.settings.rerank_guardrail_min_anchor_score:
                continue
            if not has_query_anchor_overlap(query, hit):
                continue
            copy = hit.model_copy(deep=True)
            copy.metadata["rerank_guardrail_protected"] = True
            copy.metadata["rerank_guardrail_reason"] = "query_anchor_overlap"
            copy.metadata["rerank_guardrail_input_rank"] = input_rank
            copy.metadata.setdefault("rerank_rank", 10_000 + input_rank)
            candidates.append((input_rank, copy))
        candidates.sort(key=lambda item: (-(score_value(item[1])), item[0]))
        return [hit for _, hit in candidates[: self.settings.rerank_guardrail_max_anchor_hits]]

    def _split_context_top_n(self, hits: list[SearchHit], *, reason_prefix: str) -> tuple[list[SearchHit], list[SearchHit]]:
        kept = hits[: self.settings.context_top_n]
        removed: list[SearchHit] = []
        for rank, hit in enumerate(hits[self.settings.context_top_n :], start=self.settings.context_top_n + 1):
            reason = "context_top_n_limit"
            detail = f"rank={rank} > context_top_n={self.settings.context_top_n}"
            if reason_prefix.startswith("rerank_failed"):
                reason = "rerank_failed_fallback_excluded"
                detail = f"fallback excluded rank={rank}; context_top_n={self.settings.context_top_n}"
            elif reason_prefix.startswith("rerank_empty"):
                reason = "rerank_empty"
                detail = f"rerank empty fallback excluded rank={rank}; context_top_n={self.settings.context_top_n}"
            removed.append(
                mark_removed_hit(
                    hit.model_copy(deep=True),
                    stage=STAGE_RERANK,
                    reason=reason,
                    detail=detail,
                    previous_rank=rank,
                    extra={
                        "retrieval_removed_by": "context_top_n" if reason == "context_top_n_limit" else reason,
                        "retrieval_removed_limit": self.settings.context_top_n,
                        "retrieval_previous_rank": rank,
                    },
                )
            )
        return kept, removed


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


def _hit_key(hit: SearchHit) -> str:
    return hit.node_id or hit.point_id or f"{hit.doc_id}:{hit.metadata.get('chunk_index')}"
