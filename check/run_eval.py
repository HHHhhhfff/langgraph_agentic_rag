from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from check.metrics import compute_metrics, estimate_tokens, normalize_text, ranking_metrics, summarize_records


STAGE_NAMES = (
    "initial_recall",
    "initial_retrieval",
    "initial_expanded",
    "rerank",
    "agent_chunk_grading",
    "evidence_gate",
    "retry_1_retrieval",
    "retry_1_expanded",
    "retry_1_rerank",
    "local_recheck",
    "final_after_retry",
    "final_output",
)
STAGE_LABELS = {
    "initial_recall": "初步召回",
    "initial_retrieval": "Initial retrieval",
    "initial_expanded": "Initial expanded",
    "rerank": "Rerank 后",
    "agent_chunk_grading": "Agent chunk grading",
    "evidence_gate": "Evidence gate",
    "retry_1_retrieval": "Retry 1 retrieval",
    "retry_1_expanded": "Retry 1 expanded",
    "retry_1_rerank": "Retry 1 rerank",
    "local_recheck": "局部重检后",
    "final_after_retry": "Final after retry",
    "final_output": "最终输出",
}
QUERY_RECORD_STAGE_NAMES = STAGE_NAMES
QUERY_RECORD_STAGE_LABELS = {
    "initial_recall": "初步召回",
    "initial_retrieval": "Initial retrieval",
    "initial_expanded": "Initial expanded",
    "rerank": "Rerank 后",
    "agent_chunk_grading": "Agent chunk grading",
    "evidence_gate": "Evidence gate",
    "retry_1_retrieval": "Retry 1 retrieval",
    "retry_1_expanded": "Retry 1 expanded",
    "retry_1_rerank": "Retry 1 rerank",
    "local_recheck": "局部重检后",
    "final_after_retry": "Final after retry",
    "final_output": "最终输出",
}
RETRIEVAL_REPORT_METRICS = (
    ("hit_rate", "Hit Rate（命中率）"),
    ("mrr", "MRR（Mean Reciprocal Rank）"),
    ("precision_at_1", "Precision@1（精确率 Top1）"),
    ("precision_at_3", "Precision@3（精确率 Top3）"),
    ("precision", "Precision（实际返回精确率）"),
    ("recall", "Chunk Recall@k（候选集内去重 chunk 召回率）"),
    ("ap", "AP（Average Precision）"),
    ("ndcg", "nDCG（Normalized Discounted Cumulative Gain）"),
    ("page_hit_rate", "Page Hit Rate（页级命中率）"),
    ("page_mrr", "Page MRR（页级首命中倒数排名）"),
    ("page_precision", "Page Precision（返回页精确率）"),
    ("page_recall", "Page Recall（标准页召回率）"),
    ("bbox_hit_rate", "BBox Hit Rate"),
    ("bbox_precision", "BBox Precision"),
    ("bbox_recall", "BBox Recall"),
    ("bbox_max_iou", "BBox Max IoU"),
)
BBOX_IOU_THRESHOLD = 0.5
AI_REPORT_FIELDS = (
    ("ai_correctness", "Correctness（正确性）"),
    ("ai_completeness", "Completeness（完整性）"),
    ("ai_relevance", "Relevance（相关性）"),
    ("ai_overall", "Overall（综合评分）"),
    ("ai_score_100", "Score 100（百分制）"),
)
TOKEN_REPORT_FIELDS = (
    "query_tokens_est",
    "prompt_tokens_est",
    "answer_tokens_est",
    "total_tokens_est",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "ai_judge_prompt_tokens_est",
    "ai_judge_answer_tokens_est",
    "ai_judge_total_tokens_est",
)
TIMING_DEBUG_FIELDS = (
    "embedding_time_ms",
    "retrieval_time_ms",
    "rerank_time_ms",
    "prompt_build_time_ms",
    "generation_time_ms",
    "response_time_ms",
)
CONFIG_SNAPSHOT_FIELDS = (
    "qdrant_url",
    "qdrant_collection",
    "qdrant_recreate_collection",
    "embedding_base_url",
    "embedding_model",
    "embedding_dimensions",
    "embedding_batch_size",
    "llm_base_url",
    "llm_model",
    "rerank_enabled",
    "rerank_base_url",
    "rerank_model",
    "rerank_top_n",
    "context_top_n",
    "retrieval_top_k",
    "retrieval_min_score",
    "bm25_enabled",
    "bm25_top_k",
    "page_top_k",
    "table_top_k",
    "rrf_top_k",
    "rrf_k",
    "rel_expand_steps",
    "rel_expand_pages",
    "ingestion_engine",
    "multimodal_enabled",
    "pdf_parser",
    "text_chunk_parser",
    "chunk_size",
    "chunk_overlap",
    "chunk_min_length",
    "enable_mineru",
    "mineru_mode",
)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []


def _parse_stage_list(value: Any) -> tuple[str, ...]:
    if not value:
        return STAGE_NAMES
    if isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value]
    else:
        items = [part.strip() for part in str(value).split(",")]
    stages = tuple(item for item in items if item)
    return stages or STAGE_NAMES


def _case_id(index: int, row: dict[str, Any]) -> str:
    value = row.get("id") or row.get("case_id")
    return str(value) if value else f"case_{index:04d}"


def load_cases(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"dataset not found: {path}")

    rows: list[dict[str, Any]] = []
    if path.suffix.lower() == ".jsonl":
        with path.open("r", encoding="utf-8-sig") as handle:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSONL at line {line_no}: {exc}") from exc
                if not isinstance(row, dict):
                    raise ValueError(f"line {line_no} must be a JSON object")
                rows.append(row)
    elif path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, list):
            raise ValueError("JSON dataset must be a list of objects")
        rows = data
    else:
        raise ValueError("dataset must be .jsonl or .json")

    cases: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"case {index} must be a JSON object")
        question = str(row.get("question", "")).strip()
        if not question:
            raise ValueError(f"case {index} is missing required field: question")
        case = dict(row)
        case["id"] = _case_id(index, row)
        case["question"] = question
        cases.append(case)
        if limit and len(cases) >= limit:
            break
    return cases


def citation_to_dict(citation: Any) -> dict[str, Any]:
    if hasattr(citation, "model_dump"):
        return citation.model_dump()
    if isinstance(citation, dict):
        return citation
    return {"source": str(citation)}


def build_filters(args: argparse.Namespace, case: dict[str, Any]) -> dict[str, Any] | None:
    filters: dict[str, Any] = {}
    case_filters = case.get("metadata_filter") or case.get("filters")
    if isinstance(case_filters, dict):
        filters.update(case_filters)
    if args.source:
        filters["source"] = args.source
    elif getattr(args, "use_expected_source_filter", False):
        expected_sources = _as_list(case.get("expected_sources") or case.get("expected_source"))
        if expected_sources:
            filters["source"] = expected_sources[0]
    if args.tag:
        filters["tags"] = args.tag
    return filters or None


def optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def numeric_values(records: list[dict[str, Any]], metric_name: str) -> list[float]:
    values: list[float] = []
    for record in records:
        value = (record.get("metrics") or {}).get(metric_name)
        if isinstance(value, bool):
            values.append(1.0 if value else 0.0)
        elif isinstance(value, (int, float)):
            values.append(float(value))
    return values


def debug_timing_values(records: list[dict[str, Any]], timing_name: str) -> list[float]:
    values: list[float] = []
    for record in records:
        timings = (record.get("model_debug") or {}).get("timings_ms") or {}
        if not isinstance(timings, dict):
            continue
        value = timings.get(timing_name)
        if isinstance(value, (int, float)):
            values.append(float(value))
    return values


def mean_or_none(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def token_usage_from_case(case: dict[str, Any]) -> dict[str, int | float]:
    usage: dict[str, int | float] = {}
    nested = case.get("token_usage") or case.get("usage")
    if isinstance(nested, dict):
        for key, value in nested.items():
            number = optional_float(value)
            if number is not None:
                usage[str(key)] = number
    for key in (
        "prompt_tokens",
        "completion_tokens",
        "answer_tokens",
        "total_tokens",
        "prompt_tokens_est",
        "answer_tokens_est",
        "total_tokens_est",
    ):
        number = optional_float(case.get(key))
        if number is not None:
            usage[key] = number
    return usage


def ranked_hits_from_case(case: dict[str, Any]) -> list[dict[str, Any]]:
    rows = case.get("ranked_hits") or case.get("retrieved_hits") or case.get("hits")
    if not isinstance(rows, list):
        return []
    hits: list[dict[str, Any]] = []
    for index, item in enumerate(rows, start=1):
        if isinstance(item, dict):
            hit = dict(item)
        else:
            hit = {"source": str(item)}
        hit.setdefault("rank", index)
        hits.append(hit)
    return hits


def _add_specs(specs: list[dict[str, Any]], field: str, values: Any) -> None:
    for value in _as_list(values):
        specs.append({field: value, "grade": 1.0})


def relevance_specs(case: dict[str, Any]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    docs = case.get("relevant_docs") or case.get("expected_docs") or case.get("expected_documents")
    if isinstance(docs, list):
        for doc in docs:
            if isinstance(doc, dict):
                spec = {
                    key: doc[key]
                    for key in ("point_id", "node_id", "doc_id", "source", "title", "chunk_index", "page")
                    if doc.get(key) not in (None, "")
                }
                spec["grade"] = optional_float(doc.get("grade") or doc.get("relevance")) or 1.0
                if len(spec) > 1:
                    specs.append(spec)
            elif str(doc).strip():
                specs.append({"any_id": str(doc), "grade": 1.0})
    _add_specs(specs, "any_id", case.get("relevant_ids"))
    _add_specs(specs, "source", case.get("relevant_sources"))
    _add_specs(specs, "title", case.get("relevant_titles"))

    if not specs:
        _add_specs(specs, "any_id", case.get("expected_ids"))
        _add_specs(specs, "source", case.get("expected_sources") or case.get("expected_source"))
        _add_specs(specs, "title", case.get("expected_titles"))
    return specs


def _hit_value(hit: dict[str, Any], field: str) -> Any:
    if field in hit and hit.get(field) not in (None, ""):
        return hit.get(field)
    metadata = hit.get("metadata")
    if isinstance(metadata, dict):
        return metadata.get(field)
    return None


def _value_matches(field: str, expected: Any, actual: Any) -> bool:
    if actual is None:
        return False
    expected_norm = normalize_text(expected)
    actual_norm = normalize_text(actual)
    if not expected_norm or not actual_norm:
        return False
    if field in {"source", "title"}:
        return expected_norm in actual_norm or actual_norm in expected_norm
    return expected_norm == actual_norm


def _int_value(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _matches_spec(hit: dict[str, Any], spec: dict[str, Any], *, page_tolerance: int = 0) -> tuple[bool, str | None]:
    checks = {key: value for key, value in spec.items() if key != "grade" and value not in (None, "")}
    if not checks:
        return False, None
    reasons: list[str] = []
    for field, expected in checks.items():
        if field == "any_id":
            if not any(
                _value_matches(id_field, expected, _hit_value(hit, id_field))
                for id_field in ("point_id", "node_id", "doc_id")
            ):
                return False, None
            reasons.append("any_id")
            continue
        if field == "page":
            expected_page = _int_value(expected)
            actual_page = _int_value(_hit_value(hit, field))
            if expected_page is None or actual_page is None:
                return False, None
            if actual_page == expected_page:
                reasons.append("exact_page")
                continue
            if page_tolerance > 0 and abs(actual_page - expected_page) <= page_tolerance:
                reasons.append("page_tolerance")
                continue
            return False, None
            continue
        if not _value_matches(field, expected, _hit_value(hit, field)):
            return False, None
        reasons.append(field)
    return True, "+".join(reasons) if reasons else "match"


def _spec_is_exhaustive_chunk(spec: dict[str, Any]) -> bool:
    keys = {key for key, value in spec.items() if key != "grade" and value not in (None, "")}
    if keys & {"point_id", "node_id"}:
        return True
    if "chunk_index" in keys and (keys & {"source", "doc_id", "title", "page"}):
        return True
    return False


def _hit_has_required_fields(hit: dict[str, Any], spec: dict[str, Any]) -> bool:
    checks = [key for key, value in spec.items() if key != "grade" and value not in (None, "")]
    if not checks:
        return False
    if "any_id" in checks:
        return any(_hit_value(hit, field) not in (None, "") for field in ("point_id", "node_id", "doc_id"))
    return all(_hit_value(hit, field) not in (None, "") for field in checks)


def _page_specs(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    page_specs: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for spec in specs:
        page = _int_value(spec.get("page"))
        if page is None:
            continue
        row = {
            key: spec[key]
            for key in ("source", "doc_id", "title")
            if spec.get(key) not in (None, "")
        }
        row["page"] = page
        key = tuple(f"{name}={normalize_text(value)}" for name, value in sorted(row.items()))
        if key in seen:
            continue
        seen.add(key)
        page_specs.append(row)
    return page_specs


def _hit_page_key(hit: dict[str, Any]) -> tuple[str, int] | None:
    page = _int_value(_hit_value(hit, "page"))
    if page is None:
        return None
    identity = (
        _hit_value(hit, "source")
        or _hit_value(hit, "doc_id")
        or _hit_value(hit, "title")
        or ""
    )
    return normalize_text(identity), page


def _hit_chunk_key(hit: dict[str, Any], index: int) -> str:
    parts: list[str] = []
    for field in ("node_id", "point_id", "source", "doc_id", "title", "page", "chunk_index"):
        value = _hit_value(hit, field)
        if value not in (None, ""):
            parts.append(f"{field}={normalize_text(value)}")
    if parts:
        return "chunk:" + "|".join(parts)
    chunk_index = _hit_value(hit, "chunk_index")
    if chunk_index not in (None, ""):
        identity = (
            _hit_value(hit, "doc_id")
            or _hit_value(hit, "source")
            or _hit_value(hit, "title")
            or ""
        )
        page = _hit_value(hit, "page")
        return f"chunk:{normalize_text(identity)}:{page}:{chunk_index}"
    text = normalize_text(_hit_value(hit, "text") or hit.get("text") or "")
    if text:
        identity = _hit_value(hit, "source") or _hit_value(hit, "doc_id") or ""
        return f"text:{normalize_text(identity)}:{text[:200]}"
    return f"row:{index}"


def candidate_chunk_recall_at_k(
    hits: list[dict[str, Any]],
    grades: list[float],
    *,
    k: int,
    denominator_keys: set[str] | None = None,
) -> float:
    """Recall over relevant unique chunks seen across the pipeline."""

    top_relevant_keys: set[str] = set()
    cutoff = max(1, k)
    for index, (hit, grade) in enumerate(zip(hits, grades), start=1):
        if float(grade or 0.0) <= 0:
            continue
        key = _hit_chunk_key(hit, index)
        if index <= cutoff:
            top_relevant_keys.add(key)
    relevant_keys = denominator_keys or set(top_relevant_keys)
    if not relevant_keys:
        return 0.0
    return float(len(top_relevant_keys)) / float(len(relevant_keys))


def global_relevant_chunk_keys_by_stage(
    hits_by_stage: dict[str, list[dict[str, Any]]],
    specs: list[dict[str, Any]],
    *,
    page_tolerance: int = 0,
) -> dict[str, set[str]]:
    stage_keys: dict[str, set[str]] = {}
    all_keys: set[str] = set()
    for stage, hits in hits_by_stage.items():
        copied_hits = [dict(hit) for hit in hits]
        can_judge = not copied_hits or any(
            _hit_has_required_fields(hit, spec)
            for hit in copied_hits
            for spec in specs
        )
        if not can_judge:
            stage_keys[stage] = set()
            continue
        grades, _ideal = relevance_grades_for_hits(copied_hits, specs, page_tolerance=page_tolerance)
        keys = {
            _hit_chunk_key(hit, index)
            for index, (hit, grade) in enumerate(zip(copied_hits, grades), start=1)
            if float(grade or 0.0) > 0
        }
        stage_keys[stage] = keys
        all_keys.update(keys)
    stage_keys["__all__"] = all_keys
    return stage_keys


def page_level_metrics(
    hits: list[dict[str, Any]],
    specs: list[dict[str, Any]],
    *,
    page_tolerance: int = 0,
) -> dict[str, float | None]:
    expected = _page_specs(specs)
    if not expected:
        return {
            "page_hit_rate": None,
            "page_mrr": None,
            "page_precision": None,
            "page_recall": None,
        }
    if not hits:
        return {
            "page_hit_rate": 0.0,
            "page_mrr": 0.0,
            "page_precision": 0.0,
            "page_recall": 0.0,
        }
    if not any(
        _hit_has_required_fields(hit, spec)
        for hit in hits
        for spec in expected
    ):
        return {
            "page_hit_rate": None,
            "page_mrr": None,
            "page_precision": None,
            "page_recall": None,
        }

    covered_expected: set[int] = set()
    returned_pages: dict[tuple[str, int], bool] = {}
    first_rank: int | None = None
    for rank, hit in enumerate(hits, start=1):
        page_key = _hit_page_key(hit)
        matched = False
        for index, spec in enumerate(expected):
            ok, _reason = _matches_spec(hit, spec, page_tolerance=page_tolerance)
            if ok:
                matched = True
                covered_expected.add(index)
        if matched and first_rank is None:
            first_rank = rank
        if page_key is not None:
            returned_pages[page_key] = returned_pages.get(page_key, False) or matched

    relevant_pages = sum(1 for matched in returned_pages.values() if matched)
    returned_page_count = len(returned_pages)
    return {
        "page_hit_rate": 1.0 if covered_expected else 0.0,
        "page_mrr": (1.0 / float(first_rank)) if first_rank else 0.0,
        "page_precision": (float(relevant_pages) / float(returned_page_count)) if returned_page_count else 0.0,
        "page_recall": float(len(covered_expected)) / float(len(expected)),
    }


def bbox_specs(case: dict[str, Any]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for doc in case.get("relevant_docs") or []:
        if not isinstance(doc, dict):
            continue
        for bbox in _flatten_bbox_groups(doc.get("rel_bbox") or doc.get("bbox")):
            spec = {
                key: doc[key]
                for key in ("source", "doc_id", "title", "page")
                if doc.get(key) not in (None, "")
            }
            spec["bbox"] = bbox
            spec["bbox_coordinate_system"] = "sciqa_rel_bbox_1000" if doc.get("rel_bbox") else "sciqa_bbox_raw"
            if doc.get("subimg_type") is not None:
                spec["subimg_type"] = doc.get("subimg_type")
            specs.append(spec)
    meta = case.get("sciqa_meta") or case.get("metadata") or {}
    if isinstance(meta, dict):
        source = None
        expected_sources = _as_list(case.get("expected_sources") or case.get("expected_source"))
        if expected_sources:
            source = expected_sources[0]
        pages = _as_list(meta.get("evidence_page"))
        bboxes = _flatten_bbox_groups(meta.get("rel_bbox") or meta.get("bbox"))
        types = meta.get("subimg_type")
        for index, bbox in enumerate(bboxes):
            page = pages[min(index, len(pages) - 1)] if pages else None
            spec = {"bbox": bbox, "bbox_coordinate_system": "sciqa_rel_bbox_1000" if meta.get("rel_bbox") else "sciqa_bbox_raw"}
            if source:
                spec["source"] = source
            if page not in (None, ""):
                spec["page"] = page
            if types is not None:
                spec["subimg_type"] = types
            specs.append(spec)
    return _dedupe_bbox_specs(specs)


def _flatten_bbox_groups(value: Any) -> list[list[float]]:
    if value in (None, ""):
        return []
    boxes: list[list[float]] = []

    def walk(item: Any) -> None:
        if isinstance(item, (list, tuple)):
            if len(item) >= 4 and all(_float_or_none(v) is not None for v in item[:4]):
                boxes.append([float(_float_or_none(v) or 0.0) for v in item[:4]])
                return
            for child in item:
                walk(child)

    walk(value)
    return boxes


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _dedupe_bbox_specs(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for spec in specs:
        box = _normalize_bbox_1000(spec.get("bbox"))
        if box is None:
            continue
        spec = dict(spec)
        spec["bbox"] = box
        key = "|".join(
            [
                normalize_text(spec.get("source") or ""),
                normalize_text(spec.get("doc_id") or ""),
                normalize_text(spec.get("title") or ""),
                str(_int_value(spec.get("page"))),
                ",".join(f"{v:.3f}" for v in box),
            ]
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(spec)
    return unique


def _normalize_bbox_1000(value: Any) -> list[float] | None:
    boxes = _flatten_bbox_groups(value)
    if not boxes:
        return None
    x0, y0, x1, y1 = boxes[0]
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)
    max_coord = max(abs(x0), abs(y0), abs(x1), abs(y1))
    if max_coord <= 1.0:
        return [x0 * 1000.0, y0 * 1000.0, x1 * 1000.0, y1 * 1000.0]
    if max_coord <= 1000.0:
        return [x0, y0, x1, y1]
    return None


def _hit_pages(hit: dict[str, Any]) -> list[int]:
    pages: list[int] = []
    raw_pages = _hit_value(hit, "pages")
    if isinstance(raw_pages, list):
        for value in raw_pages:
            page = _int_value(value)
            if page is not None and page not in pages:
                pages.append(page)
    page = _int_value(_hit_value(hit, "page"))
    if page is not None and page not in pages:
        pages.append(page)
    return pages


def _page_matches(actual: int | None, expected: int | None, *, page_tolerance: int) -> bool:
    if actual is None or expected is None:
        return False
    return actual == expected or (page_tolerance > 0 and abs(actual - expected) <= page_tolerance)


def _hit_bboxes_1000(
    hit: dict[str, Any],
    *,
    page: int | None = None,
    page_tolerance: int = 0,
) -> list[list[float]]:
    span_boxes: list[list[float]] = []
    page_spans = _hit_value(hit, "page_spans")
    if isinstance(page_spans, list):
        for span in page_spans:
            if not isinstance(span, dict):
                continue
            span_page = _int_value(span.get("page"))
            if page is not None and not _page_matches(span_page, page, page_tolerance=page_tolerance):
                continue
            for box in _flatten_bbox_groups(span.get("bbox_items") or span.get("bbox")):
                normalized = _normalize_bbox_1000(box)
                if normalized is not None:
                    span_boxes.append(normalized)
    if span_boxes:
        return span_boxes

    bbox_by_page = _hit_value(hit, "bbox_by_page")
    if isinstance(bbox_by_page, dict):
        for raw_page, raw_boxes in bbox_by_page.items():
            box_page = _int_value(raw_page)
            if page is not None and not _page_matches(box_page, page, page_tolerance=page_tolerance):
                continue
            for box in _flatten_bbox_groups(raw_boxes):
                normalized = _normalize_bbox_1000(box)
                if normalized is not None:
                    span_boxes.append(normalized)
    if span_boxes:
        return span_boxes

    if page is not None:
        pages = _hit_pages(hit)
        if pages and not any(_page_matches(actual, page, page_tolerance=page_tolerance) for actual in pages):
            return []

    boxes = [_normalize_bbox_1000(box) for box in _flatten_bbox_groups(_hit_value(hit, "bbox_items"))]
    boxes = [box for box in boxes if box is not None]
    if boxes:
        return boxes
    fallback = _normalize_bbox_1000(_hit_value(hit, "bbox"))
    return [fallback] if fallback is not None else []


def _bbox_iou(left: list[float], right: list[float]) -> float:
    lx0, ly0, lx1, ly1 = left
    rx0, ry0, rx1, ry1 = right
    inter_x0 = max(lx0, rx0)
    inter_y0 = max(ly0, ry0)
    inter_x1 = min(lx1, rx1)
    inter_y1 = min(ly1, ry1)
    inter_area = max(0.0, inter_x1 - inter_x0) * max(0.0, inter_y1 - inter_y0)
    left_area = max(0.0, lx1 - lx0) * max(0.0, ly1 - ly0)
    right_area = max(0.0, rx1 - rx0) * max(0.0, ry1 - ry0)
    union = left_area + right_area - inter_area
    return inter_area / union if union > 0 else 0.0


def _bbox_source_page_matches(hit: dict[str, Any], spec: dict[str, Any], *, page_tolerance: int) -> bool:
    for field in ("source", "doc_id", "title"):
        expected = spec.get(field)
        if expected not in (None, "") and not _value_matches(field, expected, _hit_value(hit, field)):
            return False
    expected_page = _int_value(spec.get("page"))
    if expected_page is not None:
        actual_pages = _hit_pages(hit)
        if not actual_pages:
            return False
        if not any(_page_matches(actual_page, expected_page, page_tolerance=page_tolerance) for actual_page in actual_pages):
            return False
    return True


def bbox_level_metrics(
    hits: list[dict[str, Any]],
    specs: list[dict[str, Any]],
    *,
    page_tolerance: int = 0,
    iou_threshold: float = BBOX_IOU_THRESHOLD,
) -> dict[str, float | None]:
    if not specs:
        return {"bbox_hit_rate": None, "bbox_precision": None, "bbox_recall": None, "bbox_max_iou": None}
    comparable_hits = 0
    matched_hit_keys: set[str] = set()
    covered_specs: set[int] = set()
    max_iou = 0.0
    for hit_index, hit in enumerate(hits, start=1):
        all_hit_boxes = _hit_bboxes_1000(hit)
        if not all_hit_boxes:
            hit["bbox_iou"] = None
            continue
        comparable_hits += 1
        best_iou = 0.0
        best_spec_index: int | None = None
        hit["bbox_item_count"] = len(all_hit_boxes)
        for spec_index, spec in enumerate(specs):
            spec_box = _normalize_bbox_1000(spec.get("bbox"))
            if spec_box is None or not _bbox_source_page_matches(hit, spec, page_tolerance=page_tolerance):
                continue
            hit_boxes = _hit_bboxes_1000(
                hit,
                page=_int_value(spec.get("page")),
                page_tolerance=page_tolerance,
            )
            iou = max((_bbox_iou(hit_box, spec_box) for hit_box in hit_boxes), default=0.0)
            if iou > best_iou:
                best_iou = iou
                best_spec_index = spec_index
        hit["bbox_iou"] = best_iou
        hit["bbox_match"] = best_iou > iou_threshold
        hit["bbox_iou_threshold"] = iou_threshold
        if best_spec_index is not None:
            hit["bbox_matched_spec_index"] = best_spec_index
        max_iou = max(max_iou, best_iou)
        if best_iou > iou_threshold and best_spec_index is not None:
            matched_hit_keys.add(_hit_chunk_key(hit, hit_index))
            covered_specs.add(best_spec_index)
    if comparable_hits <= 0:
        return {"bbox_hit_rate": None, "bbox_precision": None, "bbox_recall": None, "bbox_max_iou": None}
    return {
        "bbox_hit_rate": 1.0 if covered_specs else 0.0,
        "bbox_precision": float(len(matched_hit_keys)) / float(comparable_hits),
        "bbox_recall": float(len(covered_specs)) / float(len(specs)) if specs else None,
        "bbox_max_iou": max_iou,
    }


def region_relevance_grades_for_hits(
    hits: list[dict[str, Any]],
    specs: list[dict[str, Any]],
    *,
    page_tolerance: int = 0,
    iou_threshold: float = BBOX_IOU_THRESHOLD,
) -> tuple[list[float], list[float]]:
    grades: list[float] = []
    for hit_index, hit in enumerate(hits, start=1):
        hit_boxes = _hit_bboxes_1000(hit)
        hit["bbox_iou_threshold"] = iou_threshold
        if hit_boxes:
            hit["bbox_item_count"] = len(hit_boxes)

        source_page_specs = [
            spec
            for spec in specs
            if _bbox_source_page_matches(hit, spec, page_tolerance=page_tolerance)
        ]
        if not source_page_specs:
            hit["relevance_grade"] = 0.0
            hit["relevance_reason"] = "not_same_source_page"
            hit["region_relevance_label"] = "miss"
            hit["bbox_match"] = False
            if hit_boxes:
                hit["bbox_iou"] = 0.0
            grades.append(0.0)
            continue

        if not hit_boxes:
            hit["relevance_grade"] = 0.0
            hit["relevance_reason"] = "missing_bbox"
            hit["region_relevance_label"] = "missing_bbox"
            hit["bbox_match"] = False
            hit["bbox_iou"] = None
            grades.append(0.0)
            continue

        best_iou = 0.0
        best_spec_index: int | None = None
        for spec in source_page_specs:
            spec_index = specs.index(spec)
            spec_box = _normalize_bbox_1000(spec.get("bbox"))
            if spec_box is None:
                continue
            page_hit_boxes = _hit_bboxes_1000(
                hit,
                page=_int_value(spec.get("page")),
                page_tolerance=page_tolerance,
            )
            iou = max((_bbox_iou(hit_box, spec_box) for hit_box in page_hit_boxes), default=0.0)
            if iou > best_iou:
                best_iou = iou
                best_spec_index = spec_index

        hit["bbox_iou"] = best_iou
        if best_spec_index is not None:
            hit["bbox_matched_spec_index"] = best_spec_index
        if best_iou > iou_threshold:
            hit["relevance_grade"] = 1.0
            hit["relevance_reason"] = "bbox_iou_gt_threshold"
            hit["region_relevance_label"] = "match"
            hit["bbox_match"] = True
            grades.append(1.0)
        elif best_iou > 0.0:
            hit["relevance_grade"] = 0.0
            hit["relevance_reason"] = "bbox_iou_partial"
            hit["region_relevance_label"] = "partial"
            hit["bbox_match"] = False
            grades.append(0.0)
        else:
            hit["relevance_grade"] = 0.0
            hit["relevance_reason"] = "bbox_iou_zero"
            hit["region_relevance_label"] = "miss"
            hit["bbox_match"] = False
            grades.append(0.0)
    return grades, [1.0 for _spec in specs]


def global_region_relevant_chunk_keys_by_stage(
    hits_by_stage: dict[str, list[dict[str, Any]]],
    specs: list[dict[str, Any]],
    *,
    page_tolerance: int = 0,
) -> dict[str, set[str]]:
    stage_keys: dict[str, set[str]] = {}
    all_keys: set[str] = set()
    for stage, hits in hits_by_stage.items():
        copied_hits = [dict(hit) for hit in hits]
        grades, _ideal = region_relevance_grades_for_hits(
            copied_hits,
            specs,
            page_tolerance=page_tolerance,
        )
        keys = {
            _hit_chunk_key(hit, index)
            for index, (hit, grade) in enumerate(zip(copied_hits, grades), start=1)
            if float(grade or 0.0) > 0
        }
        stage_keys[stage] = keys
        all_keys.update(keys)
    stage_keys["__all__"] = all_keys
    return stage_keys


def relevance_grades_for_hits(
    hits: list[dict[str, Any]],
    specs: list[dict[str, Any]],
    *,
    page_tolerance: int = 0,
) -> tuple[list[float], list[float]]:
    grades: list[float] = []
    matched_specs: set[int] = set()
    exhaustive = all(_spec_is_exhaustive_chunk(spec) for spec in specs)
    for hit in hits:
        best_index: int | None = None
        best_grade = 0.0
        for index, spec in enumerate(specs):
            if exhaustive and index in matched_specs:
                continue
            matched, reason = _matches_spec(hit, spec, page_tolerance=page_tolerance)
            if matched:
                grade = optional_float(spec.get("grade")) or 1.0
                if grade > best_grade:
                    best_index = index
                    best_grade = grade
                    hit["relevance_reason"] = reason
        if best_index is None:
            grades.append(0.0)
            hit["relevance_grade"] = 0.0
            hit["relevance_reason"] = "not_match"
            continue
        if exhaustive:
            matched_specs.add(best_index)
        grades.append(best_grade)
        hit["relevance_grade"] = best_grade
    ideal = [optional_float(spec.get("grade")) or 1.0 for spec in specs]
    return grades, ideal


def partial_ranking_metrics(relevance: list[float], *, k: int) -> dict[str, float | None]:
    cutoff = max(1, k)
    top_relevance = [float(score) for score in relevance[:cutoff]]
    evaluated_count = max(1, len(top_relevance))
    relevant_flags = [1 if score > 0 else 0 for score in top_relevance]
    relevant_hits = sum(relevant_flags)
    first_rank = next((idx + 1 for idx, flag in enumerate(relevant_flags) if flag), None)
    return {
        "hit_rate": 1.0 if relevant_hits else 0.0,
        "mrr": (1.0 / float(first_rank)) if first_rank else 0.0,
        "precision_at_1": sum(1 for score in relevance[:1] if score > 0) / 1.0,
        "precision_at_3": sum(1 for score in relevance[:3] if score > 0) / 3.0,
        "precision": float(relevant_hits) / float(evaluated_count),
        "recall": None,
        "ap": None,
        "ndcg": None,
    }


def compute_stage_ranking_metrics(
    *,
    hits_by_stage: dict[str, list[dict[str, Any]]],
    specs: list[dict[str, Any]],
    region_specs: list[dict[str, Any]] | None = None,
    k: int,
    page_tolerance: int = 0,
) -> tuple[dict[str, dict[str, float | None]], dict[str, float | None], dict[str, list[dict[str, Any]]]]:
    stage_metrics: dict[str, dict[str, float | None]] = {}
    flat_metrics: dict[str, float | None] = {}
    annotated: dict[str, list[dict[str, Any]]] = {}
    if not specs:
        return stage_metrics, flat_metrics, annotated

    ideal: list[float] = []
    region_specs = region_specs or []
    use_region_relevance = bool(region_specs)
    exhaustive = all(_spec_is_exhaustive_chunk(spec) for spec in specs)
    if use_region_relevance:
        global_relevant_keys = global_region_relevant_chunk_keys_by_stage(
            hits_by_stage,
            region_specs,
            page_tolerance=page_tolerance,
        ).get("__all__", set())
    else:
        global_relevant_keys = global_relevant_chunk_keys_by_stage(
            hits_by_stage,
            specs,
            page_tolerance=page_tolerance,
        ).get("__all__", set())
    for stage, hits in hits_by_stage.items():
        copied_hits = [dict(hit) for hit in hits]
        can_judge = not copied_hits or any(
            _hit_has_required_fields(hit, spec)
            for hit in copied_hits
            for spec in specs
        )
        if not can_judge:
            for hit in copied_hits:
                hit["relevance_grade"] = None
                hit["relevance_reason"] = "missing_relevance_fields"
            metrics = {name: None for name, _label in RETRIEVAL_REPORT_METRICS}
            stage_metrics[stage] = metrics
            annotated[stage] = copied_hits
            for name, value in metrics.items():
                flat_metrics[f"{stage}_{name}"] = value
            continue
        if use_region_relevance:
            grades, ideal = region_relevance_grades_for_hits(
                copied_hits,
                region_specs,
                page_tolerance=page_tolerance,
            )
        else:
            grades, ideal = relevance_grades_for_hits(copied_hits, specs, page_tolerance=page_tolerance)
        if exhaustive and not use_region_relevance:
            metrics = ranking_metrics(
                grades,
                total_relevant=len(specs),
                ideal_relevance=ideal,
                k=k,
            )
        else:
            metrics = partial_ranking_metrics(grades, k=k)
            for hit in copied_hits:
                hit["relevance_scope"] = "bbox_region" if use_region_relevance else "partial_page_or_source"
        metrics["recall"] = candidate_chunk_recall_at_k(
            copied_hits,
            grades,
            k=k,
            denominator_keys=global_relevant_keys,
        )
        metrics.update(page_level_metrics(copied_hits, specs, page_tolerance=page_tolerance))
        metrics.update(bbox_level_metrics(copied_hits, region_specs, page_tolerance=page_tolerance))
        stage_metrics[stage] = metrics
        annotated[stage] = copied_hits
        prefix = f"{stage}_"
        for name, value in metrics.items():
            flat_metrics[f"{prefix}{name}"] = value
    return stage_metrics, flat_metrics, annotated


def expected_pages_from_specs(specs: list[dict[str, Any]]) -> list[int]:
    pages: list[int] = []
    for spec in specs:
        page = spec.get("page")
        if isinstance(page, int) and page not in pages:
            pages.append(page)
    return pages


def compact_hit_for_chunk_log(hit: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": hit.get("rank"),
        "node_id": hit.get("node_id"),
        "point_id": hit.get("point_id"),
        "doc_id": hit.get("doc_id"),
        "source": hit.get("source"),
        "title": hit.get("title"),
        "chunk_index": hit.get("chunk_index"),
        "page": hit.get("page"),
        "modality": hit.get("modality"),
        "score": hit.get("score"),
        "relevance_grade": hit.get("relevance_grade"),
        "bbox": hit.get("bbox"),
        "bbox_items": hit.get("bbox_items"),
        "bbox_iou": hit.get("bbox_iou"),
        "bbox_match": hit.get("bbox_match"),
    }


def build_chunk_id_records(records: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    cutoff = max(1, top_n)
    rows: list[dict[str, Any]] = []
    for record in records:
        stage_hits = record.get("ranked_hits_by_stage") or {}
        chunk_ids_by_stage: dict[str, list[dict[str, Any]]] = {}
        for stage in _record_stage_order(record):
            hits = stage_hits.get(stage) if isinstance(stage_hits, dict) else []
            if not isinstance(hits, list):
                hits = []
            chunk_ids_by_stage[stage] = [
                compact_hit_for_chunk_log(hit)
                for hit in hits[:cutoff]
                if isinstance(hit, dict)
            ]
        specs = record.get("relevance_specs") or []
        rows.append(
            {
                "id": record.get("id"),
                "question": record.get("question"),
                "reference_answer": record.get("reference_answer"),
                "expected_pages": expected_pages_from_specs(specs if isinstance(specs, list) else []),
                "case_metadata": record.get("case_metadata") or {},
                "chunk_ids_by_stage_top_n": chunk_ids_by_stage,
            }
        )
    return rows


def full_hit_for_query_record(hit: dict[str, Any]) -> dict[str, Any]:
    row = dict(hit)
    row.update(compact_hit_for_chunk_log(hit))
    if row.get("text") is None:
        row["text"] = ""
    row["text_chars"] = len(str(row["text"]))
    return row


def build_query_stage_records(
    records: list[dict[str, Any]],
    *,
    top_n: int | None = None,
) -> dict[str, Any]:
    limit = top_n if top_n and top_n > 0 else None
    rows: list[dict[str, Any]] = []
    for record in records:
        stage_hits = record.get("ranked_hits_by_stage") or {}
        if not isinstance(stage_hits, dict):
            stage_hits = {}
        specs = record.get("relevance_specs") or []
        query_record: dict[str, Any] = {
            "id": record.get("id"),
            "query": record.get("question"),
            "reference_answer": record.get("reference_answer"),
            "prediction": record.get("prediction"),
            "expected_pages": expected_pages_from_specs(specs if isinstance(specs, list) else []),
            "case_metadata": record.get("case_metadata") or {},
            "metrics": record.get("metrics") or {},
            "bbox_specs": record.get("bbox_specs") or [],
            "ai_evaluation": record.get("ai_evaluation") or {},
            "model_debug": record.get("model_debug") or {},
            "stages": {},
        }
        for stage in _record_stage_order(record):
            hits = stage_hits.get(stage) or []
            if not isinstance(hits, list):
                hits = []
            selected_hits = hits[:limit] if limit else hits
            query_record["stages"][stage] = {
                "label": QUERY_RECORD_STAGE_LABELS.get(stage, stage),
                "chunk_count": len(hits),
                "recorded_chunk_count": len(selected_hits),
                "chunks": [
                    full_hit_for_query_record(hit)
                    for hit in selected_hits
                    if isinstance(hit, dict)
                ],
            }
        rows.append(query_record)

    return {
        "schema_version": 1,
        "stage_order": list(_records_stage_order(records)),
        "stage_labels": QUERY_RECORD_STAGE_LABELS,
        "record_top_n": limit,
        "case_count": len(rows),
        "records": rows,
    }


def chunk_id_summary(chunk_records: list[dict[str, Any]]) -> dict[str, Any]:
    all_stages = _chunk_record_stage_order(chunk_records)
    unique_by_stage: dict[str, set[str]] = {stage: set() for stage in all_stages}
    relevant_unique_by_stage: dict[str, set[str]] = {stage: set() for stage in all_stages}
    for record in chunk_records:
        by_stage = record.get("chunk_ids_by_stage_top_n") or {}
        if not isinstance(by_stage, dict):
            continue
        for stage in all_stages:
            hits = by_stage.get(stage) or []
            if not isinstance(hits, list):
                continue
            for hit in hits:
                if not isinstance(hit, dict):
                    continue
                chunk_key = hit.get("node_id") or hit.get("point_id") or hit.get("chunk_index")
                if chunk_key is None:
                    continue
                unique_by_stage[stage].add(str(chunk_key))
                if hit.get("relevance_grade"):
                    relevant_unique_by_stage[stage].add(str(chunk_key))
    return {
        "top_n_unique_chunk_id_count_by_stage": {
            stage: len(values) for stage, values in unique_by_stage.items()
        },
        "top_n_relevant_unique_chunk_id_count_by_stage": {
            stage: len(values) for stage, values in relevant_unique_by_stage.items()
        },
    }


def _record_stage_order(record: dict[str, Any]) -> tuple[str, ...]:
    stage_hits = record.get("ranked_hits_by_stage") or {}
    if not isinstance(stage_hits, dict):
        return STAGE_NAMES
    available = [stage for stage in STAGE_NAMES if stage in stage_hits]
    extras = [str(stage) for stage in stage_hits if str(stage) not in available]
    return tuple(available + extras) or STAGE_NAMES


def _records_stage_order(records: list[dict[str, Any]]) -> tuple[str, ...]:
    seen: list[str] = []
    for record in records:
        for stage in _record_stage_order(record):
            if stage not in seen:
                seen.append(stage)
    ordered = [stage for stage in STAGE_NAMES if stage in seen]
    ordered.extend(stage for stage in seen if stage not in ordered)
    return tuple(ordered) or STAGE_NAMES


def _chunk_record_stage_order(records: list[dict[str, Any]]) -> tuple[str, ...]:
    seen: list[str] = []
    for record in records:
        by_stage = record.get("chunk_ids_by_stage_top_n") or {}
        if not isinstance(by_stage, dict):
            continue
        for stage in by_stage:
            if str(stage) not in seen:
                seen.append(str(stage))
    ordered = [stage for stage in STAGE_NAMES if stage in seen]
    ordered.extend(stage for stage in seen if stage not in ordered)
    return tuple(ordered) or STAGE_NAMES


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def settings_snapshot(graph: Any | None) -> dict[str, Any]:
    settings = getattr(graph, "settings", None)
    if settings is None:
        return {}
    snapshot: dict[str, Any] = {}
    for field in CONFIG_SNAPSHOT_FIELDS:
        if hasattr(settings, field):
            snapshot[field] = _json_safe(getattr(settings, field))
    return snapshot


def build_run_metadata(
    *,
    args: argparse.Namespace,
    dataset_path: Path,
    graph: Any | None,
    summary: dict[str, Any],
    chunk_summary: dict[str, Any],
    index_info: dict[str, Any],
) -> dict[str, Any]:
    return {
        "dataset": str(dataset_path),
        "args": _json_safe(vars(args)),
        "settings": settings_snapshot(graph),
        "summary": {
            "case_count": summary.get("case_count"),
            "error_count": summary.get("error_count"),
        },
        "chunk_summary": chunk_summary,
        "index_info": index_info,
    }


def load_index_info(args: argparse.Namespace) -> dict[str, Any]:
    info: dict[str, Any] = {}
    if args.index_summary:
        path = Path(args.index_summary)
        info["index_summary_path"] = str(path)
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            info["index_summary"] = data.get("index_summary") if isinstance(data.get("index_summary"), dict) else {}
            index_time = optional_float(data.get("index_build_time_ms"))
            if index_time is not None:
                info["index_build_time_ms"] = index_time
    if args.index_build_time_ms is not None:
        info["index_build_time_ms"] = float(args.index_build_time_ms)
    return info


def build_metrics_report(
    *,
    records: list[dict[str, Any]],
    summary: dict[str, Any],
    args: argparse.Namespace,
    index_info: dict[str, Any],
) -> dict[str, Any]:
    mean_metrics = summary.get("mean_metrics", {})
    retrieval: dict[str, Any] = {}
    stages = _parse_stage_list(getattr(args, "stage_metrics", None))
    for stage in stages:
        stage_report: dict[str, Any] = {
            "label": STAGE_LABELS.get(stage, stage),
            "cutoff_k": args.k,
        }
        for metric_name, metric_label in RETRIEVAL_REPORT_METRICS:
            stage_report[metric_name] = {
                "label": metric_label,
                "value": mean_metrics.get(f"{stage}_{metric_name}"),
            }
        retrieval[stage] = stage_report

    index_build_time = index_info.get("index_build_time_ms")
    if index_build_time is None:
        index_build_time = mean_metrics.get("index_build_time_ms")
    timing: dict[str, Any] = {
        "index_build_time_ms": index_build_time,
        "index_build_time_sec": (float(index_build_time) / 1000.0) if isinstance(index_build_time, (int, float)) else None,
        "avg_response_time_ms": mean_metrics.get("response_time_ms"),
        "avg_latency_ms": mean_metrics.get("latency_ms"),
    }
    for timing_name in TIMING_DEBUG_FIELDS:
        values = debug_timing_values(records, timing_name)
        if values:
            timing[f"avg_{timing_name}"] = mean_or_none(values)
            timing[f"sum_{timing_name}"] = sum(values)

    token_usage: dict[str, Any] = {}
    for token_name in TOKEN_REPORT_FIELDS:
        values = numeric_values(records, token_name)
        if values:
            token_usage[token_name] = {
                "average": mean_or_none(values),
                "total": sum(values),
            }

    ai_scores: dict[str, Any] = {}
    for field_name, field_label in AI_REPORT_FIELDS:
        values = numeric_values(records, field_name)
        if values:
            ai_scores[field_name] = {
                "label": field_label,
                "average": mean_or_none(values),
                "total": sum(values),
                "case_count": len(values),
            }

    return {
        "case_count": summary.get("case_count"),
        "error_count": summary.get("error_count"),
        "rank_cutoff_k": args.k,
        "page_tolerance": getattr(args, "page_tolerance", 0),
        "retrieval_metrics": retrieval,
        "ai_judge_scores": ai_scores,
        "timing": timing,
        "token_usage": token_usage,
        "index_info": index_info,
    }


def format_metric(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, (int, float)):
        return f"{float(value):.4f}"
    return str(value)


def build_metrics_report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 评测指标报告",
        "",
        f"- 样本数：{report.get('case_count')}",
        f"- 错误数：{report.get('error_count')}",
        f"- 排名截断 k：{report.get('rank_cutoff_k')}",
        "",
        "## 检索指标",
        "",
        "| 阶段 | " + " | ".join(label for _name, label in RETRIEVAL_REPORT_METRICS) + " |",
        "|---" + "|---:" * len(RETRIEVAL_REPORT_METRICS) + "|",
    ]
    retrieval = report.get("retrieval_metrics") or {}
    for stage in retrieval:
        row = retrieval.get(stage) or {}
        values = {
            metric: (row.get(metric) or {}).get("value")
            for metric, _ in RETRIEVAL_REPORT_METRICS
        }
        lines.append(
            "| "
            + " | ".join(
                [str(row.get("label") or stage)]
                + [format_metric(values.get(metric)) for metric, _label in RETRIEVAL_REPORT_METRICS]
            )
            + " |"
        )

    ai_scores = report.get("ai_judge_scores") or {}
    lines.extend(
        [
            "",
            "## AI 评分",
            "",
            "| 指标 | 平均 | 总分 | 样本数 |",
            "|---|---:|---:|---:|",
        ]
    )
    if isinstance(ai_scores, dict) and ai_scores:
        for field_name, field_label in AI_REPORT_FIELDS:
            row = ai_scores.get(field_name)
            if not isinstance(row, dict):
                continue
            lines.append(
                f"| {row.get('label') or field_label} | "
                f"{format_metric(row.get('average'))} | "
                f"{format_metric(row.get('total'))} | "
                f"{format_metric(row.get('case_count'))} |"
            )
    else:
        lines.append("| - | - | - | - |")

    timing = report.get("timing") or {}
    lines.extend(
        [
            "",
            "## 耗时指标",
            "",
            "| 指标 | 值 |",
            "|---|---:|",
            f"| 索引构建时间 ms | {format_metric(timing.get('index_build_time_ms'))} |",
            f"| 索引构建时间 sec | {format_metric(timing.get('index_build_time_sec'))} |",
            f"| 平均响应时间 ms | {format_metric(timing.get('avg_response_time_ms'))} |",
            f"| 平均总延迟 ms | {format_metric(timing.get('avg_latency_ms'))} |",
        ]
    )
    for timing_name in TIMING_DEBUG_FIELDS:
        if timing_name == "response_time_ms":
            continue
        avg_key = f"avg_{timing_name}"
        if avg_key in timing and timing.get(avg_key) is not None:
            lines.append(f"| 平均 {timing_name} | {format_metric(timing.get(avg_key))} |")

    token_usage = report.get("token_usage") or {}
    lines.extend(
        [
            "",
            "## Token 消耗",
            "",
            "| 指标 | 平均 | 总量 |",
            "|---|---:|---:|",
        ]
    )
    for token_name in TOKEN_REPORT_FIELDS:
        row = token_usage.get(token_name)
        if not isinstance(row, dict):
            continue
        lines.append(
            f"| {token_name} | {format_metric(row.get('average'))} | {format_metric(row.get('total'))} |"
        )
    lines.append("")
    return "\n".join(lines)


def evaluate_case(
    *,
    case: dict[str, Any],
    graph: Any | None,
    judge_client: Any | None,
    args: argparse.Namespace,
) -> dict[str, Any]:
    question = case["question"]
    reference_answer = (
        case.get("reference_answer")
        or case.get("expected_answer")
        or case.get("ground_truth")
    )
    prediction = ""
    citations: list[dict[str, Any]] = []
    retrieved_count: int | None = None
    latency_ms = optional_float(case.get("latency_ms"))
    response_time_ms = optional_float(case.get("response_time_ms") or case.get("response_time"))
    if response_time_ms is None:
        response_time_ms = latency_ms
    index_build_time_ms = optional_float(case.get("index_build_time_ms") or case.get("index_time_ms"))
    token_usage: dict[str, int | float] = token_usage_from_case(case)
    ranked_hits: list[dict[str, Any]] = []
    ranked_hits_by_stage: dict[str, list[dict[str, Any]]] = {}
    removed_hits_by_stage: dict[str, list[dict[str, Any]]] = {}
    visual_hits_by_stage: dict[str, list[dict[str, Any]]] = {}
    stage_metrics: dict[str, dict[str, float | None]] = {}
    ai_evaluation: dict[str, Any] = {}
    model_debug: dict[str, Any] = {}
    specs: list[dict[str, Any]] = []
    region_specs: list[dict[str, Any]] = []
    error: str | None = None

    try:
        if args.use_existing_answers:
            prediction = str(case.get("prediction") or case.get("answer") or "").strip()
            if not prediction:
                raise ValueError("existing-answer mode requires prediction or answer in the dataset")
            if isinstance(case.get("citations"), list):
                citations = [citation_to_dict(item) for item in case["citations"]]
            if isinstance(case.get("retrieved_count"), int):
                retrieved_count = case["retrieved_count"]
            ranked_hits = ranked_hits_from_case(case)
            raw_stage_hits = case.get("ranked_hits_by_stage") or case.get("stage_ranked_hits")
            if isinstance(raw_stage_hits, dict):
                for stage, rows in raw_stage_hits.items():
                    rows = raw_stage_hits.get(stage)
                    if isinstance(rows, list):
                        ranked_hits_by_stage[str(stage)] = [
                            {**item, "rank": item.get("rank", i)}
                            if isinstance(item, dict)
                            else {"source": str(item), "rank": i}
                            for i, item in enumerate(rows, start=1)
                        ]
            raw_removed_hits = case.get("removed_hits_by_stage")
            if isinstance(raw_removed_hits, dict):
                removed_hits_by_stage = {
                    str(stage): [item for item in rows if isinstance(item, dict)]
                    for stage, rows in raw_removed_hits.items()
                    if isinstance(rows, list)
                }
            raw_visual_hits = case.get("visual_hits_by_stage")
            if isinstance(raw_visual_hits, dict):
                visual_hits_by_stage = {
                    str(stage): [item for item in rows if isinstance(item, dict)]
                    for stage, rows in raw_visual_hits.items()
                    if isinstance(rows, list)
                }
            if ranked_hits and not ranked_hits_by_stage:
                ranked_hits_by_stage = {
                    "initial_recall": ranked_hits,
                    "rerank": ranked_hits,
                    "local_recheck": ranked_hits,
                    "final_output": ranked_hits,
                }
            if not token_usage:
                token_usage = {
                    "query_tokens_est": estimate_tokens(question),
                    "answer_tokens_est": estimate_tokens(prediction),
                    "total_tokens_est": estimate_tokens(question) + estimate_tokens(prediction),
                }
        else:
            if graph is None:
                raise RuntimeError("evaluation pipeline is not initialized")
            result = graph.run(question=question, filters=build_filters(args, case))
            prediction = result.answer
            citations = [citation_to_dict(item) for item in result.citations]
            retrieved_count = result.retrieved_count
            response_time_ms = result.timings_ms.get("response_time_ms")
            latency_ms = response_time_ms
            token_usage = result.token_usage
            ranked_by_source = {
                "retrieved": result.retrieved_hits,
                "reranked": result.reranked_hits,
                "context": result.context_hits,
                "final_output": result.context_hits,
                "initial_recall": result.initial_hits,
                "initial_retrieval": result.initial_hits,
                "initial_expanded": result.initial_expanded_hits,
                "local_recheck": result.local_recheck_hits,
                "rerank": result.reranked_hits,
                "agent_chunk_grading": result.agent_graded_hits,
                "evidence_gate": result.evidence_gate_hits,
                "final_after_retry": result.final_hits,
            }
            ranked_hits = ranked_by_source.get(args.rank_source, result.reranked_hits)
            ranked_hits_by_stage = dict(result.ranked_hits_by_stage or {})
            removed_hits_by_stage = dict(result.removed_hits_by_stage or {})
            visual_hits_by_stage = dict(result.visual_hits_by_stage or {})
            if not ranked_hits_by_stage:
                ranked_hits_by_stage = {
                    "initial_recall": result.initial_hits,
                    "initial_retrieval": result.initial_hits,
                    "initial_expanded": result.initial_expanded_hits,
                    "rerank": result.reranked_hits,
                    "agent_chunk_grading": result.agent_graded_hits,
                    "evidence_gate": result.evidence_gate_hits,
                    "local_recheck": result.local_recheck_hits,
                    "final_after_retry": result.final_hits,
                    "final_output": result.context_hits,
                }
            if not visual_hits_by_stage:
                visual_hits_by_stage = dict(ranked_hits_by_stage)
            model_debug = {
                **result.debug,
                "timings_ms": result.timings_ms,
                "rank_source": args.rank_source,
                "rank_cutoff": args.k,
                "pipeline": args.pipeline,
            }

        metrics = compute_metrics(
            prediction=prediction,
            reference_answer=str(reference_answer) if reference_answer else None,
            reference_keywords=_as_list(case.get("reference_keywords") or case.get("keywords")),
            expected_sources=_as_list(case.get("expected_sources") or case.get("expected_source")),
            citations=citations,
            retrieved_count=retrieved_count,
            latency_ms=latency_ms,
            response_time_ms=response_time_ms,
            index_build_time_ms=index_build_time_ms,
            token_usage=token_usage,
        )
        specs = relevance_specs(case)
        region_specs = bbox_specs(case)
        if specs and (ranked_hits_by_stage or ranked_hits):
            if not ranked_hits_by_stage:
                ranked_hits_by_stage = {args.rank_source: ranked_hits}
            stage_metrics, flat_stage_metrics, annotated_stage_hits = compute_stage_ranking_metrics(
                hits_by_stage=ranked_hits_by_stage,
                specs=specs,
                region_specs=region_specs,
                k=args.k,
                page_tolerance=args.page_tolerance,
            )
            metrics.update(flat_stage_metrics)
            preferred_stage = args.rank_source
            if preferred_stage == "retrieved":
                preferred_stage = "initial_expanded"
            elif preferred_stage == "reranked":
                preferred_stage = "rerank"
            elif preferred_stage == "context":
                preferred_stage = "final_output"
            ranked_hits = annotated_stage_hits.get(preferred_stage) or next(iter(annotated_stage_hits.values()))
            ranked_hits_by_stage = annotated_stage_hits
            model_debug.setdefault("rank_source", args.rank_source)
            model_debug.setdefault("rank_cutoff", args.k)
            model_debug["stage_hit_counts"] = {
                stage: len(hits) for stage, hits in ranked_hits_by_stage.items()
            }
            model_debug["relevance_spec_count"] = len(specs)
            model_debug["bbox_spec_count"] = len(region_specs)
            model_debug["expected_pages"] = expected_pages_from_specs(specs)
            model_debug["page_tolerance"] = args.page_tolerance

        if judge_client is not None and reference_answer:
            try:
                judge_metrics = judge_answer_with_client(
                    judge_client=judge_client,
                    question=question,
                    prediction=prediction,
                    reference_answer=str(reference_answer),
                )
                ai_evaluation = judge_metrics
                for key, value in judge_metrics.items():
                    if isinstance(value, bool):
                        metrics[key] = 1.0 if value else 0.0
                    elif isinstance(value, (int, float)):
                        metrics[key] = value
            except Exception as exc:
                ai_evaluation = {"ai_error": f"{type(exc).__name__}: {exc}"}
                model_debug["ai_judge_error"] = ai_evaluation["ai_error"]
    except Exception as exc:
        metrics = {}
        error = f"{type(exc).__name__}: {exc}"
        if args.fail_fast:
            raise

    return {
        "id": case["id"],
        "question": question,
        "reference_answer": reference_answer,
        "prediction": prediction,
        "citations": citations,
        "ranked_hits": ranked_hits,
        "ranked_hits_by_stage": ranked_hits_by_stage,
        "removed_hits_by_stage": removed_hits_by_stage,
        "visual_hits_by_stage": visual_hits_by_stage,
        "metrics": metrics,
        "stage_metrics": stage_metrics,
        "relevance_specs": specs,
        "bbox_specs": region_specs,
        "case_metadata": case.get("sciqa_meta") or case.get("metadata") or {},
        "ai_evaluation": ai_evaluation,
        "model_debug": model_debug,
        "error": error,
    }


def judge_answer_with_client(
    *,
    judge_client: Any,
    question: str,
    prediction: str,
    reference_answer: str,
) -> dict[str, Any]:
    from check.judge import judge_answer

    return judge_answer(
        llm_client=judge_client,
        question=question,
        prediction=prediction,
        reference_answer=reference_answer,
    )


def build_graph_if_needed(args: argparse.Namespace) -> Any | None:
    if args.use_existing_answers:
        return None
    if args.pipeline == "taskgraph":
        from check.pipeline import TaskGraphEvaluationPipeline

        return TaskGraphEvaluationPipeline()
    from check.pipeline import EvaluationPipeline

    return EvaluationPipeline()


def build_judge_if_needed(args: argparse.Namespace) -> Any | None:
    if not args.ai_judge:
        return None
    from agentic_rag.config import get_settings
    from agentic_rag.models.providers import build_llm_client

    return build_llm_client(get_settings())


def write_outputs(
    *,
    run_dir: Path,
    records: list[dict[str, Any]],
    summary: dict[str, Any],
    args: argparse.Namespace,
    dataset_path: Path,
    graph: Any | None,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    index_info = load_index_info(args)
    results_path = run_dir / "results.jsonl"
    with results_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    chunk_records = build_chunk_id_records(records, top_n=args.chunk_log_top_n)
    chunk_summary = chunk_id_summary(chunk_records)
    chunk_ids_path = run_dir / "chunk_ids_by_case.jsonl"
    with chunk_ids_path.open("w", encoding="utf-8") as handle:
        for record in chunk_records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    query_record_dir = Path(args.query_record_dir)
    if not query_record_dir.is_absolute():
        query_record_dir = REPO_ROOT / query_record_dir
    query_record_dir.mkdir(parents=True, exist_ok=True)
    query_stage_records = build_query_stage_records(
        records,
        top_n=args.query_record_top_n,
    )
    query_stage_records.update(
        {
            "run_name": run_dir.name,
            "dataset": str(dataset_path),
            "output_run_dir": str(run_dir),
        }
    )
    query_stage_records_path = query_record_dir / f"{run_dir.name}_query_stage_chunks.json"
    query_stage_records_path.write_text(
        json.dumps(query_stage_records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    metadata = build_run_metadata(
        args=args,
        dataset_path=dataset_path,
        graph=graph,
        summary=summary,
        chunk_summary=chunk_summary,
        index_info=index_info,
    )
    (run_dir / "run_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    metrics_report = build_metrics_report(
        records=records,
        summary=summary,
        args=args,
        index_info=index_info,
    )
    (run_dir / "metrics_report.json").write_text(
        json.dumps(metrics_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "metrics_report.md").write_text(
        build_metrics_report_markdown(metrics_report),
        encoding="utf-8",
    )
    summary["artifacts"] = {
        "results": str(results_path),
        "summary": str(run_dir / "summary.json"),
        "chunk_ids_by_case": str(chunk_ids_path),
        "query_stage_chunks": str(query_stage_records_path),
        "run_metadata": str(run_dir / "run_metadata.json"),
        "metrics_report_json": str(run_dir / "metrics_report.json"),
        "metrics_report_md": str(run_dir / "metrics_report.md"),
    }
    summary["chunk_summary"] = chunk_summary
    summary["required_metrics"] = metrics_report
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def print_summary(run_dir: Path, summary: dict[str, Any]) -> None:
    print(f"Run directory: {run_dir}")
    print(f"Cases: {summary['case_count']}  Errors: {summary['error_count']}")
    mean_metrics = summary.get("mean_metrics", {})
    for name in sorted(mean_metrics):
        print(f"{name}: {mean_metrics[name]:.4f}")
    required = summary.get("required_metrics") or {}
    retrieval = required.get("retrieval_metrics") if isinstance(required, dict) else {}
    if isinstance(retrieval, dict) and retrieval:
        print("Required retrieval metrics:")
        for stage in retrieval:
            row = retrieval.get(stage) or {}
            values = {
                metric: (row.get(metric) or {}).get("value")
                for metric, _ in RETRIEVAL_REPORT_METRICS
            }
            print(
                f"{stage}: "
                + ", ".join(
                    f"{metric}={format_metric(values.get(metric))}"
                    for metric, _label in RETRIEVAL_REPORT_METRICS
                )
            )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local RAG/model evaluation cases.")
    parser.add_argument("--dataset", required=True, help="Path to .jsonl or .json evaluation dataset")
    parser.add_argument("--output-dir", default="check/runs", help="Directory for evaluation outputs")
    parser.add_argument("--run-name", default=None, help="Optional run directory name")
    parser.add_argument("--limit", type=int, default=None, help="Evaluate only the first N cases")
    parser.add_argument(
        "--use-existing-answers",
        action="store_true",
        help="Evaluate prediction/answer fields from the dataset without calling the RAG pipeline",
    )
    parser.add_argument(
        "--judge",
        "--ai-judge",
        dest="ai_judge",
        action="store_true",
        help="Use configured LLM as an AI evaluator for query/reference/prediction",
    )
    parser.add_argument("--k", type=int, default=10, help="Cutoff for Hit Rate/MRR/Precision/Recall/AP/nDCG")
    parser.add_argument(
        "--rank-source",
        choices=[
            "retrieved",
            "reranked",
            "context",
            "initial_recall",
            "initial_retrieval",
            "initial_expanded",
            "rerank",
            "agent_chunk_grading",
            "evidence_gate",
            "local_recheck",
            "final_after_retry",
            "final_output",
        ],
        default="reranked",
        help="Ranked list used for retrieval metrics",
    )
    parser.add_argument(
        "--pipeline",
        choices=["simple", "taskgraph"],
        default="simple",
        help="Evaluation pipeline: simple uses check pipeline, taskgraph runs the full TaskGraphRAG path",
    )
    parser.add_argument(
        "--stage-metrics",
        default=None,
        help="Comma-separated stages included in metrics report. Defaults to all recorded TaskGraph-compatible stages.",
    )
    parser.add_argument(
        "--use-expected-source-filter",
        action="store_true",
        help="Use the first expected source as a metadata source filter for evaluation-only single-document runs.",
    )
    parser.add_argument(
        "--page-tolerance",
        type=int,
        default=0,
        help="Treat expected page +/- N as relevant for retrieval metrics.",
    )
    parser.add_argument("--source", default=None, help="Global metadata source filter")
    parser.add_argument("--tag", action="append", default=None, help="Global metadata tag filter, repeatable")
    parser.add_argument("--chunk-log-top-n", type=int, default=10, help="Top N chunk ids to save per stage/case")
    parser.add_argument("--query-record-dir", default="check/query_records", help="Dedicated directory for query stage chunk JSON records")
    parser.add_argument("--query-record-top-n", type=int, default=0, help="Top N chunks to save per stage in query records; 0 saves all")
    parser.add_argument("--index-summary", default=None, help="Path to check.benchmark_index index_summary.json")
    parser.add_argument("--index-build-time-ms", type=float, default=None, help="Index build time to include in reports")
    parser.add_argument("--fail-fast", action="store_true", help="Stop on the first failed case")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dataset_path = Path(args.dataset)
    output_root = Path(args.output_dir)
    if not output_root.is_absolute():
        output_root = REPO_ROOT / output_root
    run_name = args.run_name or datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = output_root / run_name

    cases = load_cases(dataset_path, limit=args.limit)
    graph = build_graph_if_needed(args)
    judge_client = build_judge_if_needed(args)

    records = [
        evaluate_case(case=case, graph=graph, judge_client=judge_client, args=args)
        for case in cases
    ]
    summary = summarize_records(records)
    write_outputs(
        run_dir=run_dir,
        records=records,
        summary=summary,
        args=args,
        dataset_path=dataset_path,
        graph=graph,
    )
    print_summary(run_dir, summary)
    return 1 if summary["error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
