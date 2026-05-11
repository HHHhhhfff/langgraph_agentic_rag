from __future__ import annotations

import base64
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from agentic_rag.config import Settings
from agentic_rag.ingestion.token_splitter import TokenHardSplitter, TokenSplitConfig, looks_like_markdown_table
from agentic_rag.observability.stage_logger import StageLogger, StageTimer


class ProviderError(RuntimeError):
    """Raised when remote model provider call fails."""


@dataclass(slots=True)
class OpenAICompatibleClient:
    """Thin OpenAI-compatible HTTP client wrapper."""

    base_url: str
    api_key: str
    timeout: float

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _safe_response_excerpt(text: str, max_chars: int = 400) -> str:
        compact = " ".join(text.strip().split())
        return compact[:max_chars]

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, headers=self._headers(), json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            excerpt = self._safe_response_excerpt(exc.response.text)
            raise ProviderError(
                f"HTTP {exc.response.status_code} calling {url}: {excerpt}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"HTTP error calling {url}: {exc}") from exc
        except ValueError as exc:
            raise ProviderError(f"Invalid JSON from {url}: {exc}") from exc


class EmbeddingProvider(ABC):
    """Abstract embedding provider."""

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class ImageEmbeddingProvider(ABC):
    """Abstract image embedding provider."""

    @abstractmethod
    def embed_images(self, image_paths: list[str]) -> list[list[float]]:
        raise NotImplementedError


class Reranker(ABC):
    """Abstract reranker provider."""

    @abstractmethod
    def rerank(self, query: str, documents: list[str], top_n: int) -> list[dict[str, Any]]:
        raise NotImplementedError


class LLMClient(ABC):
    """Abstract text generation provider."""

    @abstractmethod
    def generate(self, prompt: str) -> str:
        raise NotImplementedError


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """Embedding provider using /embeddings endpoint."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.stage_logger: StageLogger | None = None
        self._hard_splitter = TokenHardSplitter(
            TokenSplitConfig(
                max_input_tokens=settings.embedding_input_max_tokens,
                safety_margin_tokens=settings.embedding_input_safety_margin_tokens,
            )
        )
        self.client = OpenAICompatibleClient(
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key,
            timeout=settings.embedding_timeout_sec,
        )

    def set_stage_logger(self, stage_logger: StageLogger) -> None:
        self.stage_logger = stage_logger

    def _embed_batch_once(self, texts: list[str]) -> list[list[float]]:
        payload: dict[str, Any] = {
            "model": self.settings.embedding_model,
            "input": texts,
        }
        if self.settings.embedding_dimensions:
            payload["dimensions"] = self.settings.embedding_dimensions
        data = self.client.post("embeddings", payload)
        rows = data.get("data")
        if not isinstance(rows, list):
            raise ProviderError("Embedding response missing data list")
        vectors: list[list[float]] = []
        for item in rows:
            emb = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(emb, list):
                raise ProviderError("Embedding response item missing embedding")
            vectors.append([float(x) for x in emb])
        return vectors

    @staticmethod
    def _is_input_too_long_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        if "maximum input length" in msg:
            return True
        if "invalid 'input" in msg and "length" in msg:
            return True
        if "input length" in msg and "token" in msg:
            return True
        return bool(re.search(r"\b8192\b.*token", msg))

    @staticmethod
    def _merge_vectors(vectors: list[list[float]], weights: list[int]) -> list[float]:
        if not vectors:
            raise ProviderError("cannot merge empty vectors")
        dim = len(vectors[0])
        if dim == 0:
            raise ProviderError("cannot merge zero-dimension vectors")
        if len(vectors) != len(weights):
            raise ProviderError("merge vector/weight count mismatch")
        acc = [0.0] * dim
        total = 0.0
        for vector, weight in zip(vectors, weights):
            if len(vector) != dim:
                raise ProviderError("merge vector dimension mismatch")
            w = float(max(1, weight))
            total += w
            for i, value in enumerate(vector):
                acc[i] += value * w
        return [x / total for x in acc]

    def _embed_single_with_recovery(
        self,
        text: str,
        *,
        retry_call,
        batch_index: int,
        depth: int = 0,
    ) -> list[float]:
        if self._hard_splitter.count_tokens(text) > self._hard_splitter.config.hard_limit:
            segments = self._hard_splitter.split_hard(
                text,
                prefer_table=looks_like_markdown_table(text),
            )
            if len(segments) > 1:
                if self.stage_logger:
                    self.stage_logger.log_counter(
                        "embedding_pre_split",
                        parser="embedding_provider",
                        batch_index=batch_index,
                        depth=depth,
                        split_count=len(segments),
                        fallback=True,
                    )
                seg_vectors: list[list[float]] = []
                seg_weights: list[int] = []
                for segment in segments:
                    vector = self._embed_single_with_recovery(
                        segment,
                        retry_call=retry_call,
                        batch_index=batch_index,
                        depth=depth + 1,
                    )
                    seg_vectors.append(vector)
                    seg_weights.append(max(1, self._hard_splitter.count_tokens(segment)))
                return self._merge_vectors(seg_vectors, seg_weights)
        try:
            vectors = retry_call([text])
            if not vectors:
                raise ProviderError("embedding provider returned empty vector list")
            return vectors[0]
        except Exception as exc:
            if not self._is_input_too_long_error(exc):
                raise
            if depth >= 5:
                raise ProviderError("embedding recursive split depth exceeded") from exc

            segments = self._hard_splitter.split_hard(
                text,
                prefer_table=looks_like_markdown_table(text),
            )
            if len(segments) <= 1:
                raise

            if self.stage_logger:
                self.stage_logger.log_counter(
                    "embedding_auto_resplit",
                    parser="embedding_provider",
                    batch_index=batch_index,
                    depth=depth,
                    split_count=len(segments),
                    fallback=True,
                    error_type=type(exc).__name__,
                    error_msg=str(exc),
                )

            seg_vectors: list[list[float]] = []
            seg_weights: list[int] = []
            for segment in segments:
                vector = self._embed_single_with_recovery(
                    segment,
                    retry_call=retry_call,
                    batch_index=batch_index,
                    depth=depth + 1,
                )
                seg_vectors.append(vector)
                seg_weights.append(max(1, self._hard_splitter.count_tokens(segment)))
            return self._merge_vectors(seg_vectors, seg_weights)

    def _embed_batch_with_recovery(self, batch: list[str], *, retry_call, batch_index: int) -> list[list[float]]:
        if any(self._hard_splitter.count_tokens(text) > self._hard_splitter.config.hard_limit for text in batch):
            recovered = [
                self._embed_single_with_recovery(
                    text,
                    retry_call=retry_call,
                    batch_index=batch_index,
                )
                for text in batch
            ]
            if self.stage_logger:
                self.stage_logger.log_counter(
                    "embedding_batch_presplit_recover",
                    parser="embedding_provider",
                    batch_index=batch_index,
                    chunk_count=len(batch),
                    fallback=True,
                )
            return recovered
        try:
            return retry_call(batch)
        except Exception as exc:
            if not self._is_input_too_long_error(exc):
                raise
            if self.stage_logger:
                self.stage_logger.log_counter(
                    "embedding_overlength_recover",
                    parser="embedding_provider",
                    batch_index=batch_index,
                    chunk_count=len(batch),
                    fallback=True,
                    error_type=type(exc).__name__,
                    error_msg=str(exc),
                )
            recovered: list[list[float]] = []
            for text in batch:
                recovered.append(
                    self._embed_single_with_recovery(
                        text,
                        retry_call=retry_call,
                        batch_index=batch_index,
                    )
                )
            return recovered

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        safe_texts = [t.strip() if isinstance(t, str) else "" for t in texts]
        safe_texts = [t if t else "[empty-chunk]" for t in safe_texts]

        retry_call = retry(
            reraise=True,
            stop=stop_after_attempt(self.settings.embedding_max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type(ProviderError),
        )(self._embed_batch_once)
        batch_size = self.settings.embedding_batch_size
        vectors: list[list[float]] = []
        for i in range(0, len(safe_texts), batch_size):
            batch = safe_texts[i : i + batch_size]
            timer = StageTimer.start_now()
            if self.stage_logger:
                self.stage_logger.log_stage_start(
                    "embedding_batch",
                    parser="embedding_provider",
                    chunk_count=len(batch),
                    batch_index=i,
                )
            try:
                out = self._embed_batch_with_recovery(batch, retry_call=retry_call, batch_index=i)
            except Exception as exc:
                if self.stage_logger:
                    self.stage_logger.log_stage_error(
                        "embedding_batch",
                        exc,
                        parser="embedding_provider",
                        batch_index=i,
                    )
                raise
            vectors.extend(out)
            if self.stage_logger:
                self.stage_logger.log_stage_end(
                    "embedding_batch",
                    latency_ms=timer.elapsed_ms(),
                    parser="embedding_provider",
                    chunk_count=len(batch),
                    vector_count=len(out),
                    batch_index=i,
                )
        return vectors


class OpenAICompatibleImageEmbeddingProvider(ImageEmbeddingProvider):
    """Image embedding provider using OpenAI-compatible /embeddings endpoint."""

    MIME_MAP = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".gif": "image/gif",
    }

    def __init__(self, settings: Settings):
        self.settings = settings
        self.stage_logger: StageLogger | None = None
        self.client = OpenAICompatibleClient(
            base_url=settings.image_embed_base_url,
            api_key=settings.image_embed_api_key,
            timeout=settings.image_embed_timeout_sec,
        )

    def set_stage_logger(self, stage_logger: StageLogger) -> None:
        self.stage_logger = stage_logger

    def _image_to_data_url(self, image_path: str) -> str:
        path = Path(image_path)
        if self.settings.image_embed_require_file_exists and not path.exists():
            raise ProviderError(f"Image file not found: {path}")
        if not path.exists():
            raise ProviderError(f"Image file not accessible: {path}")
        mime = self.MIME_MAP.get(path.suffix.lower(), "application/octet-stream")
        try:
            content = path.read_bytes()
        except Exception as exc:
            raise ProviderError(f"Failed to read image file {path}: {exc}") from exc
        encoded = base64.b64encode(content).decode("utf-8")
        return f"data:{mime};base64,{encoded}"

    @staticmethod
    def _parse_vectors(data: dict[str, Any]) -> list[list[float]]:
        rows = data.get("data")
        if not isinstance(rows, list):
            raise ProviderError("Image embedding response missing data list")
        vectors: list[list[float]] = []
        for row in rows:
            emb = row.get("embedding") if isinstance(row, dict) else None
            if not isinstance(emb, list):
                raise ProviderError("Image embedding response item missing embedding")
            vectors.append([float(x) for x in emb])
        return vectors

    def _embed_batch_once(self, image_paths: list[str]) -> list[list[float]]:
        data_urls = [self._image_to_data_url(path) for path in image_paths]

        primary_payload: dict[str, Any] = {
            "model": self.settings.image_embed_model,
            "input": [
                {
                    "type": "input_image",
                    "image_url": data_url,
                }
                for data_url in data_urls
            ],
        }
        try:
            data = self.client.post("embeddings", primary_payload)
            vectors = self._parse_vectors(data)
            if len(vectors) == len(image_paths):
                return vectors
        except ProviderError:
            pass

        # Some OpenAI-compatible providers only accept the chat-style image_url object.
        fallback_payload: dict[str, Any] = {
            "model": self.settings.image_embed_model,
            "input": [
                {
                    "type": "image_url",
                    "image_url": {"url": data_url},
                }
                for data_url in data_urls
            ],
        }
        data = self.client.post("embeddings", fallback_payload)
        vectors = self._parse_vectors(data)
        if len(vectors) != len(image_paths):
            raise ProviderError(
                f"Image embedding count mismatch: expected={len(image_paths)}, got={len(vectors)}"
            )
        return vectors

    def embed_images(self, image_paths: list[str]) -> list[list[float]]:
        if not image_paths:
            return []
        retry_call = retry(
            reraise=True,
            stop=stop_after_attempt(self.settings.image_embed_max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type(ProviderError),
        )(self._embed_batch_once)

        batch_size = self.settings.image_embed_batch_size
        vectors: list[list[float]] = []
        for i in range(0, len(image_paths), batch_size):
            batch = image_paths[i : i + batch_size]
            timer = StageTimer.start_now()
            if self.stage_logger:
                self.stage_logger.log_stage_start(
                    "image_embedding_batch",
                    parser="image_embedding_provider",
                    modality="image",
                    image_count=len(batch),
                    batch_index=i,
                )
            try:
                out = retry_call(batch)
            except Exception as exc:
                if self.stage_logger:
                    self.stage_logger.log_stage_error(
                        "image_embedding_batch",
                        exc,
                        parser="image_embedding_provider",
                        modality="image",
                        image_count=len(batch),
                        batch_index=i,
                    )
                raise
            vectors.extend(out)
            if self.stage_logger:
                self.stage_logger.log_stage_end(
                    "image_embedding_batch",
                    latency_ms=timer.elapsed_ms(),
                    parser="image_embedding_provider",
                    modality="image",
                    image_count=len(batch),
                    vector_count=len(out),
                    batch_index=i,
                )
        return vectors


class OpenAICompatibleReranker(Reranker):
    """Reranker using OpenAI-compatible /rerank endpoint."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.stage_logger: StageLogger | None = None
        self.client = OpenAICompatibleClient(
            base_url=settings.rerank_base_url,
            api_key=settings.rerank_api_key,
            timeout=settings.rerank_timeout_sec,
        )

    def set_stage_logger(self, stage_logger: StageLogger) -> None:
        self.stage_logger = stage_logger

    def _rerank_once(self, query: str, documents: list[str], top_n: int) -> list[dict[str, Any]]:
        payload = {
            "model": self.settings.rerank_model,
            "query": query,
            "documents": documents,
            "top_n": top_n,
        }
        data = self.client.post("services/rerank/text-rerank/text-rerank", payload)  # rerank请求URL后缀格式
        rows = data.get("data")
        if not isinstance(rows, list):
            raise ProviderError("Rerank response missing data list")
        parsed: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            index = row.get("index")
            score = row.get("relevance_score")
            if isinstance(index, int) and isinstance(score, (int, float)):
                parsed.append({"index": index, "score": float(score)})
        return parsed

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[dict[str, Any]]:
        timer = StageTimer.start_now()
        if self.stage_logger:
            self.stage_logger.log_stage_start(
                "rerank_call",
                parser="reranker_provider",
                chunk_count=len(documents),
            )
        retry_call = retry(
            reraise=True,
            stop=stop_after_attempt(self.settings.rerank_max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=4),
            retry=retry_if_exception_type(ProviderError),
        )(self._rerank_once)
        try:
            out = retry_call(query, documents, top_n)
        except Exception as exc:
            if self.stage_logger:
                self.stage_logger.log_stage_error("rerank_call", exc, parser="reranker_provider")
            raise
        if self.stage_logger:
            self.stage_logger.log_stage_end(
                "rerank_call",
                latency_ms=timer.elapsed_ms(),
                parser="reranker_provider",
                chunk_count=len(documents),
            )
        return out


class OpenAICompatibleLLMClient(LLMClient):
    """LLM client using /chat/completions endpoint."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.stage_logger: StageLogger | None = None
        self.client = OpenAICompatibleClient(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            timeout=settings.llm_timeout_sec,
        )

    def set_stage_logger(self, stage_logger: StageLogger) -> None:
        self.stage_logger = stage_logger

    def _generate_once(self, prompt: str) -> str:
        payload = {
            "model": self.settings.llm_model,
            "temperature": self.settings.llm_temperature,
            "max_tokens": self.settings.llm_max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a rigorous RAG assistant. Follow the provided instructions exactly.",
                },
                {"role": "user", "content": prompt},
            ],
        }
        data = self.client.post("chat/completions", payload)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError("LLM response missing choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise ProviderError("Invalid LLM choice format")
        message = first.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ProviderError("LLM response missing message content")
        return message["content"].strip()

    def generate(self, prompt: str) -> str:
        retry_call = retry(
            reraise=True,
            stop=stop_after_attempt(self.settings.llm_max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=4),
            retry=retry_if_exception_type(ProviderError),
        )(self._generate_once)
        return retry_call(prompt)


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Factory for embedding provider."""

    return OpenAICompatibleEmbeddingProvider(settings)


def build_image_embedding_provider(settings: Settings) -> ImageEmbeddingProvider:
    """Factory for image embedding provider."""

    return OpenAICompatibleImageEmbeddingProvider(settings)


def build_reranker(settings: Settings) -> Reranker:
    """Factory for reranker provider."""

    return OpenAICompatibleReranker(settings)


def build_llm_client(settings: Settings) -> LLMClient:
    """Factory for generation client."""

    return OpenAICompatibleLLMClient(settings)

