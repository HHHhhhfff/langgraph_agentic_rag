from __future__ import annotations

from agentic_rag.config import get_settings
from agentic_rag.generation.prompt_builder import PromptBuilder
from agentic_rag.graph.rag_graph import RAGGraph
from agentic_rag.models.providers import (
    build_embedding_provider,
    build_llm_client,
    build_reranker,
)
from agentic_rag.retrieval.rerank import RerankService
from agentic_rag.retrieval.retriever import VectorRetriever
from agentic_rag.store.qdrant_store import QdrantStore


def build_rag_graph() -> RAGGraph:
    """Create configured RAG graph instance."""

    settings = get_settings()
    embedding = build_embedding_provider(settings)
    reranker = build_reranker(settings)
    llm = build_llm_client(settings)
    store = QdrantStore(settings)
    retriever = VectorRetriever(settings=settings, store=store)
    rerank_service = RerankService(settings=settings, reranker=reranker)
    prompt_builder = PromptBuilder(settings=settings)

    return RAGGraph(
        settings=settings,
        embedding_provider=embedding,
        retriever=retriever,
        rerank_service=rerank_service,
        llm_client=llm,
        prompt_builder=prompt_builder,
    )
