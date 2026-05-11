from __future__ import annotations

from agentic_rag.config import Settings
from agentic_rag.generation.prompt_builder import PromptBuilder
from agentic_rag.schemas import SearchHit


def test_prompt_context_and_citation_assembly() -> None:
    settings = Settings(prompt_max_context_chars=5000)
    builder = PromptBuilder(settings)

    hits = [
        SearchHit(
            point_id="1",
            text="Chunk A content",
            score=0.91,
            metadata={"source": "s1.md", "title": "Doc1", "chunk_index": 0, "tags": ["a"]},
        ),
        SearchHit(
            point_id="2",
            text="Chunk B content",
            score=0.88,
            metadata={"source": "s2.md", "title": "Doc2", "chunk_index": 1, "tags": ["b"]},
        ),
    ]

    context, citations = builder.build_context(hits)
    prompt = builder.build_prompt("问题？", context)

    assert "[1]" in context
    assert "[2]" in context
    assert len(citations) == 2
    assert citations[0].source == "s1.md"
    assert "仅依据给定上下文回答" in prompt
    assert "[1][2]" in prompt
