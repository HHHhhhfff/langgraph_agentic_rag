from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from agentic_rag.config import Settings
from agentic_rag.evaluation.retrieval_history import RetrievalHistoryRecorder, build_snapshot, serialize_hit
from agentic_rag.generation.prompt_builder import PromptBuilder
from agentic_rag.graph.task_graph import TaskGraphRAG
from agentic_rag.retrieval.rerank import RerankResult
from agentic_rag.schemas import SearchHit


def _hit(point_id: str = "p1", text: str = "TaskGraph evidence") -> SearchHit:
    return SearchHit(
        point_id=point_id,
        node_id=f"n-{point_id}",
        text=text,
        score=0.8,
        score_vector=0.7,
        score_rrf=0.02,
        doc_id="doc1",
        page=2,
        channel="vector",
        modality="text",
        metadata={
            "source": "doc.md",
            "title": "Doc",
            "chunk_index": 3,
            "vector_name": "text",
            "parser_name": "llamaindex:sentence",
        },
        relationships={"parent_node_id": "parent"},
    )


def test_serialize_hit_outputs_llamaindex_like_fields() -> None:
    hit = _hit()
    hit.metadata["rerank_score"] = 0.91

    row = serialize_hit(hit, rank=1, max_text_chars=5)

    assert row["rank"] == 1
    assert row["node_id"] == "n-p1"
    assert row["chunk_index"] == 3
    assert row["text"] == "TaskG"
    assert row["metadata"]["source"] == "doc.md"
    assert row["relationships"]["parent_node_id"] == "parent"
    assert row["scores"]["rerank_score"] == 0.91
    assert row["retrieval"]["vector_name"] == "text"
    assert row["retrieval"]["source_parser"] == "llamaindex:sentence"
    assert row["metadata"]["parser_name"] == "llamaindex:sentence"
    assert row["eval"]["is_relevant"] is None


def test_recorder_append_jsonl_does_not_overwrite(tmp_path: Path) -> None:
    settings = Settings(
        retrieval_eval_log_enabled=True,
        retrieval_eval_log_dir=str(tmp_path),
        retrieval_eval_log_file="history.jsonl",
    )
    recorder = RetrievalHistoryRecorder(settings)

    recorder.append({"query_id": "1"})
    recorder.append({"query_id": "2"})

    rows = [json.loads(line) for line in (tmp_path / "history.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["query_id"] for row in rows] == ["1", "2"]


def test_recorder_disabled_does_not_write(tmp_path: Path) -> None:
    settings = Settings(
        retrieval_eval_log_enabled=False,
        retrieval_eval_log_dir=str(tmp_path),
        retrieval_eval_log_file="history.jsonl",
    )

    RetrievalHistoryRecorder(settings).append({"query_id": "1"})

    assert not (tmp_path / "history.jsonl").exists()


class DummyEmbedding:
    def embed_texts(self, texts):
        return [[0.1, 0.2] for _ in texts]


class DummyLLM:
    def generate(self, prompt: str) -> str:
        return "answer [1]"


class DummyRetriever:
    def retrieve(self, *, query_text, query_vector, filters=None, plan=None):
        hit = _hit()
        return SimpleNamespace(
            hits=[hit],
            route_hits={"vector": [hit], "bm25": []},
            expanded_hits=[hit],
            executed_channels=["vector", "bm25"],
        )


class RetryRetriever:
    def __init__(self):
        self.calls = 0

    def retrieve(self, *, query_text, query_vector, filters=None, plan=None):
        self.calls += 1
        hit = _hit(point_id=f"p{self.calls}", text=f"evidence {self.calls}")
        score = 0.2 if self.calls == 1 else 0.9
        hit.score = score
        hit.score_vector = score
        return SimpleNamespace(
            hits=[hit],
            route_hits={"vector": [hit], "bm25": []},
            expanded_hits=[hit],
            executed_channels=["vector"],
        )


class DummyRerankService:
    def rerank(self, query: str, hits: list[SearchHit]) -> RerankResult:
        for hit in hits:
            hit.metadata["rerank_score"] = 0.9
            hit.score = 0.9
        return RerankResult(hits=hits, used_rerank=True)


def test_task_graph_writes_retrieval_history_record(tmp_path: Path) -> None:
    settings = Settings(
        retrieval_eval_log_enabled=True,
        retrieval_eval_log_dir=str(tmp_path),
        retrieval_eval_log_file="history.jsonl",
        tg_max_retries=1,
        tg_min_evidence_hits=1,
        tg_min_coverage_ratio=0.0,
        tg_min_support_score=0.1,
        tg_citation_strict=False,
        tg_agent_evidence_critic_enabled=False,
        tg_agent_retry_advisor_enabled=False,
    )
    graph = TaskGraphRAG(
        settings=settings,
        embedding_provider=DummyEmbedding(),
        retriever=DummyRetriever(),
        rerank_service=DummyRerankService(),
        llm_client=DummyLLM(),
        prompt_builder=PromptBuilder(settings),
    )

    result = graph.invoke("TaskGraph")

    assert result.debug.get("retrieval_eval_log_error") is None
    rows = (tmp_path / "history.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    record = json.loads(rows[0])
    assert record["schema_version"] == "retrieval_eval.v1"
    assert record["query"]["text"] == "TaskGraph"
    assert [row["stage"] for row in record["snapshots"]] == ["initial_retrieval", "rerank", "final_after_retry"]
    assert record["snapshots"][0]["hits"][0]["node_id"] == "n-p1"
    assert record["labels"]["relevant_node_ids"] == []


def test_task_graph_final_snapshot_uses_retry_hits(tmp_path: Path) -> None:
    settings = Settings(
        retrieval_eval_log_enabled=True,
        retrieval_eval_log_dir=str(tmp_path),
        retrieval_eval_log_file="history.jsonl",
        rerank_enabled=False,
        tg_max_retries=2,
        tg_min_evidence_hits=1,
        tg_min_coverage_ratio=0.0,
        tg_min_support_score=0.8,
        tg_citation_strict=False,
        tg_agent_evidence_critic_enabled=False,
        tg_agent_retry_advisor_enabled=False,
    )
    retriever = RetryRetriever()
    graph = TaskGraphRAG(
        settings=settings,
        embedding_provider=DummyEmbedding(),
        retriever=retriever,
        rerank_service=None,
        llm_client=DummyLLM(),
        prompt_builder=PromptBuilder(settings),
    )

    graph.invoke("TaskGraph")

    record = json.loads((tmp_path / "history.jsonl").read_text(encoding="utf-8").splitlines()[0])
    snapshots = {row["stage"]: row for row in record["snapshots"]}
    assert retriever.calls >= 2
    assert snapshots["initial_retrieval"]["hits"][0]["point_id"] == "p1"
    assert snapshots["final_after_retry"]["hits"][0]["point_id"] == "p2"


def test_retrieval_history_records_final_snapshot_when_local_retry_disabled(tmp_path: Path) -> None:
    settings = Settings(
        retrieval_eval_log_enabled=True,
        retrieval_eval_log_dir=str(tmp_path),
        retrieval_eval_log_file="history.jsonl",
        taskgraph_local_retry_enabled=False,
        rerank_enabled=False,
        tg_max_retries=1,
        tg_min_evidence_hits=2,
        tg_min_coverage_ratio=0.0,
        tg_citation_strict=False,
    )
    retriever = RetryRetriever()
    graph = TaskGraphRAG(
        settings=settings,
        embedding_provider=DummyEmbedding(),
        retriever=retriever,
        rerank_service=None,
        llm_client=DummyLLM(),
        prompt_builder=PromptBuilder(settings),
    )

    result = graph.invoke("TaskGraph")

    record = json.loads((tmp_path / "history.jsonl").read_text(encoding="utf-8").splitlines()[0])
    snapshots = {row["stage"]: row for row in record["snapshots"]}
    assert result.debug["local_retry_skipped"] is True
    assert retriever.calls == 1
    assert "final_after_retry" in snapshots
    assert snapshots["final_after_retry"]["hits"][0]["point_id"] == "p1"
    assert record["taskgraph"]["local_retry_enabled"] is False
    assert record["taskgraph"]["local_retry_skipped"] is True


def test_clear_retrieval_history_script(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    path.write_text('{"x":1}\n', encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "scripts/clear_retrieval_history.py", "--path", str(path)],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        text=True,
        capture_output=True,
    )

    assert path.read_text(encoding="utf-8") == ""
    assert "Cleared retrieval history" in completed.stdout


def test_build_snapshot_contains_serialized_hits() -> None:
    snapshot = build_snapshot(
        stage="initial_retrieval",
        query_text="q",
        hits=[_hit()],
        max_text_chars=2000,
    )

    assert snapshot["stage"] == "initial_retrieval"
    assert snapshot["top_k"] == 1
    assert snapshot["hits"][0]["eval"]["graded_relevance"] is None
