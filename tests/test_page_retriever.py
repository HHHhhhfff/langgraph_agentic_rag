from __future__ import annotations

from agentic_rag.retrieval.page_retriever import PageRetriever
from agentic_rag.schemas import SearchHit


class DummyStore:
    def scroll_hits(self, *args, **kwargs):
        return [
            SearchHit(point_id="1", text="page one a", score=0.1, doc_id="d1", page=1, metadata={}),
            SearchHit(point_id="2", text="page two b", score=0.1, doc_id="d1", page=2, metadata={}),
        ]


def test_page_retriever_filters_page() -> None:
    retriever = PageRetriever(DummyStore())
    hits = retriever.retrieve("page two", page=2, top_k=1)
    assert hits[0].page == 2
    assert hits[0].channel == "page"

