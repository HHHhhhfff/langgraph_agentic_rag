from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentic_rag.config import Settings, get_settings
from agentic_rag.evaluation.retrieval_visualization import (
    RetrievalVisualizationError,
    load_history_records,
    select_history_record,
    write_retrieval_visualization_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render retrieval visualization reports from retrieval_history.jsonl")
    parser.add_argument("--latest", action="store_true", help="Visualize the latest retrieval history record")
    parser.add_argument("--query-id", type=str, default=None, help="Visualize a specific query_id")
    parser.add_argument("--history", type=str, default=None, help="Path to retrieval_history.jsonl")
    parser.add_argument("--output", type=str, default=None, help="Output root directory")
    parser.add_argument("--max-text-chars", type=int, default=None, help="Max chars shown per hit text")
    parser.add_argument(
        "--include-full-vectors",
        dest="include_full_vectors",
        action="store_true",
        default=None,
        help="Include full vector-like fields in raw_record.json",
    )
    parser.add_argument(
        "--no-include-full-vectors",
        dest="include_full_vectors",
        action="store_false",
        help="Omit full vector-like fields from raw_record.json",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    args = parser.parse_args()

    if args.latest and args.query_id:
        print("[ERROR] Use either --latest or --query-id, not both.", file=sys.stderr)
        return 2

    settings = get_settings()
    history_path = Path(args.history or Path(settings.retrieval_eval_log_dir) / settings.retrieval_eval_log_file)
    output_dir = Path(args.output or settings.retrieval_vis_output_dir)

    try:
        records = load_history_records(history_path)
        record = select_history_record(records, latest=args.latest or not args.query_id, query_id=args.query_id)
        source_mode = "history_query_id" if args.query_id else "history_latest"
        run_dir = write_retrieval_visualization_report(
            record,
            settings=settings,
            output_dir=output_dir,
            source_mode=source_mode,
            history_path=history_path,
            max_text_chars=args.max_text_chars or settings.retrieval_vis_max_text_chars,
            include_full_vectors=(
                settings.retrieval_vis_include_full_vectors
                if args.include_full_vectors is None
                else args.include_full_vectors
            ),
        )
    except RetrievalVisualizationError as exc:
        print(f"[ERROR] Retrieval inspect failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[ERROR] Retrieval inspect failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "run_dir": str(run_dir),
                    "query_id": record.get("query_id"),
                    "query_text": (record.get("query") or {}).get("text"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print("Retrieval visualization completed")
    print(f"- run_dir: {run_dir}")
    print(f"- query_id: {record.get('query_id')}")
    print(f"- open: chunks.html / stage_compare.html / rank_flow.html / citations.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
