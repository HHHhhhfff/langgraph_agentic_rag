from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from check.run_sciqa_pdf import DEFAULT_BENCH, REPO_ROOT, safe_id


def _extract_json(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("command did not emit a JSON object")
    payload = text[start : end + 1]
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("command JSON output must be an object")
    return data


def _load_json(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    if not candidate.exists():
        return {}
    data = json.loads(candidate.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _metric(mean_metrics: dict[str, Any], name: str) -> float | None:
    value = mean_metrics.get(name)
    return float(value) if isinstance(value, (int, float)) else None


def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
    summary = _load_json(result.get("summary"))
    mean_metrics = summary.get("mean_metrics") or result.get("mean_metrics") or {}
    index_info = ((summary.get("required_metrics") or {}).get("index_info") or {})
    artifacts = summary.get("artifacts") or {}
    timing = ((summary.get("required_metrics") or {}).get("timing") or {})

    return {
        "doc_id": result.get("doc_id"),
        "pdf": result.get("pdf"),
        "qa_count": result.get("qa_count"),
        "collection": result.get("collection"),
        "eval_run": result.get("eval_run"),
        "summary": result.get("summary"),
        "metrics_report": artifacts.get("metrics_report_md") or result.get("metrics_report"),
        "metrics_report_json": artifacts.get("metrics_report_json"),
        "query_stage_chunks": artifacts.get("query_stage_chunks") or result.get("query_stage_chunks"),
        "index_chunks": (index_info.get("index_summary") or {}).get("chunks"),
        "index_vectors": (index_info.get("index_summary") or {}).get("vectors"),
        "index_build_time_sec": timing.get("index_build_time_sec"),
        "avg_response_time_ms": timing.get("avg_response_time_ms"),
        "initial_precision_at_1": _metric(mean_metrics, "initial_recall_precision_at_1"),
        "initial_precision_at_3": _metric(mean_metrics, "initial_recall_precision_at_3"),
        "initial_hit_rate": _metric(mean_metrics, "initial_recall_hit_rate"),
        "rerank_precision_at_1": _metric(mean_metrics, "rerank_precision_at_1"),
        "rerank_precision_at_3": _metric(mean_metrics, "rerank_precision_at_3"),
        "rerank_hit_rate": _metric(mean_metrics, "rerank_hit_rate"),
        "final_precision_at_1": _metric(mean_metrics, "local_recheck_precision_at_1"),
        "final_precision_at_3": _metric(mean_metrics, "local_recheck_precision_at_3"),
        "final_hit_rate": _metric(mean_metrics, "local_recheck_hit_rate"),
        "ai_score_100": _metric(mean_metrics, "ai_score_100"),
    }


def _write_markdown(path: Path, records: list[dict[str, Any]], failures: list[dict[str, Any]]) -> None:
    lines = [
        "# SciEGQA PDF Directory Evaluation",
        "",
        "| doc_id | QA | chunks | P@1 initial/rerank/final | P@3 initial/rerank/final | AI/100 | report | query records |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for item in records:
        report = item.get("metrics_report") or ""
        query_records = item.get("query_stage_chunks") or ""
        lines.append(
            "| {doc_id} | {qa_count} | {chunks} | {p1i:.4f}/{p1r:.4f}/{p1f:.4f} | "
            "{p3i:.4f}/{p3r:.4f}/{p3f:.4f} | {ai:.2f} | {report} | {query_records} |".format(
                doc_id=item.get("doc_id") or "",
                qa_count=item.get("qa_count") or 0,
                chunks=item.get("index_chunks") or 0,
                p1i=item.get("initial_precision_at_1") or 0.0,
                p1r=item.get("rerank_precision_at_1") or 0.0,
                p1f=item.get("final_precision_at_1") or 0.0,
                p3i=item.get("initial_precision_at_3") or 0.0,
                p3r=item.get("rerank_precision_at_3") or 0.0,
                p3f=item.get("final_precision_at_3") or 0.0,
                ai=item.get("ai_score_100") or 0.0,
                report=report,
                query_records=query_records,
            )
        )
    if failures:
        lines.extend(["", "## Failures", ""])
        for failure in failures:
            lines.append(f"- {failure.get('pdf')}: {failure.get('error')}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run SciEGQA evaluation for every PDF in a directory.")
    parser.add_argument("--pdf-dir", required=True, help="Directory containing PDF files")
    parser.add_argument("--bench", default=str(DEFAULT_BENCH), help="SciEGQA_Bench.jsonl path")
    parser.add_argument("--run-suffix", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--query-record-top-n", type=int, default=0)
    parser.add_argument("--skip-index", action="store_true", help="Reuse existing per-PDF collections")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue if one PDF fails")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    pdf_dir = Path(args.pdf_dir).resolve()
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if not pdfs:
        raise FileNotFoundError(f"no PDFs found in: {pdf_dir}")

    batch_name = f"{safe_id(pdf_dir.name)}_batch_{safe_id(args.run_suffix)}"
    batch_dir = REPO_ROOT / "check" / "runs" / batch_name
    batch_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for index, pdf in enumerate(pdfs, start=1):
        print(f"[{index}/{len(pdfs)}] evaluating {pdf.name}", flush=True)
        command = [
            sys.executable,
            "-m",
            "check.run_sciqa_pdf",
            "--pdf",
            str(pdf),
            "--bench",
            str(Path(args.bench).resolve()),
            "--run-suffix",
            args.run_suffix,
            "--k",
            str(args.k),
            "--query-record-top-n",
            str(args.query_record_top_n),
        ]
        if args.skip_index:
            command.append("--skip-index")
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        log_path = batch_dir / f"{safe_id(pdf.stem)}_command_log.json"
        log_path.write_text(
            json.dumps(
                {
                    "command": command,
                    "returncode": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if completed.returncode:
            failure = {"pdf": str(pdf), "returncode": completed.returncode, "error": completed.stderr, "log": str(log_path)}
            failures.append(failure)
            if not args.continue_on_error:
                break
            continue
        result = _extract_json(completed.stdout)
        record = _compact_result(result)
        records.append(record)
        print(
            "  done: P@1 rerank={:.4f}, P@3 rerank={:.4f}, AI/100={:.2f}".format(
                record.get("rerank_precision_at_1") or 0.0,
                record.get("rerank_precision_at_3") or 0.0,
                record.get("ai_score_100") or 0.0,
            ),
            flush=True,
        )

    summary = {
        "pdf_dir": str(pdf_dir),
        "pdf_count": len(pdfs),
        "completed_count": len(records),
        "failure_count": len(failures),
        "run_suffix": args.run_suffix,
        "records": records,
        "failures": failures,
    }
    summary_path = batch_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path = batch_dir / "summary.md"
    _write_markdown(markdown_path, records, failures)
    print(json.dumps({"summary": str(summary_path), "summary_md": str(markdown_path), **summary}, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
