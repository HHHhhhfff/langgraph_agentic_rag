from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from agentic_rag.graph.rag_graph import RAGGraph
from agentic_rag.models.providers import EmbeddingProvider
from agentic_rag.retrieval.bm25_retriever import BM25Retriever
from agentic_rag.retrieval.hybrid import HybridRetriever
from agentic_rag.retrieval.page_retriever import PageRetriever
from agentic_rag.retrieval.table_retriever import TableRetriever


class RetrievalToolInput(BaseModel):
    question: str = Field(description="User question")
    source: str | None = Field(default=None, description="Optional source filter")
    tag: str | None = Field(default=None, description="Optional tag filter")
    page: int | None = Field(default=None, description="Optional page filter")


def _build_filters(source: str | None = None, tag: str | None = None, page: int | None = None) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if source:
        filters["source"] = source
    if tag:
        filters["tags"] = [tag]
    if page is not None:
        filters["page"] = page
    return filters


def build_rag_tool(rag_graph: RAGGraph) -> StructuredTool:
    def _run(question: str, source: str | None = None, tag: str | None = None, page: int | None = None) -> str:
        result = rag_graph.invoke(question=question, filters=_build_filters(source, tag, page) or None)
        lines = ["answer:", result.answer, "", "citations:"]
        if not result.citations:
            lines.append("- (none)")
        else:
            for c in result.citations:
                lines.append(
                    f"- [{c.index}] source={c.source}; title={c.title}; chunk_index={c.chunk_index}; score={c.score:.4f}"
                )
        return "\n".join(lines)

    return StructuredTool.from_function(
        name="rag_search_answer",
        description="Search knowledge base and answer with citations",
        func=_run,
        args_schema=RetrievalToolInput,
    )


def build_bm25_tool(retriever: BM25Retriever) -> StructuredTool:
    def _run(question: str, source: str | None = None, tag: str | None = None, page: int | None = None) -> str:
        hits = retriever.retrieve(question, filters=_build_filters(source, tag, page))
        return "\n".join([f"{i+1}. {h.text[:200]}" for i, h in enumerate(hits)]) or "(empty)"

    return StructuredTool.from_function(
        name="bm25_search",
        description="Keyword search over knowledge base",
        func=_run,
        args_schema=RetrievalToolInput,
    )


def build_page_tool(retriever: PageRetriever) -> StructuredTool:
    def _run(question: str, source: str | None = None, tag: str | None = None, page: int | None = None) -> str:
        filters = _build_filters(source, tag, page)
        hits = retriever.retrieve(question, page=page, filters=filters)
        return "\n".join([f"{i+1}. page={h.page} {h.text[:200]}" for i, h in enumerate(hits)]) or "(empty)"

    return StructuredTool.from_function(
        name="page_search",
        description="Page-level retrieval",
        func=_run,
        args_schema=RetrievalToolInput,
    )


def build_table_tool(retriever: TableRetriever) -> StructuredTool:
    def _run(question: str, source: str | None = None, tag: str | None = None, page: int | None = None) -> str:
        hits = retriever.retrieve(question, filters=_build_filters(source, tag, page))
        return "\n".join([f"{i+1}. {h.table_markdown[:200]}" for i, h in enumerate(hits)]) or "(empty)"

    return StructuredTool.from_function(
        name="table_search",
        description="Table-only retrieval",
        func=_run,
        args_schema=RetrievalToolInput,
    )


def build_hybrid_tool(retriever: HybridRetriever, embedding_provider: EmbeddingProvider) -> StructuredTool:
    def _run(question: str, source: str | None = None, tag: str | None = None, page: int | None = None) -> str:
        query_vector = embedding_provider.embed_texts([question])[0]
        result = retriever.retrieve(
            query_text=question,
            query_vector=query_vector,
            filters=_build_filters(source, tag, page) or None,
        )
        lines = ["hybrid_hits:"]
        for i, h in enumerate(result.hits, start=1):
            lines.append(f"{i}. [{h.channel}] page={h.page} score={h.score:.4f} {h.text[:150]}")
        return "\n".join(lines)

    return StructuredTool.from_function(
        name="hybrid_search",
        description="Hybrid retrieval with vector + BM25 + page + table fusion",
        func=_run,
        args_schema=RetrievalToolInput,
    )


def build_retrieval_tools(
    *,
    rag_graph: RAGGraph,
    bm25: BM25Retriever,
    page: PageRetriever,
    table: TableRetriever,
    hybrid: HybridRetriever,
    embedding_provider: EmbeddingProvider,
) -> list[StructuredTool]:
    return [
        build_rag_tool(rag_graph),
        build_bm25_tool(bm25),
        build_page_tool(page),
        build_table_tool(table),
        build_hybrid_tool(hybrid, embedding_provider),
    ]
