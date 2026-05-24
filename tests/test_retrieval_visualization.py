from __future__ import annotations

import json
from pathlib import Path

from agentic_rag.config import Settings
from agentic_rag.evaluation.retrieval_visualization import (
    build_stage_compare,
    load_history_records,
    select_history_record,
    write_retrieval_visualization_report,
)
from agentic_rag.generation.prompt_builder import PromptBuilder
from agentic_rag.graph.task_graph import TaskGraphRAG
from agentic_rag.retrieval.rerank import RerankResult
from agentic_rag.schemas import SearchHit


def _hit(
    *,
    node_id: str,
    rank_score: float = 0.8,
    source: str = "doc.md",
    chunk_index: int = 1,
    text: str = "retrieved chunk",
) -> dict:
    return {
        "rank": 1,
        "node_id": node_id,
        "point_id": f"point-{node_id}",
        "doc_id": "doc1",
        "chunk_id": node_id,
        "chunk_index": chunk_index,
        "text": text,
        "vector": [0.1, 0.2, 0.3],
        "metadata": {
            "source": source,
            "title": "Doc",
            "page": 2,
            "modality": "text",
            "parser_name": "mineru",
            "embedding": [0.4, 0.5],
        },
        "relationships": {"next_id": "n2"},
        "scores": {
            "score": rank_score,
            "score_vector": rank_score,
            "score_bm25": None,
            "score_rrf": 0.02,
            "rerank_score": None,
        },
        "retrieval": {
            "channel": "vector",
            "vector_name": "text",
            "modality": "text",
            "source_parser": "mineru",
        },
    }


def _record(query_id: str = "q1") -> dict:
    initial_a = _hit(node_id="a", rank_score=0.9, chunk_index=1, text="initial a")
    initial_a["rank"] = 1
    initial_b = _hit(node_id="b", rank_score=0.8, chunk_index=2, text="initial b")
    initial_b["rank"] = 2
    rerank_a = _hit(node_id="a", rank_score=0.95, chunk_index=1, text="rerank a")
    rerank_a["rank"] = 2
    rerank_b = _hit(node_id="b", rank_score=0.97, chunk_index=2, text="rerank b")
    rerank_b["rank"] = 1
    final_b = _hit(node_id="b", rank_score=0.96, chunk_index=2, text="final b")
    final_b["rank"] = 1
    final_c = _hit(node_id="c", rank_score=0.7, chunk_index=3, text="final c")
    final_c["rank"] = 2
    return {
        "schema_version": "retrieval_eval.v1",
        "query_id": query_id,
        "timestamp": "2026-05-24T00:00:00+00:00",
        "query": {"text": "what is revenue", "rewritten_text": None, "filters": {}, "executed_channels": ["vector"]},
        "taskgraph": {},
        "snapshots": [
            {"stage": "initial_retrieval", "query_text": "what is revenue", "hits": [initial_a, initial_b]},
            {"stage": "rerank", "query_text": "what is revenue", "hits": [rerank_b, rerank_a]},
            {"stage": "final_after_retry", "query_text": "what is revenue", "hits": [final_b, final_c]},
        ],
    }


def test_load_and_select_history_records(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    rows = [_record("q1"), _record("q2")]
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")

    records = load_history_records(path)

    assert select_history_record(records, latest=True)["query_id"] == "q2"
    assert select_history_record(records, query_id="q1")["query_id"] == "q1"


def test_write_retrieval_visualization_report_outputs_artifacts_without_vectors(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, retrieval_vis_output_dir=str(tmp_path / "vis"))

    run_dir = write_retrieval_visualization_report(
        _record(),
        settings=settings,
        output_dir=tmp_path / "vis",
        run_id="run1",
        history_path=tmp_path / "history.jsonl",
    )

    for name in (
        "manifest.json",
        "query.json",
        "snapshots.json",
        "chunks.html",
        "stage_compare.html",
        "rank_flow.html",
        "citations.html",
        "raw_record.json",
    ):
        assert (run_dir / name).exists()
    chunks = (run_dir / "chunks.html").read_text(encoding="utf-8")
    assert "what is revenue" in chunks
    assert "initial_retrieval" in chunks
    assert "score_vector" in chunks
    assert "doc.md" in chunks
    assert "chunk_index" in chunks
    raw = json.loads((run_dir / "raw_record.json").read_text(encoding="utf-8"))
    first_hit = raw["snapshots"][0]["hits"][0]
    assert "vector" not in first_hit
    assert "embedding" not in first_hit["metadata"]


def test_stage_compare_marks_rank_changes() -> None:
    rows = {row["node_id"]: row for row in build_stage_compare(_record())}

    assert "demoted_by_rerank" in rows["a"]["status"]
    assert "promoted_by_rerank" in rows["b"]["status"]
    assert rows["c"]["status"] == "new_in_final"


class DummyEmbedding:
    def embed_texts(self, texts):
        return [[0.1, 0.2] for _ in texts]


class DummyLLM:
    def generate(self, prompt: str) -> str:
        return "answer [1]"


class DummyRetriever:
    def retrieve(self, *, query_text, query_vector, filters=None, plan=None):
        hit = SearchHit(
            point_id="p1",
            node_id="n1",
            text="TaskGraph evidence",
            score=0.8,
            score_vector=0.7,
            doc_id="doc1",
            page=1,
            channel="vector",
            modality="text",
            metadata={"source": "doc.md", "title": "Doc", "chunk_index": 1, "vector_name": "text"},
        )
        from types import SimpleNamespace

        return SimpleNamespace(
            hits=[hit],
            route_hits={"vector": [hit]},
            expanded_hits=[hit],
            executed_channels=["vector"],
        )


class DummyRerankService:
    def rerank(self, query: str, hits: list[SearchHit]) -> RerankResult:
        return RerankResult(hits=hits, used_rerank=True)


def test_task_graph_auto_writes_retrieval_visualization_without_history_log(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        retrieval_eval_log_enabled=False,
        retrieval_eval_log_dir=str(tmp_path / "history"),
        retrieval_vis_auto_write=True,
        retrieval_vis_output_dir=str(tmp_path / "vis"),
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

    run_dir = result.debug.get("retrieval_visualization_run_dir")
    assert run_dir
    assert Path(run_dir, "chunks.html").exists()
    assert not (tmp_path / "history" / "retrieval_history.jsonl").exists()
