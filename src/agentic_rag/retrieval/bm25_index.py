from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from agentic_rag.schemas import SearchHit


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in re.findall(r"[\u4e00-\u9fff]|[a-zA-Z0-9_]+", text or "")]


def _matches(hit: SearchHit, filters: dict[str, Any] | None) -> bool:
    if not filters:
        return True
    md = hit.metadata or {}
    if "source" in filters and str(filters["source"]) != str(hit.metadata.get("source") or md.get("source")):
        return False
    if "doc_id" in filters and str(filters["doc_id"]) != str(hit.doc_id or md.get("doc_id")):
        return False
    if "page" in filters and filters["page"] != hit.page and str(filters["page"]) != str(md.get("page")):
        return False
    if "modality" in filters and str(filters["modality"]) != str(hit.modality):
        return False
    tags = filters.get("tags")
    if tags:
        hit_tags = md.get("tags") or []
        if isinstance(hit_tags, str):
            hit_tags = [hit_tags]
        if not set(map(str, tags)).intersection({str(x) for x in hit_tags}):
            return False
    return True


@dataclass(slots=True)
class BM25Index:
    """Simple BM25 index built from SearchHit items."""

    docs: list[SearchHit] = field(default_factory=list)
    tokens: list[list[str]] = field(default_factory=list)
    df: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    avgdl: float = 0.0
    k1: float = 1.5
    b: float = 0.75

    @classmethod
    def build(cls, docs: list[SearchHit]) -> "BM25Index":
        index = cls()
        index.docs = list(docs)
        lengths = []
        for hit in docs:
            text = hit.table_markdown if hit.modality == "table" and hit.table_markdown else hit.text or ""
            toks = _tokenize(text)
            index.tokens.append(toks)
            lengths.append(len(toks))
            for tok in set(toks):
                index.df[tok] += 1
        index.avgdl = sum(lengths) / len(lengths) if lengths else 0.0
        return index

    def search(self, query: str, top_k: int = 12, filters: dict[str, Any] | None = None) -> list[SearchHit]:
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []
        scores: list[tuple[int, float]] = []
        n = len(self.docs)
        for i, toks in enumerate(self.tokens):
            hit = self.docs[i]
            if not _matches(hit, filters):
                continue
            if not toks:
                continue
            tf = Counter(toks)
            dl = len(toks) or 1
            score = 0.0
            for tok in q_tokens:
                df = self.df.get(tok, 0)
                if not df:
                    continue
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                freq = tf.get(tok, 0)
                denom = freq + self.k1 * (1 - self.b + self.b * dl / max(self.avgdl, 1e-6))
                score += idf * (freq * (self.k1 + 1)) / denom if denom else 0.0
            if score > 0:
                scores.append((i, score))
        scores.sort(key=lambda x: x[1], reverse=True)
        results: list[SearchHit] = []
        for idx, score in scores[:top_k]:
            hit = self.docs[idx].model_copy(deep=True)
            hit.channel = "bm25"
            hit.score_bm25 = score
            hit.score = score
            results.append(hit)
        return results
