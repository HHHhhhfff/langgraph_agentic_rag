from __future__ import annotations

import html
import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_rag.config import Settings


VECTOR_PAYLOAD_KEYS = {"vector", "embedding", "dense_vector", "sparse_vector", "query_vector"}
STAGES = ["initial_retrieval", "rerank", "final_after_retry"]


class RetrievalVisualizationError(RuntimeError):
    """Raised when retrieval visualization cannot be generated."""


def build_run_id(now: datetime | None = None) -> str:
    ts = (now or datetime.now(timezone.utc)).astimezone().strftime("%Y%m%d_%H%M%S")
    return f"{ts}_{uuid.uuid4().hex[:8]}"


def load_history_records(path: str | Path) -> list[dict[str, Any]]:
    history_path = Path(path)
    if not history_path.exists():
        raise RetrievalVisualizationError(f"retrieval history does not exist: {history_path}")
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(history_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RetrievalVisualizationError(f"invalid JSONL at line {line_no}: {exc}") from exc
        if isinstance(value, dict):
            records.append(value)
    if not records:
        raise RetrievalVisualizationError(f"retrieval history is empty: {history_path}")
    return records


def select_history_record(
    records: list[dict[str, Any]],
    *,
    latest: bool = False,
    query_id: str | None = None,
) -> dict[str, Any]:
    if query_id:
        for record in reversed(records):
            if str(record.get("query_id") or "") == query_id:
                return record
        raise RetrievalVisualizationError(f"query_id not found: {query_id}")
    if latest or not query_id:
        return records[-1]
    raise RetrievalVisualizationError("either latest or query_id must be specified")


def write_retrieval_visualization_report(
    record: dict[str, Any],
    *,
    settings: Settings,
    output_dir: str | Path | None = None,
    source_mode: str = "history_latest",
    history_path: str | Path | None = None,
    run_id: str | None = None,
    max_text_chars: int | None = None,
    include_full_vectors: bool | None = None,
) -> Path:
    max_chars = max_text_chars or settings.retrieval_vis_max_text_chars
    include_vectors = settings.retrieval_vis_include_full_vectors if include_full_vectors is None else include_full_vectors
    root = Path(output_dir or settings.retrieval_vis_output_dir)
    actual_run_id = run_id or build_run_id()
    run_dir = root / "runs" / actual_run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    sanitized = sanitize_record(record, include_full_vectors=include_vectors, max_text_chars=max_chars)
    query = sanitized.get("query") if isinstance(sanitized.get("query"), dict) else {}
    snapshots = sanitized.get("snapshots") if isinstance(sanitized.get("snapshots"), list) else []
    stage_compare = build_stage_compare(sanitized)
    rank_flow = build_rank_flow(stage_compare)
    hit_counts = {str(s.get("stage")): len(s.get("hits") or []) for s in snapshots if isinstance(s, dict)}
    stages = [stage for stage in STAGES if stage in hit_counts] or [str(s.get("stage")) for s in snapshots if isinstance(s, dict)]

    manifest = {
        "run_id": actual_run_id,
        "mode": "retrieval_visualization",
        "source_mode": source_mode,
        "history_path": str(history_path) if history_path is not None else None,
        "query_id": sanitized.get("query_id"),
        "query_text": query.get("text") or _first_snapshot_query(sanitized),
        "stages": stages,
        "hit_counts": hit_counts,
        "include_full_vectors": include_vectors,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "warnings": [],
        "artifacts": {
            "query": "query.json",
            "snapshots": "snapshots.json",
            "chunks_html": "chunks.html",
            "stage_compare_html": "stage_compare.html",
            "rank_flow_html": "rank_flow.html",
            "citations_html": "citations.html",
            "raw_record": "raw_record.json",
        },
    }

    _write_json(run_dir / "manifest.json", manifest)
    _write_json(run_dir / "query.json", query)
    _write_json(run_dir / "snapshots.json", snapshots)
    _write_json(run_dir / "raw_record.json", sanitized)
    _write_json(run_dir / "stage_compare.json", stage_compare)
    render_chunks_html(run_dir / "chunks.html", sanitized)
    render_stage_compare_html(run_dir / "stage_compare.html", stage_compare)
    render_rank_flow_html(run_dir / "rank_flow.html", rank_flow)
    render_citations_html(run_dir / "citations.html", sanitized)
    return run_dir


def sanitize_record(
    record: dict[str, Any],
    *,
    include_full_vectors: bool = False,
    max_text_chars: int = 4000,
) -> dict[str, Any]:
    sanitized = _sanitize_value(deepcopy(record), include_full_vectors=include_full_vectors)
    if not isinstance(sanitized, dict):
        return {}
    for snapshot in sanitized.get("snapshots") or []:
        if not isinstance(snapshot, dict):
            continue
        for hit in snapshot.get("hits") or []:
            if isinstance(hit, dict) and isinstance(hit.get("text"), str):
                hit["text"] = _truncate(hit["text"], max_text_chars)
    return sanitized


def build_stage_compare(record: dict[str, Any]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    snapshots = record.get("snapshots") or []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        stage = str(snapshot.get("stage") or "")
        for hit in snapshot.get("hits") or []:
            if not isinstance(hit, dict):
                continue
            key = _hit_key(hit)
            if not key:
                continue
            row = by_key.setdefault(
                key,
                {
                    "key": key,
                    "node_id": hit.get("node_id"),
                    "point_id": hit.get("point_id"),
                    "source": (hit.get("metadata") or {}).get("source"),
                    "title": (hit.get("metadata") or {}).get("title"),
                    "chunk_index": hit.get("chunk_index") or (hit.get("metadata") or {}).get("chunk_index"),
                    "page": (hit.get("metadata") or {}).get("page"),
                    "modality": (hit.get("retrieval") or {}).get("modality") or (hit.get("metadata") or {}).get("modality"),
                    "channel": (hit.get("retrieval") or {}).get("channel"),
                    "score": (hit.get("scores") or {}).get("score"),
                },
            )
            row[f"{stage}_rank"] = hit.get("rank")
            row[f"{stage}_score"] = (hit.get("scores") or {}).get("score")
    rows = list(by_key.values())
    for row in rows:
        row["status"] = _rank_status(row)
        row["rank_flow"] = _rank_flow_text(row)
    rows.sort(key=_compare_sort_key)
    return rows


def build_rank_flow(stage_compare: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "key": row.get("key"),
            "node_id": row.get("node_id"),
            "flow": row.get("rank_flow"),
            "status": row.get("status"),
            "source": row.get("source"),
            "chunk_index": row.get("chunk_index"),
        }
        for row in stage_compare
    ]


def render_chunks_html(path: Path, record: dict[str, Any]) -> None:
    query = record.get("query") if isinstance(record.get("query"), dict) else {}
    sections = [
        f"""
        <section class="summary">
          <h2>Query</h2>
          <div class="grid">
            <div><strong>query_id</strong><span>{_e(record.get("query_id"))}</span></div>
            <div><strong>query_text</strong><span>{_e(query.get("text") or _first_snapshot_query(record))}</span></div>
            <div><strong>rewritten_text</strong><span>{_e(query.get("rewritten_text"))}</span></div>
            <div><strong>executed_channels</strong><span>{_e(query.get("executed_channels"))}</span></div>
          </div>
        </section>
        """
    ]
    for snapshot in record.get("snapshots") or []:
        if not isinstance(snapshot, dict):
            continue
        stage = str(snapshot.get("stage") or "unknown")
        hits = [hit for hit in (snapshot.get("hits") or []) if isinstance(hit, dict)]
        cards = "\n".join(_render_hit_card(stage, snapshot, hit) for hit in hits)
        sections.append(
            f"""
            <section>
              <h2>{_e(stage)} <span class="count">{len(hits)} hits</span></h2>
              {cards or '<p class="note">No hits for this stage.</p>'}
            </section>
            """
        )
    path.write_text(_html_page("Retrieval Chunks", "\n".join(sections)), encoding="utf-8")


def render_stage_compare_html(path: Path, rows: list[dict[str, Any]]) -> None:
    body_rows = []
    for row in rows:
        body_rows.append(
            f"""
            <tr class="{_status_class(row.get("status"))}">
              <td>{_e(row.get("status"))}</td>
              <td>{_e(row.get("node_id") or row.get("point_id") or row.get("key"))}</td>
              <td>{_e(row.get("source"))}</td>
              <td>{_e(row.get("page"))}</td>
              <td>{_e(row.get("chunk_index"))}</td>
              <td>{_e(row.get("initial_retrieval_rank"))}</td>
              <td>{_e(row.get("rerank_rank"))}</td>
              <td>{_e(row.get("final_after_retry_rank"))}</td>
              <td>{_e(row.get("rank_flow"))}</td>
            </tr>
            """
        )
    body = f"""
    <section>
      <h2>Stage Rank Compare</h2>
      <table>
        <thead><tr><th>status</th><th>node</th><th>source</th><th>page</th><th>chunk</th><th>initial</th><th>rerank</th><th>final</th><th>flow</th></tr></thead>
        <tbody>{''.join(body_rows)}</tbody>
      </table>
    </section>
    """
    path.write_text(_html_page("Retrieval Stage Compare", body), encoding="utf-8")


def render_rank_flow_html(path: Path, flows: list[dict[str, Any]]) -> None:
    items = []
    for item in flows:
        items.append(
            f"""
            <article class="flow {_status_class(item.get("status"))}">
              <strong>{_e(item.get("status"))}</strong>
              <span>{_e(item.get("flow"))}</span>
              <small>{_e(item.get("node_id") or item.get("key"))} · source={_e(item.get("source"))} · chunk={_e(item.get("chunk_index"))}</small>
            </article>
            """
        )
    content = "".join(items) or '<p class="note">No rank flow rows.</p>'
    body = f"<section><h2>Rank Flow</h2>{content}</section>"
    path.write_text(_html_page("Retrieval Rank Flow", body), encoding="utf-8")


def render_citations_html(path: Path, record: dict[str, Any]) -> None:
    final_snapshot = _snapshot_by_stage(record, "final_after_retry") or _last_snapshot(record)
    hits = final_snapshot.get("hits") if isinstance(final_snapshot, dict) else []
    rows = []
    for index, hit in enumerate([x for x in hits or [] if isinstance(x, dict)], start=1):
        metadata = hit.get("metadata") or {}
        scores = hit.get("scores") or {}
        retrieval = hit.get("retrieval") or {}
        rows.append(
            f"""
            <article class="citation">
              <strong>[{index}]</strong>
              <span>source={_e(metadata.get("source"))}</span>
              <span>title={_e(metadata.get("title"))}</span>
              <span>page={_e(metadata.get("page"))}</span>
              <span>chunk_index={_e(hit.get("chunk_index") or metadata.get("chunk_index"))}</span>
              <span>score={_fmt_score(scores.get("score"))}</span>
              <span>rerank_score={_fmt_score(scores.get("rerank_score"))}</span>
              <span>modality={_e(retrieval.get("modality") or metadata.get("modality"))}</span>
              <span>node_id={_e(hit.get("node_id"))}</span>
              <span>tags=[]</span>
            </article>
            """
        )
    content = "".join(rows) or '<p class="note">No final hits.</p>'
    body = f"<section><h2>Citations</h2>{content}</section>"
    path.write_text(_html_page("Retrieval Citations", body), encoding="utf-8")


def _render_hit_card(stage: str, snapshot: dict[str, Any], hit: dict[str, Any]) -> str:
    metadata = hit.get("metadata") or {}
    relationships = hit.get("relationships") or {}
    scores = hit.get("scores") or {}
    retrieval = hit.get("retrieval") or {}
    modality = str(retrieval.get("modality") or metadata.get("modality") or "text")
    warnings = []
    if metadata.get("page") is None:
        warnings.append("page=null")
    if (hit.get("chunk_index") or metadata.get("chunk_index")) is None:
        warnings.append("chunk_index=null")
    if not relationships:
        warnings.append("relationships={}")
    if scores.get("rerank_score") is None:
        warnings.append("rerank_score=null")
    if metadata.get("source") is None:
        warnings.append("source=null")
    if hit.get("node_id") is None:
        warnings.append("node_id=null")
    if scores.get("score") is None:
        warnings.append("score=null")
    content = hit.get("text") or metadata.get("table_markdown") or metadata.get("formula_latex") or metadata.get("image_path") or ""
    fields = {
        "stage": stage,
        "rank": hit.get("rank"),
        "query_text": snapshot.get("query_text"),
        "node_id": hit.get("node_id"),
        "point_id": hit.get("point_id"),
        "doc_id": hit.get("doc_id"),
        "source": metadata.get("source"),
        "title": metadata.get("title"),
        "page": metadata.get("page"),
        "chunk_index": hit.get("chunk_index") or metadata.get("chunk_index"),
        "modality": modality,
        "channel": retrieval.get("channel"),
        "vector_name": retrieval.get("vector_name"),
        "parser_name": metadata.get("parser_name"),
        "source_parser": retrieval.get("source_parser"),
        "score": _fmt_score(scores.get("score")),
        "score_vector": _fmt_score(scores.get("score_vector")),
        "score_bm25": _fmt_score(scores.get("score_bm25")),
        "score_rrf": _fmt_score(scores.get("score_rrf")),
        "rerank_score": _fmt_score(scores.get("rerank_score")),
    }
    chips = "".join(f"<span><strong>{_e(k)}</strong>={_e(v)}</span>" for k, v in fields.items())
    return f"""
    <article class="hit {html.escape(modality)}">
      <div class="meta">{chips}</div>
      <div class="warnings">{_e(', '.join(warnings))}</div>
      <pre>{_e(content)}</pre>
      <details><summary>Hit JSON</summary><pre>{_e(json.dumps(hit, ensure_ascii=False, indent=2))}</pre></details>
    </article>
    """


def _snapshot_by_stage(record: dict[str, Any], stage: str) -> dict[str, Any] | None:
    for snapshot in record.get("snapshots") or []:
        if isinstance(snapshot, dict) and snapshot.get("stage") == stage:
            return snapshot
    return None


def _last_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    snapshots = [s for s in record.get("snapshots") or [] if isinstance(s, dict)]
    return snapshots[-1] if snapshots else {}


def _first_snapshot_query(record: dict[str, Any]) -> str | None:
    for snapshot in record.get("snapshots") or []:
        if isinstance(snapshot, dict) and snapshot.get("query_text"):
            return str(snapshot.get("query_text"))
    return None


def _sanitize_value(value: Any, *, include_full_vectors: bool) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, child in value.items():
            if not include_full_vectors and str(key).lower() in VECTOR_PAYLOAD_KEYS:
                continue
            clean[key] = _sanitize_value(child, include_full_vectors=include_full_vectors)
        return clean
    if isinstance(value, list):
        return [_sanitize_value(item, include_full_vectors=include_full_vectors) for item in value]
    return value


def _rank_status(row: dict[str, Any]) -> str:
    initial = row.get("initial_retrieval_rank")
    rerank = row.get("rerank_rank")
    final = row.get("final_after_retry_rank")
    statuses: list[str] = []
    if initial is None and rerank is not None:
        statuses.append("new_in_rerank")
    if initial is None and rerank is None and final is not None:
        statuses.append("new_in_final")
    if initial is not None and rerank is None and final is None:
        statuses.append("dropped_after_initial")
    if rerank is not None and final is None:
        statuses.append("dropped_after_rerank")
    if isinstance(initial, int) and isinstance(rerank, int):
        if rerank < initial:
            statuses.append("promoted_by_rerank")
        elif rerank > initial:
            statuses.append("demoted_by_rerank")
    if isinstance(rerank, int) and isinstance(final, int):
        if final < rerank:
            statuses.append("promoted_after_retry")
        elif final > rerank:
            statuses.append("demoted_after_retry")
    return ",".join(statuses) if statuses else "stable"


def _rank_flow_text(row: dict[str, Any]) -> str:
    parts = []
    for stage, label in (
        ("initial_retrieval_rank", "initial"),
        ("rerank_rank", "rerank"),
        ("final_after_retry_rank", "final"),
    ):
        rank = row.get(stage)
        parts.append(f"{label} #{rank}" if rank is not None else f"{label} dropped")
    return " -> ".join(parts)


def _hit_key(hit: dict[str, Any]) -> str:
    return str(hit.get("node_id") or hit.get("point_id") or hit.get("chunk_id") or "")


def _compare_sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    ranks = [
        row.get("initial_retrieval_rank"),
        row.get("rerank_rank"),
        row.get("final_after_retry_rank"),
    ]
    numeric = [rank for rank in ranks if isinstance(rank, int)]
    return (min(numeric) if numeric else 999999, len(numeric) * -1, str(row.get("key") or ""))


def _status_class(status: Any) -> str:
    raw = str(status or "stable")
    if "promoted" in raw:
        return "promoted"
    if "demoted" in raw:
        return "demoted"
    if "dropped" in raw:
        return "dropped"
    if "new" in raw:
        return "new"
    return "stable"


def _fmt_score(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, int):
        return str(value)
    return "null" if value is None else str(value)


def _truncate(text: str | None, max_chars: int) -> str:
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + f"\n...[truncated {len(value) - max_chars} chars]"


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _e(value: Any) -> str:
    return html.escape("null" if value is None else str(value))


def _html_page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ --ink:#17211b; --muted:#66736b; --bg:#f4efe4; --card:#fffdf7; --text:#7bb7d8; --table:#6fbf73; --formula:#d7a33a; --image:#d9795b; }}
    body {{ margin:0; color:var(--ink); background:radial-gradient(circle at 18% 4%, #fff7d6 0, transparent 28%), linear-gradient(135deg,#f4efe4,#e8f1e9); font-family: Cambria, Georgia, 'Times New Roman', serif; }}
    header {{ padding:30px 38px; color:#fff; background:linear-gradient(135deg,#1f392e,#526b47); }}
    header p {{ margin:.2rem 0 0; color:#d8e6dc; }}
    main {{ max-width:1320px; margin:0 auto; padding:24px; }}
    section {{ margin:0 0 26px; }}
    h2 {{ margin:18px 0 12px; }}
    .summary, .hit, .flow, .citation {{ border:1px solid rgba(0,0,0,.12); border-radius:16px; background:rgba(255,253,247,.92); box-shadow:0 10px 26px rgba(38,51,40,.08); }}
    .summary {{ padding:18px; }}
    .hit {{ margin:14px 0; padding:16px; border-left:9px solid var(--text); }}
    .hit.table {{ border-left-color:var(--table); }}
    .hit.formula {{ border-left-color:var(--formula); }}
    .hit.image {{ border-left-color:var(--image); }}
    .meta, .grid {{ display:flex; flex-wrap:wrap; gap:9px; color:var(--muted); font-size:13px; }}
    .meta span, .grid div, .count {{ border-radius:999px; background:rgba(31,57,46,.08); padding:5px 9px; }}
    .grid div {{ border-radius:12px; }}
    .grid strong {{ margin-right:8px; color:#294034; }}
    .warnings {{ min-height:20px; color:#b42318; margin:9px 0 0; font-weight:700; }}
    pre {{ white-space:pre-wrap; word-break:break-word; background:rgba(255,255,255,.72); border:1px solid rgba(0,0,0,.08); padding:12px; border-radius:12px; overflow:auto; }}
    table {{ width:100%; border-collapse:collapse; background:rgba(255,253,247,.95); border-radius:14px; overflow:hidden; box-shadow:0 8px 22px rgba(38,51,40,.08); }}
    th, td {{ border-bottom:1px solid rgba(0,0,0,.08); padding:10px; text-align:left; vertical-align:top; font-size:14px; }}
    th {{ background:#254235; color:white; }}
    tr.promoted, .flow.promoted {{ background:#edf8ea; }}
    tr.demoted, .flow.demoted {{ background:#fff8df; }}
    tr.dropped, .flow.dropped {{ background:#fff0ed; }}
    tr.new, .flow.new {{ background:#eaf4ff; }}
    .flow, .citation {{ margin:10px 0; padding:13px 15px; display:flex; gap:12px; flex-wrap:wrap; align-items:center; }}
    .flow small {{ color:var(--muted); flex-basis:100%; }}
    .note {{ color:var(--muted); }}
    @media (max-width: 860px) {{ header {{ padding:22px; }} main {{ padding:14px; }} .meta span {{ width:100%; border-radius:10px; }} }}
  </style>
</head>
<body>
  <header><h1>{html.escape(title)}</h1><p>Retrieval visualization for initial retrieval, rerank, and final retry snapshots.</p></header>
  <main>{body}</main>
</body>
</html>
"""
