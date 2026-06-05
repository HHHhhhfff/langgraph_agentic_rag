from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentic_rag.config import Settings
from agentic_rag.models.json_llm import generate_json
from agentic_rag.models.providers import LLMClient
from agentic_rag.retrieval.retrieval_plan import RetrievalPlan


AgentRoute = Literal["text_first", "table_first", "page_first", "image_first", "formula_first", "cross_doc"]
AgentChannel = Literal["vector", "bm25", "page", "table", "image", "formula", "relationship"]


class AgentRouteDecision(BaseModel):
    """LLM-proposed route analysis. This is advisory only."""

    intent: str
    target_modalities: list[str] = Field(default_factory=list)
    need_cross_doc: bool = False
    need_page_level: bool = False
    route: AgentRoute = "text_first"
    reasoning_summary: str = ""
    confidence: float = 0.0


class AgentRetrievalTask(BaseModel):
    """LLM-proposed retrieval task. It must be validated before execution."""

    channel: AgentChannel
    query_text: str
    top_k: int
    filters: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class AgentRetrievalPlanDecision(BaseModel):
    """LLM-proposed retrieval plan. This is advisory only."""

    tasks: list[AgentRetrievalTask] = Field(default_factory=list)
    page_window: int | None = None
    risk_flags: list[str] = Field(default_factory=list)
    reasoning_summary: str = ""


class AgentRouteAnalyzer:
    """Use LLM to propose question routing under a strict JSON schema."""

    def __init__(self, settings: Settings, llm_client: LLMClient):
        self.settings = settings
        self.llm_client = llm_client

    def analyze(
        self,
        *,
        question: str,
        filters: dict[str, Any] | None,
        rule_state: dict[str, Any],
    ) -> AgentRouteDecision:
        prompt = _agent_contract() + f"""
Task: analyze the user question and return an AgentRouteDecision JSON object.

Allowed routes: text_first, table_first, page_first, image_first, formula_first, cross_doc.
Allowed modalities: text, table, page, image, formula.
Treat implicit visual references such as 图中, 图 1, 图2, figure, chart, screenshot, photo, diagram, 趋势图, 示意图, 右上角 as image questions.
For image questions choose route=image_first and include image in target_modalities. Add page when page/figure number or layout position is mentioned.

User question:
{question}

Filters:
{json.dumps(filters or {}, ensure_ascii=False)}

Rule analysis:
{json.dumps(rule_state, ensure_ascii=False)}

Return only JSON with keys: intent, target_modalities, need_cross_doc, need_page_level, route, reasoning_summary, confidence.
"""
        return generate_json(self.llm_client, prompt, AgentRouteDecision)


class AgentRetrievalPlanner:
    """Use LLM to propose retrieval tasks under a strict JSON schema."""

    def __init__(self, settings: Settings, llm_client: LLMClient):
        self.settings = settings
        self.llm_client = llm_client

    def plan(
        self,
        *,
        question: str,
        filters: dict[str, Any] | None,
        state: dict[str, Any],
        rule_plan: RetrievalPlan,
    ) -> AgentRetrievalPlanDecision:
        prompt = _agent_contract() + f"""
Task: propose retrieval tasks to improve evidence gathering.

Allowed channels: vector, bm25, page, table, image, formula, relationship.
Allowed filter keys: source, doc_id, page, tags, modality, image_semantic_type.
Do not modify max_retries, budget_tokens, or budget_ms.
Use top_k no larger than {self.settings.tg_retry_max_top_k}.
For image questions, consider image channel first. Add page if the question mentions page, figure number, position, or layout. Add bm25 for OCR/caption keywords. Add relationship for cross-document image comparison.
Allowed image_semantic_type values: whole_image, caption, ocr, object, chart_data.

User question:
{question}

Filters:
{json.dumps(filters or {}, ensure_ascii=False)}

TaskGraph state summary:
{json.dumps(_state_summary(state), ensure_ascii=False)}

Rule RetrievalPlan:
{json.dumps(rule_plan.model_dump(), ensure_ascii=False)}

Return only JSON with keys: tasks, page_window, risk_flags, reasoning_summary.
Each task must contain: channel, query_text, top_k, filters, reason.
"""
        return generate_json(self.llm_client, prompt, AgentRetrievalPlanDecision)


def _state_summary(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "intent": state.get("intent"),
        "route": state.get("route"),
        "target_modalities": state.get("target_modalities", []),
        "need_cross_doc": state.get("need_cross_doc", False),
        "need_page_level": state.get("need_page_level", False),
    }


def _agent_contract() -> str:
    return (
        "You are a constrained retrieval planning agent. "
        "You must output only valid JSON. Do not output markdown. "
        "Do not generate the final answer. Do not bypass evidence_gate or citation_verify. "
        "If uncertain, prefer retry-oriented retrieval rather than forcing pass.\n\n"
    )
