from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from agentic_rag.config import Settings
from agentic_rag.models.json_llm import generate_json
from agentic_rag.models.providers import LLMClient
from agentic_rag.retrieval.query_variants import has_query_anchor_overlap
from agentic_rag.retrieval.scoring import mark_removed_hit, score_value
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


@dataclass(slots=True)
class AgentChunkGradingOutput:
    hits: list[SearchHit]
    removed_hits: list[SearchHit]
    grade_cache: dict[str, dict[str, Any]]
    cache_hits: int = 0
    llm_graded_hits: int = 0


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
        return self.grade_hits_with_removed(question=question, hits=hits, context_pool=context_pool).hits

    def grade_hits_with_removed(
        self,
        *,
        question: str,
        hits: list[SearchHit],
        context_pool: list[SearchHit] | None = None,
        grade_cache: dict[str, Any] | None = None,
    ) -> AgentChunkGradingOutput:
        if not self.settings.tg_agent_chunk_grading_enabled or not hits:
            return AgentChunkGradingOutput(hits=hits, removed_hits=[], grade_cache=dict(grade_cache or {}))
        context_pool = context_pool or hits
        selected = self._select_hits(hits)
        cache_enabled = bool(self.settings.tg_agent_chunk_grade_cache_enabled)
        incoming_cache = dict(grade_cache or {}) if cache_enabled else {}
        grades: dict[str, ChunkGrade] = {}
        grade_sources: dict[str, str] = {}
        for hit in hits:
            key = _hit_id(hit)
            cached = _cached_grade(incoming_cache.get(key))
            if cached is None:
                continue
            grades[key] = cached
            grade_sources[key] = "cache"

        uncached_selected = [
            hit
            for hit in selected
            if _hit_id(hit) not in grades
        ]
        if uncached_selected:
            payload = [
                self._grade_payload(hit, context_pool=context_pool)
                for hit in uncached_selected
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
            for grade in result.grades:
                grades[grade.node_id] = grade
                grade_sources[grade.node_id] = "agent"

        if not grades:
            return AgentChunkGradingOutput(hits=hits, removed_hits=[], grade_cache=incoming_cache)

        graded = [
            self._apply_grade(
                hit,
                grades.get(_hit_id(hit)),
                question=question,
                grade_source=grade_sources.get(_hit_id(hit), "agent"),
            )
            for hit in hits
        ]
        graded = self._apply_related_context_policy(graded, grades, context_pool=context_pool)
        removed_hits: list[SearchHit] = []
        if self.settings.tg_agent_chunk_drop_enabled:
            kept: list[SearchHit] = []
            for rank, hit in enumerate(graded, start=1):
                if hit.metadata.get("agent_relevance_drop"):
                    removed_hits.append(self._mark_agent_removed(hit, previous_rank=rank))
                else:
                    kept.append(hit)
            graded = kept
        updated_cache = dict(incoming_cache)
        if cache_enabled:
            for key, grade in grades.items():
                updated_cache[key] = _grade_to_cache(grade)
        cache_hits = sum(1 for source in grade_sources.values() if source == "cache")
        llm_graded_hits = sum(1 for source in grade_sources.values() if source == "agent")
        return AgentChunkGradingOutput(
            hits=graded,
            removed_hits=removed_hits,
            grade_cache=updated_cache,
            cache_hits=cache_hits,
            llm_graded_hits=llm_graded_hits,
        )

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

    def _apply_grade(
        self,
        hit: SearchHit,
        grade: ChunkGrade | None,
        *,
        question: str,
        grade_source: str = "agent",
    ) -> SearchHit:
        if grade is None:
            return hit
        copy = hit.model_copy(deep=True)
        cache_key = _hit_id(copy)
        score = _clamp_score(grade.relevance_score)
        label = grade.relevance_label
        before = score_value(copy)
        already_applied = bool(copy.metadata.get("agent_relevance_used")) and copy.metadata.get("agent_grade_cache_key") == cache_key
        drop, drop_reason, protected_reason = self._should_drop(
            hit=copy,
            grade=grade,
            label=label,
            score=score,
            score_before=before,
            question=question,
        )
        after = before
        label_delta = 0.0
        if self.settings.tg_agent_chunk_score_adjust_enabled and not already_applied:
            label_delta = _label_float_map(self.settings.tg_agent_chunk_label_score_deltas).get(label, 0.0)
            after = _clamp_score(before + label_delta)
            _set_score(copy, after, score_policy="agent_chunk_label_delta_v1")
        copy.metadata.update(
            {
                "agent_relevance_used": True,
                "agent_relevance_source": grade_source,
                "agent_relevance_cache_hit": grade_source == "cache",
                "agent_grade_cache_key": cache_key,
                "agent_relevance_score": score,
                "agent_relevance_label": label,
                "agent_relevance_keep": bool(not drop),
                "agent_relevance_drop": drop,
                "agent_relevance_drop_candidate": bool(drop_reason),
                "agent_relevance_drop_reason": drop_reason,
                "agent_relevance_drop_protected": bool(protected_reason),
                "agent_relevance_drop_protected_reason": protected_reason,
                "agent_relevance_score_before": before,
                "agent_relevance_score_after": after,
                "agent_relevance_score_adjust_reused": already_applied,
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

    def _should_drop(
        self,
        *,
        hit: SearchHit,
        grade: ChunkGrade,
        label: str,
        score: float,
        score_before: float,
        question: str,
    ) -> tuple[bool, str | None, str | None]:
        if not self.settings.tg_agent_chunk_drop_enabled:
            return False, None, None
        drop_labels = _csv_set(self.settings.tg_agent_chunk_drop_labels)
        threshold = self.settings.tg_agent_chunk_drop_score_threshold
        label_drop = label in drop_labels
        score_drop = score < threshold
        if self.settings.tg_agent_chunk_drop_require_label_and_score:
            drop = label_drop and score_drop
            reason = "label_and_score_threshold" if drop else None
        else:
            drop = bool(grade.drop or label_drop or score_drop)
            if grade.drop:
                reason = "agent_drop_flag"
            elif label_drop:
                reason = "label"
            elif score_drop:
                reason = "score_threshold"
            else:
                reason = None
        if not drop:
            return False, reason, None

        protect_by_score = self.settings.tg_agent_chunk_drop_protect_drop_labels or label not in drop_labels
        rerank_score = _metadata_float(hit, "rerank_score")
        if protect_by_score and score_before >= self.settings.tg_agent_chunk_drop_protect_prior_score:
            return False, reason, "prior_score"
        if protect_by_score and rerank_score is not None and rerank_score >= self.settings.tg_agent_chunk_drop_protect_rerank_score:
            return False, reason, "rerank_score"
        if self.settings.tg_agent_chunk_drop_protect_anchors and has_query_anchor_overlap(question, hit):
            return False, reason, "query_anchor_overlap"
        return True, reason, None

    def _mark_agent_removed(self, hit: SearchHit, *, previous_rank: int) -> SearchHit:
        label = str(hit.metadata.get("agent_relevance_label") or "")
        score = hit.metadata.get("agent_relevance_score")
        drop_labels = _csv_set(self.settings.tg_agent_chunk_drop_labels)
        configured_reason = str(hit.metadata.get("agent_relevance_drop_reason") or "")
        if configured_reason == "label_and_score_threshold":
            reason = "agent_low_relevance_score" if label not in drop_labels else "agent_irrelevant_label"
            drop_reason = "label_and_score_threshold"
        elif hit.metadata.get("agent_relevance_drop") and label not in drop_labels:
            reason = "agent_chunk_drop"
            drop_reason = "agent_drop_flag"
        elif label in drop_labels:
            reason = "agent_irrelevant_label"
            drop_reason = "label"
        else:
            reason = "agent_low_relevance_score"
            drop_reason = "score_threshold"
        detail = (
            f"label={label or 'unknown'}; score={score}; "
            f"threshold={self.settings.tg_agent_chunk_drop_score_threshold}"
        )
        hit.metadata.update(
            {
                "agent_relevance_drop_reason": drop_reason,
                "agent_relevance_drop_threshold": self.settings.tg_agent_chunk_drop_score_threshold,
            }
        )
        return mark_removed_hit(
            hit,
            stage="agent_chunk_grading",
            reason=reason,
            detail=detail,
            previous_rank=previous_rank,
        )

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
            if current_hit.metadata.get("agent_related_context_policy_applied"):
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
                        "agent_related_context_policy_applied": True,
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
                    "agent_related_context_policy_applied": True,
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


def _metadata_float(hit: SearchHit, key: str) -> float | None:
    value = hit.metadata.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _cached_grade(value: Any) -> ChunkGrade | None:
    if isinstance(value, ChunkGrade):
        return value
    if not isinstance(value, dict):
        return None
    try:
        return ChunkGrade.model_validate(value)
    except Exception:
        return None


def _grade_to_cache(grade: ChunkGrade) -> dict[str, Any]:
    return grade.model_dump(mode="json")


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
