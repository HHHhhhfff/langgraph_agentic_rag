from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCH = REPO_ROOT.parent / "SciEGQA-Bench" / "SciEGQA-Bench" / "SciEGQA_Bench.jsonl"


def doc_id_from_pdf(path: Path) -> str:
    match = re.search(r"(\d{4}\.\d{5})", path.name)
    if not match:
        raise ValueError(f"cannot infer doc id from PDF name: {path.name}")
    return match.group(1)


def safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")


def load_bench_rows(bench_path: Path, doc_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with bench_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if str(row.get("doc_name")) == doc_id:
                rows.append(row)
    return rows


def write_cases(*, rows: list[dict[str, Any]], doc_id: str, pdf_name: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    case_lines: list[str] = []
    for index, row in enumerate(rows, start=1):
        pages = row.get("evidence_page")
        if not isinstance(pages, list):
            pages = [pages] if pages not in (None, "") else []
        relevant_docs = []
        for page in pages:
            if isinstance(page, int):
                relevant_docs.append({"source": pdf_name, "page": page, "grade": 1.0})
        case = {
            "id": f"sciqa_{safe_id(doc_id)}_{index:03d}",
            "question": str(row.get("query", "")),
            "reference_answer": str(row.get("answer", "")),
            "expected_sources": [pdf_name, doc_id],
            "relevant_docs": relevant_docs,
            "sciqa_meta": {
                "doc_name": str(row.get("doc_name", doc_id)),
                "category": str(row.get("category", "")),
                "evidence_page": row.get("evidence_page"),
                "subimg_type": row.get("subimg_type"),
            },
        }
        case_lines.append(json.dumps(case, ensure_ascii=False))
    output_path.write_text("\n".join(case_lines) + "\n", encoding="utf-8")


def run_command(command: list[str], *, env: dict[str, str], log_path: Path) -> None:
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
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
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run SciEGQA evaluation for one PDF.")
    parser.add_argument("--pdf", required=True, help="PDF path")
    parser.add_argument("--bench", default=str(DEFAULT_BENCH), help="SciEGQA_Bench.jsonl path")
    parser.add_argument("--run-suffix", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--query-record-top-n", type=int, default=0)
    parser.add_argument("--skip-index", action="store_true", help="Reuse an existing collection/index")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    pdf_path = Path(args.pdf).resolve()
    bench_path = Path(args.bench).resolve()
    doc_id = doc_id_from_pdf(pdf_path)
    doc_safe = safe_id(doc_id)
    rows = load_bench_rows(bench_path, doc_id)
    if not rows:
        raise ValueError(f"no QA rows found for doc_id={doc_id}")

    eval_doc_dir = REPO_ROOT / "check" / "eval_docs" / f"{doc_safe}_pdf"
    eval_doc_dir.mkdir(parents=True, exist_ok=True)
    eval_pdf = eval_doc_dir / pdf_path.name
    shutil.copy2(pdf_path, eval_pdf)

    cases_path = REPO_ROOT / "check" / "eval_cases" / f"sciqa_{doc_safe}_cases.jsonl"
    write_cases(rows=rows, doc_id=doc_id, pdf_name=pdf_path.name, output_path=cases_path)

    collection = f"sciqa_{doc_safe}_eval"
    index_run_name = f"{doc_safe}_index_{args.run_suffix}"
    eval_run_name = f"{doc_safe}_eval_{args.run_suffix}"

    env = os.environ.copy()
    env["QDRANT_COLLECTION"] = collection
    env["NO_PROXY"] = "localhost,127.0.0.1,::1"
    env["no_proxy"] = "localhost,127.0.0.1,::1"
    env["PYTHONIOENCODING"] = "utf-8"

    index_summary_path = REPO_ROOT / "check" / "runs" / index_run_name / "index_summary.json"
    if not args.skip_index:
        env["QDRANT_RECREATE_COLLECTION"] = "true"
        run_command(
            [
                sys.executable,
                "-m",
                "check.benchmark_index",
                "--docs",
                str(eval_doc_dir.relative_to(REPO_ROOT)),
                "--run-name",
                index_run_name,
            ],
            env=env,
            log_path=REPO_ROOT / "check" / "runs" / index_run_name / "command_log.json",
        )

    env["QDRANT_RECREATE_COLLECTION"] = "false"
    run_command(
        [
            sys.executable,
            "-m",
            "check",
            "--dataset",
            str(cases_path.relative_to(REPO_ROOT)),
            "--k",
            str(args.k),
            "--rank-source",
            "rerank",
            "--ai-judge",
            "--index-summary",
            str(index_summary_path.relative_to(REPO_ROOT)),
            "--query-record-top-n",
            str(args.query_record_top_n),
            "--run-name",
            eval_run_name,
        ],
        env=env,
        log_path=REPO_ROOT / "check" / "runs" / eval_run_name / "command_log.json",
    )

    summary_path = REPO_ROOT / "check" / "runs" / eval_run_name / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    result = {
        "doc_id": doc_id,
        "pdf": str(pdf_path),
        "qa_count": len(rows),
        "collection": collection,
        "cases": str(cases_path),
        "index_run": index_run_name,
        "eval_run": eval_run_name,
        "summary": str(summary_path),
        "metrics_report": summary.get("artifacts", {}).get("metrics_report_md"),
        "query_stage_chunks": summary.get("artifacts", {}).get("query_stage_chunks"),
        "mean_metrics": summary.get("mean_metrics", {}),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
