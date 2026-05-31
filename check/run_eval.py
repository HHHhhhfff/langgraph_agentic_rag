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
    ("precision", "Precision@k（精确率）"),
    ("recall", "Recall（召回率）"),
    ("ap", "AP（Average Precision）"),
    ("ndcg", "nDCG（Normalized Discounted Cumulative Gain）"),
)
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
    relevant_flags = [1 if score > 0 else 0 for score in top_relevance]
    relevant_hits = sum(relevant_flags)
    first_rank = next((idx + 1 for idx, flag in enumerate(relevant_flags) if flag), None)
    return {
        "hit_rate": 1.0 if relevant_hits else 0.0,
        "mrr": (1.0 / float(first_rank)) if first_rank else 0.0,
        "precision_at_1": sum(1 for score in relevance[:1] if score > 0) / 1.0,
        "precision_at_3": sum(1 for score in relevance[:3] if score > 0) / 3.0,
        "precision": float(relevant_hits) / float(cutoff),
        "recall": None,
        "ap": None,
        "ndcg": None,
    }


def compute_stage_ranking_metrics(
    *,
    hits_by_stage: dict[str, list[dict[str, Any]]],
    specs: list[dict[str, Any]],
    k: int,
    page_tolerance: int = 0,
) -> tuple[dict[str, dict[str, float | None]], dict[str, float | None], dict[str, list[dict[str, Any]]]]:
    stage_metrics: dict[str, dict[str, float | None]] = {}
    flat_metrics: dict[str, float | None] = {}
    annotated: dict[str, list[dict[str, Any]]] = {}
    if not specs:
        return stage_metrics, flat_metrics, annotated

    ideal: list[float] = []
    exhaustive = all(_spec_is_exhaustive_chunk(spec) for spec in specs)
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
        grades, ideal = relevance_grades_for_hits(copied_hits, specs, page_tolerance=page_tolerance)
        if exhaustive:
            metrics = ranking_metrics(
                grades,
                total_relevant=len(specs),
                ideal_relevance=ideal,
                k=k,
            )
        else:
            metrics = partial_ranking_metrics(grades, k=k)
            for hit in copied_hits:
                hit["relevance_scope"] = "partial_page_or_source"
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
        "| 阶段 | Hit Rate | MRR | Precision@1 | Precision@3 | Precision@k | Recall | AP | nDCG |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
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
                [
                    str(row.get("label") or stage),
                    format_metric(values.get("hit_rate")),
                    format_metric(values.get("mrr")),
                    format_metric(values.get("precision_at_1")),
                    format_metric(values.get("precision_at_3")),
                    format_metric(values.get("precision")),
                    format_metric(values.get("recall")),
                    format_metric(values.get("ap")),
                    format_metric(values.get("ndcg")),
                ]
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
        if specs and (ranked_hits_by_stage or ranked_hits):
            if not ranked_hits_by_stage:
                ranked_hits_by_stage = {args.rank_source: ranked_hits}
            stage_metrics, flat_stage_metrics, annotated_stage_hits = compute_stage_ranking_metrics(
                hits_by_stage=ranked_hits_by_stage,
                specs=specs,
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
                f"hit_rate={format_metric(values.get('hit_rate'))}, "
                f"mrr={format_metric(values.get('mrr'))}, "
                f"precision@1={format_metric(values.get('precision_at_1'))}, "
                f"precision@3={format_metric(values.get('precision_at_3'))}, "
                f"precision@k={format_metric(values.get('precision'))}, "
                f"recall={format_metric(values.get('recall'))}, "
                f"ap={format_metric(values.get('ap'))}, "
                f"ndcg={format_metric(values.get('ndcg'))}"
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
