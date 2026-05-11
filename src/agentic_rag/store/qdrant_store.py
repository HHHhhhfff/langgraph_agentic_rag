from __future__ import annotations

import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models

from agentic_rag.config import Settings
from agentic_rag.ingestion.node_schema import Node
from agentic_rag.observability.stage_logger import StageLogger, StageTimer
from agentic_rag.schemas import DocumentChunk, SearchHit


class QdrantStoreError(RuntimeError):
    """Raised for Qdrant operation errors."""


DISTANCE_MAP = {
    "cosine": models.Distance.COSINE,
    "dot": models.Distance.DOT,
    "euclid": models.Distance.EUCLID,
}


class QdrantStore:
    """Qdrant wrapper for collection management, upsert and retrieval."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.stage_logger: StageLogger | None = None
        try:
            self.client = QdrantClient(
                url=settings.qdrant_url,
                api_key=settings.qdrant_api_key or None,
                timeout=settings.qdrant_timeout_sec,
            )
        except Exception as exc:
            raise QdrantStoreError(f"Failed to initialize Qdrant client: {exc}") from exc

    def set_stage_logger(self, stage_logger: StageLogger) -> None:
        self.stage_logger = stage_logger

    def _build_filter(self, filters: dict[str, Any] | None) -> models.Filter | None:
        if not filters:
            return None
        conditions: list[models.FieldCondition] = []
        for key, value in filters.items():
            field_name = key if key.startswith("metadata.") else f"metadata.{key}"
            if value is None:
                continue
            if isinstance(value, list):
                conditions.append(
                    models.FieldCondition(
                        key=field_name,
                        match=models.MatchAny(any=[str(v) for v in value]),
                    )
                )
            else:
                conditions.append(
                    models.FieldCondition(key=field_name, match=models.MatchValue(value=str(value)))
                )
        if not conditions:
            return None
        return models.Filter(must=conditions)

    def ensure_collection(self, vector_size: int) -> None:
        """Create or validate target collection with expected vector settings."""

        name = self.settings.qdrant_collection
        distance = DISTANCE_MAP[self.settings.qdrant_distance]
        timer = StageTimer.start_now()
        if self.stage_logger:
            self.stage_logger.log_stage_start(
                "qdrant_ensure_collection",
                source=name,
                vector_size=vector_size,
                distance=self.settings.qdrant_distance,
            )

        try:
            exists = self.client.collection_exists(name)
        except Exception as exc:
            raise QdrantStoreError(f"Failed to check collection existence: {exc}") from exc

        if not exists or self.settings.qdrant_recreate_collection:
            try:
                self.client.recreate_collection(
                    collection_name=name,
                    vectors_config=models.VectorParams(size=vector_size, distance=distance),
                )
            except Exception as exc:
                if self.stage_logger:
                    self.stage_logger.log_stage_error("qdrant_ensure_collection", exc, source=name)
                raise QdrantStoreError(f"Failed to create/recreate collection {name}: {exc}") from exc
            if self.stage_logger:
                self.stage_logger.log_stage_end(
                    "qdrant_ensure_collection",
                    latency_ms=timer.elapsed_ms(),
                    source=name,
                    vector_size=vector_size,
                )
            return

        try:
            info = self.client.get_collection(name)
            vectors = info.config.params.vectors
            if isinstance(vectors, models.VectorParams):
                existing_size = vectors.size
                existing_distance = vectors.distance
            else:
                raise QdrantStoreError("Unsupported named-vector collection format in this baseline")

            if existing_size != vector_size:
                raise QdrantStoreError(
                    f"Vector size mismatch for collection {name}: existing={existing_size}, expected={vector_size}"
                )
            if existing_distance != distance:
                raise QdrantStoreError(
                    f"Distance mismatch for collection {name}: existing={existing_distance}, expected={distance}"
                )
        except QdrantStoreError:
            if self.stage_logger:
                self.stage_logger.log_stage_error(
                    "qdrant_ensure_collection",
                    QdrantStoreError("validation_failed"),
                    source=name,
                )
            raise
        except Exception as exc:
            if self.stage_logger:
                self.stage_logger.log_stage_error("qdrant_ensure_collection", exc, source=name)
            raise QdrantStoreError(f"Failed to validate collection {name}: {exc}") from exc
        if self.stage_logger:
            self.stage_logger.log_stage_end(
                "qdrant_ensure_collection",
                latency_ms=timer.elapsed_ms(),
                source=name,
                vector_size=vector_size,
            )

    def upsert_chunks(
        self,
        chunks: list[DocumentChunk],
        vectors: list[list[float]],
        batch_size: int = 64,
    ) -> int:
        """Batch upsert chunk payload and vectors."""

        if len(chunks) != len(vectors):
            raise QdrantStoreError("Chunks and vectors size mismatch")
        if not chunks:
            return 0

        total = 0
        for i in range(0, len(chunks), batch_size):
            batch_timer = StageTimer.start_now()
            chunk_batch = chunks[i : i + batch_size]
            vector_batch = vectors[i : i + batch_size]
            points: list[models.PointStruct] = []
            for chunk, vector in zip(chunk_batch, vector_batch):
                payload = {
                    "text": chunk.text,
                    "metadata": chunk.metadata,
                }
                point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, chunk.chunk_id))
                points.append(models.PointStruct(id=point_id, vector=vector, payload=payload))

            try:
                self.client.upsert(
                    collection_name=self.settings.qdrant_collection,
                    points=points,
                    wait=True,
                )
            except Exception as exc:
                if self.stage_logger:
                    self.stage_logger.log_stage_error(
                        "qdrant_upsert_chunks_batch",
                        exc,
                        source=self.settings.qdrant_collection,
                        batch_index=i,
                    )
                raise QdrantStoreError(f"Upsert failed for batch starting {i}: {exc}") from exc
            total += len(points)
            if self.stage_logger:
                self.stage_logger.log_stage_end(
                    "qdrant_upsert_chunks_batch",
                    latency_ms=batch_timer.elapsed_ms(),
                    source=self.settings.qdrant_collection,
                    upserted_count=len(points),
                    batch_index=i,
                )

        return total

    def upsert_nodes(
        self,
        nodes: list[Node],
        vectors: list[list[float]],
        batch_size: int = 64,
    ) -> int:
        """Batch upsert multimodal nodes with flattened payload fields."""

        if len(nodes) != len(vectors):
            raise QdrantStoreError("Nodes and vectors size mismatch")
        if not nodes:
            return 0

        total = 0
        for i in range(0, len(nodes), batch_size):
            batch_timer = StageTimer.start_now()
            node_batch = nodes[i : i + batch_size]
            vector_batch = vectors[i : i + batch_size]
            points: list[models.PointStruct] = []
            for node, vector in zip(node_batch, vector_batch):
                md = node.metadata
                payload = {
                    "text": node.text,
                    "image_path": node.image_path,
                    "table_markdown": node.table_markdown,
                    "relationships": node.relationships,
                    "node_id": node.node_id,
                    "modality": node.modality,
                    # flat metadata fields
                    "source": md.source,
                    "doc_id": md.doc_id,
                    "page": md.page,
                    "chunk_index": md.chunk_index,
                    "title": md.title,
                    "section": md.section,
                    "parser_name": md.parser_name,
                    # backward compatible nested metadata
                    "metadata": md.model_dump(),
                }
                point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, node.node_id))
                points.append(models.PointStruct(id=point_id, vector=vector, payload=payload))

            try:
                self.client.upsert(
                    collection_name=self.settings.qdrant_collection,
                    points=points,
                    wait=True,
                )
            except Exception as exc:
                if self.stage_logger:
                    self.stage_logger.log_stage_error(
                        "qdrant_upsert_nodes_batch",
                        exc,
                        source=self.settings.qdrant_collection,
                        batch_index=i,
                    )
                raise QdrantStoreError(f"Node upsert failed for batch starting {i}: {exc}") from exc
            total += len(points)
            if self.stage_logger:
                extra_fields = {"batch_index": i}
                if self.settings.log_include_payload_stats:
                    modality_stats: dict[str, int] = {}
                    for node in node_batch:
                        modality_stats[node.modality] = modality_stats.get(node.modality, 0) + 1
                    extra_fields["modality_stats"] = modality_stats
                self.stage_logger.log_stage_end(
                    "qdrant_upsert_nodes_batch",
                    latency_ms=batch_timer.elapsed_ms(),
                    source=self.settings.qdrant_collection,
                    upserted_count=len(points),
                    **extra_fields,
                )
        return total

    def search(
        self,
        query_vector: list[float],
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchHit]:
        """Search similar vectors with optional metadata filter."""

        qdrant_filter = self._build_filter(filters)
        timer = StageTimer.start_now()
        if self.stage_logger:
            self.stage_logger.log_stage_start(
                "qdrant_search",
                source=self.settings.qdrant_collection,
                vector_count=1,
                top_k=top_k,
                has_filter=bool(filters),
            )
        try:
            if hasattr(self.client, "query_points"):
                response = self.client.query_points(
                    collection_name=self.settings.qdrant_collection,
                    query=query_vector,
                    limit=top_k,
                    query_filter=qdrant_filter,
                    with_payload=True,
                )
                points = response.points
            else:
                points = self.client.search(
                    collection_name=self.settings.qdrant_collection,
                    query_vector=query_vector,
                    limit=top_k,
                    query_filter=qdrant_filter,
                    with_payload=True,
                )
        except Exception as exc:
            if self.stage_logger:
                self.stage_logger.log_stage_error(
                    "qdrant_search",
                    exc,
                    source=self.settings.qdrant_collection,
                )
            raise QdrantStoreError(f"Qdrant search failed: {exc}") from exc

        hits: list[SearchHit] = []
        for point in points:
            payload = point.payload or {}
            metadata = payload.get("metadata") if isinstance(payload, dict) else {}
            if not metadata and isinstance(payload, dict):
                metadata = {
                    "source": payload.get("source"),
                    "doc_id": payload.get("doc_id"),
                    "page": payload.get("page"),
                    "chunk_index": payload.get("chunk_index"),
                    "title": payload.get("title"),
                    "section": payload.get("section"),
                    "modality": payload.get("modality"),
                    "parser_name": payload.get("parser_name"),
                }
            text = payload.get("text") if isinstance(payload, dict) else ""
            hits.append(
                SearchHit(
                    point_id=str(point.id),
                    text=str(text or ""),
                    score=float(point.score or 0.0),
                    modality=str(payload.get("modality", "text")) if isinstance(payload, dict) else "text",
                    image_path=str(payload.get("image_path")) if isinstance(payload, dict) and payload.get("image_path") else None,
                    table_markdown=str(payload.get("table_markdown")) if isinstance(payload, dict) and payload.get("table_markdown") else None,
                    relationships=payload.get("relationships", {}) if isinstance(payload, dict) else {},
                    metadata=metadata if isinstance(metadata, dict) else {},
                )
            )
        if self.stage_logger:
            self.stage_logger.log_stage_end(
                "qdrant_search",
                latency_ms=timer.elapsed_ms(),
                source=self.settings.qdrant_collection,
                chunk_count=len(hits),
            )
        return hits
