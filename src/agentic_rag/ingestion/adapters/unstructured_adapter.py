from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_rag.config import Settings
from agentic_rag.ingestion.chunk_strategies import TableChunker, build_text_chunk_strategy
from agentic_rag.ingestion.formula_extractor import extract_formulas
from agentic_rag.ingestion.node_normalizer import NodeNormalizer
from agentic_rag.ingestion.node_schema import IngestionFailure, MultimodalIngestionResult
from agentic_rag.observability.stage_logger import StageLogger, StageTimer


class UnstructuredAdapterError(RuntimeError):
    """Raised for unstructured adapter errors."""


class UnstructuredAdapter:
    """Use unstructured partition APIs for multimodal parsing."""

    def __init__(self, settings: Settings, stage_logger: StageLogger | None = None, run_id: str = ""):
        self.settings = settings
        self.normalizer = NodeNormalizer()
        self.text_strategy = build_text_chunk_strategy(settings)
        self.table_chunker = TableChunker()
        self.stage_logger = stage_logger
        self.run_id = run_id

    def _partition_elements(self, path: Path) -> list[Any]:
        suffix = path.suffix.lower()
        strategy = self.settings.unstructured_strategy

        try:
            if suffix in {".html", ".htm"}:
                from unstructured.partition.html import partition_html

                return partition_html(filename=str(path))

            if suffix in {".doc", ".docx"}:
                from unstructured.partition.docx import partition_docx

                return partition_docx(filename=str(path), strategy=strategy)

            if suffix == ".pdf":
                from unstructured.partition.pdf import partition_pdf

                return partition_pdf(filename=str(path), strategy=strategy)

            from unstructured.partition.text import partition_text

            return partition_text(filename=str(path))
        except Exception as exc:
            raise UnstructuredAdapterError(f"Unstructured parse failed for {path}: {exc}") from exc

    def _parse_pdf_with_pypdf(self, path: Path) -> MultimodalIngestionResult:
        try:
            from pypdf import PdfReader
        except Exception as exc:
            return MultimodalIngestionResult(
                failures=[
                    IngestionFailure(
                        source=str(path),
                        error=f"Unstructured parse failed and pypdf fallback is unavailable: {exc}",
                    )
                ]
            )

        try:
            reader = PdfReader(str(path))
        except Exception as exc:
            return MultimodalIngestionResult(
                failures=[IngestionFailure(source=str(path), error=f"pypdf open failed: {exc}")]
            )

        nodes = []
        idx = 0
        formula_count = 0
        for page_index, page in enumerate(reader.pages, start=1):
            try:
                raw_text = (page.extract_text() or "").strip()
            except Exception:
                raw_text = ""
            if not raw_text:
                continue

            for chunk in self.text_strategy.chunk_text(raw_text):
                nodes.append(
                    self.normalizer.normalize(
                        source=str(path),
                        parser_name="pypdf_fallback",
                        chunk_index=idx,
                        modality="text",
                        text=chunk,
                        page=page_index,
                        title=path.stem,
                    )
                )
                idx += 1

            if self.settings.enable_formula_recognition:
                for formula in extract_formulas(raw_text):
                    nodes.append(
                        self.normalizer.normalize(
                            source=str(path),
                            parser_name="pypdf_fallback:formula",
                            chunk_index=idx,
                            modality="formula",
                            text=formula.text,
                            formula_latex=formula.formula_latex,
                            page=page_index,
                            title=path.stem,
                        )
                    )
                    idx += 1
                    formula_count += 1

        if self.stage_logger:
            self.stage_logger.log_counter(
                "pypdf_fallback_nodes",
                source=str(path),
                parser="pypdf_fallback",
                chunk_count=len(nodes),
                formula_count=formula_count,
            )

        if not nodes:
            return MultimodalIngestionResult(
                failures=[IngestionFailure(source=str(path), error="pypdf fallback produced no text")]
            )
        return MultimodalIngestionResult(nodes=nodes, failures=[])

    def parse_file(self, path: str | Path) -> MultimodalIngestionResult:
        file_path = Path(path)
        if not file_path.exists():
            return MultimodalIngestionResult(
                failures=[IngestionFailure(source=str(file_path), error="file not found")]
            )
        timer = StageTimer.start_now()
        if self.stage_logger:
            self.stage_logger.log_stage_start("unstructured_parse", source=str(file_path), parser="unstructured")

        try:
            elements = self._partition_elements(file_path)
        except Exception as exc:
            if self.stage_logger:
                self.stage_logger.log_stage_error("unstructured_parse", exc, source=str(file_path), parser="unstructured")
            if file_path.suffix.lower() == ".pdf":
                if self.stage_logger:
                    self.stage_logger.log_counter(
                        "unstructured_pypdf_fallback",
                        source=str(file_path),
                        parser="unstructured",
                        fallback=True,
                        error_msg=str(exc),
                    )
                return self._parse_pdf_with_pypdf(file_path)
            return MultimodalIngestionResult(
                failures=[IngestionFailure(source=str(file_path), error=str(exc))]
            )

        nodes = []
        idx = 0
        text_count = 0
        image_count = 0
        table_count = 0
        formula_count = 0
        for element in elements:
            etype = str(type(element).__name__).lower()
            page = getattr(getattr(element, "metadata", None), "page_number", None)
            raw_text = str(getattr(element, "text", "") or "").strip()
            parser_name = "unstructured"

            if self.settings.enable_formula_recognition and "formula" in etype and raw_text:
                nodes.append(
                    self.normalizer.normalize(
                        source=str(file_path),
                        parser_name=parser_name,
                        chunk_index=idx,
                        modality="formula",
                        text=f"Formula (unstructured): {raw_text}",
                        formula_latex=raw_text,
                        page=page,
                        title=file_path.stem,
                    )
                )
                idx += 1
                formula_count += 1
                continue

            if "table" in etype:
                chunks = self.table_chunker.chunk_markdown_table(raw_text)
                for chunk in chunks:
                    nodes.append(
                        self.normalizer.normalize(
                            source=str(file_path),
                            parser_name=parser_name,
                            chunk_index=idx,
                            modality="table",
                            text=chunk,
                            table_markdown=chunk,
                            page=page,
                            title=file_path.stem,
                        )
                    )
                    idx += 1
                    table_count += 1
                continue

            if "image" in etype:
                caption = f"Image element in {file_path.name}" if self.settings.enable_image_caption else None
                nodes.append(
                    self.normalizer.normalize(
                        source=str(file_path),
                        parser_name=parser_name,
                        chunk_index=idx,
                        modality="image",
                        text=caption,
                        image_path=str(file_path),
                        page=page,
                        title=file_path.stem,
                    )
                )
                idx += 1
                image_count += 1
                continue

            if raw_text:
                text_chunks = self.text_strategy.chunk_text(raw_text)
                for chunk in text_chunks:
                    nodes.append(
                        self.normalizer.normalize(
                            source=str(file_path),
                            parser_name=parser_name,
                            chunk_index=idx,
                            modality="text",
                            text=chunk,
                            page=page,
                            title=file_path.stem,
                        )
                    )
                    idx += 1
                    text_count += 1

                if self.settings.enable_formula_recognition:
                    for formula in extract_formulas(raw_text):
                        nodes.append(
                            self.normalizer.normalize(
                                source=str(file_path),
                                parser_name=f"{parser_name}:formula",
                                chunk_index=idx,
                                modality="formula",
                                text=formula.text,
                                formula_latex=formula.formula_latex,
                                page=page,
                                title=file_path.stem,
                            )
                        )
                        idx += 1
                        formula_count += 1

        if self.stage_logger:
            self.stage_logger.log_counter(
                "unstructured_modality_counts",
                source=str(file_path),
                parser="unstructured",
                text_count=text_count,
                image_count=image_count,
                table_count=table_count,
                formula_count=formula_count,
            )
            self.stage_logger.log_stage_end(
                "unstructured_parse",
                latency_ms=timer.elapsed_ms(),
                source=str(file_path),
                parser="unstructured",
                chunk_count=len(nodes),
            )
        return MultimodalIngestionResult(nodes=nodes, failures=[])
