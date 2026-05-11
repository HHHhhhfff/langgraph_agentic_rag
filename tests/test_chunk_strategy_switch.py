from __future__ import annotations

from agentic_rag.config import Settings
from agentic_rag.ingestion.chunk_strategies import build_text_chunk_strategy


class DummyStrategy:
    def __init__(self, name: str):
        self.name = name


def test_chunk_strategy_switch_sentence(monkeypatch) -> None:
    import agentic_rag.ingestion.chunk_strategies as cs

    monkeypatch.setattr(cs, "SentenceSplitterStrategy", lambda settings: DummyStrategy("sentence"))
    settings = Settings(text_chunk_parser="sentence")
    strategy = build_text_chunk_strategy(settings)
    assert strategy.name == "sentence"


def test_chunk_strategy_switch_markdown(monkeypatch) -> None:
    import agentic_rag.ingestion.chunk_strategies as cs

    monkeypatch.setattr(cs, "MarkdownNodeParserStrategy", lambda: DummyStrategy("markdown"))
    settings = Settings(text_chunk_parser="markdown")
    strategy = build_text_chunk_strategy(settings)
    assert strategy.name == "markdown"


def test_chunk_strategy_switch_hierarchical(monkeypatch) -> None:
    import agentic_rag.ingestion.chunk_strategies as cs

    monkeypatch.setattr(cs, "HierarchicalNodeParserStrategy", lambda settings: DummyStrategy("hierarchical"))
    settings = Settings(text_chunk_parser="hierarchical")
    strategy = build_text_chunk_strategy(settings)
    assert strategy.name == "hierarchical"
