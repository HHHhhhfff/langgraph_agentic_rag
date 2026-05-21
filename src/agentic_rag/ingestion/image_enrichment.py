from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agentic_rag.config import Settings
from agentic_rag.ingestion.adapters.image_vlm_client import ImageVLMClient, ImageVLMDescription
from agentic_rag.ingestion.adapters.mineru_adapter import MinerUAdapter
from agentic_rag.ingestion.node_normalizer import NodeNormalizer
from agentic_rag.ingestion.node_schema import Node
from agentic_rag.observability.stage_logger import StageLogger, StageTimer


IMAGE_SEMANTIC_WHOLE = "whole_image"
IMAGE_SEMANTIC_CAPTION = "caption"
IMAGE_SEMANTIC_OCR = "ocr"
IMAGE_SEMANTIC_OBJECT = "object"


class ImageDescriptionClient(Protocol):
    def describe_image(self, image_path: str | Path) -> ImageVLMDescription:
        raise NotImplementedError


class ImageEnricher:
    """Build optional pure-image derived evidence nodes."""

    def __init__(
        self,
        settings: Settings,
        *,
        vlm_client: ImageDescriptionClient | None = None,
        mineru_adapter: MinerUAdapter | None = None,
        stage_logger: StageLogger | None = None,
        run_id: str = "",
    ):
        self.settings = settings
        self.vlm_client = vlm_client
        self.mineru_adapter = mineru_adapter
        self.stage_logger = stage_logger
        self.run_id = run_id
        self.normalizer = NodeNormalizer()

    def enrich(self, *, image_path: str | Path, base_node: Node) -> list[Node]:
        """Return base whole-image node plus optional caption/OCR/object nodes."""

        if not self.settings.image_enrichment_enabled:
            return [self._as_whole_image(base_node)]

        path = Path(image_path)
        timer = StageTimer.start_now()
        if self.stage_logger:
            self.stage_logger.log_stage_start("image_enrichment", source=str(path), modality="image")

        nodes = [self._as_whole_image(base_node)]
        nodes.extend(self._vlm_nodes(path=path, base_node=base_node, start_index=len(nodes)))
        nodes.extend(self._mineru_nodes(path=path, base_node=base_node, start_index=len(nodes)))

        if self.stage_logger:
            semantic_counts: dict[str, int] = {}
            for node in nodes:
                semantic_type = image_semantic_type(node) or "unknown"
                semantic_counts[semantic_type] = semantic_counts.get(semantic_type, 0) + 1
            self.stage_logger.log_stage_end(
                "image_enrichment",
                latency_ms=timer.elapsed_ms(),
                source=str(path),
                modality="image",
                node_count=len(nodes),
                semantic_counts=semantic_counts,
            )
        return nodes

    def _as_whole_image(self, node: Node) -> Node:
        relationships = dict(node.relationships or {})
        relationships.setdefault("image_semantic_type", IMAGE_SEMANTIC_WHOLE)
        relationships.setdefault("source_parser", node.metadata.parser_name or "image_adapter")
        relationships.setdefault("image_path", node.image_path or node.metadata.source)
        relationships.setdefault("parent_image_node_id", node.node_id)
        return node.model_copy(update={"relationships": relationships}, deep=True)

    def _vlm_nodes(self, *, path: Path, base_node: Node, start_index: int) -> list[Node]:
        if not self.settings.image_vlm_caption_enabled:
            return []
        client = self.vlm_client or ImageVLMClient(self.settings)
        try:
            description = client.describe_image(path)
        except Exception as exc:
            error_msg = _clip(str(exc), 500)
            if self.stage_logger:
                self.stage_logger.log_warning(
                    "image_vlm_caption",
                    "image_vlm_failed_continue",
                    source=str(path),
                    modality="image",
                    error_type=type(exc).__name__,
                    error_msg=error_msg,
                    provider=self.settings.image_vlm_provider,
                    model=self.settings.image_vlm_model,
                    image_suffix=path.suffix.lower(),
                    image_exists=path.exists(),
                    image_size_bytes=path.stat().st_size if path.exists() else None,
                )
            return []

        nodes: list[Node] = []
        caption = _clip(description.caption, self.settings.image_caption_max_chars)
        if caption:
            nodes.append(
                self._derived_node(
                    path=path,
                    base_node=base_node,
                    chunk_index=start_index + len(nodes),
                    semantic_type=IMAGE_SEMANTIC_CAPTION,
                    parser_name="vlm:caption",
                    text=caption,
                    confidence=description.confidence,
                    extra={"caption": caption, "scene_type": description.scene_type},
                )
            )

        ocr_text = _clip(description.visible_text_summary, self.settings.image_ocr_max_chars)
        if ocr_text:
            nodes.append(
                self._derived_node(
                    path=path,
                    base_node=base_node,
                    chunk_index=start_index + len(nodes),
                    semantic_type=IMAGE_SEMANTIC_OCR,
                    parser_name="vlm:visible_text",
                    text=ocr_text,
                    confidence=description.confidence,
                    extra={"ocr_text": ocr_text, "scene_type": description.scene_type},
                )
            )

        for obj in description.objects[: self.settings.image_object_max_items]:
            text = _clip(" ".join(part for part in [obj.label, obj.description] if part).strip(), self.settings.image_caption_max_chars)
            if not text:
                continue
            nodes.append(
                self._derived_node(
                    path=path,
                    base_node=base_node,
                    chunk_index=start_index + len(nodes),
                    semantic_type=IMAGE_SEMANTIC_OBJECT,
                    parser_name="vlm:object",
                    text=text,
                    confidence=obj.confidence,
                    extra={"object_label": obj.label, "object_description": obj.description},
                )
            )
        return nodes

    def _mineru_nodes(self, *, path: Path, base_node: Node, start_index: int) -> list[Node]:
        if not (self.settings.image_mineru_enrich_enabled and self.settings.enable_mineru):
            return []
        adapter = self.mineru_adapter or MinerUAdapter(self.settings, stage_logger=self.stage_logger, run_id=self.run_id)
        try:
            result = adapter.parse_file(path)
        except Exception as exc:
            if self.stage_logger:
                self.stage_logger.log_warning(
                    "image_mineru_enrichment",
                    "image_mineru_failed_continue",
                    source=str(path),
                    modality="image",
                    error_type=type(exc).__name__,
                    error_msg=str(exc),
                )
            return []
        if result.failures and self.stage_logger:
            for failure in result.failures:
                self.stage_logger.log_warning(
                    "image_mineru_enrichment",
                    "image_mineru_failure_continue",
                    source=failure.source,
                    modality="image",
                    error_msg=failure.error,
                )

        nodes: list[Node] = []
        for parsed in result.nodes:
            text = _clip(parsed.table_markdown or parsed.formula_latex or parsed.text or "", self.settings.image_ocr_max_chars)
            if not text:
                continue
            semantic_type = IMAGE_SEMANTIC_OCR if parsed.modality in {"text", "image"} else str(parsed.modality)
            relationships = dict(parsed.relationships or {})
            relationships.update(
                _base_relationships(
                    base_node=base_node,
                    image_path=str(path),
                    semantic_type=semantic_type,
                    source_parser=f"mineru:{parsed.modality}",
                    confidence=None,
                )
            )
            relationships.setdefault("ocr_text", text)
            nodes.append(
                self.normalizer.normalize(
                    source=str(path),
                    parser_name=f"mineru:image_{semantic_type}",
                    chunk_index=start_index + len(nodes),
                    modality="image" if semantic_type == IMAGE_SEMANTIC_OCR else parsed.modality,
                    text=text,
                    image_path=str(path),
                    table_markdown=parsed.table_markdown if parsed.modality == "table" else None,
                    formula_latex=parsed.formula_latex if parsed.modality == "formula" else None,
                    page=parsed.metadata.page,
                    title=base_node.metadata.title,
                    section=parsed.metadata.section,
                    relationships=relationships,
                )
            )
        return nodes

    def _derived_node(
        self,
        *,
        path: Path,
        base_node: Node,
        chunk_index: int,
        semantic_type: str,
        parser_name: str,
        text: str,
        confidence: float | None,
        extra: dict[str, object],
    ) -> Node:
        relationships = _base_relationships(
            base_node=base_node,
            image_path=str(path),
            semantic_type=semantic_type,
            source_parser=parser_name,
            confidence=confidence,
        )
        relationships.update({key: value for key, value in extra.items() if value not in (None, "")})
        return self.normalizer.normalize(
            source=str(path),
            parser_name=parser_name,
            chunk_index=chunk_index,
            modality="image",
            text=text,
            image_path=str(path),
            title=base_node.metadata.title,
            relationships=relationships,
        )


def image_semantic_type(node: Node) -> str | None:
    value = (node.relationships or {}).get("image_semantic_type")
    return str(value) if value else None


def _base_relationships(
    *,
    base_node: Node,
    image_path: str,
    semantic_type: str,
    source_parser: str,
    confidence: float | None,
) -> dict[str, object]:
    relationships: dict[str, object] = {
        "parent_image_node_id": base_node.node_id,
        "image_semantic_type": semantic_type,
        "source_parser": source_parser,
        "image_path": image_path,
    }
    if confidence is not None:
        relationships["confidence"] = float(confidence)
    return relationships


def _clip(text: str | None, max_chars: int) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    return value[: max(1, max_chars)]
