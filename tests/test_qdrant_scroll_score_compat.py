from __future__ import annotations

from types import SimpleNamespace

from agentic_rag.store.qdrant_store import QdrantStore


def test_point_to_search_hit_without_score_field() -> None:
    point = SimpleNamespace(
        id="p1",
        payload={
            "text": "hello",
            "doc_id": "d1",
            "page": 2,
            "modality": "text",
            "metadata": {"source": "a.md", "title": "A", "chunk_index": 1},
        },
    )
    hit = QdrantStore._point_to_search_hit(point)
    assert hit.point_id == "p1"
    assert hit.score == 0.0
    assert hit.score_vector == 0.0

