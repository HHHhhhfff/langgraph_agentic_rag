from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentic_rag.config import Settings
from agentic_rag.models.json_llm import generate_json
from agentic_rag.models.providers import LLMClient
from agentic_rag.retrieval.scoring import score_value
from agentic_rag.schemas import SearchHit


RelevanceLabel = Literal["irrelevant", "weak", "relevant", "strong"]


class ChunkGrade(BaseModel):
    node_id: str
    relevance_score: float = 0.0
    relevance_label: RelevanceLabel = "weak"
    keep: bool = True
    drop: bool = False
    needs_context: bool = False
    needs_related_table: bool = False
    needs_related_formula: bool = False
    reasoning_summary: str = ""


class ChunkGradingResult(BaseModel):
    grades: list[ChunkGrade] = Field(default_factory=list)


class AgentChunkGrader:
    """Grade per-hit relevance before evidence gate."""

    def __init__(self, settings: Settings, llm_client: LLMClient):
        self.settings = settings
        self.llm_client = llm_client

    def grade_hits(
        self,
        *,
        question: str,
        hits: list[SearchHit],
        context_pool: list[SearchHit] | None = None,
    ) -> list[SearchHit]:
        if not self.settings.tg_agent_chunk_grading_enabled or not hits:
            return hits
        context_pool = context_pool or hits
        selected = self._select_hits(hits)
        if not selected:
            return hits
        payload = [
            self._grade_payload(hit, context_pool=context_pool)
            for hit in selected
        ]
        prompt = _agent_contract(self.settings) + f"""
Task: grade each candidate chunk for relevance to the user question.

User question:
{question}

Candidate chunks:
{json.dumps(payload, ensure_ascii=False)}

Return only JSON:
{{
  "grades": [
    {{
      "node_id": "...",
      "relevance_score": 0.0,
      "relevance_label": "irrelevant|weak|relevant|strong",
      "keep": true,
      "drop": false,
      "needs_context": false,
      "needs_related_table": false,
      "needs_related_formula": false,
      "reasoning_summary": "short reason"
    }}
  ]
}}
"""
        result = generate_json(self.llm_client, prompt, ChunkGradingResult)
        grades = {grade.node_id: grade for grade in result.grades}
        graded = [self._apply_grade(hit, grades.get(_hit_id(hit))) for hit in hits]
        graded = self._apply_related_context_policy(graded, grades, context_pool=context_pool)
        if self.settings.tg_agent_chunk_drop_enabled:
            graded = [hit for hit in graded if not hit.metadata.get("agent_relevance_drop")]
        return graded

    def _select_hits(self, hits: list[SearchHit]) -> list[SearchHit]:
        mode = (self.settings.tg_agent_chunk_grading_mode or "head_tail").lower()
        if mode == "none":
            return []
        max_chunks = max(1, self.settings.tg_agent_chunk_grading_max_chunks)
        if mode == "all":
            return hits[:max_chunks]
        head_m = max(0, self.settings.tg_agent_chunk_grading_head_m)
        if mode == "head":
            return hits[: min(head_m, max_chunks)]
        tail_n = max(0, self.settings.tg_agent_chunk_grading_tail_n)
        selected: list[SearchHit] = []
        seen: set[str] = set()
        candidates = [*hits[:head_m], *(hits[-tail_n:] if tail_n else [])]
        for hit in candidates:
            key = _hit_id(hit)
            if key in seen:
                continue
            seen.add(key)
            selected.append(hit)
            if len(selected) >= max_chunks:
                break
        return selected

    def _grade_payload(self, hit: SearchHit, *, context_pool: list[SearchHit]) -> dict[str, Any]:
        context_hits = self._context_hits_for(hit, context_pool=context_pool)
        return {
            "node_id": _hit_id(hit),
            "modality": hit.modality or hit.metadata.get("modality"),
            "score_composite": hit.metadata.get("score_composite", hit.score),
            "source": hit.metadata.get("source"),
            "title": hit.metadata.get("title"),
            "page": hit.page or hit.metadata.get("page"),
            "chunk_index": hit.metadata.get("chunk_index"),
            "text": _hit_text(hit)[:1200],
            "linked_context": [
                {
                    "node_id": _hit_id(context),
                    "modality": context.modality or context.metadata.get("modality"),
                    "text": _hit_text(context)[:900],
                }
                for context in context_hits
            ],
        }

    def _context_hits_for(self, hit: SearchHit, *, context_pool: list[SearchHit]) -> list[SearchHit]:
        if not self.settings.tg_agent_chunk_context_for_table_formula:
            return []
        modality = hit.modality or hit.metadata.get("modality")
        if modality not in {"table", "formula"}:
            return []
        by_id = {_hit_id(item): item for item in context_pool}
        candidates: list[str] = []
        expanded_from = hit.metadata.get("retrieval_expanded_from_node_id")
        if isinstance(expanded_from, str):
            candidates.append(expanded_from)
        relationships = hit.relationships or {}
        for key in ("context_node_ids", "context_prev_node_id", "context_next_node_id", "same_page_node_ids"):
            raw = relationships.get(key)
            if isinstance(raw, str):
                candidates.append(raw)
            elif isinstance(raw, list):
                candidates.extend(item for item in raw if isinstance(item, str))
        result: list[SearchHit] = []
        seen: set[str] = set()
        for node_id in candidates:
            context = by_id.get(node_id)
            if context is None or _hit_id(context) in seen:
                continue
            if (context.modality or context.metadata.get("modality") or "text") != "text":
                continue
            seen.add(_hit_id(context))
            result.append(context)
        return result

    def _apply_grade(self, hit: SearchHit, grade: ChunkGrade | None) -> SearchHit:
        if grade is None:
            return hit
        copy = hit.model_copy(deep=True)
        score = _clamp_score(grade.relevance_score)
        label = grade.relevance_label
        drop_labels = _csv_set(self.settings.tg_agent_chunk_drop_labels)
        drop = bool(grade.drop or label in drop_labels or score < self.settings.tg_agent_chunk_drop_score_threshold)
        before = score_value(copy)
        after = before
        label_delta = 0.0
        if self.settings.tg_agent_chunk_score_adjust_enabled:
            label_delta = _label_float_map(self.settings.tg_agent_chunk_label_score_deltas).get(label, 0.0)
            after = _clamp_score(before + label_delta)
            _set_score(copy, after, score_policy="agent_chunk_label_delta_v1")
        copy.metadata.update(
            {
                "agent_relevance_used": True,
                "agent_relevance_score": score,
                "agent_relevance_label": label,
                "agent_relevance_keep": bool(grade.keep and not drop),
                "agent_relevance_drop": drop,
                "agent_relevance_score_before": before,
                "agent_relevance_score_after": after,
                "agent_score_before": before,
                "agent_label_score_delta": label_delta,
                "agent_score_after": after,
                "agent_score_policy": "agent_chunk_label_delta_v1" if self.settings.tg_agent_chunk_score_adjust_enabled else "agent_chunk_no_score_adjust_v1",
                "agent_relevance_reasoning": grade.reasoning_summary,
                "agent_relevance_needs_context": grade.needs_context,
                "agent_relevance_needs_related_table": grade.needs_related_table,
                "agent_relevance_needs_related_formula": grade.needs_related_formula,
            }
        )
        return copy

    def _apply_related_context_policy(
        self,
        hits: list[SearchHit],
        grades: dict[str, ChunkGrade],
        *,
        context_pool: list[SearchHit],
    ) -> list[SearchHit]:
        if not self.settings.tg_agent_chunk_related_context_enabled:
            return hits
        by_id = {_hit_id(hit): hit for hit in hits}
        additions: list[SearchHit] = []
        fixed_scores = _label_float_map(self.settings.tg_agent_chunk_related_context_fixed_scores)
        existing_modality_deltas = _label_float_map(self.settings.tg_agent_chunk_related_context_existing_modality_deltas)
        existing_text_deltas = _label_float_map(self.settings.tg_agent_chunk_related_context_existing_text_deltas)
        added_modality_deltas = _label_float_map(self.settings.tg_agent_chunk_related_context_added_modality_deltas)
        add_labels = _csv_set(self.settings.tg_agent_chunk_related_context_add_labels)
        for hit in hits:
            modality = hit.modality or hit.metadata.get("modality")
            if modality not in {"table", "formula"}:
                continue
            grade = grades.get(_hit_id(hit))
            if grade is None:
                continue
            label = grade.relevance_label
            context = self._context_hits_for(hit, context_pool=context_pool)
            if not context:
                continue
            context_source = context[0]
            context_id = _hit_id(context_source)
            current_hit = by_id.get(_hit_id(hit))
            if current_hit is None:
                continue
            if context_id in by_id:
                context_hit = by_id[context_id]
                modality_delta = existing_modality_deltas.get(label, 0.0)
                text_delta = existing_text_deltas.get(label, 0.0)
                _add_score_delta(current_hit, modality_delta)
                _add_score_delta(context_hit, text_delta)
                current_hit.metadata.update(
                    {
                        "agent_related_context_node_id": context_id,
                        "agent_related_context_present": True,
                        "agent_related_context_added": False,
                        "agent_related_modality_delta": modality_delta,
                        "agent_related_context_text_delta": text_delta,
                        "agent_related_context_fixed_score": None,
                        "agent_related_context_policy": self.settings.tg_agent_chunk_related_context_trigger_mode,
                        "agent_score_after": score_value(current_hit),
                        "agent_relevance_score_after": score_value(current_hit),
                    }
                )
                context_hit.metadata.update(
                    {
                        "agent_related_context_adjusted": True,
                        "agent_related_context_text_delta": text_delta,
                        "agent_related_context_source_node_id": _hit_id(current_hit),
                    }
                )
                continue
            current_hit.metadata.update(
                {
                    "agent_related_context_node_id": context_id,
                    "agent_related_context_present": False,
                    "agent_related_context_policy": self.settings.tg_agent_chunk_related_context_trigger_mode,
                }
            )
            if not _related_context_should_add(
                label=label,
                add_labels=add_labels,
                mode=self.settings.tg_agent_chunk_related_context_trigger_mode,
            ):
                current_hit.metadata["agent_related_context_added"] = False
                continue
            modality_delta = added_modality_deltas.get(label, 0.0)
            fixed_score = fixed_scores.get(label, 0.0)
            _add_score_delta(current_hit, modality_delta)
            current_hit.metadata.update(
                {
                    "agent_related_context_added": True,
                    "agent_related_modality_delta": modality_delta,
                    "agent_related_context_text_delta": None,
                    "agent_related_context_fixed_score": fixed_score,
                    "agent_score_after": score_value(current_hit),
                    "agent_relevance_score_after": score_value(current_hit),
                }
            )
            context_hit = context_source.model_copy(deep=True)
            _set_score(context_hit, fixed_score, score_policy="agent_context_fixed_by_label_v1")
            context_hit.metadata["retrieval_candidate_pool"] = "agent_grading_context"
            context_hit.metadata["retrieval_expanded_from_node_id"] = _hit_id(current_hit)
            context_hit.metadata["retrieval_expansion_relation"] = "agent_required_context"
            context_hit.metadata["agent_related_context_added"] = True
            context_hit.metadata["agent_related_context_source_node_id"] = _hit_id(current_hit)
            context_hit.metadata["agent_related_context_fixed_score"] = fixed_score
            additions.append(context_hit)
            by_id[_hit_id(context_hit)] = context_hit
        return [*hits, *additions]


def _agent_contract(settings: Settings) -> str:
    labels = settings.tg_agent_chunk_labels
    return (
        "You are a strict RAG evidence chunk relevance grader. "
        "Grade only whether each chunk is relevant to the user question. "
        "Do not judge whether the whole database has enough evidence. "
        f"Allowed labels: {labels}. "
        "For table/formula chunks, use linked_context when provided before judging relevance. "
        "Return only valid JSON.\n\n"
    )


def _hit_id(hit: SearchHit) -> str:
    return hit.node_id or hit.point_id


def _hit_text(hit: SearchHit) -> str:
    return hit.text or hit.table_markdown or hit.formula_latex or hit.caption or hit.ocr_text or ""


def _csv_set(value: str) -> set[str]:
    return {item.strip() for item in (value or "").split(",") if item.strip()}


def _label_float_map(value: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for item in (value or "").split(","):
        if ":" not in item:
            continue
        key, raw_value = item.split(":", 1)
        key = key.strip()
        if not key:
            continue
        try:
            result[key] = float(raw_value.strip())
        except ValueError:
            continue
    return result


def _clamp_score(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _set_score(hit: SearchHit, value: float, *, score_policy: str) -> None:
    score = _clamp_score(value)
    hit.score = score
    hit.metadata["score_composite"] = score
    hit.metadata["score_policy"] = score_policy


def _add_score_delta(hit: SearchHit, delta: float) -> None:
    if not delta:
        return
    _set_score(hit, score_value(hit) + delta, score_policy="agent_chunk_related_context_delta_v1")


def _related_context_should_add(*, label: str, add_labels: set[str], mode: str) -> bool:
    mode = (mode or "label_only").lower()
    if mode != "label_only":
        return False
    return label in add_labels
