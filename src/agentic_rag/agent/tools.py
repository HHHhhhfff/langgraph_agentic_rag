from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from agentic_rag.graph.rag_graph import RAGGraph


class RAGToolInput(BaseModel):
    """Input schema for RAG tool call."""

    question: str = Field(description="User question for RAG")
    source: str | None = Field(default=None, description="Optional source filter")
    tag: str | None = Field(default=None, description="Optional tag filter")


def build_rag_tool(rag_graph: RAGGraph) -> StructuredTool:
    """Wrap RAG graph invocation as a LangChain/LangGraph tool."""

    def _run(question: str, source: str | None = None, tag: str | None = None) -> str:
        filters: dict[str, Any] = {}
        if source:
            filters["source"] = source
        if tag:
            filters["tags"] = [tag]
        result = rag_graph.invoke(question=question, filters=filters or None)
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
        args_schema=RAGToolInput,
    )
