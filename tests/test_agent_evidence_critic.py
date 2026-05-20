from __future__ import annotations

from agentic_rag.config import Settings
from agentic_rag.graph.agent_evidence import AgentEvidenceCritic, EvidenceCritique, merge_evidence_gate
from agentic_rag.retrieval.evidence_pack import EvidencePack
from agentic_rag.retrieval.retrieval_plan import RetrievalPlan
from agentic_rag.schemas import SearchHit


class DummyLLM:
    def __init__(self, output: str):
        self.output = output

    def generate(self, prompt: str) -> str:
        return self.output


def _pack(decision="pass") -> EvidencePack:
    return EvidencePack(
        plan=RetrievalPlan(question="q"),
        hits=[SearchHit(point_id="p", text="evidence", score=0.9)],
        expanded_hits=[SearchHit(point_id="p", text="evidence", score=0.9)],
        gate_decision=decision,
        evidence_ok=decision == "pass",
        support_level="strong",
        support_score=0.9,
    )


def test_agent_evidence_critic_parses_json() -> None:
    llm = DummyLLM(
        '{"claim_supported":false,"support_level":"partial","support_score":0.5,'
        '"missing_slots":["numeric"],"unsupported_claims":["number missing"],'
        '"conflict_detected":false,"conflict_level":"none","conflict_reasons":[],'
        '"recommended_retry_actions":["missing_numeric"],"gate_decision":"retry",'
        '"reasoning_summary":"missing number"}'
    )
    critic = AgentEvidenceCritic(Settings(), llm)

    critique = critic.critique(question="q", pack=_pack())

    assert critique.gate_decision == "retry"
    assert critique.missing_slots == ["numeric"]


def test_merge_evidence_gate_soft_fail_can_be_uplifted() -> None:
    critique = EvidenceCritique(
        claim_supported=True,
        support_level="strong",
        support_score=0.9,
        gate_decision="pass",
    )

    rule_pack = _pack("retry")
    rule_pack.gate_reasons = ["low_keyword_coverage"]
    rule_pack.missing_slots = []
    rule_pack.conflict_level = "none"

    merged = merge_evidence_gate(rule_pack, critique, Settings())

    assert merged.gate_decision == "pass"
    assert merged.evidence_ok is True
    assert merged.support_score >= rule_pack.support_score


def test_merge_evidence_gate_allows_agent_uplift_on_soft_fail() -> None:
    rule_pack = _pack("retry")
    rule_pack.gate_reasons = ["low_keyword_coverage"]
    rule_pack.missing_slots = []
    rule_pack.conflict_level = "none"
    critique = EvidenceCritique(
        claim_supported=True,
        support_level="strong",
        support_score=0.9,
        gate_decision="pass",
    )

    merged = merge_evidence_gate(rule_pack, critique, Settings())

    assert merged.gate_decision == "pass"
    assert merged.evidence_ok is True
    assert merged.support_score >= rule_pack.support_score


def test_merge_evidence_gate_high_conflict_refuses() -> None:
    rule_pack = _pack("retry")
    rule_pack.gate_reasons = ["missing_page"]
    rule_pack.conflict_level = "high"
    critique = EvidenceCritique(
        conflict_detected=True,
        conflict_level="high",
        conflict_reasons=["contradiction"],
        gate_decision="refuse",
    )

    merged = merge_evidence_gate(rule_pack, critique, Settings(tg_allow_refusal=True))

    assert merged.gate_decision == "refuse"
    assert merged.conflict_level == "high"
