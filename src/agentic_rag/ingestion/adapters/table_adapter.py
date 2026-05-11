from __future__ import annotations

from pathlib import Path

from agentic_rag.ingestion.chunk_strategies import TableChunker
from agentic_rag.ingestion.node_normalizer import NodeNormalizer
from agentic_rag.ingestion.node_schema import MultimodalIngestionResult
from agentic_rag.observability.stage_logger import StageLogger, StageTimer


class TableAdapter:
    """Table adapter for CSV/TSV/markdown-table style content."""

    def __init__(self, stage_logger: StageLogger | None = None, run_id: str = ""):
        self.chunker = TableChunker()
        self.normalizer = NodeNormalizer()
        self.stage_logger = stage_logger
        self.run_id = run_id

    def _to_markdown(self, path: Path) -> str:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "|" in text and "\n" in text:
            return text

        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            return ""
        sep = "," if "," in lines[0] else "\t"
        rows = [ln.split(sep) for ln in lines]
        cols = max(len(r) for r in rows)
        rows = [r + [""] * (cols - len(r)) for r in rows]

        header = "| " + " | ".join(rows[0]) + " |"
        split = "| " + " | ".join(["---"] * cols) + " |"
        body = ["| " + " | ".join(r) + " |" for r in rows[1:]]
        return "\n".join([header, split, *body])

    def parse_file(self, path: str | Path) -> MultimodalIngestionResult:
        file_path = Path(path)
        if not file_path.exists():
            return MultimodalIngestionResult(
                failures=[{"source": str(file_path), "error": "table file not found"}]
            )
        timer = StageTimer.start_now()
        if self.stage_logger:
            self.stage_logger.log_stage_start("table_parse", source=str(file_path), parser="table_adapter", modality="table")

        markdown = self._to_markdown(file_path)
        chunks = self.chunker.chunk_markdown_table(markdown)

        nodes = []
        for idx, chunk in enumerate(chunks):
            nodes.append(
                self.normalizer.normalize(
                    source=str(file_path),
                    parser_name="table_adapter",
                    chunk_index=idx,
                    modality="table",
                    text=chunk,
                    table_markdown=chunk,
                    title=file_path.stem,
                )
            )
        if self.stage_logger:
            self.stage_logger.log_counter(
                "table_chunks",
                source=str(file_path),
                parser="table_adapter",
                modality="table",
                chunk_count=len(nodes),
            )
            self.stage_logger.log_stage_end(
                "table_parse",
                latency_ms=timer.elapsed_ms(),
                source=str(file_path),
                parser="table_adapter",
                modality="table",
                chunk_count=len(nodes),
            )
        return MultimodalIngestionResult(nodes=nodes, failures=[])
