from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from agentic_rag.config import Settings
from agentic_rag.generation.prompt_builder import PromptBuilder
from agentic_rag.models.providers import EmbeddingProvider, LLMClient
from agentic_rag.retrieval.rerank import RerankService
from agentic_rag.retrieval.retriever import MultiChannelRetriever, VectorRetriever
from agentic_rag.schemas import RAGResult, SearchHit


class RAGGraphState(TypedDict, total=False):
    """LangGraph state for RAG query pipeline."""

    question: str
    filters: dict[str, Any] | None
    query_vector: list[float]
    retrieved_hits: list[SearchHit]
    fallback_used: bool
    reranked_hits: list[SearchHit]
    used_rerank: bool
    rerank_fallback_reason: str | None
    context: str
    citations: list[dict[str, Any]]
    prompt: str
    answer: str
    result: RAGResult


class RAGGraph:
    """StateGraph-based RAG execution graph."""

    def __init__(
        self,
        settings: Settings,
        embedding_provider: EmbeddingProvider,
        retriever: VectorRetriever | MultiChannelRetriever,
        rerank_service: RerankService,
        llm_client: LLMClient,
        prompt_builder: PromptBuilder,
    ):
        self.settings = settings
        self.embedding_provider = embedding_provider
        self.retriever = retriever
        self.rerank_service = rerank_service
        self.llm_client = llm_client
        self.prompt_builder = prompt_builder
        self.graph = self._compile_graph()

    def _embed_query_node(self, state: RAGGraphState) -> RAGGraphState:
        question = state.get("question", "").strip()
        if not question:
            raise ValueError("question cannot be empty")
        vectors = self.embedding_provider.embed_texts([question])
        if not vectors:
            raise RuntimeError("query embedding returned empty vectors")
        return {"query_vector": vectors[0]}

    def _retrieve_node(self, state: RAGGraphState) -> RAGGraphState:
        question = state.get("question", "")
        query_vector = state["query_vector"]
        filters = state.get("filters")
        if isinstance(self.retriever, MultiChannelRetriever):
            result = self.retriever.retrieve(
                query_text=question,
                query_vector=query_vector,
                filters=filters,
            )
            hits = result.expanded_hits or result.hits
            fallback_used = False
        else:
            base = self.retriever.retrieve(query_vector=query_vector, filters=filters)
            hits = base.hits
            fallback_used = base.fallback_used
        return {
            "retrieved_hits": hits,
            "fallback_used": fallback_used,
        }

    def _rerank_node(self, state: RAGGraphState) -> RAGGraphState:
        question = state["question"]
        hits = state.get("retrieved_hits", [])
        result = self.rerank_service.rerank(question, hits)
        return {
            "reranked_hits": result.hits,
            "used_rerank": result.used_rerank,
            "rerank_fallback_reason": result.fallback_reason,
        }

    def _build_prompt_node(self, state: RAGGraphState) -> RAGGraphState:
        hits = state.get("reranked_hits", [])
        context, citations = self.prompt_builder.build_context(hits)
        prompt = self.prompt_builder.build_prompt(question=state["question"], context=context)
        return {
            "context": context,
            "citations": [c.model_dump() for c in citations],
            "prompt": prompt,
        }

    def _generate_node(self, state: RAGGraphState) -> RAGGraphState:
        citations = state.get("citations", [])
        if not citations:
            return {"answer": self.settings.uncertain_answer_text}
        answer = self.llm_client.generate(state["prompt"])
        if not answer:
            answer = self.settings.uncertain_answer_text
        return {"answer": answer}

    def _finalize_node(self, state: RAGGraphState) -> RAGGraphState:
        result = RAGResult(
            answer=state.get("answer", self.settings.uncertain_answer_text),
            citations=[],
            retrieved_count=len(state.get("retrieved_hits", [])),
            used_rerank=bool(state.get("used_rerank", False)),
            fallback_used=bool(state.get("fallback_used", False)),
            debug={
                "rerank_fallback_reason": state.get("rerank_fallback_reason"),
            },
        )
        for row in state.get("citations", []):
            try:
                from agentic_rag.schemas import Citation

                result.citations.append(Citation(**row))
            except Exception:
                continue
        return {"result": result}

    def _compile_graph(self):
        graph = StateGraph(RAGGraphState)
        graph.add_node("embed_query", self._embed_query_node)
        graph.add_node("retrieve", self._retrieve_node)
        graph.add_node("rerank", self._rerank_node)
        graph.add_node("build_prompt", self._build_prompt_node)
        graph.add_node("generate", self._generate_node)
        graph.add_node("finalize", self._finalize_node)

        graph.set_entry_point("embed_query")
        graph.add_edge("embed_query", "retrieve")
        graph.add_edge("retrieve", "rerank")
        graph.add_edge("rerank", "build_prompt")
        graph.add_edge("build_prompt", "generate")
        graph.add_edge("generate", "finalize")
        graph.add_edge("finalize", END)

        return graph.compile()

    def invoke(self, question: str, filters: dict[str, Any] | None = None) -> RAGResult:
        state: RAGGraphState = {
            "question": question,
            "filters": filters,
        }
        output = self.graph.invoke(state)
        result = output.get("result")
        if not isinstance(result, RAGResult):
            raise RuntimeError("RAG graph did not produce a valid result")
        return result
