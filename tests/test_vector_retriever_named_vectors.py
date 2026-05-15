from __future__ import annotations

from agentic_rag.config import Settings
from agentic_rag.retrieval.retriever import VectorRetriever
from agentic_rag.schemas import SearchHit


class RecordingStore:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def search(self, *, query_vector, top_k, filters=None, vector_name=None):
        self.calls.append(
            {
                "query_vector": query_vector,
                "top_k": top_k,
                "filters": filters,
                "vector_name": vector_name,
            }
        )
        return self.responses.pop(0)


def _hit(score: float = 0.9) -> SearchHit:
    return SearchHit(point_id="p1", text="hit", score=score, metadata={"source": "a.md"})


def test_vector_retriever_passes_vector_name_to_store() -> None:
    store = RecordingStore([[ _hit() ]])
    retriever = VectorRetriever(Settings(retrieval_min_score=0.1), store)

    result = retriever.retrieve([0.1, 0.2], filters={"modality": "table"}, vector_name="table", top_k=5)

    assert result.hits
    assert store.calls[0]["vector_name"] == "table"
    assert store.calls[0]["top_k"] == 5


def test_vector_retriever_filter_fallback_keeps_vector_name() -> None:
    store = RecordingStore([[], [_hit()]])
    retriever = VectorRetriever(Settings(retrieval_min_score=0.1, retrieval_filter_fallback=True), store)

    result = retriever.retrieve([0.1], filters={"source": "missing.md"}, vector_name="image")

    assert result.fallback_used is True
    assert len(store.calls) == 2
    assert store.calls[0]["vector_name"] == "image"
    assert store.calls[1]["vector_name"] == "image"


def test_vector_retriever_without_vector_name_remains_compatible() -> None:
    store = RecordingStore([[_hit()]])
    retriever = VectorRetriever(Settings(retrieval_min_score=0.1), store)

    result = retriever.retrieve([0.1])

    assert result.hits
    assert store.calls[0]["vector_name"] is None
