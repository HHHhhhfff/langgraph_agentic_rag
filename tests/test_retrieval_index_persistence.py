from __future__ import annotations

from agentic_rag.config import Settings
from agentic_rag.retrieval.bm25_retriever import BM25Retriever
from agentic_rag.retrieval.index_persistence import (
    RetrievalIndexPaths,
    build_persistent_retrieval_indexes,
)
from agentic_rag.retrieval.page_retriever import PageRetriever
from agentic_rag.retrieval.table_retriever import TableRetriever
from agentic_rag.schemas import SearchHit


class DummyStore:
    def __init__(self, settings: Settings, hits: list[SearchHit]):
        self.settings = settings
        self.hits = hits
        self.scroll_calls = 0
        self.stage_logger = None

    def scroll_hits(self, *args, **kwargs):
        self.scroll_calls += 1
        return list(self.hits)


class FailScrollStore(DummyStore):
    def scroll_hits(self, *args, **kwargs):
        self.scroll_calls += 1
        raise AssertionError("scroll_hits should not be called")


def _settings(tmp_path, **kwargs) -> Settings:
    return Settings(
        retrieval_index_dir=str(tmp_path / "retrieval_indexes"),
        qdrant_collection="test_collection",
        **kwargs,
    )


def _hits() -> list[SearchHit]:
    return [
        SearchHit(
            point_id="1",
            node_id="n1",
            text="linux find command",
            score=0.0,
            doc_id="doc1",
            page=1,
            modality="text",
            metadata={"source": "doc1.md", "doc_id": "doc1", "page": 1, "chunk_index": 0, "modality": "text"},
        ),
        SearchHit(
            point_id="2",
            node_id="n2",
            text="linux grep command",
            score=0.0,
            doc_id="doc1",
            page=2,
            modality="text",
            metadata={"source": "doc1.md", "doc_id": "doc1", "page": 2, "chunk_index": 1, "modality": "text"},
        ),
        SearchHit(
            point_id="3",
            node_id="n3",
            text="table",
            score=0.0,
            doc_id="doc2",
            page=3,
            modality="table",
            table_markdown="| metric | value |\n| accuracy | 90 |",
            metadata={"source": "doc2.md", "doc_id": "doc2", "page": 3, "chunk_index": 0, "modality": "table"},
        ),
    ]


def test_build_persistent_retrieval_indexes_writes_all_files(tmp_path) -> None:
    settings = _settings(tmp_path)
    store = DummyStore(settings, _hits())
    paths = RetrievalIndexPaths.from_settings(settings)

    summary = build_persistent_retrieval_indexes(settings, store, hits=_hits())

    assert summary.persisted is True
    assert paths.bm25.exists()
    assert paths.page.exists()
    assert paths.table.exists()
    assert summary.bm25_docs == 3
    assert summary.page_docs == 3
    assert summary.table_docs == 1
    assert store.scroll_calls == 0


def test_bm25_retriever_loads_local_index_without_scroll(tmp_path) -> None:
    settings = _settings(tmp_path)
    build_persistent_retrieval_indexes(settings, DummyStore(settings, []), hits=_hits())
    store = FailScrollStore(settings, _hits())

    retriever = BM25Retriever(settings, store)
    hits = retriever.retrieve("find", top_k=1)

    assert hits[0].point_id == "1"
    assert store.scroll_calls == 0


def test_page_retriever_loads_page_index_with_filters(tmp_path) -> None:
    settings = _settings(tmp_path)
    build_persistent_retrieval_indexes(settings, DummyStore(settings, []), hits=_hits())
    store = FailScrollStore(settings, _hits())

    retriever = PageRetriever(store, settings=settings)
    hits = retriever.retrieve("grep", doc_id="doc1", page=2, top_k=1)

    assert hits[0].page == 2
    assert hits[0].doc_id == "doc1"
    assert hits[0].channel == "page"


def test_table_retriever_loads_only_table_index(tmp_path) -> None:
    settings = _settings(tmp_path)
    build_persistent_retrieval_indexes(settings, DummyStore(settings, []), hits=_hits())
    store = FailScrollStore(settings, _hits())

    retriever = TableRetriever(store, settings=settings)
    hits = retriever.retrieve("accuracy", top_k=3)

    assert len(hits) == 1
    assert hits[0].modality == "table"
    assert hits[0].channel == "table"


def test_missing_index_falls_back_to_scroll_and_writes_index(tmp_path) -> None:
    settings = _settings(tmp_path)
    store = DummyStore(settings, _hits())

    retriever = BM25Retriever(settings, store)
    hits = retriever.retrieve("find", top_k=1)

    assert hits[0].point_id == "1"
    assert store.scroll_calls == 1
    assert RetrievalIndexPaths.from_settings(settings).bm25.exists()


def test_corrupt_index_falls_back_to_scroll(tmp_path) -> None:
    settings = _settings(tmp_path)
    paths = RetrievalIndexPaths.from_settings(settings)
    paths.bm25.parent.mkdir(parents=True, exist_ok=True)
    paths.bm25.write_text("{bad json", encoding="utf-8")
    store = DummyStore(settings, _hits())

    retriever = BM25Retriever(settings, store)
    hits = retriever.retrieve("find", top_k=1)

    assert hits[0].point_id == "1"
    assert store.scroll_calls == 1

