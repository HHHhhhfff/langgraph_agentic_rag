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
