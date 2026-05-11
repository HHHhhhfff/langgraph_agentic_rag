from __future__ import annotations

from agentic_rag.retrieval.relationship_expander import RelationshipExpander
from agentic_rag.schemas import SearchHit


def test_relationship_expander_nearby_pages_and_children() -> None:
    seed = SearchHit(
        point_id="1",
        node_id="n1",
        text="seed",
        score=1.0,
        doc_id="d1",
        page=2,
        relationships={"child_ids": ["n2"]},
        metadata={},
    )
    child = SearchHit(point_id="2", node_id="n2", text="child", score=0.5, doc_id="d1", page=3, metadata={})
    neighbor = SearchHit(point_id="3", node_id="n3", text="neighbor", score=0.4, doc_id="d1", page=1, metadata={})
    expander = RelationshipExpander([seed, child, neighbor])
    hits = expander.expand([seed], steps=1, page_window=1)
    ids = {h.node_id for h in hits}
    assert "n1" in ids and "n2" in ids and "n3" in ids

