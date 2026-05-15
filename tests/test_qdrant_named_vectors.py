from __future__ import annotations

from types import SimpleNamespace

import pytest
from qdrant_client.http import models

from agentic_rag.config import Settings
from agentic_rag.ingestion.node_schema import Node, NodeMetadata
from agentic_rag.schemas import DocumentChunk
from agentic_rag.store.qdrant_store import QdrantStore, QdrantStoreError


def _store(settings: Settings, client) -> QdrantStore:
    store = object.__new__(QdrantStore)
    store.settings = settings
    store.stage_logger = None
    store.named_vectors_enabled = settings.enable_named_vectors
    store.client = client
    return store


class CreateClient:
    def __init__(self):
        self.vectors_config = None

    def collection_exists(self, name):
        return False

    def recreate_collection(self, *, collection_name, vectors_config):
        self.vectors_config = vectors_config


def test_ensure_collection_creates_named_vectors() -> None:
    settings = Settings(enable_named_vectors=True)
    client = CreateClient()
    store = _store(settings, client)

    store.ensure_collection(vector_size=3)

    assert set(client.vectors_config) == {"text", "table", "image"}
    assert client.vectors_config["text"].size == 3


class ExistingSingleVectorClient:
    def collection_exists(self, name):
        return True

    def get_collection(self, name):
        return SimpleNamespace(
            config=SimpleNamespace(
                params=SimpleNamespace(vectors=models.VectorParams(size=3, distance=models.Distance.COSINE))
            )
        )


def test_named_vectors_reject_existing_single_vector_collection() -> None:
    store = _store(Settings(enable_named_vectors=True), ExistingSingleVectorClient())

    with pytest.raises(QdrantStoreError, match="existing=single"):
        store.ensure_collection(vector_size=3)


class RecordingUpsertClient:
    def __init__(self):
        self.points = []

    def upsert(self, *, collection_name, points, wait):
        self.points.extend(points)


def test_upsert_chunks_writes_text_named_vector() -> None:
    settings = Settings(enable_named_vectors=True)
    client = RecordingUpsertClient()
    store = _store(settings, client)
    chunk = DocumentChunk(chunk_id="c1", text="hello", metadata={"source": "a.md"})

    count = store.upsert_chunks([chunk], [[0.1, 0.2, 0.3]], batch_size=1)

    assert count == 1
    assert client.points[0].vector == {"text": [0.1, 0.2, 0.3]}
    assert client.points[0].payload["metadata"]["modality"] == "text"


def _node(node_id: str, modality: str) -> Node:
    return Node(
        node_id=node_id,
        modality=modality,
        text=f"{modality} text",
        table_markdown="| a | b |" if modality == "table" else None,
        image_path="img.png" if modality == "image" else None,
        formula_latex="E=mc^2" if modality == "formula" else None,
        metadata=NodeMetadata(
            source=f"{node_id}.md",
            doc_id=node_id,
            chunk_index=0,
            modality=modality,
        ),
    )


def test_upsert_nodes_writes_modality_named_vectors() -> None:
    settings = Settings(enable_named_vectors=True)
    client = RecordingUpsertClient()
    store = _store(settings, client)
    nodes = [_node("t", "text"), _node("tab", "table"), _node("img", "image"), _node("f", "formula")]
    vectors = [[1.0], [2.0], [3.0], [4.0]]

    store.upsert_nodes(nodes, vectors, batch_size=10)

    point_vectors = [point.vector for point in client.points]
    assert point_vectors == [{"text": [1.0]}, {"table": [2.0]}, {"image": [3.0]}, {"text": [4.0]}]


class SearchClient:
    def __init__(self):
        self.kwargs = None

    def query_points(self, **kwargs):
        self.kwargs = kwargs
        point = SimpleNamespace(id="p1", score=0.9, payload={"text": "hit", "metadata": {"source": "a.md"}})
        return SimpleNamespace(points=[point])


def test_search_uses_named_vector_query() -> None:
    settings = Settings(enable_named_vectors=True)
    client = SearchClient()
    store = _store(settings, client)

    hits = store.search([0.1, 0.2], top_k=2, vector_name="table")

    assert len(hits) == 1
    assert client.kwargs["using"] == "table"
    assert client.kwargs["query"] == [0.1, 0.2]


class SingleVectorUpsertClient:
    def __init__(self):
        self.points = []

    def upsert(self, *, collection_name, points, wait):
        self.points.extend(points)


def test_named_vectors_disabled_keeps_single_vector_upsert() -> None:
    settings = Settings(enable_named_vectors=False)
    client = SingleVectorUpsertClient()
    store = _store(settings, client)
    chunk = DocumentChunk(chunk_id="c1", text="hello")

    store.upsert_chunks([chunk], [[0.1, 0.2]], batch_size=1)

    assert client.points[0].vector == [0.1, 0.2]
