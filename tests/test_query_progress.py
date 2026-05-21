from __future__ import annotations

from io import StringIO

from agentic_rag.cli.query_progress import QueryProgress
from agentic_rag.config import Settings
from agentic_rag.generation.prompt_builder import PromptBuilder
from agentic_rag.graph.task_graph import TaskGraphRAG
from tests.test_task_graph_flow import DummyEmbedding, DummyLLM, DummyRetriever, RetryAwareRetriever


def test_query_progress_start_done_outputs_stage_text() -> None:
    stream = StringIO()
    progress = QueryProgress(stream=stream)

    progress.start("question_analyze")
    progress.done("question_analyze")

    text = stream.getvalue()
    assert "Query progress:" in text
    assert "[1/6] 问题分析 ... running" in text
    assert "[1/6] 问题分析 ... done" in text


def test_query_progress_skip_and_retry_outputs_status() -> None:
    stream = StringIO()
    progress = QueryProgress(stream=stream)

    progress.skip("local_retry")
    progress.retry("local_retry", 1)

    text = stream.getvalue()
    assert "[5/6] 局部重检 ... skipped" in text
    assert "[5/6] 局部重检 ... retry 1" not in text


def test_query_progress_retry_outputs_retry_count() -> None:
    stream = StringIO()
    progress = QueryProgress(stream=stream)

    progress.retry("local_retry", 1)

    assert "[5/6] 局部重检 ... retry 1" in stream.getvalue()


def test_task_graph_progress_normal_query_contains_six_stages() -> None:
    stream = StringIO()
    progress = QueryProgress(stream=stream)
    settings = Settings(
        rerank_enabled=False,
        tg_max_retries=1,
        tg_min_evidence_hits=1,
        tg_min_coverage_ratio=0.0,
        tg_citation_strict=False,
    )
    graph = TaskGraphRAG(
        settings=settings,
        embedding_provider=DummyEmbedding(),
        retriever=DummyRetriever(),
        llm_client=DummyLLM(),
        prompt_builder=PromptBuilder(settings),
        progress=progress,
    )

    graph.invoke("TaskGraph")

    text = stream.getvalue()
    assert "[1/6] 问题分析 ... done" in text
    assert "[2/6] 任务路由 ... done" in text
    assert "[3/6] 检索 ... done" in text
    assert "[4/6] 证据校验 ... done" in text
    assert "[5/6] 局部重检 ... skipped" in text
    assert "[6/6] 内容生成 ... done" in text


def test_task_graph_progress_retry_query_shows_retry() -> None:
    stream = StringIO()
    progress = QueryProgress(stream=stream)
    settings = Settings(
        rerank_enabled=False,
        tg_max_retries=1,
        tg_min_evidence_hits=1,
        tg_min_coverage_ratio=0.0,
        tg_citation_strict=False,
    )
    graph = TaskGraphRAG(
        settings=settings,
        embedding_provider=DummyEmbedding(),
        retriever=RetryAwareRetriever(),
        llm_client=DummyLLM(),
        prompt_builder=PromptBuilder(settings),
        progress=progress,
    )

    graph.invoke("retry question")

    assert "[5/6] 局部重检 ... retry 1" in stream.getvalue()

