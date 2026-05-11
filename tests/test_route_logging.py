from __future__ import annotations

import json
from pathlib import Path

from agentic_rag.config import Settings
from agentic_rag.ingestion.multimodal_orchestrator import MultiModalOrchestrator
from agentic_rag.observability.stage_logger import build_stage_logger


def test_route_match_logging(tmp_path: Path) -> None:
    data_dir = tmp_path / "docs"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "a.md").write_text("# t\nhello", encoding="utf-8")
    (data_dir / "b.csv").write_text("c1,c2\n1,2", encoding="utf-8")

    log_path = tmp_path / "route.jsonl"
    settings = Settings(
        enable_stage_log=True,
        log_file_path=str(log_path),
        log_format="json",
        ingestion_engine="multimodal",
        multimodal_enabled=True,
        text_chunk_parser="sentence",
    )

    stage_logger = build_stage_logger(settings, run_id="run-route")
    orchestrator = MultiModalOrchestrator(settings, stage_logger=stage_logger, run_id="run-route")
    _ = orchestrator.parse_directory(data_dir)

    rows = [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    route_rows = [r for r in rows if r.get("stage") == "route_file"]

    assert len(route_rows) >= 2
    adapters = {r.get("adapter") for r in route_rows}
    assert "LlamaIndexAdapter" in adapters
    assert "TableAdapter" in adapters
