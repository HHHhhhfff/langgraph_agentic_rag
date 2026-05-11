from __future__ import annotations

from agentic_rag.config import Settings
from agentic_rag.ingestion.adapters.mineru_client import MinerUParseResult
from agentic_rag.ingestion.token_splitter import TokenHardSplitter, TokenSplitConfig


def parse_mineru_markdown(result: MinerUParseResult, settings: Settings | None = None) -> str:
    """Normalize MinerU markdown output for downstream chunking."""

    text = (result.markdown_content or "").strip()
    if not text:
        raise ValueError("MinerU markdown content is empty")
    cfg = settings or Settings()
    splitter = TokenHardSplitter(
        TokenSplitConfig(
            max_input_tokens=cfg.embedding_input_max_tokens,
            safety_margin_tokens=cfg.embedding_input_safety_margin_tokens,
        )
    )
    return _preprocess_mineru_markdown(text, splitter)


def _preprocess_mineru_markdown(text: str, splitter: TokenHardSplitter) -> str:
    lines = text.splitlines()
    output: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if "|" in line:
            table_block = [line]
            j = i + 1
            while j < len(lines) and "|" in lines[j]:
                table_block.append(lines[j])
                j += 1
            block = "\n".join(table_block).strip()
            if len(table_block) >= 2 and "---" in table_block[1]:
                parts = splitter.split_hard(block, prefer_table=True)
                output.extend(parts)
                i = j
                continue

        if line.strip() and splitter.count_tokens(line) > splitter.config.hard_limit:
            parts = splitter.split_hard(line, prefer_table=False)
            output.extend(parts)
            i += 1
            continue

        output.append(line)
        i += 1
    return "\n".join(output).strip()
