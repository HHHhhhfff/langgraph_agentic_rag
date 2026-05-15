from __future__ import annotations

import json
from pathlib import Path

from agentic_rag.cli import eval as eval_cli
from agentic_rag.evaluation.cases import EvalCase
from agentic_rag.evaluation.runner import load_eval_cases, load_eval_results, run_live_eval, run_offline_eval
from agentic_rag.schemas import RAGResult


def test_load_eval_results_supports_result_wrapper_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "results.jsonl"
    path.write_text(
        json.dumps(
            {
                "case_id": "case1",
                "result": {
                    "answer": "TaskGraph answer",
                    "citations": [],
                    "retrieved_count": 1,
                    "debug": {"route": "text_first"},
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    results = load_eval_results(path)

    assert set(results) == {"case1"}
    assert results["case1"].answer == "TaskGraph answer"


def test_run_offline_eval_uses_loaded_cases_and_results(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.jsonl"
    results_path = tmp_path / "results.jsonl"
    cases_path.write_text(
        '{"id":"case1","category":"text_qa","question":"q","expected_answer_keywords":["TaskGraph"]}\n',
        encoding="utf-8",
    )
    results_path.write_text('{"case_id":"case1","answer":"TaskGraph","citations":[],"debug":{}}\n', encoding="utf-8")

    summary = run_offline_eval(load_eval_cases(cases_path), load_eval_results(results_path))

    assert summary.total == 1
    assert summary.passed == 1


class DummyGraph:
    def __init__(self):
        self.calls = []

    def invoke(self, *, question, filters=None):
        self.calls.append((question, filters))
        return RAGResult(answer="TaskGraph live", debug={"gate_decision": "pass"})


def test_run_live_eval_invokes_graph_with_case_filters() -> None:
    case = EvalCase(
        id="case1",
        category="text_qa",
        question="q",
        filters={"source": "doc.md"},
        expected_answer_keywords=["TaskGraph"],
    )
    graph = DummyGraph()

    summary = run_live_eval([case], graph)

    assert graph.calls == [("q", {"source": "doc.md"})]
    assert summary.passed == 1


def test_eval_cli_offline_does_not_call_build_rag_graph(monkeypatch, tmp_path: Path, capsys) -> None:
    cases_path = tmp_path / "cases.jsonl"
    results_path = tmp_path / "results.jsonl"
    cases_path.write_text(
        '{"id":"case1","category":"text_qa","question":"q","expected_answer_keywords":["TaskGraph"]}\n',
        encoding="utf-8",
    )
    results_path.write_text('{"case_id":"case1","answer":"TaskGraph","citations":[],"debug":{}}\n', encoding="utf-8")

    def fail_build():
        raise AssertionError("build_rag_graph should not be called in offline mode")

    monkeypatch.setattr(eval_cli, "build_rag_graph", fail_build)
    monkeypatch.setattr("sys.argv", ["eval", "--cases", str(cases_path), "--results", str(results_path), "--json"])

    assert eval_cli.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] == 1


def test_eval_cli_live_uses_mock_graph(monkeypatch, tmp_path: Path, capsys) -> None:
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        '{"id":"case1","category":"text_qa","question":"q","expected_answer_keywords":["TaskGraph"]}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(eval_cli, "build_rag_graph", lambda: DummyGraph())
    monkeypatch.setattr("sys.argv", ["eval", "--cases", str(cases_path), "--live", "--json"])

    assert eval_cli.main() == 0
    stdout = capsys.readouterr().out
    payload = json.loads(stdout)
    assert payload["total"] == 1
    assert payload["passed"] == 1
