from __future__ import annotations

from dataclasses import dataclass

from agentic_rag.schemas import DocumentChunk


@dataclass(slots=True)
class ChunkConfig:
    """Chunking parameters."""

    chunk_size: int
    chunk_overlap: int
    chunk_min_length: int


class TextChunker:
    """Simple char-based chunker with overlap."""

    def __init__(self, config: ChunkConfig):
        if config.chunk_overlap >= config.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        self.config = config

    def chunk_document(self, doc: dict) -> list[DocumentChunk]:
        text = (doc.get("content") or "").strip()
        if not text:
            return []

        chunks: list[DocumentChunk] = []
        start = 0
        index = 0
        length = len(text)

        while start < length:
            end = min(start + self.config.chunk_size, length)
            snippet = text[start:end].strip()
            if len(snippet) >= self.config.chunk_min_length or (end == length and snippet):
                metadata = {
                    "source": doc.get("source", ""),
                    "title": doc.get("title", ""),
                    "tags": doc.get("tags", []),
                    "chunk_index": index,
                    "char_start": start,
                    "char_end": end,
                }
                metadata.update(doc.get("metadata", {}))
                chunk_id = f"{doc.get('source', 'doc')}::{index}"
                chunks.append(DocumentChunk(chunk_id=chunk_id, text=snippet, metadata=metadata))
                index += 1

            if end >= length:
                break
            start = end - self.config.chunk_overlap

        return chunks

    def chunk_documents(self, docs: list[dict]) -> list[DocumentChunk]:
        result: list[DocumentChunk] = []
        for doc in docs:
            result.extend(self.chunk_document(doc))
        return result
