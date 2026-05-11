from __future__ import annotations

from agentic_rag.ingestion.chunk_strategies import TableChunker


def test_table_chunk_small_and_large() -> None:
    chunker = TableChunker(max_small_rows=3, group_rows=2)

    small = "\n".join([
        "|a|b|",
        "|---|---|",
        "|1|2|",
        "|3|4|",
    ])
    small_chunks = chunker.chunk_markdown_table(small)
    assert len(small_chunks) == 1

    large = "\n".join([
        "|a|b|",
        "|---|---|",
        "|1|2|",
        "|3|4|",
        "|5|6|",
        "|7|8|",
        "|9|10|",
    ])
    large_chunks = chunker.chunk_markdown_table(large)
    assert len(large_chunks) >= 2
    assert all("|---|---|" in x for x in large_chunks)
