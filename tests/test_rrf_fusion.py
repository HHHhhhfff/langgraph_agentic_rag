from __future__ import annotations

from agentic_rag.retrieval.fusion import rrf_fuse
from agentic_rag.schemas import SearchHit


def test_rrf_fuses_multiple_channels() -> None:
    a = SearchHit(point_id="1", text="A", score=0.9, channel="vector", metadata={})
    b = SearchHit(point_id="1", text="A", score=0.8, channel="bm25", metadata={})
    c = SearchHit(point_id="2", text="B", score=0.7, channel="bm25", metadata={})
    fused = rrf_fuse([[a], [b, c]], k=60, top_k=2)
    assert fused[0].score_rrf is not None
    assert fused[0].point_id == "1"

