from __future__ import annotations

import re
import time
from typing import Any, cast

from langgraph.graph import END, StateGraph

from agentic_rag.config import Settings
from agentic_rag.generation.prompt_builder import PromptBuilder
from agentic_rag.graph.task_graph_prep import TaskGraphState
from agentic_rag.models.providers import EmbeddingProvider, LLMClient
from agentic_rag.observability.stage_logger import StageLogger, StageTimer
from agentic_rag.retrieval.evidence_pack import EvidencePack
from agentic_rag.retrieval.retrieval_plan import RetrievalPlan, RetrievalTask
from agentic_rag.retrieval.retriever import MultiChannelRetriever
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
        stage_logger: StageLogger | None = None,
    ):
        self.settings = settings
        self.embedding_provider = embedding_provider
        self.retriever = retriever
        self.llm_client = llm_client
        self.prompt_builder = prompt_builder
        self.stage_logger = stage_logger
        self.graph = self._compile_graph()

    def _log_start(self, stage: str, **fields: Any) -> StageTimer:
        timer = StageTimer.start_now()
        if self.stage_logger:
            self.stage_logger.log_stage_start(stage, **fields)
        return timer

    def _log_end(self, stage: str, timer: StageTimer, **fields: Any) -> None:
        if self.stage_logger:
            self.stage_logger.log_stage_end(stage, latency_ms=timer.elapsed_ms(), **fields)

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
            else "page_first"
            if "page" in target_modalities
            else "text_first"
        )
        self._log_end(
            "query_analyze",
            timer,
            route=route,
            query_text=question,
            need_cross_doc=need_cross_doc,
            need_page_level=need_page_level,
        )
        return {
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
        }

    def _task_router_node(self, state: TaskGraphState) -> TaskGraphState:
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
            tasks.append(
                RetrievalTask(
                    channel="page",
                    query_text=question,
                    top_k=self.settings.page_top_k,
                    filters=filters,
                )
            )
        if "table" in target:
            tasks.append(
                RetrievalTask(
                    channel="table",
                    query_text=question,
                    top_k=self.settings.table_top_k,
                    filters=filters,
                )
            )
        if state.get("need_cross_doc"):
            tasks.append(
                RetrievalTask(
                    channel="relationship",
                    query_text=question,
                    top_k=self.settings.rrf_top_k,
                    filters=filters,
                )
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
            query_text=question,
            tasks=tasks,
        )
        self._log_end("task_route", timer, query_plan=plan.model_dump())
        return {"retrieval_plan": plan.model_dump()}

    def _embed_query_node(self, state: TaskGraphState) -> TaskGraphState:
        question = state.get("question", "")
        vectors = self.embedding_provider.embed_texts([question])
        if not vectors:
            raise RuntimeError("TaskGraph embed_query returned empty vectors")
        return {"query_vector": vectors[0]}

    def _retrieve_fanout_node(self, state: TaskGraphState) -> TaskGraphState:
        timer = self._log_start("retrieve_fanout", query_text=state.get("question", ""))
        question = state.get("question", "")
        query_vector = state.get("query_vector") or []
        filters = state.get("filters")
        result = self.retriever.retrieve(
            query_text=question,
            query_vector=query_vector,
            filters=filters,
        )
        self._log_end(
            "retrieve_fanout",
            timer,
            evidence_count=sum(len(v) for v in result.route_hits.values()),
        )
        return {
            "route_hits": result.route_hits,
            "fused_hits": result.hits,
            "expanded_hits": result.expanded_hits,
            "evidence_gain": state.get("evidence_gain", 1.0),
        }

    def _retrieve_rrf_node(self, state: TaskGraphState) -> TaskGraphState:
        timer = self._log_start("retrieve_rrf")
        fused = state.get("fused_hits", [])
        self._log_end("retrieve_rrf", timer, evidence_count=len(fused))
        return {"fused_hits": fused}

    def _relationship_expand_node(self, state: TaskGraphState) -> TaskGraphState:
        expanded = state.get("expanded_hits", [])
        return {"expanded_hits": expanded}

    def _evidence_gate_node(self, state: TaskGraphState) -> TaskGraphState:
        timer = self._log_start("evidence_gate")
        hits = state.get("expanded_hits", [])
        question = state.get("question", "")
        q_tokens = set(_keyword_tokens(question))
        coverage = 0.0
        if q_tokens and hits:
            covered = set()
            for hit in hits[: max(1, self.settings.tg_min_evidence_hits * 2)]:
                covered |= set(_keyword_tokens(_hit_evidence_text(hit))) & q_tokens
            coverage = len(covered) / max(1, len(q_tokens))
        target_modalities = state.get("target_modalities") or []
        has_formula_evidence = any(
            hit.modality == "formula" or hit.formula_latex or hit.metadata.get("modality") == "formula"
            for hit in hits
        )
        min_coverage_ratio = self.settings.tg_min_coverage_ratio
        if "formula" in target_modalities and has_formula_evidence:
            min_coverage_ratio = 0.0
        evidence_gaps: list[str] = []
        if len(hits) < self.settings.tg_min_evidence_hits:
            evidence_gaps.append("insufficient_hits")
        if coverage < min_coverage_ratio:
            evidence_gaps.append("low_keyword_coverage")
        conflict_detected = False
        # simple conflict heuristic for "yes/no" contradiction.
        top_text = " ".join((h.text or "") for h in hits[:4]).lower()
        if " not " in f" {top_text} " and ("\u662f" in top_text or "yes" in top_text):
            conflict_detected = True
            evidence_gaps.append("possible_conflict")
        evidence_ok = len(evidence_gaps) == 0
        refusal = bool(conflict_detected and self.settings.tg_allow_refusal)
        refusal_reason = "evidence_conflict" if refusal else None
        plan_raw = state.get("retrieval_plan") or {}
        plan = RetrievalPlan(**plan_raw) if isinstance(plan_raw, dict) else plan_raw
        pack = EvidencePack(
            plan=plan,
            hits=hits,
            route_hits=state.get("route_hits", {}),
            expanded_hits=hits,
            evidence_ok=evidence_ok,
            evidence_gaps=evidence_gaps,
            conflict_detected=conflict_detected,
        )
        self._log_end(
            "evidence_gate",
            timer,
            evidence_count=len(hits),
            fallback=not evidence_ok,
            error_msg=",".join(evidence_gaps),
        )
        return {
            "evidence_pack": pack.model_dump(),
            "evidence_ok": evidence_ok,
            "evidence_gaps": evidence_gaps,
            "refusal": refusal,
            "refusal_reason": refusal_reason,
        }

    def _local_retry_node(self, state: TaskGraphState) -> TaskGraphState:
        timer = self._log_start("local_retry")
        old_hits = state.get("expanded_hits", [])
        old_ids = {h.point_id for h in old_hits}
        retry_count = int(state.get("retry_count", 0)) + 1
        plan = RetrievalPlan(**(state.get("retrieval_plan") or {}))

        if "low_keyword_coverage" in (state.get("evidence_gaps") or []):
            has_bm25 = any(t.channel == "bm25" for t in plan.tasks)
            if not has_bm25:
                plan.tasks.append(
                    RetrievalTask(
                        channel="bm25",
                        query_text=plan.query_text or state.get("question", ""),
                        top_k=self.settings.bm25_top_k,
                        filters=cast(dict[str, object], state.get("filters") or {}),
                    )
                )
        if "insufficient_hits" in (state.get("evidence_gaps") or []):
            has_page = any(t.channel == "page" for t in plan.tasks)
            if not has_page:
                plan.tasks.append(
                    RetrievalTask(
                        channel="page",
                        query_text=plan.query_text or state.get("question", ""),
                        top_k=self.settings.page_top_k,
                        filters=cast(dict[str, object], state.get("filters") or {}),
                    )
                )
        plan.retry_count = retry_count
        new_ids = {h.point_id for h in state.get("fused_hits", [])}
        union = len(old_ids | new_ids)
        gain = 0.0 if union == 0 else (len(new_ids - old_ids) / union)
        self._log_end(
            "local_retry",
            timer,
            fallback=True,
            task_name="plan_update",
            retry_count=retry_count,
            evidence_gain=gain,
        )
        return {"retrieval_plan": plan.model_dump(), "retry_count": retry_count, "evidence_gain": gain}

    def _build_prompt_node(self, state: TaskGraphState) -> TaskGraphState:
        hits = state.get("expanded_hits", [])
        context, citations = self.prompt_builder.build_context(hits)
        prompt = self.prompt_builder.build_prompt(question=state.get("question", ""), context=context)
        return {"context": context, "citations": [c.model_dump() for c in citations], "prompt": prompt}

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
        result = RAGResult(
            answer=state.get("answer", self.settings.uncertain_answer_text),
            citations=[],
            retrieved_count=len(state.get("expanded_hits", [])),
            used_rerank=False,
            fallback_used=bool(state.get("retry_count", 0) > 0),
            debug={
                "route": state.get("route"),
                "retry_count": state.get("retry_count", 0),
                "evidence_ok": state.get("evidence_ok", False),
                "citation_ok": state.get("citation_ok", False),
                "refusal": state.get("refusal", False),
                "refusal_reason": state.get("refusal_reason"),
                "evidence_gaps": state.get("evidence_gaps", []),
            },
        )
        for row in state.get("citations", []):
            try:
                result.citations.append(Citation(**row))
            except Exception:
                continue
        if state.get("refusal") and self.settings.tg_allow_refusal:
            result.answer = self.settings.uncertain_answer_text
        return {"result": result}

    def _retry_decision(self, state: TaskGraphState) -> str:
        if state.get("refusal"):
            return "finalize"
        if state.get("evidence_ok"):
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

    def _compile_graph(self):
        graph = StateGraph(TaskGraphState)
        graph.add_node("question_analyze", self._question_analyze_node)
        graph.add_node("task_router", self._task_router_node)
        graph.add_node("embed_query", self._embed_query_node)
        graph.add_node("retrieve_fanout", self._retrieve_fanout_node)
        graph.add_node("retrieve_rrf", self._retrieve_rrf_node)
        graph.add_node("relationship_expand", self._relationship_expand_node)
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
        graph.add_edge("relationship_expand", "evidence_gate")
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
        }
        output = self.graph.invoke(state)
        result = output.get("result")
        if not isinstance(result, RAGResult):
            raise RuntimeError("TaskGraph did not produce a valid result")
        return result
