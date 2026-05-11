from __future__ import annotations

from pathlib import Path

from agentic_rag.config import Settings
from agentic_rag.ingestion.adapters.image_adapter import ImageAdapter
from agentic_rag.ingestion.adapters.llamaindex_adapter import LlamaIndexAdapter
from agentic_rag.ingestion.adapters.llamaparse_adapter import LlamaParseAdapter
from agentic_rag.ingestion.adapters.mineru_adapter import MinerUAdapter
from agentic_rag.ingestion.adapters.table_adapter import TableAdapter
from agentic_rag.ingestion.adapters.unstructured_adapter import UnstructuredAdapter
from agentic_rag.ingestion.node_schema import IngestionFailure, MultimodalIngestionResult
from agentic_rag.observability.stage_logger import StageLogger, StageTimer


class MultiModalOrchestrator:
    """Route files to multimodal adapters and collect nodes/failures."""

    def __init__(self, settings: Settings, stage_logger: StageLogger | None = None, run_id: str = ""):
        self.settings = settings
        self.stage_logger = stage_logger
        self.run_id = run_id
        self.llamaindex_adapter = LlamaIndexAdapter(settings, stage_logger=stage_logger, run_id=run_id)
        self.unstructured_adapter = UnstructuredAdapter(settings, stage_logger=stage_logger, run_id=run_id)
        self.llamaparse_adapter = LlamaParseAdapter(settings, stage_logger=stage_logger, run_id=run_id)
        self.mineru_adapter = MinerUAdapter(settings, stage_logger=stage_logger, run_id=run_id)
        self.image_adapter = ImageAdapter(settings, stage_logger=stage_logger, run_id=run_id)
        self.table_adapter = TableAdapter(stage_logger=stage_logger, run_id=run_id)

    def _adapter_for(self, path: Path):
        suffix = path.suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
            return self.image_adapter
        if suffix in {".csv", ".tsv"}:
            return self.table_adapter
        if suffix == ".pdf":
            return self.llamaparse_adapter if self.settings.pdf_parser == "llamaparse" else self.unstructured_adapter
        if suffix in {".html", ".htm", ".doc", ".docx"}:
            return self.unstructured_adapter
        return self.llamaindex_adapter

    def parse_directory(self, directory: str | Path) -> MultimodalIngestionResult:
        dir_path = Path(directory)
        if not dir_path.exists() or not dir_path.is_dir():
            return MultimodalIngestionResult(
                failures=[IngestionFailure(source=str(dir_path), error="directory is invalid")]
            )

        all_nodes = []
        failures: list[IngestionFailure] = []

        file_paths = [p for p in sorted(dir_path.rglob("*")) if p.is_file()]
        total_files = len(file_paths)
        for idx, path in enumerate(file_paths, start=1):
            adapter = self._adapter_for(path)
            adapter_name = adapter.__class__.__name__
            if self.stage_logger:
                self.stage_logger.log_counter(
                    "route_file",
                    source=str(path),
                    adapter=adapter_name,
                    suffix=path.suffix.lower(),
                    file_index=idx,
                    total_files=total_files,
                )
            timer = StageTimer.start_now()
            result = self._parse_with_fallback(path, adapter)
            if self.stage_logger:
                self.stage_logger.log_stage_end(
                    "route_file_parse",
                    latency_ms=timer.elapsed_ms(),
                    source=str(path),
                    adapter=adapter_name,
                    chunk_count=len(result.nodes),
                    failed_files=len(result.failures),
                )
            all_nodes.extend(result.nodes)
            failures.extend(result.failures)
            for failure in result.failures:
                if self.stage_logger:
                    self.stage_logger.log_warning(
                        "route_file_failure",
                        "single_file_failed_continue",
                        source=failure.source,
                        error_msg=failure.error,
                    )

        return MultimodalIngestionResult(nodes=all_nodes, failures=failures)

    def _parse_with_fallback(self, path: Path, adapter) -> MultimodalIngestionResult:
        suffix = path.suffix.lower()
        is_doc_like = suffix in {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".html", ".htm"}
        if not (self.settings.enable_mineru and is_doc_like):
            return adapter.parse_file(path)

        primary_name = adapter.__class__.__name__
        result = self.mineru_adapter.parse_file(path)
        if result.nodes:
            return result
        if not self.settings.mineru_fallback_to_existing:
            return result

        if self.stage_logger:
            err_msg = result.failures[0].error if result.failures else "unknown"
            self.stage_logger.log_counter(
                "mineru_fallback",
                source=str(path),
                fallback=True,
                fallback_from="mineru",
                fallback_to=primary_name,
                error_msg=err_msg,
            )
        return adapter.parse_file(path)
