from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from agentic_rag.config import Settings
from agentic_rag.ingestion.adapters.image_vlm_client import ImageVLMClient, ImageVLMError


def _write_png(path):
    path.write_bytes(b"fake image")


def test_default_image_vlm_model_is_qwen_vl_plus() -> None:
    assert Settings().image_vlm_model == "qwen3-vl-plus"


def test_openai_compatible_payload_uses_qwen_vl_plus_and_json_prompt(tmp_path, monkeypatch) -> None:
    image = tmp_path / "img.png"
    _write_png(image)
    captured = {}

    def fake_post_stream(self, path, payload):
        captured["path"] = path
        captured["payload"] = payload
        yield {"choices": [{"delta": {"content": '{"caption":"cap","scene_type":"screenshot",'}}]}
        yield {"choices": [{"delta": {"content": '"visible_text_summary":"","objects":[],"confidence":0.9}'}}]}

    monkeypatch.setattr("agentic_rag.models.providers.OpenAICompatibleClient.post_stream", fake_post_stream)
    client = ImageVLMClient(Settings(image_vlm_provider="openai_compatible", image_vlm_enable_thinking=False))

    desc = client.describe_image(image)

    assert desc.caption == "cap"
    assert captured["path"] == "chat/completions"
    assert captured["payload"]["model"] == "qwen3-vl-plus"
    assert captured["payload"]["stream"] is True
    assert captured["payload"]["enable_thinking"] is False
    assert "thinking_budget" not in captured["payload"]
    prompt = captured["payload"]["messages"][0]["content"][1]["text"]
    assert "请只输出一个 JSON 对象" in prompt
    assert "????" not in prompt
    assert captured["payload"]["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_openai_compatible_thinking_payload_uses_top_level_fields(tmp_path, monkeypatch) -> None:
    image = tmp_path / "img.png"
    _write_png(image)
    captured = {}

    def fake_post_stream(self, path, payload):
        captured.update(payload)
        yield {"choices": [{"delta": {"reasoning_content": "thinking", "content": ""}}]}
        yield {
            "choices": [
                {
                    "delta": {
                        "content": '{"caption":"cap","scene_type":"photo","visible_text_summary":"","objects":[],"confidence":0.8}'
                    }
                }
            ]
        }

    monkeypatch.setattr("agentic_rag.models.providers.OpenAICompatibleClient.post_stream", fake_post_stream)
    client = ImageVLMClient(Settings(image_vlm_provider="openai_compatible", image_vlm_enable_thinking=True))

    desc = client.describe_image(image)

    assert desc.caption == "cap"
    assert captured["enable_thinking"] is True
    assert captured["thinking_budget"] == 81920
    assert "extra_body" not in captured


def test_dashscope_sdk_payload_and_response_parse(tmp_path, monkeypatch) -> None:
    image = tmp_path / "img.png"
    _write_png(image)
    captured = {}

    class FakeMultiModalConversation:
        @staticmethod
        def call(**kwargs):
            captured.update(kwargs)
            return iter(
                [
                    SimpleNamespace(
                        output=SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    message=SimpleNamespace(
                                        content=[{"text": '{"caption":"dog","scene_type":"photo",'}]
                                    )
                                )
                            ]
                        )
                    ),
                    SimpleNamespace(
                        output=SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    message=SimpleNamespace(
                                        content=[{"text": '"visible_text_summary":"","objects":[],"confidence":0.8}'}]
                                    )
                                )
                            ]
                        )
                    ),
                ]
            )

    fake_dashscope = SimpleNamespace(MultiModalConversation=FakeMultiModalConversation, base_http_api_url="")
    monkeypatch.setitem(sys.modules, "dashscope", fake_dashscope)
    client = ImageVLMClient(
        Settings(image_vlm_provider="dashscope_sdk", image_vlm_api_key="sk-test", image_vlm_enable_thinking=False)
    )

    desc = client.describe_image(image)

    assert desc.caption == "dog"
    assert captured["api_key"] == "sk-test"
    assert captured["model"] == "qwen3-vl-plus"
    assert captured["stream"] is True
    assert captured["enable_thinking"] is False
    assert "thinking_budget" not in captured
    assert captured["messages"][0]["content"][0]["image"].startswith("data:image/png;base64,")
    assert fake_dashscope.base_http_api_url == "https://dashscope.aliyuncs.com/api/v1"


def test_image_vlm_json_validation_error_contains_raw_excerpt(tmp_path, monkeypatch) -> None:
    image = tmp_path / "img.png"
    _write_png(image)

    def fake_post_stream(self, path, payload):
        yield {"choices": [{"delta": {"content": "not json response"}}]}

    monkeypatch.setattr("agentic_rag.models.providers.OpenAICompatibleClient.post_stream", fake_post_stream)
    client = ImageVLMClient(Settings(image_vlm_provider="openai_compatible"))

    with pytest.raises(ImageVLMError, match="raw_excerpt=not json response"):
        client.describe_image(image)
