from __future__ import annotations

from pathlib import Path

from agentic_rag.config import Settings
from agentic_rag.ingestion.node_normalizer import NodeNormalizer
from agentic_rag.ingestion.node_schema import MultimodalIngestionResult
from agentic_rag.observability.stage_logger import StageLogger, StageTimer


class ImageAdapter:
    """Image ingestion adapter supporting direct or caption_text mode."""

    def __init__(self, settings: Settings, stage_logger: StageLogger | None = None, run_id: str = ""):
        self.settings = settings
        self.normalizer = NodeNormalizer()
        self.stage_logger = stage_logger
        self.run_id = run_id

    def _build_caption(self, path: Path) -> str:
        # Placeholder deterministic caption. Keeps pipeline runnable without extra VLM dependency.
        return f"Image file: {path.name}"

    def parse_file(self, path: str | Path) -> MultimodalIngestionResult:
        file_path = Path(path)
        if not file_path.exists():
            return MultimodalIngestionResult(
                failures=[{"source": str(file_path), "error": "image file not found"}]
            )
        timer = StageTimer.start_now()
        if self.stage_logger:
            self.stage_logger.log_stage_start("image_parse", source=str(file_path), parser="image_adapter", modality="image")

        text = None
        if self.settings.image_embed_mode == "caption_text" or self.settings.enable_image_caption:
            text = self._build_caption(file_path)

        node = self.normalizer.normalize(
            source=str(file_path),
            parser_name="image_adapter",
            chunk_index=0,
            modality="image",
            text=text,
            image_path=str(file_path),
            title=file_path.stem,
        )
        if self.stage_logger:
            self.stage_logger.log_counter(
                "image_node_built",
                source=str(file_path),
                parser="image_adapter",
                modality="image",
                caption_mode=self.settings.image_embed_mode,
            )
            self.stage_logger.log_stage_end(
                "image_parse",
                latency_ms=timer.elapsed_ms(),
                source=str(file_path),
                parser="image_adapter",
                modality="image",
                chunk_count=1,
            )
        return MultimodalIngestionResult(nodes=[node], failures=[])
