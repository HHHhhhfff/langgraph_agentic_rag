from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ExpectedDebug(BaseModel):
    """Expected TaskGraph/RAG debug signals for one evaluation case."""

    route: str | None = None
    route_any: list[str] = Field(default_factory=list)
    required_channels_all: list[str] = Field(default_factory=list)
    required_channels_any: list[str] = Field(default_factory=list)
    forbidden_channels: list[str] = Field(default_factory=list)
    required_modalities_any: list[str] = Field(default_factory=list)
    gate_decision: str | None = None
    gate_decision_any: list[str] = Field(default_factory=list)
    evidence_ok: bool | None = None
    citation_ok: bool | None = None
    should_refuse: bool | None = None
    min_support_score: float | None = None
    max_retry_count: int | None = None
    require_retry: bool | None = None
    require_page_window: bool | None = None


class EvalCase(BaseModel):
    """Serializable regression case for offline or live RAG evaluation."""

    id: str
    category: str
    question: str
    filters: dict[str, Any] | None = None
    expected_answer_keywords: list[str] = Field(default_factory=list)
    expected_answer_keywords_mode: Literal["all", "any"] = "all"
    forbidden_answer_keywords: list[str] = Field(default_factory=list)
    expected_citation_sources: list[str] = Field(default_factory=list)
    expected_debug: ExpectedDebug = Field(default_factory=ExpectedDebug)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvalCaseResult(BaseModel):
    """Pass/fail result for a single evaluation case."""

    case_id: str
    category: str
    passed: bool
    failure_reasons: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class EvalSummary(BaseModel):
    """Aggregate evaluation summary."""

    total: int = 0
    passed: int = 0
    failed: int = 0
    pass_rate: float = 0.0
    category_breakdown: dict[str, dict[str, Any]] = Field(default_factory=dict)
    failures: list[EvalCaseResult] = Field(default_factory=list)
