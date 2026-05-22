from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from check.metrics import estimate_tokens, mixed_tokens, ranking_metrics, summarize_records


def hash_vector(text: str, dim: int) -> list[float]:
    vector = [0.0] * dim
    for token in mixed_tokens(text):
        digest = hashlib.md5(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def load_cases(bench_path: Path, doc_name: str) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with bench_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if str(row.get("doc_name")) == doc_name:
                cases.append(row)
    return cases


def extract_pdf_pages(pdf_path: Path) -> list[str]:
    from pdfminer.high_level import extract_text
    from pdfminer.pdfpage import PDFPage

    with pdf_path.open("rb") as handle:
        page_count = sum(1 for _ in PDFPage.get_pages(handle))

    pages: list[str] = []
    for page_index in range(page_count):
        text = extract_text(str(pdf_path), page_numbers=[page_index]) or ""
        pages.append(text.strip())
    return pages


def recreate_collection(client: Any, collection: str, dim: int) -> None:
    from qdrant_client.http import models

    if client.collection_exists(collection):
        client.delete_collection(collection)
    client.create_collection(
        collection_name=collection,
        vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
    )


def upsert_pages(client: Any, collection: str, pdf_path: Path, pages: list[str], dim: int) -> None:
    from qdrant_client.http import models

    points = []
    for index, text in enumerate(pages, start=1):
        if not text:
            text = f"Page {index}"
        payload = {
            "source": pdf_path.name,
            "title": pdf_path.stem,
            "page": index,
            "text": text[:12000],
            "metadata": {
                "source": pdf_path.name,
                "title": pdf_path.stem,
                "page": index,
                "chunk_index": index - 1,
            },
        }
        points.append(
            models.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{pdf_path.name}:{index}")),
                vector=hash_vector(text, dim),
                payload=payload,
            )
        )
    client.upsert(collection_name=collection, points=points, wait=True)


def search_pages(client: Any, collection: str, query: str, dim: int, top_k: int) -> list[dict[str, Any]]:
    query_vector = hash_vector(query, dim)
    if hasattr(client, "query_points"):
        response = client.query_points(
            collection_name=collection,
            query=query_vector,
            limit=top_k,
            with_payload=True,
        )
        points = response.points
    else:
        points = client.search(
            collection_name=collection,
            query_vector=query_vector,
            limit=top_k,
            with_payload=True,
        )
    hits: list[dict[str, Any]] = []
    for rank, point in enumerate(points, start=1):
        payload = point.payload or {}
        hits.append(
            {
                "rank": rank,
                "point_id": str(point.id),
                "source": payload.get("source"),
                "title": payload.get("title"),
                "page": payload.get("page"),
                "score": float(getattr(point, "score", 0.0) or 0.0),
            }
        )
    return hits


def evaluate(
    *,
    bench_path: Path,
    pdf_path: Path,
    doc_name: str,
    collection: str,
    top_k: int,
    dim: int,
    run_dir: Path,
) -> int:
    from qdrant_client import QdrantClient

    cases = load_cases(bench_path, doc_name)
    if not cases:
        raise ValueError(f"No cases found for doc_name={doc_name}")

    client = QdrantClient(url="http://localhost:6333", timeout=30.0)
    started = time.perf_counter()
    pages = extract_pdf_pages(pdf_path)
    recreate_collection(client, collection, dim)
    upsert_pages(client, collection, pdf_path, pages, dim)
    index_build_time_ms = (time.perf_counter() - started) * 1000

    records: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        query = str(case.get("query", ""))
        evidence_pages = [int(page) for page in case.get("evidence_page", []) if isinstance(page, int)]
        response_started = time.perf_counter()
        hits = search_pages(client, collection, query, dim, top_k)
        response_time_ms = (time.perf_counter() - response_started) * 1000
        relevance = [1.0 if int(hit.get("page") or -1) in evidence_pages else 0.0 for hit in hits]
        for hit, grade in zip(hits, relevance):
            hit["relevance_grade"] = grade
        metrics = ranking_metrics(
            relevance,
            total_relevant=max(1, len(set(evidence_pages))),
            ideal_relevance=[1.0] * max(1, len(set(evidence_pages))),
            k=top_k,
        )
        metrics.update(
            {
                "response_time_ms": response_time_ms,
                "index_build_time_ms": index_build_time_ms,
                "query_tokens_est": estimate_tokens(query),
                "reference_answer_tokens_est": estimate_tokens(case.get("answer", "")),
            }
        )
        records.append(
            {
                "id": f"{doc_name}_{index:03d}",
                "question": query,
                "reference_answer": case.get("answer"),
                "evidence_page": evidence_pages,
                "ranked_hits": hits,
                "metrics": metrics,
                "error": None,
            }
        )

    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "results.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    summary = summarize_records(records)
    summary["run"] = {
        "doc_name": doc_name,
        "pdf": str(pdf_path),
        "collection": collection,
        "top_k": top_k,
        "vector_dim": dim,
        "page_count": len(pages),
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Run directory: {run_dir}")
    print(f"Cases: {summary['case_count']}  Errors: {summary['error_count']}")
    for name, value in sorted(summary["mean_metrics"].items()):
        print(f"{name}: {value:.4f}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local Qdrant page-retrieval eval for a SciEGQA PDF.")
    parser.add_argument("--bench", required=True, help="SciEGQA_Bench.jsonl path")
    parser.add_argument("--pdf", required=True, help="PDF path")
    parser.add_argument("--doc-name", required=True, help="SciEGQA doc_name")
    parser.add_argument("--collection", default="sciqa_local_pdf_eval")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--dim", type=int, default=512)
    parser.add_argument("--output-dir", default="check/runs")
    parser.add_argument("--run-name", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output_root = Path(args.output_dir)
    if not output_root.is_absolute():
        output_root = REPO_ROOT / output_root
    run_dir = output_root / (args.run_name or f"local_pdf_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    return evaluate(
        bench_path=Path(args.bench),
        pdf_path=Path(args.pdf),
        doc_name=args.doc_name,
        collection=args.collection,
        top_k=args.k,
        dim=args.dim,
        run_dir=run_dir,
    )


if __name__ == "__main__":
    raise SystemExit(main())
