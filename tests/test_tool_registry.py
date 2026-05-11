from __future__ import annotations

from agentic_rag.agent.tools import build_retrieval_tools
from agentic_rag.config import Settings


def test_tool_registry_builds_all_tools() -> None:
    settings = Settings()

    class DummyRag:
        def invoke(self, question, filters=None):
            class R:
                answer = "ok"
                citations = []
            return R()

    class DummyRetriever:
        def retrieve(self, *args, **kwargs):
            return type("X", (), {"hits": [], "route_hits": {}, "expanded_hits": []})()

    class DummyVector:
        def embed_texts(self, texts):
            return [[0.0]]

    tools = build_retrieval_tools(
        rag_graph=DummyRag(),
        bm25=DummyRetriever(),
        page=DummyRetriever(),
        table=DummyRetriever(),
        hybrid=DummyRetriever(),
        embedding_provider=DummyVector(),
    )
    assert len(tools) == 5

