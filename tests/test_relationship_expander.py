from __future__ import annotations

from agentic_rag.config import Settings
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


def test_relationship_expander_uses_context_and_related_node_ids() -> None:
    seed = SearchHit(
        point_id="1",
        node_id="table1",
        text="table",
        score=1.0,
        doc_id="d1",
        page=None,
        relationships={"context_node_ids": ["text1"], "context_next_node_id": "text2"},
        metadata={},
    )
    text1 = SearchHit(
        point_id="2",
        node_id="text1",
        text="before",
        score=0.5,
        doc_id="d1",
        page=None,
        relationships={"related_formula_node_ids": ["formula1"]},
        metadata={},
    )
    text2 = SearchHit(point_id="3", node_id="text2", text="after", score=0.4, doc_id="d1", page=None, metadata={})
    formula = SearchHit(point_id="4", node_id="formula1", text="f", score=0.3, doc_id="d1", page=None, metadata={})

    expander = RelationshipExpander([seed, text1, text2, formula])
    hits = expander.expand([seed], steps=2, page_window=0)

    ids = {h.node_id for h in hits}
    assert {"table1", "text1", "text2", "formula1"}.issubset(ids)


def test_relationship_expander_assigns_inherited_scores_to_related_table() -> None:
    settings = Settings(
        _env_file=None,
        rel_expand_min_seed_composite_score=0.3,
        rel_expand_seed_top_m=3,
        rel_expand_related_table_weight=0.9,
        rel_expand_context_text_enabled=False,
    )
    seed = SearchHit(
        point_id="1",
        node_id="text1",
        text="seed",
        score=0.8,
        doc_id="d1",
        page=None,
        modality="text",
        relationships={"related_table_node_ids": ["table1"]},
        metadata={"score_composite": 0.8},
    )
    table = SearchHit(
        point_id="2",
        node_id="table1",
        text="table",
        score=0.0,
        doc_id="d1",
        page=None,
        modality="table",
        metadata={},
    )

    hits = RelationshipExpander([seed, table], settings=settings).expand([seed], steps=1, page_window=0)
    table_hit = next(hit for hit in hits if hit.node_id == "table1")

    assert table_hit.metadata["retrieval_expanded_from_node_id"] == "text1"
    assert table_hit.metadata["retrieval_expansion_relation"] == "related_table_node_ids"
    assert table_hit.metadata["retrieval_candidate_pool"] == "related_modality"
    assert round(table_hit.metadata["score_composite"], 2) == 0.72
