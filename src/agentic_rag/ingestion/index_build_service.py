from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from agentic_rag.config import Settings
from agentic_rag.ingestion.index_builder import IndexBuilder, IndexBuildSummary, build_default_chunker
from agentic_rag.ingestion.node_schema import Node
from agentic_rag.ingestion.parser import MarkdownParser
from agentic_rag.models.providers import build_embedding_provider
from agentic_rag.observability.console_progress import build_console_progress_reporter
from agentic_rag.observability.stage_logger import StageLogger, StageTimer, build_stage_logger
from agentic_rag.store.qdrant_store import QdrantStore


@dataclass(slots=True)
class IndexBuildOptions:
    recreate_collection: bool = False
    write_local_index: bool = True
    emit_logs: bool = True
    source_label: str = "build_index"
    run_id: str | None = None


@dataclass(slots=True)
class IndexBuildResult:
    summary: IndexBuildSummary
    node_count: int
    embedded_count: int
    upserted_point_count: int
    wrote_qdrant: bool
    wrote_local_index: bool
    recreated_collection: bool
    qdrant_collection: str
    elapsed_ms: int
    run_id: str


def build_index_from_docs(
    docs_path: str | Path,
    *,
    settings: Settings,
    options: IndexBuildOptions | None = None,
) -> IndexBuildResult:
    options = options or IndexBuildOptions()
    run_id = options.run_id or str(uuid.uuid4())
    effective_settings = _settings_for_index_build(settings, options)
    stage_logger = _build_service_stage_logger(effective_settings, run_id, options)
    builder = _build_index_builder(effective_settings, stage_logger=stage_logger, run_id=run_id)
    source = str(docs_path)
    timer = StageTimer.start_now()
    if stage_logger:
        stage_logger.log_stage_start("build_index_run", source=source, source_label=options.source_label)
    try:
        summary = builder.build_from_directory(source)
    except Exception as exc:
        if stage_logger:
            stage_logger.log_stage_error("build_index_run", exc, source=source, source_label=options.source_label)
        raise
    elapsed_ms = timer.elapsed_ms()
    if stage_logger:
        _log_build_index_success(stage_logger, summary, source=source, elapsed_ms=elapsed_ms, source_label=options.source_label)
    return _result_from_summary(
        summary,
        node_count=summary.chunks,
        settings=effective_settings,
        options=options,
        elapsed_ms=elapsed_ms,
        run_id=run_id,
    )


def build_index_from_nodes(
    nodes: list[Node],
    *,
    source: str,
    settings: Settings,
    options: IndexBuildOptions | None = None,
) -> IndexBuildResult:
    options = options or IndexBuildOptions()
    run_id = options.run_id or str(uuid.uuid4())
    effective_settings = _settings_for_index_build(settings, options)
    stage_logger = _build_service_stage_logger(effective_settings, run_id, options)
    builder = _build_index_builder(effective_settings, stage_logger=stage_logger, run_id=run_id)
    timer = StageTimer.start_now()
    if stage_logger:
        stage_logger.log_stage_start("build_index_run", source=source, source_label=options.source_label)
    try:
        summary = builder.build_from_nodes(nodes, source=source)
    except Exception as exc:
        if stage_logger:
            stage_logger.log_stage_error("build_index_run", exc, source=source, source_label=options.source_label)
        raise
    elapsed_ms = timer.elapsed_ms()
    if stage_logger:
        _log_build_index_success(stage_logger, summary, source=source, elapsed_ms=elapsed_ms, source_label=options.source_label)
    return _result_from_summary(
        summary,
        node_count=summary.chunks,
        settings=effective_settings,
        options=options,
        elapsed_ms=elapsed_ms,
        run_id=run_id,
    )


def print_index_build_summary(summary: IndexBuildSummary) -> None:
    print("Index build completed")
    print(f"- documents: {summary.documents}")
    print(f"- chunks: {summary.chunks}")
    print(f"- vectors: {summary.vectors}")
    print(f"- upserted: {summary.upserted}")
    print(f"- vector_size: {summary.vector_size}")
    print(f"- failed_files: {summary.failed_files}")
    print(f"- named_vectors_enabled: {str(summary.named_vectors_enabled).lower()}")
    if summary.named_vectors_enabled:
        print("- named_vector_counts:")
        if summary.named_vector_counts:
            for name in sorted(summary.named_vector_counts):
                print(f"  - {name}: {summary.named_vector_counts[name]}")
        else:
            print("  - (none)")
    else:
        print("- vector_mode: single")


def _settings_for_index_build(settings: Settings, options: IndexBuildOptions) -> Settings:
    return settings.model_copy(
        update={
            "qdrant_recreate_collection": options.recreate_collection,
            "retrieval_index_persist_enabled": bool(
                settings.retrieval_index_persist_enabled and options.write_local_index
            ),
        }
    )


def _build_service_stage_logger(
    settings: Settings,
    run_id: str,
    options: IndexBuildOptions,
) -> StageLogger | None:
    if not options.emit_logs:
        return None
    stage_logger = build_stage_logger(settings, run_id=run_id)
    console_progress = build_console_progress_reporter(settings, run_id=run_id)
    stage_logger.add_listener(console_progress.handle_event)
    return stage_logger


def _build_index_builder(
    settings: Settings,
    *,
    stage_logger: StageLogger | None,
    run_id: str,
) -> IndexBuilder:
    return IndexBuilder(
        settings=settings,
        parser=MarkdownParser(),
        chunker=build_default_chunker(settings),
        embedding_provider=build_embedding_provider(settings),
        store=QdrantStore(settings),
        stage_logger=stage_logger,
        run_id=run_id,
    )


def _log_build_index_success(
    stage_logger: StageLogger,
    summary: IndexBuildSummary,
    *,
    source: str,
    elapsed_ms: int,
    source_label: str,
) -> None:
    stage_logger.log_stage_end(
        "build_index_run",
        latency_ms=elapsed_ms,
        source=source,
        source_label=source_label,
        chunk_count=summary.chunks,
        vector_count=summary.vectors,
        upserted_count=summary.upserted,
        failed_files=summary.failed_files,
        vector_size=summary.vector_size,
    )
    stage_logger.log_counter(
        "summary",
        source=source,
        source_label=source_label,
        failed_files=summary.failed_files,
        upserted_count=summary.upserted,
        vector_size=summary.vector_size,
        chunk_count=summary.chunks,
        vector_count=summary.vectors,
    )


def _result_from_summary(
    summary: IndexBuildSummary,
    *,
    node_count: int,
    settings: Settings,
    options: IndexBuildOptions,
    elapsed_ms: int,
    run_id: str,
) -> IndexBuildResult:
    return IndexBuildResult(
        summary=summary,
        node_count=node_count,
        embedded_count=summary.vectors,
        upserted_point_count=summary.upserted,
        wrote_qdrant=True,
        wrote_local_index=bool(settings.retrieval_index_persist_enabled and options.write_local_index),
        recreated_collection=options.recreate_collection,
        qdrant_collection=settings.qdrant_collection,
        elapsed_ms=elapsed_ms,
        run_id=run_id,
    )
