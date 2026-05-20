from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser


@dataclass(frozen=True, slots=True)
class TableBlock:
    raw: str
    markdown: str
    start: int
    end: int
    kind: str


class _HTMLTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._current_row = []
        if tag in {"td", "th"} and self._current_row is not None:
            self._current_cell = []

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._current_cell is not None and self._current_row is not None:
            cell = " ".join("".join(self._current_cell).split())
            self._current_row.append(cell)
            self._current_cell = None
        if tag == "tr" and self._current_row is not None:
            if any(cell.strip() for cell in self._current_row):
                self.rows.append(self._current_row)
            self._current_row = None


def html_table_to_markdown(html: str) -> str:
    parser = _HTMLTableParser()
    parser.feed(html or "")
    rows = [row for row in parser.rows if row]
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    header = padded[0]
    body = padded[1:]
    return "\n".join(
        [
            "| " + " | ".join(header) + " |",
            "| " + " | ".join(["---"] * width) + " |",
            *("| " + " | ".join(row) + " |" for row in body),
        ]
    ).strip()


def is_markdown_table_block(lines: list[str]) -> bool:
    if len(lines) < 2:
        return False
    if not all("|" in line for line in lines):
        return False
    return any("---" in line for line in lines[:3])


def is_table_like_text(text: str) -> bool:
    value = text or ""
    return bool(re.search(r"<table\b", value, flags=re.IGNORECASE)) or is_markdown_table_block(
        [line for line in value.splitlines() if line.strip()]
    )


def extract_table_blocks(text: str) -> list[TableBlock]:
    source = text or ""
    blocks: list[TableBlock] = []
    occupied: list[tuple[int, int]] = []

    for match in re.finditer(r"<table\b.*?</table>", source, flags=re.IGNORECASE | re.DOTALL):
        markdown = html_table_to_markdown(match.group(0))
        if markdown:
            blocks.append(
                TableBlock(
                    raw=match.group(0),
                    markdown=markdown,
                    start=match.start(),
                    end=match.end(),
                    kind="html",
                )
            )
            occupied.append((match.start(), match.end()))

    lines = source.splitlines(keepends=True)
    offset = 0
    i = 0
    while i < len(lines):
        line_start = offset
        if "|" not in lines[i]:
            offset += len(lines[i])
            i += 1
            continue
        table_lines = [lines[i]]
        j = i + 1
        end = offset + len(lines[i])
        while j < len(lines) and "|" in lines[j]:
            table_lines.append(lines[j])
            end += len(lines[j])
            j += 1
        stripped = [line.strip() for line in table_lines if line.strip()]
        if is_markdown_table_block(stripped) and not _overlaps((line_start, end), occupied):
            raw = "".join(table_lines)
            blocks.append(
                TableBlock(raw=raw, markdown="\n".join(stripped), start=line_start, end=end, kind="markdown")
            )
            occupied.append((line_start, end))
        for k in range(i, j):
            offset += len(lines[k])
        i = j

    return sorted(blocks, key=lambda block: block.start)


def strip_table_blocks(text: str, blocks: list[TableBlock] | None = None) -> str:
    source = text or ""
    table_blocks = blocks if blocks is not None else extract_table_blocks(source)
    if not table_blocks:
        return source
    parts: list[str] = []
    cursor = 0
    for block in table_blocks:
        parts.append(source[cursor:block.start])
        parts.append("\n")
        cursor = block.end
    parts.append(source[cursor:])
    return "".join(parts)


def _overlaps(span: tuple[int, int], accepted: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < prev_end and end > prev_start for prev_start, prev_end in accepted)
