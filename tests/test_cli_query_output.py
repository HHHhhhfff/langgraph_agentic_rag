from __future__ import annotations

import json
from types import SimpleNamespace

from agentic_rag.cli import query
from agentic_rag.cli.debug_format import build_query_debug_lines, format_taskgraph_debug
from agentic_rag.schemas import RAGResult


def _result(debug=None) -> RAGResult:
    return RAGResult(
        answer="answer",
        retrieved_count=3,
        used_rerank=False,
        fallback_used=True,
        debug=debug or {},
    )


def _settings(taskgraph_enabled: bool):
    return SimpleNamespace(
        taskgraph_enabled=taskgraph_enabled,
        uncertain_answer_text="uncertain",
    )


def test_format_taskgraph_debug_missing_fields_is_safe() -> None:
    lines = format_taskgraph_debug({})

    assert "- route=None" in lines
    assert "- executed_channels=[]" in lines
    assert "- evidence_ok=false" in lines
    assert "- citation_ok=false" in lines


def test_format_taskgraph_debug_full_fields() -> None:
    lines = format_taskgraph_debug(
        {
            "route": "table_first",
            "executed_channels": ["vector", "bm25", "table"],
            "retry_count": 1,
            "gate_decision": "pass",
            "evidence_ok": True,
            "support_level": "strong",
            "support_score": 0.812345,
            "rerank_available": False,
            "support_features": {
                "top_hit_score": 0.9,
                "avg_top_score": 0.8,
                "score_consistency": 0.5,
                "rerank_top_score": 0.0,
                "source_diversity": 1.0,
                "slot_coverage_ratio": 1.0,
                "keyword_coverage": 0.25,
            },
            "support_feature_weights": {"top_hit_score": 0.3},
            "support_feature_contributions": {"top_hit_score": 0.27},
            "evidence_gaps": [],
            "missing_slots": [],
            "gate_reasons": [],
            "conflict_level": "none",
            "conflict_reasons": [],
            "retry_actions": ["rewrite_query", "increase_top_k"],
            "rewritten_query_text": "table accuracy",
            "page_window": 2,
            "citation_ok": True,
            "refusal": False,
            "refusal_reason": None,
        }
    )
    text = "\n".join(lines)

    assert "- route=table_first" in text
    assert "- executed_channels=vector,bm25,table" in text
    assert "- gate_decision=pass" in text
    assert "- support_level=strong" in text
    assert "- support_score=0.8123" in text
    assert "- support_features.top_hit_score=0.9000" in text
    assert "- support_features.keyword_coverage=0.2500" in text
    assert "- support_feature_weights.top_hit_score=0.3000" in text
    assert "- support_feature_contributions.top_hit_score=0.2700" in text
    assert "- retry_actions=[rewrite_query,increase_top_k]" in text
    assert "- rewritten_query_text=table accuracy" in text
    assert "- citation_ok=true" in text


def test_format_taskgraph_debug_only_shows_last_retry_summary() -> None:
    lines = format_taskgraph_debug(
        {
            "retry_history": [
                {"actions": ["first"], "channels": ["vector"], "top_k": {"vector": 4}},
                {"actions": ["second"], "channels": ["vector", "bm25"], "top_k": {"vector": 6, "bm25": 12}},
            ]
        }
    )
    text = "\n".join(lines)

    assert "last_retry.actions=[second]" in text
    assert "last_retry.channels=[vector,bm25]" in text
    assert "last_retry.top_k={vector:6,bm25:12}" in text
    assert "last_retry.actions=[first]" not in text


def test_build_query_debug_lines_respects_taskgraph_switch() -> None:
    disabled = build_query_debug_lines(_result({"route": "text_first"}), _settings(False))
    enabled = build_query_debug_lines(_result({"route": "text_first"}), _settings(True))

    assert "TaskGraph Debug:" not in disabled
    assert "TaskGraph Debug:" in enabled


class DummyGraph:
    def __init__(self, result: RAGResult):
        self.result = result

    def invoke(self, *, question, filters=None):
        return self.result


def test_query_main_normal_output_includes_taskgraph_debug(monkeypatch, capsys) -> None:
    result = _result(
        {
            "route": "text_first",
            "executed_channels": ["vector"],
            "gate_decision": "pass",
            "support_level": "strong",
            "support_score": 0.9,
            "citation_ok": True,
        }
    )
    monkeypatch.setattr(query, "get_settings", lambda: _settings(True))
    monkeypatch.setattr(query, "build_rag_graph", lambda: DummyGraph(result))
    monkeypatch.setattr("sys.argv", ["query", "hello"])

    assert query.main() == 0
    out = capsys.readouterr().out
    assert "TaskGraph Debug:" in out
    assert "- route=text_first" in out
    assert "- citation_ok=true" in out


def test_query_main_json_output_does_not_include_normal_debug(monkeypatch, capsys) -> None:
    result = _result({"route": "text_first", "citation_ok": True})
    monkeypatch.setattr(query, "get_settings", lambda: _settings(True))
    monkeypatch.setattr(query, "build_rag_graph", lambda: DummyGraph(result))
    monkeypatch.setattr("sys.argv", ["query", "hello", "--json"])

    assert query.main() == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["debug"]["route"] == "text_first"
    assert "TaskGraph Debug:" not in out
