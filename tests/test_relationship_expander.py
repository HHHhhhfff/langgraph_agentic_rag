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
    hits = expander.expand([seed], steps=1, page_window=1, mode="routed")
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
    assert table_hit.metadata["retrieval_expansion_mode"] == "auto"
    assert table_hit.metadata["retrieval_expansion_allowed_by"] == "related_modality_threshold"
    assert table_hit.metadata["retrieval_seed_rank"] == 1
    assert table_hit.metadata["retrieval_seed_threshold"] == 0.3
    assert round(table_hit.metadata["score_composite"], 2) == 0.72


def test_auto_expansion_does_not_expand_related_table_below_seed_threshold() -> None:
    settings = Settings(
        _env_file=None,
        rel_expand_min_seed_composite_score=0.5,
        rel_expand_context_text_enabled=False,
    )
    seed = SearchHit(
        point_id="1",
        node_id="text1",
        text="seed",
        score=0.31,
        modality="text",
        relationships={"related_table_node_ids": ["table1"]},
        metadata={"score_composite": 0.31, "modality": "text"},
    )
    table = SearchHit(point_id="2", node_id="table1", text="table", score=0.0, modality="table", metadata={"modality": "table"})

    hits = RelationshipExpander([seed, table], settings=settings).expand([seed], page_window=0, mode="auto")

    assert {hit.node_id for hit in hits} == {"text1"}


def test_auto_expansion_does_not_expand_related_formula_after_seed_top_m() -> None:
    settings = Settings(
        _env_file=None,
        rel_expand_seed_top_m=1,
        rel_expand_min_seed_composite_score=0.1,
        rel_expand_context_text_enabled=False,
    )
    low_rank_seed = SearchHit(
        point_id="2",
        node_id="text2",
        text="second",
        score=0.9,
        modality="text",
        relationships={"related_formula_node_ids": ["formula1"]},
        metadata={"score_composite": 0.9, "modality": "text"},
    )
    first_seed = SearchHit(point_id="1", node_id="text1", text="first", score=1.0, modality="text", metadata={"score_composite": 1.0})
    formula = SearchHit(point_id="3", node_id="formula1", text="f", score=0.0, modality="formula", metadata={"modality": "formula"})

    hits = RelationshipExpander([first_seed, low_rank_seed, formula], settings=settings).expand(
        [first_seed, low_rank_seed],
        page_window=0,
        mode="auto",
    )

    assert "formula1" not in {hit.node_id for hit in hits}


def test_auto_expansion_ignores_same_page_relationships() -> None:
    settings = Settings(
        _env_file=None,
        rel_expand_same_page_relationships=True,
        rel_expand_min_seed_composite_score=0.0,
        rel_expand_context_text_enabled=False,
    )
    seed = SearchHit(
        point_id="1",
        node_id="text1",
        text="seed",
        score=1.0,
        modality="text",
        relationships={"same_page_node_ids": ["table1"]},
        metadata={"score_composite": 1.0, "modality": "text"},
    )
    table = SearchHit(point_id="2", node_id="table1", text="table", score=0.0, modality="table", metadata={"modality": "table"})

    hits = RelationshipExpander([seed, table], settings=settings).expand([seed], page_window=0, mode="auto")

    assert "table1" not in {hit.node_id for hit in hits}


def test_auto_prev_next_expansion_only_allows_text_neighbors() -> None:
    settings = Settings(
        _env_file=None,
        rel_expand_context_text_enabled=True,
        rel_expand_context_text_min_seed_score=0.1,
    )
    seed = SearchHit(
        point_id="1",
        node_id="text1",
        text="seed",
        score=0.9,
        modality="text",
        relationships={"prev_id": "table1", "next_id": "text2"},
        metadata={"score_composite": 0.9, "modality": "text"},
    )
    table = SearchHit(point_id="2", node_id="table1", text="table", score=0.0, modality="table", metadata={"modality": "table"})
    text2 = SearchHit(point_id="3", node_id="text2", text="next", score=0.0, modality="text", metadata={"modality": "text"})

    hits = RelationshipExpander([seed, table, text2], settings=settings).expand([seed], page_window=0, mode="auto")
    ids = {hit.node_id for hit in hits}

    assert "text2" in ids
    assert "table1" not in ids


def test_routed_expansion_allows_same_page_with_routed_thresholds() -> None:
    settings = Settings(
        _env_file=None,
        rel_expand_routed_min_seed_score=0.5,
        rel_expand_routed_seed_top_m=2,
        rel_expand_routed_max_per_seed=2,
        rel_expand_routed_max_total=2,
        rel_expand_routed_allowed_modalities="text,table",
    )
    seed = SearchHit(
        point_id="1",
        node_id="text1",
        text="seed",
        score=0.8,
        modality="text",
        relationships={"same_page_node_ids": ["table1"]},
        metadata={"score_composite": 0.8, "modality": "text"},
    )
    table = SearchHit(point_id="2", node_id="table1", text="table", score=0.0, modality="table", metadata={"modality": "table"})

    hits = RelationshipExpander([seed, table], settings=settings).expand([seed], page_window=0, mode="routed")
    table_hit = next(hit for hit in hits if hit.node_id == "table1")

    assert table_hit.metadata["retrieval_candidate_pool"] == "routed"
    assert table_hit.metadata["retrieval_expansion_mode"] == "routed"
    assert table_hit.metadata["retrieval_seed_threshold"] == 0.5


def test_retry_expansion_allows_page_window_only_in_retry_mode() -> None:
    settings = Settings(
        _env_file=None,
        rel_expand_retry_min_seed_score=0.5,
        rel_expand_retry_seed_top_m=1,
        rel_expand_retry_max_per_seed=2,
        rel_expand_retry_max_total=2,
    )
    seed = SearchHit(
        point_id="1",
        node_id="text1",
        text="seed",
        score=0.8,
        doc_id="d1",
        page=1,
        modality="text",
        metadata={"score_composite": 0.8, "modality": "text"},
    )
    neighbor = SearchHit(point_id="2", node_id="text2", text="same page", score=0.0, doc_id="d1", page=1, modality="text")

    auto_hits = RelationshipExpander([seed, neighbor], settings=settings).expand([seed], page_window=1, mode="auto")
    retry_hits = RelationshipExpander([seed, neighbor], settings=settings).expand([seed], page_window=1, mode="retry")

    assert "text2" not in {hit.node_id for hit in auto_hits}
    assert "text2" in {hit.node_id for hit in retry_hits}


def test_same_page_switch_disables_routed_and_retry_same_page() -> None:
    settings = Settings(
        _env_file=None,
        rel_expand_same_page_relationships=False,
        rel_expand_routed_min_seed_score=0.0,
        rel_expand_retry_min_seed_score=0.0,
    )
    seed = SearchHit(
        point_id="1",
        node_id="text1",
        text="seed",
        score=1.0,
        modality="text",
        relationships={"same_page_node_ids": ["text2"]},
        metadata={"score_composite": 1.0, "modality": "text"},
    )
    text2 = SearchHit(point_id="2", node_id="text2", text="same", score=0.0, modality="text", metadata={"modality": "text"})

    routed = RelationshipExpander([seed, text2], settings=settings).expand([seed], page_window=0, mode="routed")
    retry = RelationshipExpander([seed, text2], settings=settings).expand([seed], page_window=0, mode="retry")

    assert "text2" not in {hit.node_id for hit in routed}
    assert "text2" not in {hit.node_id for hit in retry}


def test_related_formula_priority_over_page_window() -> None:
    settings = Settings(
        _env_file=None,
        rel_expand_min_seed_composite_score=0.1,
        rel_expand_related_formula_weight=0.85,
        rel_expand_routed_min_seed_score=0.1,
        rel_expand_routed_seed_top_m=2,
        rel_expand_routed_max_per_seed=5,
        rel_expand_routed_max_total=5,
        rel_expand_page_window_weight=0.30,
    )
    seed = SearchHit(
        point_id="1",
        node_id="text1",
        text="seed",
        score=0.8,
        doc_id="d1",
        page=1,
        modality="text",
        relationships={"related_formula_node_ids": ["formula1"]},
        metadata={"score_composite": 0.8, "modality": "text"},
    )
    formula = SearchHit(
        point_id="2",
        node_id="formula1",
        text="formula",
        score=0.0,
        doc_id="d1",
        page=1,
        modality="formula",
        metadata={"modality": "formula"},
    )

    hits = RelationshipExpander([seed, formula], settings=settings).expand([seed], page_window=1, mode="routed")
    formula_hit = next(hit for hit in hits if hit.node_id == "formula1")

    assert formula_hit.metadata["retrieval_expansion_relation"] == "related_formula_node_ids"
    assert formula_hit.metadata["retrieval_relation_weight"] == 0.85
    assert formula_hit.metadata["retrieval_expansion_relation_priority"] == 100
    assert round(formula_hit.metadata["retrieval_inherited_score"], 2) == 0.68


def test_related_table_priority_over_same_page() -> None:
    settings = Settings(
        _env_file=None,
        rel_expand_min_seed_composite_score=0.1,
        rel_expand_related_table_weight=0.9,
        rel_expand_routed_min_seed_score=0.1,
    )
    seed = SearchHit(
        point_id="1",
        node_id="text1",
        text="seed",
        score=0.8,
        modality="text",
        relationships={"same_page_node_ids": ["table1"], "related_table_node_ids": ["table1"]},
        metadata={"score_composite": 0.8, "modality": "text"},
    )
    table = SearchHit(point_id="2", node_id="table1", text="table", score=0.0, modality="table", metadata={"modality": "table"})

    hits = RelationshipExpander([seed, table], settings=settings).expand([seed], page_window=0, mode="routed")
    table_hit = next(hit for hit in hits if hit.node_id == "table1")

    assert table_hit.metadata["retrieval_expansion_relation"] == "related_table_node_ids"
    assert table_hit.metadata["retrieval_relation_weight"] == 0.9


def test_stronger_relation_upgrades_existing_weak_relation_metadata() -> None:
    settings = Settings(
        _env_file=None,
        rel_expand_min_seed_composite_score=0.1,
        rel_expand_related_formula_weight=0.85,
        rel_expand_routed_min_seed_score=0.1,
        rel_expand_page_window_weight=0.3,
    )
    formula = SearchHit(point_id="2", node_id="formula1", text="formula", score=0.0, doc_id="d1", page=1, modality="formula")
    weak_seed = SearchHit(
        point_id="1",
        node_id="weak",
        text="weak",
        score=0.8,
        doc_id="d1",
        page=1,
        modality="text",
        metadata={"score_composite": 0.8, "modality": "text"},
    )
    strong_seed = SearchHit(
        point_id="3",
        node_id="strong",
        text="strong",
        score=0.8,
        modality="text",
        relationships={"related_formula_node_ids": ["formula1"]},
        metadata={"score_composite": 0.8, "modality": "text"},
    )

    hits = RelationshipExpander([weak_seed, strong_seed, formula], settings=settings).expand(
        [weak_seed, strong_seed],
        page_window=1,
        mode="routed",
    )
    formula_hit = next(hit for hit in hits if hit.node_id == "formula1")

    assert formula_hit.metadata["retrieval_expansion_relation"] == "related_formula_node_ids"
    assert formula_hit.metadata["retrieval_expansion_replaced_relation"] == "page_window"


def test_page_window_weight_is_configurable() -> None:
    settings = Settings(
        _env_file=None,
        rel_expand_page_window_weight=0.11,
        rel_expand_routed_min_seed_score=0.1,
    )
    seed = SearchHit(
        point_id="1",
        node_id="text1",
        text="seed",
        score=0.8,
        doc_id="d1",
        page=1,
        modality="text",
        metadata={"score_composite": 0.8, "modality": "text"},
    )
    neighbor = SearchHit(point_id="2", node_id="text2", text="same page", score=0.0, doc_id="d1", page=1, modality="text")

    hits = RelationshipExpander([seed, neighbor], settings=settings).expand([seed], page_window=1, mode="routed")
    neighbor_hit = next(hit for hit in hits if hit.node_id == "text2")

    assert neighbor_hit.metadata["retrieval_relation_weight"] == 0.11
    assert round(neighbor_hit.metadata["retrieval_inherited_score"], 3) == 0.088
