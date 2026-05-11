from __future__ import annotations

from pathlib import Path

from agentic_rag.config import Settings
from agentic_rag.observability.stage_logger import build_stage_logger


def test_stage_log_disabled_no_output(tmp_path: Path) -> None:
    log_path = tmp_path / "stage.log"
    settings = Settings(
        enable_stage_log=False,
        log_file_path=str(log_path),
        log_format="json",
    )
    logger = build_stage_logger(settings, run_id="run-x")
    logger.log_stage_start("test_stage", source="a.md")

    if log_path.exists():
        content = log_path.read_text(encoding="utf-8")
        assert content.strip() == ""
    else:
        assert True
