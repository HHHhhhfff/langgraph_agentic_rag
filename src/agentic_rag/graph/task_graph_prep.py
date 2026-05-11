from __future__ import annotations

from agentic_rag.retrieval.evidence_pack import EvidencePack
from agentic_rag.retrieval.retrieval_plan import RetrievalPlan


class QuestionAnalysisState(dict):
    """Placeholder state for future question analyzer node."""


class TaskRouteState(dict):
    """Placeholder state for future task router node."""


class EvidenceGateState(dict):
    """Placeholder state for future evidence gate node."""


class LocalRetryState(dict):
    """Placeholder state for future local retry node."""


__all__ = [
    "EvidenceGateState",
    "EvidencePack",
    "LocalRetryState",
    "QuestionAnalysisState",
    "RetrievalPlan",
    "TaskRouteState",
]

