from __future__ import annotations

import math
import re
from typing import Any, Iterable

from pydantic import BaseModel, Field

from agentic_rag.config import Settings
from agentic_rag.retrieval.retrieval_plan import RetrievalChannel, RetrievalPlan, RetrievalTask
from agentic_rag.schemas import SearchHit


class LocalRetryDecision(BaseModel):
    """Serializable retry planning result."""

    plan: RetrievalPlan
    retry_count: int
    retry_actions: list[str] = Field(default_factory=list)
    rewritten_query_text: str | None = None
    evidence_gain: float = 0.0
    stop_reason: str | None = None


class LocalRetryPlanner:
    """Rule-based TaskGraph retry planner.

    This planner only updates RetrievalPlan. It never calls retrievers or LLMs.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def plan_retry(self, state: dict[str, Any]) -> LocalRetryDecision:
        retry_count = int(state.get("retry_count", 0)) + 1
        question = str(state.get("question") or "")
        filters = dict(state.get("filters") or {})
        plan = RetrievalPlan.model_validate(state.get("retrieval_plan") or {"question": question})
        plan.original_query = plan.original_query or question
        plan.query_text = plan.query_text or question
        plan.retry_count = retry_count

        reasons = self._reason_set(state)
        rewritten_query = self._rewrite_query(
            base_query=plan.original_query or plan.query_text or question,
            missing_slots=list(state.get("missing_slots") or []),
            reasons=reasons,
        )
        plan.rewritten_query_text = rewritten_query
        plan.query_text = rewritten_query

        actions: list[str] = []
        self._update_existing_queries(plan, rewritten_query, actions)

        if self._needs_global_top_k_increase(reasons, state):
            self._increase_all_top_k(plan, actions)

        if "low_keyword_coverage" in reasons or "keyword" in reasons or "missing_keyword" in reasons:
            self._upsert_task(plan, "bm25", rewritten_query, self.settings.bm25_top_k, filters, actions, min_multiplier=True)

        if "insufficient_hits" in reasons or "hits" in reasons or "missing_hits" in reasons:
            self._upsert_task(plan, "bm25", rewritten_query, self.settings.bm25_top_k, filters, actions, min_multiplier=True)
            self._upsert_task(plan, "page", rewritten_query, self.settings.page_top_k, filters, actions)

        if "numeric" in reasons or "missing_numeric" in reasons:
            self._upsert_task(plan, "table", rewritten_query, self.settings.table_top_k, filters, actions)
            self._upsert_task(plan, "bm25", rewritten_query, self.settings.bm25_top_k, filters, actions, min_multiplier=True)

        if "source" in reasons or "missing_source" in reasons:
            self._upsert_task(plan, "bm25", rewritten_query, self.settings.bm25_top_k, filters, actions, min_multiplier=True)

        if "page" in reasons or "missing_page" in reasons:
            self._upsert_task(plan, "page", rewritten_query, self.settings.page_top_k, filters, actions)
            self._increase_page_window(plan, actions)

        for slot, channel, top_k in (
            ("modality:table", "table", self.settings.table_top_k),
            ("modality:formula", "formula", self.settings.rrf_top_k),
            ("modality:image", "image", self.settings.rrf_top_k),
        ):
            if slot in reasons or f"missing_{slot}" in reasons:
                self._upsert_task(plan, channel, rewritten_query, top_k, filters, actions)

        if self._needs_relationship(state, reasons):
            self._upsert_task(plan, "relationship", rewritten_query, self.settings.rrf_top_k, filters, actions)
            self._increase_page_window(plan, actions)

        if not actions:
            self._increase_all_top_k(plan, actions)
        if not actions:
            actions.append("no_retry_plan_change")

        plan.retry_actions = _dedupe([*plan.retry_actions, *actions])
        evidence_gain = self._evidence_gain(state)
        plan.retry_history.append(
            {
                "retry_count": retry_count,
                "actions": list(actions),
                "reasons": sorted(reasons),
                "query_text": plan.query_text,
                "channels": list(plan.channels()),
                "top_k": {task.channel: task.top_k for task in plan.tasks},
                "page_window": plan.page_window,
                "evidence_gain": evidence_gain,
            }
        )
        return LocalRetryDecision(
            plan=plan,
            retry_count=retry_count,
            retry_actions=actions,
            rewritten_query_text=rewritten_query,
            evidence_gain=evidence_gain,
        )

    def _reason_set(self, state: dict[str, Any]) -> set[str]:
        raw: list[Any] = []
        for key in ("evidence_gaps", "missing_slots", "gate_reasons", "conflict_reasons"):
            raw.extend(state.get(key) or [])
        if state.get("conflict_level") in {"medium", "high"}:
            raw.append("possible_conflict")
        if state.get("need_cross_doc"):
            raw.append("need_cross_doc")
        if state.get("citation_ok") is False:
            raw.append("citation_failure")
        if float(state.get("support_score", 1.0) or 0.0) < self.settings.tg_min_support_score:
            raw.append("low_support_score")
        return {str(item) for item in raw if item}

    def _rewrite_query(self, *, base_query: str, missing_slots: list[str], reasons: set[str]) -> str:
        if not self.settings.tg_retry_rewrite_enabled:
            return base_query.strip() or base_query
        query = base_query.strip()
        query = re.sub(r"^(please\s+|what\s+is\s+)", "", query, flags=re.IGNORECASE)
        query = re.sub(r"^(请|帮我|麻烦|简单)?(介绍下|介绍一下|说明下|解释下)", "", query)
        query = re.sub(r"(是什么|是多少|多少|吗|呢|？|\?)$", "", query).strip()
        additions: list[str] = []
        reason_text = set(reasons) | set(missing_slots)
        if "numeric" in reason_text or "missing_numeric" in reason_text:
            additions.append("数值 百分比 统计 指标 value percent")
        if "modality:table" in reason_text or "missing_modality:table" in reason_text:
            additions.append("表格 table 指标 数值")
        if "modality:formula" in reason_text or "missing_modality:formula" in reason_text:
            additions.append("公式 latex equation formula")
        if "modality:image" in reason_text or "missing_modality:image" in reason_text:
            additions.append("图片 图像 图中 image figure")
        if "page" in reason_text or "missing_page" in reason_text:
            additions.append("第X页 page X 上下文")
        rewritten = " ".join(part for part in [query, *additions] if part).strip()
        return rewritten or base_query

    def _update_existing_queries(self, plan: RetrievalPlan, query_text: str, actions: list[str]) -> None:
        changed = False
        for task in plan.tasks:
            if task.query_text != query_text:
                task.query_text = query_text
                changed = True
        if changed:
            actions.append("rewrite_query")

    def _needs_global_top_k_increase(self, reasons: set[str], state: dict[str, Any]) -> bool:
        return bool(
            {"insufficient_hits", "missing_hits", "low_support_score", "citation_failure"} & reasons
            or state.get("citation_ok") is False
        )

    def _increase_all_top_k(self, plan: RetrievalPlan, actions: list[str]) -> None:
        changed = False
        for task in plan.tasks:
            new_top_k = self._scaled_top_k(task.top_k)
            if new_top_k > task.top_k:
                task.top_k = new_top_k
                changed = True
        if changed:
            actions.append("increase_top_k")

    def _upsert_task(
        self,
        plan: RetrievalPlan,
        channel: RetrievalChannel,
        query_text: str,
        top_k: int,
        filters: dict[str, Any],
        actions: list[str],
        *,
        min_multiplier: bool = False,
    ) -> None:
        existing = self._task_for(plan, channel)
        target_top_k = self._capped_top_k(top_k)
        if min_multiplier:
            target_top_k = self._scaled_top_k(target_top_k)
        if existing is None:
            plan.tasks.append(
                RetrievalTask(
                    channel=channel,
                    query_text=query_text,
                    top_k=target_top_k,
                    filters=dict(filters),
                    metadata={"retry": True},
                )
            )
            actions.append(f"add_{channel}")
            return
        changed = False
        if existing.query_text != query_text:
            existing.query_text = query_text
            changed = True
        if target_top_k > existing.top_k:
            existing.top_k = target_top_k
            changed = True
        merged_filters = dict(existing.filters or {})
        merged_filters.update(filters)
        if merged_filters != existing.filters:
            existing.filters = merged_filters
            changed = True
        if changed:
            actions.append(f"update_{channel}")

    def _increase_page_window(self, plan: RetrievalPlan, actions: list[str]) -> None:
        current = plan.page_window if plan.page_window is not None else self.settings.rel_expand_pages
        new_window = min(self.settings.tg_retry_max_page_window, current + self.settings.tg_retry_page_window_step)
        if plan.page_window != new_window:
            plan.page_window = new_window
            actions.append("expand_page_window")

    def _needs_relationship(self, state: dict[str, Any], reasons: set[str]) -> bool:
        return bool(
            state.get("need_cross_doc")
            or state.get("conflict_level") in {"medium", "high"}
            or "possible_conflict" in reasons
            or "numeric_value_conflict" in reasons
            or "cross_source_polarity_conflict" in reasons
            or bool(state.get("conflict_reasons") or [])
        )

    def _task_for(self, plan: RetrievalPlan, channel: RetrievalChannel) -> RetrievalTask | None:
        for task in plan.tasks:
            if task.channel == channel:
                return task
        return None

    def _scaled_top_k(self, current: int) -> int:
        return self._capped_top_k(max(current + 1, math.ceil(current * self.settings.tg_retry_top_k_multiplier)))

    def _capped_top_k(self, value: int) -> int:
        return max(1, min(self.settings.tg_retry_max_top_k, int(value)))

    def _evidence_gain(self, state: dict[str, Any]) -> float:
        old_ids = _hit_ids(state.get("expanded_hits") or [])
        new_ids = _hit_ids(state.get("fused_hits") or [])
        union = old_ids | new_ids
        if not union:
            return 0.0
        return len(new_ids - old_ids) / len(union)


def _hit_ids(hits: Iterable[SearchHit]) -> set[str]:
    return {hit.node_id or hit.point_id for hit in hits}


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
