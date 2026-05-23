from __future__ import annotations

from agentic_rag.ingestion.formula_extractor import extract_formulas


def test_extract_formulas_detects_common_math_forms() -> None:
    text = (
        "Price is $5, not a formula. Inline math is $E=mc^2$.\n"
        "$$\\frac{a}{b}=c$$\n"
        "\\begin{equation}x^2+y^2=z^2\\end{equation}"
    )

    formulas = extract_formulas(text)

    assert len(formulas) == 3
    assert formulas[0].formula_latex == "E=mc^2"
    assert "\\frac{a}{b}=c" in formulas[1].formula_latex
    assert formulas[2].kind == "environment"
    assert all("$5" not in item.formula_latex for item in formulas)


def test_extract_formulas_skips_overlapping_inline_matches() -> None:
    formulas = extract_formulas("Use $$a_i=b_i+c_i$$ in the proof.")

    assert len(formulas) == 1
    assert formulas[0].kind == "display"
    assert formulas[0].formula_latex == "a_i=b_i+c_i"


def test_extract_formulas_filters_references_superscripts_and_short_inline() -> None:
    text = r"$[12]$ $^{1,2,*}$ $Q$ $E=mc^2$ $$x+y=z$$"

    formulas = extract_formulas(
        text,
        min_chars=8,
        inline_as_text_only=True,
        skip_inline_references=True,
        skip_superscript_notes=True,
    )

    assert len(formulas) == 1
    assert formulas[0].formula_latex == "x+y=z"


def test_extract_formulas_groups_adjacent_display_formulas() -> None:
    text = "$$a=b$$\n\n$$b=c$$\n\nparagraph\n\n$$c=d$$"

    formulas = extract_formulas(text, group_display=True, group_max_gap_lines=2)

    assert len(formulas) == 2
    assert formulas[0].formula_count == 2
    assert formulas[0].formula_latex == "a=b\n\nb=c"
    assert formulas[1].formula_count == 1
