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
                matched_block = _infer_block_for_text(chunk, structured_blocks) or _infer_block_for_text(text, structured_blocks)
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
                        relationships=relationships,
                    )
                )
                idx += 1
                formula_count += 1

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
    for item in structured_content:
        blocks.extend(_walk_structured_item(item, inherited_page=None, inherited_section=None))
    return [block for block in blocks if block.text]


def _walk_structured_item(
    item: Any,
    *,
    inherited_page: int | None,
    inherited_section: str | None,
) -> list[MinerUStructuredBlock]:
    if isinstance(item, list):
        blocks: list[MinerUStructuredBlock] = []
        for sub in item:
            blocks.extend(
                _walk_structured_item(
                    sub,
                    inherited_page=inherited_page,
                    inherited_section=inherited_section,
                )
            )
        return blocks
    if not isinstance(item, dict):
        return []

    page = _extract_page(item)
    if page is None:
        page = inherited_page
    section = _extract_section(item) or inherited_section
    item_type = _normalize_block_type(item.get("type") or item.get("category") or item.get("role"))

    blocks: list[MinerUStructuredBlock] = []
    text = _extract_block_text(item, item_type)
    if text:
        blocks.append(MinerUStructuredBlock(type=item_type, text=text, page=page, section=section))

    for key in ("children", "blocks", "content", "items", "spans", "lines", "layout"):
        value = item.get(key)
        if isinstance(value, (list, dict)):
            blocks.extend(_walk_structured_item(value, inherited_page=page, inherited_section=section))
    return blocks


def _extract_block_text(item: dict[str, Any], item_type: str) -> str:
    if item_type == "table":
        table_html = str(item.get("table_body") or item.get("html") or item.get("content") or "")
        table_md = html_table_to_markdown(table_html)
        if table_md:
            return table_md

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


def _infer_block_for_text(text: str, blocks: list[MinerUStructuredBlock]) -> MinerUStructuredBlock | None:
    needle = _compact_text(text)
    if not needle:
        return None
    best: tuple[int, MinerUStructuredBlock] | None = None
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
        if score and (best is None or score > best[0]):
            best = (score, block)
    return best[1] if best else None


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


def _section_from_markdown(text: str) -> str | None:
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title
    return None


def _mineru_relationships(block: MinerUStructuredBlock | None) -> dict[str, Any]:
    relationships: dict[str, Any] = {"source_parser": "mineru"}
    if block is None:
        return relationships
    relationships["mineru_block_type"] = block.type
    if block.page is not None:
        relationships["page"] = block.page
        relationships["page_node_id"] = f"page:{block.page}"
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
