from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import asdict

from agentic_rag.config import get_settings
from agentic_rag.ingestion.index_builder import IndexBuilder, build_default_chunker
from agentic_rag.ingestion.parser import MarkdownParser
from agentic_rag.models.providers import build_embedding_provider
from agentic_rag.observability.console_progress import build_console_progress_reporter
from agentic_rag.observability.stage_logger import StageTimer, build_stage_logger
from agentic_rag.store.qdrant_store import QdrantStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Qdrant index from markdown documents")
    parser.add_argument("--docs", type=str, required=True, help="Markdown directory path")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    settings = get_settings()
    run_id = str(uuid.uuid4())
    stage_logger = build_stage_logger(settings, run_id=run_id)
    console_progress = build_console_progress_reporter(settings, run_id=run_id)
    stage_logger.add_listener(console_progress.handle_event)
    parser_service = MarkdownParser()
    chunker = build_default_chunker(settings)
    embedding = build_embedding_provider(settings)
    store = QdrantStore(settings)

    builder = IndexBuilder(
        settings=settings,
        parser=parser_service,
        chunker=chunker,
        embedding_provider=embedding,
        store=store,
        stage_logger=stage_logger,
        run_id=run_id,
    )

    timer = StageTimer.start_now()
    stage_logger.log_stage_start("build_index_run", source=args.docs)
    try:
        summary = builder.build_from_directory(args.docs)
    except Exception as exc:
        stage_logger.log_stage_error("build_index_run", exc, source=args.docs)
        print(f"[ERROR] Index build failed: {exc}", file=sys.stderr)
        return 1
    stage_logger.log_stage_end(
        "build_index_run",
        latency_ms=timer.elapsed_ms(),
        source=args.docs,
        chunk_count=summary.chunks,
        vector_count=summary.vectors,
        upserted_count=summary.upserted,
        failed_files=summary.failed_files,
        vector_size=summary.vector_size,
    )
    stage_logger.log_counter(
        "summary",
        source=args.docs,
        failed_files=summary.failed_files,
        upserted_count=summary.upserted,
        vector_size=summary.vector_size,
        chunk_count=summary.chunks,
        vector_count=summary.vectors,
    )

    if args.json:
        print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
        return 0

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
