from __future__ import annotations

from agentic_rag.config import get_settings
from agentic_rag.generation.prompt_builder import PromptBuilder
from agentic_rag.graph.rag_graph import RAGGraph
from agentic_rag.graph.task_graph import TaskGraphRAG
from agentic_rag.models.providers import (
    build_embedding_provider,
    build_llm_client,
    build_reranker,
)
from agentic_rag.retrieval.rerank import RerankService
from agentic_rag.retrieval.retriever import MultiChannelRetriever, VectorRetriever
from agentic_rag.store.qdrant_store import QdrantStore


def _build_shared_components():
    settings = get_settings()
    embedding = build_embedding_provider(settings)
    reranker = build_reranker(settings)
    llm = build_llm_client(settings)
    store = QdrantStore(settings)
    vector_retriever = VectorRetriever(settings=settings, store=store)
    retriever = MultiChannelRetriever(settings=settings, store=store, vector_retriever=vector_retriever)
    rerank_service = RerankService(settings=settings, reranker=reranker)
    prompt_builder = PromptBuilder(settings=settings)
    return settings, embedding, llm, retriever, rerank_service, prompt_builder


def build_task_graph() -> TaskGraphRAG:
    """Create configured TaskGraph RAG instance."""

    settings, embedding, llm, retriever, _rerank_service, prompt_builder = _build_shared_components()
    return TaskGraphRAG(
        settings=settings,
        embedding_provider=embedding,
        retriever=retriever,
        rerank_service=_rerank_service,
        llm_client=llm,
        prompt_builder=prompt_builder,
    )


def build_rag_graph() -> RAGGraph | TaskGraphRAG:
    """Create configured graph instance with optional TaskGraph path."""

    settings, embedding, llm, retriever, rerank_service, prompt_builder = _build_shared_components()
    if settings.taskgraph_enabled:
        return TaskGraphRAG(
            settings=settings,
            embedding_provider=embedding,
            retriever=retriever,
            rerank_service=rerank_service,
            llm_client=llm,
            prompt_builder=prompt_builder,
        )
    return RAGGraph(
        settings=settings,
        embedding_provider=embedding,
        retriever=retriever,
        rerank_service=rerank_service,
        llm_client=llm,
        prompt_builder=prompt_builder,
    )

