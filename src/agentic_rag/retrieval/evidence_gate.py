from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from agentic_rag.config import Settings
from agentic_rag.retrieval.evidence_pack import EvidencePack
from agentic_rag.retrieval.retrieval_plan import RetrievalPlan
from agentic_rag.schemas import SearchHit


_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z][a-zA-Z0-9_./-]*|\d+(?:\.\d+)?%?")
_PAGE_PATTERNS = (
    re.compile(r"\u7b2c\s*(\d+)\s*\u9875"),
    re.compile(r"\bpage\s*(\d+)\b", re.IGNORECASE),
    re.compile(r"\bp\.?\s*(\d+)\b", re.IGNORECASE),
)
_NUMBER_RE = re.compile(r"(?<![A-Za-z])(?:19|20)\d{2}(?!\d)|\d+(?:\.\d+)?%?")
_SOURCE_RE = re.compile(r"[\w\u4e00-\u9fff ._-]+\.(?:pdf|md|docx?|txt|pptx?|xlsx?)", re.IGNORECASE)

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "what",
    "which",
    "with",
    "\u7684",
    "\u4e86",
    "\u5728",
    "\u662f",
    "\u548c",
    "\u4e0e",
    "\u6216",
    "\u4e2d",
    "\u4e0a",
    "\u4e0b",
    "\u7b2c",
    "\u9875",
    "\u8be5",
    "\u8bf7",
    "\u5417",
    "\u591a",
    "\u5c11",
}


class QuerySlots(BaseModel):
    """Rule-extracted query requirements for evidence gating."""

    page_refs: list[int] = Field(default_factory=list)
    numeric_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    modality_requirements: list[str] = Field(default_factory=list)
    keyword_terms: list[str] = Field(default_factory=list)
    required_slots: list[str] = Field(default_factory=list)


class EvidenceGateResult(BaseModel):
    """Serializable wrapper for future node/tool use."""

    pack: EvidencePack


class EvidenceEvaluator:
    """Rule-based evidence support, coverage, and conflict evaluator."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def extract_query_slots(
        self,
        question: str,
        target_modalities: list[str],
        filters: dict[str, Any] | None = None,
    ) -> QuerySlots:
        filters = filters or {}
        page_refs = self._extract_page_refs(question)
        numeric_refs = self._extract_numeric_refs(question)
        source_refs = self._extract_source_refs(question, filters)
        modality_requirements = self._extract_modality_requirements(question, target_modalities)
        keyword_terms = self._extract_keyword_terms(question)

        required_slots: list[str] = []
        if keyword_terms:
            required_slots.append("keyword")
        if page_refs or "page" in modality_requirements:
            required_slots.append("page")
        if source_refs:
            required_slots.append("source")
        if self._numeric_required(numeric_refs, page_refs):
            required_slots.append("numeric")
        for modality in modality_requirements:
            if modality != "page":
                required_slots.append(f"modality:{modality}")

        return QuerySlots(
            page_refs=page_refs,
            numeric_refs=numeric_refs,
            source_refs=source_refs,
            modality_requirements=modality_requirements,
            keyword_terms=keyword_terms,
            required_slots=_dedupe(required_slots),
        )

    def evaluate(
        self,
        question: str,
        hits: list[SearchHit],
        route_hits: dict[str, list[SearchHit]] | None,
        target_modalities: list[str],
        plan: RetrievalPlan | None = None,
        filters: dict[str, Any] | None = None,
    ) -> EvidencePack:
        filters = filters or {}
        route_hits = route_hits or {}
        plan = plan or RetrievalPlan(question=question)
        slots = self.extract_query_slots(question, target_modalities, filters)
        source_coverage = _count_by_key(hits, _hit_source)
        page_coverage = _count_by_key(hits, _hit_page_key)
        modality_coverage = _modality_coverage(hits)
        keyword_coverage = self._keyword_coverage(slots.keyword_terms, hits)

        required_slots = ["hits", *slots.required_slots]
        slot_coverage = self._slot_coverage(
            slots=slots,
            hits=hits,
            keyword_coverage=keyword_coverage,
            modality_coverage=modality_coverage,
        )
        slot_coverage["hits"] = len(hits) >= self.settings.tg_min_evidence_hits
        covered_slots = [slot for slot in required_slots if slot_coverage.get(slot, False)]
        missing_slots = [slot for slot in required_slots if not slot_coverage.get(slot, False)]

        conflict_level, conflict_reasons = self._detect_conflicts(question, hits, plan)
        conflict_detected = conflict_level != "none"
        support_score = self._support_score(
            hits=hits,
            keyword_coverage=keyword_coverage,
            required_slots=required_slots,
            covered_slots=covered_slots,
            conflict_level=conflict_level,
        )
        support_level = self._support_level(support_score, hits, missing_slots)
        claim_supported = (
            support_level in {"partial", "strong"}
            and conflict_level != "high"
            and (not self.settings.tg_required_slot_strict or not missing_slots)
        )

        gate_reasons = self._gate_reasons(missing_slots, support_score, conflict_level, conflict_reasons)
        if conflict_level == "high" and self.settings.tg_allow_refusal:
            gate_decision = "refuse"
        elif claim_supported:
            gate_decision = "pass"
        else:
            gate_decision = "retry"

        supporting_hit_ids = self._supporting_hit_ids(hits)
        return EvidencePack(
            plan=plan,
            hits=hits,
            route_hits=route_hits,
            expanded_hits=hits,
            evidence_ok=gate_decision == "pass",
            evidence_gaps=gate_reasons,
            conflict_detected=conflict_detected,
            claim_supported=claim_supported,
            source_coverage=source_coverage,
            page_coverage=page_coverage,
            modality_coverage=modality_coverage,
            conflict_level=conflict_level,
            missing_slots=missing_slots,
            supporting_hit_ids=supporting_hit_ids,
            support_level=support_level,
            support_score=support_score,
            slot_coverage=slot_coverage,
            required_slots=required_slots,
            covered_slots=covered_slots,
            conflict_reasons=conflict_reasons,
            gate_decision=gate_decision,
            gate_reasons=gate_reasons,
        )

    def _extract_page_refs(self, question: str) -> list[int]:
        refs: list[int] = []
        for pattern in _PAGE_PATTERNS:
            refs.extend(int(match.group(1)) for match in pattern.finditer(question or ""))
        return sorted(set(refs))

    def _extract_numeric_refs(self, question: str) -> list[str]:
        return _dedupe(match.group(0).lower() for match in _NUMBER_RE.finditer(question or ""))

    def _extract_source_refs(self, question: str, filters: dict[str, Any]) -> list[str]:
        refs = [match.group(0).strip() for match in _SOURCE_RE.finditer(question or "")]
        for key in ("source", "doc_id"):
            value = filters.get(key)
            if value:
                refs.append(str(value))
        return _dedupe(refs)

    def _extract_modality_requirements(self, question: str, target_modalities: list[str]) -> list[str]:
        lower = (question or "").lower()
        requirements = [m for m in target_modalities if m in {"table", "page", "image", "formula"}]
        if _contains_any(question, ("\u8868", "\u7edf\u8ba1", "\u5bf9\u6bd4", "\u6392\u540d", "\u767e\u5206\u6bd4", "\u589e\u957f", "\u4e0b\u964d", "\u591a\u5c11")):
            requirements.append("table")
        if _contains_any(question, ("\u7b2c", "\u9875", "\u8be5\u9875", "\u4e0a\u4e0b\u6587", "\u56fe\u4e2d")):
            requirements.append("page")
        if _contains_any(
            question,
            (
                "\u56fe\u7247",
                "\u56fe\u50cf",
                "\u622a\u56fe",
                "\u56fe\u4e2d",
                "\u793a\u610f\u56fe",
                "\u8d8b\u52bf\u56fe",
                "\u89c6\u89c9",
                "\u7167\u7247",
            ),
        ) or any(token in lower for token in ("figure", "fig.", "chart", "screenshot", "diagram", "photo")):
            requirements.append("image")
        if _contains_any(question, ("\u516c\u5f0f", "\u65b9\u7a0b", "\u8868\u8fbe\u5f0f")) or "latex" in lower or "formula" in lower or "equation" in lower:
            requirements.append("formula")
        return _dedupe(requirements)

    def _extract_keyword_terms(self, question: str) -> list[str]:
        terms: list[str] = []
        for token in _TOKEN_RE.findall(question or ""):
            token = token.lower().strip()
            if not token or token in _STOPWORDS:
                continue
            if re.fullmatch(r"\d+(?:\.\d+)?%?", token):
                continue
            terms.append(token)
        return _dedupe(terms)

    def _numeric_required(self, numeric_refs: list[str], page_refs: list[int]) -> bool:
        if not numeric_refs:
            return False
        page_values = {str(page) for page in page_refs}
        non_page_values = [value.rstrip("%") for value in numeric_refs if value.rstrip("%") not in page_values]
        return bool(non_page_values)

    def _keyword_coverage(self, keyword_terms: list[str], hits: list[SearchHit]) -> float:
        if not keyword_terms:
            return 1.0
        evidence_text = " ".join(_hit_evidence_text(hit).lower() for hit in hits)
        if not evidence_text:
            return 0.0
        covered = {term for term in keyword_terms if term in evidence_text}
        return len(covered) / max(1, len(keyword_terms))

    def _slot_coverage(
        self,
        *,
        slots: QuerySlots,
        hits: list[SearchHit],
        keyword_coverage: float,
        modality_coverage: dict[str, int],
    ) -> dict[str, bool]:
        coverage: dict[str, bool] = {}
        non_text_modalities = {"table", "image", "formula"} & set(slots.modality_requirements)
        non_text_covered = any(modality_coverage.get(modality, 0) > 0 for modality in non_text_modalities)
        coverage["keyword"] = (
            keyword_coverage >= self.settings.tg_min_coverage_ratio
            or (non_text_covered and keyword_coverage > 0)
            or (non_text_covered and self.settings.tg_min_coverage_ratio >= 0.8)
        )
        coverage["page"] = self._page_covered(slots.page_refs, hits)
        coverage["source"] = self._source_covered(slots.source_refs, hits)
        coverage["numeric"] = self._numeric_covered(slots.numeric_refs, slots.page_refs, hits)
        for modality in slots.modality_requirements:
            if modality == "page":
                coverage["modality:page"] = coverage["page"]
            else:
                coverage[f"modality:{modality}"] = modality_coverage.get(modality, 0) > 0
        return coverage

    def _page_covered(self, page_refs: list[int], hits: list[SearchHit]) -> bool:
        if page_refs:
            return any(hit.page in page_refs or _metadata_int(hit, "page") in page_refs for hit in hits)
        return any(hit.channel == "page" or hit.page is not None or _metadata_int(hit, "page") is not None for hit in hits)

    def _source_covered(self, source_refs: list[str], hits: list[SearchHit]) -> bool:
        if not source_refs:
            return True
        haystack = " ".join(
            str(value).lower()
            for hit in hits
            for value in (_hit_source(hit), hit.doc_id, hit.metadata.get("source"))
            if value
        )
        return all(ref.lower() in haystack for ref in source_refs)

    def _numeric_covered(self, numeric_refs: list[str], page_refs: list[int], hits: list[SearchHit]) -> bool:
        required = [value for value in numeric_refs if value.rstrip("%") not in {str(page) for page in page_refs}]
        if not required:
            return True
        evidence_text = " ".join(_hit_evidence_text(hit).lower() for hit in hits)
        return any(value.lower() in evidence_text for value in required)

    def _detect_conflicts(
        self,
        question: str,
        hits: list[SearchHit],
        plan: RetrievalPlan | None,
    ) -> tuple[str, list[str]]:
        reasons: list[str] = []
        polarity_by_source: dict[str, set[str]] = {}
        global_polarity: set[str] = set()
        for hit in hits[:6]:
            polarity = _detect_polarity(_hit_evidence_text(hit))
            if not polarity:
                continue
            source = _hit_source(hit) or hit.doc_id or hit.point_id
            polarity_by_source.setdefault(str(source), set()).update(polarity)
            global_polarity.update(polarity)
            if {"positive", "negative"}.issubset(polarity):
                reasons.append("same_hit_positive_negative")

        if {"positive", "negative"}.issubset(global_polarity):
            reasons.append("mixed_positive_negative_evidence")

        numeric_reason = self._numeric_conflict_reason(hits)
        if numeric_reason:
            reasons.append(numeric_reason)

        cross_doc = bool(getattr(plan, "need_cross_doc", False))
        if cross_doc:
            source_polarities = list(polarity_by_source.values())
            if any("positive" in p for p in source_polarities) and any("negative" in p for p in source_polarities):
                reasons.append("cross_source_polarity_conflict")

        reasons = _dedupe(reasons)
        if any(reason in reasons for reason in ("same_hit_positive_negative", "cross_source_polarity_conflict")):
            return "high", reasons
        if "mixed_positive_negative_evidence" in reasons:
            return "medium", reasons
        if "numeric_value_conflict" in reasons:
            return "medium", reasons
        return "none", reasons

    def _numeric_conflict_reason(self, hits: list[SearchHit]) -> str | None:
        values_by_context: dict[str, set[str]] = {}
        for hit in hits[:6]:
            values = {match.group(0) for match in _NUMBER_RE.finditer(_hit_evidence_text(hit))}
            if not values:
                continue
            context = f"{_hit_source(hit) or hit.doc_id}:{hit.page}"
            values_by_context.setdefault(context, set()).update(values)
        if len(values_by_context) < 2:
            return None
        all_values = set().union(*values_by_context.values())
        if len(_normalize_numbers(all_values, self.settings.tg_conflict_numeric_tolerance)) > 1:
            return "numeric_value_conflict"
        return None

    def _support_score(
        self,
        *,
        hits: list[SearchHit],
        keyword_coverage: float,
        required_slots: list[str],
        covered_slots: list[str],
        conflict_level: str,
    ) -> float:
        if not hits:
            return 0.0
        hit_score = min(1.0, len(hits) / max(1, self.settings.tg_min_evidence_hits))
        slot_score = len(covered_slots) / max(1, len(required_slots))
        score = (0.30 * hit_score) + (0.35 * min(1.0, keyword_coverage)) + (0.35 * slot_score)
        if conflict_level == "high":
            score = min(score, 0.2)
        elif conflict_level == "medium":
            score = min(score, 0.55)
        return round(max(0.0, min(1.0, score)), 4)

    def _support_level(self, support_score: float, hits: list[SearchHit], missing_slots: list[str]) -> str:
        if not hits or support_score <= 0:
            return "none"
        if support_score >= self.settings.tg_strong_support_score and not missing_slots:
            return "strong"
        if support_score >= self.settings.tg_min_support_score:
            return "partial"
        return "weak"

    def _gate_reasons(
        self,
        missing_slots: list[str],
        support_score: float,
        conflict_level: str,
        conflict_reasons: list[str],
    ) -> list[str]:
        reasons: list[str] = []
        for slot in missing_slots:
            if slot == "hits":
                reasons.append("insufficient_hits")
            elif slot == "keyword":
                reasons.append("low_keyword_coverage")
            else:
                reasons.append(f"missing_{slot}")
        if support_score < self.settings.tg_min_support_score:
            reasons.append("low_support_score")
        if conflict_level != "none":
            reasons.append("possible_conflict")
            reasons.extend(conflict_reasons)
        return _dedupe(reasons)

    def _supporting_hit_ids(self, hits: list[SearchHit]) -> list[str]:
        ids: list[str] = []
        for hit in hits[: self.settings.context_top_n]:
            ids.append(hit.node_id or hit.point_id)
        return _dedupe(ids)


def _hit_evidence_text(hit: SearchHit) -> str:
    parts = [hit.text or ""]
    if hit.formula_latex:
        parts.append(hit.formula_latex)
    if hit.table_markdown:
        parts.append(hit.table_markdown)
    for key in ("source", "title", "doc_id"):
        value = hit.metadata.get(key)
        if value:
            parts.append(str(value))
    return " ".join(parts)


def _hit_source(hit: SearchHit) -> str | None:
    return str(hit.metadata.get("source") or hit.doc_id or "") or None


def _hit_page_key(hit: SearchHit) -> str | None:
    page = hit.page if hit.page is not None else _metadata_int(hit, "page")
    if page is None:
        return None
    return f"{_hit_source(hit) or hit.doc_id or 'unknown'}:{page}"


def _metadata_int(hit: SearchHit, key: str) -> int | None:
    value = hit.metadata.get(key)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _modality_coverage(hits: list[SearchHit]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for hit in hits:
        modalities = {hit.modality or str(hit.metadata.get("modality") or "text")}
        if hit.table_markdown or hit.modality == "table" or hit.metadata.get("modality") == "table":
            modalities.add("table")
        if hit.image_path or hit.modality == "image" or hit.metadata.get("modality") == "image":
            modalities.add("image")
        if hit.formula_latex or hit.modality == "formula" or hit.metadata.get("modality") == "formula":
            modalities.add("formula")
        if hit.channel == "page" or hit.page is not None or _metadata_int(hit, "page") is not None:
            modalities.add("page")
        for modality in modalities:
            counts[modality] = counts.get(modality, 0) + 1
    return counts


def _count_by_key(hits: list[SearchHit], key_fn) -> dict[str, int]:
    counts: dict[str, int] = {}
    for hit in hits:
        raw = key_fn(hit)
        if raw is None or raw == "":
            continue
        key = str(raw)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _detect_polarity(text: str) -> set[str]:
    lower = f" {text.lower()} "
    polarity: set[str] = set()
    if re.search(r"\b(no|false|not|cannot|can't)\b", lower) or re.search(
        r"\u4e0d\u662f|\u4e0d\u652f\u6301|\u4e0d\u53ef\u4ee5|\u4e0d\u5b58\u5728|\u6ca1\u6709|\u65e0",
        text,
    ):
        polarity.add("negative")
    if re.search(r"\b(yes|true|support|supports|supported)\b", lower) or re.search(
        r"(?<!\u4e0d)\u662f|(?<!\u4e0d)\u652f\u6301|(?<!\u4e0d)\u53ef\u4ee5|(?<!\u4e0d)\u5b58\u5728|(?<!\u6ca1)\u6709",
        text,
    ):
        polarity.add("positive")
    return polarity


def _normalize_numbers(values: set[str], tolerance: float) -> set[str]:
    if tolerance <= 0:
        return values
    normalized: set[str] = set()
    for value in values:
        try:
            number = float(value.rstrip("%"))
        except ValueError:
            normalized.add(value)
            continue
        bucket = round(number / tolerance) if tolerance else number
        normalized.add(f"{bucket}{'%' if value.endswith('%') else ''}")
    return normalized


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _dedupe(values) -> list:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
