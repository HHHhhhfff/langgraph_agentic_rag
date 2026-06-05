from __future__ import annotations

import html
import json
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_rag.config import Settings
from agentic_rag.ingestion.node_schema import IngestionFailure, MultimodalIngestionResult, Node
from agentic_rag.ingestion.utils import build_doc_id
from agentic_rag.retrieval.index_persistence import build_page_hits, build_table_hits, hits_from_nodes


DOC_LIKE_SUFFIXES = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".html", ".htm"}


class IngestionInspectionError(RuntimeError):
    """Raised when ingestion inspection cannot be completed."""


def build_run_id(now: datetime | None = None) -> str:
    ts = (now or datetime.now(timezone.utc)).astimezone().strftime("%Y%m%d_%H%M%S")
    return f"{ts}_{uuid.uuid4().hex[:8]}"


def node_embedding_text(node: Node) -> str:
    if node.modality == "table":
        return node.table_markdown or node.text or ""
    if node.modality == "formula":
        return node.formula_latex or node.text or ""
    if node.modality == "image":
        image_path = node.image_path or node.metadata.source
        return node.text or f"Image file: {Path(image_path).name}"
    return node.text or ""


def serialize_node(node: Node, *, max_text_chars: int = 4000) -> dict[str, Any]:
    data = node.model_dump()
    for key in ("text", "table_markdown", "formula_latex"):
        if isinstance(data.get(key), str):
            data[key] = _truncate(data[key], max_text_chars)
    return data


def serialize_embedding_preview(
    node: Node,
    *,
    settings: Settings,
    max_text_chars: int = 4000,
    vector_dim: int | None = None,
) -> dict[str, Any]:
    vector_name = _vector_name_for_node(node, settings)
    text = node_embedding_text(node)
    return {
        "node_id": node.node_id,
        "modality": node.modality,
        "vector_name": vector_name,
        "vector_dim": vector_dim if vector_dim is not None else settings.embedding_dimensions,
        "embedding_text_excerpt": _truncate(text, max_text_chars),
        "embedding_text_chars": len(text),
    }


def build_qdrant_payload_preview(
    node: Node,
    *,
    settings: Settings,
    vector_dim: int | None = None,
) -> dict[str, Any]:
    md = node.metadata
    payload = {
        "text": node.text,
        "image_path": node.image_path,
        "image_semantic_type": node.relationships.get("image_semantic_type"),
        "parent_image_node_id": node.relationships.get("parent_image_node_id"),
        "caption_node_id": node.relationships.get("caption_node_id"),
        "related_image_node_ids": node.relationships.get("related_image_node_ids"),
        "image_semantic_node_ids": node.relationships.get("image_semantic_node_ids"),
        "mineru_image_role": node.relationships.get("mineru_image_role"),
        "mineru_generated_semantic": node.relationships.get("mineru_generated_semantic"),
        "details_summary": node.relationships.get("details_summary"),
        "source_parser": node.relationships.get("source_parser"),
        "confidence": node.relationships.get("confidence"),
        "caption": node.relationships.get("caption"),
        "ocr_text": node.relationships.get("ocr_text"),
        "object_label": node.relationships.get("object_label"),
        "object_description": node.relationships.get("object_description"),
        "table_markdown": node.table_markdown,
        "formula_latex": node.formula_latex,
        "relationships": node.relationships,
        "node_id": node.node_id,
        "modality": node.modality,
        "page": md.page,
        "doc_id": md.doc_id,
        "section_path": [x for x in [md.section] if x],
        "source": md.source,
        "chunk_index": md.chunk_index,
        "title": md.title,
        "section": md.section,
        "parser_name": md.parser_name,
        "bbox": md.bbox,
        "bbox_items": md.bbox_items,
        "pages": md.pages,
        "bbox_by_page": md.bbox_by_page,
        "page_spans": md.page_spans,
        "bbox_coordinate_system": md.bbox_coordinate_system,
        "bbox_source": md.bbox_source,
        "bbox_merge_policy": md.bbox_merge_policy,
        "metadata": md.model_dump(),
    }
    for key in (
        "image_semantic_type",
        "parent_image_node_id",
        "caption_node_id",
        "related_image_node_ids",
        "image_semantic_node_ids",
        "mineru_image_role",
        "mineru_generated_semantic",
        "details_summary",
        "source_parser",
        "confidence",
        "caption",
        "ocr_text",
        "object_label",
        "object_description",
        "bbox",
        "bbox_items",
        "pages",
        "bbox_by_page",
        "page_spans",
        "bbox_coordinate_system",
        "bbox_source",
        "bbox_merge_policy",
    ):
        if payload.get(key) is not None:
            payload["metadata"][key] = payload[key]
    vector_name = _vector_name_for_node(node, settings)
    return {
        "node_id": node.node_id,
        "point_id": str(uuid.uuid5(uuid.NAMESPACE_URL, node.node_id)),
        "vector_names": [vector_name],
        "vector_dims": {vector_name: vector_dim if vector_dim is not None else settings.embedding_dimensions},
        "payload": payload,
    }


def build_retrieval_index_preview(nodes: list[Node], *, sample_size: int = 5) -> dict[str, Any]:
    hits = hits_from_nodes(nodes)
    page_hits = build_page_hits(hits)
    table_hits = build_table_hits(hits)
    pages = sorted({hit.page for hit in hits if hit.page is not None})
    return {
        "bm25": {
            "count": len(hits),
            "sample_hits": [_serialize_hit(hit) for hit in hits[:sample_size]],
        },
        "page": {
            "count": len(page_hits),
            "pages": pages,
            "sample_hits": [_serialize_hit(hit) for hit in page_hits[:sample_size]],
        },
        "table": {
            "count": len(table_hits),
            "sample_hits": [_serialize_hit(hit) for hit in table_hits[:sample_size]],
        },
    }


def build_block_map(source: str, structured_content: list[Any], nodes: list[Node]) -> dict[str, Any]:
    node_lookup: dict[tuple[int | None, str], list[str]] = defaultdict(list)
    for node in nodes:
        node_lookup[(node.metadata.page, node.modality)].append(node.node_id)
    blocks = []
    for idx, item in enumerate(_iter_structured_dicts(structured_content)):
        block_type = _normalize_structured_item_type(item)
        page = _extract_page(item)
        node_ids = node_lookup.get((page, block_type), [])
        if not node_ids and block_type == "heading":
            node_ids = node_lookup.get((page, "text"), [])
        blocks.append(
            {
                "block_id": f"mineru:block:{idx}",
                "page": page,
                "type": block_type,
                "bbox": _extract_bbox(item),
                "node_ids": node_ids,
                "has_node": bool(node_ids),
            }
        )
    return {"source": source, "blocks": blocks}


class IngestionInspectionRecorder:
    """Write inspect-only ingestion visualization artifacts."""

    def __init__(
        self,
        *,
        settings: Settings,
        output_dir: str | Path,
        run_id: str | None = None,
        max_text_chars: int | None = None,
    ):
        self.settings = settings
        self.output_root = Path(output_dir)
        self.run_id = run_id or build_run_id()
        self.run_dir = self.output_root / "runs" / self.run_id
        self.max_text_chars = max_text_chars or settings.ingestion_inspect_max_text_chars
        self.mineru_raw_dir = self.run_dir / "mineru_raw"
        self.pages_dir = self.run_dir / "pages"
        self.previews_dir = self.run_dir / "previews"

    def write_report(
        self,
        *,
        input_path: str | Path,
        result: MultimodalIngestionResult,
        parser: str,
        render_pages: bool,
        mineru_raw: dict[str, Any] | None = None,
    ) -> Path:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.previews_dir.mkdir(parents=True, exist_ok=True)
        nodes = result.nodes
        warnings: list[str] = []
        rendered_pages = []
        pymupdf_available = None
        path = Path(input_path)
        if render_pages and path.suffix.lower() == ".pdf":
            rendered_pages, pymupdf_available, render_warnings = render_pdf_pages_with_pymupdf(path, self.pages_dir)
            warnings.extend(render_warnings)
        elif render_pages and path.suffix.lower() != ".pdf":
            warnings.append("page rendering is only supported for PDF; skipped")
            pymupdf_available = None

        raw_structured = []
        if mineru_raw:
            raw_structured = mineru_raw.get("structured_content") or []
            if self.settings.ingestion_inspect_include_mineru_raw:
                save_mineru_raw_artifacts(self.mineru_raw_dir, mineru_raw)

        serialized_nodes = [serialize_node(node, max_text_chars=self.max_text_chars) for node in nodes]
        _write_jsonl(self.run_dir / "nodes.jsonl", serialized_nodes)
        _write_json(self.run_dir / "nodes.pretty.json", serialized_nodes)

        embedding_preview = [
            serialize_embedding_preview(node, settings=self.settings, max_text_chars=self.max_text_chars)
            for node in nodes
        ]
        if self.settings.ingestion_inspect_include_embedding_preview:
            _write_jsonl(self.previews_dir / "embedding_preview.jsonl", embedding_preview)
        qdrant_preview = [build_qdrant_payload_preview(node, settings=self.settings) for node in nodes]
        _write_jsonl(self.previews_dir / "qdrant_payload_preview.jsonl", qdrant_preview)
        retrieval_preview = build_retrieval_index_preview(nodes)
        _write_json(self.previews_dir / "retrieval_index_preview.json", retrieval_preview)
        block_map = build_block_map(str(input_path), raw_structured, nodes)
        _write_json(self.previews_dir / "block_map.json", block_map)

        render_chunks_html(self.run_dir / "chunks.html", serialized_nodes)
        render_document_map_html(
            self.run_dir / "document_map.html",
            nodes=serialized_nodes,
            rendered_pages=rendered_pages,
            input_path=path,
            block_map=block_map,
        )

        modality_counts = Counter(node.modality for node in nodes)
        manifest = {
            "run_id": self.run_id,
            "mode": "inspect_only",
            "input_path": str(input_path),
            "write_qdrant_requested": False,
            "wrote_qdrant": False,
            "write_local_index_requested": False,
            "wrote_local_index": False,
            "recreate_collection_requested": False,
            "recreated_collection": False,
            "parser": parser,
            "render_pages": render_pages,
            "pymupdf_available": pymupdf_available,
            "node_count": len(nodes),
            "modality_counts": dict(modality_counts),
            "failed_files": len(result.failures),
            "failures": [failure.model_dump() for failure in result.failures],
            "warnings": warnings,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "artifacts": {
                "nodes_jsonl": "nodes.jsonl",
                "nodes_pretty": "nodes.pretty.json",
                "chunks_html": "chunks.html",
                "document_map_html": "document_map.html",
                "mineru_raw_dir": "mineru_raw" if mineru_raw and self.settings.ingestion_inspect_include_mineru_raw else None,
                "mineru_raw_bbox_summary": "mineru_raw/bbox_summary.json"
                if mineru_raw and self.settings.ingestion_inspect_include_mineru_raw
                else None,
                "pages_dir": "pages" if rendered_pages else None,
                "previews_dir": "previews",
            },
        }
        _write_json(self.run_dir / "manifest.json", manifest)
        return self.run_dir

    def update_write_status(
        self,
        *,
        write_qdrant_requested: bool,
        wrote_qdrant: bool,
        write_local_index_requested: bool,
        wrote_local_index: bool,
        recreate_collection_requested: bool,
        recreated_collection: bool,
        qdrant_collection: str | None = None,
        embedded_count: int | None = None,
        upserted_point_count: int | None = None,
        build_index_log_mode: str | None = None,
        ingestion_error: str | None = None,
    ) -> None:
        manifest_path = self.run_dir / "manifest.json"
        manifest = _read_json(manifest_path)
        manifest.update(
            {
                "mode": "inspect_then_build_index" if write_qdrant_requested else "inspect_only",
                "write_qdrant_requested": write_qdrant_requested,
                "wrote_qdrant": wrote_qdrant,
                "write_local_index_requested": write_local_index_requested,
                "wrote_local_index": wrote_local_index,
                "recreate_collection_requested": recreate_collection_requested,
                "recreated_collection": recreated_collection,
            }
        )
        if qdrant_collection is not None:
            manifest["qdrant_collection"] = qdrant_collection
        if embedded_count is not None:
            manifest["embedded_count"] = embedded_count
        if upserted_point_count is not None:
            manifest["upserted_point_count"] = upserted_point_count
        if build_index_log_mode is not None:
            manifest["build_index_log_mode"] = build_index_log_mode
        if ingestion_error:
            manifest["ingestion_error"] = ingestion_error
            warnings = manifest.get("warnings", [])
            if not isinstance(warnings, list):
                warnings = []
            warnings.append("inspect artifacts were generated before build_index write failed")
            manifest["warnings"] = warnings
        _write_json(manifest_path, manifest)

    def write_build_index_results(self, build_result: Any) -> None:
        summary = build_result.summary
        summary_data = _object_to_dict(summary)
        build_result_data = _object_to_dict(build_result)
        build_result_data["summary"] = summary_data
        _write_json(self.previews_dir / "build_index_summary.json", build_result_data)
        _write_json(
            self.previews_dir / "qdrant_write_result.json",
            {
                "wrote_qdrant": build_result.wrote_qdrant,
                "qdrant_collection": build_result.qdrant_collection,
                "upserted_point_count": build_result.upserted_point_count,
                "embedded_count": build_result.embedded_count,
                "recreated_collection": build_result.recreated_collection,
            },
        )
        _write_json(
            self.previews_dir / "local_index_write_result.json",
            {
                "wrote_local_index": build_result.wrote_local_index,
            },
        )


def save_mineru_raw_artifacts(output_dir: Path, mineru_raw: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "raw_markdown.md").write_text(str(mineru_raw.get("raw_markdown") or ""), encoding="utf-8")
    (output_dir / "parsed_markdown.md").write_text(str(mineru_raw.get("parsed_markdown") or ""), encoding="utf-8")
    _write_json(output_dir / "structured_content.json", mineru_raw.get("structured_content") or [])
    _write_json(output_dir / "raw_result_manifest.json", mineru_raw.get("raw_result_manifest") or {})
    _write_mineru_assets(output_dir, mineru_raw.get("assets") or {})

    structured_content = mineru_raw.get("structured_content") or []
    bbox_rows, bbox_summary = build_mineru_bbox_diagnostics(structured_content)
    _write_jsonl(output_dir / "structured_bbox_blocks.jsonl", bbox_rows)
    _write_json(output_dir / "bbox_summary.json", bbox_summary)


def _write_mineru_assets(output_dir: Path, assets: Any) -> None:
    if not isinstance(assets, dict):
        return
    for raw_name, content in assets.items():
        if not isinstance(content, bytes):
            continue
        safe_parts = _safe_asset_parts(str(raw_name))
        if not safe_parts:
            continue
        path = output_dir.joinpath(*safe_parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _safe_asset_parts(name: str) -> list[str]:
    normalized = name.replace("\\", "/").strip("/")
    if not normalized:
        return []
    parts = [part for part in normalized.split("/") if part and part not in {".", ".."}]
    if not parts:
        return []
    return parts


def build_mineru_bbox_diagnostics(structured_content: list[Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    type_counts: dict[str, Counter[str]] = defaultdict(Counter)
    page_bbox_counts: Counter[str] = Counter()
    coord_counts: Counter[str] = Counter()

    for idx, (item, inherited_page, inherited_source_kind) in enumerate(
        _iter_structured_dicts_with_context(structured_content)
    ):
        if not isinstance(item, dict):
            continue
        block_type = _normalize_structured_item_type(item)
        raw_type = _raw_block_type(item)
        source_kind = inherited_source_kind or _infer_structured_source_kind(item, idx)
        page = _extract_page(item)
        if page is None:
            page = inherited_page
        bbox = _extract_bbox(item)
        text = _extract_structured_text(item, block_type)
        row = {
            "block_index": idx,
            "type": block_type,
            "raw_type": raw_type,
            "source_kind": source_kind,
            "page": page,
            "has_bbox": bbox is not None,
            "bbox": bbox,
            "bbox_coordinate_system": _bbox_coordinate_system_guess(bbox, source_kind),
            "text_excerpt": _truncate(text, 300),
            "text_chars": len(text or ""),
            "keys": sorted(str(key) for key in item.keys()),
        }
        rows.append(row)

        type_counts[block_type]["total"] += 1
        if bbox is not None:
            type_counts[block_type]["with_bbox"] += 1
            page_bbox_counts[str(page)] += 1
            coord_counts[row["bbox_coordinate_system"]] += 1
        else:
            type_counts[block_type]["without_bbox"] += 1

    summary = {
        "total_blocks": len(rows),
        "blocks_with_bbox": sum(1 for row in rows if row["has_bbox"]),
        "blocks_without_bbox": sum(1 for row in rows if not row["has_bbox"]),
        "by_type": {key: dict(value) for key, value in type_counts.items()},
        "bbox_by_page": dict(page_bbox_counts),
        "bbox_coordinate_system_counts": dict(coord_counts),
        "sample_with_bbox": [row for row in rows if row["has_bbox"]][:20],
        "sample_without_bbox": [row for row in rows if not row["has_bbox"]][:20],
    }
    return rows, summary


def _bbox_coordinate_system_guess(bbox: list[float] | None, source_kind: str | None) -> str | None:
    if bbox is None:
        return None
    coords = [abs(float(value)) for value in bbox[:4]]
    if coords and max(coords) <= 1.5:
        return "mineru_relative_0_1"
    if source_kind in {"content_list", "content_list_v2"}:
        return "mineru_content_list_raw"
    if source_kind == "model":
        return "mineru_model_raw"
    if source_kind == "layout":
        return "mineru_layout_raw"
    return "mineru_raw"


def _extract_structured_text(item: dict[str, Any], block_type: str) -> str:
    list_text = _extract_list_items_text(item)
    if list_text:
        return list_text

    if block_type == "table":
        candidates: list[Any] = [item.get("table_body"), item.get("html"), item.get("markdown"), item.get("md"), item.get("text"), item.get("content")]
        content = item.get("content")
        if isinstance(content, dict):
            candidates.extend(content.get(key) for key in ("table_body", "html", "markdown", "md", "text", "content"))
        for value in candidates:
            if isinstance(value, str) and value.strip():
                return value.strip()
    for key in ("text", "content", "md", "markdown", "latex", "formula", "caption", "title"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    content = item.get("content")
    if isinstance(content, dict):
        return _extract_structured_text(content, block_type)
    return ""


def _extract_list_items_text(item: dict[str, Any]) -> str:
    values: list[Any] = []
    list_items = item.get("list_items")
    if isinstance(list_items, list):
        values.extend(list_items)
    content = item.get("content")
    if isinstance(content, dict):
        nested = content.get("list_items")
        if isinstance(nested, list):
            values.extend(nested)
    lines = [_stringify_list_item(value).strip() for value in values]
    return "\n".join(line for line in lines if line)


def _stringify_list_item(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts: list[str] = []
        for key in ("text", "content"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
        item_content = value.get("item_content")
        if isinstance(item_content, list):
            parts.extend(_stringify_list_item(item).strip() for item in item_content)
        if parts:
            return " ".join(part for part in parts if part)
    if isinstance(value, list):
        parts = [_stringify_list_item(item).strip() for item in value]
        return " ".join(part for part in parts if part)
    return ""


def render_pdf_pages_with_pymupdf(pdf_path: Path, output_dir: Path) -> tuple[list[dict[str, Any]], bool, list[str]]:
    warnings: list[str] = []
    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError:
        return [], False, ["PyMuPDF is not installed; install with: pip install pymupdf"]

    output_dir.mkdir(parents=True, exist_ok=True)
    rendered = []
    try:
        doc = fitz.open(str(pdf_path))
        for index, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
            filename = f"page_{index:03d}.png"
            out_path = output_dir / filename
            pix.save(str(out_path))
            rendered.append({"page": index, "path": f"pages/{filename}"})
        doc.close()
    except Exception as exc:
        warnings.append(f"PDF page rendering failed: {type(exc).__name__}: {exc}")
    return rendered, True, warnings


def render_chunks_html(path: Path, nodes: list[dict[str, Any]]) -> None:
    cards = []
    for node in nodes:
        md = node.get("metadata") or {}
        rel = node.get("relationships") or {}
        modality = str(node.get("modality") or "text")
        content = node.get("table_markdown") or node.get("formula_latex") or node.get("text") or node.get("image_path") or ""
        warnings = []
        if md.get("page") is None:
            warnings.append("page=null")
        if md.get("section") is None:
            warnings.append("section=null")
        if not rel:
            warnings.append("relationships={}")
        cards.append(
            f"""
            <article class="node {html.escape(modality)}">
              <div class="meta">
                <strong>{html.escape(modality)}</strong>
                <span>{html.escape(str(node.get("node_id") or ""))}</span>
                <span>page={html.escape(str(md.get("page")))}</span>
                <span>chunk={html.escape(str(md.get("chunk_index")))}</span>
                <span>section={html.escape(str(md.get("section")))}</span>
                <span>parser={html.escape(str(md.get("parser_name")))}</span>
                <span>image_role={html.escape(str(rel.get("mineru_image_role")))}</span>
                <span>image_semantic_type={html.escape(str(rel.get("image_semantic_type")))}</span>
                <span>parent_image_node_id={html.escape(str(rel.get("parent_image_node_id")))}</span>
                <span>caption_node_id={html.escape(str(rel.get("caption_node_id")))}</span>
                <span>caption_for={html.escape(str(rel.get("mineru_caption_for_image_node_id")))}</span>
                <span>generated_semantic={html.escape(str(rel.get("mineru_generated_semantic")))}</span>
                <span>image_path={html.escape(str(node.get("image_path") or rel.get("image_path") or rel.get("mineru_image_path")))}</span>
                <span>bbox={html.escape(str(md.get("bbox")))}</span>
                <span>bbox_items={html.escape(str(len(md.get("bbox_items") or [])))}</span>
                <span>bbox_system={html.escape(str(md.get("bbox_coordinate_system")))}</span>
              </div>
              <div class="note">details_summary={html.escape(str(rel.get("details_summary") or ""))}</div>
              <div class="warnings">{html.escape(", ".join(warnings))}</div>
              <pre>{html.escape(str(content))}</pre>
              <details><summary>Node JSON</summary><pre>{html.escape(json.dumps(node, ensure_ascii=False, indent=2))}</pre></details>
            </article>
            """
        )
    path.write_text(_html_page("Ingestion Chunks", "\n".join(cards)), encoding="utf-8")


def render_document_map_html(
    path: Path,
    *,
    nodes: list[dict[str, Any]],
    rendered_pages: list[dict[str, Any]],
    input_path: Path,
    block_map: dict[str, Any],
) -> None:
    nodes_by_page: dict[int | None, list[dict[str, Any]]] = defaultdict(list)
    for node in nodes:
        page = (node.get("metadata") or {}).get("page")
        nodes_by_page[page if isinstance(page, int) else None].append(node)

    sections = []
    if rendered_pages:
        for page_info in rendered_pages:
            page = page_info["page"]
            sections.append(
                f"""
                <section class="page-row">
                  <div class="page-image"><img src="{html.escape(page_info["path"])}" alt="page {page}"></div>
                  <div class="page-nodes">
                    <h2>Page {page}</h2>
                    {_render_node_list(nodes_by_page.get(page, []))}
                  </div>
                </section>
                """
            )
    else:
        sections.append(
            f"""
            <section>
              <h2>{html.escape(input_path.name)}</h2>
              <p class="note">No page screenshot is rendered. PDF rendering requires PyMuPDF; DOCX is shown as text flow.</p>
              {_render_node_list(nodes)}
            </section>
            """
        )
    if nodes_by_page.get(None):
        sections.append(f"<section><h2>Page unknown</h2>{_render_node_list(nodes_by_page[None])}</section>")
    sections.append(
        f"<section><h2>MinerU block map</h2><pre>{html.escape(json.dumps(block_map, ensure_ascii=False, indent=2))}</pre></section>"
    )
    body = "\n".join(sections)
    body += '<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>'
    path.write_text(_html_page("Document Map", body), encoding="utf-8")


def inspection_result_for_path(path: Path, result: MultimodalIngestionResult) -> MultimodalIngestionResult:
    if path.is_file():
        return result
    return result


def collect_input_files(path: str | Path) -> list[Path]:
    p = Path(path)
    if p.is_file():
        return [p]
    if p.is_dir():
        return [item for item in sorted(p.rglob("*")) if item.is_file()]
    raise IngestionInspectionError(f"input path does not exist: {p}")


def _vector_name_for_node(node: Node, settings: Settings) -> str:
    if not settings.enable_named_vectors:
        return "default"
    if node.modality == "table":
        return settings.named_vector_table_name
    if node.modality == "image":
        return settings.named_vector_image_name
    return settings.named_vector_text_name


def _serialize_hit(hit) -> dict[str, Any]:
    return {
        "point_id": hit.point_id,
        "node_id": hit.node_id,
        "doc_id": hit.doc_id,
        "page": hit.page,
        "modality": hit.modality,
        "source": hit.metadata.get("source"),
        "chunk_index": hit.metadata.get("chunk_index"),
        "text_excerpt": _truncate(hit.text, 500),
    }


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _object_to_dict(value: Any) -> dict[str, Any]:
    from dataclasses import asdict, is_dataclass

    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        data = value.model_dump()
        return data if isinstance(data, dict) else {}
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    if isinstance(value, dict):
        return dict(value)
    return {}


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _truncate(text: str | None, max_chars: int) -> str:
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + f"\n...[truncated {len(value) - max_chars} chars]"


def _html_page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ --ink:#1d252c; --muted:#667085; --bg:#f6f2ea; --card:#fffaf0; --text:#d8ebff; --table:#e1f7df; --formula:#fff1b8; --image:#ffe0d4; }}
    body {{ margin:0; font-family: Georgia, 'Times New Roman', serif; color:var(--ink); background:linear-gradient(135deg,#f6f2ea,#e8f0ee); }}
    header {{ padding:28px 36px; background:#263238; color:#fff; }}
    main {{ padding:24px; max-width:1280px; margin:0 auto; }}
    .node {{ border:1px solid rgba(0,0,0,.12); border-radius:14px; margin:16px 0; padding:16px; background:var(--card); box-shadow:0 8px 24px rgba(0,0,0,.06); }}
    .node.text {{ border-left:8px solid #5aa9e6; }}
    .node.table {{ border-left:8px solid #5cb85c; }}
    .node.formula {{ border-left:8px solid #f0ad4e; }}
    .node.image {{ border-left:8px solid #ff7f50; }}
    .meta {{ display:flex; gap:10px; flex-wrap:wrap; color:var(--muted); font-size:13px; }}
    .warnings {{ color:#b42318; margin-top:8px; font-weight:600; }}
    pre {{ white-space:pre-wrap; word-break:break-word; background:rgba(255,255,255,.65); padding:12px; border-radius:10px; overflow:auto; }}
    .page-row {{ display:grid; grid-template-columns:minmax(280px, 42%) 1fr; gap:18px; align-items:start; margin-bottom:28px; }}
    .page-image img {{ max-width:100%; border-radius:12px; box-shadow:0 8px 20px rgba(0,0,0,.18); background:#fff; }}
    .note {{ color:var(--muted); }}
    @media (max-width: 860px) {{ .page-row {{ grid-template-columns:1fr; }} header {{ padding:22px; }} main {{ padding:14px; }} }}
  </style>
</head>
<body>
  <header><h1>{html.escape(title)}</h1><p>Inspect-only ingestion visualization. No Qdrant writes.</p></header>
  <main>{body}</main>
</body>
</html>
"""


def _render_node_list(nodes: list[dict[str, Any]]) -> str:
    if not nodes:
        return '<p class="note">No nodes for this page.</p>'
    items = []
    for node in nodes:
        md = node.get("metadata") or {}
        modality = str(node.get("modality") or "text")
        content = node.get("table_markdown") or node.get("formula_latex") or node.get("text") or node.get("image_path") or ""
        items.append(
            f"""
            <article class="node {html.escape(modality)}">
              <div class="meta"><strong>{html.escape(modality)}</strong><span>{html.escape(str(node.get("node_id") or ""))}</span><span>chunk={html.escape(str(md.get("chunk_index")))}</span><span>page={html.escape(str(md.get("page")))}</span><span>pages={html.escape(str(md.get("pages")))}</span><span>bbox={html.escape(str(md.get("bbox")))}</span><span>bbox_items={html.escape(str(len(md.get("bbox_items") or [])))}</span><span>page_spans={html.escape(str(len(md.get("page_spans") or [])))}</span><span>bbox_by_page={html.escape(str(sorted((md.get("bbox_by_page") or {}).keys()) if isinstance(md.get("bbox_by_page"), dict) else None))}</span></div>
              <pre>{html.escape(str(content))}</pre>
            </article>
            """
        )
    return "\n".join(items)


def _iter_structured_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_structured_dicts(child)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_structured_dicts(item)


def _iter_structured_dicts_with_context(
    value: Any,
    *,
    inherited_page: int | None = None,
    inherited_source_kind: str | None = None,
):
    if isinstance(value, dict):
        source_kind = _source_kind_from_name(value.get("source") or value.get("filename") or value.get("file_name") or value.get("name"))
        if source_kind is None:
            source_kind = inherited_source_kind
        if source_kind is None:
            source_kind = _infer_structured_source_kind(value, 0)

        page = _extract_page(value)
        if page is None:
            page = inherited_page
        yield value, page, source_kind

        for key, child in value.items():
            if isinstance(child, (list, dict)):
                child_source_kind = "nested_content" if key == "content" and isinstance(child, (list, dict)) else source_kind
                yield from _iter_structured_dicts_with_context(
                    child,
                    inherited_page=page,
                    inherited_source_kind=child_source_kind,
                )
    elif isinstance(value, list):
        for item in value:
            yield from _iter_structured_dicts_with_context(
                item,
                inherited_page=inherited_page,
                inherited_source_kind=inherited_source_kind,
            )


def _normalize_block_type(value: Any) -> str:
    raw = str(value or "text").strip().lower()
    if "table" in raw:
        return "table"
    if "formula" in raw or "equation" in raw:
        return "formula"
    if "title" in raw or "heading" in raw:
        return "heading"
    if "image" in raw or "figure" in raw:
        return "image"
    return "text"


def _normalize_structured_item_type(item: dict[str, Any]) -> str:
    block_type = _normalize_block_type(item.get("type") or item.get("category") or item.get("role"))
    if block_type == "text" and _is_text_level_heading(item):
        return "heading"
    return block_type


def _is_text_level_heading(item: dict[str, Any]) -> bool:
    value = item.get("text_level")
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return int(value) > 0
    if isinstance(value, str):
        stripped = value.strip()
        return stripped.isdigit() and int(stripped) > 0
    return False


def _raw_block_type(item: dict[str, Any]) -> str | None:
    for key in ("type", "block_type", "layout_type", "category", "kind", "sub_type", "role"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    content = item.get("content")
    if isinstance(content, dict):
        table_type = content.get("table_type")
        if isinstance(table_type, str) and table_type.strip():
            return table_type.strip().lower()
        if any(isinstance(content.get(key), str) and content.get(key).strip() for key in ("html", "table_body")):
            return "table"
    if any(isinstance(item.get(key), str) and item.get(key).strip() for key in ("table_body", "html")):
        return "table"
    return None


def _infer_structured_source_kind(item: Any, idx: int) -> str:
    if isinstance(item, dict):
        source = item.get("source") or item.get("filename") or item.get("file_name") or item.get("name")
        source_kind = _source_kind_from_name(source)
        if source_kind:
            return source_kind

        keys = {str(key).lower() for key in item}
        if "content_list_v2" in keys:
            return "content_list_v2"
        if "content_list" in keys:
            return "content_list"
        if "layout" in keys:
            return "layout"
        if "model" in keys or "middle" in keys or "pages" in keys:
            return "model"
        if {"type", "table_body"} & keys or {"type", "page_idx"} <= keys:
            return "content_list"
        content = item.get("content")
        if isinstance(content, dict) and {"html", "table_type"} & {str(key).lower() for key in content}:
            return "model"
    if isinstance(item, list):
        sample = [sub for sub in item[:5] if isinstance(sub, dict)]
        if sample and any("table_body" in sub or "page_idx" in sub for sub in sample):
            return "content_list"
    return f"structured_json_{idx}"


def _source_kind_from_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.replace("\\", "/").lower()
    if "content_list_v2" in name:
        return "content_list_v2"
    if "content_list" in name:
        return "content_list"
    if "layout" in name:
        return "layout"
    if "middle" in name or "model" in name:
        return "model"
    return None


def _extract_page(item: dict[str, Any]) -> int | None:
    for key in ("page", "page_no", "page_number", "page_num", "page_idx", "page_index", "page_id"):
        value = item.get(key)
        zero_based = key in {"page_idx", "page_index"}
        page = _coerce_page(value, zero_based=zero_based)
        if page is not None:
            return page
    metadata = item.get("metadata") or item.get("meta")
    if isinstance(metadata, dict):
        return _extract_page(metadata)
    return None


def _coerce_page(value: Any, *, zero_based: bool) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value + 1 if zero_based and value >= 0 else value
    if isinstance(value, float) and value.is_integer():
        page = int(value)
        return page + 1 if zero_based and page >= 0 else page
    if isinstance(value, str):
        digits = "".join(ch for ch in value if ch.isdigit())
        if digits:
            page = int(digits)
            return page + 1 if zero_based and page >= 0 else page
    return None


def _extract_bbox(item: dict[str, Any]) -> list[float] | None:
    for key in ("bbox", "box", "bounding_box", "position", "coordinates"):
        bbox = _coerce_bbox(item.get(key))
        if bbox is not None:
            return bbox
    metadata = item.get("metadata") or item.get("meta")
    if isinstance(metadata, dict):
        return _extract_bbox(metadata)
    return None


def _coerce_bbox(value: Any) -> list[float] | None:
    if isinstance(value, dict):
        if all(key in value for key in ("x", "y", "width", "height")):
            x = _coerce_float(value.get("x"))
            y = _coerce_float(value.get("y"))
            width = _coerce_float(value.get("width"))
            height = _coerce_float(value.get("height"))
            if None not in (x, y, width, height):
                return [x, y, x + width, y + height]  # type: ignore[operator]
        if all(key in value for key in ("x0", "y0", "x1", "y1")):
            coords = [_coerce_float(value.get(key)) for key in ("x0", "y0", "x1", "y1")]
            if all(coord is not None for coord in coords):
                return [coord for coord in coords if coord is not None]
    if isinstance(value, list):
        if value and all(isinstance(item, list) and len(item) >= 2 for item in value):
            xs = [_coerce_float(item[0]) for item in value]
            ys = [_coerce_float(item[1]) for item in value]
            if all(coord is not None for coord in xs + ys):
                numeric_xs = [coord for coord in xs if coord is not None]
                numeric_ys = [coord for coord in ys if coord is not None]
                return [min(numeric_xs), min(numeric_ys), max(numeric_xs), max(numeric_ys)]
        numbers: list[float] = []
        for item in value:
            if isinstance(item, list):
                for sub in item:
                    number = _coerce_float(sub)
                    if number is not None:
                        numbers.append(number)
            else:
                number = _coerce_float(item)
                if number is not None:
                    numbers.append(number)
        if len(numbers) >= 4:
            return numbers[:4]
    return None


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def failure_result(source: str, error: str) -> MultimodalIngestionResult:
    return MultimodalIngestionResult(nodes=[], failures=[IngestionFailure(source=source, error=error)])


def empty_mineru_raw(source: str) -> dict[str, Any]:
    return {
        "raw_markdown": "",
        "parsed_markdown": "",
        "structured_content": [],
        "raw_result_manifest": {"source": source},
    }
