from __future__ import annotations

import hashlib
from pathlib import Path


def build_doc_id(source: str) -> str:
    """Build stable document id from source path."""

    return hashlib.md5(source.encode("utf-8")).hexdigest()


def guess_title(source: str) -> str:
    """Infer title from file path when unavailable."""

    return Path(source).stem
