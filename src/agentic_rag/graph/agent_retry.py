from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentic_rag.config import Settings
from agentic_rag.models.json_llm import generate_json
from agentic_rag.models.providers import LLMClient
from agentic_rag.retrieval.retrieval_plan import RetrievalPlan
from agentic_rag.schemas import SearchHit


RetryChannel = Literal["vector", "bm25", "page", "table", "image", "formula", "relationship"]


class RetryAdvice(BaseModel):
    """LLM-proposed local retry advice. Advisory only."""

    rewritten_query_text: str
    add_channels: list[RetryChannel] = Field(default_factory=list)
    increase_top_k_channels: list[str] = Field(default_factory=list)
    page_window: int | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)


class AgentRetryAdvisor:
    """Use LLM to suggest local retry changes under strict constraints."""

    def __init__(self, settings: Settings, llm_client: LLMClient):
        self.settings = settings
        self.llm_client = llm_client

    def advise(self, *, state: dict[str, Any], rule_plan: RetrievalPlan) -> RetryAdvice:
        prompt = _agent_contract() + f"""
Task: suggest one local retry plan update to improve evidence.

Allowed channels: vector, bm25, page, table, image, formula, relationship.
Allowed filter keys: source, doc_id, page, tags, modality, image_semantic_type.
Do not exceed top_k max {self.settings.tg_retry_max_top_k} or page_window max {self.settings.tg_retry_max_page_window}.
Do not modify retry budget fields.
For image evidence gaps, prefer add_channels=["image"]. Add page when page/figure number or layout position is unclear. Rewrite with terms like 图中, figure, image, caption, OCR, object when image caption/OCR/object evidence is missing.

State:
{json.dumps(_state_summary(state), ensure_ascii=False)}

Current rule retry plan:
{json.dumps(rule_plan.model_dump(), ensure_ascii=False)}

Route hit summary:
{json.dumps(_route_summary(state.get("route_hits") or {}), ensure_ascii=False)}

Return only JSON with keys: rewritten_query_text, add_channels, increase_top_k_channels, page_window, filters, reasons.
"""
        return generate_json(self.llm_client, prompt, RetryAdvice)


def _agent_contract() -> str:
    return (
        "You are a constrained local retry advisor for RAG. "
        "You must output only valid JSON. Do not output markdown. "
        "Do not generate the final answer. Do not bypass evidence_gate or citation_verify. "
        "If uncertain, add conservative retrieval channels and choose retry-oriented advice.\n\n"
    )


def _state_summary(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "question": state.get("question"),
        "missing_slots": state.get("missing_slots", []),
        "gate_reasons": state.get("gate_reasons", []),
        "evidence_gaps": state.get("evidence_gaps", []),
        "support_score": state.get("support_score"),
        "support_level": state.get("support_level"),
        "conflict_level": state.get("conflict_level"),
        "conflict_reasons": state.get("conflict_reasons", []),
        "citation_ok": state.get("citation_ok"),
    }


def _route_summary(route_hits: dict[str, list[SearchHit]]) -> dict[str, int]:
    return {channel: len(hits) for channel, hits in route_hits.items()}
