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
from agentic_rag.retrieval.scoring import STAGE_FINAL, compute_composite_scores, filter_by_stage_threshold
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


def _replace_snapshot(snapshots: list[dict[str, Any]], snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    stage = snapshot.get("stage")
    return [row for row in snapshots if row.get("stage") != stage] + [snapshot]


def _retrieval_observability_enabled(settings: Settings) -> bool:
    return bool(settings.retrieval_eval_log_enabled or settings.retrieval_vis_auto_write)


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
) -> None:
    if any(task.channel == channel for task in tasks):
        return
    tasks.append(
        RetrievalTask(
            channel=channel,
            query_text=query_text,
            top_k=top_k,
            filters=filters,
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
        if state.get("need_cross_doc"):
            _add_retrieval_task_if_missing(
                tasks,
                channel="relationship",
                query_text=question,
                top_k=self.settings.rrf_top_k,
                filters=filters,
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
        if _retrieval_observability_enabled(self.settings) and (
            retry_count > 0 or not any(s.get("stage") == "initial_retrieval" for s in snapshots)
        ):
            snapshots.append(
                build_snapshot(
                    stage=snapshot_stage,
                    query_text=state.get("rewritten_query_text") or question,
                    hits=result.hits,
                    max_text_chars=self.settings.retrieval_eval_max_text_chars,
                    used_rerank=False,
                )
            )
            snapshots = _replace_snapshot(
                snapshots,
                build_snapshot(
                    stage=expanded_snapshot_stage,
                    query_text=state.get("rewritten_query_text") or question,
                    hits=result.expanded_hits,
                    max_text_chars=self.settings.retrieval_eval_max_text_chars,
                    used_rerank=False,
                ),
            )
        return {
            "route_hits": result.route_hits,
            "fused_hits": result.hits,
            "expanded_hits": result.expanded_hits,
            "evidence_gain": state.get("evidence_gain", 1.0),
            "executed_channels": result.executed_channels,
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
            if _retrieval_observability_enabled(self.settings):
                retry_count = int(state.get("retry_count", 0) or 0)
                snapshots = _replace_snapshot(
                    snapshots,
                    build_snapshot(
                        stage=f"retry_{retry_count}_rerank" if retry_count > 0 else "rerank",
                        query_text=state.get("question", ""),
                        hits=hits[: self.settings.context_top_n],
                        max_text_chars=self.settings.retrieval_eval_max_text_chars,
                        used_rerank=False,
                        rerank_fallback_reason=None if not self.settings.rerank_enabled else "rerank_service_unavailable",
                    ),
                )
            return {
                "reranked_hits": hits[: self.settings.context_top_n],
                "expanded_hits": hits,
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
                    max_text_chars=self.settings.retrieval_eval_max_text_chars,
                    used_rerank=result.used_rerank,
                    rerank_fallback_reason=result.fallback_reason,
                ),
            )
            if _retrieval_observability_enabled(self.settings)
            else state.get("retrieval_eval_snapshots", []),
            **skipped_evidence,
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
        return {
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

    def _local_retry_node(self, state: TaskGraphState) -> TaskGraphState:
        timer = self._log_start("local_retry")
        decision = self.local_retry_planner.plan_retry(cast(dict[str, Any], state))
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
        )
        return {
            "retrieval_plan": plan.model_dump(),
            "retry_count": decision.retry_count,
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
        hits = filter_by_stage_threshold(hits, stage=STAGE_FINAL, settings=self.settings)
        context, citations = self.prompt_builder.build_context(hits)
        prompt = self.prompt_builder.build_prompt(question=state.get("question", ""), context=context)
        return {"expanded_hits": hits, "context": context, "citations": [c.model_dump() for c in citations], "prompt": prompt}

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
        answer = state.get("answer", "")
        citations = state.get("citations", [])
        hits = state.get("expanded_hits", [])
        ok = True

        valid_keys = {
            (
                str(hit.metadata.get("source", "unknown")),
                int(hit.metadata.get("chunk_index", -1)) if str(hit.metadata.get("chunk_index", "-1")).isdigit() else -1,
            )
            for hit in hits
        }
        for c in citations:
            if isinstance(c, dict):
                key = (str(c.get("source", "unknown")), int(c.get("chunk_index", -1)))
                if key not in valid_keys:
                    ok = False
                    break

        if self.settings.tg_citation_strict and citations:
            tags = {f"[{c.get('index')}]" for c in citations if isinstance(c, dict)}
            if not any(tag in answer for tag in tags):
                ok = False
        if self.settings.tg_citation_strict and not citations:
            ok = False
        self._log_end("citation_verify", timer, fallback=not ok)
        return {"citation_ok": ok}

    def _finalize_node(self, state: TaskGraphState) -> TaskGraphState:
        if int(state.get("retry_count", 0) or 0) <= 0:
            self._progress_skip("local_retry")
        if not self._local_retry_enabled():
            self._progress_skip("local_retry")
        snapshots = list(state.get("retrieval_eval_snapshots", []) or [])
        retrieval_eval_log_error = state.get("retrieval_eval_log_error")
        retrieval_visualization_run_dir = None
        retrieval_visualization_error = None
        if self.settings.retrieval_eval_log_enabled or self.settings.retrieval_vis_auto_write:
            snapshots = _replace_snapshot(
                snapshots,
                build_snapshot(
                    stage="final_after_retry",
                    query_text=state.get("rewritten_query_text") or state.get("question", ""),
                    hits=filter_by_stage_threshold(
                        compute_composite_scores(
                            state.get("expanded_hits", []),
                            stage=STAGE_FINAL,
                            settings=self.settings,
                        ),
                        stage=STAGE_FINAL,
                        settings=self.settings,
                    ),
                    max_text_chars=self.settings.retrieval_eval_max_text_chars,
                    used_rerank=bool(state.get("used_rerank", False)),
                    rerank_fallback_reason=state.get("rerank_fallback_reason"),
                ),
            )
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
            citations=[],
            retrieved_count=len(state.get("expanded_hits", [])),
            used_rerank=bool(state.get("used_rerank", False)),
            fallback_used=bool(state.get("retry_count", 0) > 0),
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
                "evidence_ok": state.get("evidence_ok", False),
                "citation_ok": state.get("citation_ok", False),
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
                "agent_retry_used": state.get("agent_retry_used", False),
                "agent_retry_reasoning": state.get("agent_retry_reasoning", ""),
                "agent_fallback_reason": state.get("agent_fallback_reason"),
                "plan_validation_errors": state.get("plan_validation_errors", []),
                "retry_plan_validation_errors": state.get("retry_plan_validation_errors", []),
                "rerank_fallback_reason": state.get("rerank_fallback_reason"),
                "rerank_score_top": state.get("rerank_score_top"),
                "rerank_hit_count": state.get("rerank_hit_count", 0),
                "retrieval_eval_log_error": retrieval_eval_log_error,
                "retrieval_visualization_run_dir": retrieval_visualization_run_dir,
                "retrieval_visualization_error": retrieval_visualization_error,
            },
        )
        for row in state.get("citations", []):
            try:
                result.citations.append(Citation(**row))
            except Exception:
                continue
        if state.get("refusal") and self.settings.tg_allow_refusal:
            result.answer = self.settings.uncertain_answer_text
        self._progress_done("generation")
        return {"result": result}

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
        graph.add_conditional_edges(
            "rerank",
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
