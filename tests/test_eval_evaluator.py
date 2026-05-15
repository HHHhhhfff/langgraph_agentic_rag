from __future__ import annotations

from agentic_rag.evaluation.cases import EvalCase, ExpectedDebug
from agentic_rag.evaluation.evaluator import evaluate_result, evaluate_results
from agentic_rag.schemas import Citation, RAGResult


def _result(answer: str = "TaskGraph 使用任务规划和检索生成答案。", debug=None, citations=None) -> RAGResult:
    return RAGResult(
        answer=answer,
        citations=citations or [
            Citation(index=1, source="doc.md", title="Doc", chunk_index=0, score=0.9)
        ],
        retrieved_count=3,
        debug=debug
        or {
            "route": "text_first",
            "executed_channels": ["vector", "bm25"],
            "modality_coverage": {"text": 2},
            "gate_decision": "pass",
            "evidence_ok": True,
            "citation_ok": True,
            "refusal": False,
            "support_score": 0.8,
            "retry_count": 0,
        },
    )


def test_evaluate_result_passes_expected_keywords_and_debug() -> None:
    case = EvalCase(
        id="case1",
        category="text_qa",
        question="简单介绍 TaskGraph",
        expected_answer_keywords=["TaskGraph", "检索"],
        expected_citation_sources=["doc.md"],
        expected_debug=ExpectedDebug(
            route="text_first",
            required_channels_any=["vector"],
            required_modalities_any=["text"],
            gate_decision="pass",
            evidence_ok=True,
            citation_ok=True,
            min_support_score=0.5,
            max_retry_count=1,
            should_refuse=False,
        ),
    )

    result = evaluate_result(case, _result(), uncertain_answer_text="无法根据已检索到的资料确定答案。")

    assert result.passed is True
    assert result.failure_reasons == []


def test_evaluate_result_reports_missing_expected_keyword() -> None:
    case = EvalCase(
        id="case1",
        category="text_qa",
        question="q",
        expected_answer_keywords=["不存在的关键词"],
    )

    result = evaluate_result(case, _result())

    assert result.passed is False
    assert any("missing_expected_answer_keywords" in reason for reason in result.failure_reasons)


def test_evaluate_result_reports_forbidden_keyword() -> None:
    case = EvalCase(
        id="case1",
        category="text_qa",
        question="q",
        forbidden_answer_keywords=["错误"],
    )

    result = evaluate_result(case, _result(answer="这是错误答案"))

    assert result.passed is False
    assert any("forbidden_answer_keywords" in reason for reason in result.failure_reasons)


def test_evaluate_result_checks_citation_sources() -> None:
    case = EvalCase(
        id="case1",
        category="text_qa",
        question="q",
        expected_citation_sources=["missing.md"],
    )

    result = evaluate_result(case, _result())

    assert result.passed is False
    assert any("missing_citation_sources" in reason for reason in result.failure_reasons)


def test_evaluate_result_checks_channels_modalities_and_gate_fields() -> None:
    case = EvalCase(
        id="case1",
        category="table_qa",
        question="q",
        expected_debug=ExpectedDebug(
            required_channels_all=["table"],
            required_modalities_any=["table"],
            gate_decision_any=["pass"],
            evidence_ok=True,
            citation_ok=True,
        ),
    )
    result = _result(
        debug={
            "executed_channels": ["vector"],
            "modality_coverage": {"text": 1},
            "gate_decision": "retry",
            "evidence_ok": False,
            "citation_ok": False,
        }
    )

    evaluation = evaluate_result(case, result)

    assert evaluation.passed is False
    assert any("missing_required_channels" in reason for reason in evaluation.failure_reasons)
    assert any("missing_any_required_modality" in reason for reason in evaluation.failure_reasons)
    assert any("gate_decision_not_allowed" in reason for reason in evaluation.failure_reasons)
    assert any("evidence_ok_mismatch" in reason for reason in evaluation.failure_reasons)
    assert any("citation_ok_mismatch" in reason for reason in evaluation.failure_reasons)


def test_evaluate_result_accepts_refusal_or_uncertain_answer() -> None:
    case = EvalCase(
        id="refusal",
        category="no_answer_refusal",
        question="q",
        expected_debug=ExpectedDebug(should_refuse=True),
    )

    explicit = evaluate_result(case, _result(debug={"refusal": True, "gate_decision": "refuse"}))
    uncertain = evaluate_result(
        case,
        _result(answer="无法根据已检索到的资料确定答案。", debug={"refusal": False, "gate_decision": "retry"}),
        uncertain_answer_text="无法根据已检索到的资料确定答案。",
    )

    assert explicit.passed is True
    assert uncertain.passed is True


def test_evaluate_result_checks_support_score_retry_and_page_window() -> None:
    case = EvalCase(
        id="case1",
        category="page_qa",
        question="q",
        expected_debug=ExpectedDebug(
            min_support_score=0.9,
            max_retry_count=1,
            require_retry=True,
            require_page_window=True,
        ),
    )
    result = _result(debug={"support_score": 0.4, "retry_count": 0, "page_window": None})

    evaluation = evaluate_result(case, result)

    assert evaluation.passed is False
    assert any("support_score_too_low" in reason for reason in evaluation.failure_reasons)
    assert "retry_required_but_not_observed" in evaluation.failure_reasons
    assert "page_window_required_but_missing" in evaluation.failure_reasons


def test_evaluate_results_builds_summary() -> None:
    cases = [
        EvalCase(id="ok", category="text_qa", question="q", expected_answer_keywords=["TaskGraph"]),
        EvalCase(id="bad", category="table_qa", question="q", expected_answer_keywords=["missing"]),
        EvalCase(id="missing", category="page_qa", question="q"),
    ]
    results = {"ok": _result(), "bad": _result()}

    summary = evaluate_results(cases, results)

    assert summary.total == 3
    assert summary.passed == 1
    assert summary.failed == 2
    assert summary.pass_rate == 0.3333
    assert summary.category_breakdown["text_qa"]["passed"] == 1
    assert len(summary.failures) == 2
