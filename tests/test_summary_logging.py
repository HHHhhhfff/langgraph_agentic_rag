from __future__ import annotations

import json
from pathlib import Path

from agentic_rag.config import Settings
from agentic_rag.observability.stage_logger import build_stage_logger


def test_build_summary_log_contains_metrics(tmp_path: Path) -> None:
    log_path = tmp_path / "summary.jsonl"
    settings = Settings(
        enable_stage_log=True,
        log_file_path=str(log_path),
        log_format="json",
    )
    logger = build_stage_logger(settings, run_id="run-summary")

    logger.log_stage_end(
        "build_index_run",
        latency_ms=123,
        source="docs",
        failed_files=2,
        upserted_count=10,
        vector_size=1536,
    )

    rows = [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    target = [r for r in rows if r.get("stage") == "build_index_run" and r.get("message") == "stage_end:build_index_run"]
    assert target
    row = target[-1]
    assert row.get("failed_files") == 2
    assert row.get("upserted_count") == 10
    assert row.get("vector_size") == 1536
