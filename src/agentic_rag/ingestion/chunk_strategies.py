from __future__ import annotations

from typing import Protocol

from agentic_rag.config import Settings


class TextChunkStrategy(Protocol):
    """Protocol for text chunk strategy."""

    def chunk_text(self, text: str) -> list[str]:
        ...


class SentenceSplitterStrategy:
    """LlamaIndex SentenceSplitter-backed strategy."""

    def __init__(self, settings: Settings):
        from llama_index.core.node_parser import SentenceSplitter

        self.splitter = SentenceSplitter(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        )

    def chunk_text(self, text: str) -> list[str]:
        docs = self.splitter.split_text(text)
        return [x.strip() for x in docs if x.strip()]


class MarkdownNodeParserStrategy:
    """LlamaIndex MarkdownNodeParser strategy."""

    def __init__(self):
        from llama_index.core.node_parser import MarkdownNodeParser
        from llama_index.core.schema import Document

        self.Document = Document
        self.parser = MarkdownNodeParser()

    def chunk_text(self, text: str) -> list[str]:
        nodes = self.parser.get_nodes_from_documents([self.Document(text=text)])
        return [n.get_content().strip() for n in nodes if n.get_content().strip()]


class HierarchicalNodeParserStrategy:
    """LlamaIndex HierarchicalNodeParser strategy."""

    def __init__(self, settings: Settings):
        from llama_index.core.node_parser import HierarchicalNodeParser
        from llama_index.core.schema import Document

        self.Document = Document
        self.parser = HierarchicalNodeParser.from_defaults(
            chunk_sizes=[settings.chunk_size * 2, settings.chunk_size]
        )

    def chunk_text(self, text: str) -> list[str]:
        nodes = self.parser.get_nodes_from_documents([self.Document(text=text)])
        leaves = [n for n in nodes if not getattr(n, "child_nodes", None)]
        if not leaves:
            leaves = nodes
        return [n.get_content().strip() for n in leaves if n.get_content().strip()]


class TableChunker:
    """Table chunking rules for small/large tables."""

    def __init__(self, max_small_rows: int = 12, group_rows: int = 8):
        self.max_small_rows = max_small_rows
        self.group_rows = group_rows

    def chunk_markdown_table(self, markdown: str) -> list[str]:
        lines = [ln for ln in markdown.splitlines() if ln.strip()]
        if len(lines) <= 2:
            return [markdown.strip()] if markdown.strip() else []

        header = lines[:2]
        rows = lines[2:]

        if len(rows) <= self.max_small_rows:
            return [markdown.strip()]

        chunks: list[str] = []
        for i in range(0, len(rows), self.group_rows):
            grp = rows[i : i + self.group_rows]
            content = "\n".join(header + grp).strip()
            if content:
                chunks.append(content)
        return chunks


def build_text_chunk_strategy(settings: Settings) -> TextChunkStrategy:
    """Factory for switchable text chunk strategies."""

    parser = settings.text_chunk_parser
    if parser == "markdown":
        return MarkdownNodeParserStrategy()
    if parser == "hierarchical":
        return HierarchicalNodeParserStrategy(settings)
    return SentenceSplitterStrategy(settings)
