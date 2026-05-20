from __future__ import annotations

from pydantic import BaseModel, Field

from agentic_rag.retrieval.retrieval_plan import RetrievalPlan
from agentic_rag.schemas import SearchHit


class EvidencePack(BaseModel):
    """Grouped evidence bundle returned by multi-route retrieval."""

    plan: RetrievalPlan
    hits: list[SearchHit] = Field(default_factory=list)
    route_hits: dict[str, list[SearchHit]] = Field(default_factory=dict)
    expanded_hits: list[SearchHit] = Field(default_factory=list)
    evidence_ok: bool = False
    evidence_gaps: list[str] = Field(default_factory=list)
    conflict_detected: bool = False
    claim_supported: bool = False
    source_coverage: dict[str, int] = Field(default_factory=dict)
    page_coverage: dict[str, int] = Field(default_factory=dict)
    modality_coverage: dict[str, int] = Field(default_factory=dict)
    conflict_level: str = "none"
    missing_slots: list[str] = Field(default_factory=list)
    supporting_hit_ids: list[str] = Field(default_factory=list)
    support_level: str = "none"
    support_score: float = 0.0
    support_features: dict[str, float] = Field(default_factory=dict)
    support_feature_weights: dict[str, float] = Field(default_factory=dict)
    support_feature_contributions: dict[str, float] = Field(default_factory=dict)
    support_raw_features: dict[str, float] = Field(default_factory=dict)
    support_normalized_features: dict[str, float] = Field(default_factory=dict)
    rerank_available: bool = False
    slot_coverage: dict[str, bool] = Field(default_factory=dict)
    required_slots: list[str] = Field(default_factory=list)
    covered_slots: list[str] = Field(default_factory=list)
    conflict_reasons: list[str] = Field(default_factory=list)
    gate_decision: str = "retry"
    gate_reasons: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    agent_gate_decision: str | None = None
    notes: list[str] = Field(default_factory=list)
