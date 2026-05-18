from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from agentic_rag.models.providers import LLMClient


class JsonLLMError(RuntimeError):
    """Raised when an LLM response cannot be parsed into the expected JSON schema."""


T = TypeVar("T", bound=BaseModel)


def extract_json_object(text: str) -> dict:
    """Extract the first JSON object from plain text or a markdown fenced block."""

    text = (text or "").strip()
    if not text:
        raise JsonLLMError("LLM returned empty output")

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()

    decoder = json.JSONDecoder()
    for idx, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise JsonLLMError("LLM output did not contain a valid JSON object")


def generate_json(llm_client: LLMClient, prompt: str, model_cls: type[T]) -> T:
    """Generate JSON with the existing LLM client and validate it with Pydantic."""

    raw = llm_client.generate(prompt)
    payload = extract_json_object(raw)
    try:
        return model_cls.model_validate(payload)
    except ValidationError as exc:
        raise JsonLLMError(f"LLM JSON schema validation failed: {exc}") from exc
