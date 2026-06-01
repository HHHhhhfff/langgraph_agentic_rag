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
        self.named_vectors_enabled = settings.enable_named_vectors
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

    def _named_vector_names(self) -> tuple[str, str, str]:
        return (
            self.settings.named_vector_text_name,
            self.settings.named_vector_table_name,
            self.settings.named_vector_image_name,
        )

    def _vector_name_for_modality(self, modality: str | None) -> str:
        if modality == "table":
            return self.settings.named_vector_table_name
        if modality == "image":
            return self.settings.named_vector_image_name
        return self.settings.named_vector_text_name

    def _vector_payload(self, vector: list[float], *, modality: str | None = None) -> Any:
        if not self.named_vectors_enabled:
            return vector
        return {self._vector_name_for_modality(modality): vector}

    @staticmethod
    def _build_named_query_vector(vector_name: str, query_vector: list[float]) -> Any:
        named_vector_cls = getattr(models, "NamedVector", None)
        if named_vector_cls is not None:
            try:
                return named_vector_cls(name=vector_name, vector=query_vector)
            except Exception:
                pass
        return (vector_name, query_vector)

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

    @staticmethod
    def _point_to_search_hit(point: Any) -> SearchHit:
        payload = point.payload or {}
        raw_score = getattr(point, "score", None)
        try:
            score = float(raw_score) if raw_score is not None else 0.0
        except (TypeError, ValueError):
            score = 0.0
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
                "image_semantic_type": payload.get("image_semantic_type"),
                "parent_image_node_id": payload.get("parent_image_node_id"),
                "source_parser": payload.get("source_parser"),
                "bbox": payload.get("bbox"),
                "bbox_items": payload.get("bbox_items"),
                "bbox_coordinate_system": payload.get("bbox_coordinate_system"),
                "bbox_source": payload.get("bbox_source"),
                "bbox_merge_policy": payload.get("bbox_merge_policy"),
            }
        text = payload.get("text") if isinstance(payload, dict) else ""
        page = payload.get("page") if isinstance(payload, dict) else None
        section_path = payload.get("section_path") if isinstance(payload, dict) else []
        return SearchHit(
            point_id=str(point.id),
            node_id=str(payload.get("node_id")) if isinstance(payload, dict) and payload.get("node_id") else None,
            text=str(text or ""),
            score=score,
            doc_id=str(payload.get("doc_id")) if isinstance(payload, dict) and payload.get("doc_id") is not None else None,
            page=int(page) if isinstance(page, int) else None,
            section_path=[str(x) for x in section_path] if isinstance(section_path, list) else [],
            channel=str(payload.get("modality", "text")) if isinstance(payload, dict) else "text",
            score_vector=score,
            modality=str(payload.get("modality", "text")) if isinstance(payload, dict) else "text",
            image_path=str(payload.get("image_path")) if isinstance(payload, dict) and payload.get("image_path") else None,
            image_semantic_type=str(payload.get("image_semantic_type")) if isinstance(payload, dict) and payload.get("image_semantic_type") else None,
            parent_image_node_id=str(payload.get("parent_image_node_id")) if isinstance(payload, dict) and payload.get("parent_image_node_id") else None,
            source_parser=str(payload.get("source_parser") or payload.get("parser_name"))
            if isinstance(payload, dict) and (payload.get("source_parser") or payload.get("parser_name"))
            else None,
            confidence=float(payload.get("confidence")) if isinstance(payload, dict) and isinstance(payload.get("confidence"), (int, float)) else None,
            caption=str(payload.get("caption")) if isinstance(payload, dict) and payload.get("caption") else None,
            ocr_text=str(payload.get("ocr_text")) if isinstance(payload, dict) and payload.get("ocr_text") else None,
            object_label=str(payload.get("object_label")) if isinstance(payload, dict) and payload.get("object_label") else None,
            object_description=str(payload.get("object_description")) if isinstance(payload, dict) and payload.get("object_description") else None,
            table_markdown=str(payload.get("table_markdown")) if isinstance(payload, dict) and payload.get("table_markdown") else None,
            formula_latex=str(payload.get("formula_latex")) if isinstance(payload, dict) and payload.get("formula_latex") else None,
            relationships=payload.get("relationships", {}) if isinstance(payload, dict) else {},
            metadata=metadata if isinstance(metadata, dict) else {},
        )

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
                named_vectors_enabled=self.named_vectors_enabled,
                vector_mode="named" if self.named_vectors_enabled else "single",
            )

        try:
            exists = self.client.collection_exists(name)
        except Exception as exc:
            raise QdrantStoreError(f"Failed to check collection existence: {exc}") from exc

        if not exists or self.settings.qdrant_recreate_collection:
            try:
                vectors_config: Any
                if self.named_vectors_enabled:
                    text_name, table_name, image_name = self._named_vector_names()
                    vectors_config = {
                        text_name: models.VectorParams(size=vector_size, distance=distance),
                        table_name: models.VectorParams(size=vector_size, distance=distance),
                        image_name: models.VectorParams(size=vector_size, distance=distance),
                    }
                else:
                    vectors_config = models.VectorParams(size=vector_size, distance=distance)
                self.client.recreate_collection(collection_name=name, vectors_config=vectors_config)
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
                    named_vectors_enabled=self.named_vectors_enabled,
                    vector_mode="named" if self.named_vectors_enabled else "single",
                )
            return

        try:
            info = self.client.get_collection(name)
            vectors = info.config.params.vectors
            self._validate_vectors_config(name, vectors, vector_size, distance)
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
                named_vectors_enabled=self.named_vectors_enabled,
                vector_mode="named" if self.named_vectors_enabled else "single",
            )

    def validate_collection_compatibility(self, vector_size: int) -> None:
        """Validate existing collection vector mode/dimensions before expensive indexing work."""

        name = self.settings.qdrant_collection
        distance = DISTANCE_MAP[self.settings.qdrant_distance]
        if self.stage_logger:
            self.stage_logger.log_stage_start(
                "qdrant_preflight_collection",
                source=name,
                vector_size=vector_size,
                distance=self.settings.qdrant_distance,
                named_vectors_enabled=self.named_vectors_enabled,
                vector_mode="named" if self.named_vectors_enabled else "single",
            )
        try:
            exists = self.client.collection_exists(name)
            if not exists or self.settings.qdrant_recreate_collection:
                if self.stage_logger:
                    self.stage_logger.log_stage_end(
                        "qdrant_preflight_collection",
                        latency_ms=0,
                        source=name,
                        vector_size=vector_size,
                        skipped=True,
                        collection_exists=exists,
                        recreate=self.settings.qdrant_recreate_collection,
                    )
                return
            info = self.client.get_collection(name)
            self._validate_vectors_config(name, info.config.params.vectors, vector_size, distance)
        except QdrantStoreError:
            raise
        except Exception as exc:
            raise QdrantStoreError(f"Failed to preflight collection {name}: {exc}") from exc
        if self.stage_logger:
            self.stage_logger.log_stage_end(
                "qdrant_preflight_collection",
                latency_ms=0,
                source=name,
                vector_size=vector_size,
                named_vectors_enabled=self.named_vectors_enabled,
                vector_mode="named" if self.named_vectors_enabled else "single",
            )

    def _validate_vectors_config(
        self,
        collection_name: str,
        vectors: Any,
        vector_size: int,
        distance: models.Distance,
    ) -> None:
        if self.named_vectors_enabled:
            expected_vectors = list(self._named_vector_names())
            if isinstance(vectors, models.VectorParams):
                raise QdrantStoreError(
                    f"Vector mode mismatch for collection {collection_name}: existing=single, expected named vectors "
                    f"{expected_vectors}. This usually means the collection was created before named vectors were enabled. "
                    f"Use a new collection or set QDRANT_RECREATE_COLLECTION=true."
                )
            vector_map = dict(vectors) if hasattr(vectors, "items") else None
            if not vector_map:
                raise QdrantStoreError(
                    f"Unsupported named-vector collection format for {collection_name}; expected named vectors "
                    f"{expected_vectors}."
                )
            for vector_name in expected_vectors:
                params = vector_map.get(vector_name)
                if not isinstance(params, models.VectorParams):
                    raise QdrantStoreError(
                        f"Missing named vector '{vector_name}' in collection {collection_name}; expected named vectors "
                        f"{expected_vectors}. This collection schema cannot route table nodes to the table named vector. "
                        f"Use a new collection or set QDRANT_RECREATE_COLLECTION=true."
                    )
                self._validate_vector_params(collection_name, vector_name, params, vector_size, distance)
            return

        if isinstance(vectors, models.VectorParams):
            self._validate_vector_params(collection_name, "default", vectors, vector_size, distance)
            return
        if hasattr(vectors, "items"):
            raise QdrantStoreError(
                f"Vector mode mismatch for collection {collection_name}: existing=named vectors, expected=single vector. "
                f"Use a new collection or set QDRANT_RECREATE_COLLECTION=true."
            )
        raise QdrantStoreError("Unsupported collection vector format")

    @staticmethod
    def _validate_vector_params(
        collection_name: str,
        vector_name: str,
        params: models.VectorParams,
        vector_size: int,
        distance: models.Distance,
    ) -> None:
        if params.size != vector_size:
            raise QdrantStoreError(
                f"Vector size mismatch for collection {collection_name} vector={vector_name}: "
                f"existing={params.size}, expected={vector_size}"
            )
        if params.distance != distance:
            raise QdrantStoreError(
                f"Distance mismatch for collection {collection_name} vector={vector_name}: "
                f"existing={params.distance}, expected={distance}"
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
                md = dict(chunk.metadata or {})
                md.setdefault("modality", "text")
                payload = {
                    "text": chunk.text,
                    "node_id": chunk.chunk_id,
                    "modality": "text",
                    "source": md.get("source"),
                    "doc_id": md.get("doc_id"),
                    "page": md.get("page"),
                    "chunk_index": md.get("chunk_index"),
                    "title": md.get("title"),
                    "section": md.get("section"),
                    "parser_name": md.get("parser_name"),
                    "metadata": md,
                }
                point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, chunk.chunk_id))
                points.append(models.PointStruct(id=point_id, vector=self._vector_payload(vector, modality="text"), payload=payload))

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
                    named_vectors_enabled=self.named_vectors_enabled,
                    vector_mode="named" if self.named_vectors_enabled else "single",
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
                    "image_semantic_type": node.relationships.get("image_semantic_type"),
                    "parent_image_node_id": node.relationships.get("parent_image_node_id"),
                    "source_parser": node.relationships.get("source_parser"),
                    "confidence": node.relationships.get("confidence"),
                    "caption": node.relationships.get("caption"),
                    "ocr_text": node.relationships.get("ocr_text"),
                    "object_label": node.relationships.get("object_label"),
                    "object_description": node.relationships.get("object_description"),
                    "table_markdown": node.table_markdown,
                    "formula_latex": node.formula_latex,
                    "relationships": node.relationships,
                    "node_id": node.node_id,
                    "modality": node.modality,
                    "page": md.page,
                    "doc_id": md.doc_id,
                    "section_path": [x for x in [md.section] if x],
                    # flat metadata fields
                    "source": md.source,
                    "doc_id": md.doc_id,
                    "page": md.page,
                    "chunk_index": md.chunk_index,
                    "title": md.title,
                    "section": md.section,
                    "parser_name": md.parser_name,
                    "bbox": md.bbox,
                    "bbox_items": md.bbox_items,
                    "bbox_coordinate_system": md.bbox_coordinate_system,
                    "bbox_source": md.bbox_source,
                    "bbox_merge_policy": md.bbox_merge_policy,
                    # backward compatible nested metadata
                    "metadata": md.model_dump(),
                }
                for key in (
                    "image_semantic_type",
                    "parent_image_node_id",
                    "source_parser",
                    "confidence",
                    "caption",
                    "ocr_text",
                    "object_label",
                    "object_description",
                    "bbox",
                    "bbox_items",
                    "bbox_coordinate_system",
                    "bbox_source",
                    "bbox_merge_policy",
                ):
                    if payload.get(key) is not None:
                        payload["metadata"][key] = payload[key]
                point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, node.node_id))
                point_vector: Any = self._vector_payload(vector, modality=node.modality)
                points.append(models.PointStruct(id=point_id, vector=point_vector, payload=payload))

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
                    named_vectors_enabled=self.named_vectors_enabled,
                    vector_mode="named" if self.named_vectors_enabled else "single",
                    **extra_fields,
                )
        return total

    def search(
        self,
        query_vector: list[float],
        top_k: int,
        filters: dict[str, Any] | None = None,
        vector_name: str | None = None,
    ) -> list[SearchHit]:
        """Search similar vectors with optional metadata filter."""

        qdrant_filter = self._build_filter(filters)
        resolved_vector_name = vector_name or self.settings.named_vector_text_name
        timer = StageTimer.start_now()
        if self.stage_logger:
            self.stage_logger.log_stage_start(
                "qdrant_search",
                source=self.settings.qdrant_collection,
                vector_count=1,
                top_k=top_k,
                has_filter=bool(filters),
                vector_name=resolved_vector_name if self.named_vectors_enabled else None,
                named_vectors_enabled=self.named_vectors_enabled,
                vector_mode="named" if self.named_vectors_enabled else "single",
            )
        try:
            if hasattr(self.client, "query_points"):
                if self.named_vectors_enabled:
                    try:
                        response = self.client.query_points(
                            collection_name=self.settings.qdrant_collection,
                            query=query_vector,
                            using=resolved_vector_name,
                            limit=top_k,
                            query_filter=qdrant_filter,
                            with_payload=True,
                        )
                    except TypeError:
                        response = self.client.query_points(
                            collection_name=self.settings.qdrant_collection,
                            query=self._build_named_query_vector(resolved_vector_name, query_vector),
                            limit=top_k,
                            query_filter=qdrant_filter,
                            with_payload=True,
                        )
                else:
                    response = self.client.query_points(
                        collection_name=self.settings.qdrant_collection,
                        query=query_vector,
                        limit=top_k,
                        query_filter=qdrant_filter,
                        with_payload=True,
                    )
                points = response.points
            else:
                query_vector_payload: Any = query_vector
                if self.named_vectors_enabled:
                    query_vector_payload = self._build_named_query_vector(resolved_vector_name, query_vector)
                points = self.client.search(
                    collection_name=self.settings.qdrant_collection,
                    query_vector=query_vector_payload,
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
                    vector_name=resolved_vector_name if self.named_vectors_enabled else None,
                    named_vectors_enabled=self.named_vectors_enabled,
                    vector_mode="named" if self.named_vectors_enabled else "single",
                )
            raise QdrantStoreError(f"Qdrant search failed: {exc}") from exc

        hits: list[SearchHit] = []
        for point in points:
            hits.append(self._point_to_search_hit(point))
        if self.stage_logger:
            self.stage_logger.log_stage_end(
                "qdrant_search",
                latency_ms=timer.elapsed_ms(),
                source=self.settings.qdrant_collection,
                chunk_count=len(hits),
                vector_name=resolved_vector_name if self.named_vectors_enabled else None,
                named_vectors_enabled=self.named_vectors_enabled,
                vector_mode="named" if self.named_vectors_enabled else "single",
            )
        return hits

    def scroll_hits(
        self,
        filters: dict[str, Any] | None = None,
        limit: int = 256,
        batch_size: int = 256,
    ) -> list[SearchHit]:
        """Scroll all stored points as SearchHit objects for offline retrieval indexes."""

        qdrant_filter = self._build_filter(filters)
        offset = None
        hits: list[SearchHit] = []
        while True:
            try:
                points, offset = self.client.scroll(
                    collection_name=self.settings.qdrant_collection,
                    scroll_filter=qdrant_filter,
                    limit=batch_size,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
            except Exception as exc:
                raise QdrantStoreError(f"Qdrant scroll failed: {exc}") from exc
            for point in points:
                hits.append(self._point_to_search_hit(point))
                if len(hits) >= limit:
                    return hits
            if offset is None:
                break
        return hits
