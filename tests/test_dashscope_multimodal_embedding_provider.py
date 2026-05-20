from __future__ import annotations

from http import HTTPStatus
from types import SimpleNamespace
import sys

import pytest
from pydantic import ValidationError

from agentic_rag.config import Settings
from agentic_rag.models.providers import (
    DashScopeMultimodalEmbeddingProvider,
    DashScopeMultimodalImageEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    ProviderError,
    build_embedding_provider,
    build_image_embedding_provider,
)


class FakeMultiModalEmbedding:
    calls: list[dict] = []
    response = SimpleNamespace(
        status_code=HTTPStatus.OK,
        output={"embeddings": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]},
        message="",
        request_id="req-ok",
    )

    @classmethod
    def call(cls, **kwargs):
        cls.calls.append(kwargs)
        return cls.response


def _install_fake_dashscope(monkeypatch, response=None):
    FakeMultiModalEmbedding.calls = []
    if response is not None:
        FakeMultiModalEmbedding.response = response
    else:
        FakeMultiModalEmbedding.response = SimpleNamespace(
            status_code=HTTPStatus.OK,
            output={"embeddings": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]},
            message="",
            request_id="req-ok",
        )
    fake_module = SimpleNamespace(MultiModalEmbedding=FakeMultiModalEmbedding)
    monkeypatch.setitem(sys.modules, "dashscope", fake_module)
    return fake_module


def test_dashscope_text_embedding_builds_text_payload(monkeypatch) -> None:
    _install_fake_dashscope(monkeypatch)
    settings = Settings(
        embedding_provider_type="dashscope_multimodal",
        dashscope_api_key="sk-test",
        dashscope_embedding_model="qwen3-vl-embedding",
        dashscope_embedding_dimension=1024,
        embedding_dimensions=1024,
        embedding_batch_size=8,
    )
    provider = DashScopeMultimodalEmbeddingProvider(settings)

    vectors = provider.embed_texts(["hello", "world"])

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    call = FakeMultiModalEmbedding.calls[0]
    assert call["api_key"] == "sk-test"
    assert call["model"] == "qwen3-vl-embedding"
    assert call["dimension"] == 1024
    assert call["input"] == [{"text": "hello"}, {"text": "world"}]


def test_dashscope_text_embedding_non_ok_raises(monkeypatch) -> None:
    _install_fake_dashscope(
        monkeypatch,
        response=SimpleNamespace(status_code=HTTPStatus.BAD_REQUEST, output={}, message="bad model", request_id="r1"),
    )
    settings = Settings(
        embedding_provider_type="dashscope_multimodal",
        dashscope_api_key="sk-test",
        embedding_batch_size=1,
        embedding_max_retries=1,
    )
    provider = DashScopeMultimodalEmbeddingProvider(settings)

    with pytest.raises(ProviderError, match="DashScope multimodal embedding failed"):
        provider.embed_texts(["hello"])


def test_dashscope_text_embedding_missing_embedding_raises(monkeypatch) -> None:
    _install_fake_dashscope(
        monkeypatch,
        response=SimpleNamespace(status_code=HTTPStatus.OK, output={"embeddings": [{"nope": []}]}, message="", request_id="r2"),
    )
    settings = Settings(
        embedding_provider_type="dashscope_multimodal",
        dashscope_api_key="sk-test",
        embedding_batch_size=1,
        embedding_max_retries=1,
    )
    provider = DashScopeMultimodalEmbeddingProvider(settings)

    with pytest.raises(ProviderError, match="missing embeddings|missing embedding|not a list"):
        provider.embed_texts(["hello"])


def test_dashscope_image_embedding_builds_image_payload(monkeypatch, tmp_path) -> None:
    image = tmp_path / "img.png"
    image.write_bytes(b"fake")
    _install_fake_dashscope(
        monkeypatch,
        response=SimpleNamespace(
            status_code=HTTPStatus.OK,
            output={"embeddings": [{"embedding": [0.5, 0.6]}]},
            message="",
            request_id="req-image",
        ),
    )
    settings = Settings(
        image_embed_provider_type="dashscope_multimodal",
        dashscope_api_key="sk-test",
        dashscope_embedding_model="qwen3-vl-embedding",
        dashscope_embedding_dimension=1024,
        embedding_dimensions=1024,
        image_embed_batch_size=8,
    )
    provider = DashScopeMultimodalImageEmbeddingProvider(settings)

    vectors = provider.embed_images([str(image)])

    assert vectors == [[0.5, 0.6]]
    call = FakeMultiModalEmbedding.calls[0]
    assert call["input"] == [{"image": str(image.resolve())}]
    assert call["model"] == "qwen3-vl-embedding"
    assert call["dimension"] == 1024


def test_dashscope_image_embedding_url_is_passed_through(monkeypatch) -> None:
    _install_fake_dashscope(
        monkeypatch,
        response=SimpleNamespace(
            status_code=HTTPStatus.OK,
            output={"embedding": [0.7, 0.8]},
            message="",
            request_id="req-url",
        ),
    )
    settings = Settings(image_embed_provider_type="dashscope_multimodal", dashscope_api_key="sk-test")
    provider = DashScopeMultimodalImageEmbeddingProvider(settings)

    vectors = provider.embed_images(["https://example.com/img.png"])

    assert vectors == [[0.7, 0.8]]
    assert FakeMultiModalEmbedding.calls[0]["input"] == [{"image": "https://example.com/img.png"}]


def test_dashscope_factories_select_provider(monkeypatch) -> None:
    _install_fake_dashscope(monkeypatch)
    assert isinstance(
        build_embedding_provider(Settings(embedding_provider_type="openai_compatible")),
        OpenAICompatibleEmbeddingProvider,
    )
    assert isinstance(
        build_embedding_provider(
            Settings(embedding_provider_type="dashscope_multimodal", dashscope_api_key="sk-test")
        ),
        DashScopeMultimodalEmbeddingProvider,
    )
    assert isinstance(
        build_image_embedding_provider(
            Settings(image_embed_provider_type="dashscope_multimodal", dashscope_api_key="sk-test")
        ),
        DashScopeMultimodalImageEmbeddingProvider,
    )


def test_dashscope_config_validation() -> None:
    with pytest.raises(ValidationError):
        Settings(embedding_provider_type="invalid")
    with pytest.raises(ValidationError, match="dashscope_embedding_dimension must match embedding_dimensions"):
        Settings(embedding_dimensions=1024, dashscope_embedding_dimension=768)
