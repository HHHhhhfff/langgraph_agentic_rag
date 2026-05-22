from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from agentic_rag.models.providers import OpenAICompatibleClient, ProviderError
from check.metrics import estimate_tokens


@dataclass(slots=True)
class QueryRun:
    answer: str
    citations: list[dict[str, Any]]
    initial_hits: list[dict[str, Any]]
    retrieved_hits: list[dict[str, Any]]
    reranked_hits: list[dict[str, Any]]
    local_recheck_hits: list[dict[str, Any]]
    context_hits: list[dict[str, Any]]
    retrieved_count: int
    timings_ms: dict[str, float]
    token_usage: dict[str, int]
    debug: dict[str, Any]


def hit_to_dict(hit: Any, rank: int) -> dict[str, Any]:
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
            retrieved_hits=[hit_to_dict(hit, i) for i, hit in enumerate(local_recheck_hits, start=1)],
            reranked_hits=[hit_to_dict(hit, i) for i, hit in enumerate(reranked_hits, start=1)],
            local_recheck_hits=[hit_to_dict(hit, i) for i, hit in enumerate(local_recheck_hits, start=1)],
            context_hits=[hit_to_dict(hit, i) for i, hit in enumerate(reranked_hits[:context_count], start=1)],
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
