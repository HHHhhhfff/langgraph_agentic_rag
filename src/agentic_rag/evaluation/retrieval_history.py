from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentic_rag.config import Settings
from agentic_rag.schemas import SearchHit


SCHEMA_VERSION = "retrieval_eval.v1"


def new_query_id() -> str:
    return str(uuid4())


class RetrievalHistoryRecorder:
    """Append retrieval snapshots as JSONL records for later offline evaluation."""

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def path(self) -> Path:
        return Path(self.settings.retrieval_eval_log_dir) / self.settings.retrieval_eval_log_file

    def append(self, record: dict[str, Any]) -> None:
        if not self.settings.retrieval_eval_log_enabled:
            return
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def build_snapshot(
    *,
    stage: str,
    query_text: str,
    hits: list[SearchHit],
    max_text_chars: int,
    used_rerank: bool = False,
    rerank_fallback_reason: str | None = None,
    removed_hits: list[SearchHit] | None = None,
) -> dict[str, Any]:
    removed = removed_hits or []
    return {
        "stage": stage,
        "query_text": query_text,
        "top_k": len(hits),
        "used_rerank": used_rerank,
        "rerank_fallback_reason": rerank_fallback_reason,
        "hits": [serialize_hit(hit, rank=i, max_text_chars=max_text_chars) for i, hit in enumerate(hits, start=1)],
        "removed_hits": [
            serialize_hit(hit, rank=i, max_text_chars=max_text_chars)
            for i, hit in enumerate(removed, start=1)
        ],
        "removed_summary": _removed_summary(removed),
    }


def build_query_record(
    *,
    query_id: str,
    question: str,
    state: dict[str, Any],
    snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "query_id": query_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "query": {
            "text": question,
            "rewritten_text": state.get("rewritten_query_text"),
            "filters": state.get("filters") or {},
            "route": state.get("route"),
            "executed_channels": state.get("executed_channels", []),
        },
        "taskgraph": {
            "retry_count": state.get("retry_count", 0),
            "retry_actions": state.get("retry_actions", []),
            "retry_history": state.get("retry_history", []),
            "gate_decision": state.get("gate_decision"),
            "evidence_ok": state.get("evidence_ok", False),
            "support_level": state.get("support_level"),
            "support_score": state.get("support_score", 0.0),
            "citation_ok": state.get("citation_ok", False),
            "used_rerank": state.get("used_rerank", False),
            "rerank_fallback_reason": state.get("rerank_fallback_reason"),
            "evidence_gate_enabled": state.get("evidence_gate_enabled", True),
            "local_retry_enabled": state.get("local_retry_enabled", True),
            "evidence_gate_skipped": state.get("evidence_gate_skipped", False),
            "local_retry_skipped": state.get("local_retry_skipped", False),
        },
        "snapshots": snapshots,
        "labels": {
            "relevant_node_ids": [],
            "relevant_doc_ids": [],
            "graded_relevance": {},
        },
    }


def serialize_hit(hit: SearchHit, *, rank: int, max_text_chars: int) -> dict[str, Any]:
    metadata = dict(hit.metadata or {})
    chunk_index = metadata.get("chunk_index")
    source_parser = hit.source_parser or metadata.get("source_parser") or metadata.get("parser_name")
    return {
        "rank": rank,
        "node_id": hit.node_id,
        "point_id": hit.point_id,
        "doc_id": hit.doc_id,
        "chunk_id": metadata.get("chunk_id") or hit.node_id or hit.point_id,
        "chunk_index": chunk_index,
        "text": _clip(hit.text or hit.table_markdown or hit.formula_latex or "", max_text_chars),
        "metadata": {
            **metadata,
            "source": metadata.get("source"),
            "title": metadata.get("title"),
            "page": hit.page if hit.page is not None else metadata.get("page"),
            "modality": hit.modality or metadata.get("modality"),
            "section_path": hit.section_path or metadata.get("section_path", []),
            "parser_name": source_parser,
        },
        "relationships": dict(hit.relationships or {}),
        "scores": {
            "score": hit.score,
            "score_vector": hit.score_vector,
            "score_bm25": hit.score_bm25,
            "score_rrf": hit.score_rrf,
            "rerank_score": metadata.get("rerank_score"),
            "score_composite": metadata.get("score_composite"),
            "score_stage": metadata.get("score_stage"),
            "score_policy": metadata.get("score_policy"),
            "score_components": metadata.get("score_components"),
            "score_weights": metadata.get("score_weights"),
            "score_final_preserved": metadata.get("score_final_preserved"),
            "score_threshold": metadata.get("score_threshold"),
            "score_threshold_passed": metadata.get("score_threshold_passed"),
        },
        "retrieval": {
            "channel": hit.channel,
            "vector_name": metadata.get("vector_name"),
            "modality": hit.modality,
            "image_semantic_type": hit.image_semantic_type or metadata.get("image_semantic_type"),
            "source_parser": source_parser,
        },
        "eval": {
            "is_relevant": None,
            "relevance_label": None,
            "graded_relevance": None,
        },
    }


def _clip(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    return text[:max_chars]


def _removed_summary(hits: list[SearchHit]) -> dict[str, Any]:
    by_reason: dict[str, int] = {}
    for hit in hits:
        reason = str((hit.metadata or {}).get("removed_reason") or "unknown_removed")
        by_reason[reason] = by_reason.get(reason, 0) + 1
    return {"total": len(hits), "by_reason": by_reason}
