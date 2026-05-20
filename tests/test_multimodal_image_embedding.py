from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agentic_rag.config import Settings
from agentic_rag.ingestion.chunker import ChunkConfig, TextChunker
from agentic_rag.ingestion.index_builder import IndexBuilder
from agentic_rag.ingestion.node_normalizer import NodeNormalizer
from agentic_rag.ingestion.node_schema import IngestionFailure, MultimodalIngestionResult
from agentic_rag.ingestion.parser import MarkdownParser
from agentic_rag.cli import build_index
from agentic_rag.models.providers import EmbeddingProvider, ImageEmbeddingProvider, ProviderError
from agentic_rag.store.qdrant_store import QdrantStore


class DummyTextEmbedding(EmbeddingProvider):
    def __init__(self):
        self.calls: list[list[str]] = []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[float(len(t) + i), 0.0, 1.0] for i, t in enumerate(texts)]


class DummyImageEmbedding(ImageEmbeddingProvider):
    def __init__(self, vectors: list[list[float]] | None = None, error: Exception | None = None):
        self.vectors = vectors or []
        self.error = error
        self.calls: list[list[str]] = []

    def embed_images(self, image_paths: list[str]) -> list[list[float]]:
        self.calls.append(image_paths)
        if self.error is not None:
            raise self.error
        return self.vectors


class DummyStore(QdrantStore):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.stage_logger = None
        self.collection_dim = 0
        self.last_nodes = []
        self.last_vectors = []

    def set_stage_logger(self, stage_logger):
        self.stage_logger = stage_logger

    def ensure_collection(self, vector_size: int) -> None:
        self.collection_dim = vector_size

    def upsert_nodes(self, nodes, vectors, batch_size: int = 64) -> int:
        self.last_nodes = list(nodes)
        self.last_vectors = list(vectors)
        return len(nodes)


class DummyOrchestrator:
    def __init__(self, result: MultimodalIngestionResult):
        self.result = result

    def parse_directory(self, _directory):
        return self.result


def _build_multimodal_nodes(tmp_path: Path):
    n = NodeNormalizer()
    text_node = n.normalize(
        source=str(tmp_path / "a.md"),
        parser_name="x",
        chunk_index=0,
        modality="text",
        text="hello text",
    )
    table_node = n.normalize(
        source=str(tmp_path / "a.csv"),
        parser_name="x",
        chunk_index=1,
        modality="table",
        text="|a|b|\n|---|---|\n|1|2|",
        table_markdown="|a|b|\n|---|---|\n|1|2|",
    )
    image_path = tmp_path / "img.png"
    image_path.write_bytes(b"fake")
    image_node = n.normalize(
        source=str(image_path),
        parser_name="x",
        chunk_index=2,
        modality="image",
        text="Image file: img.png",
        image_path=str(image_path),
    )
    return [text_node, table_node, image_node]


def _build_builder(settings: Settings, text_provider: DummyTextEmbedding, image_provider: DummyImageEmbedding, store: DummyStore) -> IndexBuilder:
    parser = MarkdownParser()
    chunker = TextChunker(ChunkConfig(chunk_size=500, chunk_overlap=20, chunk_min_length=20))
    return IndexBuilder(
        settings=settings,
        parser=parser,
        chunker=chunker,
        embedding_provider=text_provider,
        image_embedding_provider=image_provider,
        store=store,
        stage_logger=None,
        run_id="run-test",
    )


def test_multimodal_direct_image_embedding_path(tmp_path: Path) -> None:
    settings = Settings(
        ingestion_engine="multimodal",
        multimodal_enabled=True,
        image_embed_mode="direct",
        image_embed_fallback_to_caption=True,
        embedding_dimensions=None,
    )
    nodes = _build_multimodal_nodes(tmp_path)

    text_provider = DummyTextEmbedding()
    image_provider = DummyImageEmbedding(vectors=[[9.0, 9.1, 9.2]])
    store = DummyStore(settings)
    builder = _build_builder(settings, text_provider, image_provider, store)
    builder.multimodal_orchestrator = DummyOrchestrator(
        MultimodalIngestionResult(nodes=nodes, failures=[])
    )

    summary = builder.build_from_directory(str(tmp_path))

    assert summary.upserted == 3
    assert len(text_provider.calls) == 1
    assert len(image_provider.calls) == 1
    assert image_provider.calls[0] == [nodes[2].image_path]
    assert store.last_vectors[2] == [9.0, 9.1, 9.2]


def test_image_embedding_fallback_to_caption(tmp_path: Path) -> None:
    settings = Settings(
        ingestion_engine="multimodal",
        multimodal_enabled=True,
        image_embed_mode="direct",
        image_embed_fallback_to_caption=True,
        embedding_dimensions=None,
    )
    nodes = _build_multimodal_nodes(tmp_path)

    text_provider = DummyTextEmbedding()
    image_provider = DummyImageEmbedding(error=ProviderError("image provider down"))
    store = DummyStore(settings)
    builder = _build_builder(settings, text_provider, image_provider, store)
    builder.multimodal_orchestrator = DummyOrchestrator(
        MultimodalIngestionResult(nodes=nodes, failures=[])
    )

    summary = builder.build_from_directory(str(tmp_path))

    assert summary.upserted == 3
    assert len(text_provider.calls) >= 2
    # second text call should be caption fallback for image
    assert any("Image file: img.png" in batch for batch in text_provider.calls)


def test_image_derived_nodes_use_text_embedding(tmp_path: Path) -> None:
    settings = Settings(
        ingestion_engine="multimodal",
        multimodal_enabled=True,
        image_embed_mode="direct",
        embedding_dimensions=None,
    )
    image_path = tmp_path / "img.png"
    image_path.write_bytes(b"fake")
    normalizer = NodeNormalizer()
    whole = normalizer.normalize(
        source=str(image_path),
        parser_name="image_adapter",
        chunk_index=0,
        modality="image",
        text="Image file: img.png",
        image_path=str(image_path),
        relationships={"image_semantic_type": "whole_image"},
    )
    caption = normalizer.normalize(
        source=str(image_path),
        parser_name="vlm:caption",
        chunk_index=1,
        modality="image",
        text="A chart caption",
        image_path=str(image_path),
        relationships={"image_semantic_type": "caption", "parent_image_node_id": whole.node_id},
    )
    ocr = normalizer.normalize(
        source=str(image_path),
        parser_name="vlm:visible_text",
        chunk_index=2,
        modality="image",
        text="OCR text",
        image_path=str(image_path),
        relationships={"image_semantic_type": "ocr", "parent_image_node_id": whole.node_id},
    )
    obj = normalizer.normalize(
        source=str(image_path),
        parser_name="vlm:object",
        chunk_index=3,
        modality="image",
        text="object description",
        image_path=str(image_path),
        relationships={"image_semantic_type": "object", "parent_image_node_id": whole.node_id},
    )

    text_provider = DummyTextEmbedding()
    image_provider = DummyImageEmbedding(vectors=[[9.0, 9.1, 9.2]])
    store = DummyStore(settings)
    builder = _build_builder(settings, text_provider, image_provider, store)
    builder.multimodal_orchestrator = DummyOrchestrator(
        MultimodalIngestionResult(nodes=[whole, caption, ocr, obj], failures=[])
    )

    summary = builder.build_from_directory(str(tmp_path))

    assert summary.upserted == 4
    assert image_provider.calls == [[str(image_path)]]
    embedded_texts = [text for batch in text_provider.calls for text in batch]
    assert embedded_texts == ["A chart caption", "OCR text", "object description"]
    assert store.last_vectors[0] == [9.0, 9.1, 9.2]


def test_image_embedding_no_fallback_records_failure(tmp_path: Path) -> None:
    settings = Settings(
        ingestion_engine="multimodal",
        multimodal_enabled=True,
        image_embed_mode="direct",
        image_embed_fallback_to_caption=False,
        embedding_dimensions=None,
    )
    nodes = _build_multimodal_nodes(tmp_path)

    text_provider = DummyTextEmbedding()
    image_provider = DummyImageEmbedding(error=ProviderError("image provider down"))
    store = DummyStore(settings)
    builder = _build_builder(settings, text_provider, image_provider, store)
    builder.multimodal_orchestrator = DummyOrchestrator(
        MultimodalIngestionResult(nodes=nodes, failures=[IngestionFailure(source="x", error="y")])
    )

    summary = builder.build_from_directory(str(tmp_path))

    assert summary.upserted == 2
    assert summary.failed_files >= 2
    assert len(store.last_nodes) == 2
    assert all(n.modality != "image" for n in store.last_nodes)


def test_formula_nodes_use_formula_latex_for_embedding(tmp_path: Path) -> None:
    settings = Settings(
        ingestion_engine="multimodal",
        multimodal_enabled=True,
        embedding_dimensions=None,
    )
    normalizer = NodeNormalizer()
    formula_node = normalizer.normalize(
        source=str(tmp_path / "math.md"),
        parser_name="llamaindex:formula",
        chunk_index=0,
        modality="formula",
        text="Formula (display): E=mc^2",
        formula_latex="E=mc^2",
    )

    text_provider = DummyTextEmbedding()
    image_provider = DummyImageEmbedding()
    store = DummyStore(settings)
    builder = _build_builder(settings, text_provider, image_provider, store)
    builder.multimodal_orchestrator = DummyOrchestrator(
        MultimodalIngestionResult(nodes=[formula_node], failures=[])
    )

    summary = builder.build_from_directory(str(tmp_path))

    assert summary.upserted == 1
    assert text_provider.calls[0] == ["E=mc^2"]
    assert store.last_nodes[0].modality == "formula"


def test_build_index_cli_prints_named_vector_counts(monkeypatch, capsys) -> None:
    summary = SimpleNamespace(
        documents=2,
        chunks=4,
        vectors=4,
        upserted=4,
        vector_size=1024,
        failed_files=0,
        named_vectors_enabled=True,
        named_vector_counts={"text": 2, "table": 1, "image": 1},
    )

    class DummyBuilder:
        def build_from_directory(self, docs):
            return summary

    monkeypatch.setattr(build_index, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(build_index, "build_stage_logger", lambda settings, run_id: SimpleNamespace(
        add_listener=lambda *args, **kwargs: None,
        log_stage_start=lambda *args, **kwargs: None,
        log_stage_end=lambda *args, **kwargs: None,
        log_stage_error=lambda *args, **kwargs: None,
        log_counter=lambda *args, **kwargs: None,
    ))
    monkeypatch.setattr(build_index, "build_console_progress_reporter", lambda settings, run_id: SimpleNamespace(
        handle_event=lambda *args, **kwargs: None
    ))
    monkeypatch.setattr(build_index, "MarkdownParser", lambda: None)
    monkeypatch.setattr(build_index, "build_default_chunker", lambda settings: None)
    monkeypatch.setattr(build_index, "build_embedding_provider", lambda settings: None)
    monkeypatch.setattr(build_index, "QdrantStore", lambda settings: None)
    monkeypatch.setattr(build_index, "IndexBuilder", lambda **kwargs: DummyBuilder())
    monkeypatch.setattr("sys.argv", ["build_index", "--docs", "docs"])

    assert build_index.main() == 0
    out = capsys.readouterr().out
    assert "Index build completed" in out
    assert "- named_vectors_enabled: true" in out
    assert "- named_vector_counts:" in out
    assert "  - table: 1" in out
    assert "  - image: 1" in out
