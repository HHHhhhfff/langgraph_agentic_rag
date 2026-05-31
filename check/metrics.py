from __future__ import annotations

import re
import statistics
import unicodedata
from collections import Counter
from math import log2
from typing import Any


TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9_]+(?:[-_][a-zA-Z0-9_]+)*")


def normalize_text(text: Any) -> str:
    value = "" if text is None else str(text)
    value = unicodedata.normalize("NFKC", value).lower()
    value = re.sub(r"\s+", "", value)
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value)


def mixed_tokens(text: Any) -> list[str]:
    value = "" if text is None else str(text)
    value = unicodedata.normalize("NFKC", value).lower()
    return TOKEN_RE.findall(value)


def safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def token_overlap_scores(prediction: str, reference: str) -> dict[str, float]:
    pred_tokens = mixed_tokens(prediction)
    ref_tokens = mixed_tokens(reference)
    if not pred_tokens and not ref_tokens:
        return {"token_precision": 1.0, "token_recall": 1.0, "token_f1": 1.0}
    if not pred_tokens or not ref_tokens:
        return {"token_precision": 0.0, "token_recall": 0.0, "token_f1": 0.0}

    pred_counts = Counter(pred_tokens)
    ref_counts = Counter(ref_tokens)
    overlap = sum((pred_counts & ref_counts).values())
    precision = safe_div(overlap, len(pred_tokens))
    recall = safe_div(overlap, len(ref_tokens))
    f1 = safe_div(2 * precision * recall, precision + recall)
    return {
        "token_precision": precision,
        "token_recall": recall,
        "token_f1": f1,
    }


def keyword_recall(prediction: str, keywords: list[str]) -> float | None:
    cleaned = [kw for kw in keywords if str(kw).strip()]
    if not cleaned:
        return None
    answer_norm = normalize_text(prediction)
    hits = 0
    for keyword in cleaned:
        if normalize_text(keyword) in answer_norm:
            hits += 1
    return safe_div(hits, len(cleaned))


def citation_source_recall(citations: list[dict[str, Any]], expected_sources: list[str]) -> float | None:
    expected = [src for src in expected_sources if str(src).strip()]
    if not expected:
        return None

    citation_fields: list[str] = []
    for citation in citations:
        citation_fields.append(str(citation.get("source", "")))
        citation_fields.append(str(citation.get("title", "")))
    normalized_citations = [normalize_text(field) for field in citation_fields if field]

    hits = 0
    for source in expected:
        expected_norm = normalize_text(source)
        if any(expected_norm in cit or cit in expected_norm for cit in normalized_citations if cit):
            hits += 1
    return safe_div(hits, len(expected))


def dcg(relevance: list[float]) -> float:
    return sum((2**score - 1) / log2(rank + 2) for rank, score in enumerate(relevance))


def precision_at(relevance: list[float], rank_cutoff: int) -> float:
    cutoff = max(1, rank_cutoff)
    top_relevance = [float(score) for score in relevance[:cutoff]]
    relevant_hits = sum(1 for score in top_relevance if score > 0)
    return safe_div(float(relevant_hits), float(cutoff))


def ranking_metrics(
    relevance: list[float],
    *,
    total_relevant: int,
    ideal_relevance: list[float] | None = None,
    k: int = 10,
) -> dict[str, float]:
    cutoff = max(1, k)
    top_relevance = [float(score) for score in relevance[:cutoff]]
    evaluated_count = max(1, len(top_relevance))
    relevant_flags = [1 if score > 0 else 0 for score in top_relevance]
    relevant_hits = sum(relevant_flags)

    first_rank = next((idx + 1 for idx, flag in enumerate(relevant_flags) if flag), None)
    precision_values: list[float] = []
    running_hits = 0
    for idx, flag in enumerate(relevant_flags, start=1):
        if flag:
            running_hits += 1
            precision_values.append(running_hits / idx)

    denominator = min(max(total_relevant, 1), cutoff)
    ap = safe_div(sum(precision_values), denominator)

    if ideal_relevance:
        ideal = sorted([float(score) for score in ideal_relevance if score > 0], reverse=True)[:cutoff]
    else:
        ideal = [1.0] * min(total_relevant, cutoff)
    ndcg = safe_div(dcg(top_relevance), dcg(ideal))

    return {
        "hit_rate": 1.0 if relevant_hits else 0.0,
        "mrr": safe_div(1.0, float(first_rank)) if first_rank else 0.0,
        "precision_at_1": precision_at(relevance, 1),
        "precision_at_3": precision_at(relevance, 3),
        "precision": safe_div(float(relevant_hits), float(evaluated_count)),
        "recall": safe_div(float(relevant_hits), float(total_relevant)),
        "ap": ap,
        "ndcg": ndcg,
    }


def estimate_tokens(text: Any, model: str | None = None) -> int:
    value = "" if text is None else str(text)
    if not value:
        return 0
    try:
        import tiktoken

        try:
            encoding = tiktoken.encoding_for_model(model or "")
        except Exception:
            encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(value))
    except Exception:
        return max(1, len(mixed_tokens(value)))


def compute_metrics(
    *,
    prediction: str,
    reference_answer: str | None,
    reference_keywords: list[str],
    expected_sources: list[str],
    citations: list[dict[str, Any]],
    retrieved_count: int | None,
    latency_ms: float | None,
    response_time_ms: float | None = None,
    index_build_time_ms: float | None = None,
    token_usage: dict[str, int | float] | None = None,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "answer_chars": len(prediction or ""),
        "citation_count": len(citations),
        "has_citation": 1.0 if citations else 0.0,
    }
    if retrieved_count is not None:
        metrics["retrieved_count"] = retrieved_count
    if latency_ms is not None:
        metrics["latency_ms"] = latency_ms
    if response_time_ms is not None:
        metrics["response_time_ms"] = response_time_ms
    if index_build_time_ms is not None:
        metrics["index_build_time_ms"] = index_build_time_ms
    if token_usage:
        metrics.update(token_usage)

    if reference_answer:
        metrics["exact_match"] = 1.0 if normalize_text(prediction) == normalize_text(reference_answer) else 0.0
        metrics.update(token_overlap_scores(prediction, reference_answer))

    kw_recall = keyword_recall(prediction, reference_keywords)
    if kw_recall is not None:
        metrics["keyword_recall"] = kw_recall

    source_recall = citation_source_recall(citations, expected_sources)
    if source_recall is not None:
        metrics["expected_source_recall"] = source_recall

    return metrics


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    metric_values: dict[str, list[float]] = {}
    for record in records:
        for name, value in record.get("metrics", {}).items():
            if isinstance(value, bool):
                metric_values.setdefault(name, []).append(1.0 if value else 0.0)
            elif isinstance(value, (int, float)):
                metric_values.setdefault(name, []).append(float(value))

    mean_metrics = {
        name: statistics.fmean(values)
        for name, values in sorted(metric_values.items())
        if values
    }
    return {
        "case_count": len(records),
        "error_count": sum(1 for record in records if record.get("error")),
        "mean_metrics": mean_metrics,
    }
