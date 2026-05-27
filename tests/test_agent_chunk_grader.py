from __future__ import annotations

import json

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


def test_agent_chunk_grader_drops_irrelevant_and_boosts_strong() -> None:
    settings = Settings(
        _env_file=None,
        tg_agent_chunk_grading_enabled=True,
        tg_agent_chunk_grading_mode="all",
        tg_agent_chunk_boost_enabled=True,
        tg_agent_chunk_drop_enabled=True,
        tg_agent_chunk_strong_boost=0.1,
    )
    llm = DummyLLM(
        {
            "grades": [
                {
                    "node_id": "a",
                    "relevance_score": 0.9,
                    "relevance_label": "strong",
                    "keep": True,
                    "boost": True,
                    "drop": False,
                    "reasoning_summary": "directly answers",
                },
                {
                    "node_id": "b",
                    "relevance_score": 0.1,
                    "relevance_label": "irrelevant",
                    "keep": False,
                    "boost": False,
                    "drop": True,
                    "reasoning_summary": "off topic",
                },
            ]
        }
    )
    hits = [_hit("a", "answer", 0.5), _hit("b", "noise", 0.5)]

    graded = AgentChunkGrader(settings, llm).grade_hits(question="q", hits=hits)

    assert [hit.node_id for hit in graded] == ["a"]
    assert graded[0].score == 0.6
    assert graded[0].metadata["agent_relevance_label"] == "strong"
    assert graded[0].metadata["agent_relevance_boost"] == 0.1


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


def test_agent_chunk_grader_adds_linked_text_context_for_relevant_formula() -> None:
    settings = Settings(
        _env_file=None,
        tg_agent_chunk_grading_enabled=True,
        tg_agent_chunk_grading_mode="all",
        tg_agent_chunk_drop_enabled=True,
        tg_agent_chunk_add_context_for_related_modality=True,
        tg_agent_chunk_context_fixed_score=0.7,
    )
    llm = DummyLLM(
        {
            "grades": [
                {
                    "node_id": "formula",
                    "relevance_score": 0.82,
                    "relevance_label": "strong",
                    "keep": True,
                    "boost": False,
                    "drop": False,
                    "reasoning_summary": "formula plus context is relevant",
                }
            ]
        }
    )
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

    ids = {hit.node_id for hit in graded}
    assert {"formula", "text1"}.issubset(ids)
    added = next(hit for hit in graded if hit.node_id == "text1")
    assert added.metadata["agent_grading_context_added"] is True
    assert added.metadata["score_composite"] == 0.7
    assert "explains formula" in llm.prompts[0]
