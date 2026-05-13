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
    notes: list[str] = Field(default_factory=list)
