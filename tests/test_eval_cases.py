from __future__ import annotations

from pathlib import Path

from agentic_rag.evaluation.cases import EvalCase
from agentic_rag.evaluation.runner import load_eval_cases


CASES_PATH = Path("tests/eval_cases/taskgraph_eval_cases.jsonl")


def test_load_eval_cases_covers_required_categories() -> None:
    cases = load_eval_cases(CASES_PATH)
    categories = {case.category for case in cases}

    assert len(cases) >= 6
    assert {
        "text_qa",
        "table_qa",
        "page_qa",
        "cross_page_qa",
        "cross_doc_qa",
        "no_answer_refusal",
    }.issubset(categories)


def test_eval_case_model_dump_validate_and_ignores_unknown_fields() -> None:
    case = EvalCase.model_validate(
        {
            "id": "case1",
            "category": "text_qa",
            "question": "What is TaskGraph?",
            "expected_answer_keywords": ["TaskGraph"],
            "unknown_field": "ignored",
        }
    )
    restored = EvalCase.model_validate(case.model_dump())

    assert restored.id == "case1"
    assert restored.expected_answer_keywords == ["TaskGraph"]
