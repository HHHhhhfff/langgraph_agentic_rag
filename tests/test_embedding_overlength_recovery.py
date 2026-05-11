from __future__ import annotations

import pytest

from agentic_rag.config import Settings
from agentic_rag.models.providers import OpenAICompatibleEmbeddingProvider, ProviderError


def _long_text(repeat: int = 6000) -> str:
    return ("token " * repeat).strip()


def test_embedding_hard_splitter_limits_segments() -> None:
    settings = Settings(
        embedding_input_max_tokens=200,
        embedding_input_safety_margin_tokens=20,
    )
    provider = OpenAICompatibleEmbeddingProvider(settings)
    long_text = _long_text(1200)

    parts = provider._hard_splitter.split_hard(long_text, prefer_table=False)
    assert len(parts) > 1
    assert all(
        provider._hard_splitter.count_tokens(p) <= provider._hard_splitter.config.hard_limit
        for p in parts
    )


def test_embedding_batch_overlength_auto_recover(monkeypatch) -> None:
    settings = Settings(
        embedding_batch_size=16,
        embedding_max_retries=1,
        embedding_input_max_tokens=120,
        embedding_input_safety_margin_tokens=10,
    )
    provider = OpenAICompatibleEmbeddingProvider(settings)

    def fake_retry_call(batch: list[str]) -> list[list[float]]:
        if len(batch) > 1:
            raise ProviderError("HTTP 400: Invalid 'input[1]': maximum input length is 8192 tokens.")
        return [[float(len(batch[0]) % 10), 1.0, 2.0]]

    out = provider._embed_batch_with_recovery(
        ["short text", _long_text(800)],
        retry_call=fake_retry_call,
        batch_index=0,
    )
    assert len(out) == 2
    assert all(len(v) == 3 for v in out)


def test_embedding_single_overlength_pre_split(monkeypatch) -> None:
    settings = Settings(
        embedding_input_max_tokens=120,
        embedding_input_safety_margin_tokens=10,
        embedding_max_retries=1,
    )
    provider = OpenAICompatibleEmbeddingProvider(settings)

    call_count = {"n": 0}

    def fake_retry_call(batch: list[str]) -> list[list[float]]:
        call_count["n"] += 1
        if len(batch) != 1:
            raise ProviderError("expected singleton batch")
        text = batch[0]
        if provider._hard_splitter.count_tokens(text) > provider._hard_splitter.config.hard_limit:
            raise ProviderError("maximum input length is 8192 tokens")
        return [[0.1, 0.2, 0.3]]

    vec = provider._embed_single_with_recovery(
        _long_text(1200),
        retry_call=fake_retry_call,
        batch_index=0,
    )
    assert vec == pytest.approx([0.1, 0.2, 0.3], rel=1e-6, abs=1e-9)
    assert call_count["n"] > 1
