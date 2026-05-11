from __future__ import annotations

from agentic_rag.retrieval.bm25_index import BM25Index
from agentic_rag.schemas import SearchHit


def test_bm25_keyword_hit() -> None:
    docs = [
        SearchHit(point_id="1", text="linux find search file", score=0.0, metadata={"source": "a.md"}),
        SearchHit(point_id="2", text="database index", score=0.0, metadata={"source": "b.md"}),
    ]
    index = BM25Index.build(docs)
    hits = index.search("find file", top_k=1)
    assert hits[0].point_id == "1"
    assert hits[0].channel == "bm25"

