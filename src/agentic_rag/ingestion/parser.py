from __future__ import annotations

from pathlib import Path
from typing import Any

import frontmatter


class MarkdownParserError(RuntimeError):
    """Raised when markdown parsing fails."""


class MarkdownParser:
    """Parse markdown file and front-matter metadata."""

    def parse_file(self, path: str | Path) -> dict[str, Any]:
        file_path = Path(path)
        if not file_path.exists():
            raise MarkdownParserError(f"Markdown file does not exist: {file_path}")
        try:
            post = frontmatter.load(file_path)
        except Exception as exc:
            raise MarkdownParserError(f"Failed to parse markdown file {file_path}: {exc}") from exc

        metadata = dict(post.metadata or {})
        content = str(post.content or "").strip()
        title = metadata.get("title") or file_path.stem
        tags = metadata.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        if not isinstance(tags, list):
            tags = []

        return {
            "source": str(file_path),
            "title": str(title),
            "tags": [str(tag) for tag in tags],
            "metadata": metadata,
            "content": content,
        }

    def parse_directory(self, directory: str | Path) -> list[dict[str, Any]]:
        dir_path = Path(directory)
        if not dir_path.exists() or not dir_path.is_dir():
            raise MarkdownParserError(f"Markdown directory is invalid: {dir_path}")

        docs: list[dict[str, Any]] = []
        for path in sorted(dir_path.rglob("*.md")):
            docs.append(self.parse_file(path))
        return docs
