from __future__ import annotations

import pytest
from pydantic import BaseModel

from agentic_rag.models.json_llm import JsonLLMError, extract_json_object, generate_json


class DummyModel(BaseModel):
    name: str
    count: int


class DummyLLM:
    def __init__(self, output: str):
        self.output = output

    def generate(self, prompt: str) -> str:
        return self.output


def test_extract_json_object_from_plain_json() -> None:
    assert extract_json_object('{"name":"a","count":1}') == {"name": "a", "count": 1}


def test_extract_json_object_from_fenced_block() -> None:
    text = '```json\n{"name":"a","count":1}\n```'
    assert extract_json_object(text) == {"name": "a", "count": 1}


def test_generate_json_validates_schema() -> None:
    result = generate_json(DummyLLM('prefix {"name":"a","count":1} suffix'), "prompt", DummyModel)
    assert result.name == "a"
    assert result.count == 1


def test_generate_json_rejects_non_json() -> None:
    with pytest.raises(JsonLLMError):
        generate_json(DummyLLM("not json"), "prompt", DummyModel)


def test_generate_json_rejects_invalid_schema() -> None:
    with pytest.raises(JsonLLMError):
        generate_json(DummyLLM('{"name":"a"}'), "prompt", DummyModel)
