from __future__ import annotations

import json

import pytest

from agentic_rag.config import Settings
from agentic_rag.graph.agent_chunk_grader import AgentChunkGrader
from agentic_rag.schemas import SearchHit


class DummyLLM:
    def __init__(self, payload: dict):
        self.payload = payload
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return json.dumps(self.payload, ensure_ascii=False)


def _hit(node_id: str, text: str, score: float = 0.5, modality: str = "text", **kwargs) -> SearchHit:
    return SearchHit(
        point_id=node_id,
        node_id=node_id,
        text=text,
        score=score,
        modality=modality,
        metadata={"score_composite": score, "modality": modality, **kwargs.pop("metadata", {})},
        relationships=kwargs.pop("relationships", {}),
        **kwargs,
    )


def _grade(node_id: str, label: str, score: float, *, drop: bool = False) -> dict:
    return {
        "node_id": node_id,
        "relevance_score": score,
        "relevance_label": label,
        "keep": not drop,
        "drop": drop,
        "reasoning_summary": f"{label} reason",
    }


def test_agent_chunk_grader_applies_label_delta_and_drops_irrelevant() -> None:
    settings = Settings(
        _env_file=None,
        tg_agent_chunk_grading_enabled=True,
        tg_agent_chunk_grading_mode="all",
        tg_agent_chunk_drop_enabled=True,
        tg_agent_chunk_label_score_deltas="irrelevant:-0.20,weak:-0.05,relevant:0.03,strong:0.10",
    )
    llm = DummyLLM({"grades": [_grade("a", "strong", 0.9), _grade("b", "irrelevant", 0.1, drop=True)]})
    hits = [_hit("a", "answer", 0.5), _hit("b", "noise", 0.5)]

    graded = AgentChunkGrader(settings, llm).grade_hits(question="q", hits=hits)

    assert [hit.node_id for hit in graded] == ["a"]
    assert graded[0].score == 0.6
    assert graded[0].metadata["agent_label_score_delta"] == 0.1
    assert graded[0].metadata["score_policy"] == "agent_chunk_label_delta_v1"


def test_agent_chunk_grader_with_removed_exposes_drop_reason_metadata() -> None:
    settings = Settings(
        _env_file=None,
        tg_agent_chunk_grading_enabled=True,
        tg_agent_chunk_grading_mode="all",
        tg_agent_chunk_drop_enabled=True,
        tg_agent_chunk_drop_labels="irrelevant",
        tg_agent_chunk_drop_score_threshold=0.25,
        tg_agent_chunk_label_score_deltas="irrelevant:-0.20,weak:-0.05,relevant:0.03,strong:0.10",
    )
    llm = DummyLLM({"grades": [_grade("a", "strong", 0.9), _grade("b", "irrelevant", 0.1)]})
    hits = [_hit("a", "answer", 0.5), _hit("b", "noise", 0.5)]

    result = AgentChunkGrader(settings, llm).grade_hits_with_removed(question="q", hits=hits)

    assert [hit.node_id for hit in result.hits] == ["a"]
    assert [hit.node_id for hit in result.removed_hits] == ["b"]
    removed = result.removed_hits[0]
    assert removed.metadata["visual_removed"] is True
    assert removed.metadata["removed_stage"] == "agent_chunk_grading"
    assert removed.metadata["removed_reason"] == "agent_irrelevant_label"
    assert removed.metadata["agent_relevance_drop_reason"] == "label_and_score_threshold"
    assert removed.metadata["agent_relevance_drop_threshold"] == 0.25
    assert "label=irrelevant" in removed.metadata["removed_reason_detail"]


def test_agent_chunk_grader_protects_high_prior_irrelevant_grade_from_hard_drop() -> None:
    settings = Settings(
        _env_file=None,
        tg_agent_chunk_grading_enabled=True,
        tg_agent_chunk_grading_mode="all",
        tg_agent_chunk_drop_enabled=True,
        tg_agent_chunk_drop_require_label_and_score=True,
        tg_agent_chunk_drop_protect_prior_score=0.55,
        tg_agent_chunk_label_score_deltas="irrelevant:-0.20,weak:-0.05,relevant:0.03,strong:0.10",
    )
    llm = DummyLLM({"grades": [_grade("a", "irrelevant", 0.1)]})
    hit = _hit("a", "exact anchor evidence", 0.7)

    result = AgentChunkGrader(settings, llm).grade_hits_with_removed(question="anchor?", hits=[hit])

    assert [item.node_id for item in result.hits] == ["a"]
    assert result.removed_hits == []
    assert result.hits[0].metadata["agent_relevance_drop_protected"] is True
    assert result.hits[0].metadata["agent_relevance_drop_protected_reason"] == "prior_score"


def test_agent_chunk_grader_keeps_negative_delta_when_drop_disabled() -> None:
    settings = Settings(
        _env_file=None,
        tg_agent_chunk_grading_enabled=True,
        tg_agent_chunk_grading_mode="all",
        tg_agent_chunk_drop_enabled=False,
        tg_agent_chunk_label_score_deltas="irrelevant:-0.20,weak:-0.05,relevant:0.03,strong:0.10",
    )
    llm = DummyLLM({"grades": [_grade("weak", "weak", 0.4)]})

    graded = AgentChunkGrader(settings, llm).grade_hits(question="q", hits=[_hit("weak", "weak", 0.03)])

    assert graded[0].score == 0.0
    assert graded[0].metadata["agent_label_score_delta"] == -0.05
    assert graded[0].metadata["agent_relevance_drop"] is False


def test_agent_chunk_grader_uses_head_tail_selection() -> None:
    settings = Settings(
        _env_file=None,
        tg_agent_chunk_grading_enabled=True,
        tg_agent_chunk_grading_mode="head_tail",
        tg_agent_chunk_grading_head_m=2,
        tg_agent_chunk_grading_tail_n=1,
        tg_agent_chunk_grading_max_chunks=3,
    )
    llm = DummyLLM({"grades": []})
    hits = [_hit(str(i), f"text {i}") for i in range(5)]

    AgentChunkGrader(settings, llm).grade_hits(question="q", hits=hits)

    prompt = llm.prompts[0]
    assert '"node_id": "0"' in prompt
    assert '"node_id": "1"' in prompt
    assert '"node_id": "4"' in prompt
    assert '"node_id": "2"' not in prompt


def test_strong_formula_adds_missing_linked_text_context_with_label_fixed_score() -> None:
    settings = Settings(
        _env_file=None,
        tg_agent_chunk_grading_enabled=True,
        tg_agent_chunk_grading_mode="all",
        tg_agent_chunk_drop_enabled=True,
        tg_agent_chunk_label_score_deltas="irrelevant:-0.20,weak:-0.05,relevant:0.03,strong:0.10",
        tg_agent_chunk_related_context_enabled=True,
        tg_agent_chunk_related_context_add_labels="strong",
        tg_agent_chunk_related_context_fixed_scores="irrelevant:0.00,weak:0.40,relevant:0.65,strong:0.70",
        tg_agent_chunk_related_context_added_modality_deltas="irrelevant:0.00,weak:0.00,relevant:0.03,strong:0.08",
    )
    llm = DummyLLM({"grades": [_grade("formula", "strong", 0.82)]})
    formula = _hit(
        "formula",
        "x=1",
        0.4,
        modality="formula",
        metadata={"retrieval_expanded_from_node_id": "text1"},
    )
    context = _hit("text1", "explains formula", 0.6, modality="text")

    graded = AgentChunkGrader(settings, llm).grade_hits(
        question="q",
        hits=[formula],
        context_pool=[formula, context],
    )

    formula_hit = next(hit for hit in graded if hit.node_id == "formula")
    added = next(hit for hit in graded if hit.node_id == "text1")
    assert formula_hit.score == 0.58
    assert formula_hit.metadata["agent_related_modality_delta"] == 0.08
    assert formula_hit.metadata["agent_related_context_added"] is True
    assert added.metadata["score_composite"] == 0.7
    assert added.metadata["score_policy"] == "agent_context_fixed_by_label_v1"
    assert added.metadata["agent_related_context_source_node_id"] == "formula"
    assert "explains formula" in llm.prompts[0]


def test_strong_formula_adjusts_existing_linked_text_without_duplicate_addition() -> None:
    settings = Settings(
        _env_file=None,
        tg_agent_chunk_grading_enabled=True,
        tg_agent_chunk_grading_mode="all",
        tg_agent_chunk_drop_enabled=True,
        tg_agent_chunk_label_score_deltas="irrelevant:-0.20,weak:-0.05,relevant:0.03,strong:0.10",
        tg_agent_chunk_related_context_enabled=True,
        tg_agent_chunk_related_context_existing_modality_deltas="irrelevant:-0.10,weak:-0.03,relevant:0.03,strong:0.08",
        tg_agent_chunk_related_context_existing_text_deltas="irrelevant:0.00,weak:0.00,relevant:0.02,strong:0.06",
    )
    llm = DummyLLM({"grades": [_grade("formula", "strong", 0.9)]})
    formula = _hit(
        "formula",
        "x=1",
        0.4,
        modality="formula",
        metadata={"retrieval_expanded_from_node_id": "text1"},
    )
    context = _hit("text1", "explains formula", 0.6, modality="text")

    graded = AgentChunkGrader(settings, llm).grade_hits(
        question="q",
        hits=[formula, context],
        context_pool=[formula, context],
    )

    assert [hit.node_id for hit in graded].count("text1") == 1
    formula_hit = next(hit for hit in graded if hit.node_id == "formula")
    text_hit = next(hit for hit in graded if hit.node_id == "text1")
    assert formula_hit.score == 0.58
    assert text_hit.score == pytest.approx(0.66)
    assert formula_hit.metadata["agent_related_context_present"] is True
    assert formula_hit.metadata["agent_related_context_added"] is False


def test_relevant_formula_does_not_add_context_when_add_labels_only_strong() -> None:
    settings = Settings(
        _env_file=None,
        tg_agent_chunk_grading_enabled=True,
        tg_agent_chunk_grading_mode="all",
        tg_agent_chunk_drop_enabled=True,
        tg_agent_chunk_label_score_deltas="irrelevant:-0.20,weak:-0.05,relevant:0.03,strong:0.10",
        tg_agent_chunk_related_context_enabled=True,
        tg_agent_chunk_related_context_add_labels="strong",
        tg_agent_chunk_related_context_added_modality_deltas="irrelevant:0.00,weak:0.00,relevant:0.03,strong:0.08",
    )
    llm = DummyLLM({"grades": [_grade("formula", "relevant", 0.7)]})
    formula = _hit(
        "formula",
        "x=1",
        0.4,
        modality="formula",
        metadata={"retrieval_expanded_from_node_id": "text1"},
    )
    context = _hit("text1", "explains formula", 0.6, modality="text")

    graded = AgentChunkGrader(settings, llm).grade_hits(
        question="q",
        hits=[formula],
        context_pool=[formula, context],
    )

    assert [hit.node_id for hit in graded] == ["formula"]
    assert graded[0].score == pytest.approx(0.43)
    assert graded[0].metadata["agent_related_context_added"] is False


def test_score_adjust_and_related_context_switches_disable_score_changes() -> None:
    settings = Settings(
        _env_file=None,
        tg_agent_chunk_grading_enabled=True,
        tg_agent_chunk_grading_mode="all",
        tg_agent_chunk_drop_enabled=True,
        tg_agent_chunk_score_adjust_enabled=False,
        tg_agent_chunk_related_context_enabled=False,
    )
    llm = DummyLLM({"grades": [_grade("formula", "strong", 0.9)]})
    formula = _hit(
        "formula",
        "x=1",
        0.4,
        modality="formula",
        metadata={"retrieval_expanded_from_node_id": "text1"},
    )
    context = _hit("text1", "explains formula", 0.6, modality="text")

    graded = AgentChunkGrader(settings, llm).grade_hits(
        question="q",
        hits=[formula],
        context_pool=[formula, context],
    )

    assert [hit.node_id for hit in graded] == ["formula"]
    assert graded[0].score == 0.4
    assert graded[0].metadata["agent_label_score_delta"] == 0.0
