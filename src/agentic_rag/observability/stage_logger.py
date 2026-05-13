from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agentic_rag.config import Settings

BASE_FIELDS = {
    "run_id",
    "stage",
    "source",
    "parser",
    "modality",
    "latency_ms",
    "chunk_count",
    "vector_count",
    "upserted_count",
    "fallback",
    "error_type",
    "error_msg",
    "query_text",
    "query_plan",
    "channel",
    "doc_id",
    "page",
    "section_path",
    "evidence_count",
    "route",
    "task_name",
    "evidence_gain",
}


class JsonFormatter(logging.Formatter):
    """Simple JSON formatter for structured stage logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": int(record.created * 1000),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for field in BASE_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)

        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            for key, value in extra.items():
                if key not in payload:
                    payload[key] = value

        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Readable text formatter with stage context."""

    def format(self, record: logging.LogRecord) -> str:
        base = f"[{record.levelname}] {record.getMessage()}"
        parts: list[str] = []
        for field in ["run_id", "stage", "source", "modality", "latency_ms", "fallback", "error_type"]:
            if hasattr(record, field):
                value = getattr(record, field)
                if value is not None and value != "":
                    parts.append(f"{field}={value}")
        suffix = " | " + " ".join(parts) if parts else ""
        return base + suffix


@dataclass(slots=True)
class StageTimer:
    """Stage timer helper."""

    start: float

    @classmethod
    def start_now(cls) -> "StageTimer":
        return cls(start=time.perf_counter())

    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self.start) * 1000)


class StageLogger:
    """Structured stage logger with optional enable switch."""

    def __init__(self, settings: Settings, run_id: str = ""):
        self.settings = settings
        self.run_id = run_id
        self.logger = logging.getLogger("agentic_rag.stage")
        self.logger.propagate = False
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._ensure_handler()

    def _ensure_handler(self) -> None:
        self.logger.setLevel(getattr(logging, self.settings.log_level))
        for handler in list(self.logger.handlers):
            self.logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

        if self.settings.log_file_path:
            log_path = Path(self.settings.log_file_path)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handler: logging.Handler = logging.FileHandler(log_path, encoding="utf-8")
        else:
            handler = logging.StreamHandler()

        if self.settings.log_format == "text":
            formatter = TextFormatter()
        else:
            formatter = JsonFormatter()
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)

    def child(self, run_id: str) -> "StageLogger":
        return StageLogger(self.settings, run_id=run_id)

    def add_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(listener)

    def _emit(self, level: int, message: str, event_type: str, **fields: Any) -> None:
        payload = {
            "run_id": self.run_id,
            "stage": fields.pop("stage", ""),
            "source": fields.pop("source", None),
            "parser": fields.pop("parser", None),
            "modality": fields.pop("modality", None),
            "latency_ms": fields.pop("latency_ms", None),
            "chunk_count": fields.pop("chunk_count", None),
            "vector_count": fields.pop("vector_count", None),
            "upserted_count": fields.pop("upserted_count", None),
            "fallback": fields.pop("fallback", None),
            "error_type": fields.pop("error_type", None),
            "error_msg": fields.pop("error_msg", None),
            "extra_fields": fields,
        }
        event = {
            "event_type": event_type,
            "run_id": payload["run_id"],
            "stage": payload["stage"],
            "source": payload["source"],
            "parser": payload["parser"],
            "modality": payload["modality"],
            "latency_ms": payload["latency_ms"],
            "chunk_count": payload["chunk_count"],
            "vector_count": payload["vector_count"],
            "upserted_count": payload["upserted_count"],
            "fallback": payload["fallback"],
            "error_type": payload["error_type"],
            "error_msg": payload["error_msg"],
            "extra_fields": payload["extra_fields"],
        }
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                # Never let progress hooks affect core pipeline execution.
                pass
        if self.settings.enable_stage_log:
            self.logger.log(level, message, extra=payload)

    def log_stage_start(self, stage: str, **fields: Any) -> None:
        self._emit(logging.INFO, f"stage_start:{stage}", event_type="start", stage=stage, **fields)

    def log_stage_end(self, stage: str, latency_ms: int, **fields: Any) -> None:
        self._emit(
            logging.INFO,
            f"stage_end:{stage}",
            event_type="end",
            stage=stage,
            latency_ms=latency_ms,
            **fields,
        )
        if latency_ms >= self.settings.log_slow_stage_ms:
            self._emit(
                logging.WARNING,
                f"stage_slow:{stage}",
                event_type="warn",
                stage=stage,
                latency_ms=latency_ms,
                **fields,
            )

    def log_stage_error(self, stage: str, error: Exception, **fields: Any) -> None:
        self._emit(
            logging.ERROR,
            f"stage_error:{stage}",
            event_type="error",
            stage=stage,
            error_type=type(error).__name__,
            error_msg=str(error),
            **fields,
        )

    def log_counter(self, stage: str, **fields: Any) -> None:
        self._emit(logging.INFO, f"stage_counter:{stage}", event_type="counter", stage=stage, **fields)

    def log_warning(self, stage: str, message: str, **fields: Any) -> None:
        self._emit(logging.WARNING, message, event_type="warn", stage=stage, **fields)


def build_stage_logger(settings: Settings, run_id: str = "") -> StageLogger:
    """Factory function for stage logger."""

    return StageLogger(settings=settings, run_id=run_id)
