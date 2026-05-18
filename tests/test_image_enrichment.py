from __future__ import annotations

from pathlib import Path

from agentic_rag.config import Settings
from agentic_rag.ingestion.adapters.image_vlm_client import ImageObjectDescription, ImageVLMDescription
from agentic_rag.ingestion.image_enrichment import ImageEnricher, image_semantic_type
from agentic_rag.ingestion.node_normalizer import NodeNormalizer
from agentic_rag.ingestion.node_schema import IngestionFailure, MultimodalIngestionResult


class StubVLM:
    def __init__(self, description: ImageVLMDescription | None = None, error: Exception | None = None):
        self.description = description
        self.error = error
        self.calls: list[str] = []

    def describe_image(self, image_path):
        self.calls.append(str(image_path))
        if self.error:
            raise self.error
        assert self.description is not None
        return self.description


class StubMinerU:
    def __init__(self, result: MultimodalIngestionResult | None = None, error: Exception | None = None):
        self.result = result or MultimodalIngestionResult()
        self.error = error
        self.calls: list[str] = []

    def parse_file(self, path):
        self.calls.append(str(path))
        if self.error:
            raise self.error
        return self.result


def _base_node(path: Path):
    return NodeNormalizer().normalize(
        source=str(path),
        parser_name="image_adapter",
        chunk_index=0,
        modality="image",
        text="Image file: img.png",
        image_path=str(path),
        title="img",
    )


def test_image_enrichment_disabled_keeps_whole_image_node(tmp_path: Path) -> None:
    image = tmp_path / "img.png"
    image.write_bytes(b"fake")
    node = _base_node(image)
    enricher = ImageEnricher(Settings(image_enrichment_enabled=False))

    nodes = enricher.enrich(image_path=image, base_node=node)

    assert len(nodes) == 1
    assert image_semantic_type(nodes[0]) == "whole_image"


def test_image_vlm_success_generates_caption_ocr_and_object_nodes(tmp_path: Path) -> None:
    image = tmp_path / "img.png"
    image.write_bytes(b"fake")
    description = ImageVLMDescription(
        caption="A chart showing accuracy growth",
        scene_type="chart",
        visible_text_summary="Accuracy 95%",
        objects=[ImageObjectDescription(label="line", description="upward trend", confidence=0.8)],
        confidence=0.9,
    )
    enricher = ImageEnricher(
        Settings(image_enrichment_enabled=True, image_vlm_caption_enabled=True),
        vlm_client=StubVLM(description),
    )

    nodes = enricher.enrich(image_path=image, base_node=_base_node(image))
    semantic_types = [image_semantic_type(node) for node in nodes]

    assert semantic_types == ["whole_image", "caption", "ocr", "object"]
    assert nodes[1].relationships["caption"] == "A chart showing accuracy growth"
    assert nodes[2].relationships["ocr_text"] == "Accuracy 95%"
    assert nodes[3].relationships["object_label"] == "line"


def test_image_vlm_failure_falls_back_to_whole_image(tmp_path: Path) -> None:
    image = tmp_path / "img.png"
    image.write_bytes(b"fake")
    enricher = ImageEnricher(
        Settings(image_enrichment_enabled=True, image_vlm_caption_enabled=True),
        vlm_client=StubVLM(error=RuntimeError("bad json")),
    )

    nodes = enricher.enrich(image_path=image, base_node=_base_node(image))

    assert len(nodes) == 1
    assert image_semantic_type(nodes[0]) == "whole_image"


def test_image_mineru_success_generates_ocr_node(tmp_path: Path) -> None:
    image = tmp_path / "img.png"
    image.write_bytes(b"fake")
    parsed = NodeNormalizer().normalize(
        source=str(image),
        parser_name="mineru",
        chunk_index=0,
        modality="text",
        text="OCR text from image",
    )
    mineru = StubMinerU(MultimodalIngestionResult(nodes=[parsed], failures=[]))
    enricher = ImageEnricher(
        Settings(image_enrichment_enabled=True, image_mineru_enrich_enabled=True, enable_mineru=True),
        mineru_adapter=mineru,  # type: ignore[arg-type]
    )

    nodes = enricher.enrich(image_path=image, base_node=_base_node(image))

    assert [image_semantic_type(node) for node in nodes] == ["whole_image", "ocr"]
    assert nodes[1].text == "OCR text from image"
    assert nodes[1].relationships["source_parser"] == "mineru:text"


def test_image_mineru_failure_falls_back(tmp_path: Path) -> None:
    image = tmp_path / "img.png"
    image.write_bytes(b"fake")
    mineru = StubMinerU(
        MultimodalIngestionResult(failures=[IngestionFailure(source=str(image), error="failed")])
    )
    enricher = ImageEnricher(
        Settings(image_enrichment_enabled=True, image_mineru_enrich_enabled=True, enable_mineru=True),
        mineru_adapter=mineru,  # type: ignore[arg-type]
    )

    nodes = enricher.enrich(image_path=image, base_node=_base_node(image))

    assert len(nodes) == 1
    assert image_semantic_type(nodes[0]) == "whole_image"
