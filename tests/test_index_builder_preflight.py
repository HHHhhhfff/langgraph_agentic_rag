from __future__ import annotations

import pytest

from agentic_rag.config import Settings
from agentic_rag.ingestion.index_builder import IndexBuildError, IndexBuilder


class DummyParser:
    pass


class DummyChunker:
    pass


class DummyEmbedding:
    pass


class PreflightFailStore:
    def validate_collection_compatibility(self, vector_size: int) -> None:
        raise RuntimeError(f"dimension mismatch for {vector_size}")


class PreflightPassStore:
    def __init__(self):
        self.vector_size = None

    def validate_collection_compatibility(self, vector_size: int) -> None:
        self.vector_size = vector_size


def _builder(settings: Settings, store) -> IndexBuilder:
    return IndexBuilder(
        settings=settings,
        parser=DummyParser(),
        chunker=DummyChunker(),
        embedding_provider=DummyEmbedding(),
        store=store,
        image_embedding_provider=DummyEmbedding(),
    )


def test_index_builder_preflight_fails_before_parsing() -> None:
    builder = _builder(
        Settings(embedding_dimensions=1024, ingestion_engine="legacy", multimodal_enabled=False),
        PreflightFailStore(),
    )

    with pytest.raises(IndexBuildError, match="Qdrant collection vector dimension check failed"):
        builder.build_from_directory("docs")


def test_index_builder_preflight_uses_embedding_dimensions() -> None:
    store = PreflightPassStore()
    builder = _builder(
        Settings(embedding_dimensions=1024, ingestion_engine="legacy", multimodal_enabled=False),
        store,
    )

    with pytest.raises(AttributeError):
        builder.build_from_directory("docs")
    assert store.vector_size == 1024
