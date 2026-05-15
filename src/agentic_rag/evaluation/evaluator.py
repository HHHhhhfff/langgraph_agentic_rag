from __future__ import annotations

from typing import Any

from agentic_rag.evaluation.cases import EvalCase, EvalCaseResult, EvalSummary
from agentic_rag.schemas import RAGResult


def evaluate_result(
    case: EvalCase,
    result: RAGResult,
    *,
    uncertain_answer_text: str | None = None,
) -> EvalCaseResult:
    """Evaluate one RAGResult against a fixed regression case."""

    failures: list[str] = []
    answer = result.answer or ""
    answer_lower = answer.lower()
    debug = result.debug or {}

    _check_expected_keywords(case, answer_lower, failures)
    _check_forbidden_keywords(case, answer_lower, failures)
    _check_citation_sources(case, result, failures)
    _check_debug(case, debug, answer, uncertain_answer_text, failures)

    return EvalCaseResult(
        case_id=case.id,
        category=case.category,
        passed=not failures,
        failure_reasons=failures,
        metrics={
            "retrieved_count": result.retrieved_count,
            "citation_count": len(result.citations),
            "retry_count": int(debug.get("retry_count", 0) or 0),
            "support_score": _as_float(debug.get("support_score"), 0.0),
        },
    )


def evaluate_results(
    cases: list[EvalCase],
    results: dict[str, RAGResult],
    *,
    uncertain_answer_text: str | None = None,
) -> EvalSummary:
    """Evaluate multiple cases and build an aggregate summary."""

    case_results: list[EvalCaseResult] = []
    for case in cases:
        result = results.get(case.id)
        if result is None:
            case_results.append(
                EvalCaseResult(
                    case_id=case.id,
                    category=case.category,
                    passed=False,
                    failure_reasons=["missing_result"],
                )
            )
            continue
        case_results.append(evaluate_result(case, result, uncertain_answer_text=uncertain_answer_text))

    total = len(case_results)
    passed = sum(1 for item in case_results if item.passed)
    failed = total - passed
    return EvalSummary(
        total=total,
        passed=passed,
        failed=failed,
        pass_rate=round(passed / total, 4) if total else 0.0,
        category_breakdown=_category_breakdown(case_results),
        failures=[item for item in case_results if not item.passed],
    )


def _check_expected_keywords(case: EvalCase, answer_lower: str, failures: list[str]) -> None:
    keywords = [kw for kw in case.expected_answer_keywords if kw]
    if not keywords:
        return
    matches = [kw for kw in keywords if kw.lower() in answer_lower]
    if case.expected_answer_keywords_mode == "any":
        if not matches:
            failures.append(f"missing_any_expected_answer_keyword:{keywords}")
    else:
        missing = [kw for kw in keywords if kw.lower() not in answer_lower]
        if missing:
            failures.append(f"missing_expected_answer_keywords:{missing}")


def _check_forbidden_keywords(case: EvalCase, answer_lower: str, failures: list[str]) -> None:
    forbidden = [kw for kw in case.forbidden_answer_keywords if kw and kw.lower() in answer_lower]
    if forbidden:
        failures.append(f"forbidden_answer_keywords:{forbidden}")


def _check_citation_sources(case: EvalCase, result: RAGResult, failures: list[str]) -> None:
    if not case.expected_citation_sources:
        return
    actual = {citation.source for citation in result.citations}
    missing = [source for source in case.expected_citation_sources if source not in actual]
    if missing:
        failures.append(f"missing_citation_sources:{missing}")


def _check_debug(
    case: EvalCase,
    debug: dict[str, Any],
    answer: str,
    uncertain_answer_text: str | None,
    failures: list[str],
) -> None:
    expected = case.expected_debug
    if expected.route is not None and debug.get("route") != expected.route:
        failures.append(f"route_mismatch:expected={expected.route},actual={debug.get('route')}")
    if expected.route_any and debug.get("route") not in expected.route_any:
        failures.append(f"route_not_allowed:expected_any={expected.route_any},actual={debug.get('route')}")

    channels = set(_as_list(debug.get("executed_channels")))
    missing_channels = [channel for channel in expected.required_channels_all if channel not in channels]
    if missing_channels:
        failures.append(f"missing_required_channels:{missing_channels}")
    if expected.required_channels_any and not channels.intersection(expected.required_channels_any):
        failures.append(f"missing_any_required_channel:{expected.required_channels_any}")
    forbidden_channels = [channel for channel in expected.forbidden_channels if channel in channels]
    if forbidden_channels:
        failures.append(f"forbidden_channels_present:{forbidden_channels}")

    modalities = _covered_modalities(debug)
    if expected.required_modalities_any and not modalities.intersection(expected.required_modalities_any):
        failures.append(f"missing_any_required_modality:{expected.required_modalities_any}")

    gate_decision = debug.get("gate_decision")
    if expected.gate_decision is not None and gate_decision != expected.gate_decision:
        failures.append(f"gate_decision_mismatch:expected={expected.gate_decision},actual={gate_decision}")
    if expected.gate_decision_any and gate_decision not in expected.gate_decision_any:
        failures.append(f"gate_decision_not_allowed:expected_any={expected.gate_decision_any},actual={gate_decision}")

    _check_optional_bool(debug, "evidence_ok", expected.evidence_ok, failures)
    _check_optional_bool(debug, "citation_ok", expected.citation_ok, failures)
    _check_refusal(expected.should_refuse, debug, answer, uncertain_answer_text, failures)

    if expected.min_support_score is not None:
        actual = _as_float(debug.get("support_score"), 0.0)
        if actual < expected.min_support_score:
            failures.append(f"support_score_too_low:expected_min={expected.min_support_score},actual={actual}")

    retry_count = int(debug.get("retry_count", 0) or 0)
    if expected.max_retry_count is not None and retry_count > expected.max_retry_count:
        failures.append(f"retry_count_too_high:expected_max={expected.max_retry_count},actual={retry_count}")
    if expected.require_retry is True and retry_count <= 0 and not debug.get("retry_actions"):
        failures.append("retry_required_but_not_observed")
    if expected.require_retry is False and (retry_count > 0 or debug.get("retry_actions")):
        failures.append("retry_not_expected_but_observed")
    if expected.require_page_window is True and int(debug.get("page_window", 0) or 0) <= 0:
        failures.append("page_window_required_but_missing")


def _check_optional_bool(
    debug: dict[str, Any],
    key: str,
    expected: bool | None,
    failures: list[str],
) -> None:
    if expected is None:
        return
    actual = bool(debug.get(key, False))
    if actual != expected:
        failures.append(f"{key}_mismatch:expected={expected},actual={actual}")


def _check_refusal(
    expected: bool | None,
    debug: dict[str, Any],
    answer: str,
    uncertain_answer_text: str | None,
    failures: list[str],
) -> None:
    if expected is None:
        return
    uncertain_match = bool(uncertain_answer_text and answer.strip() == uncertain_answer_text.strip())
    refusal_observed = bool(debug.get("refusal", False)) or debug.get("gate_decision") == "refuse" or uncertain_match
    if expected and not refusal_observed:
        failures.append("refusal_expected_but_not_observed")
    if expected is False and refusal_observed:
        failures.append("refusal_not_expected_but_observed")


def _covered_modalities(debug: dict[str, Any]) -> set[str]:
    coverage = debug.get("modality_coverage") or {}
    if not isinstance(coverage, dict):
        return set()
    return {str(key) for key, value in coverage.items() if _as_float(value, 0.0) > 0}


def _as_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return [str(value)]


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _category_breakdown(results: list[EvalCaseResult]) -> dict[str, dict[str, Any]]:
    breakdown: dict[str, dict[str, Any]] = {}
    for result in results:
        row = breakdown.setdefault(result.category, {"total": 0, "passed": 0, "failed": 0, "pass_rate": 0.0})
        row["total"] += 1
        if result.passed:
            row["passed"] += 1
        else:
            row["failed"] += 1
    for row in breakdown.values():
        row["pass_rate"] = round(row["passed"] / row["total"], 4) if row["total"] else 0.0
    return breakdown
