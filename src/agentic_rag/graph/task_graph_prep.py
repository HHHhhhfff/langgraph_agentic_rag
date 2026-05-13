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
    evidence_pack: dict[str, Any]
    evidence_ok: bool
    evidence_gaps: list[str]
    evidence_gain: float
    refusal: bool
    refusal_reason: str | None

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
    refusal: bool
    refusal_reason: str | None


class LocalRetryState(TypedDict, total=False):
    retrieval_plan: dict[str, Any]
    retry_count: int
    evidence_gain: float


__all__ = [
    "TaskGraphState",
    "QuestionAnalysisState",
    "TaskRouteState",
    "EvidenceGateState",
    "LocalRetryState",
    "RetrievalPlan",
    "EvidencePack",
]

