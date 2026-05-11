from __future__ import annotations

from agentic_rag.config import Settings
from agentic_rag.ingestion.adapters.mineru_client import MinerUParseResult
from agentic_rag.ingestion.adapters.mineru_result_parser import parse_mineru_markdown


def test_mineru_parser_splits_long_table() -> None:
    settings = Settings(
        embedding_input_max_tokens=140,
        embedding_input_safety_margin_tokens=10,
    )
    table = ["| h1 | h2 |", "| --- | --- |"]
    for i in range(80):
        table.append(f"| row{i} | {'x ' * 8} |")
    md = "\n".join(table)

    out = parse_mineru_markdown(
        MinerUParseResult(task_id="t", state="done", markdown_content=md),
        settings=settings,
    )
    assert out.count("| h1 | h2 |") > 1
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) > len(table)


def test_mineru_parser_splits_long_single_line() -> None:
    settings = Settings(
        embedding_input_max_tokens=120,
        embedding_input_safety_margin_tokens=10,
    )
    long_line = "https://example.com/" + ("very-long-segment-" * 1000)
    md = f"# title\n\n{long_line}\n"
    out = parse_mineru_markdown(
        MinerUParseResult(task_id="t", state="done", markdown_content=md),
        settings=settings,
    )
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) > 2
