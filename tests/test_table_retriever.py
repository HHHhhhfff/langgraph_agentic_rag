from __future__ import annotations

from agentic_rag.retrieval.table_retriever import TableRetriever
from agentic_rag.schemas import SearchHit


class DummyStore:
    def scroll_hits(self, *args, **kwargs):
        return [
            SearchHit(point_id="1", text="a", score=0.1, modality="table", table_markdown="| a | b |", metadata={}),
            SearchHit(point_id="2", text="x", score=0.1, modality="text", metadata={}),
        ]


def test_table_retriever_only_tables() -> None:
    retriever = TableRetriever(DummyStore())
    hits = retriever.retrieve("a", top_k=1)
    assert hits[0].modality == "table"
    assert hits[0].channel == "table"

