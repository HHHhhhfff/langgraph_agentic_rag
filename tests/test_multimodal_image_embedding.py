from __future__ import annotations

from pathlib import Path

from agentic_rag.config import Settings
from agentic_rag.ingestion.chunker import ChunkConfig, TextChunker
from agentic_rag.ingestion.index_builder import IndexBuilder
from agentic_rag.ingestion.node_normalizer import NodeNormalizer
from agentic_rag.ingestion.node_schema import IngestionFailure, MultimodalIngestionResult
from agentic_rag.ingestion.parser import MarkdownParser
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


def test_image_embedding_no_fallback_records_failure(tmp_path: Path) -> None:
    settings = Settings(
        ingestion_engine="multimodal",
        multimodal_enabled=True,
        image_embed_mode="direct",
        image_embed_fallback_to_caption=False,
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
