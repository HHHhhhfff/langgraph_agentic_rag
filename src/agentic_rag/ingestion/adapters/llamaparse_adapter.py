from __future__ import annotations

from pathlib import Path

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from agentic_rag.config import Settings
from agentic_rag.ingestion.adapters.unstructured_adapter import UnstructuredAdapter
from agentic_rag.ingestion.chunk_strategies import TableChunker, build_text_chunk_strategy
from agentic_rag.ingestion.formula_extractor import extract_formulas
from agentic_rag.ingestion.node_normalizer import NodeNormalizer
from agentic_rag.ingestion.node_schema import IngestionFailure, MultimodalIngestionResult
from agentic_rag.observability.stage_logger import StageLogger, StageTimer


class LlamaParseAdapterError(RuntimeError):
    """Raised for LlamaParse adapter failures."""


class LlamaParseAdapter:
    """Parse PDF via LlamaParse with optional fallback to Unstructured."""

    def __init__(self, settings: Settings, stage_logger: StageLogger | None = None, run_id: str = ""):
        self.settings = settings
        self.normalizer = NodeNormalizer()
        self.text_strategy = build_text_chunk_strategy(settings)
        self.table_chunker = TableChunker()
        self.stage_logger = stage_logger
        self.run_id = run_id
        self.fallback = UnstructuredAdapter(settings, stage_logger=stage_logger, run_id=run_id)

    @retry(
        reraise=True,
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception_type(LlamaParseAdapterError),
    )
    def _parse_pdf_once(self, path: Path):
        try:
            from llama_parse import LlamaParse

            parser = LlamaParse(
                api_key=self.settings.llama_cloud_api_key,
                result_type="markdown",
                num_workers=1,
                verbose=False,
            )
            return parser.load_data(str(path))
        except Exception as exc:
            raise LlamaParseAdapterError(f"LlamaParse failed for {path}: {exc}") from exc

    def parse_file(self, path: str | Path) -> MultimodalIngestionResult:
        file_path = Path(path)
        if not file_path.exists():
            return MultimodalIngestionResult(
                failures=[IngestionFailure(source=str(file_path), error="file not found")]
            )
        timer = StageTimer.start_now()
        if self.stage_logger:
            self.stage_logger.log_stage_start("llamaparse_parse", source=str(file_path), parser="llamaparse")

        try:
            docs = self._parse_pdf_once(file_path)
        except Exception as exc:
            if self.settings.llamaparse_fallback_to_unstructured:
                if self.stage_logger:
                    self.stage_logger.log_counter(
                        "llamaparse_fallback",
                        source=str(file_path),
                        parser="llamaparse",
                        fallback=True,
                        fallback_from="llamaparse",
                        fallback_to="unstructured",
                        error_msg=str(exc),
                    )
                return self.fallback.parse_file(file_path)
            if self.stage_logger:
                self.stage_logger.log_stage_error("llamaparse_parse", exc, source=str(file_path), parser="llamaparse")
            return MultimodalIngestionResult(
                failures=[IngestionFailure(source=str(file_path), error=str(exc))]
            )

        nodes = []
        chunk_index = 0
        text_count = 0
        table_count = 0
        formula_count = 0
        for doc in docs:
            page = None
            content = str(getattr(doc, "text", "") or "").strip()
            if not content:
                continue

            if "|" in content and "---" in content:
                table_chunks = self.table_chunker.chunk_markdown_table(content)
                for table_md in table_chunks:
                    nodes.append(
                        self.normalizer.normalize(
                            source=str(file_path),
                            parser_name="llamaparse",
                            chunk_index=chunk_index,
                            modality="table",
                            text=table_md,
                            table_markdown=table_md,
                            page=page,
                            title=file_path.stem,
                        )
                    )
                    chunk_index += 1
                    table_count += 1

            if self.settings.enable_formula_recognition:
                for formula in extract_formulas(content):
                    nodes.append(
                        self.normalizer.normalize(
                            source=str(file_path),
                            parser_name="llamaparse:formula",
                            chunk_index=chunk_index,
                            modality="formula",
                            text=formula.text,
                            formula_latex=formula.formula_latex,
                            page=page,
                            title=file_path.stem,
                        )
                    )
                    chunk_index += 1
                    formula_count += 1

            text_chunks = self.text_strategy.chunk_text(content)
            for chunk in text_chunks:
                nodes.append(
                    self.normalizer.normalize(
                        source=str(file_path),
                        parser_name="llamaparse",
                        chunk_index=chunk_index,
                        modality="text",
                        text=chunk,
                        page=page,
                        title=file_path.stem,
                    )
                )
                chunk_index += 1
                text_count += 1

        if self.stage_logger:
            self.stage_logger.log_counter(
                "llamaparse_modality_counts",
                source=str(file_path),
                parser="llamaparse",
                text_count=text_count,
                table_count=table_count,
                formula_count=formula_count,
            )
            self.stage_logger.log_stage_end(
                "llamaparse_parse",
                latency_ms=timer.elapsed_ms(),
                source=str(file_path),
                parser="llamaparse",
                chunk_count=len(nodes),
            )
        return MultimodalIngestionResult(nodes=nodes, failures=[])
