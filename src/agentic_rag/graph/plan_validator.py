from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentic_rag.config import Settings
from agentic_rag.retrieval.retrieval_plan import RetrievalChannel, RetrievalPlan, RetrievalTask


ALLOWED_CHANNELS = {"vector", "bm25", "page", "table", "image", "formula", "relationship"}
ALLOWED_MODALITIES = {"text", "table", "page", "image", "formula"}
ALLOWED_ROUTES = {"text_first", "table_first", "page_first", "image_first", "formula_first", "cross_doc"}
ALLOWED_FILTERS = {"source", "doc_id", "page", "tags", "modality", "image_semantic_type"}


@dataclass(slots=True)
class RouteMergeResult:
    state: dict[str, Any]
    errors: list[str] = field(default_factory=list)
    used: bool = False


@dataclass(slots=True)
class PlanMergeResult:
    plan: RetrievalPlan
    errors: list[str] = field(default_factory=list)
    used: bool = False


class PlanValidator:
    """Validate and merge LLM advisory outputs into safe TaskGraph state."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def merge_agent_route(self, rule_state: dict[str, Any], agent_decision: Any) -> RouteMergeResult:
        errors: list[str] = []
        route = str(getattr(agent_decision, "route", ""))
        if route not in ALLOWED_ROUTES:
            errors.append(f"invalid_route:{route}")
        confidence = _as_float(getattr(agent_decision, "confidence", 0.0), 0.0)
        if confidence < self.settings.tg_agent_min_route_confidence:
            errors.append("low_route_confidence")
        modalities = [str(item) for item in getattr(agent_decision, "target_modalities", [])]
        invalid_modalities = [item for item in modalities if item not in ALLOWED_MODALITIES]
        if invalid_modalities:
            errors.append(f"invalid_modalities:{invalid_modalities}")
        if errors:
            return RouteMergeResult(state=dict(rule_state), errors=errors, used=False)

        merged = dict(rule_state)
        merged["intent"] = str(getattr(agent_decision, "intent", None) or merged.get("intent") or "general")
        merged["target_modalities"] = _dedupe([*(merged.get("target_modalities") or []), *modalities])
        merged["need_cross_doc"] = bool(merged.get("need_cross_doc", False) or getattr(agent_decision, "need_cross_doc", False))
        merged["need_page_level"] = bool(merged.get("need_page_level", False) or getattr(agent_decision, "need_page_level", False))
        merged["route"] = route
        return RouteMergeResult(state=merged, errors=[], used=True)

    def merge_agent_plan(self, rule_plan: RetrievalPlan, agent_decision: Any) -> PlanMergeResult:
        plan = rule_plan.model_copy(deep=True)
        errors: list[str] = []
        used = False
        for raw_task in list(getattr(agent_decision, "tasks", []) or []):
            channel = str(getattr(raw_task, "channel", ""))
            if channel not in ALLOWED_CHANNELS:
                errors.append(f"invalid_channel:{channel}")
                continue
            filters = dict(getattr(raw_task, "filters", {}) or {})
            invalid_filters = [key for key in filters if key not in ALLOWED_FILTERS]
            if invalid_filters:
                errors.append(f"invalid_filters:{invalid_filters}")
                continue
            query_text = str(getattr(raw_task, "query_text", "") or plan.query_text or plan.question)
            top_k = self._clamp_top_k(getattr(raw_task, "top_k", self.settings.rrf_top_k))
            self._upsert_task(plan, channel, query_text, top_k, filters)
            used = True
        page_window = getattr(agent_decision, "page_window", None)
        if page_window is not None:
            plan.page_window = self._clamp_page_window(page_window)
            used = True
        self._dedupe_tasks(plan)
        return PlanMergeResult(plan=plan, errors=errors, used=used)

    def merge_retry_advice(self, rule_plan: RetrievalPlan, advice: Any) -> PlanMergeResult:
        plan = rule_plan.model_copy(deep=True)
        errors: list[str] = []
        used = False
        filters = dict(getattr(advice, "filters", {}) or {})
        invalid_filters = [key for key in filters if key not in ALLOWED_FILTERS]
        if invalid_filters:
            return PlanMergeResult(plan=rule_plan, errors=[f"invalid_filters:{invalid_filters}"], used=False)
        query_text = str(getattr(advice, "rewritten_query_text", "") or plan.query_text or plan.question)
        if query_text:
            plan.query_text = query_text
            plan.rewritten_query_text = query_text
            for task in plan.tasks:
                task.query_text = query_text
            used = True
        for channel in list(getattr(advice, "add_channels", []) or []):
            channel = str(channel)
            if channel not in ALLOWED_CHANNELS:
                errors.append(f"invalid_channel:{channel}")
                continue
            self._upsert_task(plan, channel, query_text, self._default_top_k(channel), filters)
            used = True
        for channel in list(getattr(advice, "increase_top_k_channels", []) or []):
            channel = str(channel)
            if channel not in ALLOWED_CHANNELS:
                errors.append(f"invalid_top_k_channel:{channel}")
                continue
            task = self._task_for(plan, channel)
            if task is not None:
                task.top_k = self._clamp_top_k(max(task.top_k + 1, int(task.top_k * self.settings.tg_retry_top_k_multiplier)))
                used = True
        page_window = getattr(advice, "page_window", None)
        if page_window is not None:
            plan.page_window = self._clamp_page_window(page_window)
            used = True
        self._dedupe_tasks(plan)
        return PlanMergeResult(plan=plan, errors=errors, used=used)

    def _upsert_task(
        self,
        plan: RetrievalPlan,
        channel: str,
        query_text: str,
        top_k: int,
        filters: dict[str, Any],
    ) -> None:
        task = self._task_for(plan, channel)
        if task is None:
            plan.tasks.append(
                RetrievalTask(
                    channel=channel,  # type: ignore[arg-type]
                    query_text=query_text,
                    top_k=self._clamp_top_k(top_k),
                    filters=dict(filters),
                    metadata={"agent": True},
                )
            )
            return
        task.query_text = query_text or task.query_text
        task.top_k = max(task.top_k, self._clamp_top_k(top_k))
        merged_filters = dict(task.filters or {})
        merged_filters.update(filters)
        task.filters = merged_filters

    def _task_for(self, plan: RetrievalPlan, channel: str) -> RetrievalTask | None:
        for task in plan.tasks:
            if task.channel == channel:
                return task
        return None

    def _dedupe_tasks(self, plan: RetrievalPlan) -> None:
        seen: set[str] = set()
        tasks: list[RetrievalTask] = []
        for task in plan.tasks:
            if task.channel in seen:
                continue
            seen.add(task.channel)
            tasks.append(task)
        plan.tasks = tasks

    def _default_top_k(self, channel: str) -> int:
        if channel == "vector":
            return self.settings.retrieval_top_k
        if channel == "bm25":
            return self.settings.bm25_top_k
        if channel == "page":
            return self.settings.page_top_k
        if channel == "table":
            return self.settings.table_top_k
        return self.settings.rrf_top_k

    def _clamp_top_k(self, value: Any) -> int:
        return max(1, min(self.settings.tg_retry_max_top_k, int(value)))

    def _clamp_page_window(self, value: Any) -> int:
        return max(1, min(self.settings.tg_retry_max_page_window, int(value)))


def validate_route_decision(rule_state: dict[str, Any], agent_decision: Any, settings: Settings) -> RouteMergeResult:
    return PlanValidator(settings).merge_agent_route(rule_state, agent_decision)


def validate_retrieval_plan(rule_plan: RetrievalPlan, agent_decision: Any, settings: Settings) -> PlanMergeResult:
    return PlanValidator(settings).merge_agent_plan(rule_plan, agent_decision)


def validate_retry_advice(rule_plan: RetrievalPlan, advice: Any, settings: Settings) -> PlanMergeResult:
    return PlanValidator(settings).merge_retry_advice(rule_plan, advice)


def _dedupe(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value)
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
