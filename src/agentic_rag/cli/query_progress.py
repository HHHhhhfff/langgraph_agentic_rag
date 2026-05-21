from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TextIO


@dataclass(frozen=True)
class QueryProgressStage:
    key: str
    index: int
    label: str


class QueryProgress:
    """Plain stderr progress reporter for coarse TaskGraph query stages."""

    STAGES: dict[str, QueryProgressStage] = {
        "question_analyze": QueryProgressStage("question_analyze", 1, "问题分析"),
        "task_router": QueryProgressStage("task_router", 2, "任务路由"),
        "retrieval": QueryProgressStage("retrieval", 3, "检索"),
        "evidence_gate": QueryProgressStage("evidence_gate", 4, "证据校验"),
        "local_retry": QueryProgressStage("local_retry", 5, "局部重检"),
        "generation": QueryProgressStage("generation", 6, "内容生成"),
    }

    _REPEATABLE_STAGES = {"retrieval", "evidence_gate"}

    def __init__(
        self,
        *,
        enabled: bool = True,
        style: str = "plain",
        show_retry: bool = True,
        stream: TextIO | None = None,
    ) -> None:
        self.enabled = enabled and style == "plain"
        self.style = style
        self.show_retry = show_retry
        self.stream = stream or sys.stderr
        self._header_printed = False
        self._started: set[str] = set()
        self._finished: dict[str, str] = {}

    def start(self, stage_key: str) -> None:
        if not self.enabled:
            return
        if stage_key in self._finished:
            if stage_key not in self._REPEATABLE_STAGES:
                return
            self._finished.pop(stage_key, None)
            self._started.discard(stage_key)
        if stage_key in self._started:
            return
        self._started.add(stage_key)
        self._print_header()
        self._write(stage_key, "running")

    def done(self, stage_key: str) -> None:
        if not self.enabled or self._is_finished(stage_key):
            return
        self._started.add(stage_key)
        self._finished[stage_key] = "done"
        self._print_header()
        self._write(stage_key, "done")

    def skip(self, stage_key: str) -> None:
        if not self.enabled or self._is_finished(stage_key):
            return
        self._started.add(stage_key)
        self._finished[stage_key] = "skipped"
        self._print_header()
        self._write(stage_key, "skipped")

    def retry(self, stage_key: str, retry_count: int) -> None:
        if not self.enabled or not self.show_retry:
            return
        if self._finished.get(stage_key) in {"done", "skipped"}:
            return
        status = f"retry {retry_count}"
        if self._finished.get(stage_key) == status:
            return
        self._started.add(stage_key)
        self._finished[stage_key] = status
        self._print_header()
        self._write(stage_key, status)

    def fail(self, stage_key: str, reason: str) -> None:
        if not self.enabled:
            return
        status = f"failed: {self._safe_reason(reason)}"
        if self._finished.get(stage_key) == status:
            return
        self._started.add(stage_key)
        self._finished[stage_key] = status
        self._print_header()
        self._write(stage_key, status)

    def _is_finished(self, stage_key: str) -> bool:
        return stage_key in self._finished

    def _print_header(self) -> None:
        if self._header_printed:
            return
        print("Query progress:", file=self.stream, flush=True)
        self._header_printed = True

    def _write(self, stage_key: str, status: str) -> None:
        stage = self.STAGES.get(stage_key)
        if stage is None:
            return
        print(f"[{stage.index}/6] {stage.label} ... {status}", file=self.stream, flush=True)

    @staticmethod
    def _safe_reason(reason: str) -> str:
        text = str(reason or "").replace("\n", " ").replace("\r", " ").strip()
        return text[:120] if text else "unknown"
