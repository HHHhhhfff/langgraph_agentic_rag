from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Any, TextIO

from agentic_rag.config import Settings


@dataclass(slots=True)
class ProgressEvent:
    """Normalized progress event emitted from StageLogger."""

    event_type: str
    run_id: str
    stage: str
    source: str | None
    payload: dict[str, Any]


class ConsoleProgressReporter:
    """Human-readable console progress output for ingestion pipeline stages."""

    def __init__(
        self,
        settings: Settings,
        run_id: str,
        *,
        stream: TextIO | None = None,
        clock: callable | None = None,
    ):
        self.settings = settings
        self.run_id = run_id
        self.stream = stream or sys.stdout
        self._clock = clock or time.perf_counter
        self._last_emit_ms = 0

    def handle_event(self, event: dict[str, Any]) -> None:
        if not self.settings.enable_console_progress:
            return

        normalized = self._normalize(event)
        if normalized is None:
            return
        if not self._should_emit(normalized):
            return

        line = self._format_line(normalized)
        if line:
            self.stream.write(line + "\n")
            self.stream.flush()
            self._last_emit_ms = self._now_ms()

    def _normalize(self, event: dict[str, Any]) -> ProgressEvent | None:
        event_type = str(event.get("event_type", "")).strip().lower()
        stage = str(event.get("stage", "")).strip()
        if not event_type or not stage:
            return None

        payload = dict(event)
        source = event.get("source")
        if source is not None:
            source = str(source)

        return ProgressEvent(
            event_type=event_type,
            run_id=str(event.get("run_id") or self.run_id),
            stage=stage,
            source=source,
            payload=payload,
        )

    def _should_emit(self, event: ProgressEvent) -> bool:
        if event.event_type == "end" and not self.settings.console_show_stage_done:
            return False

        batch_like = self._get_value(event.payload, "batch_index") is not None
        if batch_like and not self.settings.console_show_batch_progress:
            return False

        if event.event_type in {"error", "warn"}:
            return True

        now_ms = self._now_ms()
        if now_ms - self._last_emit_ms < self.settings.console_progress_min_interval_ms:
            keep_stages = {
                "build_index_run",
                "qdrant_ensure_collection",
                "multimodal_parse_directory",
                "query_analyze",
                "task_route",
                "retrieve_fanout",
                "retrieve_rrf",
                "evidence_gate",
                "local_retry",
            }
            return event.stage in keep_stages
        return True

    def _format_line(self, event: ProgressEvent) -> str:
        status_map = {
            "start": "start",
            "end": "done",
            "counter": "count",
            "error": "error",
            "warn": "warn",
        }
        status = status_map.get(event.event_type, event.event_type)

        parts = [
            f"[PROGRESS][{status}]",
            f"run_id={event.run_id}",
            f"stage={event.stage}",
        ]

        if event.source:
            parts.append(f"source={event.source}")

        batch_index = self._get_value(event.payload, "batch_index")
        total_batch = self._get_value(event.payload, "total_batch")
        if batch_index is not None:
            if total_batch is not None:
                parts.append(f"batch={batch_index}/{total_batch}")
            else:
                parts.append(f"batch={batch_index}")

        chunk_count = self._get_value(event.payload, "chunk_count")
        vector_count = self._get_value(event.payload, "vector_count")
        upserted_count = self._get_value(event.payload, "upserted_count")
        latency_ms = self._get_value(event.payload, "latency_ms")

        if chunk_count is not None:
            parts.append(f"chunks={chunk_count}")
        if vector_count is not None:
            parts.append(f"vectors={vector_count}")
        if upserted_count is not None:
            parts.append(f"upserted={upserted_count}")
        if latency_ms is not None:
            parts.append(f"latency_ms={latency_ms}")

        for key in [
            "failed_files",
            "vector_size",
            "modality",
            "fallback",
            "error_type",
            "error_msg",
            "task_id",
            "state",
            "batch_id",
            "fallback_from",
            "fallback_to",
            "query_text",
            "query_plan",
            "channel",
            "doc_id",
            "page",
            "section_path",
            "evidence_count",
            "route",
            "task_name",
        ]:
            value = self._get_value(event.payload, key)
            if value is not None and value != "":
                parts.append(f"{key}={value}")

        return " ".join(parts)

    @staticmethod
    def _get_value(payload: dict[str, Any], key: str) -> Any:
        if key in payload:
            return payload[key]
        extra = payload.get("extra_fields")
        if isinstance(extra, dict):
            return extra.get(key)
        return None

    def _now_ms(self) -> int:
        return int(self._clock() * 1000)


def build_console_progress_reporter(settings: Settings, run_id: str) -> ConsoleProgressReporter:
    """Factory for console progress reporter."""

    return ConsoleProgressReporter(settings=settings, run_id=run_id)
