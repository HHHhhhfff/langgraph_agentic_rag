from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from agentic_rag.config import get_settings
from agentic_rag.ingestion.index_build_service import (
    IndexBuildOptions,
    build_index_from_docs,
    print_index_build_summary,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Qdrant index from markdown documents")
    parser.add_argument("--docs", type=str, required=True, help="Markdown directory path")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    settings = get_settings()
    try:
        build_result = build_index_from_docs(
            args.docs,
            settings=settings,
            options=IndexBuildOptions(
                recreate_collection=settings.qdrant_recreate_collection,
                write_local_index=settings.retrieval_index_persist_enabled,
                emit_logs=True,
                source_label="build_index",
            ),
        )
        summary = build_result.summary
    except Exception as exc:
        print(f"[ERROR] Index build failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
        return 0

    print_index_build_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
