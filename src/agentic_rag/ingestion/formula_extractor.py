from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


FormulaKind = Literal["display", "inline", "environment", "mathml"]


@dataclass(frozen=True, slots=True)
class FormulaMatch:
    """A formula span detected in parser output."""

    raw: str
    formula_latex: str
    kind: FormulaKind
    start: int
    end: int

    @property
    def text(self) -> str:
        return f"Formula ({self.kind}): {self.formula_latex}"


_ENV_PATTERN = (
    r"equation\*?|align\*?|aligned|gather\*?|multline\*?|split|cases|matrix|pmatrix|bmatrix|vmatrix"
)

_PATTERNS: list[tuple[FormulaKind, re.Pattern[str], bool]] = [
    ("mathml", re.compile(r"<math\b.*?</math>", re.IGNORECASE | re.DOTALL), False),
    (
        "environment",
        re.compile(rf"\\begin\{{(?P<env>{_ENV_PATTERN})\}}.*?\\end\{{(?P=env)\}}", re.DOTALL),
        False,
    ),
    ("display", re.compile(r"\$\$(?P<body>.+?)\$\$", re.DOTALL), True),
    ("display", re.compile(r"\\\[(?P<body>.+?)\\\]", re.DOTALL), True),
    ("inline", re.compile(r"\\\((?P<body>.+?)\\\)", re.DOTALL), True),
]

_LATEX_COMMAND_RE = re.compile(r"\\[a-zA-Z]+")
_MATH_OPERATOR_RE = re.compile(r"[A-Za-z0-9)\]}]\s*(=|\\leq|\\geq|\\approx|[+\-*/^_<>])\s*[A-Za-z0-9({\\]")
_MATH_SYMBOLS = {
    "=",
    "^",
    "_",
    "+",
    "-",
    "*",
    "/",
    "<",
    ">",
    "≤",
    "≥",
    "≈",
    "≠",
    "∑",
    "∫",
    "√",
    "∞",
    "π",
}


def _is_formula_like(text: str) -> bool:
    compact = " ".join((text or "").strip().split())
    if not compact:
        return False
    if _LATEX_COMMAND_RE.search(compact):
        return True
    if any(symbol in compact for symbol in _MATH_SYMBOLS):
        return True
    return bool(_MATH_OPERATOR_RE.search(compact))


def _body(match: re.Match[str], keep_delimiters: bool) -> str:
    if keep_delimiters and "body" in match.groupdict():
        return str(match.group("body") or "").strip()
    return str(match.group(0) or "").strip()


def _overlaps(span: tuple[int, int], accepted: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < prev_end and end > prev_start for prev_start, prev_end in accepted)


def _is_single_dollar(text: str, index: int) -> bool:
    if text[index] != "$":
        return False
    if index > 0 and text[index - 1] in {"\\", "$"}:
        return False
    if index + 1 < len(text) and text[index + 1] == "$":
        return False
    return True


def _extract_dollar_inline(text: str) -> list[FormulaMatch]:
    formulas: list[FormulaMatch] = []
    start = 0
    while start < len(text):
        start = text.find("$", start)
        if start < 0:
            break
        if not _is_single_dollar(text, start):
            start += 1
            continue

        end = start + 1
        matched = False
        while end < len(text):
            end = text.find("$", end)
            if end < 0:
                break
            if not _is_single_dollar(text, end):
                end += 1
                continue
            body = text[start + 1 : end]
            if "\n" in body:
                break
            if _is_formula_like(body):
                raw = text[start : end + 1].strip()
                formulas.append(
                    FormulaMatch(
                        raw=raw,
                        formula_latex=body.strip(),
                        kind="inline",
                        start=start,
                        end=end + 1,
                    )
                )
                start = end + 1
                matched = True
                break
            break
        if not matched:
            start += 1
    return formulas


def extract_formulas(text: str) -> list[FormulaMatch]:
    """Extract LaTeX/MathML formulas from parsed Markdown-like text."""

    if not text:
        return []

    candidates: list[FormulaMatch] = []
    for kind, pattern, strip_delimiters in _PATTERNS:
        for match in pattern.finditer(text):
            formula_latex = _body(match, strip_delimiters)
            if kind == "inline" and not _is_formula_like(formula_latex):
                continue
            if kind in {"display", "environment"} and not _is_formula_like(formula_latex):
                continue
            raw = str(match.group(0) or "").strip()
            candidates.append(
                FormulaMatch(
                    raw=raw,
                    formula_latex=formula_latex,
                    kind=kind,
                    start=match.start(),
                    end=match.end(),
                )
            )
    candidates.extend(_extract_dollar_inline(text))

    candidates.sort(key=lambda item: (item.start, -(item.end - item.start)))
    accepted_spans: list[tuple[int, int]] = []
    formulas: list[FormulaMatch] = []
    for candidate in candidates:
        span = (candidate.start, candidate.end)
        if _overlaps(span, accepted_spans):
            continue
        accepted_spans.append(span)
        formulas.append(candidate)
    return formulas
