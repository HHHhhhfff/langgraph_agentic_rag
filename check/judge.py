from __future__ import annotations

import json
import re
from typing import Any

from check.metrics import estimate_tokens

JUDGE_PROMPT = """You are an independent AI evaluator for question answering.
Your job is to score the actual answer against the standard answer for the query.

Scoring rules:
- correctness: factual consistency with the standard answer.
- completeness: whether the actual answer covers all necessary key points.
- relevance: whether the actual answer directly answers the query.
- overall: holistic answer quality, considering correctness first.

Return only a compact JSON object with these keys:
correctness, completeness, relevance, overall, reason.
All scores must be numbers from 0 to 1.
Ignore citation markers such as [1] unless they change the answer meaning.

Question:
{question}

Standard answer:
{reference_answer}

Actual answer:
{prediction}
"""


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("judge response does not contain a JSON object")
    return json.loads(cleaned[start : end + 1])


def _score(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def judge_answer(
    *,
    llm_client: Any,
    question: str,
    prediction: str,
    reference_answer: str,
) -> dict[str, Any]:
    prompt = JUDGE_PROMPT.format(
        question=question,
        reference_answer=reference_answer,
        prediction=prediction,
    )
    raw = llm_client.generate(prompt)
    parsed = _extract_json_object(raw)
    prompt_tokens = estimate_tokens(prompt)
    response_tokens = estimate_tokens(raw)
    correctness = _score(parsed.get("correctness"))
    completeness = _score(parsed.get("completeness"))
    relevance = _score(parsed.get("relevance"))
    overall_raw = parsed.get("overall")
    overall = (
        _score(overall_raw)
        if overall_raw is not None
        else round(0.5 * correctness + 0.3 * completeness + 0.2 * relevance, 6)
    )
    return {
        "ai_correctness": correctness,
        "ai_completeness": completeness,
        "ai_relevance": relevance,
        "ai_overall": overall,
        "ai_score_100": round(overall * 100, 2),
        "ai_reason": str(parsed.get("reason", "")).strip()[:800],
        "ai_judge_prompt_tokens_est": prompt_tokens,
        "ai_judge_answer_tokens_est": response_tokens,
        "ai_judge_total_tokens_est": prompt_tokens + response_tokens,
    }
