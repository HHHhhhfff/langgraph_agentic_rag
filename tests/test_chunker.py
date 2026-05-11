from __future__ import annotations

from agentic_rag.ingestion.chunker import ChunkConfig, TextChunker


def test_chunker_basic_behavior() -> None:
    chunker = TextChunker(ChunkConfig(chunk_size=50, chunk_overlap=10, chunk_min_length=5))
    doc = {
        "source": "demo.md",
        "title": "Demo",
        "tags": ["t1"],
        "metadata": {"dataset": "kb1"},
        "content": "A" * 120,
    }

    chunks = chunker.chunk_document(doc)

    assert len(chunks) >= 2
    assert chunks[0].metadata["source"] == "demo.md"
    assert chunks[0].metadata["title"] == "Demo"
    assert chunks[0].metadata["chunk_index"] == 0
    assert chunks[1].metadata["chunk_index"] == 1
    assert chunks[0].text
