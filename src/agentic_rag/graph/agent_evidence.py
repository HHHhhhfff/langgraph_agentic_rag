from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentic_rag.config import Settings
from agentic_rag.models.json_llm import generate_json
from agentic_rag.models.providers import LLMClient
from agentic_rag.retrieval.evidence_pack import EvidencePack
from agentic_rag.schemas import SearchHit


SupportLevel = Literal["none", "weak", "partial", "strong"]
ConflictLevel = Literal["none", "low", "medium", "high"]
GateDecision = Literal["pass", "retry", "refuse"]


class EvidenceCritique(BaseModel):
    """LLM semantic critique of an EvidencePack. Advisory only."""

    claim_supported: bool = False
    support_level: SupportLevel = "none"
    support_score: float = 0.0
    missing_slots: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    conflict_detected: bool = False
    conflict_level: ConflictLevel = "none"
    conflict_reasons: list[str] = Field(default_factory=list)
    recommended_retry_actions: list[str] = Field(default_factory=list)
    gate_decision: GateDecision = "retry"
    reasoning_summary: str = ""


class AgentEvidenceCritic:
    """Use LLM to judge whether evidence semantically supports the question."""

    def __init__(self, settings: Settings, llm_client: LLMClient):
        self.settings = settings
        self.llm_client = llm_client

    def critique(
        self,
        *,
        question: str,
        pack: EvidencePack,
        route_hits: dict[str, list[SearchHit]] | None = None,
    ) -> EvidenceCritique:
        prompt = _agent_contract() + f"""
Task: critique whether the evidence supports answering the user question.

Be conservative. If evidence is incomplete or conflicting, choose retry. If there is high conflict and refusal is allowed, choose refuse.
For image questions, verify the evidence contains image modality and relevant image_semantic_type evidence: whole_image, caption, ocr, object, or chart_data. If the question asks about a visual/figure/screenshot/chart but evidence is text-only or lacks image semantics, choose retry.

User question:
{question}

Rule EvidencePack:
{json.dumps(_pack_summary(pack), ensure_ascii=False)}

Top evidence snippets:
{json.dumps(_hit_snippets(pack.expanded_hits or pack.hits, self.settings.tg_agent_max_context_hits), ensure_ascii=False)}

Route hit summary:
{json.dumps(_route_summary(route_hits or pack.route_hits), ensure_ascii=False)}

Return only JSON with keys: claim_supported, support_level, support_score, missing_slots, unsupported_claims, conflict_detected, conflict_level, conflict_reasons, recommended_retry_actions, gate_decision, reasoning_summary.
"""
        return generate_json(self.llm_client, prompt, EvidenceCritique)


def merge_evidence_gate(rule_pack: EvidencePack, critique: EvidenceCritique, settings: Settings) -> EvidencePack:
    """Merge rule gate and LLM critique using conservative decision rules."""

    merged = rule_pack.model_copy(deep=True)
    merged.missing_slots = _dedupe([*merged.missing_slots, *critique.missing_slots])
    merged.conflict_reasons = _dedupe([*merged.conflict_reasons, *critique.conflict_reasons])
    merged.gate_reasons = _dedupe([*merged.gate_reasons, *critique.recommended_retry_actions])
    merged.unsupported_claims = _dedupe([*merged.unsupported_claims, *critique.unsupported_claims])
    merged.support_score = max(float(merged.support_score), max(0.0, min(1.0, float(critique.support_score))))
    merged.support_level = _stronger_support(merged.support_level, critique.support_level)
    merged.claim_supported = bool(merged.claim_supported or critique.claim_supported)
    merged.conflict_detected = bool(merged.conflict_detected or critique.conflict_detected)
    merged.conflict_level = _max_conflict(merged.conflict_level, critique.conflict_level)
    merged.agent_gate_decision = critique.gate_decision
    merged.notes.append(f"agent_evidence_reasoning:{critique.reasoning_summary}")

    rule_hard_fail = bool(
        any(
            slot in {"insufficient_hits", "missing_page", "missing_source", "missing_numeric"}
            or slot.startswith("missing_modality:")
            for slot in merged.gate_reasons
        )
    )
    if rule_hard_fail and merged.conflict_level == "high" and settings.tg_allow_refusal:
        merged.gate_decision = "refuse"
    elif rule_hard_fail:
        merged.gate_decision = "retry"
    elif critique.gate_decision == "pass" and merged.support_score >= settings.tg_min_support_score:
        merged.gate_decision = "pass"
    else:
        merged.gate_decision = "pass" if rule_pack.gate_decision == "pass" or critique.gate_decision == "pass" else "retry"
    merged.evidence_ok = merged.gate_decision == "pass"
    if merged.gate_decision != "pass" and not merged.gate_reasons:
        merged.gate_reasons.append("agent_or_rule_requires_retry")
    merged.evidence_gaps = list(merged.gate_reasons)
    return merged


def _agent_contract() -> str:
    return (
        "You are a constrained evidence critic for RAG. "
        "You must output only valid JSON. Do not output markdown. "
        "Do not generate the final answer. Do not bypass evidence_gate or citation_verify. "
        "If uncertain, choose retry.\n\n"
    )


def _pack_summary(pack: EvidencePack) -> dict[str, Any]:
    return {
        "gate_decision": pack.gate_decision,
        "support_level": pack.support_level,
        "support_score": pack.support_score,
        "missing_slots": pack.missing_slots,
        "required_slots": pack.required_slots,
        "covered_slots": pack.covered_slots,
        "conflict_level": pack.conflict_level,
        "conflict_reasons": pack.conflict_reasons,
        "source_coverage": pack.source_coverage,
        "page_coverage": pack.page_coverage,
        "modality_coverage": pack.modality_coverage,
    }


def _hit_snippets(hits: list[SearchHit], max_hits: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for hit in hits[:max(1, max_hits)]:
        rows.append(
            {
                "id": hit.node_id or hit.point_id,
                "channel": hit.channel,
                "doc_id": hit.doc_id,
                "page": hit.page,
                "modality": hit.modality,
                "image_semantic_type": hit.image_semantic_type or hit.metadata.get("image_semantic_type"),
                "source_parser": hit.source_parser or hit.metadata.get("source_parser"),
                "text": (hit.text or hit.table_markdown or hit.formula_latex or "")[:700],
            }
        )
    return rows


def _route_summary(route_hits: dict[str, list[SearchHit]]) -> dict[str, int]:
    return {channel: len(hits) for channel, hits in route_hits.items()}


def _stronger_support(left: str, right: str) -> str:
    order = {"none": 0, "weak": 1, "partial": 2, "strong": 3}
    reverse = {value: key for key, value in order.items()}
    return reverse[max(order.get(left, 0), order.get(right, 0))]


def _max_conflict(left: str, right: str) -> str:
    order = {"none": 0, "low": 1, "medium": 2, "high": 3}
    reverse = {value: key for key, value in order.items()}
    return reverse[max(order.get(left, 0), order.get(right, 0))]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
