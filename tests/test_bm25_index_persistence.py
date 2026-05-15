from __future__ import annotations

from agentic_rag.retrieval.bm25_index import BM25Index
from agentic_rag.schemas import SearchHit


def test_bm25_index_save_load_search_consistent(tmp_path) -> None:
    docs = [
        SearchHit(point_id="1", text="linux find search file", score=0.0, metadata={"source": "a.md"}),
        SearchHit(point_id="2", text="database index table", score=0.0, metadata={"source": "b.md"}),
    ]
    index = BM25Index.build(docs)
    path = tmp_path / "bm25.json"

    index.save(path)
    loaded = BM25Index.load(path)

    before = index.search("find file", top_k=1)
    after = loaded.search("find file", top_k=1)
    assert before[0].point_id == after[0].point_id == "1"
    assert after[0].score > 0

