from __future__ import annotations

from agentic_rag.config import Settings
from agentic_rag.retrieval.query_variants import build_query_variants, has_query_anchor_overlap
from agentic_rag.schemas import SearchHit


def test_query_variants_include_symbol_normalized_and_keyword_forms() -> None:
    settings = Settings(_env_file=None, query_variants_max=4)

    variants = build_query_variants("At ϑ ≈ 1.5, compare Σ_s and Σ_a for f=3 or f=4.", settings)

    joined = " | ".join(variants).lower()
    assert "vartheta" in joined
    assert "sigma" in joined
    assert "1.5" in joined


def test_query_anchor_overlap_matches_normalized_symbols() -> None:
    hit = SearchHit(
        point_id="1",
        node_id="n1",
        text="The plot reports Sigma_s and Sigma_a versus vartheta for f=3 and f=4.",
        score=0.5,
        metadata={},
    )

    assert has_query_anchor_overlap("At ϑ≈1.5, compare Σ_s and Σ_a.", hit)
