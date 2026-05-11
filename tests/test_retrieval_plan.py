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

