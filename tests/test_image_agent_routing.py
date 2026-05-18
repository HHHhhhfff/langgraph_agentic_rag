from __future__ import annotations

from types import SimpleNamespace

from agentic_rag.config import Settings
from agentic_rag.generation.prompt_builder import PromptBuilder
from agentic_rag.graph.agent_planner import AgentRetrievalPlanDecision, AgentRetrievalTask
from agentic_rag.graph.plan_validator import PlanValidator
from agentic_rag.graph.task_graph import TaskGraphRAG
from agentic_rag.schemas import SearchHit


class DummyEmbedding:
    def embed_texts(self, texts):
        return [[0.1, 0.2] for _ in texts]


class DummyLLM:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.outputs:
            return self.outputs.pop(0)
        return "answer [1]"


class RecordingRetriever:
    def __init__(self):
        self.plans = []

    def retrieve(self, *, query_text, query_vector, filters=None, plan=None):
        self.plans.append(plan)
        hits = [
            SearchHit(
                point_id="img-caption",
                node_id="img:caption",
                text="图中展示准确率上升趋势",
                score=0.9,
                channel="image",
                modality="image",
                image_path="img.png",
                image_semantic_type="caption",
                metadata={"modality": "image", "image_semantic_type": "caption"},
            )
        ]
        return SimpleNamespace(hits=hits, route_hits={"image": hits}, expanded_hits=hits, executed_channels=["image"])


def test_rule_image_question_routes_to_image_channel() -> None:
    graph = TaskGraphRAG(
        settings=Settings(
            tg_min_evidence_hits=1,
            tg_min_coverage_ratio=0.0,
            tg_citation_strict=False,
        ),
        embedding_provider=DummyEmbedding(),
        retriever=RecordingRetriever(),
        llm_client=DummyLLM(["answer [1]"]),
        prompt_builder=PromptBuilder(Settings()),
    )

    result = graph.invoke("图中展示了什么趋势？")

    assert result.debug["route"] == "image_first"
    assert "image" in result.debug["executed_channels"]


def test_agent_image_route_and_plan_can_add_image_page_filters() -> None:
    llm = DummyLLM(
        [
            '{"intent":"image_qa","target_modalities":["image","page"],"need_cross_doc":false,'
            '"need_page_level":true,"route":"image_first","reasoning_summary":"figure page","confidence":0.9}',
            '{"tasks":[{"channel":"image","query_text":"第3页 图2 结论","top_k":6,'
            '"filters":{"modality":"image","image_semantic_type":"caption"},"reason":"image caption"},'
            '{"channel":"page","query_text":"第3页 图2","top_k":4,"filters":{"page":"3"},"reason":"page context"}],'
            '"page_window":2,"risk_flags":[],"reasoning_summary":"image plus page"}',
            "answer [1]",
        ]
    )
    retriever = RecordingRetriever()
    settings = Settings(
        tg_agent_route_enabled=True,
        tg_agent_retrieval_planner_enabled=True,
        tg_min_evidence_hits=1,
        tg_min_coverage_ratio=0.0,
        tg_citation_strict=False,
    )
    graph = TaskGraphRAG(
        settings=settings,
        embedding_provider=DummyEmbedding(),
        retriever=retriever,
        llm_client=llm,
        prompt_builder=PromptBuilder(settings),
    )

    result = graph.invoke("第 3 页图 2 的结论是什么？")
    plan = retriever.plans[0]

    assert result.debug["agent_route_used"] is True
    assert result.debug["agent_plan_used"] is True
    assert "image" in plan.channels()
    assert "page" in plan.channels()
    image_task = next(task for task in plan.tasks if task.channel == "image")
    assert image_task.filters["image_semantic_type"] == "caption"


def test_plan_validator_allows_image_semantic_type_filter() -> None:
    decision = AgentRetrievalPlanDecision(
        tasks=[
            AgentRetrievalTask(
                channel="image",
                query_text="caption",
                top_k=5,
                filters={"modality": "image", "image_semantic_type": "ocr"},
            )
        ],
        reasoning_summary="ocr image",
    )
    from agentic_rag.retrieval.retrieval_plan import RetrievalPlan, RetrievalTask

    rule_plan = RetrievalPlan(
        question="图中文字是什么？",
        tasks=[RetrievalTask(channel="vector", query_text="图中文字是什么？", top_k=3)],
    )
    result = PlanValidator(Settings()).merge_agent_plan(rule_plan, decision)

    assert result.used is True
    image_task = next(task for task in result.plan.tasks if task.channel == "image")
    assert image_task.filters["image_semantic_type"] == "ocr"
