from __future__ import annotations

import argparse
import json
import sys

from agentic_rag.cli.debug_format import build_query_debug_lines
from agentic_rag.config import get_settings
from agentic_rag.factory import build_rag_graph


def main() -> int:
    parser = argparse.ArgumentParser(description="Run RAG query with LangGraph pipeline")
    parser.add_argument("question", type=str, help="User question")
    parser.add_argument("--source", type=str, default=None, help="Optional metadata source filter")
    parser.add_argument("--tag", type=str, default=None, help="Optional metadata tag filter")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    settings = get_settings()
    graph = build_rag_graph()
    filters = {}
    if args.source:
        filters["source"] = args.source
    if args.tag:
        filters["tags"] = [args.tag]

    try:
        result = graph.invoke(question=args.question, filters=filters or None)
    except Exception as exc:
        print(f"[ERROR] Query failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
        return 0

    print("Answer:")
    print(result.answer)
    print("\nCitations:")
    if not result.citations:
        print("- (none)")
    for c in result.citations:
        print(
            f"- [{c.index}] source={c.source}; title={c.title}; "
            f"chunk_index={c.chunk_index}; score={c.score:.4f}; tags={c.tags}"
        )

    print()
    for line in build_query_debug_lines(result, settings):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
