from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from agentic_rag.config import Settings
from agentic_rag.schemas import SearchHit


_SYMBOL_REPLACEMENTS = {
    "Σ": " Sigma ",
    "σ": " sigma ",
    "ϑ": " vartheta theta ",
    "θ": " theta ",
    "τ": " tau ",
    "ζ": " zeta ",
    "Ξ": " Xi ",
    "Ω": " Omega ",
    "ω": " omega ",
    "π": " pi ",
    "Δ": " Delta ",
    "δ": " delta ",
    "μ": " mu ",
    "ν": " nu ",
    "λ": " lambda ",
    "α": " alpha ",
    "β": " beta ",
    "γ": " gamma ",
}

_FIGURE_TERMS = {
    "figure",
    "fig",
    "fig.",
    "plot",
    "graph",
    "curve",
    "axis",
    "legend",
    "panel",
    "caption",
    "snippet",
}

_FUNDING_TERMS = {
    "financially",
    "supported",
    "funding",
    "grant",
    "program",
    "acknowledge",
    "acknowledgement",
    "acknowledgment",
}


def build_query_variants(query: str, settings: Settings) -> list[str]:
    """Build deterministic lexical query variants for BM25-like retrieval."""

    raw = " ".join((query or "").split())
    if not raw:
        return []
    if not settings.query_variants_enabled:
        return [raw]

    variants: list[str] = [raw]
    normalized = normalize_query_symbols(raw)
    if normalized and normalized != raw:
        variants.append(normalized)

    keyword_variant = build_keyword_variant(normalized or raw, settings=settings)
    if keyword_variant and keyword_variant not in variants:
        variants.append(keyword_variant)

    lower = raw.lower()
    if any(term in lower for term in _FIGURE_TERMS):
        figure_variant = build_domain_variant(normalized or raw, include_terms=("figure", "fig", "caption", "plot", "curve"))
        if figure_variant and figure_variant not in variants:
            variants.append(figure_variant)
    if any(term in lower for term in _FUNDING_TERMS):
        funding_variant = build_domain_variant(
            normalized or raw,
            include_terms=("funding", "financially supported", "program", "grant", "acknowledgement"),
        )
        if funding_variant and funding_variant not in variants:
            variants.append(funding_variant)

    return _dedupe_preserve_order(variants)[: max(1, settings.query_variants_max)]


def normalize_query_symbols(text: str) -> str:
    normalized = text or ""
    for symbol, replacement in _SYMBOL_REPLACEMENTS.items():
        normalized = normalized.replace(symbol, replacement)
    normalized = unicodedata.normalize("NFKD", normalized)
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    for symbol, replacement in _SYMBOL_REPLACEMENTS.items():
        normalized = normalized.replace(symbol, replacement)
    normalized = normalized.replace("≈", " approximately ").replace("~", " approximately ")
    normalized = normalized.replace("Σ̄", " Sigma bar ")
    normalized = re.sub(r"([A-Za-z]+)_\{?([A-Za-z0-9]+)\}?", r"\1_\2 \1 \2", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def build_keyword_variant(text: str, *, settings: Settings) -> str:
    tokens = _query_tokens(text)
    min_len = max(1, settings.query_variant_min_token_len)
    kept = [
        token
        for token in tokens
        if len(token) >= min_len or any(ch.isdigit() for ch in token) or "_" in token
    ]
    return " ".join(_dedupe_preserve_order(kept))


def build_domain_variant(text: str, *, include_terms: Iterable[str]) -> str:
    tokens = _query_tokens(text)
    anchors = [token for token in tokens if _looks_like_anchor(token)]
    return " ".join(_dedupe_preserve_order([*include_terms, *anchors]))


def has_query_anchor_overlap(query: str, hit: SearchHit) -> bool:
    anchors = [token.lower() for token in _query_tokens(normalize_query_symbols(query)) if _looks_like_anchor(token)]
    if not anchors:
        return False
    haystack = " ".join(
        str(part or "")
        for part in (
            hit.text,
            hit.table_markdown,
            hit.formula_latex,
            hit.caption,
            hit.ocr_text,
            hit.object_description,
            hit.metadata.get("title"),
            hit.metadata.get("section"),
        )
    )
    normalized_haystack = normalize_query_symbols(haystack).lower()
    return any(anchor in normalized_haystack for anchor in anchors)


def _query_tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z0-9_.-]*|[0-9]+(?:\.[0-9]+)?", text or "")


def _looks_like_anchor(token: str) -> bool:
    token = token.strip().lower()
    if not token:
        return False
    if any(ch.isdigit() for ch in token):
        return True
    if "_" in token or "-" in token:
        return True
    if token in {
        "sigma",
        "theta",
        "vartheta",
        "tau",
        "figure",
        "fig",
        "caption",
        "plot",
        "curve",
        "funding",
        "grant",
        "program",
    }:
        return True
    return len(token) >= 5 and token not in {"which", "what", "where", "according", "value", "larger"}


def _dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = " ".join(str(value or "").split())
        key = item.lower()
        if not item or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
