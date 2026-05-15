from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentic_rag.evaluation.cases import EvalCase, EvalSummary
from agentic_rag.evaluation.evaluator import evaluate_results
from agentic_rag.schemas import RAGResult


def load_eval_cases(path: Path) -> list[EvalCase]:
    """Load JSONL evaluation cases."""

    cases: list[EvalCase] = []
    for line_no, line in _iter_jsonl(path):
        try:
            cases.append(EvalCase.model_validate(json.loads(line)))
        except Exception as exc:  # pragma: no cover - exact pydantic errors are version-specific
            raise ValueError(f"Invalid eval case at {path}:{line_no}: {exc}") from exc
    return cases


def load_eval_results(path: Path) -> dict[str, RAGResult]:
    """Load offline eval results from JSONL or a JSON array."""

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    payloads: list[dict[str, Any]]
    if text.startswith("["):
        raw = json.loads(text)
        if not isinstance(raw, list):
            raise ValueError("Result JSON must be an array or JSONL records")
        payloads = [item for item in raw if isinstance(item, dict)]
    else:
        payloads = [json.loads(line) for _line_no, line in _iter_jsonl(path)]

    results: dict[str, RAGResult] = {}
    for payload in payloads:
        case_id = str(payload.get("case_id") or payload.get("id") or "")
        if not case_id:
            raise ValueError("Every result record must include case_id or id")
        result_payload = payload.get("result") if isinstance(payload.get("result"), dict) else payload
        results[case_id] = RAGResult.model_validate(result_payload)
    return results


def run_offline_eval(
    cases: list[EvalCase],
    results: dict[str, RAGResult],
    *,
    uncertain_answer_text: str | None = None,
) -> EvalSummary:
    """Evaluate already-produced RAG results without external services."""

    return evaluate_results(cases, results, uncertain_answer_text=uncertain_answer_text)


def run_live_eval(
    cases: list[EvalCase],
    graph: Any,
    *,
    uncertain_answer_text: str | None = None,
) -> EvalSummary:
    """Run the configured graph for each case, then evaluate the outputs."""

    results: dict[str, RAGResult] = {}
    for case in cases:
        results[case.id] = graph.invoke(question=case.question, filters=case.filters)
    return run_offline_eval(cases, results, uncertain_answer_text=uncertain_answer_text)


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            yield line_no, line
