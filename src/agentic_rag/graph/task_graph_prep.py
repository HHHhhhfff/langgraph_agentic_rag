from __future__ import annotations

from typing import Any, TypedDict

from agentic_rag.retrieval.evidence_pack import EvidencePack
from agentic_rag.retrieval.retrieval_plan import RetrievalPlan
from agentic_rag.schemas import SearchHit


class TaskGraphState(TypedDict, total=False):
    question: str
    filters: dict[str, Any] | None

    intent: str
    target_modalities: list[str]
    need_cross_doc: bool
    need_page_level: bool
    route: str

    retrieval_plan: dict[str, Any]
    retry_count: int
    max_retries: int
    budget_tokens: int
    budget_ms: int
    started_at_ms: int

    query_vector: list[float]
    route_hits: dict[str, list[SearchHit]]
    fused_hits: list[SearchHit]
    expanded_hits: list[SearchHit]
    reranked_hits: list[SearchHit]
    used_rerank: bool
    rerank_fallback_reason: str | None
    rerank_score_top: float | None
    rerank_hit_count: int
    evidence_pack: dict[str, Any]
    evidence_ok: bool
    evidence_gaps: list[str]
    evidence_gain: float
    executed_channels: list[str]
    claim_supported: bool
    source_coverage: dict[str, int]
    page_coverage: dict[str, int]
    modality_coverage: dict[str, int]
    conflict_level: str
    missing_slots: list[str]
    supporting_hit_ids: list[str]
    support_level: str
    support_score: float
    support_features: dict[str, float]
    support_feature_weights: dict[str, float]
    support_feature_contributions: dict[str, float]
    support_raw_features: dict[str, float]
    support_normalized_features: dict[str, float]
    rerank_available: bool
    slot_coverage: dict[str, bool]
    required_slots: list[str]
    covered_slots: list[str]
    conflict_reasons: list[str]
    gate_decision: str
    gate_reasons: list[str]
    retry_actions: list[str]
    retry_history: list[dict[str, object]]
    rewritten_query_text: str | None
    page_window: int | None
    agent_route_used: bool
    agent_route_confidence: float
    agent_route_reasoning: str
    agent_plan_used: bool
    agent_plan_reasoning: str
    agent_evidence_used: bool
    agent_evidence_reasoning: str
    agent_gate_decision: str | None
    unsupported_claims: list[str]
    agent_retry_used: bool
    agent_retry_reasoning: str
    agent_fallback_reason: str | None
    plan_validation_errors: list[str]
    retry_plan_validation_errors: list[str]
    refusal: bool
    refusal_reason: str | None
    retrieval_eval_query_id: str
    retrieval_eval_snapshots: list[dict[str, Any]]
    retrieval_eval_log_error: str | None

    context: str
    citations: list[dict[str, Any]]
    prompt: str
    answer: str
    citation_ok: bool
    result: Any
    debug: dict[str, Any]


class QuestionAnalysisState(TypedDict, total=False):
    question: str
    intent: str
    target_modalities: list[str]
    need_cross_doc: bool
    need_page_level: bool
    route: str


class TaskRouteState(TypedDict, total=False):
    retrieval_plan: dict[str, Any]


class EvidenceGateState(TypedDict, total=False):
    evidence_ok: bool
    evidence_gaps: list[str]
    support_level: str
    support_score: float
    gate_decision: str
    gate_reasons: list[str]
    refusal: bool
    refusal_reason: str | None


class LocalRetryState(TypedDict, total=False):
    retrieval_plan: dict[str, Any]
    retry_count: int
    evidence_gain: float
    retry_actions: list[str]
    retry_history: list[dict[str, object]]
    rewritten_query_text: str | None
    page_window: int | None


__all__ = [
    "TaskGraphState",
    "QuestionAnalysisState",
    "TaskRouteState",
    "EvidenceGateState",
    "LocalRetryState",
    "RetrievalPlan",
    "EvidencePack",
]

