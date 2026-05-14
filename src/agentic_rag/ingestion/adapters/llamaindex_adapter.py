from __future__ import annotations

from pathlib import Path

import frontmatter

from agentic_rag.ingestion.formula_extractor import extract_formulas
from agentic_rag.ingestion.chunk_strategies import build_text_chunk_strategy
from agentic_rag.ingestion.node_normalizer import NodeNormalizer
from agentic_rag.ingestion.node_schema import MultimodalIngestionResult
from agentic_rag.observability.stage_logger import StageLogger, StageTimer


class LlamaIndexAdapterError(RuntimeError):
    """Raised when llamaindex parsing/chunking fails."""


class LlamaIndexAdapter:
    """LlamaIndex text adapter for markdown/txt/html-like plain text docs."""

    def __init__(self, settings, stage_logger: StageLogger | None = None, run_id: str = ""):
        self.settings = settings
        self.strategy = build_text_chunk_strategy(settings)
        self.normalizer = NodeNormalizer()
        self.stage_logger = stage_logger
        self.run_id = run_id

    def parse_file(self, path: str | Path) -> MultimodalIngestionResult:
        file_path = Path(path)
        if not file_path.exists():
            raise LlamaIndexAdapterError(f"File not found: {file_path}")
        timer = StageTimer.start_now()
        if self.stage_logger:
            self.stage_logger.log_stage_start("llamaindex_parse", source=str(file_path), parser="llamaindex")

        try:
            post = frontmatter.load(file_path)
            metadata = dict(post.metadata or {})
            content = str(post.content or "")
        except Exception:
            try:
                metadata = {}
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception as exc:
                if self.stage_logger:
                    self.stage_logger.log_stage_error("llamaindex_parse", exc, source=str(file_path), parser="llamaindex")
                raise LlamaIndexAdapterError(f"Failed reading file {file_path}: {exc}") from exc

        title = str(metadata.get("title") or file_path.stem)
        section = str(metadata.get("section")) if metadata.get("section") else None

        chunks = self.strategy.chunk_text(content)
        nodes = []
        idx = 0
        for chunk in chunks:
            nodes.append(
                self.normalizer.normalize(
                    source=str(file_path),
                    parser_name=f"llamaindex:{self.settings.text_chunk_parser}",
                    chunk_index=idx,
                    modality="text",
                    text=chunk,
                    title=title,
                    section=section,
                )
            )
            idx += 1

        formula_count = 0
        if self.settings.enable_formula_recognition:
            for formula in extract_formulas(content):
                nodes.append(
                    self.normalizer.normalize(
                        source=str(file_path),
                        parser_name=f"llamaindex:{self.settings.text_chunk_parser}:formula",
                        chunk_index=idx,
                        modality="formula",
                        text=formula.text,
                        formula_latex=formula.formula_latex,
                        title=title,
                        section=section,
                    )
                )
                idx += 1
                formula_count += 1

        if self.stage_logger:
            self.stage_logger.log_counter(
                "llamaindex_text_chunks",
                source=str(file_path),
                parser=f"llamaindex:{self.settings.text_chunk_parser}",
                chunk_count=len(chunks),
                modality="text",
            )
            self.stage_logger.log_counter(
                "formula_nodes",
                source=str(file_path),
                parser=f"llamaindex:{self.settings.text_chunk_parser}",
                modality="formula",
                formula_count=formula_count,
            )
            self.stage_logger.log_stage_end(
                "llamaindex_parse",
                latency_ms=timer.elapsed_ms(),
                source=str(file_path),
                parser=f"llamaindex:{self.settings.text_chunk_parser}",
                chunk_count=len(nodes),
            )
        return MultimodalIngestionResult(nodes=nodes, failures=[])
