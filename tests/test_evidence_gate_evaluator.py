from __future__ import annotations

from agentic_rag.config import Settings
from agentic_rag.retrieval.evidence_gate import EvidenceEvaluator
from agentic_rag.retrieval.evidence_pack import EvidencePack
from agentic_rag.retrieval.retrieval_plan import RetrievalPlan
from agentic_rag.schemas import SearchHit


def _hit(
    text: str,
    *,
    point_id: str = "p1",
    doc_id: str = "d1",
    page: int | None = 1,
    modality: str = "text",
    channel: str = "vector",
    table_markdown: str | None = None,
) -> SearchHit:
    return SearchHit(
        point_id=point_id,
        node_id=point_id,
        text=text,
        score=0.9,
        doc_id=doc_id,
        page=page,
        modality=modality,
        channel=channel,
        table_markdown=table_markdown,
        metadata={"source": f"{doc_id}.md", "doc_id": doc_id, "page": page, "modality": modality, "chunk_index": 0},
    )


def test_evidence_pack_new_fields_serializable() -> None:
    pack = EvidencePack(
        plan=RetrievalPlan(question="q"),
        support_level="strong",
        support_score=0.9,
        slot_coverage={"keyword": True},
        required_slots=["hits", "keyword"],
        covered_slots=["hits", "keyword"],
        conflict_reasons=[],
        gate_decision="pass",
        gate_reasons=[],
    )

    loaded = EvidencePack.model_validate(pack.model_dump())

    assert loaded.support_level == "strong"
    assert loaded.support_score == 0.9
    assert loaded.gate_decision == "pass"


def test_extract_query_slots_detects_page_numeric_modality_and_source() -> None:
    evaluator = EvidenceEvaluator(Settings())

    slots = evaluator.extract_query_slots(
        "\u7b2c 3 \u9875\u8868\u683c\u4e2d 2024 \u5e74\u589e\u957f\u7387\u662f\u591a\u5c11\uff1f",
        ["table"],
        {"source": "report.pdf"},
    )

    assert slots.page_refs == [3]
    assert "2024" in slots.numeric_refs
    assert "report.pdf" in slots.source_refs
    assert "table" in slots.modality_requirements
    assert "page" in slots.required_slots
    assert "numeric" in slots.required_slots
    assert "modality:table" in slots.required_slots


def test_no_hits_retries_and_is_not_supported() -> None:
    evaluator = EvidenceEvaluator(Settings(tg_min_evidence_hits=1, tg_min_coverage_ratio=0.0))

    pack = evaluator.evaluate("taskgraph", [], {}, ["text"])

    assert pack.gate_decision == "retry"
    assert pack.claim_supported is False
    assert "missing_hits" in pack.gate_reasons


def test_sufficient_keyword_evidence_passes() -> None:
    evaluator = EvidenceEvaluator(Settings(tg_min_evidence_hits=1, tg_min_coverage_ratio=0.5))

    pack = evaluator.evaluate("taskgraph agent", [_hit("TaskGraph agent evidence")], {}, ["text"])

    assert pack.gate_decision == "pass"
    assert pack.claim_supported is True
    assert pack.support_level in {"partial", "strong"}


def test_table_question_requires_table_modality() -> None:
    evaluator = EvidenceEvaluator(Settings(tg_min_evidence_hits=1, tg_min_coverage_ratio=0.0, tg_required_slot_strict=True))

    pack = evaluator.evaluate("\u8868\u683c\u4e2d\u51c6\u786e\u7387\u662f\u591a\u5c11\uff1f", [_hit("accuracy is 90")], {}, ["table"])

    assert pack.gate_decision == "retry"
    assert "modality:table" in pack.missing_slots
    assert pack.slot_coverage["modality:table"] is False


def test_page_question_requires_target_page() -> None:
    evaluator = EvidenceEvaluator(Settings(tg_min_evidence_hits=1, tg_min_coverage_ratio=0.0, tg_required_slot_strict=True))

    pack = evaluator.evaluate("\u7b2c 3 \u9875\u4e3b\u8981\u8bb2\u4e86\u4ec0\u4e48\uff1f", [_hit("page two", page=2)], {}, ["page"])

    assert pack.gate_decision == "retry"
    assert "page" in pack.missing_slots


def test_numeric_question_requires_numeric_evidence() -> None:
    evaluator = EvidenceEvaluator(Settings(tg_min_evidence_hits=1, tg_min_coverage_ratio=0.0, tg_required_slot_strict=True))

    pack = evaluator.evaluate("accuracy 90%", [_hit("accuracy is high")], {}, ["text"])

    assert pack.gate_decision == "retry"
    assert "numeric" in pack.missing_slots


def test_positive_negative_conflict_can_refuse() -> None:
    evaluator = EvidenceEvaluator(Settings(tg_min_evidence_hits=1, tg_min_coverage_ratio=0.0, tg_allow_refusal=True))

    pack = evaluator.evaluate("is it supported", [_hit("This is not true, yes it is")], {}, ["text"])

    assert pack.conflict_level == "high"
    assert pack.gate_decision in {"pass", "refuse"}
    assert "same_hit_positive_negative" in pack.conflict_reasons


def test_intro_question_matches_compact_keyword_and_ignores_incidental_conflict() -> None:
    evaluator = EvidenceEvaluator(Settings(tg_min_evidence_hits=2, tg_min_coverage_ratio=0.5))
    hits = [
        _hit(
            "Task Graph 是一种把问题拆成检索、验证和回答步骤的工作流。2024 版示例没有开放式工具调用。",
            point_id="p1",
            doc_id="d1",
        ),
        _hit(
            "Task Graph 支持按证据门和引用校验组织流程，包含 3 个阶段。",
            point_id="p2",
            doc_id="d2",
        ),
    ]

    pack = evaluator.evaluate("简单介绍下TaskGraph", hits, {}, ["text"])

    assert pack.gate_decision == "pass"
    assert pack.conflict_level == "none"
    assert "keyword" not in pack.missing_slots
    assert "numeric_value_conflict" not in pack.conflict_reasons
    assert "same_hit_positive_negative" not in pack.conflict_reasons


def test_numeric_conflict_only_applies_to_numeric_questions() -> None:
    evaluator = EvidenceEvaluator(Settings(tg_min_evidence_hits=2, tg_min_coverage_ratio=0.0))
    hits = [
        _hit("TaskGraph 在 2023 年用于检索流程说明。", point_id="p1", doc_id="d1"),
        _hit("TaskGraph 在 2024 年用于证据校验说明。", point_id="p2", doc_id="d2"),
    ]

    generic_pack = evaluator.evaluate("简单介绍下TaskGraph", hits, {}, ["text"])
    numeric_pack = evaluator.evaluate("TaskGraph 2024 年准确率是多少？", hits, {}, ["text"])

    assert "numeric_value_conflict" not in generic_pack.conflict_reasons
    assert numeric_pack.conflict_level == "medium"
    assert "numeric_value_conflict" in numeric_pack.conflict_reasons


def test_keyword_coverage_is_soft_when_threshold_is_zero() -> None:
    evaluator = EvidenceEvaluator(Settings(tg_min_evidence_hits=1, tg_min_coverage_ratio=0.0))

    pack = evaluator.evaluate("简单介绍下TaskGraph", [_hit("Task Graph 支持证据门与引用校验")], {}, ["text"])

    assert pack.gate_decision == "pass"
    assert "low_keyword_coverage" not in pack.gate_reasons


def test_support_score_weights_are_configurable() -> None:
    settings = Settings(
        tg_min_evidence_hits=1,
        tg_min_coverage_ratio=0.0,
        tg_support_w_top_hit=0.5,
        tg_support_w_avg_top=0.1,
        tg_support_w_bm25_vector=0.1,
        tg_support_w_rerank=0.1,
        tg_support_w_source_diversity=0.1,
        tg_support_w_slot_coverage=0.05,
        tg_support_w_keyword=0.05,
    )
    evaluator = EvidenceEvaluator(settings)

    pack = evaluator.evaluate("TaskGraph", [_hit("TaskGraph evidence", point_id="p1")], {"vector": [_hit("TaskGraph evidence", point_id="p1")]}, ["text"])

    assert 0.0 <= pack.support_score <= 1.0
    assert isinstance(pack.support_score, float)
