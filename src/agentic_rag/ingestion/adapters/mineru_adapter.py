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

        for table_block in table_blocks_with_metadata:
            table_md = table_block.text
            for table_chunk in self.table_chunker.chunk_markdown_table(table_md):
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

        table_blocks = extract_table_blocks(markdown)
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
            if "|" in line:
                table_block = [line]
                j = i + 1
                while j < len(lines) and "|" in lines[j]:
                    table_block.append(lines[j])
                    j += 1
                candidate = "\n".join(table_block)
                if len([ln for ln in table_block if ln.strip()]) >= 2 and (
                    candidate.lstrip().startswith("<table") or any("---" in ln for ln in table_block)
                ):
                    flush_text()
                    for table_chunk in self.table_chunker.chunk_markdown_table(candidate):
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

        if not nodes and table_blocks:
            for block in table_blocks:
                for table_chunk in self.table_chunker.chunk_markdown_table(block.markdown):
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

        if self.settings.enable_formula_recognition:
            formula_source = strip_table_blocks(markdown, table_blocks)
            for formula in extract_formulas(formula_source):
                matched_block = _infer_block_for_text(formula.text, structured_blocks)
                page = matched_block.page if matched_block else None
                section = matched_block.section if matched_block else None
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
                        relationships=_mineru_relationships(
                            matched_block
                            or MinerUStructuredBlock(type="formula", text=formula.text, page=page, section=section)
                        ),
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
        relationships["page_node_id"] = f"page:{block.page}"
    return relationships
