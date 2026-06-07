from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from check.run_eval import (
    REPO_ROOT,
    _bbox_iou,
    _bbox_source_page_matches,
    _bbox_hash,
    _hit_chunk_key,
    _hit_region_identity,
    _hit_value,
    _int_value,
    _normalize_bbox_1000,
    _hit_bboxes_1000,
    bbox_specs,
    load_cases,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Derive query-specific bbox relevance labels from cases and index nodes.")
    parser.add_argument("--dataset", required=True, help="Path to check cases JSONL/JSON.")
    parser.add_argument("--index-nodes", required=True, help="Path to ingestion visualization nodes.jsonl.")
    parser.add_argument("--output", default=None, help="Output derived labels JSON path.")
    parser.add_argument("--iou-threshold", type=float, default=0.1, help="Minimum IoU for marking an indexed bbox region relevant.")
    parser.add_argument("--page-tolerance", type=int, default=0, help="Allow expected page +/- N while deriving labels.")
    parser.add_argument("--limit", type=int, default=None, help="Only derive labels for first N cases.")
    return parser.parse_args(argv)


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def load_index_nodes(path: Path) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            obj = json.loads(line)
            if not isinstance(obj, dict):
                continue
            metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
            hit = {
                "rank": line_no,
                "node_id": obj.get("node_id"),
                "point_id": obj.get("point_id"),
                "doc_id": obj.get("doc_id") or metadata.get("doc_id"),
                "source": metadata.get("source") or obj.get("source"),
                "title": metadata.get("title") or obj.get("title"),
                "page": metadata.get("page") or obj.get("page"),
                "pages": metadata.get("pages") or obj.get("pages"),
                "chunk_index": metadata.get("chunk_index") or obj.get("chunk_index"),
                "modality": metadata.get("modality"),
                "bbox": metadata.get("bbox"),
                "bbox_items": metadata.get("bbox_items"),
                "bbox_by_page": metadata.get("bbox_by_page"),
                "page_spans": metadata.get("page_spans"),
                "bbox_coordinate_system": metadata.get("bbox_coordinate_system"),
                "bbox_source": metadata.get("bbox_source"),
                "bbox_merge_policy": metadata.get("bbox_merge_policy"),
                "text": obj.get("text"),
            }
            hits.append(hit)
    return hits


def derive_case_labels(
    *,
    case: dict[str, Any],
    index_hits: list[dict[str, Any]],
    iou_threshold: float,
    page_tolerance: int,
) -> dict[str, Any]:
    specs = bbox_specs(case)
    matched_regions: dict[str, dict[str, Any]] = {}
    matched_chunks: dict[str, dict[str, Any]] = {}
    for spec_index, spec in enumerate(specs):
        spec_box = _normalize_bbox_1000(spec.get("bbox"))
        if spec_box is None:
            continue
        for hit_index, hit in enumerate(index_hits, start=1):
            if not _bbox_source_page_matches(hit, spec, page_tolerance=page_tolerance):
                continue
            hit_boxes = _hit_bboxes_1000(
                hit,
                page=_int_value(spec.get("page")),
                page_tolerance=page_tolerance,
            )
            chunk_key = _hit_chunk_key(hit, hit_index)
            for box_index, hit_box in enumerate(hit_boxes):
                iou = _bbox_iou(hit_box, spec_box)
                if iou <= iou_threshold:
                    continue
                identity = _hit_region_identity(hit) or chunk_key
                page = _int_value(spec.get("page")) or _int_value(_hit_value(hit, "page"))
                region_key = f"{identity}|page={page}|bbox={_bbox_hash(hit_box)}"
                previous = matched_regions.get(region_key)
                if previous is None or iou > float(previous.get("iou", 0.0)):
                    matched_regions[region_key] = {
                        "region_key": region_key,
                        "chunk_key": chunk_key,
                        "node_id": _hit_value(hit, "node_id"),
                        "point_id": _hit_value(hit, "point_id"),
                        "source": _hit_value(hit, "source"),
                        "doc_id": _hit_value(hit, "doc_id"),
                        "title": _hit_value(hit, "title"),
                        "page": _hit_value(hit, "page"),
                        "chunk_index": _hit_value(hit, "chunk_index"),
                        "modality": _hit_value(hit, "modality"),
                        "bbox": hit_box,
                        "iou": iou,
                        "matched_spec_index": spec_index,
                    }
                chunk_previous = matched_chunks.get(chunk_key)
                if chunk_previous is None or iou > float(chunk_previous.get("best_iou", 0.0)):
                    matched_chunks[chunk_key] = {
                        "chunk_key": chunk_key,
                        "node_id": _hit_value(hit, "node_id"),
                        "point_id": _hit_value(hit, "point_id"),
                        "source": _hit_value(hit, "source"),
                        "doc_id": _hit_value(hit, "doc_id"),
                        "title": _hit_value(hit, "title"),
                        "page": _hit_value(hit, "page"),
                        "chunk_index": _hit_value(hit, "chunk_index"),
                        "modality": _hit_value(hit, "modality"),
                        "best_iou": iou,
                    }

    return {
        "case_id": case["id"],
        "bbox_spec_count": len(specs),
        "matched_region_count": len(matched_regions),
        "matched_chunk_count": len(matched_chunks),
        "relevant_region_keys": sorted(matched_regions),
        "relevant_chunk_keys": sorted(matched_chunks),
        "matched_regions": sorted(matched_regions.values(), key=lambda item: (str(item.get("node_id")), str(item.get("region_key")))),
        "matched_chunks": sorted(matched_chunks.values(), key=lambda item: str(item.get("chunk_key"))),
    }


def default_output_path(dataset: Path, index_nodes: Path) -> Path:
    dataset_name = dataset.stem
    run_id = index_nodes.parent.name
    return REPO_ROOT / "check" / "derived_bbox_labels" / dataset_name / f"{run_id}.json"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dataset = resolve_path(args.dataset)
    index_nodes = resolve_path(args.index_nodes)
    output = resolve_path(args.output) if args.output else default_output_path(dataset, index_nodes)
    cases = load_cases(dataset, limit=args.limit)
    index_hits = load_index_nodes(index_nodes)
    labels = {
        "schema_version": "derived_bbox_labels_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "dataset": str(dataset),
        "index_nodes": str(index_nodes),
        "iou_threshold": args.iou_threshold,
        "page_tolerance": args.page_tolerance,
        "case_count": len(cases),
        "cases": {
            case["id"]: derive_case_labels(
                case=case,
                index_hits=index_hits,
                iou_threshold=args.iou_threshold,
                page_tolerance=args.page_tolerance,
            )
            for case in cases
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
    total_chunks = sum((case.get("matched_chunk_count") or 0) for case in labels["cases"].values())
    total_regions = sum((case.get("matched_region_count") or 0) for case in labels["cases"].values())
    print(json.dumps({"output": str(output), "case_count": len(cases), "matched_chunks": total_chunks, "matched_regions": total_regions}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
