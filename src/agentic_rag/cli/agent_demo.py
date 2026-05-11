from __future__ import annotations

import argparse
import json
import sys
from typing import TypedDict

from langgraph.graph import END, StateGraph

from agentic_rag.agent.tools import build_rag_tool
from agentic_rag.factory import build_rag_graph


class AgentState(TypedDict, total=False):
    """Typed state for demo agent graph."""

    question: str
    source: str | None
    tag: str | None
    tool_output: str


def main() -> int:
    parser = argparse.ArgumentParser(description="Run demo LangGraph agent calling RAG tool")
    parser.add_argument("question", type=str, help="User question")
    parser.add_argument("--source", type=str, default=None, help="Optional source filter")
    parser.add_argument("--tag", type=str, default=None, help="Optional tag filter")
    args = parser.parse_args()

    try:
        rag_graph = build_rag_graph()
    except Exception as exc:
        print(f"[ERROR] Failed to init RAG graph: {exc}", file=sys.stderr)
        return 1

    rag_tool = build_rag_tool(rag_graph)

    def call_rag_node(state: AgentState) -> AgentState:
        q = state.get("question", "")
        source = state.get("source")
        tag = state.get("tag")
        output = rag_tool.invoke({"question": q, "source": source, "tag": tag})
        return {"tool_output": output}

    graph = StateGraph(AgentState)
    graph.add_node("call_rag", call_rag_node)
    graph.set_entry_point("call_rag")
    graph.add_edge("call_rag", END)
    app = graph.compile()

    try:
        result = app.invoke({"question": args.question, "source": args.source, "tag": args.tag})
    except Exception as exc:
        print(f"[ERROR] Agent execution failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
