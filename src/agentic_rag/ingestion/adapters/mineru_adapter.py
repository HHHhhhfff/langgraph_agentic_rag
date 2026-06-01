from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentic_rag.config import Settings
from agentic_rag.ingestion.adapters.mineru_client import MinerUClient
from agentic_rag.ingestion.adapters.mineru_result_parser import parse_mineru_markdown
from agentic_rag.ingestion.chunk_strategies import TableChunker, build_text_chunk_strategy
from agentic_rag.ingestion.formula_extractor import extract_formulas
from agentic_rag.ingestion.node_normalizer import NodeNormalizer
from agentic_rag.ingestion.node_schema import IngestionFailure, MultimodalIngestionResult
from agentic_rag.ingestion.table_extractor import extract_table_blocks, html_table_to_markdown, strip_table_blocks
from agentic_rag.observability.stage_logger import StageLogger, StageTimer


class MinerUAdapterError(RuntimeError):
    """Raised for MinerU adapter errors."""


@dataclass(slots=True)
class MinerUStructuredBlock:
    type: str
    text: str
    page: int | None = None
    section: str | None = None
    bbox: list[float] | None = None
    source_kind: str | None = None
    raw_type: str | None = None
    structured_duplicate_count: int = 1


class MinerUAdapter:
    """Parse local files via MinerU API and normalize into text/table nodes."""

    def __init__(self, settings: Settings, stage_logger: StageLogger | None = None, run_id: str = ""):
        self.settings = settings
        self.stage_logger = stage_logger
        self.run_id = run_id
        self.client = MinerUClient(settings)
        self.normalizer = NodeNormalizer()
        self.text_strategy = build_text_chunk_strategy(settings)
        self.table_chunker = TableChunker()

    def parse_file(self, path: str | Path) -> MultimodalIngestionResult:
        file_path = Path(path)
        if not file_path.exists():
            return MultimodalIngestionResult(
                failures=[IngestionFailure(source=str(file_path), error="file not found")]
            )

        timer = StageTimer.start_now()
        if self.stage_logger:
            self.stage_logger.log_stage_start(
                "mineru_submit_task",
                source=str(file_path),
                parser="mineru",
                mineru_mode=self.settings.mineru_mode,
            )

        try:
            if self.settings.mineru_mode == "agent":
                result = self._parse_agent(file_path)
            else:
                result = self._parse_precise(file_path)
        except Exception as exc:
            if self.stage_logger:
                self.stage_logger.log_stage_error(
                    "mineru_submit_task",
                    exc,
                    source=str(file_path),
                    parser="mineru",
                )
            return MultimodalIngestionResult(
                failures=[IngestionFailure(source=str(file_path), error=str(exc))]
            )

        if self.stage_logger:
            self.stage_logger.log_stage_end(
                "mineru_submit_task",
                latency_ms=timer.elapsed_ms(),
                source=str(file_path),
                parser="mineru",
                task_id=result.task_id,
                state=result.state,
            )

        parse_timer = StageTimer.start_now()
        if self.stage_logger:
            self.stage_logger.log_stage_start(
                "mineru_parse_result",
                source=str(file_path),
                parser="mineru",
                task_id=result.task_id,
                state=result.state,
            )

        try:
            markdown = parse_mineru_markdown(result, self.settings)
            nodes = self._build_nodes_from_markdown(markdown, file_path, structured_content=result.structured_content or [])
        except Exception as exc:
            if self.stage_logger:
                self.stage_logger.log_stage_error(
                    "mineru_parse_result",
                    exc,
                    source=str(file_path),
                    parser="mineru",
                    task_id=result.task_id,
                )
            return MultimodalIngestionResult(
                failures=[IngestionFailure(source=str(file_path), error=f"MinerU parse result failed: {exc}")]
            )

        if self.stage_logger:
            self.stage_logger.log_stage_end(
                "mineru_parse_result",
                latency_ms=parse_timer.elapsed_ms(),
                source=str(file_path),
                parser="mineru",
                task_id=result.task_id,
                chunk_count=len(nodes),
            )

        return MultimodalIngestionResult(nodes=nodes, failures=[])

    def _parse_agent(self, file_path: Path):
        task_id, upload_url = self.client._agent_create_file_task(file_path)
        if self.stage_logger:
            self.stage_logger.log_stage_end(
                "mineru_submit_task",
                latency_ms=0,
                source=str(file_path),
                parser="mineru",
                task_id=task_id,
                state="submitted",
            )
            self.stage_logger.log_stage_start(
                "mineru_upload_file",
                source=str(file_path),
                parser="mineru",
                task_id=task_id,
            )
        upload_timer = StageTimer.start_now()
        self.client._upload_file(upload_url, file_path)
        if self.stage_logger:
            self.stage_logger.log_stage_end(
                "mineru_upload_file",
                latency_ms=upload_timer.elapsed_ms(),
                source=str(file_path),
                parser="mineru",
                task_id=task_id,
                state="uploaded",
            )
            self.stage_logger.log_stage_start(
                "mineru_poll_status",
                source=str(file_path),
                parser="mineru",
                task_id=task_id,
            )
        poll_timer = StageTimer.start_now()
        final = self.client._poll_agent_task(task_id)
        if self.stage_logger:
            self.stage_logger.log_stage_end(
                "mineru_poll_status",
                latency_ms=poll_timer.elapsed_ms(),
                source=str(file_path),
                parser="mineru",
                task_id=task_id,
                state=str((final.get("data") or {}).get("state") or ""),
            )
            self.stage_logger.log_stage_start(
                "mineru_download_result",
                source=str(file_path),
                parser="mineru",
                task_id=task_id,
            )
        download_timer = StageTimer.start_now()
        result = self.client._agent_download_markdown(task_id, final)
        if self.stage_logger:
            self.stage_logger.log_stage_end(
                "mineru_download_result",
                latency_ms=download_timer.elapsed_ms(),
                source=str(file_path),
                parser="mineru",
                task_id=task_id,
                state=result.state,
            )
        return result

    def _parse_precise(self, file_path: Path):
        task_id, batch_id, upload_url = self.client._precise_create_upload_task(file_path)
        if self.stage_logger:
            self.stage_logger.log_stage_end(
                "mineru_submit_task",
                latency_ms=0,
                source=str(file_path),
                parser="mineru",
                task_id=task_id,
                batch_id=batch_id,
                state="submitted",
            )
            self.stage_logger.log_stage_start(
                "mineru_upload_file",
                source=str(file_path),
                parser="mineru",
                task_id=task_id,
                batch_id=batch_id,
            )
        upload_timer = StageTimer.start_now()
        self.client._upload_file(upload_url, file_path)
        if self.stage_logger:
            self.stage_logger.log_stage_end(
                "mineru_upload_file",
                latency_ms=upload_timer.elapsed_ms(),
                source=str(file_path),
                parser="mineru",
                task_id=task_id,
                batch_id=batch_id,
                state="uploaded",
            )
            self.stage_logger.log_stage_start(
                "mineru_poll_status",
                source=str(file_path),
                parser="mineru",
                task_id=task_id,
                batch_id=batch_id,
            )
        poll_timer = StageTimer.start_now()
        final = self.client._poll_precise_batch(batch_id, file_path.name)
        if self.stage_logger:
            self.stage_logger.log_stage_end(
                "mineru_poll_status",
                latency_ms=poll_timer.elapsed_ms(),
                source=str(file_path),
                parser="mineru",
                task_id=task_id,
                batch_id=batch_id,
                state=self.client._extract_precise_state(final, file_path.name),
            )
            self.stage_logger.log_stage_start(
                "mineru_download_result",
                source=str(file_path),
                parser="mineru",
                task_id=task_id,
                batch_id=batch_id,
            )
        download_timer = StageTimer.start_now()
        result = self.client._precise_download_result(task_id, final)
        if self.stage_logger:
            self.stage_logger.log_stage_end(
                "mineru_download_result",
                latency_ms=download_timer.elapsed_ms(),
                source=str(file_path),
                parser="mineru",
                task_id=task_id,
                batch_id=batch_id,
                state=result.state,
            )
        return result

    def _build_nodes_from_markdown(self, markdown: str, file_path: Path, structured_content: list[Any] | None = None):
        structured_content = structured_content or []
        nodes = []
        idx = 0
        text_count = 0
        table_count = 0
        formula_count = 0

        structured_blocks = _extract_structured_blocks(structured_content)
        table_blocks_with_metadata = [block for block in structured_blocks if block.type == "table"]
        if self.settings.mineru_structured_table_coalesce_enabled:
            table_blocks_with_metadata = _coalesce_structured_table_blocks(
                table_blocks_with_metadata,
                keep_chart=self.settings.mineru_structured_table_coalesce_keep_chart,
            )
        table_blocks = extract_table_blocks(markdown)
        covered_table_fingerprints: set[str] = set()

        if self.settings.mineru_table_structured_first:
            table_source_blocks = table_blocks_with_metadata
        else:
            table_source_blocks = []

        for table_block in table_source_blocks:
            table_md = table_block.text
            _add_table_fingerprint(covered_table_fingerprints, table_md)
            for table_chunk in self.table_chunker.chunk_markdown_table(table_md):
                _add_table_fingerprint(covered_table_fingerprints, table_chunk)
                nodes.append(
                    self.normalizer.normalize(
                        source=str(file_path),
                        parser_name="mineru",
                        chunk_index=idx,
                        modality="table",
                        text=table_chunk,
                        table_markdown=table_chunk,
                        page=table_block.page,
                        title=file_path.stem,
                        section=table_block.section,
                        **_mineru_bbox_payload([table_block]),
                        relationships=_mineru_relationships(table_block),
                    )
                )
                idx += 1
                table_count += 1

        if self.settings.mineru_table_markdown_fallback:
            markdown_table_blocks = table_blocks
        else:
            markdown_table_blocks = []

        for block in markdown_table_blocks:
            compact_markdown = _compact_text(block.markdown)
            if not compact_markdown or _table_fingerprint(block.markdown) in covered_table_fingerprints:
                continue
            _add_table_fingerprint(covered_table_fingerprints, block.markdown)
            for table_chunk in self.table_chunker.chunk_markdown_table(block.markdown):
                if _table_fingerprint(table_chunk) in covered_table_fingerprints and _table_fingerprint(table_chunk) != _table_fingerprint(block.markdown):
                    continue
                _add_table_fingerprint(covered_table_fingerprints, table_chunk)
                matched_block = _infer_block_for_text(table_chunk, structured_blocks) or _infer_block_for_text(
                    block.markdown, structured_blocks
                )
                page = matched_block.page if matched_block else None
                section = (matched_block.section if matched_block else None) or _section_from_markdown(block.markdown)
                nodes.append(
                    self.normalizer.normalize(
                        source=str(file_path),
                        parser_name="mineru",
                        chunk_index=idx,
                        modality="table",
                        text=table_chunk,
                        table_markdown=table_chunk,
                        page=page,
                        title=file_path.stem,
                        section=section,
                        **_mineru_bbox_payload([matched_block] if matched_block else []),
                        relationships=_mineru_relationships(
                            matched_block
                            or MinerUStructuredBlock(type="table", text=table_chunk, page=page, section=section)
                        ),
                    )
                )
                idx += 1
                table_count += 1

        clean_markdown = strip_table_blocks(markdown, table_blocks)

        def flush_text() -> None:
            nonlocal idx, text_count
            text = "\n".join(buffer).strip()
            buffer.clear()
            if not text:
                return
            for chunk in self.text_strategy.chunk_text(text):
                matched_blocks = _infer_blocks_for_text(chunk, structured_blocks)
                if not matched_blocks:
                    fallback_block = _infer_block_for_text(text, structured_blocks)
                    matched_blocks = [fallback_block] if fallback_block else []
                matched_block = _best_block(matched_blocks)
                page = matched_block.page if matched_block else None
                section = (matched_block.section if matched_block else None) or _section_from_markdown(chunk)
                nodes.append(
                    self.normalizer.normalize(
                        source=str(file_path),
                        parser_name="mineru",
                        chunk_index=idx,
                        modality="text",
                        text=chunk,
                        page=page,
                        title=file_path.stem,
                        section=section,
                        **_mineru_bbox_payload(matched_blocks),
                        relationships=_mineru_relationships(
                            matched_block or MinerUStructuredBlock(type="text", text=chunk, page=page, section=section)
                        ),
                    )
                )
                idx += 1
                text_count += 1

        buffer: list[str] = []
        i = 0
        lines = clean_markdown.splitlines()
        while i < len(lines):
            line = lines[i]
            if self.settings.mineru_table_second_pass_enabled and "|" in line:
                table_block = [line]
                j = i + 1
                while j < len(lines) and "|" in lines[j]:
                    table_block.append(lines[j])
                    j += 1
                candidate = "\n".join(table_block)
                if len([ln for ln in table_block if ln.strip()]) >= 2 and (
                    candidate.lstrip().startswith("<table") or any("---" in ln for ln in table_block)
                ):
                    if _table_fingerprint(candidate) in covered_table_fingerprints:
                        i = j
                        continue
                    flush_text()
                    for table_chunk in self.table_chunker.chunk_markdown_table(candidate):
                        if _table_fingerprint(table_chunk) in covered_table_fingerprints:
                            continue
                        _add_table_fingerprint(covered_table_fingerprints, table_chunk)
                        matched_block = _infer_block_for_text(table_chunk, structured_blocks) or _infer_block_for_text(
                            candidate, structured_blocks
                        )
                        page = matched_block.page if matched_block else None
                        section = (matched_block.section if matched_block else None) or _section_from_markdown(candidate)
                        nodes.append(
                            self.normalizer.normalize(
                                source=str(file_path),
                                parser_name="mineru",
                                chunk_index=idx,
                                modality="table",
                                text=table_chunk,
                                table_markdown=table_chunk,
                                page=page,
                                title=file_path.stem,
                                section=section,
                                **_mineru_bbox_payload([matched_block] if matched_block else []),
                                relationships=_mineru_relationships(
                                    matched_block
                                    or MinerUStructuredBlock(type="table", text=table_chunk, page=page, section=section)
                                ),
                            )
                        )
                        idx += 1
                        table_count += 1
                    i = j
                    continue
            buffer.append(line)
            i += 1

        flush_text()

        if self.settings.enable_formula_recognition:
            formula_source = strip_table_blocks(markdown, table_blocks)
            for formula in extract_formulas(
                formula_source,
                min_chars=self.settings.formula_node_min_chars,
                inline_as_text_only=self.settings.formula_inline_as_text_only,
                skip_inline_references=self.settings.formula_skip_inline_references,
                skip_superscript_notes=self.settings.formula_skip_superscript_notes,
                group_display=self.settings.formula_group_display_enabled,
                group_max_gap_lines=self.settings.formula_group_max_gap_lines,
            ):
                matched_block = (
                    _infer_block_for_text(formula.raw, structured_blocks)
                    or _infer_block_for_text(formula.formula_latex, structured_blocks)
                    or _infer_block_for_text(formula.text, structured_blocks)
                )
                page = matched_block.page if matched_block else None
                section = matched_block.section if matched_block else None
                relationships = _mineru_relationships(
                    matched_block
                    or MinerUStructuredBlock(type="formula", text=formula.text, page=page, section=section)
                )
                if formula.formula_count > 1:
                    relationships["formula_group"] = True
                    relationships["formula_count"] = formula.formula_count
                nodes.append(
                    self.normalizer.normalize(
                        source=str(file_path),
                        parser_name="mineru:formula",
                        chunk_index=idx,
                        modality="formula",
                        text=formula.text,
                        formula_latex=formula.formula_latex,
                        page=page,
                        title=file_path.stem,
                        section=section,
                        **_mineru_bbox_payload([matched_block] if matched_block else []),
                        relationships=relationships,
                    )
                )
                idx += 1
                formula_count += 1

        if self.settings.mineru_table_exact_content_coalesce_enabled:
            nodes = _coalesce_duplicate_table_nodes(
                nodes,
                keep_chart_separate=self.settings.mineru_table_exact_content_coalesce_keep_chart_separate,
            )
            table_count = sum(1 for node in nodes if node.modality == "table")

        if self.stage_logger:
            self.stage_logger.log_counter(
                "mineru_modality_counts",
                source=str(file_path),
                parser="mineru",
                text_count=text_count,
                table_count=table_count,
                image_count=0,
                formula_count=formula_count,
            )

        if self.settings.mineru_context_link_enabled:
            nodes = _link_mineru_nodes(
                nodes,
                context_window_chars=self.settings.mineru_context_window_chars,
                same_page_link_max_nodes=self.settings.mineru_same_page_link_max_nodes,
            )

        return nodes


def _extract_structured_blocks(structured_content: list[Any]) -> list[MinerUStructuredBlock]:
    blocks: list[MinerUStructuredBlock] = []
    for idx, item in enumerate(structured_content):
        source_kind = _infer_structured_source_kind(item, idx)
        blocks.extend(
            _walk_structured_item(
                item,
                inherited_page=None,
                inherited_section=None,
                source_kind=source_kind,
            )
        )
    return [block for block in blocks if block.text]


def _walk_structured_item(
    item: Any,
    *,
    inherited_page: int | None,
    inherited_section: str | None,
    source_kind: str | None,
) -> list[MinerUStructuredBlock]:
    if isinstance(item, list):
        blocks: list[MinerUStructuredBlock] = []
        for sub in item:
            blocks.extend(
                _walk_structured_item(
                    sub,
                    inherited_page=inherited_page,
                    inherited_section=inherited_section,
                    source_kind=source_kind,
                )
            )
        return blocks
    if not isinstance(item, dict):
        return []

    if "structured_content" in item and isinstance(item.get("structured_content"), (list, dict)):
        return _walk_structured_item(
            item.get("structured_content"),
            inherited_page=inherited_page,
            inherited_section=inherited_section,
            source_kind=_infer_structured_source_kind(item, 0),
        )

    page = _extract_page(item)
    if page is None:
        page = inherited_page
    section = _extract_section(item) or inherited_section
    raw_type = _raw_block_type(item)
    item_type = _normalize_block_type(raw_type)
    bbox = _extract_bbox(item)

    blocks: list[MinerUStructuredBlock] = []
    text = _extract_block_text(item, item_type)
    if text:
        blocks.append(
            MinerUStructuredBlock(
                type=item_type,
                text=text,
                page=page,
                section=section,
                bbox=bbox,
                source_kind=source_kind,
                raw_type=str(raw_type or item_type).strip().lower(),
            )
        )
        if item_type == "table":
            return blocks

    for key in ("children", "blocks", "content", "items", "spans", "lines", "layout"):
        value = item.get(key)
        if isinstance(value, (list, dict)):
            blocks.extend(
                _walk_structured_item(
                    value,
                    inherited_page=page,
                    inherited_section=section,
                    source_kind="nested_content" if key == "content" else source_kind,
                )
            )
    return blocks


def _extract_block_text(item: dict[str, Any], item_type: str) -> str:
    if item_type == "table":
        table_values: list[Any] = [item.get("table_body"), item.get("html"), item.get("content")]
        content = item.get("content")
        if isinstance(content, dict):
            table_values.extend(
                content.get(key)
                for key in ("table_body", "html", "markdown", "md", "text", "content")
            )
        for value in table_values:
            if not isinstance(value, str) or not value.strip():
                continue
            table_md = html_table_to_markdown(value)
            if table_md:
                return table_md
            if "|" in value:
                return value.strip()

    for key in (
        "text",
        "content",
        "md",
        "markdown",
        "table_body",
        "latex",
        "formula",
        "caption",
        "title",
    ):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_page(item: dict[str, Any]) -> int | None:
    for key in (
        "page",
        "page_no",
        "page_number",
        "page_num",
        "page_idx",
        "page_index",
        "page_id",
    ):
        page = _coerce_page(item.get(key), zero_based=key in {"page_idx", "page_index"})
        if page is not None:
            return page
    metadata = item.get("metadata") or item.get("meta")
    if isinstance(metadata, dict):
        return _extract_page(metadata)
    return None


def _coerce_page(value: Any, *, zero_based: bool = False) -> int | None:
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


def _extract_section(item: dict[str, Any]) -> str | None:
    for key in ("section", "section_title", "heading", "header", "title"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    metadata = item.get("metadata") or item.get("meta")
    if isinstance(metadata, dict):
        return _extract_section(metadata)
    return None


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


def _raw_block_type(item: dict[str, Any]) -> Any:
    for key in ("type", "block_type", "layout_type", "category", "kind", "sub_type"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    content = item.get("content")
    if isinstance(content, dict):
        table_type = content.get("table_type")
        if isinstance(table_type, str) and table_type.strip():
            return table_type
        if any(isinstance(content.get(key), str) and content.get(key).strip() for key in ("html", "table_body")):
            return "table"
    if any(isinstance(item.get(key), str) and item.get(key).strip() for key in ("table_body", "html")):
        return "table"
    return None


def _extract_bbox(item: dict[str, Any]) -> list[float] | None:
    for key in ("bbox", "box", "bounding_box", "position"):
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
                return [x, y, x + width, y + height]
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


def _coalesce_structured_table_blocks(
    blocks: list[MinerUStructuredBlock],
    *,
    keep_chart: bool,
) -> list[MinerUStructuredBlock]:
    kept: list[MinerUStructuredBlock] = []
    for block in blocks:
        if not _table_fingerprint(block.text):
            kept.append(block)
            continue
        match_idx = next(
            (
                idx
                for idx, existing in enumerate(kept)
                if _can_coalesce_structured_table(existing, block, keep_chart=keep_chart)
            ),
            None,
        )
        if match_idx is None:
            kept.append(block)
            continue

        existing = kept[match_idx]
        duplicate_count = existing.structured_duplicate_count + block.structured_duplicate_count
        if _structured_table_quality_key(block) > _structured_table_quality_key(existing):
            block.structured_duplicate_count = duplicate_count
            kept[match_idx] = block
        else:
            existing.structured_duplicate_count = duplicate_count
    return kept


def _can_coalesce_structured_table(
    existing: MinerUStructuredBlock,
    candidate: MinerUStructuredBlock,
    *,
    keep_chart: bool,
) -> bool:
    if _table_fingerprint(existing.text) != _table_fingerprint(candidate.text):
        return False
    if keep_chart and (_is_chart_like_block(existing) or _is_chart_like_block(candidate)):
        return False
    if existing.page is not None and candidate.page is not None and existing.page != candidate.page:
        return False
    if not _bbox_compatible(existing.bbox, candidate.bbox):
        return False

    existing_has_location = _has_structured_location(existing)
    candidate_has_location = _has_structured_location(candidate)
    if existing.bbox is not None and candidate.bbox is not None:
        return True
    if existing.bbox is not None or candidate.bbox is not None:
        return True
    if existing_has_location != candidate_has_location:
        return True
    if existing.page is None and candidate.page is None:
        return _is_lower_quality_structured_duplicate(existing, candidate)
    if existing.page == candidate.page and _is_lower_quality_structured_duplicate(existing, candidate):
        return True
    return False


def _has_structured_location(block: MinerUStructuredBlock) -> bool:
    return block.page is not None or block.bbox is not None


def _is_lower_quality_structured_duplicate(
    existing: MinerUStructuredBlock,
    candidate: MinerUStructuredBlock,
) -> bool:
    if existing.source_kind != candidate.source_kind:
        return True
    if _source_priority(existing.source_kind) != _source_priority(candidate.source_kind):
        return True
    if (
        existing.raw_type
        and candidate.raw_type
        and existing.raw_type != candidate.raw_type
        and not (_is_chart_like_block(existing) or _is_chart_like_block(candidate))
    ):
        return True
    if existing.structured_duplicate_count > 1 or candidate.structured_duplicate_count > 1:
        return True
    if (
        existing.page is not None
        and candidate.page is not None
        and existing.page == candidate.page
        and existing.bbox is None
        and candidate.bbox is None
    ):
        return False
    return False


def _is_chart_like_block(block: MinerUStructuredBlock) -> bool:
    raw = str(block.raw_type or "").lower()
    source = str(block.source_kind or "").lower()
    return "chart" in raw or "chart" in source


def _bbox_compatible(left: list[float] | None, right: list[float] | None) -> bool:
    if left is None or right is None:
        return True
    return _bbox_iou(left, right) >= 0.8 or _bbox_close(left, right)


def _bbox_iou(left: list[float], right: list[float]) -> float:
    lx0, ly0, lx1, ly1 = _normalize_bbox(left)
    rx0, ry0, rx1, ry1 = _normalize_bbox(right)
    inter_x0 = max(lx0, rx0)
    inter_y0 = max(ly0, ry0)
    inter_x1 = min(lx1, rx1)
    inter_y1 = min(ly1, ry1)
    inter_area = max(0.0, inter_x1 - inter_x0) * max(0.0, inter_y1 - inter_y0)
    left_area = max(0.0, lx1 - lx0) * max(0.0, ly1 - ly0)
    right_area = max(0.0, rx1 - rx0) * max(0.0, ry1 - ry0)
    union = left_area + right_area - inter_area
    return inter_area / union if union > 0 else 0.0


def _bbox_close(left: list[float], right: list[float]) -> bool:
    normalized_left = _normalize_bbox(left)
    normalized_right = _normalize_bbox(right)
    return all(abs(a - b) <= 2.0 for a, b in zip(normalized_left, normalized_right, strict=True))


def _normalize_bbox(bbox: list[float]) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = (float(value) for value in bbox[:4])
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def _structured_table_quality_key(block: MinerUStructuredBlock) -> tuple[int, int, int, int]:
    return (
        1 if block.page is not None else 0,
        1 if block.bbox is not None else 0,
        _source_priority(block.source_kind),
        len(block.text or ""),
    )


def _source_priority(source_kind: str | None) -> int:
    priorities = {
        "content_list": 100,
        "content_list_v2": 90,
        "model": 70,
        "layout": 60,
        "nested_content": 40,
        "unknown": 10,
    }
    return priorities.get(str(source_kind or "unknown"), 10)


def _infer_block_for_text(text: str, blocks: list[MinerUStructuredBlock]) -> MinerUStructuredBlock | None:
    matches = _infer_blocks_for_text(text, blocks)
    return _best_block(matches)


def _infer_blocks_for_text(text: str, blocks: list[MinerUStructuredBlock]) -> list[MinerUStructuredBlock]:
    needle = _compact_text(text)
    if not needle:
        return []
    scored: list[tuple[int, int, int, MinerUStructuredBlock]] = []
    for block in blocks:
        if block.page is None and block.section is None:
            continue
        haystack = _compact_text(block.text)
        if not haystack:
            continue
        score = 0
        if needle in haystack or haystack in needle:
            score = min(len(needle), len(haystack))
        elif len(needle) >= 40 and needle[:40] in haystack:
            score = 40
        elif len(haystack) >= 40 and haystack[:40] in needle:
            score = 40
        if score:
            scored.append((score, _source_priority(block.source_kind), 1 if block.bbox is not None else 0, block))
    scored.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    return [block for _score, _priority, _has_bbox, block in scored]


def _best_block(blocks: list[MinerUStructuredBlock]) -> MinerUStructuredBlock | None:
    return blocks[0] if blocks else None


def _compact_text(text: str) -> str:
    return "".join(str(text or "").split())


def _table_fingerprint(text: str) -> str:
    rows: list[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped or "|" not in stripped:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if cells and all(cell and set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        rows.append("|".join("".join(cell.split()) for cell in cells))
    return "\n".join(rows) if rows else _compact_text(text)


def _add_table_fingerprint(fingerprints: set[str], text: str) -> None:
    fingerprint = _table_fingerprint(text)
    if fingerprint:
        fingerprints.add(fingerprint)


def _coalesce_duplicate_table_nodes(nodes, *, keep_chart_separate: bool):
    kept = []
    table_key_to_idx: dict[tuple[str, str, tuple[tuple[str, ...], ...]], int] = {}
    for node in nodes:
        if node.modality != "table":
            kept.append(node)
            continue
        if keep_chart_separate and _is_chart_table_node(node):
            kept.append(node)
            continue
        exact_key = _table_exact_content_key(node.table_markdown or node.text or "")
        if not exact_key:
            kept.append(node)
            continue

        group_key = (node.metadata.source, node.metadata.doc_id, exact_key)
        existing_idx = table_key_to_idx.get(group_key)
        if existing_idx is None:
            table_key_to_idx[group_key] = len(kept)
            kept.append(node)
            continue

        existing = kept[existing_idx]
        if _table_node_quality_key(node) > _table_node_quality_key(existing):
            _merge_table_node_relationships(node, existing)
            kept[existing_idx] = node
        else:
            _merge_table_node_relationships(existing, node)

    return kept


def _table_exact_content_key(text: str) -> tuple[tuple[str, ...], ...]:
    table_text = str(text or "").strip()
    if not table_text:
        return ()
    if table_text.lstrip().lower().startswith("<table"):
        converted = html_table_to_markdown(table_text)
        if converted:
            table_text = converted

    rows: list[tuple[str, ...]] = []
    for line in table_text.splitlines():
        stripped = line.strip()
        if not stripped or "|" not in stripped:
            continue
        cells = tuple(cell.strip() for cell in stripped.strip("|").split("|"))
        if cells and all(cell and set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        rows.append(cells)
    return tuple(rows)


def _table_node_quality_key(node) -> tuple[int, int, int, int, int]:
    relationships = node.relationships or {}
    structured_duplicate_count = relationships.get("structured_duplicate_count")
    if not isinstance(structured_duplicate_count, int):
        structured_duplicate_count = 0
    return (
        1 if node.metadata.page is not None else 0,
        1 if relationships.get("page_source") == "structured" else 0,
        structured_duplicate_count,
        1 if node.metadata.section else 0,
        -node.metadata.chunk_index,
    )


def _merge_table_node_relationships(keeper, duplicate) -> None:
    rel = dict(keeper.relationships or {})
    duplicate_rel = dict(duplicate.relationships or {})

    duplicate_ids = [duplicate.node_id]
    duplicate_ids.extend(_string_list(duplicate_rel.get("merged_table_node_ids")))
    merged_ids = _unique_strings(_string_list(rel.get("merged_table_node_ids")) + duplicate_ids)
    merged_ids = [node_id for node_id in merged_ids if node_id != keeper.node_id]
    rel["merged_table_node_ids"] = merged_ids
    rel["table_duplicate_count"] = 1 + len(merged_ids)

    duplicate_occurrences = [_table_node_occurrence(duplicate)]
    duplicate_occurrences.extend(_dict_list(duplicate_rel.get("merged_table_occurrences")))
    rel["merged_table_occurrences"] = _unique_occurrences(
        _dict_list(rel.get("merged_table_occurrences")) + duplicate_occurrences
    )

    structured_duplicate_count = max(
        _int_value(rel.get("structured_duplicate_count"), default=1),
        _int_value(duplicate_rel.get("structured_duplicate_count"), default=1),
    )
    if structured_duplicate_count > 1 or "structured_duplicate_count" in rel:
        rel["structured_duplicate_count"] = structured_duplicate_count
    keeper.relationships.clear()
    keeper.relationships.update(rel)


def _table_node_occurrence(node) -> dict[str, Any]:
    relationships = node.relationships or {}
    return {
        "node_id": node.node_id,
        "page": node.metadata.page,
        "chunk_index": node.metadata.chunk_index,
        "mineru_source_kind": relationships.get("mineru_source_kind"),
        "mineru_raw_type": relationships.get("mineru_raw_type"),
        "page_source": relationships.get("page_source"),
    }


def _is_chart_table_node(node) -> bool:
    relationships = node.relationships or {}
    raw_type = str(relationships.get("mineru_raw_type") or "").lower()
    source_kind = str(relationships.get("mineru_source_kind") or "").lower()
    context_kind = str(relationships.get("markdown_context_kind") or "").lower()
    return "chart" in raw_type or "chart" in source_kind or "details_chart" in context_kind


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def _unique_occurrences(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for value in values:
        node_id = value.get("node_id")
        if not isinstance(node_id, str) or node_id in seen:
            continue
        seen.add(node_id)
        unique.append(value)
    return unique


def _int_value(value: Any, *, default: int) -> int:
    return value if isinstance(value, int) else default


def _section_from_markdown(text: str) -> str | None:
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title
    return None


def _mineru_bbox_source(block: MinerUStructuredBlock | None) -> str | None:
    if block is None or block.bbox is None:
        return None
    return block.source_kind or "mineru"


def _mineru_bbox_coordinate_system(block: MinerUStructuredBlock | None) -> str | None:
    if block is None or block.bbox is None:
        return None
    source_kind = str(block.source_kind or "").lower()
    if source_kind in {"content_list", "content_list_v2"}:
        return "mineru_content_list_1000"
    if source_kind == "model":
        return "mineru_model_raw"
    if source_kind == "layout":
        return "mineru_layout_raw"
    return "mineru_raw"


def _mineru_bbox_payload(blocks: list[MinerUStructuredBlock]) -> dict[str, Any]:
    located = [block for block in blocks if block is not None and block.bbox is not None]
    if not located:
        return {
            "bbox": None,
            "bbox_items": None,
            "bbox_coordinate_system": None,
            "bbox_source": None,
            "bbox_merge_policy": None,
        }

    # Avoid mixing duplicate detections from different MinerU output files or coordinate systems.
    best_priority = max(_source_priority(block.source_kind) for block in located)
    located = [block for block in located if _source_priority(block.source_kind) == best_priority]
    coordinate_system = _mineru_bbox_coordinate_system(located[0])
    located = [block for block in located if _mineru_bbox_coordinate_system(block) == coordinate_system]

    pages = {block.page for block in located if block.page is not None}
    if len(pages) > 1:
        return {
            "bbox": None,
            "bbox_items": None,
            "bbox_coordinate_system": coordinate_system,
            "bbox_source": _mineru_bbox_source(located[0]),
            "bbox_merge_policy": "skipped_multi_page",
        }

    bbox_items = _unique_bboxes([_normalize_bbox(block.bbox or [0, 0, 0, 0]) for block in located])
    if not bbox_items:
        return {
            "bbox": None,
            "bbox_items": None,
            "bbox_coordinate_system": coordinate_system,
            "bbox_source": _mineru_bbox_source(located[0]),
            "bbox_merge_policy": None,
        }
    return {
        "bbox": _union_bboxes(bbox_items),
        "bbox_items": bbox_items,
        "bbox_coordinate_system": coordinate_system,
        "bbox_source": _mineru_bbox_source(located[0]),
        "bbox_merge_policy": "single" if len(bbox_items) == 1 else "union",
    }


def _unique_bboxes(values: list[tuple[float, float, float, float]]) -> list[list[float]]:
    seen: set[tuple[float, float, float, float]] = set()
    result: list[list[float]] = []
    for value in values:
        key = tuple(round(coord, 3) for coord in value)
        if key in seen:
            continue
        seen.add(key)
        result.append([float(coord) for coord in value])
    return result


def _union_bboxes(values: list[list[float]]) -> list[float]:
    normalized = [_normalize_bbox(value) for value in values]
    return [
        min(box[0] for box in normalized),
        min(box[1] for box in normalized),
        max(box[2] for box in normalized),
        max(box[3] for box in normalized),
    ]


def _mineru_relationships(block: MinerUStructuredBlock | None) -> dict[str, Any]:
    relationships: dict[str, Any] = {"source_parser": "mineru"}
    if block is None:
        return relationships
    relationships["mineru_block_type"] = block.type
    if block.raw_type:
        relationships["mineru_raw_type"] = block.raw_type
    if block.source_kind:
        relationships["mineru_source_kind"] = block.source_kind
    if block.bbox is not None:
        bbox_payload = _mineru_bbox_payload([block])
        relationships["bbox"] = bbox_payload.get("bbox")
        relationships["bbox_items"] = bbox_payload.get("bbox_items")
        relationships["bbox_source"] = _mineru_bbox_source(block)
        relationships["bbox_coordinate_system"] = _mineru_bbox_coordinate_system(block)
        relationships["bbox_merge_policy"] = bbox_payload.get("bbox_merge_policy")
    if block.structured_duplicate_count > 1:
        relationships["structured_duplicate_count"] = block.structured_duplicate_count
    if block.page is not None:
        relationships["page"] = block.page
        relationships["page_node_id"] = f"page:{block.page}"
        if block.source_kind or block.raw_type:
            relationships["page_source"] = "structured"
    return relationships


def _link_mineru_nodes(
    nodes,
    *,
    context_window_chars: int,
    same_page_link_max_nodes: int,
):
    if not nodes:
        return nodes

    linked = [node.model_copy(deep=True) for node in nodes]
    linked.sort(key=lambda node: (node.metadata.source, node.metadata.doc_id, node.metadata.chunk_index))

    by_page: dict[tuple[str, int], list] = {}
    for node in linked:
        if node.metadata.page is None:
            continue
        by_page.setdefault((node.metadata.doc_id, node.metadata.page), []).append(node)

    text_nodes = [node for node in linked if node.modality == "text"]
    table_nodes = [node for node in linked if node.modality == "table"]
    formula_nodes = [node for node in linked if node.modality == "formula"]

    for idx, node in enumerate(linked):
        rel = dict(node.relationships or {})
        if idx > 0:
            prev_id = linked[idx - 1].node_id
            rel["prev_id"] = prev_id
            rel["doc_prev_node_id"] = prev_id
        if idx + 1 < len(linked):
            next_id = linked[idx + 1].node_id
            rel["next_id"] = next_id
            rel["doc_next_node_id"] = next_id
        if node.metadata.page is not None:
            rel["page"] = node.metadata.page
            rel["page_node_id"] = f"{node.metadata.doc_id}:page:{node.metadata.page}"
            same_page_ids = [
                other.node_id
                for other in by_page.get((node.metadata.doc_id, node.metadata.page), [])
                if other.node_id != node.node_id
            ]
            if same_page_link_max_nodes > 0:
                same_page_ids = same_page_ids[:same_page_link_max_nodes]
            if same_page_ids:
                rel["same_page_node_ids"] = same_page_ids
        node.relationships.clear()
        node.relationships.update(rel)

    for node in linked:
        if node.modality not in {"table", "formula"}:
            continue
        prev_text = _nearest_text_node(node, text_nodes, direction=-1)
        next_text = _nearest_text_node(node, text_nodes, direction=1)
        context_ids = []
        rel = dict(node.relationships or {})
        if prev_text is not None:
            rel["context_prev_node_id"] = prev_text.node_id
            context_ids.append(prev_text.node_id)
        if next_text is not None:
            rel["context_next_node_id"] = next_text.node_id
            context_ids.append(next_text.node_id)
        if context_ids:
            rel["context_node_ids"] = context_ids
            rel["context_window_chars"] = context_window_chars
        node.relationships.clear()
        node.relationships.update(rel)

        for text_node in [candidate for candidate in (prev_text, next_text) if candidate is not None]:
            text_rel = dict(text_node.relationships or {})
            key = "related_table_node_ids" if node.modality == "table" else "related_formula_node_ids"
            related = text_rel.get(key, [])
            if not isinstance(related, list):
                related = [related]
            if node.node_id not in related:
                related.append(node.node_id)
            text_rel[key] = [item for item in related if isinstance(item, str)]
            text_node.relationships.clear()
            text_node.relationships.update(text_rel)

    # Prefer deterministic table/formula relation ordering on text nodes.
    for node in text_nodes:
        rel = dict(node.relationships or {})
        for key, candidates in (
            ("related_table_node_ids", table_nodes),
            ("related_formula_node_ids", formula_nodes),
        ):
            ids = rel.get(key)
            if isinstance(ids, list):
                order = {candidate.node_id: idx for idx, candidate in enumerate(candidates)}
                rel[key] = sorted({item for item in ids if isinstance(item, str)}, key=lambda item: order.get(item, 10**9))
        node.relationships.clear()
        node.relationships.update(rel)

    return sorted(linked, key=lambda node: node.metadata.chunk_index)


def _nearest_text_node(node, text_nodes: list, *, direction: int):
    same_doc = [candidate for candidate in text_nodes if candidate.metadata.doc_id == node.metadata.doc_id]
    if direction < 0:
        candidates = [
            candidate
            for candidate in same_doc
            if candidate.metadata.chunk_index < node.metadata.chunk_index
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda candidate: candidate.metadata.chunk_index)
    candidates = [
        candidate
        for candidate in same_doc
        if candidate.metadata.chunk_index > node.metadata.chunk_index
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: candidate.metadata.chunk_index)
