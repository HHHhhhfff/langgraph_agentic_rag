from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentic_rag.config import Settings
from agentic_rag.ingestion.chunker import ChunkConfig, TextChunker
from agentic_rag.ingestion.multimodal_orchestrator import MultiModalOrchestrator
from agentic_rag.ingestion.node_schema import Node
from agentic_rag.ingestion.parser import MarkdownParser
from agentic_rag.models.providers import (
    EmbeddingProvider,
    ImageEmbeddingProvider,
    build_image_embedding_provider,
)
from agentic_rag.observability.stage_logger import StageLogger, StageTimer
from agentic_rag.retrieval.index_persistence import (
    build_persistent_retrieval_indexes,
    hits_from_chunks,
    hits_from_nodes,
)
from agentic_rag.schemas import SearchHit
from agentic_rag.store.qdrant_store import QdrantStore


class IndexBuildError(RuntimeError):
    """Raised for index building pipeline errors."""


@dataclass(slots=True)
class IndexBuildSummary:
    """Index building statistics."""

    documents: int
    chunks: int
    vectors: int
    upserted: int
    vector_size: int
    failed_files: int = 0


class IndexBuilder:
    """Build vector index from markdown files."""

    def __init__(
        self,
        settings: Settings,
        parser: MarkdownParser,
        chunker: TextChunker,
        embedding_provider: EmbeddingProvider,
        store: QdrantStore,
        image_embedding_provider: ImageEmbeddingProvider | None = None,
        stage_logger: StageLogger | None = None,
        run_id: str = "",
    ):
        self.settings = settings
        self.parser = parser
        self.chunker = chunker
        self.embedding_provider = embedding_provider
        self.image_embedding_provider = image_embedding_provider or build_image_embedding_provider(settings)
        self.store = store
        self.stage_logger = stage_logger
        self.run_id = run_id
        self.multimodal_orchestrator: MultiModalOrchestrator | None = None
        if self.stage_logger is not None:
            if hasattr(self.store, "set_stage_logger"):
                self.store.set_stage_logger(self.stage_logger)
            if hasattr(self.embedding_provider, "set_stage_logger"):
                self.embedding_provider.set_stage_logger(self.stage_logger)
            if hasattr(self.image_embedding_provider, "set_stage_logger"):
                self.image_embedding_provider.set_stage_logger(self.stage_logger)

    def _normalize_vector_size(self, vectors: list[list[float]]) -> tuple[list[list[float]], int]:
        """Validate and normalize vector dimensions with configured strategy."""

        if not vectors:
            raise IndexBuildError("No vectors generated")

        expected_dim = self.settings.embedding_dimensions
        inferred_dim = len(vectors[0])
        if inferred_dim <= 0:
            raise IndexBuildError("Invalid embedding vector dimension")

        target_dim = expected_dim or inferred_dim
        normalized: list[list[float]] = []
        for idx, vector in enumerate(vectors):
            dim = len(vector)
            if dim == target_dim:
                normalized.append(vector)
                continue
            if dim > target_dim:
                if self.settings.vector_mismatch_strategy == "truncate":
                    normalized.append(vector[:target_dim])
                    continue
                raise IndexBuildError(
                    f"Vector dimension at index {idx} is larger than target: {dim} > {target_dim}"
                )
            raise IndexBuildError(
                f"Vector dimension at index {idx} is smaller than target: {dim} < {target_dim}"
            )
        return normalized, target_dim

    def build_from_directory(self, markdown_dir: str) -> IndexBuildSummary:
        if self.stage_logger:
            mode = "multimodal" if self.settings.ingestion_engine == "multimodal" and self.settings.multimodal_enabled else "legacy"
            self.stage_logger.log_counter(
                "ingestion_engine_selected",
                source=markdown_dir,
                engine=mode,
            )
        if self.settings.ingestion_engine == "multimodal" and self.settings.multimodal_enabled:
            return self._build_multimodal_from_directory(markdown_dir)

        docs = self.parser.parse_directory(markdown_dir)
        if not docs:
            raise IndexBuildError("No markdown documents found")

        chunks = self.chunker.chunk_documents(docs)
        if not chunks:
            raise IndexBuildError("No chunks produced from input documents")

        embed_timer = StageTimer.start_now()
        if self.stage_logger:
            self.stage_logger.log_stage_start(
                "legacy_embedding",
                source=markdown_dir,
                chunk_count=len(chunks),
            )
        try:
            vectors = self.embedding_provider.embed_texts([c.text for c in chunks])
        except Exception as exc:
            if self.stage_logger:
                self.stage_logger.log_stage_error("legacy_embedding", exc, source=markdown_dir)
            raise IndexBuildError(f"Embedding failed during index build: {exc}") from exc
        if self.stage_logger:
            self.stage_logger.log_stage_end(
                "legacy_embedding",
                latency_ms=embed_timer.elapsed_ms(),
                source=markdown_dir,
                chunk_count=len(chunks),
                vector_count=len(vectors),
            )

        normalized_vectors, vector_size = self._normalize_vector_size(vectors)

        self.store.ensure_collection(vector_size=vector_size)
        upsert_timer = StageTimer.start_now()
        if self.stage_logger:
            self.stage_logger.log_stage_start(
                "legacy_upsert",
                source=markdown_dir,
                vector_count=len(normalized_vectors),
            )
        upserted = self.store.upsert_chunks(
            chunks=chunks,
            vectors=normalized_vectors,
            batch_size=max(1, self.settings.embedding_batch_size * 2),
        )
        if self.stage_logger:
            self.stage_logger.log_stage_end(
                "legacy_upsert",
                latency_ms=upsert_timer.elapsed_ms(),
                source=markdown_dir,
                upserted_count=upserted,
                vector_count=len(normalized_vectors),
            )
        self._persist_retrieval_indexes(hits_from_chunks(chunks), source=markdown_dir)

        return IndexBuildSummary(
            documents=len(docs),
            chunks=len(chunks),
            vectors=len(normalized_vectors),
            upserted=upserted,
            vector_size=vector_size,
            failed_files=0,
        )

    def _build_multimodal_from_directory(self, input_dir: str) -> IndexBuildSummary:
        if self.multimodal_orchestrator is None:
            self.multimodal_orchestrator = MultiModalOrchestrator(
                self.settings,
                stage_logger=self.stage_logger,
                run_id=self.run_id,
            )
        parse_timer = StageTimer.start_now()
        if self.stage_logger:
            self.stage_logger.log_stage_start("multimodal_parse_directory", source=input_dir)
        result = self.multimodal_orchestrator.parse_directory(input_dir)
        if self.stage_logger:
            self.stage_logger.log_stage_end(
                "multimodal_parse_directory",
                latency_ms=parse_timer.elapsed_ms(),
                source=input_dir,
                chunk_count=len(result.nodes),
                failed_files=len(result.failures),
            )
        nodes = result.nodes
        if not nodes and not result.failures:
            raise IndexBuildError("No multimodal nodes produced from input directory")
        if not nodes:
            raise IndexBuildError(
                f"Multimodal parsing produced no nodes. failures={len(result.failures)}"
            )

        vector_by_idx: dict[int, list[float]] = {}
        extra_failures = 0

        text_table_pairs: list[tuple[int, str]] = []
        image_pairs: list[tuple[int, str, str]] = []
        for idx, node in enumerate(nodes):
            if node.modality == "image":
                image_path = node.image_path or node.metadata.source
                fallback_text = node.text or f"Image file: {Path(image_path).name}"
                if self.settings.image_embed_mode == "caption_text":
                    text_table_pairs.append((idx, fallback_text))
                else:
                    image_pairs.append((idx, image_path, fallback_text))
                continue
            if node.modality == "table":
                text_table_pairs.append((idx, node.table_markdown or node.text or ""))
            elif node.modality == "formula":
                text_table_pairs.append((idx, node.formula_latex or node.text or ""))
            else:
                text_table_pairs.append((idx, node.text or ""))
        if self.stage_logger:
            self.stage_logger.log_counter(
                "modality_split",
                source=input_dir,
                text_table_count=len(text_table_pairs),
                image_count=len(image_pairs),
                total_nodes=len(nodes),
            )

        if text_table_pairs:
            embed_timer = StageTimer.start_now()
            if self.stage_logger:
                self.stage_logger.log_stage_start(
                    "multimodal_text_table_embedding",
                    source=input_dir,
                    chunk_count=len(text_table_pairs),
                )
            try:
                vectors = self.embedding_provider.embed_texts([item[1] for item in text_table_pairs])
            except Exception as exc:
                if self.stage_logger:
                    self.stage_logger.log_stage_error(
                        "multimodal_text_table_embedding",
                        exc,
                        source=input_dir,
                    )
                raise IndexBuildError(f"Text/table embedding failed during multimodal index build: {exc}") from exc
            for (idx, _), vector in zip(text_table_pairs, vectors):
                vector_by_idx[idx] = vector
            if self.stage_logger:
                self.stage_logger.log_stage_end(
                    "multimodal_text_table_embedding",
                    latency_ms=embed_timer.elapsed_ms(),
                    source=input_dir,
                    chunk_count=len(text_table_pairs),
                    vector_count=len(vectors),
                )

        if image_pairs:
            extra_failures += self._embed_image_pairs(
                image_pairs=image_pairs,
                vector_by_idx=vector_by_idx,
                source=input_dir,
            )

        surviving_nodes: list[Node] = []
        surviving_vectors: list[list[float]] = []
        for idx, node in enumerate(nodes):
            vector = vector_by_idx.get(idx)
            if vector is None:
                extra_failures += 1
                if self.stage_logger:
                    self.stage_logger.log_warning(
                        "multimodal_node_drop",
                        "drop_node_without_vector",
                        source=node.metadata.source,
                        modality=node.modality,
                    )
                continue
            surviving_nodes.append(node)
            surviving_vectors.append(vector)

        if not surviving_nodes:
            raise IndexBuildError(
                f"All multimodal nodes failed to embed. failures={len(result.failures) + extra_failures}"
            )

        normalized_vectors, vector_size = self._normalize_vector_size(surviving_vectors)
        self.store.ensure_collection(vector_size=vector_size)
        upsert_timer = StageTimer.start_now()
        if self.stage_logger:
            self.stage_logger.log_stage_start(
                "multimodal_upsert",
                source=input_dir,
                vector_count=len(normalized_vectors),
            )
        upserted = self.store.upsert_nodes(
            nodes=surviving_nodes,
            vectors=normalized_vectors,
            batch_size=self.settings.ingestion_batch_size,
        )
        if self.stage_logger:
            self.stage_logger.log_stage_end(
                "multimodal_upsert",
                latency_ms=upsert_timer.elapsed_ms(),
                source=input_dir,
                upserted_count=upserted,
                vector_count=len(normalized_vectors),
            )
        self._persist_retrieval_indexes(hits_from_nodes(surviving_nodes), source=input_dir)
        doc_count = len({n.metadata.doc_id for n in surviving_nodes})
        return IndexBuildSummary(
            documents=doc_count,
            chunks=len(surviving_nodes),
            vectors=len(normalized_vectors),
            upserted=upserted,
            vector_size=vector_size,
            failed_files=len(result.failures) + extra_failures,
        )

    def _embed_image_pairs(
        self,
        image_pairs: list[tuple[int, str, str]],
        vector_by_idx: dict[int, list[float]],
        source: str,
    ) -> int:
        failed = 0
        if self.stage_logger:
            self.stage_logger.log_stage_start(
                "multimodal_image_embedding",
                source=source,
                modality="image",
                image_count=len(image_pairs),
            )
        timer = StageTimer.start_now()
        try:
            vectors = self.image_embedding_provider.embed_images([p[1] for p in image_pairs])
            for (idx, _, _), vector in zip(image_pairs, vectors):
                vector_by_idx[idx] = vector
        except Exception as exc:
            if self.stage_logger:
                self.stage_logger.log_stage_error(
                    "multimodal_image_embedding",
                    exc,
                    source=source,
                    modality="image",
                    image_count=len(image_pairs),
                )
            if not self.settings.image_embed_fallback_to_caption:
                failed += len(image_pairs)
                if self.stage_logger:
                    self.stage_logger.log_counter(
                        "image_embedding_fallback",
                        source=source,
                        modality="image",
                        image_count=len(image_pairs),
                        fallback=False,
                        error_type=type(exc).__name__,
                        error_msg=str(exc),
                    )
            else:
                if self.stage_logger:
                    self.stage_logger.log_counter(
                        "image_embedding_fallback",
                        source=source,
                        modality="image",
                        image_count=len(image_pairs),
                        fallback=True,
                        error_type=type(exc).__name__,
                        error_msg=str(exc),
                    )
                for idx, _, fallback_text in image_pairs:
                    try:
                        fallback_vector = self.embedding_provider.embed_texts([fallback_text])[0]
                        vector_by_idx[idx] = fallback_vector
                    except Exception as fallback_exc:
                        failed += 1
                        if self.stage_logger:
                            self.stage_logger.log_stage_error(
                                "image_embedding_fallback",
                                fallback_exc,
                                source=source,
                                modality="image",
                                fallback=True,
                            )
        if self.stage_logger:
            self.stage_logger.log_stage_end(
                "multimodal_image_embedding",
                latency_ms=timer.elapsed_ms(),
                source=source,
                modality="image",
                image_count=len(image_pairs),
                vector_count=sum(1 for idx, _, _ in image_pairs if idx in vector_by_idx),
            )
        return failed

    def _persist_retrieval_indexes(self, hits: list[SearchHit], source: str) -> None:
        if not self.settings.retrieval_index_persist_enabled:
            return
        try:
            build_persistent_retrieval_indexes(
                self.settings,
                self.store,
                hits=hits,
                stage_logger=self.stage_logger,
            )
        except Exception as exc:
            if self.stage_logger:
                self.stage_logger.log_warning(
                    "retrieval_index_build",
                    "retrieval_index_build_failed_continue",
                    source=source,
                    error_type=type(exc).__name__,
                    error_msg=str(exc),
                )


def build_default_chunker(settings: Settings) -> TextChunker:
    """Construct default text chunker from settings."""

    return TextChunker(
        ChunkConfig(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            chunk_min_length=settings.chunk_min_length,
        )
    )
