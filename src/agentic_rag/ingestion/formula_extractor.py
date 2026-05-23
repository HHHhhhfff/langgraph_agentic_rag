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
    formula_count: int = 1

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
_REFERENCE_RE = re.compile(r"^\[\d+(?:\s*[,，\-–]\s*\d+)*\]$")
_SUPERSCRIPT_NOTE_RE = re.compile(
    r"^\^\{?[0-9,\s*\\dagger\\ddagger\\ast†‡]+}?$",
    re.IGNORECASE,
)
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


def _looks_like_reference(text: str) -> bool:
    compact = " ".join((text or "").strip().split())
    return bool(_REFERENCE_RE.match(compact))


def _looks_like_superscript_note(text: str) -> bool:
    compact = " ".join((text or "").strip().split())
    return bool(_SUPERSCRIPT_NOTE_RE.match(compact))


def _plain_text_ratio(text: str) -> float:
    compact = "".join((text or "").split())
    if not compact:
        return 0.0
    plain = sum(1 for ch in compact if ch.isalpha() or "\u4e00" <= ch <= "\u9fff")
    return plain / len(compact)


def _is_polluted_formula(text: str) -> bool:
    value = " ".join((text or "").strip().split())
    if not value:
        return True
    if any("\u4e00" <= ch <= "\u9fff" for ch in value) and not _LATEX_COMMAND_RE.search(value):
        return True
    return len(value) > 80 and _plain_text_ratio(value) > 0.65 and not _LATEX_COMMAND_RE.search(value)


def _should_keep_formula(
    formula_latex: str,
    *,
    kind: FormulaKind,
    min_chars: int,
    inline_as_text_only: bool,
    skip_inline_references: bool,
    skip_superscript_notes: bool,
) -> bool:
    compact = " ".join((formula_latex or "").strip().split())
    if not compact:
        return False
    if kind == "inline" and inline_as_text_only:
        return False
    if skip_inline_references and _looks_like_reference(compact):
        return False
    if skip_superscript_notes and _looks_like_superscript_note(compact):
        return False
    if kind == "inline" and len(compact) < min_chars:
        return False
    if _is_polluted_formula(compact):
        return False
    return _is_formula_like(compact)


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


def _extract_dollar_inline(
    text: str,
    *,
    min_chars: int,
    inline_as_text_only: bool,
    skip_inline_references: bool,
    skip_superscript_notes: bool,
) -> list[FormulaMatch]:
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
            if _should_keep_formula(
                body,
                kind="inline",
                min_chars=min_chars,
                inline_as_text_only=inline_as_text_only,
                skip_inline_references=skip_inline_references,
                skip_superscript_notes=skip_superscript_notes,
            ):
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


def extract_formulas(
    text: str,
    *,
    min_chars: int = 1,
    inline_as_text_only: bool = False,
    skip_inline_references: bool = False,
    skip_superscript_notes: bool = False,
    group_display: bool = False,
    group_max_gap_lines: int = 2,
) -> list[FormulaMatch]:
    """Extract LaTeX/MathML formulas from parsed Markdown-like text."""

    if not text:
        return []

    candidates: list[FormulaMatch] = []
    for kind, pattern, strip_delimiters in _PATTERNS:
        for match in pattern.finditer(text):
            formula_latex = _body(match, strip_delimiters)
            if not _should_keep_formula(
                formula_latex,
                kind=kind,
                min_chars=min_chars,
                inline_as_text_only=inline_as_text_only,
                skip_inline_references=skip_inline_references,
                skip_superscript_notes=skip_superscript_notes,
            ):
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
    candidates.extend(
        _extract_dollar_inline(
            text,
            min_chars=min_chars,
            inline_as_text_only=inline_as_text_only,
            skip_inline_references=skip_inline_references,
            skip_superscript_notes=skip_superscript_notes,
        )
    )

    candidates.sort(key=lambda item: (item.start, -(item.end - item.start)))
    accepted_spans: list[tuple[int, int]] = []
    formulas: list[FormulaMatch] = []
    for candidate in candidates:
        span = (candidate.start, candidate.end)
        if _overlaps(span, accepted_spans):
            continue
        accepted_spans.append(span)
        formulas.append(candidate)
    if group_display:
        return _group_adjacent_display_formulas(text, formulas, max_gap_lines=group_max_gap_lines)
    return formulas


def _group_adjacent_display_formulas(text: str, formulas: list[FormulaMatch], *, max_gap_lines: int) -> list[FormulaMatch]:
    grouped: list[FormulaMatch] = []
    current: list[FormulaMatch] = []

    def flush() -> None:
        nonlocal current
        if not current:
            return
        if len(current) == 1:
            grouped.append(current[0])
        else:
            raw = text[current[0].start : current[-1].end].strip()
            latex = "\n\n".join(item.formula_latex for item in current)
            grouped.append(
                FormulaMatch(
                    raw=raw,
                    formula_latex=latex,
                    kind="display",
                    start=current[0].start,
                    end=current[-1].end,
                    formula_count=len(current),
                )
            )
        current = []

    for formula in formulas:
        if formula.kind != "display":
            flush()
            grouped.append(formula)
            continue
        if not current:
            current = [formula]
            continue
        gap = text[current[-1].end : formula.start]
        non_blank_lines = [line for line in gap.splitlines() if line.strip()]
        if len(non_blank_lines) == 0 and gap.count("\n") <= max_gap_lines + 1:
            current.append(formula)
            continue
        flush()
        current = [formula]
    flush()
    return grouped
