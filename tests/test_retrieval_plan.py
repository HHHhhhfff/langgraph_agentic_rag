from __future__ import annotations

from agentic_rag.retrieval.evidence_pack import EvidencePack
from agentic_rag.retrieval.retrieval_plan import RetrievalPlan, RetrievalTask


def test_retrieval_plan_serialization() -> None:
    plan = RetrievalPlan(
        question="what",
        intent="table",
        tasks=[RetrievalTask(channel="bm25", query_text="what", top_k=3)],
    )
    packed = EvidencePack(plan=plan)
    assert packed.model_dump()["plan"]["question"] == "what"
    assert packed.model_dump()["plan"]["tasks"][0]["channel"] == "bm25"


def test_retrieval_plan_supports_multimodal_channels() -> None:
    plan = RetrievalPlan(
        question="what formula",
        original_query="what formula",
        query_text="what formula",
        rewritten_query_text="formula latex",
        page_window=2,
        retry_actions=["rewrite_query"],
        retry_history=[{"retry_count": 1, "actions": ["rewrite_query"]}],
        tasks=[
            RetrievalTask(channel="vector", query_text="what", top_k=3, metadata={"source": "test"}),
            RetrievalTask(channel="formula", query_text="what formula", top_k=5),
            RetrievalTask(channel="image", query_text="what image", top_k=2),
        ],
    )
    dumped = plan.model_dump()
    loaded = RetrievalPlan.model_validate(dumped)
    assert loaded.channels() == ["vector", "formula", "image"]
    assert loaded.page_window == 2
    assert loaded.retry_actions == ["rewrite_query"]
    assert loaded.tasks[0].metadata["source"] == "test"


def test_evidence_pack_extended_fields_serialization() -> None:
    plan = RetrievalPlan(question="what")
    pack = EvidencePack(
        plan=plan,
        claim_supported=True,
        source_coverage={"doc.md": 2},
        page_coverage={"doc.md:1": 1},
        modality_coverage={"text": 2},
        conflict_level="none",
        missing_slots=[],
        supporting_hit_ids=["n1"],
        support_level="strong",
        support_score=0.8,
        slot_coverage={"keyword": True},
        gate_decision="pass",
    )
    dumped = pack.model_dump()
    assert dumped["claim_supported"] is True
    assert dumped["source_coverage"]["doc.md"] == 2
    assert dumped["supporting_hit_ids"] == ["n1"]
    assert dumped["support_level"] == "strong"
    assert dumped["gate_decision"] == "pass"

