from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class TokenSplitConfig:
    max_input_tokens: int = 8192
    safety_margin_tokens: int = 256

    @property
    def hard_limit(self) -> int:
        return max(128, self.max_input_tokens - self.safety_margin_tokens)


class TokenHardSplitter:
    """Token-aware hard splitter with table/text specific fallbacks."""

    def __init__(self, config: TokenSplitConfig):
        self.config = config
        self._encoder = self._build_encoder()

    @staticmethod
    def _build_encoder():
        try:
            import tiktoken  # type: ignore

            return tiktoken.get_encoding("cl100k_base")
        except Exception:
            return None

    def count_tokens(self, text: str) -> int:
        if not text:
            return 0
        if self._encoder is not None:
            return len(self._encoder.encode(text, disallowed_special=()))
        # Conservative heuristic fallback when tokenizer package is unavailable.
        return max(1, len(text) // 4)

    def is_within_limit(self, text: str) -> bool:
        return self.count_tokens(text) <= self.config.hard_limit

    def split_hard(self, text: str, *, prefer_table: bool = False) -> list[str]:
        cleaned = (text or "").strip()
        if not cleaned:
            return []
        if self.is_within_limit(cleaned):
            return [cleaned]

        if prefer_table:
            parts = self._split_table_like(cleaned)
        else:
            parts = self._split_text_like(cleaned)

        normalized = [p.strip() for p in parts if p and p.strip()]
        if not normalized:
            return self._split_by_window(cleaned)

        safe: list[str] = []
        for part in normalized:
            if self.is_within_limit(part):
                safe.append(part)
            else:
                # Recursive tightening with character/token windows as final guardrail.
                safe.extend(self._split_by_window(part))
        return [x for x in safe if x.strip()]

    def _split_table_like(self, text: str) -> list[str]:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) < 3:
            return self._split_text_like(text)

        has_sep = len(lines) >= 2 and "---" in lines[1]
        if not has_sep:
            return self._split_text_like(text)

        header = lines[:2]
        rows = lines[2:]
        chunks: list[str] = []
        current_rows: list[str] = []
        for row in rows:
            candidate = "\n".join(header + current_rows + [row]).strip()
            if self.count_tokens(candidate) <= self.config.hard_limit:
                current_rows.append(row)
                continue
            if current_rows:
                chunks.append("\n".join(header + current_rows).strip())
                current_rows = [row]
            else:
                # One row itself is too large, split row as plain text.
                row_chunks = self._split_text_like(row)
                for idx, row_piece in enumerate(row_chunks):
                    if idx == 0:
                        chunks.append("\n".join(header + [row_piece]).strip())
                    else:
                        chunks.append("\n".join(header + [row_piece]).strip())
                current_rows = []
        if current_rows:
            chunks.append("\n".join(header + current_rows).strip())
        return chunks

    def _split_text_like(self, text: str) -> list[str]:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
        if len(paragraphs) <= 1:
            return self._split_by_sentence(text)

        chunks: list[str] = []
        current: list[str] = []
        for para in paragraphs:
            candidate = "\n\n".join(current + [para]).strip()
            if self.count_tokens(candidate) <= self.config.hard_limit:
                current.append(para)
                continue
            if current:
                chunks.append("\n\n".join(current).strip())
                current = [para]
            else:
                chunks.extend(self._split_by_sentence(para))
        if current:
            chunks.append("\n\n".join(current).strip())
        return chunks

    def _split_by_sentence(self, text: str) -> list[str]:
        sentences = [s.strip() for s in re.split(r"(?<=[。！？!?\.])\s+|\n", text) if s.strip()]
        if len(sentences) <= 1:
            return self._split_by_window(text)

        chunks: list[str] = []
        current: list[str] = []
        for sentence in sentences:
            candidate = " ".join(current + [sentence]).strip()
            if self.count_tokens(candidate) <= self.config.hard_limit:
                current.append(sentence)
                continue
            if current:
                chunks.append(" ".join(current).strip())
                current = [sentence]
            else:
                chunks.extend(self._split_by_window(sentence))
        if current:
            chunks.append(" ".join(current).strip())
        return chunks

    def _split_by_window(self, text: str) -> list[str]:
        stripped = text.strip()
        if not stripped:
            return []

        limit = self.config.hard_limit
        if self._encoder is not None:
            token_ids = self._encoder.encode(stripped, disallowed_special=())
            chunks: list[str] = []
            for i in range(0, len(token_ids), limit):
                piece = self._encoder.decode(token_ids[i : i + limit]).strip()
                if piece:
                    chunks.append(piece)
            return chunks

        # Heuristic fallback when tokenizer unavailable.
        approx_chars = max(256, limit * 4)
        chunks = [stripped[i : i + approx_chars].strip() for i in range(0, len(stripped), approx_chars)]
        return [c for c in chunks if c]


def looks_like_markdown_table(text: str) -> bool:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return len(lines) >= 2 and "|" in lines[0] and "---" in lines[1]

