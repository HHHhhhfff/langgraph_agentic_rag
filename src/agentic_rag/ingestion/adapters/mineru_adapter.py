from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from agentic_rag.config import Settings
from agentic_rag.ingestion.adapters.mineru_client import MinerUClient
from agentic_rag.ingestion.adapters.mineru_result_parser import parse_mineru_markdown
from agentic_rag.ingestion.chunk_strategies import TableChunker, build_text_chunk_strategy
from agentic_rag.ingestion.formula_extractor import extract_formulas
from agentic_rag.ingestion.node_normalizer import NodeNormalizer
from agentic_rag.ingestion.node_schema import IngestionFailure, MultimodalIngestionResult
from agentic_rag.ingestion.table_extractor import extract_table_blocks, html_table_to_markdown, strip_table_blocks
from agentic_rag.ingestion.utils import build_doc_id
from agentic_rag.observability.stage_logger import StageLogger, StageTimer


class _TextChunkStrategy(Protocol):
    def chunk_text(self, text: str) -> list[str]:
        ...


class MinerUAdapterError(RuntimeError):
    """Raised for MinerU adapter errors."""


@dataclass(slots=True)
class MinerUStructuredBlock:
    type: str
    text: str
    page: int | None = None
    page_size: tuple[float, float] | None = None
    section: str | None = None
    bbox: list[float] | None = None
    bbox_coordinate_system: str | None = None
    image_path: str | None = None
    source_kind: str | None = None
    raw_type: str | None = None
    structured_duplicate_count: int = 1
    block_id: str | None = None
    order: int = 0
    title_region_id: int | None = None
    heading_context_block_id: str | None = None


@dataclass(slots=True)
class MinerULogicalSegment:
    blocks: list[MinerUStructuredBlock]
    cross_page_merge: bool = False
    merge_reason: str | None = None


@dataclass(slots=True)
class MinerUImageUnit:
    image_path: str | None = None
    caption_blocks: list[MinerUStructuredBlock] | None = None
    semantic_blocks: list[MinerUStructuredBlock] | None = None
    image_blocks: list[MinerUStructuredBlock] | None = None
    semantic_kind: str | None = None

    @property
    def blocks(self) -> list[MinerUStructuredBlock]:
        return [
            *(self.image_blocks or []),
            *(self.caption_blocks or []),
            *(self.semantic_blocks or []),
        ]


_CROSS_PAGE_TAIL_BOTTOM_THRESHOLD = 0.85
_CROSS_PAGE_HEAD_TOP_THRESHOLD = 0.20
_BODY_CONTINUATION_TYPES = {"text", "formula"}
_CROSS_PAGE_BARRIER_TYPES = {"heading", "table", "image", "image_caption", "image_semantic"}
IMAGE_SEMANTIC_WHOLE = "whole_image"
IMAGE_SEMANTIC_CAPTION = "caption"
IMAGE_SEMANTIC_OCR = "ocr"
_IMAGE_MARKDOWN_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_DETAILS_BLOCK_RE = re.compile(r"<details\b[^>]*>.*?</details>", re.IGNORECASE | re.DOTALL)
_DETAILS_SUMMARY_RE = re.compile(r"<summary\b[^>]*>(.*?)</summary>", re.IGNORECASE | re.DOTALL)


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
            asset_paths = _write_mineru_assets_for_doc(
                result.assets or {},
                file_path=file_path,
                output_dir=Path(self.settings.mineru_asset_output_dir),
            )
            if self.stage_logger:
                self.stage_logger.log_counter(
                    "mineru_assets",
                    source=str(file_path),
                    parser="mineru",
                    raw_asset_count=len(result.assets or {}),
                    resolved_asset_count=len(asset_paths),
                    asset_output_dir=self.settings.mineru_asset_output_dir,
                )
            nodes = self._build_nodes_from_markdown(
                markdown,
                file_path,
                structured_content=result.structured_content or [],
                asset_paths=asset_paths,
            )
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

    def _build_nodes_from_markdown(
        self,
        markdown: str,
        file_path: Path,
        structured_content: list[Any] | None = None,
        asset_paths: dict[str, str] | None = None,
    ):
        structured_content = structured_content or []
        asset_paths = asset_paths or {}
        document_markdown = _strip_mineru_markdown_image_blocks(markdown)
        nodes = []
        idx = 0
        text_count = 0
        table_count = 0
        image_count = 0
        formula_count = 0

        structured_blocks = _extract_structured_blocks(structured_content)
        _assign_title_regions(structured_blocks)
        _assign_heading_context(structured_blocks)
        table_blocks_with_metadata = [
            block for block in structured_blocks if _is_structured_table_node_source(block)
        ]
        table_blocks_with_metadata = _select_structured_table_blocks_for_nodes(table_blocks_with_metadata)
        if self.settings.mineru_structured_table_coalesce_enabled:
            table_blocks_with_metadata = _coalesce_structured_table_blocks(
                table_blocks_with_metadata,
                keep_chart=self.settings.mineru_structured_table_coalesce_keep_chart,
            )
        table_blocks = extract_table_blocks(document_markdown)
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

        image_units = _extract_mineru_image_units(
            structured_blocks,
            markdown=markdown,
            asset_paths=asset_paths,
        )
        for image_unit in image_units:
            created = self._add_mineru_image_unit_nodes(
                nodes=nodes,
                file_path=file_path,
                image_unit=image_unit,
                start_index=idx,
            )
            new_nodes = nodes[-created:] if created else []
            idx += created
            image_count += sum(1 for node in new_nodes if node.modality == "image")
            text_count += sum(1 for node in new_nodes if node.modality == "text")

        if self.settings.mineru_table_markdown_fallback and not table_blocks_with_metadata and not structured_blocks:
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

        clean_markdown = strip_table_blocks(document_markdown, table_blocks)

        structured_text_blocks = _select_structured_text_blocks_for_chunking(structured_blocks)

        def add_text_node(
            *,
            chunk: str,
            page: int | None,
            section: str | None,
            bbox_blocks: list[MinerUStructuredBlock],
            relationships: dict[str, Any],
        ) -> None:
            nonlocal idx, text_count
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
                    **_mineru_bbox_payload(bbox_blocks),
                    relationships=relationships,
                )
            )
            idx += 1
            text_count += 1

        def flush_structured_text_group(
            group: list[MinerUStructuredBlock],
            *,
            policy: str = "block_aware_v1",
            extra_relationships: dict[str, Any] | None = None,
            part_index: int | None = None,
            part_count: int | None = None,
        ) -> None:
            if not group:
                return
            text = _blocks_to_markdown(group)
            if not text:
                return
            first = group[0]
            page = first.page
            section = _section_for_text_blocks(group)
            relationships = _mineru_text_chunk_relationships(
                group,
                policy=policy,
                part_index=part_index,
                part_count=part_count,
            )
            if extra_relationships:
                relationships.update(extra_relationships)
            add_text_node(chunk=text, page=page, section=section, bbox_blocks=group, relationships=relationships)

        def add_large_structured_text_segment(
            segment: MinerULogicalSegment,
            *,
            policy: str = "block_aware_split_large_segment_v1",
            extra_relationships: dict[str, Any] | None = None,
        ) -> None:
            text = _blocks_to_markdown(segment.blocks)
            if not text:
                return
            hard_max_chars = max(1, self.settings.chunk_hard_max_chars, self.settings.chunk_size)
            chunks = _split_large_structured_text(
                text,
                self.text_strategy,
                max_chars=hard_max_chars,
                overlap=max(0, self.settings.chunk_overlap),
            )
            chunks = chunks or [text]
            for part_index, chunk in enumerate(chunks):
                chunk_blocks = _infer_blocks_for_text(chunk, segment.blocks) or segment.blocks
                chunk_blocks = sorted(chunk_blocks, key=lambda block: block.order)
                relationships = _mineru_text_chunk_relationships(
                    chunk_blocks,
                    policy=policy,
                    part_index=part_index,
                    part_count=len(chunks),
                )
                if chunk_blocks == segment.blocks and len(segment.blocks) > 1:
                    relationships["bbox_merge_policy_detail"] = "large_segment_inherited"
                if extra_relationships:
                    relationships.update(extra_relationships)
                add_text_node(
                    chunk=chunk,
                    page=chunk_blocks[0].page if chunk_blocks else None,
                    section=_section_for_text_blocks(chunk_blocks) or _section_from_markdown(chunk),
                    bbox_blocks=chunk_blocks,
                    relationships=relationships,
                )

        use_markdown_text_fallback = not structured_text_blocks

        if structured_text_blocks:
            hard_max_chars = max(1, self.settings.chunk_hard_max_chars, self.settings.chunk_size)
            segments = _assemble_logical_text_segments(
                structured_text_blocks,
                structured_blocks,
                document_markdown,
                max_chars=max(1, self.settings.chunk_size),
            )
            for segment in segments:
                extra_relationships = _logical_segment_relationships(segment)
                segment_text = _blocks_to_markdown(segment.blocks)
                is_plain_single_block = len(segment.blocks) == 1 and not segment.cross_page_merge
                if len(segment_text) > hard_max_chars:
                    add_large_structured_text_segment(
                        segment,
                        policy="block_aware_split_large_block_v1"
                        if is_plain_single_block
                        else "block_aware_split_large_segment_v1",
                        extra_relationships=extra_relationships,
                    )
                else:
                    flush_structured_text_group(
                        segment.blocks,
                        policy="block_aware_v1" if is_plain_single_block else "block_aware_logical_segment_v1",
                        extra_relationships=extra_relationships,
                    )

        buffer: list[str] = []

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
                bbox_blocks, bbox_diagnostics = _select_bbox_blocks_for_text_chunk(matched_blocks)
                page = matched_block.page if matched_block else None
                section = (matched_block.section if matched_block else None) or _section_from_markdown(chunk)
                relationships = _mineru_relationships(
                    matched_block or MinerUStructuredBlock(type="text", text=chunk, page=page, section=section)
                )
                relationships.update(bbox_diagnostics)
                add_text_node(chunk=chunk, page=page, section=section, bbox_blocks=bbox_blocks, relationships=relationships)

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
            if use_markdown_text_fallback:
                buffer.append(line)
            i += 1

        flush_text()

        if self.settings.enable_formula_recognition:
            formula_source = strip_table_blocks(document_markdown, table_blocks)
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
                    _infer_block_for_text(formula.formula_latex, structured_blocks)
                    or _infer_block_for_text(formula.raw, structured_blocks)
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
                image_count=image_count,
                formula_count=formula_count,
            )

        if self.settings.mineru_context_link_enabled:
            nodes = _link_mineru_nodes(
                nodes,
                context_window_chars=self.settings.mineru_context_window_chars,
                same_page_link_max_nodes=self.settings.mineru_same_page_link_max_nodes,
            )

        return nodes

    def _add_mineru_image_unit_nodes(
        self,
        *,
        nodes: list,
        file_path: Path,
        image_unit: MinerUImageUnit,
        start_index: int,
    ) -> int:
        created = 0
        all_image_blocks = list(image_unit.image_blocks or [])
        all_caption_blocks = list(image_unit.caption_blocks or [])
        all_semantic_blocks = list(image_unit.semantic_blocks or [])
        all_unit_blocks = image_unit.blocks
        image_unit.caption_blocks = _select_image_caption_blocks(image_unit.caption_blocks or [])
        image_unit.semantic_blocks = _select_image_semantic_blocks(image_unit.semantic_blocks or [])
        unit_blocks = image_unit.blocks
        caption_text = _join_unique_block_texts(image_unit.caption_blocks or [])
        semantic_text = _join_unique_block_texts(image_unit.semantic_blocks or [])
        resolved_image_path = image_unit.image_path
        page = _page_for_blocks(all_unit_blocks or unit_blocks)
        section = _section_for_text_blocks(image_unit.caption_blocks or []) if image_unit.caption_blocks else None
        whole_node = None
        semantic_node = None
        caption_node = None

        if resolved_image_path:
            whole_location_blocks = _whole_image_location_blocks(
                all_image_blocks,
                all_semantic_blocks,
                all_caption_blocks,
                unit_blocks,
            )
            whole_relationships = _mineru_image_unit_relationships(
                image_unit,
                role="whole_image",
                semantic_type=IMAGE_SEMANTIC_WHOLE,
            )
            _apply_location_to_relationships(whole_relationships, whole_location_blocks)
            whole_relationships["image_path"] = resolved_image_path
            whole_relationships["mineru_image_path"] = resolved_image_path
            whole_node = self.normalizer.normalize(
                source=str(file_path),
                parser_name="mineru:image",
                chunk_index=start_index + created,
                modality="image",
                text=_whole_image_text(resolved_image_path, caption_text=caption_text, semantic_text=semantic_text),
                image_path=resolved_image_path,
                page=_page_for_blocks(whole_location_blocks) or page,
                title=file_path.stem,
                section=section,
                **_mineru_bbox_payload(whole_location_blocks),
                relationships=whole_relationships,
            )
            nodes.append(whole_node)
            created += 1

        if semantic_text:
            semantic_location_blocks = _image_unit_location_blocks(
                all_semantic_blocks,
                all_image_blocks,
            )
            semantic_type = image_unit.semantic_kind or _infer_image_semantic_type(image_unit.semantic_blocks or [])
            semantic_relationships = _mineru_image_unit_relationships(
                image_unit,
                role="image_semantic",
                semantic_type=semantic_type,
            )
            _apply_location_to_relationships(semantic_relationships, semantic_location_blocks)
            semantic_relationships["mineru_generated_semantic"] = True
            semantic_relationships["details_summary"] = _details_summary(semantic_text)
            if whole_node is not None:
                semantic_relationships["parent_image_node_id"] = whole_node.node_id
            if resolved_image_path:
                semantic_relationships["image_path"] = resolved_image_path
                semantic_relationships["mineru_image_path"] = resolved_image_path
            semantic_node = self.normalizer.normalize(
                source=str(file_path),
                parser_name="mineru:image_semantic",
                chunk_index=start_index + created,
                modality="image",
                text=_image_semantic_text(caption_text=caption_text, semantic_text=semantic_text),
                image_path=resolved_image_path,
                page=_page_for_blocks(semantic_location_blocks) or page,
                title=file_path.stem,
                section=section,
                **_mineru_bbox_payload(semantic_location_blocks),
                relationships=semantic_relationships,
            )
            nodes.append(semantic_node)
            created += 1

        if caption_text:
            caption_location_blocks = _caption_location_blocks(
                image_unit.caption_blocks or [],
                all_image_blocks,
                all_semantic_blocks,
                unit_blocks,
            )
            caption_relationships = _mineru_image_unit_relationships(
                image_unit,
                role="caption_text",
                semantic_type=IMAGE_SEMANTIC_CAPTION,
            )
            _apply_location_to_relationships(caption_relationships, caption_location_blocks)
            related_image_ids = []
            if semantic_node is not None:
                related_image_ids.append(semantic_node.node_id)
            if whole_node is not None:
                caption_relationships["related_whole_image_node_ids"] = [whole_node.node_id]
                if not related_image_ids:
                    related_image_ids.append(whole_node.node_id)
            if related_image_ids:
                caption_relationships["related_image_node_ids"] = related_image_ids
                caption_relationships["mineru_caption_for_image_node_id"] = related_image_ids[0]
            if resolved_image_path:
                caption_relationships["image_path"] = resolved_image_path
                caption_relationships["mineru_image_path"] = resolved_image_path
            caption_node = self.normalizer.normalize(
                source=str(file_path),
                parser_name="mineru:image_caption",
                chunk_index=start_index + created,
                modality="text",
                text=caption_text,
                page=_page_for_blocks(caption_location_blocks) or page,
                title=file_path.stem,
                section=section,
                **_mineru_bbox_payload(caption_location_blocks),
                relationships=caption_relationships,
            )
            nodes.append(caption_node)
            created += 1

        if caption_node is not None:
            if semantic_node is not None:
                semantic_node.relationships["caption_node_id"] = caption_node.node_id
            if whole_node is not None:
                whole_node.relationships["caption_node_id"] = caption_node.node_id
        if semantic_node is not None and whole_node is not None:
            whole_node.relationships["image_semantic_node_ids"] = [semantic_node.node_id]

        return created


def _extract_structured_blocks(structured_content: list[Any]) -> list[MinerUStructuredBlock]:
    blocks: list[MinerUStructuredBlock] = []
    page_sizes = _collect_page_sizes(structured_content)
    for idx, item in enumerate(structured_content):
        source_kind = _infer_structured_source_kind(item, idx)
        blocks.extend(
            _walk_structured_item(
                item,
                inherited_page=None,
                inherited_page_size=None,
                inherited_section=None,
                source_kind=source_kind,
                page_sizes=page_sizes,
            )
        )
    kept = [
        block
        for block in blocks
        if block.text or block.type == "image" or block.image_path
    ]
    for order, block in enumerate(kept):
        block.order = order
        block.block_id = f"{block.source_kind or 'structured'}:{order}"
    return kept


def _walk_structured_item(
    item: Any,
    *,
    inherited_page: int | None,
    inherited_page_size: tuple[float, float] | None,
    inherited_section: str | None,
    source_kind: str | None,
    page_sizes: dict[int, tuple[float, float]],
) -> list[MinerUStructuredBlock]:
    if isinstance(item, list):
        blocks: list[MinerUStructuredBlock] = []
        list_source_kind = source_kind
        inferred = _infer_structured_source_kind(item, 0)
        if _should_prefer_inferred_source_kind(source_kind, inferred):
            list_source_kind = inferred
        page_list = _looks_like_page_block_list(item)
        for idx, sub in enumerate(item):
            child_page = inherited_page
            if page_list and child_page is None:
                child_page = idx + 1
            child_page_size = page_sizes.get(child_page) if child_page is not None else inherited_page_size
            if child_page_size is None:
                child_page_size = inherited_page_size
            blocks.extend(
                _walk_structured_item(
                    sub,
                    inherited_page=child_page,
                    inherited_page_size=child_page_size,
                    inherited_section=inherited_section,
                    source_kind=list_source_kind,
                    page_sizes=page_sizes,
                )
            )
        return blocks
    if not isinstance(item, dict):
        return []

    numeric_page_items = [
        (str(key), value)
        for key, value in item.items()
        if str(key).isdigit() and isinstance(value, (list, dict))
    ]
    if numeric_page_items:
        blocks: list[MinerUStructuredBlock] = []
        for key, value in sorted(numeric_page_items, key=lambda pair: int(pair[0])):
            child_page = _coerce_page(int(key), zero_based=True)
            blocks.extend(
                _walk_structured_item(
                    value,
                    inherited_page=child_page,
                    inherited_page_size=(
                        page_sizes.get(child_page) if child_page is not None else inherited_page_size
                    )
                    or inherited_page_size,
                    inherited_section=inherited_section,
                    source_kind=source_kind,
                    page_sizes=page_sizes,
                )
            )
        return blocks

    if "structured_content" in item and isinstance(item.get("structured_content"), (list, dict)):
        inferred = _infer_structured_source_kind(item, 0)
        child_source_kind = inferred if _should_prefer_inferred_source_kind(source_kind, inferred) else source_kind
        return _walk_structured_item(
            item.get("structured_content"),
            inherited_page=inherited_page,
            inherited_page_size=inherited_page_size,
            inherited_section=inherited_section,
            source_kind=child_source_kind,
            page_sizes=page_sizes,
        )

    page = _extract_page(item)
    if page is None:
        page = inherited_page
    page_size = _extract_page_size(item)
    if page_size is None and page is not None:
        page_size = page_sizes.get(page)
    if page_size is None:
        page_size = inherited_page_size
    section = _extract_section(item) or inherited_section
    raw_type = _raw_block_type(item)
    item_type = _normalize_block_type(raw_type)
    if item_type == "text" and _is_text_level_heading(item):
        item_type = "heading"
        raw_type = _text_level_raw_type(item) or raw_type
    bbox, bbox_coordinate_system = _extract_bbox_with_coordinate_system(
        item,
        source_kind=source_kind,
        page_size=page_size,
    )
    if _is_composite_bbox_block(item_type, raw_type):
        bbox, bbox_coordinate_system = _union_composite_bbox(
            item,
            own_bbox=bbox,
            own_coordinate_system=bbox_coordinate_system,
            source_kind=source_kind,
            page_size=page_size,
        )
    image_path = _extract_image_path(item)

    blocks: list[MinerUStructuredBlock] = []
    text = _extract_block_text(item, item_type)
    if item_type == "image" and not text:
        text = _image_fallback_text(image_path)
    if item_type == "image_semantic" and image_path and not text:
        # A MinerU chart span with only image_path is the image body, not generated semantic text.
        item_type = "image"
        text = _image_fallback_text(image_path)
    if text:
        blocks.append(
            MinerUStructuredBlock(
                type=item_type,
                text=text,
                page=page,
                page_size=page_size,
                section=section,
                bbox=bbox,
                bbox_coordinate_system=bbox_coordinate_system,
                image_path=image_path,
                source_kind=source_kind,
                raw_type=str(raw_type or item_type).strip().lower(),
            )
        )
        if item_type in {"image", "image_semantic"}:
            for caption_text in _extract_embedded_image_captions(item):
                blocks.append(
                    MinerUStructuredBlock(
                        type="image_caption",
                        text=caption_text,
                        page=page,
                        page_size=page_size,
                        section=None,
                        bbox=None,
                        image_path=image_path,
                        source_kind=source_kind,
                        raw_type="embedded_image_caption",
                    )
                )
            return blocks
        if item_type == "image_caption":
            return blocks
        if item_type == "table":
            return blocks

    for key in (
        "children",
        "blocks",
        "content",
        "items",
        "spans",
        "lines",
        "layout",
        "pdf_info",
        "preproc_blocks",
        "para_blocks",
        "pdfData",
    ):
        value = item.get(key)
        if isinstance(value, (list, dict)):
            blocks.extend(
                _walk_structured_item(
                    value,
                    inherited_page=page,
                    inherited_page_size=page_size,
                    inherited_section=section,
                    source_kind="nested_content" if key == "content" else source_kind,
                    page_sizes=page_sizes,
                )
            )
    return blocks


def _should_prefer_inferred_source_kind(current: str | None, inferred: str | None) -> bool:
    if not inferred:
        return False
    if inferred.startswith("structured_json"):
        return False
    if current is None:
        return True
    return current.startswith("structured_json")


def _looks_like_page_block_list(value: list[Any]) -> bool:
    """MinerU may return one list per page without repeating page_idx on blocks."""

    if len(value) == 1:
        only = value[0]
        if not isinstance(only, list):
            return False
        dict_items = [sub for sub in only if isinstance(sub, dict)]
        return bool(dict_items) and any(
            {"type", "bbox"} & {str(key).lower() for key in sub.keys()} for sub in dict_items
        )
    if len(value) < 2:
        return False
    sample = value[: min(len(value), 12)]
    page_like = 0
    for item in sample:
        if not isinstance(item, list):
            continue
        dict_items = [sub for sub in item if isinstance(sub, dict)]
        if not dict_items:
            continue
        if any(_extract_page(sub) is not None for sub in dict_items):
            continue
        if any({"type", "bbox"} & {str(key).lower() for key in sub.keys()} for sub in dict_items):
            page_like += 1
    return page_like >= max(2, len(sample) // 2)


def _collect_page_sizes(value: Any) -> dict[int, tuple[float, float]]:
    page_sizes: dict[int, tuple[float, float]] = {}

    def walk(item: Any, inherited_page: int | None = None) -> None:
        if isinstance(item, list):
            page_list = _looks_like_page_block_list(item)
            for idx, sub in enumerate(item):
                child_page = inherited_page
                if page_list and child_page is None:
                    child_page = idx + 1
                walk(sub, child_page)
            return
        if not isinstance(item, dict):
            return

        page = _extract_page(item)
        if page is None:
            page = inherited_page
        page_size = _extract_page_size(item)
        if page is not None and page_size is not None:
            page_sizes.setdefault(page, page_size)

        numeric_page_items = [
            (str(key), sub)
            for key, sub in item.items()
            if str(key).isdigit() and isinstance(sub, (list, dict))
        ]
        for key, sub in numeric_page_items:
            walk(sub, _coerce_page(int(key), zero_based=True))

        for key in (
            "structured_content",
            "children",
            "blocks",
            "content",
            "items",
            "spans",
            "lines",
            "layout",
            "pdf_info",
            "preproc_blocks",
            "para_blocks",
            "pdfData",
        ):
            sub = item.get(key)
            if isinstance(sub, (list, dict)):
                walk(sub, page)

    walk(value)
    return page_sizes


def _extract_block_text(item: dict[str, Any], item_type: str) -> str:
    list_text = _extract_list_items_text(item)
    if list_text:
        return list_text

    if item_type == "image_caption":
        nested_caption = _extract_nested_caption_text(item)
        if nested_caption:
            return nested_caption

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

    if item_type == "image_semantic":
        semantic = _extract_generated_image_semantic_text(item)
        if semantic:
            return semantic

    for key in (
        "text",
        "content",
        "md",
        "markdown",
        "table_body",
        "latex",
        "formula",
        "caption",
        "image_caption",
        "image_footnote",
        "alt",
        "title",
    ):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_image_path(item: dict[str, Any]) -> str | None:
    for key in ("image_path", "img_path", "path", "src", "url", "file_path"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    content = item.get("content")
    if isinstance(content, dict):
        image_source = content.get("image_source")
        if isinstance(image_source, dict):
            nested = _extract_image_path(image_source)
            if nested:
                return nested
        for key in ("image_path", "img_path", "path", "src", "url", "file_path"):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    metadata = item.get("metadata") or item.get("meta")
    if isinstance(metadata, dict):
        return _extract_image_path(metadata)
    return None


def _image_fallback_text(image_path: str | None) -> str:
    if image_path:
        return f"Image file: {Path(image_path).name}"
    return "Image"


def _extract_generated_image_semantic_text(item: dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, dict):
        for key in ("content", "ocr_text", "text", "markdown", "md", "html", "table_body"):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(content, str) and content.strip():
        return content.strip()
    for key in ("text", "markdown", "md", "html", "table_body", "alt"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_nested_caption_text(item: dict[str, Any]) -> str:
    parts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                parts.append(stripped)
            return
        if isinstance(value, list):
            for sub in value:
                walk(sub)
            return
        if not isinstance(value, dict):
            return
        value_type = str(value.get("type") or "").lower()
        if value_type in {"text", "inline_equation", "equation_inline"}:
            content = value.get("content") or value.get("text")
            if isinstance(content, str) and content.strip():
                parts.append(content.strip())
        for key in ("content", "blocks", "lines", "spans", "children", "items"):
            nested = value.get(key)
            if isinstance(nested, (list, dict)):
                walk(nested)

    for key in ("content", "blocks", "lines", "spans"):
        value = item.get(key)
        if isinstance(value, (str, list, dict)):
            walk(value)
    return " ".join(part for part in parts if part).strip()


def _extract_embedded_image_captions(item: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    for key in ("image_caption", "figure_caption", "chart_caption", "caption"):
        if key in item:
            values.append(item.get(key))
    content = item.get("content")
    if isinstance(content, dict):
        for key in ("image_caption", "figure_caption", "chart_caption", "caption"):
            if key in content:
                values.append(content.get(key))
    captions: list[str] = []
    for value in values:
        captions.extend(_caption_strings(value))
    return _unique_strings([caption for caption in captions if caption])


def _caption_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, list):
        results: list[str] = []
        for item in value:
            results.extend(_caption_strings(item))
        return results
    if isinstance(value, dict):
        results: list[str] = []
        for key in ("text", "content", "caption"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                results.append(item.strip())
        return results
    return []


def _strip_mineru_markdown_image_blocks(markdown: str) -> str:
    without_details = _DETAILS_BLOCK_RE.sub("", str(markdown or ""))
    without_images = _IMAGE_MARKDOWN_RE.sub("", without_details)
    return without_images.strip()


def _write_mineru_assets_for_doc(
    assets: dict[str, bytes],
    *,
    file_path: Path,
    output_dir: Path,
) -> dict[str, str]:
    if not isinstance(assets, dict) or not assets:
        return {}
    doc_id = build_doc_id(str(file_path))
    root = output_dir / doc_id
    resolved: dict[str, str] = {}
    for raw_name, content in assets.items():
        if isinstance(content, bytes):
            data = content
        elif isinstance(content, bytearray):
            data = bytes(content)
        elif isinstance(content, memoryview):
            data = content.tobytes()
        elif isinstance(content, (str, Path)):
            source = Path(content)
            if not source.exists() or not source.is_file():
                continue
            data = source.read_bytes()
        else:
            continue
        safe_parts = _safe_asset_parts(str(raw_name))
        if not safe_parts:
            continue
        target = root.joinpath(*safe_parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        key = _normalize_asset_name(str(raw_name))
        value = str(target)
        resolved[key] = value
        resolved[Path(key).name] = value
    return resolved


def _safe_asset_parts(name: str) -> list[str]:
    normalized = _normalize_asset_name(name).strip("/")
    if not normalized:
        return []
    return [part for part in normalized.split("/") if part and part not in {".", ".."}]


def _normalize_asset_name(name: str) -> str:
    return str(name or "").replace("\\", "/").strip()


def _resolve_mineru_image_path(
    image_path: str | None,
    asset_paths: dict[str, str],
    *,
    base_dir: Path | None = None,
) -> str | None:
    if not image_path:
        return None
    raw = str(image_path).strip()
    if raw.startswith(("http://", "https://", "data:")):
        return raw
    normalized = _normalize_asset_name(image_path)
    if normalized in asset_paths:
        return asset_paths[normalized]
    basename = Path(normalized).name
    if basename in asset_paths:
        return asset_paths[basename]
    for key, value in asset_paths.items():
        if key.endswith("/" + normalized) or normalized.endswith("/" + key):
            return value
    direct = Path(raw)
    if direct.exists() and direct.is_file():
        return str(direct)
    if base_dir is not None:
        candidate = base_dir / normalized
        if candidate.exists() and candidate.is_file():
            return str(candidate)
    return None


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


def _extract_page_size(item: dict[str, Any]) -> tuple[float, float] | None:
    for key in ("page_size", "pageSize", "size"):
        page_size = _coerce_page_size(item.get(key))
        if page_size is not None:
            return page_size
    metadata = item.get("metadata") or item.get("meta")
    if isinstance(metadata, dict):
        return _extract_page_size(metadata)
    return None


def _coerce_page_size(value: Any) -> tuple[float, float] | None:
    if isinstance(value, dict):
        width = _coerce_float(value.get("width") or value.get("w"))
        height = _coerce_float(value.get("height") or value.get("h"))
        if width is not None and height is not None and width > 0 and height > 0:
            return float(width), float(height)
    if isinstance(value, list) and len(value) >= 2:
        width = _coerce_float(value[0])
        height = _coerce_float(value[1])
        if width is not None and height is not None and width > 0 and height > 0:
            return float(width), float(height)
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
    if raw in {"image_caption", "figure_caption", "chart_caption", "image_footnote", "chart_footnote"}:
        return "image_caption"
    if raw in {"table_caption", "table_footnote"}:
        return "text"
    if raw in {"page_number", "page_footer", "page_header"}:
        return "page_marker"
    if "formula" in raw or "equation" in raw:
        return "formula"
    if raw in {"chart", "figure", "graphic"}:
        return "image_semantic"
    if raw in {"table", "table_body", "html_table", "latex_table", "chart_table"}:
        return "table"
    if "title" in raw or "heading" in raw:
        return "heading"
    if "image" in raw or "figure" in raw:
        return "image"
    return "text"


def _is_composite_bbox_block(item_type: str, raw_type: Any) -> bool:
    raw = str(raw_type or "").strip().lower()
    if item_type in {"image", "image_semantic", "table"}:
        return True
    return any(token in raw for token in ("chart", "figure", "image", "table"))


def _union_composite_bbox(
    item: dict[str, Any],
    *,
    own_bbox: list[float] | None,
    own_coordinate_system: str | None,
    source_kind: str | None,
    page_size: tuple[float, float] | None,
) -> tuple[list[float] | None, str | None]:
    child_bboxes = _extract_nested_bboxes(
        item,
        source_kind=source_kind,
        page_size=page_size,
    )
    if not child_bboxes:
        return own_bbox, own_coordinate_system
    candidates = []
    if own_bbox is not None:
        candidates.append(own_bbox)
    candidates.extend(child_bboxes)
    if not candidates:
        return own_bbox, own_coordinate_system
    return _union_bboxes(candidates), own_coordinate_system or "mineru_pdf_points_top_left"


def _extract_nested_bboxes(
    item: dict[str, Any],
    *,
    source_kind: str | None,
    page_size: tuple[float, float] | None,
) -> list[list[float]]:
    results: list[list[float]] = []

    def walk(value: Any) -> None:
        if isinstance(value, list):
            for sub in value:
                walk(sub)
            return
        if not isinstance(value, dict):
            return
        bbox, coordinate_system = _extract_bbox_with_coordinate_system(
            value,
            source_kind=source_kind,
            page_size=page_size,
        )
        if bbox is not None and coordinate_system == "mineru_pdf_points_top_left":
            results.append(bbox)
        for key in ("children", "blocks", "items", "spans", "lines", "content"):
            nested = value.get(key)
            if isinstance(nested, (list, dict)):
                walk(nested)

    for key in ("children", "blocks", "items", "spans", "lines", "content"):
        nested = item.get(key)
        if isinstance(nested, (list, dict)):
            walk(nested)
    return results


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


def _text_level_raw_type(item: dict[str, Any]) -> str | None:
    value = item.get("text_level")
    if value in (None, "") or isinstance(value, bool):
        return None
    return f"text_level_{value}"


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
        if "pdf_info" in keys:
            return "pdf_info"
        if "pdfdata" in keys or "mergeconnections" in keys:
            return "block_list"
        if "model" in keys or "middle" in keys or "pages" in keys:
            return "model"
        if {"type", "table_body"} & keys or {"type", "page_idx"} <= keys:
            return "content_list"
        content = item.get("content")
        if isinstance(content, dict) and {"html", "table_type"} & {str(key).lower() for key in content}:
            return "model"
    if isinstance(item, list):
        sample = _sample_structured_dicts(item)
        if sample and any("table_body" in sub or "page_idx" in sub for sub in sample):
            return "content_list"
        bbox_values = [
            abs(float(coord))
            for sub in sample
            for coord in (_extract_bbox(sub) or [])
            if isinstance(coord, (int, float))
        ]
        if bbox_values:
            max_abs = max(bbox_values)
            if max_abs <= 1.5:
                return "model"
            if _looks_like_page_block_list(item):
                return "content_list_v2"
    return f"structured_json_{idx}"


def _source_kind_from_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.replace("\\", "/").lower()
    if "content_list_v2" in name:
        return "content_list_v2"
    if "content_list" in name:
        return "content_list"
    if "pdf_info" in name:
        return "pdf_info"
    if "layout" in name:
        return "layout"
    if "block_list" in name:
        return "block_list"
    if "middle" in name or "model" in name:
        return "model"
    return None


def _sample_structured_dicts(value: Any, *, limit: int = 12) -> list[dict[str, Any]]:
    sample: list[dict[str, Any]] = []

    def walk(item: Any) -> None:
        if len(sample) >= limit:
            return
        if isinstance(item, dict):
            sample.append(item)
            return
        if isinstance(item, list):
            for sub in item:
                walk(sub)
                if len(sample) >= limit:
                    return

    walk(value)
    return sample


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


def _extract_bbox_with_coordinate_system(
    item: dict[str, Any],
    *,
    source_kind: str | None,
    page_size: tuple[float, float] | None,
) -> tuple[list[float] | None, str | None]:
    bbox = _extract_bbox(item)
    if bbox is None:
        return None, None
    return _normalize_mineru_bbox_units(bbox, source_kind=source_kind, page_size=page_size)


def _extract_bbox(item: dict[str, Any]) -> list[float] | None:
    for key in ("bbox", "box", "bounding_box", "position"):
        bbox = _coerce_bbox(item.get(key))
        if bbox is not None:
            return bbox
    metadata = item.get("metadata") or item.get("meta")
    if isinstance(metadata, dict):
        return _extract_bbox(metadata)
    return None


def _normalize_mineru_bbox_units(
    bbox: list[float],
    *,
    source_kind: str | None,
    page_size: tuple[float, float] | None,
) -> tuple[list[float], str]:
    values = [float(value) for value in bbox[:4]]
    source = str(source_kind or "").lower()
    max_abs = max((abs(value) for value in values), default=0.0)
    if page_size is not None:
        width, height = page_size
        if source == "model" or max_abs <= 1.5:
            return _scale_bbox(values, x_scale=width, y_scale=height), "mineru_pdf_points_top_left"
        if source in {"content_list", "content_list_v2"}:
            return _scale_bbox(values, x_scale=width / 1000.0, y_scale=height / 1000.0), (
                "mineru_pdf_points_top_left"
            )
        if source in {"layout", "pdf_info", "block_list"}:
            return values, "mineru_pdf_points_top_left"

    if source == "model" or max_abs <= 1.5:
        return [round(value * 1000.0, 3) for value in values], "mineru_1000_unscaled"
    if source in {"content_list", "content_list_v2"}:
        return values, "mineru_content_list_1000_unscaled"
    if source in {"layout", "pdf_info", "block_list"}:
        return values, "mineru_pdf_points_top_left"
    return values, "mineru_raw"


def _scale_bbox(values: list[float], *, x_scale: float, y_scale: float) -> list[float]:
    return [
        round(values[0] * x_scale, 3),
        round(values[1] * y_scale, 3),
        round(values[2] * x_scale, 3),
        round(values[3] * y_scale, 3),
    ]


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
        "content_list": 130,
        "content_list_v2": 125,
        "pdf_info": 110,
        "layout": 105,
        "block_list": 105,
        "model": 80,
        "nested_content": 40,
        "unknown": 10,
    }
    return priorities.get(str(source_kind or "unknown"), 10)


def _infer_block_for_text(text: str, blocks: list[MinerUStructuredBlock]) -> MinerUStructuredBlock | None:
    matches = _infer_blocks_for_text(text, blocks)
    return _best_block(matches)


def _select_structured_text_blocks_for_chunking(blocks: list[MinerUStructuredBlock]) -> list[MinerUStructuredBlock]:
    candidates = [block for block in blocks if block.type in {"text", "heading", "formula"} and block.page is not None]
    candidates = [block for block in candidates if not _skip_structured_text_block(block)]
    candidates = [block for block in candidates if _compact_text(block.text)]
    if not candidates:
        return []

    grouped: dict[str, list[MinerUStructuredBlock]] = {}
    for block in candidates:
        grouped.setdefault(str(block.source_kind or "unknown"), []).append(block)

    def group_quality(item: tuple[str, list[MinerUStructuredBlock]]) -> tuple[int, int, int]:
        source_kind, group = item
        with_bbox = sum(1 for block in group if block.bbox is not None)
        return (_source_priority(source_kind), with_bbox, len(group))

    _source_kind, selected = max(grouped.items(), key=group_quality)
    return selected


def _skip_structured_text_block(block: MinerUStructuredBlock) -> bool:
    raw = str(block.raw_type or "").strip().lower()
    if raw in {"page_number", "page_header", "page_footer", "chart"}:
        return True
    text = str(block.text or "").strip()
    if raw in {"table_caption", "table_footnote"}:
        return False
    if text.isdigit() and len(text) <= 4:
        return True
    return False


def _is_structured_table_node_source(block: MinerUStructuredBlock) -> bool:
    raw = str(block.raw_type or "").strip().lower()
    if raw in {"table_caption", "table_footnote", "chart"}:
        return False
    if raw in {"table", "table_body", "html_table", "latex_table", "chart_table"}:
        return _table_exact_content_key(block.text) != ()
    return block.type == "table" and _table_exact_content_key(block.text) != ()


def _select_structured_table_blocks_for_nodes(
    blocks: list[MinerUStructuredBlock],
) -> list[MinerUStructuredBlock]:
    if not blocks:
        return []
    grouped: dict[str, list[MinerUStructuredBlock]] = {}
    for block in blocks:
        grouped.setdefault(str(block.source_kind or "unknown"), []).append(block)

    def group_quality(item: tuple[str, list[MinerUStructuredBlock]]) -> tuple[int, int, int]:
        source_kind, group = item
        with_page = sum(1 for block in group if block.page is not None)
        return (_source_priority(source_kind), with_page, len(group))

    _source_kind, selected = max(grouped.items(), key=group_quality)
    return selected


def _select_structured_image_blocks_for_nodes(
    blocks: list[MinerUStructuredBlock],
) -> list[list[MinerUStructuredBlock]]:
    if not blocks:
        return []
    groups: list[list[MinerUStructuredBlock]] = []
    current: list[MinerUStructuredBlock] = []
    for block in sorted(blocks, key=lambda item: item.order):
        if block.type != "image" or not (block.text or block.image_path):
            if current and _is_retrievable_image_group(current):
                groups.append(current)
            current = []
            continue
        if current and not _same_image_group(current[-1], block):
            if _is_retrievable_image_group(current):
                groups.append(current)
            current = [block]
            continue
        current.append(block)
    if current:
        if _is_retrievable_image_group(current):
            groups.append(current)
    return _dedupe_image_groups(groups)


def _extract_mineru_image_units(
    blocks: list[MinerUStructuredBlock],
    *,
    markdown: str,
    asset_paths: dict[str, str],
) -> list[MinerUImageUnit]:
    markdown_units = _image_units_from_markdown(markdown, asset_paths=asset_paths)
    structured_units = _image_units_from_structured_blocks(blocks, asset_paths=asset_paths)
    if any(unit.semantic_blocks for unit in markdown_units):
        structured_units = [
            unit
            for unit in structured_units
            if unit.image_path or not unit.semantic_blocks
        ]
    units = [*structured_units, *markdown_units]
    return _dedupe_image_units(units)


def _image_units_from_structured_blocks(
    blocks: list[MinerUStructuredBlock],
    *,
    asset_paths: dict[str, str],
) -> list[MinerUImageUnit]:
    units: list[MinerUImageUnit] = []
    by_path: dict[str, MinerUImageUnit] = {}
    pending_captions: list[MinerUStructuredBlock] = []
    last_path_unit: MinerUImageUnit | None = None

    def flush_pending_captions() -> None:
        nonlocal pending_captions
        for caption in pending_captions:
            unit = MinerUImageUnit(
                image_path=None,
                caption_blocks=[],
                semantic_blocks=[],
                image_blocks=[],
                semantic_kind=None,
            )
            _append_image_unit_block(unit, caption)
            units.append(unit)
        pending_captions = []

    for block in sorted(blocks, key=lambda item: item.order):
        if block.type not in {"image", "image_caption", "image_semantic"} and not block.image_path:
            if block.type != "page_number":
                flush_pending_captions()
                last_path_unit = None
            continue
        resolved_path = _resolve_mineru_image_path(block.image_path, asset_paths)
        if block.type == "image_caption" and not resolved_path:
            if (
                last_path_unit is not None
                and _image_caption_can_attach_to_unit(block, last_path_unit)
            ):
                _append_image_unit_block(last_path_unit, block)
            else:
                flush_pending_captions()
                pending_captions = [block]
            continue
        if resolved_path:
            unit = by_path.get(resolved_path)
            if unit is None:
                unit = MinerUImageUnit(
                    image_path=resolved_path,
                    caption_blocks=[],
                    semantic_blocks=[],
                    image_blocks=[],
                    semantic_kind=None,
                )
                by_path[resolved_path] = unit
                units.append(unit)
            _append_image_unit_block(unit, block)
            for caption in pending_captions[-1:]:
                if _image_caption_can_attach_to_unit(caption, unit, fallback_page=block.page):
                    _append_image_unit_block(unit, caption)
            pending_captions = []
            if block.type in {"image", "image_semantic"}:
                last_path_unit = unit
            continue

        # Pathless MinerU captions/details are still useful textual evidence, but they
        # are kept as standalone units so they do not get merged with unrelated images.
        unit = MinerUImageUnit(
            image_path=None,
            caption_blocks=[],
            semantic_blocks=[],
            image_blocks=[],
            semantic_kind=None,
        )
        _append_image_unit_block(unit, block)
        units.append(unit)
        last_path_unit = None
    flush_pending_captions()
    return units


def _image_caption_can_attach_to_unit(
    caption: MinerUStructuredBlock,
    unit: MinerUImageUnit,
    *,
    fallback_page: int | None = None,
) -> bool:
    if not _image_caption_adjacent_to_unit(caption, unit, fallback_page=fallback_page):
        return False
    existing = unit.caption_blocks or []
    if not existing:
        return True

    caption_label = _caption_figure_label(caption.text)
    existing_labels = {_caption_figure_label(block.text) for block in existing}
    existing_labels.discard(None)
    if caption_label and existing_labels:
        return caption_label in existing_labels
    if caption_label and not existing_labels:
        return _source_priority(caption.source_kind) > max(_source_priority(block.source_kind) for block in existing)
    return False


def _image_caption_adjacent_to_unit(
    caption: MinerUStructuredBlock,
    unit: MinerUImageUnit,
    *,
    fallback_page: int | None = None,
    max_order_gap: int = 5,
) -> bool:
    pages = {block.page for block in unit.blocks if block.page is not None}
    page = caption.page if caption.page is not None else fallback_page
    if page is None or not pages:
        page_ok = True
    else:
        page_ok = page in pages
    if not page_ok:
        return False
    image_orders = [
        block.order
        for block in [*(unit.image_blocks or []), *(unit.semantic_blocks or [])]
        if block.image_path
    ]
    if not image_orders:
        return False
    return min(abs(caption.order - order) for order in image_orders) <= max_order_gap


def _caption_figure_label(text: str) -> str | None:
    match = re.search(r"\b(?:fig(?:ure)?\.?)\s*([0-9]+[A-Za-z]?)\b", str(text or ""), flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).lower()


def _append_image_unit_block(unit: MinerUImageUnit, block: MinerUStructuredBlock) -> None:
    if block.type == "image_caption":
        if unit.caption_blocks is None:
            unit.caption_blocks = []
        unit.caption_blocks.append(block)
    elif block.type == "image_semantic":
        if unit.semantic_blocks is None:
            unit.semantic_blocks = []
        unit.semantic_blocks.append(block)
        if unit.semantic_kind is None:
            unit.semantic_kind = _infer_image_semantic_type([block])
    else:
        if unit.image_blocks is None:
            unit.image_blocks = []
        unit.image_blocks.append(block)


def _image_units_from_markdown(markdown: str, *, asset_paths: dict[str, str]) -> list[MinerUImageUnit]:
    units: list[MinerUImageUnit] = []
    text = str(markdown or "")
    for match in _IMAGE_MARKDOWN_RE.finditer(text):
        raw_path = match.group(1).strip()
        resolved_path = _resolve_mineru_image_path(raw_path, asset_paths)
        image_block = MinerUStructuredBlock(
            type="image",
            text=_image_fallback_text(raw_path),
            image_path=resolved_path,
            source_kind="markdown",
            raw_type="markdown_image",
            order=match.start(),
        )
        caption_text = _markdown_caption_before(text, match.start())
        if not caption_text:
            caption_text = _markdown_caption_after_image_block(text, match.end())
        caption_blocks = []
        if caption_text:
            caption_blocks.append(
                MinerUStructuredBlock(
                    type="image_caption",
                    text=caption_text,
                    image_path=resolved_path,
                    source_kind="markdown",
                    raw_type="markdown_image_caption",
                    order=max(0, match.start() - 1),
                )
            )
        details_text, summary = _markdown_details_after(text, match.end())
        semantic_blocks = []
        if details_text:
            semantic_blocks.append(
                MinerUStructuredBlock(
                    type="image_semantic",
                    text=details_text,
                    image_path=resolved_path,
                    source_kind="markdown",
                    raw_type=f"markdown_details_{summary}" if summary else "markdown_details",
                    order=match.end(),
                )
            )
        units.append(
            MinerUImageUnit(
                image_path=resolved_path,
                caption_blocks=caption_blocks,
                semantic_blocks=semantic_blocks,
                image_blocks=[image_block],
                semantic_kind=_semantic_type_from_summary(summary) if summary else None,
            )
        )
    return units


def _dedupe_image_units(units: list[MinerUImageUnit]) -> list[MinerUImageUnit]:
    keyed: dict[str, MinerUImageUnit] = {}
    ordered_keys: list[str] = []
    for unit in units:
        key = _image_unit_key(unit)
        if not key:
            continue
        existing = keyed.get(key)
        if existing is None:
            keyed[key] = unit
            ordered_keys.append(key)
            continue
        _merge_image_unit(existing, unit)

    path_units = [unit for unit in keyed.values() if unit.image_path]
    result: list[MinerUImageUnit] = []
    for key in ordered_keys:
        unit = keyed[key]
        if not unit.image_path:
            linked = _matching_path_image_unit_for_caption(unit, path_units)
            if linked is not None:
                _merge_image_unit(linked, unit)
                continue
        result.append(unit)
    return result


def _matching_path_image_unit_for_caption(
    standalone: MinerUImageUnit,
    path_units: list[MinerUImageUnit],
) -> MinerUImageUnit | None:
    caption_text = _join_unique_block_texts(standalone.caption_blocks or [])
    if not caption_text:
        return None
    scored: list[tuple[float, MinerUImageUnit]] = []
    for candidate in path_units:
        if not _image_unit_pages_compatible(standalone, candidate):
            continue
        candidate_caption = _join_unique_block_texts(candidate.caption_blocks or [])
        score = _caption_similarity_score(caption_text, candidate_caption)
        if score >= 0.82:
            scored.append((score, candidate))
    if not scored:
        return None
    return max(scored, key=lambda item: item[0])[1]


def _image_unit_pages_compatible(left: MinerUImageUnit, right: MinerUImageUnit) -> bool:
    left_pages = {block.page for block in left.blocks if block.page is not None}
    right_pages = {block.page for block in right.blocks if block.page is not None}
    if not left_pages or not right_pages:
        return True
    return bool(left_pages & right_pages)


def _caption_similarity_score(left: str, right: str) -> float:
    left_norm = _normalize_caption_for_dedupe(left)
    right_norm = _normalize_caption_for_dedupe(right)
    if not left_norm or not right_norm:
        return 0.0
    left_label = _figure_label_for_caption(left_norm)
    right_label = _figure_label_for_caption(right_norm)
    if left_label and right_label and left_label != right_label:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    shorter, longer = sorted((left_norm, right_norm), key=len)
    if len(shorter) >= 48 and shorter in longer:
        return 0.95
    left_tokens = set(left_norm.split())
    right_tokens = set(right_norm.split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens)))


def _normalize_caption_for_dedupe(text: str) -> str:
    value = str(text or "").lower()
    value = re.sub(r"\\[()[\]]", " ", value)
    value = re.sub(r"[$^_{}\\]", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def _figure_label_for_caption(normalized: str) -> str | None:
    match = re.search(r"\b(?:supplemental figure|figure|fig)\s*([a-z0-9]+)", normalized)
    if match:
        return match.group(1)
    return None


def _image_unit_key(unit: MinerUImageUnit) -> str:
    if unit.image_path:
        return "path:" + _normalize_asset_name(unit.image_path)
    fingerprint = _compact_text(
        _join_unique_block_texts(unit.semantic_blocks or []) + _join_unique_block_texts(unit.caption_blocks or [])
    )
    if fingerprint:
        return "text:" + fingerprint[:200]
    blocks = unit.blocks
    if blocks:
        return f"block:{blocks[0].source_kind}:{blocks[0].order}"
    return ""


def _merge_image_unit(target: MinerUImageUnit, source: MinerUImageUnit) -> None:
    if not target.image_path and source.image_path:
        target.image_path = source.image_path
    target.caption_blocks = _unique_blocks([*(target.caption_blocks or []), *(source.caption_blocks or [])])
    target.semantic_blocks = _unique_blocks([*(target.semantic_blocks or []), *(source.semantic_blocks or [])])
    target.image_blocks = _unique_blocks([*(target.image_blocks or []), *(source.image_blocks or [])])
    if not target.semantic_kind:
        target.semantic_kind = source.semantic_kind


def _select_image_caption_blocks(blocks: list[MinerUStructuredBlock]) -> list[MinerUStructuredBlock]:
    if not blocks:
        return []
    unique = _unique_blocks(blocks)
    scored = []
    for block in unique:
        text = str(block.text or "")
        score = (
            1 if _looks_like_image_caption(text) else 0,
            1 if block.bbox is not None else 0,
            _source_priority(block.source_kind),
            -abs(len(text) - 300),
            -block.order,
        )
        scored.append((score, block))
    return [max(scored, key=lambda item: item[0])[1]]


def _select_image_semantic_blocks(blocks: list[MinerUStructuredBlock]) -> list[MinerUStructuredBlock]:
    if not blocks:
        return []
    unique = _unique_blocks(blocks)
    scored = []
    for block in unique:
        raw_type = str(block.raw_type or "").lower()
        score = (
            1 if block.source_kind == "markdown" else 0,
            1 if raw_type.startswith("markdown_details") else 0,
            1 if block.bbox is not None else 0,
            _source_priority(block.source_kind),
            -block.order,
        )
        scored.append((score, block))
    return [max(scored, key=lambda item: item[0])[1]]


def _unique_blocks(blocks: list[MinerUStructuredBlock]) -> list[MinerUStructuredBlock]:
    seen: set[tuple[str | None, int, str]] = set()
    unique: list[MinerUStructuredBlock] = []
    for block in blocks:
        key = (block.source_kind, block.order, _compact_text(block.text))
        if key in seen:
            continue
        seen.add(key)
        unique.append(block)
    return sorted(unique, key=lambda item: item.order)


def _markdown_caption_before(markdown: str, image_start: int) -> str:
    before = markdown[:image_start].rstrip()
    if not before:
        return ""
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n+", before) if item.strip()]
    if not paragraphs:
        return ""
    candidate = paragraphs[-1]
    if candidate.startswith("#"):
        return ""
    if not _looks_like_image_caption(candidate):
        return ""
    return candidate


def _markdown_caption_after_image_block(markdown: str, image_end: int) -> str:
    tail = markdown[image_end:]
    tail = tail[len(tail) - len(tail.lstrip()) :]
    details = _DETAILS_BLOCK_RE.match(tail)
    if details:
        details_text = details.group(0)
        # Long generated descriptions usually describe a standalone image block;
        # the following paragraph is often the next figure caption, not this image.
        if len(details_text) > 1500:
            return ""
        tail = tail[details.end() :]
        tail = tail[len(tail) - len(tail.lstrip()) :]
    next_image = _IMAGE_MARKDOWN_RE.search(tail)
    boundary = next_image.start() if next_image else len(tail)
    candidate_region = tail[:boundary].strip()
    if not candidate_region:
        return ""
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n+", candidate_region) if item.strip()]
    if not paragraphs:
        return ""
    candidate = paragraphs[0]
    if candidate.startswith("#") or not _looks_like_image_caption(candidate):
        return ""
    return candidate


def _looks_like_image_caption(text: str) -> bool:
    value = str(text or "").strip()
    if len(value) < 6:
        return False
    return bool(re.search(r"\b(fig\.?|figure|supplemental figure|图)\b", value, flags=re.IGNORECASE))


def _markdown_details_after(markdown: str, image_end: int) -> tuple[str, str | None]:
    tail = markdown[image_end:]
    leading = len(tail) - len(tail.lstrip())
    tail = tail[leading:]
    match = _DETAILS_BLOCK_RE.match(tail)
    if not match:
        return "", None
    raw = match.group(0)
    summary_match = _DETAILS_SUMMARY_RE.search(raw)
    summary = _compact_text(summary_match.group(1)).lower() if summary_match else None
    body = _DETAILS_SUMMARY_RE.sub("", raw)
    body = re.sub(r"</?details\b[^>]*>", "", body, flags=re.IGNORECASE).strip()
    return body, summary


def _join_unique_block_texts(blocks: list[MinerUStructuredBlock]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for block in sorted(blocks, key=lambda item: item.order):
        text = str(block.text or "").strip()
        if not text:
            continue
        compact = _compact_text(text)
        if compact in seen:
            continue
        seen.add(compact)
        parts.append(text)
    return "\n\n".join(parts).strip()


def _page_for_blocks(blocks: list[MinerUStructuredBlock]) -> int | None:
    for block in blocks:
        if block.page is not None:
            return block.page
    return None


def _whole_image_text(image_path: str, *, caption_text: str, semantic_text: str) -> str:
    parts = [f"Image file: {Path(image_path).name}"]
    if caption_text:
        parts.append(caption_text)
    elif semantic_text:
        parts.append(_details_summary(semantic_text, max_chars=500))
    return "\n\n".join(parts).strip()


def _image_semantic_text(*, caption_text: str, semantic_text: str) -> str:
    return str(semantic_text or "").strip()


def _details_summary(text: str, max_chars: int = 500) -> str:
    compact = " ".join(str(text or "").split())
    return compact[:max_chars]


def _infer_image_semantic_type(blocks: list[MinerUStructuredBlock]) -> str:
    raw = " ".join(str(block.raw_type or "") for block in blocks).lower()
    if "line" in raw or "chart" in raw or "table" in raw or "|" in _join_unique_block_texts(blocks):
        return "chart_data"
    return IMAGE_SEMANTIC_OCR


def _semantic_type_from_summary(summary: str | None) -> str:
    value = str(summary or "").lower()
    if value in {"line", "bar", "pie", "scatter", "contour", "radar", "surface_3d", "flowchart"}:
        return "chart_data"
    if value in {"natural_image", "other"}:
        return IMAGE_SEMANTIC_OCR
    return IMAGE_SEMANTIC_OCR


def _mineru_image_unit_relationships(
    unit: MinerUImageUnit,
    *,
    role: str,
    semantic_type: str,
) -> dict[str, Any]:
    blocks = unit.blocks
    if role == "caption_text" and unit.caption_blocks:
        first = _best_image_unit_block(unit.caption_blocks)
    elif role == "image_semantic" and unit.semantic_blocks:
        first = _best_image_unit_block(unit.semantic_blocks)
    elif role == "whole_image" and unit.image_blocks:
        first = _best_image_unit_block(unit.image_blocks)
    else:
        first = _best_image_unit_block(blocks)
    relationships = _mineru_relationships(first)
    relationships["mineru_block_type"] = "image"
    relationships["mineru_image_role"] = role
    relationships["image_semantic_type"] = semantic_type
    relationships["mineru_image_block_count"] = len(blocks)
    relationships["mineru_image_block_ids"] = [block.block_id for block in blocks if block.block_id]
    relationships["mineru_image_block_types"] = [block.type for block in blocks]
    relationships["mineru_image_raw_types"] = [block.raw_type for block in blocks if block.raw_type]
    relationships["mineru_image_source_kinds"] = _unique_strings(
        [str(block.source_kind) for block in blocks if block.source_kind]
    )
    if unit.image_path:
        relationships["mineru_image_path"] = unit.image_path
    if unit.caption_blocks:
        relationships["caption"] = _join_unique_block_texts(unit.caption_blocks)
    if unit.semantic_blocks:
        relationships["mineru_generated_semantic"] = True
    return relationships


def _image_unit_location_blocks(
    *groups: list[MinerUStructuredBlock],
) -> list[MinerUStructuredBlock]:
    candidates: list[MinerUStructuredBlock] = []
    for group in groups:
        candidates.extend(group or [])
    candidates = _unique_blocks(candidates)
    located = [block for block in candidates if block.page is not None or block.bbox is not None]
    return located or candidates


def _whole_image_location_blocks(
    image_blocks: list[MinerUStructuredBlock],
    semantic_blocks: list[MinerUStructuredBlock],
    caption_blocks: list[MinerUStructuredBlock],
    fallback_blocks: list[MinerUStructuredBlock],
) -> list[MinerUStructuredBlock]:
    located_images = [block for block in _unique_blocks(image_blocks) if block.bbox is not None or block.page is not None]
    located_images = _prefer_direct_image_path_blocks(located_images)
    layout_images = [
        block for block in located_images if str(block.source_kind or "").lower() in {"pdf_info", "layout", "block_list"}
    ]
    if layout_images:
        located_images = layout_images
    located_images = _select_consensus_bbox_blocks(located_images)
    model_images = [block for block in located_images if str(block.source_kind or "").lower() == "model"]
    if model_images:
        return model_images
    if located_images:
        return located_images
    located_semantic_images = [
        block
        for block in _unique_blocks(semantic_blocks)
        if block.image_path and (block.bbox is not None or block.page is not None)
    ]
    located_semantic_images = _prefer_direct_image_path_blocks(located_semantic_images)
    layout_semantic_images = [
        block
        for block in located_semantic_images
        if str(block.source_kind or "").lower() in {"pdf_info", "layout", "block_list"}
    ]
    if layout_semantic_images:
        located_semantic_images = layout_semantic_images
    located_semantic_images = _select_consensus_bbox_blocks(located_semantic_images)
    if located_semantic_images:
        return located_semantic_images
    return _image_unit_location_blocks(semantic_blocks, caption_blocks, fallback_blocks)


def _caption_location_blocks(
    caption_blocks: list[MinerUStructuredBlock],
    image_blocks: list[MinerUStructuredBlock],
    semantic_blocks: list[MinerUStructuredBlock],
    fallback_blocks: list[MinerUStructuredBlock],
) -> list[MinerUStructuredBlock]:
    located_captions = [
        block for block in _unique_blocks(caption_blocks) if block.bbox is not None or block.page is not None
    ]
    if located_captions:
        return located_captions
    return _image_unit_location_blocks(image_blocks, semantic_blocks, fallback_blocks)


def _prefer_direct_image_path_blocks(blocks: list[MinerUStructuredBlock]) -> list[MinerUStructuredBlock]:
    direct = [
        block
        for block in blocks
        if block.image_path and "/" not in _normalize_asset_name(block.image_path).strip("/")
    ]
    return direct or blocks


def _select_consensus_bbox_blocks(blocks: list[MinerUStructuredBlock]) -> list[MinerUStructuredBlock]:
    located = [block for block in blocks if block.bbox is not None]
    if len(located) <= 1:
        return blocks
    grouped: dict[tuple[float, float, float, float], list[MinerUStructuredBlock]] = {}
    for block in located:
        key = tuple(round(coord, 3) for coord in _normalize_bbox(block.bbox or [0, 0, 0, 0]))
        grouped.setdefault(key, []).append(block)
    if len(grouped) <= 1:
        return blocks

    def group_key(item: tuple[tuple[float, float, float, float], list[MinerUStructuredBlock]]) -> tuple[int, float]:
        bbox, group = item
        x0, y0, x1, y1 = bbox
        area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        return (len(group), -area)

    _bbox, selected = max(grouped.items(), key=group_key)
    if selected:
        return selected
    return blocks


def _apply_location_to_relationships(
    relationships: dict[str, Any],
    blocks: list[MinerUStructuredBlock],
) -> None:
    page = _page_for_blocks(blocks)
    if page is not None:
        relationships["page"] = page
        relationships["page_node_id"] = f"page:{page}"
        relationships["page_source"] = "structured"

    bbox_payload = _mineru_bbox_payload(blocks)
    for key in (
        "bbox",
        "bbox_items",
        "pages",
        "bbox_by_page",
        "page_spans",
        "bbox_coordinate_system",
        "bbox_source",
        "bbox_merge_policy",
    ):
        value = bbox_payload.get(key)
        if value is not None:
            relationships[key] = value


def _best_image_unit_block(blocks: list[MinerUStructuredBlock]) -> MinerUStructuredBlock | None:
    if not blocks:
        return None
    return max(
        blocks,
        key=lambda block: (
            1 if block.page is not None else 0,
            1 if block.bbox is not None else 0,
            _source_priority(block.source_kind),
            len(block.text or ""),
        ),
    )


def _dedupe_image_groups(
    groups: list[list[MinerUStructuredBlock]],
) -> list[list[MinerUStructuredBlock]]:
    keyed: dict[str, list[MinerUStructuredBlock]] = {}
    ordered_keys: list[str] = []
    for group in groups:
        key = _image_group_dedupe_key(group)
        if not key:
            key = f"order:{group[0].order if group else len(ordered_keys)}"
        existing = keyed.get(key)
        if existing is None:
            keyed[key] = group
            ordered_keys.append(key)
            continue
        if _image_group_quality_key(group) > _image_group_quality_key(existing):
            keyed[key] = group
    return [keyed[key] for key in ordered_keys]


def _image_group_dedupe_key(blocks: list[MinerUStructuredBlock]) -> str:
    image_path = next((block.image_path for block in blocks if block.image_path), None)
    if image_path:
        return f"path:{image_path.replace(chr(92), '/')}"
    return _compact_text(_image_group_body_text(blocks))


def _image_group_quality_key(blocks: list[MinerUStructuredBlock]) -> tuple[int, int, int, int, int]:
    return (
        sum(1 for block in blocks if block.page is not None),
        sum(1 for block in blocks if block.image_path),
        sum(1 for block in blocks if block.bbox is not None),
        max((_source_priority(block.source_kind) for block in blocks), default=0),
        len(_compact_text(_image_group_body_text(blocks))),
    )


def _same_image_group(left: MinerUStructuredBlock, right: MinerUStructuredBlock) -> bool:
    if (left.page is None) != (right.page is None):
        return False
    if left.page is not None and right.page is not None and left.page != right.page:
        return False
    if (
        left.title_region_id is not None
        and right.title_region_id is not None
        and left.title_region_id != right.title_region_id
    ):
        return False
    return True


def _is_retrievable_image_group(blocks: list[MinerUStructuredBlock]) -> bool:
    if any(block.image_path for block in blocks):
        return True
    text = _image_group_body_text(blocks)
    compact = _compact_text(text)
    if len(compact) >= 12:
        return True
    return len(blocks) > 1 and len(compact) >= 4


def _build_mineru_image_text(blocks: list[MinerUStructuredBlock]) -> str:
    body = _image_group_body_text(blocks)
    if not body:
        return ""
    section = _section_for_image_blocks(blocks)
    if section and _compact_text(section) not in _compact_text(body):
        return f"# {section}\n\n{body}".strip()
    return body


def _image_group_body_text(blocks: list[MinerUStructuredBlock]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for block in blocks:
        text = _block_to_markdown(block).strip()
        if not text:
            continue
        compact = _compact_text(text)
        if compact and compact not in seen:
            parts.append(text)
            seen.add(compact)
    return "\n\n".join(parts).strip()


def _section_for_image_blocks(blocks: list[MinerUStructuredBlock]) -> str | None:
    for block in blocks:
        if block.section:
            return block.section
    for block in blocks:
        if block.type == "heading" and block.text.strip():
            return block.text.strip().lstrip("#").strip()
    return _section_from_markdown(_image_group_body_text(blocks))


def _assemble_logical_text_segments(
    blocks: list[MinerUStructuredBlock],
    all_blocks: list[MinerUStructuredBlock],
    markdown: str,
    *,
    max_chars: int,
) -> list[MinerULogicalSegment]:
    if not blocks:
        return []

    stream = sorted(blocks, key=lambda block: block.order)
    _assign_title_regions(stream)
    source_kind = stream[0].source_kind
    boundary_blocks = [
        block
        for block in all_blocks
        if source_kind is None or block.source_kind == source_kind
    ] or all_blocks
    first_body_by_page, last_body_by_page = _body_boundary_blocks_by_page(stream)
    markdown_paragraphs = _markdown_paragraph_signatures(markdown)

    segments: list[MinerULogicalSegment] = []
    current: list[MinerUStructuredBlock] = []
    pending_headings: list[MinerUStructuredBlock] = []
    current_cross_page = False
    current_reason: str | None = None

    def flush_current() -> None:
        nonlocal current, current_cross_page, current_reason
        if current:
            segments.append(
                MinerULogicalSegment(
                    blocks=current,
                    cross_page_merge=current_cross_page,
                    merge_reason=current_reason,
                )
            )
        current = []
        current_cross_page = False
        current_reason = None

    for block in stream:
        if block.type == "heading":
            flush_current()
            pending_headings.append(block)
            continue

        block_text = _block_to_markdown(block)
        if not block_text:
            continue

        if pending_headings and block.type != "text":
            pending_headings = []
        if not current and pending_headings:
            current.extend(pending_headings)
            pending_headings = []

        if current:
            last = current[-1]
            if _title_region_id(last) != _title_region_id(block):
                flush_current()
                if pending_headings and block.type == "text":
                    current.extend(pending_headings)
                    pending_headings = []
                elif pending_headings:
                    pending_headings = []
                current.append(block)
                continue
            projected = len(_blocks_to_markdown([*current, block]))
            current_is_heading_only = all(item.type == "heading" for item in current)
            cross_page_allowed, reason = _should_merge_cross_page_text(
                last,
                block,
                boundary_blocks,
                markdown_paragraphs,
                first_body_by_page,
                last_body_by_page,
            )
            if last.page != block.page and not cross_page_allowed:
                flush_current()
                if pending_headings and block.type == "text":
                    current.extend(pending_headings)
                    pending_headings = []
                elif pending_headings:
                    pending_headings = []
            elif last.page == block.page and projected > max_chars and not current_is_heading_only:
                flush_current()
                if pending_headings and block.type == "text":
                    current.extend(pending_headings)
                    pending_headings = []
                elif pending_headings:
                    pending_headings = []
            elif cross_page_allowed:
                current_cross_page = True
                current_reason = reason

        current.append(block)

    flush_current()
    return segments


def _assign_title_regions(blocks: list[MinerUStructuredBlock]) -> None:
    """Mark heading-started regions so chunks do not straddle section titles."""
    current_region_id: int | None = None
    next_region_id = 1
    in_heading_run = False
    for block in blocks:
        if block.type == "heading":
            if not in_heading_run:
                current_region_id = next_region_id
                next_region_id += 1
            block.title_region_id = current_region_id
            in_heading_run = True
            continue
        in_heading_run = False
        block.title_region_id = current_region_id


def _assign_heading_context(blocks: list[MinerUStructuredBlock]) -> None:
    pending_heading: str | None = None
    pending_heading_region: int | None = None
    pending_heading_block_id: str | None = None
    for block in sorted(blocks, key=lambda item: item.order):
        if block.type == "heading":
            heading = _normalize_heading_text(block.text)
            if heading:
                pending_heading = heading
                pending_heading_region = block.title_region_id
                pending_heading_block_id = block.block_id
            continue
        if block.type != "text":
            pending_heading = None
            pending_heading_region = None
            pending_heading_block_id = None
            continue
        if pending_heading and not block.section:
            block.section = pending_heading
        if pending_heading_block_id:
            block.heading_context_block_id = pending_heading_block_id
        if pending_heading_region is not None and block.title_region_id is None:
            block.title_region_id = pending_heading_region
        pending_heading = None
        pending_heading_region = None
        pending_heading_block_id = None


def _normalize_heading_text(text: str) -> str:
    normalized = str(text or "").strip()
    if normalized.startswith("#"):
        normalized = normalized.lstrip("#").strip()
    return normalized


def _title_region_id(block: MinerUStructuredBlock | None) -> int | None:
    return block.title_region_id if block else None


def _body_boundary_blocks_by_page(
    blocks: list[MinerUStructuredBlock],
) -> tuple[dict[int, MinerUStructuredBlock], dict[int, MinerUStructuredBlock]]:
    first: dict[int, MinerUStructuredBlock] = {}
    last: dict[int, MinerUStructuredBlock] = {}
    for block in sorted(blocks, key=lambda item: item.order):
        if block.page is None or block.type not in _BODY_CONTINUATION_TYPES:
            continue
        first.setdefault(block.page, block)
        last[block.page] = block
    return first, last


def _should_merge_cross_page_text(
    previous: MinerUStructuredBlock,
    current: MinerUStructuredBlock,
    all_blocks: list[MinerUStructuredBlock],
    markdown_paragraphs: list[str],
    first_body_by_page: dict[int, MinerUStructuredBlock],
    last_body_by_page: dict[int, MinerUStructuredBlock],
) -> tuple[bool, str | None]:
    if previous.page is None or current.page is None or current.page != previous.page + 1:
        return False, None
    if previous.type not in _BODY_CONTINUATION_TYPES or current.type not in _BODY_CONTINUATION_TYPES:
        return False, None
    if last_body_by_page.get(previous.page) is not previous:
        return False, None
    if first_body_by_page.get(current.page) is not current:
        return False, None
    if not _bbox_near_bottom(previous.bbox) or not _bbox_near_top(current.bbox):
        return False, None
    if _has_cross_page_barrier(previous, current, all_blocks):
        return False, None
    if not _in_same_markdown_paragraph(previous.text, current.text, markdown_paragraphs):
        return False, None
    return True, "markdown_same_paragraph_page_boundary"


def _has_cross_page_barrier(
    previous: MinerUStructuredBlock,
    current: MinerUStructuredBlock,
    all_blocks: list[MinerUStructuredBlock],
) -> bool:
    left, right = sorted((previous.order, current.order))
    for block in all_blocks:
        if not (left < block.order < right):
            continue
        if block.type in _CROSS_PAGE_BARRIER_TYPES:
            return True
    return False


def _bbox_near_bottom(bbox: list[float] | None) -> bool:
    normalized = _bbox_y_span_01(bbox)
    return normalized is not None and normalized[1] >= _CROSS_PAGE_TAIL_BOTTOM_THRESHOLD


def _bbox_near_top(bbox: list[float] | None) -> bool:
    normalized = _bbox_y_span_01(bbox)
    return normalized is not None and normalized[0] <= _CROSS_PAGE_HEAD_TOP_THRESHOLD


def _bbox_y_span_01(bbox: list[float] | None) -> tuple[float, float] | None:
    if bbox is None or len(bbox) < 4:
        return None
    _x0, y0, _x1, y1 = _normalize_bbox(bbox)
    max_coord = max(abs(value) for value in bbox[:4])
    if max_coord <= 1.0:
        return y0, y1
    if max_coord <= 1000.0:
        return y0 / 1000.0, y1 / 1000.0
    return None


def _markdown_paragraph_signatures(markdown: str) -> list[str]:
    paragraphs = re.split(r"\n\s*\n+", str(markdown or ""))
    return [_continuity_text_signature(paragraph) for paragraph in paragraphs if _continuity_text_signature(paragraph)]


def _in_same_markdown_paragraph(left_text: str, right_text: str, paragraphs: list[str]) -> bool:
    left = _continuity_text_signature(left_text)
    right = _continuity_text_signature(right_text)
    if not left or not right:
        return False
    left_probe = left[-min(120, len(left)) :]
    right_probe = right[: min(120, len(right))]
    for paragraph in paragraphs:
        left_pos = paragraph.find(left_probe)
        right_pos = paragraph.find(right_probe)
        if left_pos >= 0 and right_pos >= 0 and left_pos <= right_pos:
            return True
        left_full = paragraph.find(left)
        right_full = paragraph.find(right)
        if left_full >= 0 and right_full >= 0 and left_full <= right_full:
            return True
    return False


def _continuity_text_signature(text: str) -> str:
    normalized = str(text or "").lower()
    normalized = normalized.replace("\u00a0", " ")
    normalized = re.sub(r"[\\${}_^`*#>\[\]()]|\|", "", normalized)
    normalized = re.sub(r"\s+", "", normalized)
    return normalized


def _split_large_structured_text(
    text: str,
    strategy: _TextChunkStrategy,
    *,
    max_chars: int,
    overlap: int,
) -> list[str]:
    semantic_chunks = [chunk.strip() for chunk in strategy.chunk_text(text) if chunk.strip()]
    spans = _semantic_chunk_spans(text, semantic_chunks) if semantic_chunks else []
    if not spans:
        return _merge_heading_only_chunks_with_following(
            _hard_split_text(text, max_chars=max_chars, overlap=overlap)
        )

    repaired: list[str] = []
    for index, (start, end) in enumerate(spans):
        repaired_start = 0 if index == 0 else _repair_semantic_start(text, start=start, end=end, overlap=overlap)
        repaired_end = len(text) if index == len(spans) - 1 else _repair_semantic_end(text, start=repaired_start, end=end)
        if repaired_end <= repaired_start:
            repaired_start, repaired_end = start, end
        piece = text[repaired_start:repaired_end].strip() or text[start:end].strip()
        if not piece:
            continue
        if len(piece) <= max_chars:
            repaired.append(piece)
        else:
            repaired.extend(_hard_split_text(piece, max_chars=max_chars, overlap=overlap))
    return _merge_heading_only_chunks_with_following(
        repaired or _hard_split_text(text, max_chars=max_chars, overlap=overlap)
    )


def _merge_heading_only_chunks_with_following(chunks: list[str]) -> list[str]:
    """Keep section titles attached to the first body chunk in the same logical segment."""
    merged: list[str] = []
    pending_headings: list[str] = []
    for chunk in chunks:
        stripped = str(chunk or "").strip()
        if not stripped:
            continue
        if _is_heading_only_markdown(stripped):
            pending_headings.append(stripped)
            continue
        if pending_headings:
            merged.append("\n\n".join([*pending_headings, stripped]).strip())
            pending_headings = []
        else:
            merged.append(stripped)
    merged.extend(pending_headings)
    return merged


def _is_heading_only_markdown(text: str) -> bool:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return bool(lines) and all(line.startswith("#") for line in lines)


def _semantic_chunk_spans(text: str, chunks: list[str]) -> list[tuple[int, int]]:
    compact_source, offsets = _compact_text_with_offsets(text)
    spans: list[tuple[int, int]] = []
    search_from = 0
    compact_search_from = 0
    for chunk in chunks:
        exact = text.find(chunk, search_from)
        if exact >= 0:
            start, end = exact, exact + len(chunk)
        else:
            compact_chunk = _compact_text(chunk)
            if not compact_chunk:
                return []
            compact_pos = compact_source.find(compact_chunk, compact_search_from)
            if compact_pos < 0:
                return []
            start = offsets[compact_pos]
            end = offsets[compact_pos + len(compact_chunk) - 1] + 1
        spans.append((start, end))
        search_from = min(len(text), start + 1)
        compact_search_from = _compact_offset_at_or_after(offsets, search_from)
    return spans


def _compact_text_with_offsets(text: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    offsets: list[int] = []
    for index, char in enumerate(text):
        if char.isspace():
            continue
        chars.append(char)
        offsets.append(index)
    return "".join(chars), offsets


def _compact_offset_at_or_after(offsets: list[int], text_offset: int) -> int:
    for index, offset in enumerate(offsets):
        if offset >= text_offset:
            return index
    return len(offsets)


def _repair_semantic_start(text: str, *, start: int, end: int, overlap: int) -> int:
    if start <= 0 or start >= len(text):
        return start
    max_shift = max(80, overlap * 2)
    forward_window = text[start : min(end, start + max_shift)]
    for match in _sentence_boundary_matches(forward_window):
        adjusted = _skip_leading_space(text, start + match.end())
        if start < adjusted < end:
            return adjusted

    search_start = max(0, start - max_shift)
    before = text[search_start:start]
    for match in reversed(_sentence_boundary_matches(before)):
        adjusted = _skip_leading_space(text, search_start + match.end())
        if 0 < adjusted < end:
            return adjusted
    return _adjust_overlap_start(text, start=start, end=end)


def _repair_semantic_end(text: str, *, start: int, end: int) -> int:
    if end >= len(text):
        return len(text)
    if _is_heading_only_markdown(text[start:end].strip()):
        return end
    stripped_end = end
    while stripped_end > start and text[stripped_end - 1].isspace():
        stripped_end -= 1
    if stripped_end > start and _is_sentence_terminal(text[stripped_end - 1]):
        return end
    boundary = _find_text_boundary(text, start=start, end=end)
    return boundary if boundary > start else end


def _is_sentence_terminal(char: str) -> bool:
    return char in ".!?\u3002\uff01\uff1f"


def _hard_split_text(text: str, *, max_chars: int, overlap: int) -> list[str]:
    if len(text) <= max_chars:
        return [text.strip()] if text.strip() else []
    parts: list[str] = []
    start = 0
    safe_overlap = min(max(0, overlap), max(0, max_chars - 1))
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            boundary = _find_text_boundary(text, start=start, end=end)
            if boundary > start:
                end = boundary
        piece = text[start:end].strip()
        if piece:
            parts.append(piece)
        if end >= len(text):
            break
        start = _adjust_overlap_start(text, start=max(end - safe_overlap, start + 1), end=end)
    return parts


def _adjust_overlap_start(text: str, *, start: int, end: int) -> int:
    if start <= 0 or start >= len(text):
        return start
    overlap_len = max(1, end - start)
    search_start = max(0, start - max(200, overlap_len * 2))
    before = text[search_start:start]
    for match in reversed(_sentence_boundary_matches(before)):
        adjusted = _skip_leading_space(text, search_start + match.end())
        if 0 < adjusted < end:
            return adjusted

    after = text[start:end]
    for match in _sentence_boundary_matches(after):
        adjusted = _skip_leading_space(text, start + match.end())
        if adjusted < end:
            return adjusted

    for idx, char in enumerate(after):
        if char.isspace():
            adjusted = _skip_leading_space(text, start + idx + 1)
            if adjusted < end:
                return adjusted
    return start


def _find_text_boundary(text: str, *, start: int, end: int) -> int:
    window = text[start:end]
    lower_bound = max(1, int(len(window) * 0.55))
    for match in reversed(_sentence_boundary_matches(window)):
        if match.end() >= lower_bound:
            return start + match.end()
    newline = window.rfind("\n")
    if newline >= lower_bound:
        return start + newline + 1
    space = window.rfind(" ")
    if space >= lower_bound:
        return start + space + 1
    return end


def _sentence_boundary_matches(text: str) -> list[re.Match[str]]:
    return list(re.finditer(r"\n\n+|\n|(?<=[.!?])\s+|(?<=[\u3002\uff01\uff1f])\s*", text))


def _skip_leading_space(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _block_to_markdown(block: MinerUStructuredBlock) -> str:
    text = str(block.text or "").strip()
    if not text:
        return ""
    if block.type == "heading":
        return text if text.lstrip().startswith("#") else f"# {text}"
    return text


def _blocks_to_markdown(blocks: list[MinerUStructuredBlock]) -> str:
    parts: list[str] = []
    previous: MinerUStructuredBlock | None = None
    for block in blocks:
        text = _block_to_markdown(block)
        if not text:
            continue
        if (
            previous is not None
            and previous.page != block.page
            and previous.type in _BODY_CONTINUATION_TYPES
            and block.type in _BODY_CONTINUATION_TYPES
        ):
            parts.append(" ")
        elif parts:
            parts.append("\n\n")
        parts.append(text)
        previous = block
    return "".join(parts).strip()


def _section_for_text_blocks(blocks: list[MinerUStructuredBlock]) -> str | None:
    for block in blocks:
        if block.section:
            return block.section
    for block in blocks:
        if block.type == "heading" and block.text.strip():
            return block.text.strip().lstrip("#").strip()
    return _section_from_markdown(_blocks_to_markdown(blocks))


def _mineru_text_chunk_relationships(
    blocks: list[MinerUStructuredBlock],
    *,
    policy: str,
    part_index: int | None = None,
    part_count: int | None = None,
) -> dict[str, Any]:
    first = blocks[0] if blocks else None
    relationships = _mineru_relationships(first)
    relationships["mineru_text_chunk_policy"] = policy
    relationships["mineru_text_block_count"] = len(blocks)
    relationships["mineru_text_block_ids"] = [block.block_id for block in blocks if block.block_id]
    relationships["mineru_text_block_types"] = [block.type for block in blocks]
    relationships["mineru_text_raw_types"] = [block.raw_type for block in blocks]
    relationships["mineru_text_source_kinds"] = _unique_strings([str(block.source_kind) for block in blocks if block.source_kind])
    pages = sorted({block.page for block in blocks if block.page is not None})
    relationships["mineru_text_pages"] = pages
    relationships["mineru_text_cross_page"] = len(pages) > 1
    title_region_ids = sorted({block.title_region_id for block in blocks if block.title_region_id is not None})
    if title_region_ids:
        relationships["mineru_title_region_ids"] = title_region_ids
        if len(title_region_ids) == 1:
            relationships["mineru_title_region_id"] = title_region_ids[0]
        relationships["mineru_text_cross_title_region"] = len(title_region_ids) > 1
        relationships["mineru_title_region_heading_block_ids"] = [
            block.block_id for block in blocks if block.type == "heading" and block.block_id
        ]
        relationships["mineru_title_region_boundary_policy"] = "no_cross_title_region_v1"
    if first and first.source_kind:
        relationships["mineru_source_kind"] = first.source_kind
    if len(blocks) > 1:
        relationships["bbox_merge_policy_detail"] = "integer_blocks_union"
    if part_index is not None:
        relationships["chunk_part_index"] = part_index
    if part_count is not None:
        relationships["chunk_part_count"] = part_count
        relationships["bbox_merge_policy_detail"] = "large_block_inherited"
    return relationships


def _logical_segment_relationships(segment: MinerULogicalSegment) -> dict[str, Any]:
    if not segment.cross_page_merge:
        return {}
    return {
        "mineru_cross_page_merge": True,
        "mineru_cross_page_merge_reason": segment.merge_reason,
        "mineru_cross_page_formula_as_text": True,
    }


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


def _select_bbox_blocks_for_text_chunk(
    blocks: list[MinerUStructuredBlock],
) -> tuple[list[MinerUStructuredBlock], dict[str, Any]]:
    if not blocks:
        return [], {}
    best = _best_block(blocks)
    if best is None or best.page is None:
        return blocks, {}

    located_pages = sorted({block.page for block in blocks if block.page is not None})
    if len(located_pages) <= 1:
        return blocks, {}

    same_page = [block for block in blocks if block.page == best.page]
    if not same_page:
        return blocks, {}

    return same_page, {
        "bbox_match_page_filtered": True,
        "bbox_match_selected_page": best.page,
        "bbox_match_candidate_pages": located_pages,
        "bbox_cross_page_candidate_count": len(blocks) - len(same_page),
    }


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
    return block.bbox_coordinate_system or "mineru_pdf_points_top_left"


def _mineru_bbox_payload(blocks: list[MinerUStructuredBlock]) -> dict[str, Any]:
    located = [block for block in blocks if block is not None and block.bbox is not None]
    if not located:
        return {
            "bbox": None,
            "bbox_items": None,
            "pages": None,
            "bbox_by_page": None,
            "page_spans": None,
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
        bbox_by_page: dict[str, list[list[float]]] = {}
        page_spans: list[dict[str, Any]] = []
        for page in sorted(page for page in pages if page is not None):
            page_blocks = [block for block in located if block.page == page]
            page_bbox_items = _unique_bboxes(
                [_normalize_bbox(block.bbox or [0, 0, 0, 0]) for block in page_blocks]
            )
            if not page_bbox_items:
                continue
            page_key = str(page)
            bbox_by_page[page_key] = page_bbox_items
            page_spans.append(
                {
                    "page": page,
                    "bbox_items": page_bbox_items,
                    "block_ids": [block.block_id for block in page_blocks if block.block_id],
                    "block_types": [block.type for block in page_blocks],
                }
            )
        return {
            "bbox": None,
            "bbox_items": None,
            "pages": sorted(page for page in pages if page is not None),
            "bbox_by_page": bbox_by_page or None,
            "page_spans": page_spans or None,
            "bbox_coordinate_system": coordinate_system,
            "bbox_source": _mineru_bbox_source(located[0]),
            "bbox_merge_policy": "multi_page_by_page",
        }

    bbox_items = _unique_bboxes([_normalize_bbox(block.bbox or [0, 0, 0, 0]) for block in located])
    if not bbox_items:
        return {
            "bbox": None,
            "bbox_items": None,
            "pages": None,
            "bbox_by_page": None,
            "page_spans": None,
            "bbox_coordinate_system": coordinate_system,
            "bbox_source": _mineru_bbox_source(located[0]),
            "bbox_merge_policy": None,
        }
    return {
        "bbox": _union_bboxes(bbox_items),
        "bbox_items": bbox_items,
        "pages": None,
        "bbox_by_page": None,
        "page_spans": None,
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


def _mineru_image_relationships(blocks: list[MinerUStructuredBlock]) -> dict[str, Any]:
    if not blocks:
        return {"source_parser": "mineru"}
    relationships = _mineru_relationships(blocks[0])
    relationships["mineru_block_type"] = "image"
    relationships["image_semantic_type"] = "caption"
    relationships["caption"] = _build_mineru_image_text(blocks)
    relationships["mineru_image_block_count"] = len(blocks)
    relationships["mineru_image_block_ids"] = [block.block_id for block in blocks if block.block_id]
    relationships["mineru_image_block_types"] = [block.raw_type or block.type for block in blocks]
    relationships["mineru_image_text_source"] = "structured_content"
    if any(block.image_path for block in blocks):
        relationships["mineru_image_path"] = next((block.image_path for block in blocks if block.image_path), None)
    heading_ids = [
        block.heading_context_block_id
        for block in blocks
        if block.heading_context_block_id
    ]
    if heading_ids:
        relationships["mineru_heading_block_ids"] = sorted(set(heading_ids), key=heading_ids.index)
    headings = [block.section for block in blocks if block.section]
    if headings:
        relationships["mineru_heading_context"] = headings[0]
    if any(block.title_region_id is not None for block in blocks):
        title_region_ids = sorted({block.title_region_id for block in blocks if block.title_region_id is not None})
        relationships["mineru_title_region_ids"] = title_region_ids
        if len(title_region_ids) == 1:
            relationships["mineru_title_region_id"] = title_region_ids[0]
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
