from __future__ import annotations

import time
from copy import copy
from dataclasses import dataclass
from typing import Any

from agentic_rag.evaluation.retrieval_history import serialize_hit
from agentic_rag.models.providers import OpenAICompatibleClient, ProviderError
from check.metrics import estimate_tokens


@dataclass(slots=True)
class QueryRun:
    answer: str
    citations: list[dict[str, Any]]
    initial_hits: list[dict[str, Any]]
    initial_expanded_hits: list[dict[str, Any]]
    retrieved_hits: list[dict[str, Any]]
    reranked_hits: list[dict[str, Any]]
    agent_graded_hits: list[dict[str, Any]]
    evidence_gate_hits: list[dict[str, Any]]
    retry_hits: list[dict[str, Any]]
    final_hits: list[dict[str, Any]]
    local_recheck_hits: list[dict[str, Any]]
    context_hits: list[dict[str, Any]]
    ranked_hits_by_stage: dict[str, list[dict[str, Any]]]
    retrieved_count: int
    timings_ms: dict[str, float]
    token_usage: dict[str, int | float]
    debug: dict[str, Any]


def hit_to_dict(hit: Any, rank: int) -> dict[str, Any]:
    if hasattr(hit, "metadata") and hasattr(hit, "point_id"):
        return _flatten_serialized_hit(serialize_hit(hit, rank=rank, max_text_chars=8000))
    metadata = getattr(hit, "metadata", {}) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    return {
        "rank": rank,
        "point_id": getattr(hit, "point_id", None),
        "node_id": getattr(hit, "node_id", None),
        "doc_id": getattr(hit, "doc_id", None) or metadata.get("doc_id"),
        "source": metadata.get("source"),
        "title": metadata.get("title"),
        "chunk_index": metadata.get("chunk_index"),
        "page": getattr(hit, "page", None) or metadata.get("page"),
        "score": getattr(hit, "score", None),
        "channel": getattr(hit, "channel", None),
        "modality": getattr(hit, "modality", None) or metadata.get("modality"),
        "text": str(getattr(hit, "text", "") or ""),
    }


def _flatten_serialized_hit(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    scores = row.get("scores") if isinstance(row.get("scores"), dict) else {}
    retrieval = row.get("retrieval") if isinstance(row.get("retrieval"), dict) else {}
    flat = {
        "rank": row.get("rank"),
        "point_id": row.get("point_id"),
        "node_id": row.get("node_id"),
        "doc_id": row.get("doc_id") or metadata.get("doc_id"),
        "source": metadata.get("source"),
        "title": metadata.get("title"),
        "chunk_index": row.get("chunk_index") if row.get("chunk_index") is not None else metadata.get("chunk_index"),
        "page": metadata.get("page"),
        "score": scores.get("score"),
        "channel": retrieval.get("channel"),
        "modality": retrieval.get("modality") or metadata.get("modality"),
        "text": row.get("text") or "",
        "vector_name": retrieval.get("vector_name"),
        "parser_name": metadata.get("parser_name"),
        "source_parser": retrieval.get("source_parser"),
        "score_vector": scores.get("score_vector"),
        "score_bm25": scores.get("score_bm25"),
        "score_rrf": scores.get("score_rrf"),
        "rerank_score": scores.get("rerank_score"),
        "score_composite": scores.get("score_composite"),
        "score_policy": scores.get("score_policy"),
        "score_stage": scores.get("score_stage"),
        "score_threshold": scores.get("score_threshold"),
        "score_threshold_passed": scores.get("score_threshold_passed"),
    }
    for key in (
        "retrieval_candidate_pool",
        "retrieval_expanded_from_node_id",
        "retrieval_expansion_relation",
        "retrieval_expansion_mode",
        "retrieval_expansion_allowed_by",
        "retrieval_seed_rank",
        "retrieval_seed_threshold",
        "agent_relevance_score",
        "agent_relevance_label",
        "agent_relevance_keep",
        "agent_relevance_drop",
        "agent_label_score_delta",
        "agent_related_modality_delta",
        "agent_related_context_text_delta",
        "agent_related_context_node_id",
        "agent_related_context_present",
        "agent_related_context_added",
        "agent_related_context_fixed_score",
        "agent_relevance_reasoning",
    ):
        flat[key] = metadata.get(key)
    return flat


def snapshot_hits_by_stage(snapshots: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    stages: dict[str, list[dict[str, Any]]] = {}
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        stage = str(snapshot.get("stage") or "")
        if not stage:
            continue
        hits = snapshot.get("hits")
        if not isinstance(hits, list):
            continue
        stages[stage] = [
            _flatten_serialized_hit(hit)
            for hit in hits
            if isinstance(hit, dict)
        ]
    return stages


def _stage(stages: dict[str, list[dict[str, Any]]], *names: str) -> list[dict[str, Any]]:
    for name in names:
        if name in stages:
            return stages[name]
    return []


class EvaluationPipeline:
    """Observable query pipeline used only by the check module."""

    def __init__(self) -> None:
        from agentic_rag.config import get_settings
        from agentic_rag.generation.prompt_builder import PromptBuilder
        from agentic_rag.models.providers import build_embedding_provider, build_llm_client, build_reranker
        from agentic_rag.retrieval.rerank import RerankService
        from agentic_rag.retrieval.retriever import MultiChannelRetriever, VectorRetriever
        from agentic_rag.store.qdrant_store import QdrantStore

        self.settings = get_settings()
        self.embedding_provider = build_embedding_provider(self.settings)
        self.rerank_service = RerankService(settings=self.settings, reranker=build_reranker(self.settings))
        self.llm_client = build_llm_client(self.settings)
        self.store = QdrantStore(self.settings)
        vector_retriever = VectorRetriever(settings=self.settings, store=self.store)
        self.retriever = MultiChannelRetriever(
            settings=self.settings,
            store=self.store,
            vector_retriever=vector_retriever,
        )
        self.prompt_builder = PromptBuilder(settings=self.settings)

    def _should_use_qwen3_rerank(self) -> bool:
        return (
            self.settings.rerank_enabled
            and self.settings.rerank_model == "qwen3-rerank"
            and "compatible-api" in self.settings.rerank_base_url
        )

    def _copy_hit_with_score(self, hit: Any, score: float) -> Any:
        if hasattr(hit, "model_copy"):
            copied = hit.model_copy(deep=True)
        else:
            copied = hit
        copied.score = score
        return copied

    def _qwen3_rerank(self, question: str, hits: list[Any]) -> tuple[list[Any], bool, str | None, dict[str, Any]]:
        debug = {
            "rerank_input_count": len(hits),
            "rerank_candidate_limit": 500,
        }
        if not hits:
            return [], False, None, debug
        candidate_hits = [hit for hit in hits[:500] if str(getattr(hit, "text", "") or "").strip()]
        debug["rerank_text_candidate_count"] = len(candidate_hits)
        if not candidate_hits:
            return hits[: self.settings.context_top_n], False, "qwen3_rerank_no_text_candidates", debug
        top_n = min(self.settings.rerank_top_n, len(candidate_hits))
        debug["rerank_requested_top_n"] = top_n
        client = OpenAICompatibleClient(
            base_url=self.settings.rerank_base_url,
            api_key=self.settings.rerank_api_key,
            timeout=self.settings.rerank_timeout_sec,
        )
        payload = {
            "model": self.settings.rerank_model,
            "query": question,
            "documents": [hit.text for hit in candidate_hits],
            "top_n": top_n,
            "instruct": "Given a web search query, retrieve relevant passages that answer the query.",
        }
        data = client.post("reranks", payload)
        rows = data.get("results")
        if not isinstance(rows, list):
            raise ProviderError("Qwen3 rerank response missing results list")
        picked: list[Any] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            index = row.get("index")
            score = row.get("relevance_score")
            if not isinstance(index, int) or index < 0 or index >= len(candidate_hits):
                continue
            hit = candidate_hits[index]
            if isinstance(score, (int, float)):
                hit = self._copy_hit_with_score(hit, float(score))
            picked.append(hit)
        if not picked:
            return hits[: self.settings.context_top_n], False, "qwen3_rerank_empty", debug
        debug["rerank_output_count"] = len(picked[: self.settings.context_top_n])
        return picked[: self.settings.context_top_n], True, None, debug

    def _rerank_hits(self, question: str, hits: list[Any]) -> tuple[list[Any], bool, str | None, dict[str, Any]]:
        if self._should_use_qwen3_rerank():
            try:
                return self._qwen3_rerank(question, hits)
            except Exception as exc:
                return hits[: self.settings.context_top_n], False, f"qwen3_rerank_failed: {exc}", {
                    "rerank_input_count": len(hits),
                    "rerank_candidate_limit": 500,
                }
        result = self.rerank_service.rerank(question, hits)
        return result.hits, result.used_rerank, result.fallback_reason, {
            "rerank_input_count": len(hits),
            "rerank_requested_top_n": min(self.settings.rerank_top_n, len(hits)),
            "rerank_output_count": len(result.hits),
        }

    def run(self, question: str, filters: dict[str, Any] | None = None) -> QueryRun:
        timings: dict[str, float] = {}
        total_started = time.perf_counter()

        started = time.perf_counter()
        vectors = self.embedding_provider.embed_texts([question])
        timings["embedding_time_ms"] = (time.perf_counter() - started) * 1000
        if not vectors:
            raise RuntimeError("query embedding returned empty vectors")

        started = time.perf_counter()
        retrieval_result = self.retriever.retrieve(
            query_text=question,
            query_vector=vectors[0],
            filters=filters,
        )
        initial_hits = retrieval_result.hits
        local_recheck_hits = retrieval_result.expanded_hits or retrieval_result.hits
        timings["retrieval_time_ms"] = (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        reranked_hits, used_rerank, rerank_fallback_reason, rerank_debug = self._rerank_hits(question, local_recheck_hits)
        timings["rerank_time_ms"] = (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        context, citations = self.prompt_builder.build_context(reranked_hits)
        prompt = self.prompt_builder.build_prompt(question=question, context=context)
        timings["prompt_build_time_ms"] = (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        if citations:
            answer = self.llm_client.generate(prompt)
            if not answer:
                answer = self.settings.uncertain_answer_text
        else:
            answer = self.settings.uncertain_answer_text
        timings["generation_time_ms"] = (time.perf_counter() - started) * 1000
        timings["response_time_ms"] = (time.perf_counter() - total_started) * 1000

        prompt_tokens = estimate_tokens(prompt, self.settings.llm_model)
        answer_tokens = estimate_tokens(answer, self.settings.llm_model)
        token_usage = {
            "query_tokens_est": estimate_tokens(question, self.settings.llm_model),
            "prompt_tokens_est": prompt_tokens,
            "answer_tokens_est": answer_tokens,
            "total_tokens_est": prompt_tokens + answer_tokens,
        }
        citation_dicts = [item.model_dump() if hasattr(item, "model_dump") else dict(item) for item in citations]
        context_count = len(citation_dicts)

        return QueryRun(
            answer=answer,
            citations=citation_dicts,
            initial_hits=[hit_to_dict(hit, i) for i, hit in enumerate(initial_hits, start=1)],
            initial_expanded_hits=[hit_to_dict(hit, i) for i, hit in enumerate(local_recheck_hits, start=1)],
            retrieved_hits=[hit_to_dict(hit, i) for i, hit in enumerate(local_recheck_hits, start=1)],
            reranked_hits=[hit_to_dict(hit, i) for i, hit in enumerate(reranked_hits, start=1)],
            agent_graded_hits=[],
            evidence_gate_hits=[],
            retry_hits=[],
            final_hits=[hit_to_dict(hit, i) for i, hit in enumerate(reranked_hits[:context_count], start=1)],
            local_recheck_hits=[hit_to_dict(hit, i) for i, hit in enumerate(local_recheck_hits, start=1)],
            context_hits=[hit_to_dict(hit, i) for i, hit in enumerate(reranked_hits[:context_count], start=1)],
            ranked_hits_by_stage={
                "initial_retrieval": [hit_to_dict(hit, i) for i, hit in enumerate(initial_hits, start=1)],
                "initial_expanded": [hit_to_dict(hit, i) for i, hit in enumerate(local_recheck_hits, start=1)],
                "initial_recall": [hit_to_dict(hit, i) for i, hit in enumerate(initial_hits, start=1)],
                "rerank": [hit_to_dict(hit, i) for i, hit in enumerate(reranked_hits, start=1)],
                "local_recheck": [hit_to_dict(hit, i) for i, hit in enumerate(local_recheck_hits, start=1)],
                "final_after_retry": [hit_to_dict(hit, i) for i, hit in enumerate(reranked_hits[:context_count], start=1)],
                "final_output": [hit_to_dict(hit, i) for i, hit in enumerate(reranked_hits[:context_count], start=1)],
            },
            retrieved_count=len(local_recheck_hits),
            timings_ms=timings,
            token_usage=token_usage,
            debug={
                "used_rerank": used_rerank,
                "rerank_fallback_reason": rerank_fallback_reason,
                "initial_hit_count": len(initial_hits),
                "local_recheck_hit_count": len(local_recheck_hits),
                "rerank_hit_count": len(reranked_hits),
                **rerank_debug,
            },
        )


class TaskGraphEvaluationPipeline:
    """Evaluation pipeline that executes the full TaskGraph path."""

    def __init__(self) -> None:
        from agentic_rag.config import get_settings
        from agentic_rag.generation.prompt_builder import PromptBuilder
        from agentic_rag.graph.task_graph import TaskGraphRAG
        from agentic_rag.models.providers import build_embedding_provider, build_llm_client, build_reranker
        from agentic_rag.retrieval.rerank import RerankService
        from agentic_rag.retrieval.retriever import MultiChannelRetriever, VectorRetriever
        from agentic_rag.store.qdrant_store import QdrantStore

        base_settings = get_settings()
        self.settings = copy(base_settings)
        self.settings.retrieval_eval_snapshots_enabled = True
        self.embedding_provider = build_embedding_provider(self.settings)
        self.llm_client = build_llm_client(self.settings)
        self.store = QdrantStore(self.settings)
        vector_retriever = VectorRetriever(settings=self.settings, store=self.store)
        retriever = MultiChannelRetriever(
            settings=self.settings,
            store=self.store,
            vector_retriever=vector_retriever,
        )
        self.graph = TaskGraphRAG(
            settings=self.settings,
            embedding_provider=self.embedding_provider,
            retriever=retriever,
            rerank_service=RerankService(settings=self.settings, reranker=build_reranker(self.settings)),
            llm_client=self.llm_client,
            prompt_builder=PromptBuilder(settings=self.settings),
        )

    def run(self, question: str, filters: dict[str, Any] | None = None) -> QueryRun:
        started = time.perf_counter()
        result = self.graph.invoke(question, filters=filters)
        response_time_ms = (time.perf_counter() - started) * 1000
        snapshots = list(result.retrieval_eval_snapshots or [])
        stages = snapshot_hits_by_stage(snapshots)
        final_hits = _stage(stages, "final_after_retry", "final_output")
        context_count = len(result.citations)
        context_hits = _stage(stages, "final_output") or final_hits[:context_count]
        ranked_hits_by_stage = dict(stages)
        ranked_hits_by_stage.setdefault("initial_recall", _stage(stages, "initial_retrieval"))
        ranked_hits_by_stage.setdefault("local_recheck", _stage(stages, "final_after_retry", "retry_1_expanded", "initial_expanded"))
        ranked_hits_by_stage.setdefault("final_output", context_hits)
        token_usage = {
            "query_tokens_est": estimate_tokens(question, self.settings.llm_model),
            "answer_tokens_est": estimate_tokens(result.answer, self.settings.llm_model),
        }
        token_usage["total_tokens_est"] = token_usage["query_tokens_est"] + token_usage["answer_tokens_est"]
        debug = {
            **result.debug,
            "pipeline": "taskgraph",
            "timings_ms": {"response_time_ms": response_time_ms},
            "snapshot_stage_counts": {stage: len(hits) for stage, hits in ranked_hits_by_stage.items()},
        }
        return QueryRun(
            answer=result.answer,
            citations=[item.model_dump() if hasattr(item, "model_dump") else dict(item) for item in result.citations],
            initial_hits=_stage(stages, "initial_retrieval"),
            initial_expanded_hits=_stage(stages, "initial_expanded"),
            retrieved_hits=_stage(stages, "initial_expanded", "initial_retrieval"),
            reranked_hits=_stage(stages, "rerank"),
            agent_graded_hits=_stage(stages, "agent_chunk_grading"),
            evidence_gate_hits=_stage(stages, "evidence_gate"),
            retry_hits=_stage(stages, "retry_1_expanded", "retry_1_retrieval"),
            final_hits=final_hits,
            local_recheck_hits=ranked_hits_by_stage.get("local_recheck", []),
            context_hits=context_hits,
            ranked_hits_by_stage=ranked_hits_by_stage,
            retrieved_count=result.retrieved_count,
            timings_ms={"response_time_ms": response_time_ms},
            token_usage=token_usage,
            debug=debug,
        )
