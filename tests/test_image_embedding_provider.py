from __future__ import annotations

import pytest

from agentic_rag.config import Settings
from agentic_rag.models.providers import (
    OpenAICompatibleClient,
    OpenAICompatibleImageEmbeddingProvider,
    ProviderError,
)


def test_image_embedding_provider_success(monkeypatch) -> None:
    settings = Settings(
        image_embed_base_url="https://example.com/v1",
        image_embed_api_key="",
        image_embed_model="qwen3-vl-embedding",
        image_embed_batch_size=8,
        image_embed_max_retries=2,
    )
    provider = OpenAICompatibleImageEmbeddingProvider(settings)

    payloads: list[dict] = []

    def fake_data_url(_path: str) -> str:
        return "data:image/png;base64,ZmFrZQ=="

    def fake_post(_self, path: str, payload: dict):
        payloads.append(payload)
        assert path == "embeddings"
        return {
            "data": [
                {"embedding": [0.1, 0.2, 0.3]},
                {"embedding": [0.4, 0.5, 0.6]},
            ]
    }

    monkeypatch.setattr(provider, "_image_to_data_url", fake_data_url)
    monkeypatch.setattr(OpenAICompatibleClient, "post", fake_post)

    vectors = provider.embed_images(["a.png", "b.png"])
    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert payloads
    assert payloads[0]["model"] == "qwen3-vl-embedding"


def test_image_embedding_provider_retry_and_error(monkeypatch) -> None:
    settings = Settings(
        image_embed_max_retries=3,
        image_embed_batch_size=4,
    )
    provider = OpenAICompatibleImageEmbeddingProvider(settings)

    attempts = {"count": 0}

    def fail_embed(_batch: list[str]) -> list[list[float]]:
        attempts["count"] += 1
        raise ProviderError("mock image embed failure")

    monkeypatch.setattr(provider, "_embed_batch_once", fail_embed)

    with pytest.raises(ProviderError):
        provider.embed_images(["a.png"])

    assert attempts["count"] == 3
