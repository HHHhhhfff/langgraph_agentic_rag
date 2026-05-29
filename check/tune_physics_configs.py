from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

from check.run_sciqa_pdf import doc_id_from_pdf, load_bench_rows, safe_id, write_cases


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCH = REPO_ROOT.parent / "SciEGQA-Bench" / "SciEGQA-Bench" / "SciEGQA_Bench.jsonl"
DEFAULT_PDF_DIR = REPO_ROOT.parent / "SciEGQA-Bench" / "SciEGQA-Bench" / "PDF" / "physics"
DOC_IDS = ("2409.18430", "2411.15169", "2412.16030", "2501.00475")


COMMON_ENV = {
    "LLM_BASE_URL": "https://apirouter.ai/v1",
    "LLM_MODEL": "gpt-5.4",
    "CONTEXT_TOP_N": "6",
    "TEXT_CHUNK_PARSER": "sentence",
    "CHUNK_SIZE": "900",
    "CHUNK_OVERLAP": "120",
    "CHUNK_MIN_LENGTH": "80",
    "ENABLE_MINERU": "true",
    "MINERU_MODE": "precise",
    "INGESTION_ENGINE": "multimodal",
    "MULTIMODAL_ENABLED": "true",
    "PDF_PARSER": "unstructured",
    "BM25_ENABLED": "true",
    "RRF_K": "60",
    "REL_EXPAND_STEPS": "1",
    "REL_EXPAND_PAGES": "1",
    "NO_PROXY": "localhost,127.0.0.1,::1",
    "no_proxy": "localhost,127.0.0.1,::1",
    "PYTHONIOENCODING": "utf-8",
}


TEXT_INDEX = {
    "EMBEDDING_PROVIDER_TYPE": "openai_compatible",
    "EMBEDDING_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "EMBEDDING_MODEL": "text-embedding-v4",
    "EMBEDDING_BATCH_SIZE": "8",
    "EMBEDDING_DIMENSIONS": "1024",
    "ENABLE_NAMED_VECTORS": "false",
    "IMAGE_EMBED_PROVIDER_TYPE": "openai_compatible",
}


VL_INDEX = {
    "EMBEDDING_PROVIDER_TYPE": "dashscope_multimodal",
    "EMBEDDING_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "EMBEDDING_MODEL": "qwen3-vl-embedding",
    "DASHSCOPE_EMBEDDING_MODEL": "qwen3-vl-embedding",
    "EMBEDDING_BATCH_SIZE": "10",
    "EMBEDDING_DIMENSIONS": "1024",
    "IMAGE_EMBED_PROVIDER_TYPE": "dashscope_multimodal",
    "ENABLE_NAMED_VECTORS": "true",
}


TEXT_RERANK = {
    "RERANK_PROVIDER": "dashscope",
    "RERANK_BASE_URL": "https://dashscope.aliyuncs.com/compatible-api/v1",
    "RERANK_MODEL": "qwen3-rerank",
    "RERANK_ENABLE_MULTIMODAL": "false",
}


VL_RERANK = {
    "RERANK_PROVIDER": "dashscope",
    "RERANK_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "RERANK_MODEL": "qwen3-vl-rerank",
    "RERANK_ENABLE_MULTIMODAL": "true",
}


CONFIGS: list[dict[str, Any]] = [
    {
        "id": "r01_text_base",
        "index_profile": "text",
        "note": "text embedding + qwen3-rerank baseline",
        "env": {**TEXT_INDEX, **TEXT_RERANK, "RERANK_TOP_N": "10", "RETRIEVAL_TOP_K": "12", "BM25_TOP_K": "12", "PAGE_TOP_K": "8", "TABLE_TOP_K": "8", "RRF_TOP_K": "12", "RETRIEVAL_MIN_SCORE": "0.15"},
    },
    {
        "id": "r02_text_wide",
        "index_profile": "text",
        "note": "text embedding, wider candidate pool",
        "env": {**TEXT_INDEX, **TEXT_RERANK, "RERANK_TOP_N": "10", "RETRIEVAL_TOP_K": "16", "BM25_TOP_K": "16", "PAGE_TOP_K": "10", "TABLE_TOP_K": "10", "RRF_TOP_K": "16", "RETRIEVAL_MIN_SCORE": "0.10"},
    },
    {
        "id": "r03_text_wider_rerank12",
        "index_profile": "text",
        "note": "text embedding, widest recall and rerank top 12",
        "env": {**TEXT_INDEX, **TEXT_RERANK, "RERANK_TOP_N": "12", "RETRIEVAL_TOP_K": "20", "BM25_TOP_K": "20", "PAGE_TOP_K": "12", "TABLE_TOP_K": "12", "RRF_TOP_K": "20", "RETRIEVAL_MIN_SCORE": "0.10"},
    },
    {
        "id": "r04_text_precision",
        "index_profile": "text",
        "note": "text embedding, tighter filter for precision",
        "env": {**TEXT_INDEX, **TEXT_RERANK, "RERANK_TOP_N": "8", "RETRIEVAL_TOP_K": "12", "BM25_TOP_K": "12", "PAGE_TOP_K": "8", "TABLE_TOP_K": "8", "RRF_TOP_K": "12", "RETRIEVAL_MIN_SCORE": "0.20", "REL_EXPAND_RELATED_MODALITY_ENABLED": "false"},
    },
    {
        "id": "r05_text_wide_context8",
        "index_profile": "text",
        "note": "text embedding, wider recall and more context for answer generation",
        "env": {**TEXT_INDEX, **TEXT_RERANK, "RERANK_TOP_N": "12", "CONTEXT_TOP_N": "8", "RETRIEVAL_TOP_K": "16", "BM25_TOP_K": "16", "PAGE_TOP_K": "10", "TABLE_TOP_K": "10", "RRF_TOP_K": "16", "RETRIEVAL_MIN_SCORE": "0.10"},
    },
    {
        "id": "r06_vl_current",
        "index_profile": "vl",
        "note": "current .env style: VL embedding + VL rerank",
        "env": {**VL_INDEX, **VL_RERANK, "RERANK_TOP_N": "8", "RETRIEVAL_TOP_K": "12", "BM25_TOP_K": "12", "PAGE_TOP_K": "8", "TABLE_TOP_K": "8", "RRF_TOP_K": "12", "RETRIEVAL_MIN_SCORE": "0.15"},
    },
    {
        "id": "r07_vl_wide",
        "index_profile": "vl",
        "note": "VL embedding/rerank, wider candidate pool",
        "env": {**VL_INDEX, **VL_RERANK, "RERANK_TOP_N": "10", "RETRIEVAL_TOP_K": "16", "BM25_TOP_K": "16", "PAGE_TOP_K": "10", "TABLE_TOP_K": "12", "RRF_TOP_K": "16", "RETRIEVAL_MIN_SCORE": "0.10"},
    },
    {
        "id": "r08_vl_wider_rerank12",
        "index_profile": "vl",
        "note": "VL embedding/rerank, widest recall and rerank top 12",
        "env": {**VL_INDEX, **VL_RERANK, "RERANK_TOP_N": "12", "RETRIEVAL_TOP_K": "20", "BM25_TOP_K": "20", "PAGE_TOP_K": "12", "TABLE_TOP_K": "14", "RRF_TOP_K": "20", "RETRIEVAL_MIN_SCORE": "0.10"},
    },
    {
        "id": "r09_vl_embed_text_rerank",
        "index_profile": "vl",
        "note": "VL embedding with text reranker",
        "env": {**VL_INDEX, **TEXT_RERANK, "RERANK_TOP_N": "10", "RETRIEVAL_TOP_K": "16", "BM25_TOP_K": "16", "PAGE_TOP_K": "10", "TABLE_TOP_K": "12", "RRF_TOP_K": "16", "RETRIEVAL_MIN_SCORE": "0.10"},
    },
    {
        "id": "r10_text_named_vectors",
        "index_profile": "text_named",
        "note": "text embedding with named vectors enabled",
        "env": {**TEXT_INDEX, **TEXT_RERANK, "ENABLE_NAMED_VECTORS": "true", "RERANK_TOP_N": "10", "RETRIEVAL_TOP_K": "16", "BM25_TOP_K": "16", "PAGE_TOP_K": "10", "TABLE_TOP_K": "10", "RRF_TOP_K": "16", "RETRIEVAL_MIN_SCORE": "0.10"},
    },
]


def _find_pdf(pdf_dir: Path, doc_id: str) -> Path:
    matches = sorted(pdf_dir.glob(f"{doc_id}_*.pdf"))
    if not matches:
        raise FileNotFoundError(f"missing PDF for {doc_id} under {pdf_dir}")
    return matches[0]


def _run(command: list[str], *, env: dict[str, str], log_path: Path) -> subprocess.CompletedProcess[str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
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
    return completed


def _prepare_cases(pdf: Path, bench: Path) -> tuple[str, Path, Path]:
    doc_id = doc_id_from_pdf(pdf)
    doc_safe = safe_id(doc_id)
    rows = load_bench_rows(bench, doc_id)
    if not rows:
        raise ValueError(f"no QA rows found for doc_id={doc_id}")
    eval_doc_dir = REPO_ROOT / "check" / "eval_docs" / f"{doc_safe}_pdf"
    eval_doc_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdf, eval_doc_dir / pdf.name)
    cases_path = REPO_ROOT / "check" / "eval_cases" / f"sciqa_{doc_safe}_cases.jsonl"
    write_cases(rows=rows, doc_id=doc_id, pdf_name=pdf.name, output_path=cases_path)
    return doc_id, eval_doc_dir, cases_path


def _float(data: dict[str, Any], key: str) -> float | None:
    value = data.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _objective(metrics: dict[str, float | None]) -> float:
    return (
        0.30 * (metrics.get("rerank_precision_at_1") or 0.0)
        + 0.20 * (metrics.get("rerank_precision_at_3") or 0.0)
        + 0.20 * (metrics.get("final_precision_at_1") or 0.0)
        + 0.10 * (metrics.get("final_hit_rate") or 0.0)
        + 0.20 * ((metrics.get("ai_score_100") or 0.0) / 100.0)
    )


def _average(records: list[dict[str, Any]], key: str) -> float | None:
    values = [record.get(key) for record in records if isinstance(record.get(key), (int, float))]
    return mean(values) if values else None


def _write_report(out_dir: Path, summaries: list[dict[str, Any]]) -> None:
    sorted_summaries = sorted(summaries, key=lambda item: item.get("objective", 0.0), reverse=True)
    (out_dir / "tuning_summary.json").write_text(json.dumps(sorted_summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Physics Config Tuning",
        "",
        "| rank | config | objective | Rerank P@1 | Rerank P@3 | Final P@1 | Final Hit | AI/100 | chunks | note |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for rank, item in enumerate(sorted_summaries, start=1):
        lines.append(
            "| {rank} | {config} | {objective:.4f} | {rp1:.4f} | {rp3:.4f} | {fp1:.4f} | {fh:.4f} | {ai:.2f} | {chunks:.1f} | {note} |".format(
                rank=rank,
                config=item["config_id"],
                objective=item["objective"],
                rp1=item.get("rerank_precision_at_1") or 0.0,
                rp3=item.get("rerank_precision_at_3") or 0.0,
                fp1=item.get("final_precision_at_1") or 0.0,
                fh=item.get("final_hit_rate") or 0.0,
                ai=item.get("ai_score_100") or 0.0,
                chunks=item.get("index_chunks") or 0.0,
                note=item.get("note", ""),
            )
        )
    (out_dir / "tuning_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tune a fixed set of physics evaluation configs.")
    parser.add_argument("--pdf-dir", default=str(DEFAULT_PDF_DIR))
    parser.add_argument("--bench", default=str(DEFAULT_BENCH))
    parser.add_argument("--case-limit", type=int, default=8)
    parser.add_argument("--run-prefix", default=datetime.now().strftime("tuning_physics_%Y%m%d_%H%M%S"))
    args = parser.parse_args(argv)

    pdf_dir = Path(args.pdf_dir).resolve()
    bench = Path(args.bench).resolve()
    out_dir = REPO_ROOT / "check" / "runs" / args.run_prefix
    out_dir.mkdir(parents=True, exist_ok=True)
    selected_pdfs = [_find_pdf(pdf_dir, doc_id) for doc_id in DOC_IDS]
    (out_dir / "selected_docs.json").write_text(
        json.dumps([str(path) for path in selected_pdfs], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summaries: list[dict[str, Any]] = []
    index_cache: dict[tuple[str, str], tuple[Path, str, float | None]] = {}
    for config_index, config in enumerate(CONFIGS, start=1):
        config_id = str(config["id"])
        index_profile = str(config.get("index_profile") or config_id)
        config_dir = out_dir / config_id
        config_dir.mkdir(parents=True, exist_ok=True)
        records: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        print(f"[{config_index}/{len(CONFIGS)}] {config_id}: {config['note']}", flush=True)

        for pdf in selected_pdfs:
            doc_id, eval_doc_dir, cases_path = _prepare_cases(pdf, bench)
            doc_safe = safe_id(doc_id)
            collection = f"sciqa_{doc_safe}_{safe_id(index_profile)}_{safe_id(args.run_prefix)}"
            run_name = f"{doc_safe}_{config_id}_{args.run_prefix}"
            index_run = f"{doc_safe}_index_{index_profile}_{args.run_prefix}"
            eval_run = f"{doc_safe}_eval_{config_id}_{args.run_prefix}"
            env = os.environ.copy()
            env.update(COMMON_ENV)
            env.update({str(k): str(v) for k, v in config["env"].items()})
            env["QDRANT_COLLECTION"] = collection

            index_summary = REPO_ROOT / "check" / "runs" / index_run / "index_summary.json"
            cache_key = (doc_id, index_profile)
            if cache_key in index_cache:
                index_summary, collection, cached_chunks = index_cache[cache_key]
            else:
                index_log = out_dir / "index_logs" / f"{doc_safe}_{safe_id(index_profile)}_index_command_log.json"
                env["QDRANT_RECREATE_COLLECTION"] = "true"
                index_completed = _run(
                    [
                        sys.executable,
                        "-m",
                        "check.benchmark_index",
                        "--docs",
                        str(eval_doc_dir.relative_to(REPO_ROOT)),
                        "--run-name",
                        index_run,
                    ],
                    env=env,
                    log_path=index_log,
                )
                if index_completed.returncode:
                    failures.append({"doc_id": doc_id, "stage": "index", "log": str(index_log)})
                    print(f"  {doc_id}: index failed", flush=True)
                    continue
                index_data = json.loads(index_summary.read_text(encoding="utf-8"))
                index_info = index_data.get("index_summary") or {}
                cached_chunks = _float(index_info, "chunks")
                index_cache[cache_key] = (index_summary, collection, cached_chunks)

            eval_log = config_dir / f"{doc_safe}_eval_command_log.json"
            env["QDRANT_RECREATE_COLLECTION"] = "false"
            env["QDRANT_COLLECTION"] = collection
            eval_completed = _run(
                [
                    sys.executable,
                    "-m",
                    "check",
                    "--dataset",
                    str(cases_path.relative_to(REPO_ROOT)),
                    "--limit",
                    str(args.case_limit),
                    "--k",
                    "10",
                    "--rank-source",
                    "rerank",
                    "--ai-judge",
                    "--index-summary",
                    str(index_summary.relative_to(REPO_ROOT)),
                    "--query-record-top-n",
                    "0",
                    "--run-name",
                    eval_run,
                ],
                env=env,
                log_path=eval_log,
            )
            if eval_completed.returncode:
                failures.append({"doc_id": doc_id, "stage": "eval", "log": str(eval_log)})
                print(f"  {doc_id}: eval failed", flush=True)
                continue

            summary_path = REPO_ROOT / "check" / "runs" / eval_run / "summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            mean_metrics = summary.get("mean_metrics") or {}
            record = {
                "doc_id": doc_id,
                "index_profile": index_profile,
                "collection": collection,
                "summary": str(summary_path),
                "index_summary": str(index_summary),
                "qa_count": int(summary.get("case_count") or 0),
                "index_chunks": cached_chunks,
                "initial_precision_at_1": _float(mean_metrics, "initial_recall_precision_at_1"),
                "initial_precision_at_3": _float(mean_metrics, "initial_recall_precision_at_3"),
                "initial_hit_rate": _float(mean_metrics, "initial_recall_hit_rate"),
                "rerank_precision_at_1": _float(mean_metrics, "rerank_precision_at_1"),
                "rerank_precision_at_3": _float(mean_metrics, "rerank_precision_at_3"),
                "rerank_hit_rate": _float(mean_metrics, "rerank_hit_rate"),
                "final_precision_at_1": _float(mean_metrics, "local_recheck_precision_at_1"),
                "final_precision_at_3": _float(mean_metrics, "local_recheck_precision_at_3"),
                "final_hit_rate": _float(mean_metrics, "local_recheck_hit_rate"),
                "ai_score_100": _float(mean_metrics, "ai_score_100"),
            }
            records.append(record)
            print(
                "  {doc_id}: rerank P@1={rp1:.4f}, P@3={rp3:.4f}, final P@1={fp1:.4f}, AI={ai:.2f}".format(
                    doc_id=doc_id,
                    rp1=record["rerank_precision_at_1"] or 0.0,
                    rp3=record["rerank_precision_at_3"] or 0.0,
                    fp1=record["final_precision_at_1"] or 0.0,
                    ai=record["ai_score_100"] or 0.0,
                ),
                flush=True,
            )

        aggregate = {
            "config_id": config_id,
            "note": config["note"],
            "env": config["env"],
            "case_limit_per_doc": args.case_limit,
            "doc_count": len(records),
            "failure_count": len(failures),
            "records": records,
            "failures": failures,
        }
        for key in (
            "index_chunks",
            "initial_precision_at_1",
            "initial_precision_at_3",
            "initial_hit_rate",
            "rerank_precision_at_1",
            "rerank_precision_at_3",
            "rerank_hit_rate",
            "final_precision_at_1",
            "final_precision_at_3",
            "final_hit_rate",
            "ai_score_100",
        ):
            aggregate[key] = _average(records, key)
        aggregate["objective"] = _objective(aggregate)
        (config_dir / "summary.json").write_text(json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8")
        summaries.append(aggregate)
        _write_report(out_dir, summaries)
        print(f"  objective={aggregate['objective']:.4f}", flush=True)

    _write_report(out_dir, summaries)
    print(json.dumps({"output_dir": str(out_dir), "summary": str(out_dir / "tuning_summary.md")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
