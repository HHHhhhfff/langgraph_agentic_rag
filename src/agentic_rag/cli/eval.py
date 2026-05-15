from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentic_rag.config import get_settings
from agentic_rag.evaluation.runner import load_eval_cases, load_eval_results, run_live_eval, run_offline_eval
from agentic_rag.factory import build_rag_graph


def main() -> int:
    parser = argparse.ArgumentParser(description="Run offline or live Agentic RAG evaluation")
    parser.add_argument("--cases", type=Path, required=True, help="Path to eval cases JSONL")
    parser.add_argument("--results", type=Path, default=None, help="Path to offline RAG results JSONL/JSON")
    parser.add_argument("--live", action="store_true", help="Run live graph evaluation using current settings")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON summary")
    args = parser.parse_args()

    settings = get_settings()
    cases = load_eval_cases(args.cases)

    if args.live:
        print(
            "[WARN] live eval uses current Qdrant/LLM/embedding settings and may access external services.",
            file=sys.stderr,
        )
        summary = run_live_eval(cases, build_rag_graph(), uncertain_answer_text=settings.uncertain_answer_text)
    else:
        if args.results is None:
            print("[ERROR] --results is required unless --live is set", file=sys.stderr)
            return 2
        results = load_eval_results(args.results)
        summary = run_offline_eval(cases, results, uncertain_answer_text=settings.uncertain_answer_text)

    if args.json:
        print(json.dumps(summary.model_dump(), ensure_ascii=False, indent=2))
    else:
        _print_summary(summary)
    return 0 if summary.failed == 0 else 1


def _print_summary(summary) -> None:
    print("Eval Summary:")
    print(f"- total={summary.total}")
    print(f"- passed={summary.passed}")
    print(f"- failed={summary.failed}")
    print(f"- pass_rate={summary.pass_rate:.4f}")
    print("Categories:")
    for category, row in sorted(summary.category_breakdown.items()):
        print(
            f"- {category}: total={row['total']}; passed={row['passed']}; "
            f"failed={row['failed']}; pass_rate={row['pass_rate']:.4f}"
        )
    if summary.failures:
        print("Failures:")
        for failure in summary.failures:
            print(f"- {failure.case_id}: {failure.failure_reasons}")


if __name__ == "__main__":
    raise SystemExit(main())
