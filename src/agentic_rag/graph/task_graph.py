from __future__ import annotations

import re
import time
from typing import Any, cast

from langgraph.graph import END, StateGraph

from agentic_rag.config import Settings
from agentic_rag.evaluation.retrieval_history import (
    RetrievalHistoryRecorder,
    build_query_record,
    build_snapshot,
    new_query_id,
)
from agentic_rag.evaluation.retrieval_visualization import write_retrieval_visualization_report
from agentic_rag.graph.agent_chunk_grader import AgentChunkGrader
from agentic_rag.graph.agent_evidence import AgentEvidenceCritic, merge_evidence_gate
from agentic_rag.graph.agent_planner import AgentRetrievalPlanner, AgentRouteAnalyzer
from agentic_rag.graph.agent_retry import AgentRetryAdvisor
from agentic_rag.generation.prompt_builder import PromptBuilder
from agentic_rag.graph.local_retry import LocalRetryPlanner
from agentic_rag.graph.plan_validator import PlanValidator
from agentic_rag.graph.task_graph_prep import TaskGraphState
from agentic_rag.models.providers import EmbeddingProvider, LLMClient
from agentic_rag.observability.stage_logger import StageLogger, StageTimer
from agentic_rag.retrieval.evidence_gate import EvidenceEvaluator
from agentic_rag.retrieval.evidence_pack import EvidencePack
from agentic_rag.retrieval.rerank import RerankService
from agentic_rag.retrieval.retrieval_plan import RetrievalChannel, RetrievalPlan, RetrievalTask
from agentic_rag.retrieval.retriever import MultiChannelRetriever
from agentic_rag.retrieval.scoring import STAGE_FINAL, compute_composite_scores, filter_by_stage_threshold_with_removed, mark_removed_hit, score_value
from agentic_rag.schemas import Citation, RAGResult, SearchHit


def _now_ms() -> int:
    return int(time.time() * 1000)


def _keyword_tokens(text: str) -> list[str]:
    return [x.lower() for x in re.findall(r"[\u4e00-\u9fff]|[a-zA-Z0-9_]+", text or "")]


def _hit_evidence_text(hit: SearchHit) -> str:
    parts = [hit.text or ""]
    if hit.formula_latex:
        parts.append(hit.formula_latex)
    if hit.table_markdown:
        parts.append(hit.table_markdown)
    return " ".join(parts)


def _count_by_key(hits: list[SearchHit], key_fn) -> dict[str, int]:
    counts: dict[str, int] = {}
    for hit in hits:
        raw = key_fn(hit)
        if raw is None or raw == "":
            continue
        key = str(raw)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _count_removed_reasons(hits: list[SearchHit]) -> dict[str, int]:
    return _count_by_key(hits, lambda hit: (hit.metadata or {}).get("removed_reason") or "unknown_removed")


def _replace_snapshot(snapshots: list[dict[str, Any]], snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    stage = snapshot.get("stage")
    return [row for row in snapshots if row.get("stage") != stage] + [snapshot]


def _dedupe_hits(hits: list[SearchHit]) -> list[SearchHit]:
    result: list[SearchHit] = []
    seen: set[str] = set()
    for hit in hits:
        key = _hit_key(hit)
        if key in seen:
            continue
        seen.add(key)
        result.append(hit)
    return result


def _hit_key(hit: SearchHit) -> str:
    return hit.node_id or hit.point_id or f"{hit.doc_id}:{hit.metadata.get('chunk_index')}"


def _agent_label_rank(label: str | None) -> int:
    ranks = {"irrelevant": 0, "weak": 1, "relevant": 2, "strong": 3}
    return ranks.get(str(label or "").strip().lower(), -1)


def _merge_removed_hits(*groups: list[SearchHit]) -> list[SearchHit]:
    merged: list[SearchHit] = []
    for group in groups:
        merged.extend(group or [])
    return merged


def _retrieval_observability_enabled(settings: Settings) -> bool:
    return bool(
        settings.retrieval_eval_log_enabled
        or settings.retrieval_eval_snapshots_enabled
        or settings.retrieval_vis_auto_write
    )


def _contains_any_keyword(text: str, keywords: str) -> bool:
    raw = (text or "").lower()
    for keyword in (part.strip().lower() for part in (keywords or "").split(",")):
        if keyword and keyword in raw:
            return True
    return False


def _add_retrieval_task_if_missing(
    tasks: list[RetrievalTask],
    *,
    channel: RetrievalChannel,
    query_text: str,
    top_k: int,
    filters: dict[str, object],
    metadata: dict[str, object] | None = None,
) -> None:
    for task in tasks:
        if task.channel == channel:
            if metadata:
                merged = dict(task.metadata or {})
                merged.update(metadata)
                task.metadata = merged
            return
    tasks.append(
        RetrievalTask(
            channel=channel,
            query_text=query_text,
            top_k=top_k,
            filters=filters,
            metadata=metadata or {},
        )
    )


class TaskGraphRAG:
    """TaskGraph-based constrained Agentic RAG pipeline."""

    def __init__(
        self,
        *,
        settings: Settings,
        embedding_provider: EmbeddingProvider,
        retriever: MultiChannelRetriever,
        llm_client: LLMClient,
        prompt_builder: PromptBuilder,
        rerank_service: RerankService | None = None,
        stage_logger: StageLogger | None = None,
        progress: Any | None = None,
    ):
        self.settings = settings
        self.embedding_provider = embedding_provider
        self.retriever = retriever
        self.llm_client = llm_client
        self.prompt_builder = prompt_builder
        self.rerank_service = rerank_service
        self.stage_logger = stage_logger
        self.progress = progress
        self.evidence_evaluator = EvidenceEvaluator(settings)
        self.local_retry_planner = LocalRetryPlanner(settings)
        self.agent_chunk_grader = AgentChunkGrader(settings, llm_client)
        self.plan_validator = PlanValidator(settings)
        self.agent_route_analyzer = AgentRouteAnalyzer(settings, llm_client)
        self.agent_retrieval_planner = AgentRetrievalPlanner(settings, llm_client)
        self.agent_evidence_critic = AgentEvidenceCritic(settings, llm_client)
        self.agent_retry_advisor = AgentRetryAdvisor(settings, llm_client)
        self.retrieval_history_recorder = RetrievalHistoryRecorder(settings)
        self.graph = self._compile_graph()

    def _log_start(self, stage: str, **fields: Any) -> StageTimer:
        timer = StageTimer.start_now()
        if self.stage_logger:
            self.stage_logger.log_stage_start(stage, **fields)
        return timer

    def _log_end(self, stage: str, timer: StageTimer, **fields: Any) -> None:
        if self.stage_logger:
            self.stage_logger.log_stage_end(stage, latency_ms=timer.elapsed_ms(), **fields)

    def _progress_start(self, stage: str) -> None:
        if self.progress:
            self.progress.start(stage)

    def _progress_done(self, stage: str) -> None:
        if self.progress:
            self.progress.done(stage)

    def _progress_skip(self, stage: str) -> None:
        if self.progress:
            self.progress.skip(stage)

    def _progress_retry(self, stage: str, retry_count: int) -> None:
        if self.progress:
            self.progress.retry(stage, retry_count)

    def _progress_fail(self, stage: str, reason: str) -> None:
        if self.progress:
            self.progress.fail(stage, reason)

    def _evidence_gate_enabled(self) -> bool:
        return bool(getattr(self.settings, "taskgraph_evidence_gate_enabled", True))

    def _local_retry_enabled(self) -> bool:
        return bool(getattr(self.settings, "taskgraph_local_retry_enabled", True))

    def _should_skip_evidence_gate(self, state: TaskGraphState) -> bool:
        return not self._evidence_gate_enabled()

    def _should_skip_local_retry(self, state: TaskGraphState) -> bool:
        return not self._local_retry_enabled()

    def _skipped_evidence_state(self) -> TaskGraphState:
        return {
            "evidence_ok": True,
            "evidence_gaps": [],
            "refusal": False,
            "refusal_reason": None,
            "claim_supported": True,
            "source_coverage": {},
            "page_coverage": {},
            "modality_coverage": {},
            "conflict_level": "none",
            "missing_slots": [],
            "supporting_hit_ids": [],
            "support_level": "skipped",
            "support_score": 0.0,
            "support_features": {},
            "support_feature_weights": {},
            "support_feature_contributions": {},
            "support_raw_features": {},
            "support_normalized_features": {},
            "rerank_available": False,
            "slot_coverage": {},
            "required_slots": [],
            "covered_slots": [],
            "conflict_reasons": [],
            "gate_decision": "skipped",
            "gate_reasons": [],
            "agent_evidence_used": False,
            "agent_evidence_reasoning": "",
            "agent_gate_decision": None,
            "unsupported_claims": [],
            "evidence_gate_enabled": False,
            "evidence_gate_skipped": True,
        }

    def _estimate_token_usage(self, state: TaskGraphState) -> int:
        question = state.get("question", "") or ""
        hits = state.get("expanded_hits", []) or []
        text_chars = len(question)
        for hit in hits[: self.settings.context_top_n]:
            text_chars += len(hit.text or "")
            if hit.table_markdown:
                text_chars += len(hit.table_markdown)
        # lightweight approximation: ~4 chars/token for mixed CJK+EN corpora.
        return max(1, text_chars // 4)

    def _question_analyze_node(self, state: TaskGraphState) -> TaskGraphState:
        self._progress_start("question_analyze")
        timer = self._log_start("query_analyze", query_text=state.get("question", ""))
        question = (state.get("question") or "").strip()
        lower = question.lower()
        intent = "general"
        target_modalities = ["text"]

        cross_doc_tokens = (
            "\u5bf9\u6bd4",
            "\u5dee\u5f02",
            "\u591a\u4e2a\u6587\u6863",
            "\u4e0d\u540c\u6587\u6863",
        )
        page_tokens = (
            "\u7b2c",
            "\u9875",
            "\u56fe\u4e2d",
            "\u8be5\u9875",
            "\u53f3\u4e0a\u89d2",
            "\u5de6\u4e0b\u89d2",
        )
        table_tokens = (
            "\u8868",
            "\u7edf\u8ba1",
            "\u589e\u957f",
            "\u4e0b\u964d",
            "\u6392\u540d",
            "\u767e\u5206\u6bd4",
            "\u591a\u5c11",
        )
        image_tokens = (
            "\u56fe\u7247",
            "\u56fe\u50cf",
            "\u7167\u7247",
            "\u622a\u56fe",
            "\u56fe\u4e2d",
            "\u56fe ",
            "\u56fe1",
            "\u56fe2",
            "\u56fe3",
            "\u793a\u610f\u56fe",
            "\u8d8b\u52bf\u56fe",
            "\u89c6\u89c9",
            "figure",
            "fig.",
            "chart",
            "plot",
            "graph",
            "curve",
            "caption",
            "panel",
            "subplot",
            "legend",
            "axis",
            "screenshot",
            "diagram",
            "photo",
        )
        formula_tokens = (
            "\u516c\u5f0f",
            "\u65b9\u7a0b",
            "\u8868\u8fbe\u5f0f",
            "latex",
            "equation",
            "formula",
            "ratio",
            "sigma",
            "theta",
            "tau",
        )

        need_cross_doc = any(k in question for k in cross_doc_tokens) or "compare" in lower
        need_page_level = any(k in question for k in page_tokens)
        if any(k in question for k in table_tokens):
            intent = "table_qa"
            target_modalities = ["table", "text"]
        elif any(k in question for k in image_tokens):
            intent = "image_qa"
            target_modalities = ["image", "text"]
        elif any(k in lower for k in formula_tokens):
            intent = "formula_qa"
            target_modalities = ["formula", "text"]
        elif need_page_level:
            intent = "page_qa"
            target_modalities = ["page", "text"]

        route = (
            "table_first"
            if "table" in target_modalities
            else "image_first"
            if "image" in target_modalities
            else "page_first"
            if "page" in target_modalities
            else "text_first"
        )
        result: TaskGraphState = {
            "intent": intent,
            "target_modalities": target_modalities,
            "need_cross_doc": need_cross_doc,
            "need_page_level": need_page_level,
            "route": route,
            "retry_count": state.get("retry_count", 0),
            "max_retries": self.settings.tg_max_retries,
            "budget_tokens": self.settings.tg_budget_tokens,
            "budget_ms": self.settings.tg_budget_ms,
            "started_at_ms": state.get("started_at_ms", _now_ms()),
            "agent_route_used": False,
            "agent_route_confidence": 0.0,
            "agent_route_reasoning": "",
            "agent_fallback_reason": state.get("agent_fallback_reason"),
            "plan_validation_errors": list(state.get("plan_validation_errors", []) or []),
        }
        if self.settings.tg_agent_route_enabled or self.settings.tg_route_llm_enabled:
            try:
                decision = self.agent_route_analyzer.analyze(
                    question=question,
                    filters=state.get("filters"),
                    rule_state=cast(dict[str, Any], result),
                )
                merge = self.plan_validator.merge_agent_route(cast(dict[str, Any], result), decision)
                result.update(cast(TaskGraphState, merge.state))
                result["agent_route_used"] = merge.used
                result["agent_route_confidence"] = decision.confidence
                result["agent_route_reasoning"] = decision.reasoning_summary
                result["plan_validation_errors"] = [*result.get("plan_validation_errors", []), *merge.errors]
                if not merge.used and merge.errors:
                    result["agent_fallback_reason"] = ",".join(merge.errors)
            except Exception as exc:
                result["agent_fallback_reason"] = f"agent_route_failed:{type(exc).__name__}"
                if not self.settings.tg_agent_fallback_to_rules:
                    raise

        self._log_end(
            "query_analyze",
            timer,
            route=result.get("route", route),
            query_text=question,
            need_cross_doc=result.get("need_cross_doc", need_cross_doc),
            need_page_level=result.get("need_page_level", need_page_level),
            agent_route_used=result.get("agent_route_used", False),
        )
        self._progress_done("question_analyze")
        return result

    def _task_router_node(self, state: TaskGraphState) -> TaskGraphState:
        self._progress_start("task_router")
        timer = self._log_start("task_route", route=state.get("route", ""))
        question = state.get("question", "")
        filters = cast(dict[str, object], state.get("filters") or {})
        route = state.get("route", "text_first")
        target = state.get("target_modalities", ["text"])
        tasks: list[RetrievalTask] = [
            RetrievalTask(
                channel="vector",
                query_text=question,
                top_k=self.settings.retrieval_top_k,
                filters=filters,
            )
        ]
        if self.settings.bm25_enabled:
            tasks.append(
                RetrievalTask(
                    channel="bm25",
                    query_text=question,
                    top_k=self.settings.bm25_top_k,
                    filters=filters,
                )
            )
        if "page" in target or state.get("need_page_level"):
            _add_retrieval_task_if_missing(
                tasks,
                channel="page",
                query_text=question,
                top_k=self.settings.page_top_k,
                filters=filters,
            )
        if "table" in target:
            _add_retrieval_task_if_missing(
                tasks,
                channel="table",
                query_text=question,
                top_k=self.settings.table_top_k,
                filters=filters,
            )
        if "image" in target:
            _add_retrieval_task_if_missing(
                tasks,
                channel="image",
                query_text=question,
                top_k=self.settings.rrf_top_k,
                filters=filters,
            )
        if "formula" in target:
            _add_retrieval_task_if_missing(
                tasks,
                channel="formula",
                query_text=question,
                top_k=self.settings.rrf_top_k,
                filters=filters,
            )
        if self.settings.retrieval_auto_table_channel_enabled and _contains_any_keyword(
            question, self.settings.retrieval_table_trigger_keywords
        ):
            _add_retrieval_task_if_missing(
                tasks,
                channel="table",
                query_text=question,
                top_k=self.settings.table_top_k,
                filters=filters,
            )
        if self.settings.retrieval_auto_formula_channel_enabled and _contains_any_keyword(
            question, self.settings.retrieval_formula_trigger_keywords
        ):
            _add_retrieval_task_if_missing(
                tasks,
                channel="formula",
                query_text=question,
                top_k=self.settings.rrf_top_k,
                filters=filters,
            )
        if self.settings.retrieval_auto_image_channel_enabled and _contains_any_keyword(
            question, self.settings.retrieval_image_trigger_keywords
        ):
            _add_retrieval_task_if_missing(
                tasks,
                channel="image",
                query_text=question,
                top_k=self.settings.rrf_top_k,
                filters=filters,
            )
        if state.get("need_page_level") or state.get("need_cross_doc"):
            reason = "need_cross_doc" if state.get("need_cross_doc") else "need_page_level"
            _add_retrieval_task_if_missing(
                tasks,
                channel="relationship",
                query_text=question,
                top_k=self.settings.rrf_top_k,
                filters=filters,
                metadata={"expansion_mode": "routed", "expansion_reason": reason},
            )
        plan = RetrievalPlan(
            question=question,
            intent=state.get("intent", "general"),
            target_modalities=target,
            need_cross_doc=bool(state.get("need_cross_doc", False)),
            need_page_level=bool(state.get("need_page_level", False)),
            route=route,
            retry_count=state.get("retry_count", 0),
            max_retries=state.get("max_retries", self.settings.tg_max_retries),
            budget_tokens=state.get("budget_tokens", self.settings.tg_budget_tokens),
            budget_ms=state.get("budget_ms", self.settings.tg_budget_ms),
            original_query=question,
            query_text=question,
            tasks=tasks,
        )
        agent_plan_used = False
        agent_plan_reasoning = ""
        plan_validation_errors = list(state.get("plan_validation_errors", []) or [])
        agent_fallback_reason = state.get("agent_fallback_reason")
        if self.settings.tg_agent_retrieval_planner_enabled:
            try:
                decision = self.agent_retrieval_planner.plan(
                    question=question,
                    filters=filters,
                    state=cast(dict[str, Any], state),
                    rule_plan=plan,
                )
                merge = self.plan_validator.merge_agent_plan(plan, decision)
                plan = merge.plan
                agent_plan_used = merge.used
                agent_plan_reasoning = decision.reasoning_summary
                plan_validation_errors.extend(merge.errors)
                if not merge.used and merge.errors:
                    agent_fallback_reason = ",".join(merge.errors)
            except Exception as exc:
                agent_fallback_reason = f"agent_plan_failed:{type(exc).__name__}"
                if not self.settings.tg_agent_fallback_to_rules:
                    raise
        self._log_end("task_route", timer, query_plan=plan.model_dump())
        self._progress_done("task_router")
        return {
            "retrieval_plan": plan.model_dump(),
            "agent_plan_used": agent_plan_used,
            "agent_plan_reasoning": agent_plan_reasoning,
            "plan_validation_errors": plan_validation_errors,
            "agent_fallback_reason": agent_fallback_reason,
        }

    def _embed_query_node(self, state: TaskGraphState) -> TaskGraphState:
        question = state.get("question", "")
        vectors = self.embedding_provider.embed_texts([question])
        if not vectors:
            raise RuntimeError("TaskGraph embed_query returned empty vectors")
        return {"query_vector": vectors[0]}

    def _retrieve_fanout_node(self, state: TaskGraphState) -> TaskGraphState:
        self._progress_start("retrieval")
        timer = self._log_start("retrieve_fanout", query_text=state.get("question", ""))
        question = state.get("question", "")
        query_vector = state.get("query_vector") or []
        filters = state.get("filters")
        plan_raw = state.get("retrieval_plan")
        plan = RetrievalPlan.model_validate(plan_raw) if isinstance(plan_raw, dict) else plan_raw
        result = self.retriever.retrieve(
            query_text=question,
            query_vector=query_vector,
            filters=filters,
            plan=plan,
        )
        self._log_end(
            "retrieve_fanout",
            timer,
            evidence_count=sum(len(v) for v in result.route_hits.values()),
        )
        snapshots = list(state.get("retrieval_eval_snapshots", []) or [])
        retry_count = int(state.get("retry_count", 0) or 0)
        snapshot_stage = f"retry_{retry_count}_retrieval" if retry_count > 0 else "initial_retrieval"
        expanded_snapshot_stage = f"retry_{retry_count}_expanded" if retry_count > 0 else "initial_expanded"
        drop_cache = dict(state.get("agent_chunk_drop_cache", {}) or {})
        route_hits = {channel: list(hits) for channel, hits in result.route_hits.items()}
        fused_hits = list(result.hits)
        expanded_hits = list(result.expanded_hits)
        retrieval_drop_removed: list[SearchHit] = []
        expanded_drop_removed: list[SearchHit] = []
        if retry_count > 0 and drop_cache:
            fused_hits, retrieval_drop_removed = self._filter_agent_drop_cached_hits(
                fused_hits,
                drop_cache=drop_cache,
                stage=snapshot_stage,
            )
            expanded_hits, expanded_drop_removed = self._filter_agent_drop_cached_hits(
                expanded_hits,
                drop_cache=drop_cache,
                stage=expanded_snapshot_stage,
            )
            route_hits = {
                channel: self._filter_agent_drop_cached_hits(
                    list(hits),
                    drop_cache=drop_cache,
                    stage=snapshot_stage,
                )[0]
                for channel, hits in route_hits.items()
            }
        carry_forward_added_count = 0
        carry_forward_matched_count = 0
        if retry_count > 0:
            expanded_hits, carry_stats = self._merge_retry_carry_forward_hits(
                expanded_hits,
                list(state.get("retry_carry_forward_hits", []) or []),
                retry_count=retry_count,
            )
            carry_forward_added_count = carry_stats["added"]
            carry_forward_matched_count = carry_stats["matched"]
        if _retrieval_observability_enabled(self.settings) and (
            retry_count > 0 or not any(s.get("stage") == "initial_retrieval" for s in snapshots)
        ):
            removed_by_stage = getattr(result, "removed_hits_by_stage", None) or {}
            initial_removed = removed_by_stage.get(snapshot_stage) or removed_by_stage.get("initial_retrieval", [])
            snapshots.append(
                build_snapshot(
                    stage=snapshot_stage,
                    query_text=state.get("rewritten_query_text") or question,
                    hits=fused_hits,
                    removed_hits=_merge_removed_hits(initial_removed, retrieval_drop_removed),
                    max_text_chars=self.settings.retrieval_eval_max_text_chars,
                    used_rerank=False,
                )
            )
            snapshots = _replace_snapshot(
                snapshots,
                build_snapshot(
                    stage=expanded_snapshot_stage,
                    query_text=state.get("rewritten_query_text") or question,
                    hits=expanded_hits,
                    removed_hits=_merge_removed_hits(removed_by_stage.get(expanded_snapshot_stage, []), expanded_drop_removed),
                    max_text_chars=self.settings.retrieval_eval_max_text_chars,
                    used_rerank=False,
                ),
            )
        return {
            "route_hits": route_hits,
            "fused_hits": fused_hits,
            "expanded_hits": expanded_hits,
            "evidence_gain": state.get("evidence_gain", 1.0),
            "executed_channels": result.executed_channels,
            "removed_hits_by_stage": getattr(result, "removed_hits_by_stage", None) or {},
            "retry_carry_forward_added_count": carry_forward_added_count,
            "retry_carry_forward_matched_count": carry_forward_matched_count,
            "retrieval_eval_snapshots": snapshots,
        }

    def _retrieve_rrf_node(self, state: TaskGraphState) -> TaskGraphState:
        timer = self._log_start("retrieve_rrf")
        fused = state.get("fused_hits", [])
        self._log_end("retrieve_rrf", timer, evidence_count=len(fused))
        return {"fused_hits": fused}

    def _relationship_expand_node(self, state: TaskGraphState) -> TaskGraphState:
        expanded = state.get("expanded_hits", [])
        snapshots = list(state.get("retrieval_eval_snapshots", []) or [])
        return {"expanded_hits": expanded, "retrieval_eval_snapshots": snapshots}

    def _rerank_node(self, state: TaskGraphState) -> TaskGraphState:
        hits = state.get("expanded_hits", [])
        if not self.settings.rerank_enabled or self.rerank_service is None:
            skipped_evidence = self._skipped_evidence_state() if self._should_skip_evidence_gate(state) else {}
            if skipped_evidence:
                self._progress_skip("evidence_gate")
            self._progress_done("retrieval")
            snapshots = list(state.get("retrieval_eval_snapshots", []) or [])
            scoped_hits = hits[: self.settings.context_top_n]
            removed_hits = [
                mark_removed_hit(
                    hit.model_copy(deep=True),
                    stage=f"retry_{int(state.get('retry_count', 0) or 0)}_rerank"
                    if int(state.get("retry_count", 0) or 0) > 0
                    else "rerank",
                    reason="context_top_n_limit",
                    detail=f"rank={rank} > context_top_n={self.settings.context_top_n}",
                    previous_rank=rank,
                    extra={
                        "retrieval_removed_by": "context_top_n",
                        "retrieval_removed_limit": self.settings.context_top_n,
                        "retrieval_previous_rank": rank,
                    },
                )
                for rank, hit in enumerate(hits[self.settings.context_top_n :], start=self.settings.context_top_n + 1)
            ]
            if _retrieval_observability_enabled(self.settings):
                retry_count = int(state.get("retry_count", 0) or 0)
                snapshots = _replace_snapshot(
                    snapshots,
                    build_snapshot(
                        stage=f"retry_{retry_count}_rerank" if retry_count > 0 else "rerank",
                        query_text=state.get("question", ""),
                        hits=scoped_hits,
                        removed_hits=removed_hits,
                        max_text_chars=self.settings.retrieval_eval_max_text_chars,
                        used_rerank=False,
                        rerank_fallback_reason=None if not self.settings.rerank_enabled else "rerank_service_unavailable",
                    ),
                )
            return {
                "reranked_hits": scoped_hits,
                "expanded_hits": scoped_hits,
                "rerank_removed_hits": removed_hits,
                "used_rerank": False,
                "rerank_fallback_reason": None if not self.settings.rerank_enabled else "rerank_service_unavailable",
                "rerank_score_top": None,
                "rerank_hit_count": 0,
                "retrieval_eval_snapshots": snapshots,
                **skipped_evidence,
            }
        result = self.rerank_service.rerank(state.get("question", ""), hits)
        reranked = result.hits
        skipped_evidence = self._skipped_evidence_state() if self._should_skip_evidence_gate(state) else {}
        if skipped_evidence:
            self._progress_skip("evidence_gate")
        self._progress_done("retrieval")
        top_score = None
        if reranked:
            raw_score = reranked[0].metadata.get("rerank_score", reranked[0].score)
            top_score = float(raw_score) if isinstance(raw_score, (int, float)) else None
        retry_count = int(state.get("retry_count", 0) or 0)
        return {
            "reranked_hits": reranked,
            "expanded_hits": reranked,
            "used_rerank": result.used_rerank,
            "rerank_fallback_reason": result.fallback_reason,
            "rerank_score_top": top_score,
            "rerank_hit_count": len(reranked) if result.used_rerank else 0,
            "retrieval_eval_snapshots": _replace_snapshot(
                list(state.get("retrieval_eval_snapshots", []) or []),
                build_snapshot(
                    stage=f"retry_{retry_count}_rerank" if retry_count > 0 else "rerank",
                    query_text=state.get("question", ""),
                    hits=reranked,
                    removed_hits=result.removed_hits,
                    max_text_chars=self.settings.retrieval_eval_max_text_chars,
                    used_rerank=result.used_rerank,
                    rerank_fallback_reason=result.fallback_reason,
                ),
            )
            if _retrieval_observability_enabled(self.settings)
            else state.get("retrieval_eval_snapshots", []),
            **skipped_evidence,
            "rerank_removed_hits": result.removed_hits,
        }

    def _agent_chunk_grader_node(self, state: TaskGraphState) -> TaskGraphState:
        hits = list(state.get("expanded_hits", []) or [])
        if not self.settings.tg_agent_chunk_grading_enabled or not hits:
            return {"expanded_hits": hits, "agent_chunk_grading_used": False}
        context_pool = _dedupe_hits(
            [
                *hits,
                *(state.get("fused_hits", []) or []),
                *[
                    hit
                    for route in (state.get("route_hits", {}) or {}).values()
                    for hit in route
                ],
            ]
        )
        try:
            grade_cache = dict(state.get("agent_chunk_grade_cache", {}) or {})
            grading = self.agent_chunk_grader.grade_hits_with_removed(
                question=state.get("question", ""),
                hits=hits,
                context_pool=context_pool,
                grade_cache=grade_cache,
            )
            graded = grading.hits
            updated_grade_cache = grading.grade_cache if self.settings.tg_agent_chunk_grade_cache_enabled else grade_cache
            drop_cache = dict(state.get("agent_chunk_drop_cache", {}) or {})
            for kept_hit in graded:
                drop_cache.pop(_hit_key(kept_hit), None)
            for removed_hit in grading.removed_hits:
                key = _hit_key(removed_hit)
                drop_cache[key] = {
                    "agent_relevance_label": removed_hit.metadata.get("agent_relevance_label"),
                    "agent_relevance_score": removed_hit.metadata.get("agent_relevance_score"),
                    "agent_relevance_drop_reason": removed_hit.metadata.get("agent_relevance_drop_reason"),
                    "agent_relevance_drop_threshold": removed_hit.metadata.get("agent_relevance_drop_threshold"),
                    "removed_reason": removed_hit.metadata.get("removed_reason"),
                    "removed_reason_detail": removed_hit.metadata.get("removed_reason_detail"),
                }
            snapshots = list(state.get("retrieval_eval_snapshots", []) or [])
            if _retrieval_observability_enabled(self.settings):
                snapshots = _replace_snapshot(
                    snapshots,
                    build_snapshot(
                        stage="agent_chunk_grading",
                        query_text=state.get("question", ""),
                        hits=graded,
                        removed_hits=grading.removed_hits,
                        max_text_chars=self.settings.retrieval_eval_max_text_chars,
                        used_rerank=bool(state.get("used_rerank", False)),
                        rerank_fallback_reason=state.get("rerank_fallback_reason"),
                    ),
                )
            return {
                "expanded_hits": graded,
                "agent_chunk_removed_hits": grading.removed_hits,
                "agent_chunk_grading_used": True,
                "agent_chunk_grading_hit_count": len(graded),
                "agent_chunk_removed_hit_count": len(grading.removed_hits),
                "agent_chunk_removed_reasons": _count_removed_reasons(grading.removed_hits),
                "agent_chunk_grade_cache": updated_grade_cache,
                "agent_chunk_drop_cache": drop_cache,
                "agent_chunk_drop_cache_size": len(drop_cache),
                "agent_chunk_grade_cache_size": len(updated_grade_cache),
                "agent_chunk_grade_cache_hits": grading.cache_hits,
                "agent_chunk_llm_graded_hit_count": grading.llm_graded_hits,
                "retrieval_eval_snapshots": snapshots,
            }
        except Exception as exc:
            if not self.settings.tg_agent_fallback_to_rules:
                raise
            return {
                "expanded_hits": hits,
                "agent_chunk_grading_used": False,
                "agent_fallback_reason": f"agent_chunk_grading_failed:{type(exc).__name__}",
            }

    def _evidence_gate_node(self, state: TaskGraphState) -> TaskGraphState:
        if self._should_skip_evidence_gate(state):
            self._progress_skip("evidence_gate")
            return self._skipped_evidence_state()
        self._progress_start("evidence_gate")
        timer = self._log_start("evidence_gate")
        hits = state.get("expanded_hits", [])
        plan_raw = state.get("retrieval_plan") or {}
        plan = RetrievalPlan.model_validate(plan_raw) if isinstance(plan_raw, dict) else plan_raw
        pack = self.evidence_evaluator.evaluate(
            question=state.get("question", ""),
            hits=hits,
            route_hits=state.get("route_hits", {}),
            target_modalities=state.get("target_modalities", []),
            plan=plan,
            filters=state.get("filters"),
        )
        agent_evidence_used = bool(state.get("agent_evidence_used", False))
        agent_evidence_reasoning = str(state.get("agent_evidence_reasoning", "") or "")
        agent_gate_decision = state.get("agent_gate_decision")
        unsupported_claims: list[str] = list(state.get("unsupported_claims", []) or [])
        agent_fallback_reason = state.get("agent_fallback_reason")
        if self.settings.tg_agent_evidence_critic_enabled:
            try:
                critique = self.agent_evidence_critic.critique(
                    question=state.get("question", ""),
                    pack=pack,
                    route_hits=state.get("route_hits", {}),
                )
                pack = merge_evidence_gate(pack, critique, self.settings)
                agent_evidence_used = True
                agent_evidence_reasoning = critique.reasoning_summary
                agent_gate_decision = critique.gate_decision
                unsupported_claims = list(critique.unsupported_claims)
            except Exception as exc:
                agent_fallback_reason = f"agent_evidence_failed:{type(exc).__name__}"
                if not self.settings.tg_agent_fallback_to_rules:
                    raise
        evidence_ok = pack.gate_decision == "pass"
        refusal = pack.gate_decision == "refuse"
        refusal_reason = "evidence_conflict" if refusal and pack.conflict_level == "high" else None
        self._log_end(
            "evidence_gate",
            timer,
            evidence_count=len(hits),
            fallback=not evidence_ok,
            error_msg=",".join(pack.gate_reasons),
            support_level=pack.support_level,
            support_score=pack.support_score,
            gate_decision=pack.gate_decision,
            conflict_level=pack.conflict_level,
            missing_slots=",".join(pack.missing_slots),
            agent_evidence_used=agent_evidence_used,
            agent_gate_decision=agent_gate_decision,
        )
        self._progress_done("evidence_gate")
        payload = {
            "evidence_pack": pack.model_dump(),
            "evidence_ok": evidence_ok,
            "evidence_gaps": pack.evidence_gaps,
            "refusal": refusal,
            "refusal_reason": refusal_reason,
            "claim_supported": pack.claim_supported,
            "source_coverage": pack.source_coverage,
            "page_coverage": pack.page_coverage,
            "modality_coverage": pack.modality_coverage,
            "conflict_level": pack.conflict_level,
            "missing_slots": pack.missing_slots,
            "supporting_hit_ids": pack.supporting_hit_ids,
            "support_level": pack.support_level,
            "support_score": pack.support_score,
            "support_features": pack.support_features,
            "support_feature_weights": pack.support_feature_weights,
            "support_feature_contributions": pack.support_feature_contributions,
            "support_raw_features": pack.support_raw_features,
            "support_normalized_features": pack.support_normalized_features,
            "rerank_available": pack.rerank_available,
            "slot_coverage": pack.slot_coverage,
            "required_slots": pack.required_slots,
            "covered_slots": pack.covered_slots,
            "conflict_reasons": pack.conflict_reasons,
            "gate_decision": pack.gate_decision,
            "gate_reasons": pack.gate_reasons,
            "agent_evidence_used": agent_evidence_used,
            "agent_evidence_reasoning": agent_evidence_reasoning,
            "agent_gate_decision": agent_gate_decision,
            "unsupported_claims": unsupported_claims or pack.unsupported_claims,
            "agent_fallback_reason": agent_fallback_reason,
        }
        snapshots = list(state.get("retrieval_eval_snapshots", []) or [])
        if _retrieval_observability_enabled(self.settings):
            evidence_snapshot = build_snapshot(
                stage="evidence_gate",
                query_text=state.get("question", ""),
                hits=hits,
                max_text_chars=self.settings.retrieval_eval_max_text_chars,
                used_rerank=bool(state.get("used_rerank", False)),
                rerank_fallback_reason=state.get("rerank_fallback_reason"),
            )
            evidence_snapshot["evidence_gate"] = {
                "evidence_ok": evidence_ok,
                "support_score": pack.support_score,
                "support_level": pack.support_level,
                "supporting_hit_ids": pack.supporting_hit_ids,
                "missing_slots": pack.missing_slots,
                "gate_reasons": pack.gate_reasons,
                "conflict_level": pack.conflict_level,
                "conflict_reasons": pack.conflict_reasons,
                "gate_decision": pack.gate_decision,
            }
            payload["retrieval_eval_snapshots"] = _replace_snapshot(snapshots, evidence_snapshot)
        return payload

    def _select_retry_carry_forward_hits(self, state: TaskGraphState) -> list[SearchHit]:
        if not self.settings.tg_retry_carry_forward_enabled:
            return []
        candidates: list[tuple[str, int, SearchHit]] = []
        if state.get("reranked_hits"):
            candidates.extend(("rerank", rank, hit) for rank, hit in enumerate(state.get("reranked_hits", []) or [], start=1))
        expanded_stage = "final_after_retry" if state.get("prompt") or state.get("context") else "agent_chunk_grading"
        if expanded_stage == "final_after_retry" and not self.settings.tg_retry_carry_forward_include_citation_candidates:
            expanded_stage = "agent_chunk_grading"
        candidates.extend((expanded_stage, rank, hit) for rank, hit in enumerate(state.get("expanded_hits", []) or [], start=1))

        selected: list[SearchHit] = []
        seen: set[str] = set()
        min_score = self.settings.tg_retry_carry_forward_min_score
        min_label_rank = _agent_label_rank(self.settings.tg_retry_carry_forward_min_agent_label)
        for source_stage, rank, hit in candidates:
            key = _hit_key(hit)
            if not key or key in seen:
                continue
            if hit.metadata.get("agent_relevance_drop") is True:
                continue
            score = score_value(hit)
            if score < min_score:
                continue
            label = str(hit.metadata.get("agent_relevance_label") or "")
            if min_label_rank >= 0 and _agent_label_rank(label) < min_label_rank:
                continue
            copy = hit.model_copy(deep=True)
            copy.metadata.update(
                {
                    "retry_carry_forward": True,
                    "retry_carry_forward_from_stage": source_stage,
                    "retry_carry_forward_previous_rank": rank,
                    "retry_carry_forward_score": score,
                    "retry_carry_forward_min_score": min_score,
                    "retry_carry_forward_min_agent_label": self.settings.tg_retry_carry_forward_min_agent_label,
                }
            )
            selected.append(copy)
            seen.add(key)
            if len(selected) >= self.settings.tg_retry_carry_forward_top_n:
                break
        return selected

    def _merge_retry_carry_forward_hits(
        self,
        expanded_hits: list[SearchHit],
        carry_forward_hits: list[SearchHit],
        *,
        retry_count: int,
    ) -> tuple[list[SearchHit], dict[str, int]]:
        if not carry_forward_hits:
            return expanded_hits, {"added": 0, "matched": 0}
        merged = list(expanded_hits)
        by_key = {_hit_key(hit): index for index, hit in enumerate(merged)}
        added = 0
        matched = 0
        for carry_hit in carry_forward_hits:
            key = _hit_key(carry_hit)
            if key in by_key:
                index = by_key[key]
                existing = merged[index]
                copy = carry_hit.model_copy(deep=True)
                copy.metadata["retrieval_candidate_pool"] = "retry_carry_forward"
                copy.metadata["retry_carry_forward_matched"] = True
                copy.metadata["retry_carry_forward_replaced_current"] = True
                copy.metadata["retry_carry_forward_replaced_current_score"] = score_value(existing)
                copy.metadata["retry_carry_forward_retry_count"] = retry_count
                merged[index] = copy
                matched += 1
                continue
            copy = carry_hit.model_copy(deep=True)
            copy.metadata["retrieval_candidate_pool"] = "retry_carry_forward"
            copy.metadata["retry_carry_forward_added"] = True
            copy.metadata["retry_carry_forward_retry_count"] = retry_count
            merged.append(copy)
            by_key[key] = len(merged) - 1
            added += 1
        return merged, {"added": added, "matched": matched}

    def _filter_agent_drop_cached_hits(
        self,
        hits: list[SearchHit],
        *,
        drop_cache: dict[str, dict[str, Any]],
        stage: str,
    ) -> tuple[list[SearchHit], list[SearchHit]]:
        if not hits or not drop_cache:
            return hits, []
        kept: list[SearchHit] = []
        removed: list[SearchHit] = []
        for rank, hit in enumerate(hits, start=1):
            key = _hit_key(hit)
            cached = drop_cache.get(key)
            if cached is None:
                kept.append(hit)
                continue
            label = cached.get("agent_relevance_label")
            score = cached.get("agent_relevance_score")
            detail = cached.get("removed_reason_detail") or f"previous_agent_drop label={label}; score={score}"
            copy = hit.model_copy(deep=True)
            copy.metadata.update(
                {
                    "agent_relevance_drop": True,
                    "agent_relevance_drop_cached": True,
                    "agent_relevance_label": label,
                    "agent_relevance_score": score,
                    "agent_relevance_drop_reason": cached.get("agent_relevance_drop_reason"),
                    "agent_relevance_drop_threshold": cached.get("agent_relevance_drop_threshold"),
                }
            )
            removed.append(
                mark_removed_hit(
                    copy,
                    stage=stage,
                    reason="agent_drop_cache",
                    detail=str(detail),
                    previous_rank=rank,
                    extra={
                        "retrieval_removed_by": "agent_drop_cache",
                        "retrieval_previous_rank": rank,
                    },
                )
            )
        return kept, removed

    def _local_retry_node(self, state: TaskGraphState) -> TaskGraphState:
        timer = self._log_start("local_retry")
        decision = self.local_retry_planner.plan_retry(cast(dict[str, Any], state))
        carry_forward_hits = self._select_retry_carry_forward_hits(state)
        self._progress_retry("local_retry", decision.retry_count)
        plan = decision.plan
        retry_plan_validation_errors: list[str] = []
        agent_retry_used = False
        agent_retry_reasoning = ""
        agent_fallback_reason = state.get("agent_fallback_reason")
        if self.settings.tg_agent_retry_advisor_enabled:
            try:
                advice = self.agent_retry_advisor.advise(state=cast(dict[str, Any], state), rule_plan=plan)
                merge = self.plan_validator.merge_retry_advice(plan, advice)
                plan = merge.plan
                retry_plan_validation_errors.extend(merge.errors)
                agent_retry_used = merge.used
                agent_retry_reasoning = "; ".join(advice.reasons)
                if not merge.used and merge.errors:
                    agent_fallback_reason = ",".join(merge.errors)
            except Exception as exc:
                agent_fallback_reason = f"agent_retry_failed:{type(exc).__name__}"
                if not self.settings.tg_agent_fallback_to_rules:
                    raise
        self._log_end(
            "local_retry",
            timer,
            fallback=True,
            task_name="plan_update",
            retry_count=decision.retry_count,
            evidence_gain=decision.evidence_gain,
            retry_actions=",".join(decision.retry_actions),
            rewritten_query_text=decision.rewritten_query_text or "",
            page_window=plan.page_window,
            retry_channels=",".join(plan.channels()),
            retry_top_k=",".join(f"{task.channel}:{task.top_k}" for task in plan.tasks),
            agent_retry_used=agent_retry_used,
            retry_carry_forward_candidates=len(carry_forward_hits),
        )
        return {
            "retrieval_plan": plan.model_dump(),
            "retry_count": decision.retry_count,
            "retry_carry_forward_hits": carry_forward_hits,
            "retry_carry_forward_candidate_count": len(carry_forward_hits),
            "evidence_gain": decision.evidence_gain,
            "retry_actions": list(plan.retry_actions or decision.retry_actions),
            "retry_history": plan.retry_history,
            "rewritten_query_text": plan.rewritten_query_text or decision.rewritten_query_text,
            "page_window": plan.page_window,
            "agent_retry_used": agent_retry_used,
            "agent_retry_reasoning": agent_retry_reasoning,
            "retry_plan_validation_errors": retry_plan_validation_errors,
            "agent_fallback_reason": agent_fallback_reason,
            "local_retry_skipped": False,
        }

    def _build_prompt_node(self, state: TaskGraphState) -> TaskGraphState:
        self._progress_start("generation")
        hits = state.get("expanded_hits", [])
        hits = compute_composite_scores(hits, stage=STAGE_FINAL, settings=self.settings)
        filtered = filter_by_stage_threshold_with_removed(hits, stage=STAGE_FINAL, settings=self.settings)
        hits = filtered.kept
        context, citations = self.prompt_builder.build_context(hits)
        prompt = self.prompt_builder.build_prompt(question=state.get("question", ""), context=context)
        return {
            "expanded_hits": hits,
            "final_removed_hits": filtered.removed,
            "context": context,
            "citations": [c.model_dump() for c in citations],
            "prompt": prompt,
        }

    def _generate_answer_node(self, state: TaskGraphState) -> TaskGraphState:
        if state.get("refusal"):
            return {"answer": self.settings.uncertain_answer_text}
        citations = state.get("citations", [])
        if not citations:
            return {"answer": self.settings.uncertain_answer_text}
        answer = self.llm_client.generate(state.get("prompt", ""))
        return {"answer": answer or self.settings.uncertain_answer_text}

    def _citation_verify_node(self, state: TaskGraphState) -> TaskGraphState:
        timer = self._log_start("citation_verify")
        if not self.settings.tg_citation_verify_enabled:
            self._log_end("citation_verify", timer, fallback=False, skipped=True)
            return {
                "citation_ok": True,
                "citation_verify_enabled": False,
                "citation_verify_skipped": True,
                "citation_verify_reason": "disabled",
            }
        answer = state.get("answer", "")
        citations = state.get("citations", [])
        hits = state.get("expanded_hits", [])
        ok = True
        reasons: list[str] = []

        valid_keys = {
            (
                str(hit.metadata.get("source", "unknown")),
                int(hit.metadata.get("chunk_index", -1))
                if str(hit.metadata.get("chunk_index", "-1")).lstrip("-").isdigit()
                else -1,
            )
            for hit in hits
        }
        for c in citations:
            if isinstance(c, dict):
                key = (
                    str(c.get("source", "unknown")),
                    int(c.get("chunk_index", -1))
                    if str(c.get("chunk_index", "-1")).lstrip("-").isdigit()
                    else -1,
                )
                if key not in valid_keys:
                    ok = False
                    reasons.append("citation_key_not_in_context")
                    break

        if self.settings.tg_citation_strict and citations:
            tags = {f"[{c.get('index')}]" for c in citations if isinstance(c, dict)}
            if not any(tag in answer for tag in tags):
                ok = False
                reasons.append("citation_marker_missing")
        if self.settings.tg_citation_strict and not citations:
            ok = False
            reasons.append("citation_empty")
        self._log_end("citation_verify", timer, fallback=not ok)
        return {
            "citation_ok": ok,
            "citation_verify_enabled": True,
            "citation_verify_skipped": False,
            "citation_verify_strict": self.settings.tg_citation_strict,
            "citation_verify_reasons": reasons,
        }

    def _finalize_node(self, state: TaskGraphState) -> TaskGraphState:
        if int(state.get("retry_count", 0) or 0) <= 0:
            self._progress_skip("local_retry")
        if not self._local_retry_enabled():
            self._progress_skip("local_retry")
        snapshots = list(state.get("retrieval_eval_snapshots", []) or [])
        retrieval_eval_log_error = state.get("retrieval_eval_log_error")
        retrieval_visualization_run_dir = None
        retrieval_visualization_error = None
        final_hits = list(state.get("expanded_hits", []) or [])
        final_removed_hits = list(state.get("final_removed_hits", []) or [])
        if not final_hits and state.get("expanded_hits"):
            filtered = filter_by_stage_threshold_with_removed(
                compute_composite_scores(
                    state.get("expanded_hits", []),
                    stage=STAGE_FINAL,
                    settings=self.settings,
                ),
                stage=STAGE_FINAL,
                settings=self.settings,
            )
            final_hits = filtered.kept
            final_removed_hits = filtered.removed
        if _retrieval_observability_enabled(self.settings):
            snapshots = _replace_snapshot(
                snapshots,
                build_snapshot(
                    stage="final_after_retry",
                    query_text=state.get("rewritten_query_text") or state.get("question", ""),
                    hits=final_hits,
                    removed_hits=final_removed_hits,
                    max_text_chars=self.settings.retrieval_eval_max_text_chars,
                    used_rerank=bool(state.get("used_rerank", False)),
                    rerank_fallback_reason=state.get("rerank_fallback_reason"),
                ),
            )
            citation_rows = state.get("citations", []) or []
            if citation_rows:
                citation_keys = {
                    (
                        str(citation.get("source")),
                        int(citation.get("chunk_index", -1))
                        if str(citation.get("chunk_index", "-1")).lstrip("-").isdigit()
                        else -1,
                    )
                    for citation in citation_rows
                    if isinstance(citation, dict)
                }
                output_hits = [
                    hit
                    for hit in final_hits
                    if (
                        str(hit.metadata.get("source")),
                        int(hit.metadata.get("chunk_index", -1))
                        if str(hit.metadata.get("chunk_index", "-1")).lstrip("-").isdigit()
                        else -1,
                    )
                    in citation_keys
                ]
                if not output_hits:
                    output_hits = final_hits[: len(citation_rows)]
            else:
                output_hits = []
            output_keys = {(hit.node_id or hit.point_id or f"{hit.doc_id}:{hit.metadata.get('chunk_index')}") for hit in output_hits}
            final_output_removed_hits = []
            generation_skipped = not state.get("prompt")
            for rank, hit in enumerate(final_hits, start=1):
                key = hit.node_id or hit.point_id or f"{hit.doc_id}:{hit.metadata.get('chunk_index')}"
                if key in output_keys:
                    continue
                reason = "citation_not_selected"
                detail = "not referenced by final citations"
                if generation_skipped:
                    reason = "generation_skipped"
                    detail = "build_prompt/generate_answer was skipped before final output"
                elif not citation_rows:
                    detail = "no final citations were produced"
                elif state.get("citation_ok") is False:
                    detail = "citation verification failed or citation was not selected"
                final_output_removed_hits.append(
                    mark_removed_hit(
                        hit.model_copy(deep=True),
                        stage="final_output",
                        reason=reason,
                        detail=detail,
                        previous_rank=rank,
                        extra={
                            "citation_verify_enabled": state.get("citation_verify_enabled", self.settings.tg_citation_verify_enabled),
                            "citation_verify_strict": state.get("citation_verify_strict", self.settings.tg_citation_strict),
                            "citation_ok": state.get("citation_ok"),
                            "citation_verify_reasons": state.get("citation_verify_reasons", []),
                        },
                    )
                )
            snapshots = _replace_snapshot(
                snapshots,
                build_snapshot(
                    stage="final_output",
                    query_text=state.get("rewritten_query_text") or state.get("question", ""),
                    hits=output_hits,
                    removed_hits=final_output_removed_hits,
                    max_text_chars=self.settings.retrieval_eval_max_text_chars,
                    used_rerank=bool(state.get("used_rerank", False)),
                    rerank_fallback_reason=state.get("rerank_fallback_reason"),
                ),
            )
            if self.settings.retrieval_eval_log_enabled or self.settings.retrieval_vis_auto_write:
                try:
                    record = build_query_record(
                        query_id=state.get("retrieval_eval_query_id") or new_query_id(),
                        question=state.get("question", ""),
                        state=cast(dict[str, Any], state),
                        snapshots=snapshots,
                    )
                    if self.settings.retrieval_eval_log_enabled:
                        self.retrieval_history_recorder.append(record)
                    if self.settings.retrieval_vis_auto_write:
                        try:
                            run_dir = write_retrieval_visualization_report(
                                record,
                                settings=self.settings,
                                source_mode="auto_after_query",
                            )
                            retrieval_visualization_run_dir = str(run_dir)
                        except Exception as vis_exc:
                            retrieval_visualization_error = f"{type(vis_exc).__name__}: {vis_exc}"
                except Exception as exc:
                    retrieval_eval_log_error = f"{type(exc).__name__}: {exc}"
        result = RAGResult(
            answer=state.get("answer", self.settings.uncertain_answer_text),
            citations=[
                Citation.model_validate(citation)
                for citation in (state.get("citations", []) or [])
                if isinstance(citation, dict)
            ],
            retrieved_count=len(final_hits),
            used_rerank=bool(state.get("used_rerank", False)),
            fallback_used=bool(state.get("retry_count", 0) > 0),
            retrieval_eval_snapshots=snapshots,
            debug={
                "route": state.get("route"),
                "retry_count": state.get("retry_count", 0),
                "evidence_gate_enabled": state.get("evidence_gate_enabled", self._evidence_gate_enabled()),
                "evidence_gate_skipped": state.get("evidence_gate_skipped", not self._evidence_gate_enabled()),
                "local_retry_enabled": state.get("local_retry_enabled", self._local_retry_enabled()),
                "local_retry_skipped": state.get(
                    "local_retry_skipped",
                    (not self._local_retry_enabled()) or int(state.get("retry_count", 0) or 0) <= 0,
                ),
                **self._retry_skip_debug(state),
                "evidence_ok": state.get("evidence_ok", False),
                "citation_ok": state.get("citation_ok", False),
                "citation_verify_enabled": state.get("citation_verify_enabled", self.settings.tg_citation_verify_enabled),
                "citation_verify_skipped": state.get("citation_verify_skipped", not self.settings.tg_citation_verify_enabled),
                "citation_verify_strict": state.get("citation_verify_strict", self.settings.tg_citation_strict),
                "citation_verify_reasons": state.get("citation_verify_reasons", []),
                "refusal": state.get("refusal", False),
                "refusal_reason": state.get("refusal_reason"),
                "evidence_gaps": state.get("evidence_gaps", []),
                "executed_channels": state.get("executed_channels", []),
                "claim_supported": state.get("claim_supported", False),
                "source_coverage": state.get("source_coverage", {}),
                "page_coverage": state.get("page_coverage", {}),
                "modality_coverage": state.get("modality_coverage", {}),
                "conflict_level": state.get("conflict_level", "none"),
                "missing_slots": state.get("missing_slots", []),
                "supporting_hit_ids": state.get("supporting_hit_ids", []),
                "support_level": state.get("support_level", "none"),
                "support_score": state.get("support_score", 0.0),
                "support_features": state.get("support_features", {}),
                "support_feature_weights": state.get("support_feature_weights", {}),
                "support_feature_contributions": state.get("support_feature_contributions", {}),
                "support_raw_features": state.get("support_raw_features", {}),
                "support_normalized_features": state.get("support_normalized_features", {}),
                "rerank_available": state.get("rerank_available", False),
                "slot_coverage": state.get("slot_coverage", {}),
                "conflict_reasons": state.get("conflict_reasons", []),
                "gate_decision": state.get("gate_decision", "retry"),
                "gate_reasons": state.get("gate_reasons", []),
                "retry_actions": state.get("retry_actions", []),
                "retry_history": state.get("retry_history", []),
                "retry_carry_forward_enabled": self.settings.tg_retry_carry_forward_enabled,
                "retry_carry_forward_candidate_count": state.get("retry_carry_forward_candidate_count", 0),
                "retry_carry_forward_added_count": state.get("retry_carry_forward_added_count", 0),
                "retry_carry_forward_matched_count": state.get("retry_carry_forward_matched_count", 0),
                "rewritten_query_text": state.get("rewritten_query_text"),
                "page_window": state.get("page_window"),
                "agent_route_used": state.get("agent_route_used", False),
                "agent_route_confidence": state.get("agent_route_confidence", 0.0),
                "agent_route_reasoning": state.get("agent_route_reasoning", ""),
                "agent_plan_used": state.get("agent_plan_used", False),
                "agent_plan_reasoning": state.get("agent_plan_reasoning", ""),
                "agent_evidence_used": state.get("agent_evidence_used", False),
                "agent_evidence_reasoning": state.get("agent_evidence_reasoning", ""),
                "agent_gate_decision": state.get("agent_gate_decision"),
                "unsupported_claims": state.get("unsupported_claims", []),
                "agent_chunk_grading_used": state.get("agent_chunk_grading_used", False),
                "agent_chunk_grading_hit_count": state.get("agent_chunk_grading_hit_count", 0),
                "agent_chunk_removed_hit_count": state.get("agent_chunk_removed_hit_count", 0),
                "agent_chunk_removed_reasons": state.get("agent_chunk_removed_reasons", {}),
                "agent_chunk_grade_cache_enabled": self.settings.tg_agent_chunk_grade_cache_enabled,
                "agent_chunk_drop_cache_size": state.get("agent_chunk_drop_cache_size", 0),
                "agent_chunk_grade_cache_size": state.get("agent_chunk_grade_cache_size", 0),
                "agent_chunk_grade_cache_hits": state.get("agent_chunk_grade_cache_hits", 0),
                "agent_chunk_llm_graded_hit_count": state.get("agent_chunk_llm_graded_hit_count", 0),
                "agent_retry_used": state.get("agent_retry_used", False),
                "agent_retry_reasoning": state.get("agent_retry_reasoning", ""),
                "agent_fallback_reason": state.get("agent_fallback_reason"),
                "plan_validation_errors": state.get("plan_validation_errors", []),
                "retry_plan_validation_errors": state.get("retry_plan_validation_errors", []),
                "rerank_fallback_reason": state.get("rerank_fallback_reason"),
                "rerank_score_top": state.get("rerank_score_top"),
                "rerank_hit_count": state.get("rerank_hit_count", 0),
                "retrieval_eval_snapshot_stages": [s.get("stage") for s in snapshots if isinstance(s, dict)],
                "retrieval_eval_log_error": retrieval_eval_log_error,
                "retrieval_visualization_run_dir": retrieval_visualization_run_dir,
                "retrieval_visualization_error": retrieval_visualization_error,
            },
        )
        if state.get("refusal") and self.settings.tg_allow_refusal:
            result.answer = self.settings.uncertain_answer_text
        self._progress_done("generation")
        return {"result": result}

    def _retry_skip_debug(self, state: TaskGraphState) -> dict[str, Any]:
        if state.get("evidence_ok"):
            reason = "evidence_ok"
        elif not self._local_retry_enabled():
            reason = "local_retry_disabled"
        elif state.get("refusal"):
            reason = "refusal"
        else:
            retry_count = int(state.get("retry_count", 0) or 0)
            max_retries = int(state.get("max_retries", self.settings.tg_max_retries))
            started_at = int(state.get("started_at_ms", _now_ms()))
            elapsed = _now_ms() - started_at
            token_budget = int(state.get("budget_tokens", self.settings.tg_budget_tokens))
            token_estimate = self._estimate_token_usage(state)
            if retry_count >= max_retries:
                reason = "max_retries"
            elif elapsed > self.settings.tg_budget_ms:
                reason = "budget_ms"
            elif token_estimate > token_budget:
                reason = "token_budget"
            elif retry_count > 0 and float(state.get("evidence_gain", 1.0)) < self.settings.tg_min_gain_threshold:
                reason = "min_gain"
            elif not state.get("gate_decision") and self._should_skip_evidence_gate(state):
                reason = "evidence_gate_skipped"
            elif retry_count <= 0:
                reason = "not_triggered"
            else:
                reason = None
            return {
                "retry_skipped_reason": reason,
                "retry_budget_elapsed_ms": elapsed,
                "retry_budget_limit_ms": self.settings.tg_budget_ms,
                "retry_token_usage_estimate": token_estimate,
                "retry_token_budget": token_budget,
            }
        return {
            "retry_skipped_reason": reason,
            "retry_budget_elapsed_ms": _now_ms() - int(state.get("started_at_ms", _now_ms())),
            "retry_budget_limit_ms": self.settings.tg_budget_ms,
            "retry_token_usage_estimate": self._estimate_token_usage(state),
            "retry_token_budget": int(state.get("budget_tokens", self.settings.tg_budget_tokens)),
        }

    def _retry_decision(self, state: TaskGraphState) -> str:
        if self._should_skip_evidence_gate(state):
            if self._local_retry_enabled() and int(state.get("retry_count", 0) or 0) <= 0:
                return "local_retry"
            return "build_prompt"
        if self._should_skip_local_retry(state):
            return "build_prompt"
        if state.get("refusal"):
            return "finalize"
        if state.get("evidence_ok") or (self.settings.tg_agent_evidence_critic_enabled and state.get("agent_gate_decision") == "pass"):
            return "build_prompt"
        retry_count = int(state.get("retry_count", 0))
        if retry_count >= int(state.get("max_retries", self.settings.tg_max_retries)):
            if self._should_generate_on_retry_exhausted(state):
                return "build_prompt"
            return "finalize"
        started_at = int(state.get("started_at_ms", _now_ms()))
        elapsed = _now_ms() - started_at
        if elapsed > self.settings.tg_budget_ms:
            return "finalize"
        token_budget = int(state.get("budget_tokens", self.settings.tg_budget_tokens))
        if self._estimate_token_usage(state) > token_budget:
            return "finalize"
        if retry_count > 0 and float(state.get("evidence_gain", 1.0)) < self.settings.tg_min_gain_threshold:
            return "finalize"
        return "local_retry"

    def _should_generate_on_retry_exhausted(self, state: TaskGraphState) -> bool:
        if not self.settings.tg_generate_on_retry_exhausted:
            return False
        if state.get("refusal") and self.settings.tg_allow_refusal:
            return False
        hits = state.get("expanded_hits", []) or []
        return len(hits) >= self.settings.tg_generate_on_retry_exhausted_min_hits

    def _citation_decision(self, state: TaskGraphState) -> str:
        if self._should_skip_local_retry(state):
            return "finalize"
        if state.get("citation_ok"):
            return "finalize"
        retry_count = int(state.get("retry_count", 0))
        if retry_count >= int(state.get("max_retries", self.settings.tg_max_retries)):
            return "finalize"
        started_at = int(state.get("started_at_ms", _now_ms()))
        elapsed = _now_ms() - started_at
        if elapsed > self.settings.tg_budget_ms:
            return "finalize"
        token_budget = int(state.get("budget_tokens", self.settings.tg_budget_tokens))
        if self._estimate_token_usage(state) > token_budget:
            return "finalize"
        return "local_retry"

    def _after_rerank_decision(self, state: TaskGraphState) -> str:
        if self._evidence_gate_enabled():
            return "evidence_gate"
        if self._local_retry_enabled() and int(state.get("retry_count", 0) or 0) <= 0:
            return "local_retry"
        return "build_prompt"

    def _compile_graph(self):
        graph = StateGraph(TaskGraphState)
        graph.add_node("question_analyze", self._question_analyze_node)
        graph.add_node("task_router", self._task_router_node)
        graph.add_node("embed_query", self._embed_query_node)
        graph.add_node("retrieve_fanout", self._retrieve_fanout_node)
        graph.add_node("retrieve_rrf", self._retrieve_rrf_node)
        graph.add_node("relationship_expand", self._relationship_expand_node)
        graph.add_node("rerank", self._rerank_node)
        graph.add_node("agent_chunk_grader", self._agent_chunk_grader_node)
        graph.add_node("evidence_gate", self._evidence_gate_node)
        graph.add_node("local_retry", self._local_retry_node)
        graph.add_node("build_prompt", self._build_prompt_node)
        graph.add_node("generate_answer", self._generate_answer_node)
        graph.add_node("citation_verify", self._citation_verify_node)
        graph.add_node("finalize", self._finalize_node)

        graph.set_entry_point("question_analyze")
        graph.add_edge("question_analyze", "task_router")
        graph.add_edge("task_router", "embed_query")
        graph.add_edge("embed_query", "retrieve_fanout")
        graph.add_edge("retrieve_fanout", "retrieve_rrf")
        graph.add_edge("retrieve_rrf", "relationship_expand")
        graph.add_edge("relationship_expand", "rerank")
        graph.add_edge("rerank", "agent_chunk_grader")
        graph.add_conditional_edges(
            "agent_chunk_grader",
            self._after_rerank_decision,
            {"evidence_gate": "evidence_gate", "local_retry": "local_retry", "build_prompt": "build_prompt"},
        )
        graph.add_conditional_edges(
            "evidence_gate",
            self._retry_decision,
            {"build_prompt": "build_prompt", "local_retry": "local_retry", "finalize": "finalize"},
        )
        graph.add_edge("local_retry", "retrieve_fanout")
        graph.add_edge("build_prompt", "generate_answer")
        graph.add_edge("generate_answer", "citation_verify")
        graph.add_conditional_edges(
            "citation_verify",
            self._citation_decision,
            {"local_retry": "local_retry", "finalize": "finalize"},
        )
        graph.add_edge("finalize", END)
        return graph.compile()

    def invoke(self, question: str, filters: dict[str, Any] | None = None) -> RAGResult:
        state: TaskGraphState = {
            "question": question,
            "filters": filters,
            "retry_count": 0,
            "started_at_ms": _now_ms(),
            "evidence_gain": 1.0,
            "retrieval_eval_query_id": new_query_id(),
            "retrieval_eval_snapshots": [],
            "evidence_gate_enabled": self._evidence_gate_enabled(),
            "local_retry_enabled": self._local_retry_enabled(),
            "evidence_gate_skipped": not self._evidence_gate_enabled(),
            "local_retry_skipped": not self._local_retry_enabled(),
        }
        output = self.graph.invoke(state)
        result = output.get("result")
        if not isinstance(result, RAGResult):
            raise RuntimeError("TaskGraph did not produce a valid result")
        return result
