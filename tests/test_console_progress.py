from __future__ import annotations

from pathlib import Path

from agentic_rag.config import Settings
from agentic_rag.observability.console_progress import build_console_progress_reporter


def _event(event_type: str, stage: str, **kwargs):
    base = {
        "event_type": event_type,
        "run_id": "run-p",
        "stage": stage,
        "source": "docs",
        "extra_fields": {},
    }
    base.update(kwargs)
    return base


def test_console_progress_disabled_no_output(tmp_path: Path) -> None:
    out = tmp_path / "progress.log"
    with out.open("w", encoding="utf-8") as fp:
        settings = Settings(enable_console_progress=False)
        reporter = build_console_progress_reporter(settings, run_id="run-p")
        reporter.stream = fp
        reporter.handle_event(_event("start", "build_index_run"))

    assert out.read_text(encoding="utf-8") == ""


def test_console_progress_stage_done_contains_stage_and_runid(tmp_path: Path) -> None:
    out = tmp_path / "progress.log"
    with out.open("w", encoding="utf-8") as fp:
        settings = Settings(
            enable_console_progress=True,
            console_show_stage_done=True,
            console_progress_min_interval_ms=1,
        )
        reporter = build_console_progress_reporter(settings, run_id="run-p")
        reporter.stream = fp
        reporter.handle_event(_event("end", "build_index_run", latency_ms=123, chunk_count=3))

    text = out.read_text(encoding="utf-8")
    assert "run_id=run-p" in text
    assert "stage=build_index_run" in text
    assert "[PROGRESS][done]" in text


def test_console_progress_batch_fields(tmp_path: Path) -> None:
    out = tmp_path / "progress.log"
    with out.open("w", encoding="utf-8") as fp:
        settings = Settings(
            enable_console_progress=True,
            console_show_batch_progress=True,
            console_progress_min_interval_ms=1,
        )
        reporter = build_console_progress_reporter(settings, run_id="run-p")
        reporter.stream = fp
        reporter.handle_event(
            _event(
                "end",
                "embedding_batch",
                latency_ms=200,
                chunk_count=8,
                vector_count=8,
                batch_index=1,
                total_batch=4,
            )
        )

    text = out.read_text(encoding="utf-8")
    assert "stage=embedding_batch" in text
    assert "batch=1/4" in text
    assert "vectors=8" in text


def test_console_progress_warning_output(tmp_path: Path) -> None:
    out = tmp_path / "progress.log"
    with out.open("w", encoding="utf-8") as fp:
        settings = Settings(enable_console_progress=True, console_progress_min_interval_ms=1000)
        reporter = build_console_progress_reporter(settings, run_id="run-p")
        reporter.stream = fp
        reporter.handle_event(_event("warn", "route_file_failure", error_msg="boom"))

    text = out.read_text(encoding="utf-8")
    assert "[PROGRESS][warn]" in text
    assert "stage=route_file_failure" in text


def test_console_progress_summary_fields(tmp_path: Path) -> None:
    out = tmp_path / "progress.log"
    with out.open("w", encoding="utf-8") as fp:
        settings = Settings(enable_console_progress=True, console_progress_min_interval_ms=1)
        reporter = build_console_progress_reporter(settings, run_id="run-p")
        reporter.stream = fp
        reporter.handle_event(
            _event(
                "counter",
                "summary",
                failed_files=2,
                upserted_count=10,
                vector_size=1536,
            )
        )

    text = out.read_text(encoding="utf-8")
    assert "stage=summary" in text
    assert "failed_files=2" in text
    assert "upserted=10" in text
    assert "vector_size=1536" in text
