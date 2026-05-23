from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
import uuid

from agentic_rag.config import Settings
from agentic_rag.ingestion.node_schema import Node
from agentic_rag.observability.stage_logger import StageLogger, StageTimer
from agentic_rag.retrieval.bm25_index import BM25Index
from agentic_rag.schemas import DocumentChunk, SearchHit
from agentic_rag.store.qdrant_store import QdrantStore


IndexKind = Literal["bm25", "page", "table"]


@dataclass(slots=True)
class RetrievalIndexPaths:
    root: Path
    bm25: Path
    page: Path
    table: Path

    @classmethod
    def from_settings(cls, settings: Settings) -> "RetrievalIndexPaths":
        collection = settings.qdrant_collection.replace("/", "_").replace("\\", "_")
        root = Path(settings.retrieval_index_dir) / collection
        return cls(
            root=root,
            bm25=root / "bm25.json",
            page=root / "page.json",
            table=root / "table.json",
        )

    def path_for(self, kind: IndexKind) -> Path:
        return getattr(self, kind)


@dataclass(slots=True)
class RetrievalIndexBuildSummary:
    bm25_docs: int
    page_docs: int
    table_docs: int
    root: Path
    persisted: bool


def hits_from_chunks(chunks: list[DocumentChunk]) -> list[SearchHit]:
    hits: list[SearchHit] = []
    for chunk in chunks:
        md = dict(chunk.metadata or {})
        hits.append(
            SearchHit(
                point_id=str(uuid.uuid5(uuid.NAMESPACE_URL, chunk.chunk_id)),
                node_id=chunk.chunk_id,
                text=chunk.text,
                score=0.0,
                doc_id=str(md.get("doc_id")) if md.get("doc_id") is not None else None,
                page=int(md["page"]) if isinstance(md.get("page"), int) else None,
                section_path=[str(md["section"])] if md.get("section") else [],
                channel="bm25",
                modality=str(md.get("modality", "text")),
                metadata=md,
            )
        )
    return hits


def hits_from_nodes(nodes: list[Node]) -> list[SearchHit]:
    hits: list[SearchHit] = []
    for node in nodes:
        md = node.metadata.model_dump()
        md.setdefault("source_parser", node.metadata.parser_name)
        md.update(
            {
                key: value
                for key, value in (node.relationships or {}).items()
                if key
                in {
                    "image_semantic_type",
                    "parent_image_node_id",
                    "source_parser",
                    "confidence",
                    "caption",
                    "ocr_text",
                    "object_label",
                    "object_description",
                }
            }
        )
        text = node.text or ""
        if node.modality == "table" and node.table_markdown:
            text = node.table_markdown
        if node.modality == "formula" and node.formula_latex:
            text = node.formula_latex
        hits.append(
            SearchHit(
                point_id=str(uuid.uuid5(uuid.NAMESPACE_URL, node.node_id)),
                node_id=node.node_id,
                text=text,
                score=0.0,
                doc_id=node.metadata.doc_id,
                page=node.metadata.page,
                section_path=[node.metadata.section] if node.metadata.section else [],
                channel="bm25",
                modality=node.modality,
                image_path=node.image_path,
                image_semantic_type=str(md.get("image_semantic_type")) if md.get("image_semantic_type") else None,
                parent_image_node_id=str(md.get("parent_image_node_id")) if md.get("parent_image_node_id") else None,
                source_parser=str(md.get("source_parser")) if md.get("source_parser") else None,
                confidence=float(md["confidence"]) if isinstance(md.get("confidence"), (int, float)) else None,
                caption=str(md.get("caption")) if md.get("caption") else None,
                ocr_text=str(md.get("ocr_text")) if md.get("ocr_text") else None,
                object_label=str(md.get("object_label")) if md.get("object_label") else None,
                object_description=str(md.get("object_description")) if md.get("object_description") else None,
                table_markdown=node.table_markdown,
                formula_latex=node.formula_latex,
                relationships=node.relationships,
                metadata=md,
            )
        )
    return hits


def build_page_hits(hits: list[SearchHit]) -> list[SearchHit]:
    grouped: dict[tuple[str | None, int | None], list[SearchHit]] = defaultdict(list)
    for hit in hits:
        grouped[(hit.doc_id, hit.page)].append(hit)

    page_hits: list[SearchHit] = []
    for (_, _), group in grouped.items():
        if not group:
            continue
        text = "\n".join(_hit_text(hit) for hit in group if _hit_text(hit)).strip()
        base = group[0].model_copy(deep=True)
        base.text = text
        base.channel = "page"
        base.score = max((h.score for h in group), default=0.0)
        page_hits.append(base)
    return page_hits


def build_table_hits(hits: list[SearchHit]) -> list[SearchHit]:
    return [hit for hit in hits if hit.modality == "table" or hit.metadata.get("modality") == "table"]


def build_persistent_retrieval_indexes(
    settings: Settings,
    store: QdrantStore,
    hits: list[SearchHit] | None = None,
    stage_logger: StageLogger | None = None,
) -> RetrievalIndexBuildSummary:
    paths = RetrievalIndexPaths.from_settings(settings)
    if not settings.retrieval_index_persist_enabled:
        source_hits = hits or []
        page_hits = build_page_hits(source_hits)
        table_hits = build_table_hits(source_hits)
        return RetrievalIndexBuildSummary(
            bm25_docs=len(source_hits),
            page_docs=len(page_hits),
            table_docs=len(table_hits),
            root=paths.root,
            persisted=False,
        )

    timer = StageTimer.start_now()
    if stage_logger:
        stage_logger.log_stage_start("retrieval_index_build", source=str(paths.root))
    source_hits = hits
    if source_hits is None:
        if stage_logger:
            stage_logger.log_counter(
                "retrieval_index_fallback_scroll",
                source=settings.qdrant_collection,
                fallback=True,
            )
        source_hits = store.scroll_hits(limit=10000)

    page_hits = build_page_hits(source_hits)
    table_hits = build_table_hits(source_hits)
    indexes: dict[IndexKind, BM25Index] = {
        "bm25": BM25Index.build(source_hits),
        "page": BM25Index.build(page_hits),
        "table": BM25Index.build(table_hits),
    }
    for kind, index in indexes.items():
        _save_index(index=index, kind=kind, path=paths.path_for(kind), stage_logger=stage_logger)

    if stage_logger:
        stage_logger.log_stage_end(
            "retrieval_index_build",
            latency_ms=timer.elapsed_ms(),
            source=str(paths.root),
            doc_count=len(source_hits),
            bm25_docs=len(source_hits),
            page_docs=len(page_hits),
            table_docs=len(table_hits),
        )
    return RetrievalIndexBuildSummary(
        bm25_docs=len(source_hits),
        page_docs=len(page_hits),
        table_docs=len(table_hits),
        root=paths.root,
        persisted=True,
    )


def load_or_build_bm25_index(
    settings: Settings,
    store: QdrantStore,
    kind: IndexKind,
    stage_logger: StageLogger | None = None,
) -> BM25Index:
    paths = RetrievalIndexPaths.from_settings(settings)
    path = paths.path_for(kind)
    if settings.retrieval_index_persist_enabled and path.exists():
        timer = StageTimer.start_now()
        try:
            index = BM25Index.load(path)
        except Exception as exc:
            if not settings.retrieval_index_fallback_to_scroll:
                raise
            if stage_logger:
                stage_logger.log_warning(
                    "retrieval_index_load",
                    "retrieval_index_load_failed_fallback_scroll",
                    source=str(path),
                    index_kind=kind,
                    index_path=str(path),
                    fallback=True,
                    error_type=type(exc).__name__,
                    error_msg=str(exc),
                )
        else:
            if stage_logger:
                stage_logger.log_stage_end(
                    "retrieval_index_load",
                    latency_ms=timer.elapsed_ms(),
                    source=str(path),
                    index_kind=kind,
                    index_path=str(path),
                    doc_count=len(index.docs),
                )
            return index

    if not settings.retrieval_index_fallback_to_scroll:
        raise FileNotFoundError(f"Retrieval index not found: {path}")

    if stage_logger:
        stage_logger.log_counter(
            "retrieval_index_fallback_scroll",
            source=str(path),
            index_kind=kind,
            index_path=str(path),
            fallback=True,
        )
    source_hits = store.scroll_hits(limit=10000)
    docs = _docs_for_kind(kind, source_hits)
    index = BM25Index.build(docs)
    if settings.retrieval_index_persist_enabled:
        try:
            _save_index(index=index, kind=kind, path=path, stage_logger=stage_logger)
        except Exception as exc:
            if stage_logger:
                stage_logger.log_warning(
                    "retrieval_index_save",
                    "retrieval_index_save_failed",
                    source=str(path),
                    index_kind=kind,
                    index_path=str(path),
                    error_type=type(exc).__name__,
                    error_msg=str(exc),
                )
    return index


def _docs_for_kind(kind: IndexKind, hits: list[SearchHit]) -> list[SearchHit]:
    if kind == "bm25":
        return hits
    if kind == "page":
        return build_page_hits(hits)
    return build_table_hits(hits)


def _hit_text(hit: SearchHit) -> str:
    if hit.modality == "table" and hit.table_markdown:
        return hit.table_markdown
    if hit.modality == "formula" and hit.formula_latex:
        return hit.formula_latex
    return hit.text or ""


def _save_index(
    *,
    index: BM25Index,
    kind: IndexKind,
    path: Path,
    stage_logger: StageLogger | None,
) -> None:
    timer = StageTimer.start_now()
    if stage_logger:
        stage_logger.log_stage_start(
            "retrieval_index_save",
            source=str(path),
            index_kind=kind,
            index_path=str(path),
            doc_count=len(index.docs),
        )
    index.save(path)
    if stage_logger:
        stage_logger.log_stage_end(
            "retrieval_index_save",
            latency_ms=timer.elapsed_ms(),
            source=str(path),
            index_kind=kind,
            index_path=str(path),
            doc_count=len(index.docs),
        )
