from __future__ import annotations

from pathlib import Path

from check.pipeline import (
    _final_output_generation_skipped,
    snapshot_hits_by_stage,
    snapshot_removed_hits_by_stage,
    snapshot_visual_hits_by_stage,
)
from check.run_eval import _hit_chunk_key, _hit_region_keys, compute_stage_ranking_metrics
from check.visualize_eval import default_output_path, render_html


def test_snapshot_hits_by_stage_flattens_agent_and_source_fields() -> None:
    snapshots = [
        {
            "stage": "agent_chunk_grading",
            "hits": [
                {
                    "rank": 1,
                    "node_id": "n1",
                    "point_id": "p1",
                    "doc_id": "doc1",
                    "text": "graded chunk",
                    "metadata": {
                        "source": "data/demo_docs/doc.pdf",
                        "title": "Doc",
                        "page": 3,
                        "chunk_index": 7,
                        "modality": "formula",
                        "score_composite": 0.8,
                        "agent_relevance_score": 0.91,
                        "agent_relevance_label": "strong",
                        "agent_label_score_delta": 0.1,
                        "bbox": [100, 100, 300, 300],
                        "bbox_coordinate_system": "mineru_content_list_1000",
                    },
                    "scores": {"score": 0.8, "score_composite": 0.8},
                    "retrieval": {"channel": "formula", "modality": "formula"},
                }
            ],
        }
    ]

    stages = snapshot_hits_by_stage(snapshots)

    hit = stages["agent_chunk_grading"][0]
    assert hit["source"] == "data/demo_docs/doc.pdf"
    assert hit["doc_id"] == "doc1"
    assert hit["agent_relevance_score"] == 0.91
    assert hit["agent_relevance_label"] == "strong"
    assert hit["score_composite"] == 0.8
    assert hit["bbox"] == [100, 100, 300, 300]


def test_final_output_generation_skipped_detection_requires_empty_output_hits() -> None:
    assert _final_output_generation_skipped(
        {"final_after_retry": [{"node_id": "n1"}], "final_output": []},
        {"final_output": [{"node_id": "n1", "removed_reason": "generation_skipped"}]},
    ) is True
    assert _final_output_generation_skipped(
        {"final_output": [{"node_id": "n1"}]},
        {"final_output": [{"node_id": "n2", "removed_reason": "generation_skipped"}]},
    ) is False


def test_snapshot_removed_and_visual_hits_by_stage_include_removed_metadata() -> None:
    snapshots = [
        {
            "stage": "rerank",
            "hits": [
                {
                    "rank": 1,
                    "node_id": "kept",
                    "point_id": "p1",
                    "text": "kept chunk",
                    "metadata": {"score_composite": 0.8},
                    "scores": {"score": 0.8},
                    "retrieval": {},
                }
            ],
            "removed_hits": [
                {
                    "rank": 2,
                    "node_id": "removed",
                    "point_id": "p2",
                    "text": "removed chunk",
                    "metadata": {
                        "visual_removed": True,
                        "removed_reason": "context_top_n_limit",
                        "removed_reason_detail": "rank=2 > context_top_n=1",
                        "retrieval_removed_limit": 1,
                    },
                    "scores": {"score": 0.4},
                    "retrieval": {},
                }
            ],
        }
    ]

    removed = snapshot_removed_hits_by_stage(snapshots)
    visual = snapshot_visual_hits_by_stage(snapshots)

    assert removed["rerank"][0]["node_id"] == "removed"
    assert removed["rerank"][0]["visual_removed"] is True
    assert removed["rerank"][0]["removed_reason"] == "context_top_n_limit"
    assert [hit["node_id"] for hit in visual["rerank"]] == ["kept", "removed"]


def test_page_tolerance_marks_nearby_expected_page_relevant() -> None:
    hits = {"rerank": [{"node_id": "n1", "page": 4, "source": "doc.pdf"}]}
    specs = [{"source": "doc.pdf", "page": 5, "grade": 1.0}]

    stage_metrics, _, annotated = compute_stage_ranking_metrics(
        hits_by_stage=hits,
        specs=specs,
        k=10,
        page_tolerance=1,
    )

    assert stage_metrics["rerank"]["hit_rate"] == 1.0
    assert stage_metrics["rerank"]["page_hit_rate"] == 1.0
    assert stage_metrics["rerank"]["page_recall"] == 1.0
    assert annotated["rerank"][0]["relevance_grade"] == 1.0
    assert annotated["rerank"][0]["relevance_reason"] == "source+page_tolerance"


def test_bbox_metrics_compute_iou_precision_and_recall() -> None:
    hits = {
        "rerank": [
            {"node_id": "n1", "page": 3, "source": "doc.pdf", "bbox": [100, 100, 300, 300]},
            {"node_id": "n2", "page": 3, "source": "doc.pdf", "bbox": [700, 700, 900, 900]},
        ]
    }
    specs = [{"source": "doc.pdf", "page": 3, "grade": 1.0}]
    region_specs = [{"source": "doc.pdf", "page": 3, "bbox": [120, 120, 280, 280]}]

    stage_metrics, flat, annotated = compute_stage_ranking_metrics(
        hits_by_stage=hits,
        specs=specs,
        region_specs=region_specs,
        k=10,
        page_tolerance=0,
    )

    assert stage_metrics["rerank"]["bbox_hit_rate"] == 1.0
    assert stage_metrics["rerank"]["bbox_precision"] == 0.5
    assert stage_metrics["rerank"]["bbox_recall"] == 1.0
    assert stage_metrics["rerank"]["bbox_f1"] == 2.0 / 3.0
    assert flat["rerank_bbox_recall"] == 1.0
    assert flat["rerank_bbox_f1"] == 2.0 / 3.0
    assert annotated["rerank"][0]["bbox_match"] is True
    assert annotated["rerank"][0]["bbox_iou"] > 0.5


def test_bbox_metrics_use_matching_page_span_for_cross_page_chunk() -> None:
    hits = {
        "rerank": [
            {
                "node_id": "cross",
                "page": 1,
                "pages": [1, 2],
                "source": "doc.pdf",
                "page_spans": [
                    {"page": 1, "bbox_items": [[0, 0, 100, 100]]},
                    {"page": 2, "bbox_items": [[400, 400, 600, 600]]},
                ],
            }
        ]
    }
    specs = [{"source": "doc.pdf", "page": 2, "grade": 1.0}]
    region_specs = [{"source": "doc.pdf", "page": 2, "bbox": [410, 410, 590, 590]}]

    stage_metrics, _flat, annotated = compute_stage_ranking_metrics(
        hits_by_stage=hits,
        specs=specs,
        region_specs=region_specs,
        k=10,
        page_tolerance=0,
    )

    assert stage_metrics["rerank"]["bbox_precision"] == 1.0
    assert annotated["rerank"][0]["bbox_match"] is True
    assert annotated["rerank"][0]["bbox_iou"] > 0.5


def test_bbox_recall_uses_area_coverage_and_precision_uses_low_iou_threshold() -> None:
    hits = {
        "rerank": [
            {"node_id": "left", "page": 3, "source": "doc.pdf", "bbox": [0, 0, 50, 100]},
            {"node_id": "right", "page": 3, "source": "doc.pdf", "bbox": [50, 0, 100, 100]},
        ]
    }
    specs = [{"source": "doc.pdf", "page": 3, "grade": 1.0}]
    region_specs = [{"source": "doc.pdf", "page": 3, "bbox": [0, 0, 100, 100]}]

    stage_metrics, _flat, annotated = compute_stage_ranking_metrics(
        hits_by_stage=hits,
        specs=specs,
        region_specs=region_specs,
        k=10,
        page_tolerance=0,
        bbox_precision_iou_threshold=0.1,
    )

    assert stage_metrics["rerank"]["bbox_recall"] == 1.0
    assert stage_metrics["rerank"]["bbox_precision"] == 1.0
    assert stage_metrics["rerank"]["bbox_f1"] == 1.0
    assert annotated["rerank"][0]["bbox_match"] is False
    assert annotated["rerank"][0]["bbox_precision_match"] is True
    assert annotated["rerank"][1]["bbox_precision_match"] is True


def test_derived_bbox_labels_override_bbox_precision_and_recall() -> None:
    hit_relevant = {
        "node_id": "n1",
        "point_id": "p1",
        "page": 3,
        "source": "doc.pdf",
        "chunk_index": 1,
        "bbox": [0, 0, 50, 100],
    }
    hit_irrelevant = {
        "node_id": "n2",
        "point_id": "p2",
        "page": 3,
        "source": "doc.pdf",
        "chunk_index": 2,
        "bbox": [900, 900, 950, 950],
    }
    hit_missing = {
        "node_id": "n3",
        "point_id": "p3",
        "page": 3,
        "source": "doc.pdf",
        "chunk_index": 3,
        "bbox": [50, 0, 100, 100],
    }
    relevant_chunk_key = _hit_chunk_key(hit_relevant, 1)
    missing_chunk_key = _hit_chunk_key(hit_missing, 2)
    relevant_region_key = next(iter(_hit_region_keys(hit_relevant, 1)))
    missing_region_key = next(iter(_hit_region_keys(hit_missing, 2)))
    labels = {
        "relevant_chunk_keys": [relevant_chunk_key, missing_chunk_key],
        "relevant_region_keys": [relevant_region_key, missing_region_key],
    }

    stage_metrics, _flat, annotated = compute_stage_ranking_metrics(
        hits_by_stage={"rerank": [hit_relevant, hit_irrelevant]},
        specs=[{"source": "doc.pdf", "page": 3, "grade": 1.0}],
        region_specs=[{"source": "doc.pdf", "page": 3, "bbox": [0, 0, 100, 100]}],
        derived_bbox_labels=labels,
        bbox_relevance_mode="derived_regions",
        k=10,
        page_tolerance=0,
    )

    assert stage_metrics["rerank"]["bbox_precision"] == 0.5
    assert stage_metrics["rerank"]["bbox_recall"] == 0.5
    assert stage_metrics["rerank"]["bbox_f1"] == 0.5
    assert stage_metrics["rerank"]["bbox_region_precision"] == 0.5
    assert stage_metrics["rerank"]["bbox_region_recall"] == 0.5
    assert stage_metrics["rerank"]["bbox_area_recall"] == 0.5
    assert annotated["rerank"][0]["derived_bbox_relevant"] is True
    assert annotated["rerank"][1]["derived_bbox_relevant"] is False
    assert annotated["rerank"][0]["bbox_precision_chunk_relevant"] is True
    assert annotated["rerank"][1]["bbox_precision_chunk_relevant"] is False


def test_derived_bbox_region_metrics_dedupe_duplicate_bbox_regions() -> None:
    first = {
        "node_id": "n1",
        "point_id": "p1",
        "page": 3,
        "source": "doc.pdf",
        "chunk_index": 1,
        "bbox": [0, 0, 100, 100],
    }
    duplicate_region = {
        "node_id": "n2",
        "point_id": "p2",
        "page": 3,
        "source": "doc.pdf",
        "chunk_index": 2,
        "bbox": [0, 0, 100, 100],
    }
    labels = {
        "relevant_chunk_keys": [_hit_chunk_key(first, 1)],
        "relevant_region_keys": list(_hit_region_keys(first, 1)),
    }

    stage_metrics, _flat, _annotated = compute_stage_ranking_metrics(
        hits_by_stage={"rerank": [first, duplicate_region]},
        specs=[{"source": "doc.pdf", "page": 3, "grade": 1.0}],
        region_specs=[{"source": "doc.pdf", "page": 3, "bbox": [0, 0, 100, 100]}],
        derived_bbox_labels=labels,
        bbox_relevance_mode="derived_regions",
        k=10,
        page_tolerance=0,
    )

    assert stage_metrics["rerank"]["bbox_precision"] == 1.0
    assert stage_metrics["rerank"]["bbox_recall"] == 1.0
    assert stage_metrics["rerank"]["bbox_region_precision"] == 1.0


def test_bbox_specs_override_page_relevance_with_region_threshold() -> None:
    hits = {
        "rerank": [
            {"node_id": "green", "page": 3, "source": "doc.pdf", "bbox": [100, 100, 300, 300]},
            {"node_id": "yellow", "page": 3, "source": "doc.pdf", "bbox": [250, 250, 450, 450]},
            {"node_id": "red", "page": 3, "source": "doc.pdf", "bbox": [700, 700, 900, 900]},
            {"node_id": "wrong_page", "page": 4, "source": "doc.pdf", "bbox": [100, 100, 300, 300]},
        ]
    }
    specs = [{"source": "doc.pdf", "page": 3, "grade": 1.0}]
    region_specs = [{"source": "doc.pdf", "page": 3, "bbox": [100, 100, 300, 300]}]

    stage_metrics, _flat, annotated = compute_stage_ranking_metrics(
        hits_by_stage=hits,
        specs=specs,
        region_specs=region_specs,
        k=10,
        page_tolerance=0,
    )

    rows = annotated["rerank"]
    assert [hit["relevance_grade"] for hit in rows] == [1.0, 0.0, 0.0, 0.0]
    assert rows[0]["region_relevance_label"] == "match"
    assert rows[1]["region_relevance_label"] == "partial"
    assert rows[2]["region_relevance_label"] == "miss"
    assert rows[3]["relevance_reason"] == "not_same_source_page"
    assert stage_metrics["rerank"]["precision"] == 0.25


def test_page_level_specs_can_mark_multiple_chunks_relevant_and_hide_unknown_recall_metrics() -> None:
    hits = {
        "final_after_retry": [
            {"node_id": "n1", "page": 3, "source": "doc.pdf"},
            {"node_id": "n2", "page": 3, "source": "doc.pdf"},
            {"node_id": "n3", "page": 3, "source": "doc.pdf"},
            {"node_id": "n4", "page": 2, "source": "doc.pdf"},
        ]
    }
    specs = [{"source": "doc.pdf", "page": 3, "grade": 1.0}]

    stage_metrics, flat, annotated = compute_stage_ranking_metrics(
        hits_by_stage=hits,
        specs=specs,
        k=10,
        page_tolerance=0,
    )

    assert [hit["relevance_grade"] for hit in annotated["final_after_retry"][:4]] == [1.0, 1.0, 1.0, 0.0]
    assert stage_metrics["final_after_retry"]["precision_at_3"] == 1.0
    assert stage_metrics["final_after_retry"]["precision"] == 0.75
    assert stage_metrics["final_after_retry"]["page_hit_rate"] == 1.0
    assert stage_metrics["final_after_retry"]["page_precision"] == 0.5
    assert stage_metrics["final_after_retry"]["page_recall"] == 1.0
    assert stage_metrics["final_after_retry"]["recall"] == 1.0
    assert stage_metrics["final_after_retry"]["ap"] is None
    assert stage_metrics["final_after_retry"]["ndcg"] is None
    assert flat["final_after_retry_recall"] == 1.0


def test_exact_chunk_specs_still_match_each_expected_chunk_once() -> None:
    hits = {
        "rerank": [
            {"node_id": "n1", "page": 3, "source": "doc.pdf"},
            {"node_id": "n1", "page": 3, "source": "doc.pdf"},
            {"node_id": "n2", "page": 3, "source": "doc.pdf"},
        ]
    }
    specs = [{"node_id": "n1", "grade": 1.0}]

    stage_metrics, _, annotated = compute_stage_ranking_metrics(
        hits_by_stage=hits,
        specs=specs,
        k=10,
        page_tolerance=0,
    )

    assert [hit["relevance_grade"] for hit in annotated["rerank"]] == [1.0, 0.0, 0.0]
    assert stage_metrics["rerank"]["precision_at_3"] == 1.0 / 3.0
    assert stage_metrics["rerank"]["precision"] == 1.0 / 3.0
    assert stage_metrics["rerank"]["recall"] == 1.0


def test_page_level_recall_counts_expected_pages_not_page_chunks() -> None:
    hits = {
        "rerank": [
            {"node_id": "n1", "page": 3, "source": "doc.pdf"},
            {"node_id": "n2", "page": 3, "source": "doc.pdf"},
            {"node_id": "n3", "page": 5, "source": "doc.pdf"},
            {"node_id": "n4", "page": 9, "source": "doc.pdf"},
        ]
    }
    specs = [
        {"source": "doc.pdf", "page": 3, "grade": 1.0},
        {"source": "doc.pdf", "page": 4, "grade": 1.0},
    ]

    stage_metrics, flat, _annotated = compute_stage_ranking_metrics(
        hits_by_stage=hits,
        specs=specs,
        k=10,
        page_tolerance=0,
    )

    assert stage_metrics["rerank"]["page_recall"] == 0.5
    assert stage_metrics["rerank"]["page_precision"] == 1.0 / 3.0
    assert flat["rerank_page_recall"] == 0.5


def test_candidate_chunk_recall_dedupes_same_chunk_in_denominator() -> None:
    hits = {
        "rerank": [
            {"node_id": "n1", "page": 3, "source": "doc.pdf"},
            {"node_id": "n2", "page": 3, "source": "doc.pdf"},
            {"node_id": "n1", "page": 3, "source": "doc.pdf"},
            {"node_id": "n3", "page": 3, "source": "doc.pdf"},
        ]
    }
    specs = [{"source": "doc.pdf", "page": 3, "grade": 1.0}]

    stage_metrics, flat, _annotated = compute_stage_ranking_metrics(
        hits_by_stage=hits,
        specs=specs,
        k=2,
        page_tolerance=0,
    )

    assert stage_metrics["rerank"]["recall"] == 2.0 / 3.0
    assert flat["rerank_recall"] == 2.0 / 3.0


def test_candidate_chunk_recall_does_not_merge_same_node_with_different_stable_fields() -> None:
    hits = {
        "rerank": [
            {"node_id": "n1", "point_id": "p1", "page": 3, "chunk_index": 1, "source": "doc.pdf"},
            {"node_id": "n1", "point_id": "p2", "page": 3, "chunk_index": 2, "source": "doc.pdf"},
            {"node_id": "n1", "point_id": "p1", "page": 3, "chunk_index": 1, "source": "doc.pdf"},
        ]
    }
    specs = [{"source": "doc.pdf", "page": 3, "grade": 1.0}]

    stage_metrics, _flat, _annotated = compute_stage_ranking_metrics(
        hits_by_stage=hits,
        specs=specs,
        k=1,
        page_tolerance=0,
    )

    assert stage_metrics["rerank"]["recall"] == 0.5


def test_candidate_chunk_recall_dedupes_same_chunk_in_topk_numerator() -> None:
    hits = {
        "rerank": [
            {"node_id": "n1", "point_id": "p1", "page": 3, "chunk_index": 1, "source": "doc.pdf"},
            {"node_id": "n1", "point_id": "p1", "page": 3, "chunk_index": 1, "source": "doc.pdf"},
            {"node_id": "n2", "point_id": "p2", "page": 3, "chunk_index": 2, "source": "doc.pdf"},
        ]
    }
    specs = [{"source": "doc.pdf", "page": 3, "grade": 1.0}]

    stage_metrics, _flat, _annotated = compute_stage_ranking_metrics(
        hits_by_stage=hits,
        specs=specs,
        k=2,
        page_tolerance=0,
    )

    assert stage_metrics["rerank"]["recall"] == 0.5


def test_recall_denominator_uses_relevant_unique_chunks_seen_across_all_stages() -> None:
    hits = {
        "initial_expanded": [
            {"node_id": "n1", "page": 3, "source": "doc.pdf"},
            {"node_id": "n2", "page": 3, "source": "doc.pdf"},
            {"node_id": "n3", "page": 3, "source": "doc.pdf"},
        ],
        "final_output": [
            {"node_id": "n1", "page": 3, "source": "doc.pdf"},
        ],
    }
    specs = [{"source": "doc.pdf", "page": 3, "grade": 1.0}]

    stage_metrics, flat, _annotated = compute_stage_ranking_metrics(
        hits_by_stage=hits,
        specs=specs,
        k=10,
        page_tolerance=0,
    )

    assert stage_metrics["initial_expanded"]["recall"] == 1.0
    assert stage_metrics["final_output"]["recall"] == 1.0 / 3.0
    assert flat["final_output_recall"] == 1.0 / 3.0


def test_visual_report_renders_dynamic_agent_stage_fields(tmp_path: Path) -> None:
    html = render_html(
        {
            "name": "run1",
            "source": "check/runs/run1",
            "mode": "run",
            "runs": [{"run_name": "run1", "summary": {"case_count": 1, "error_count": 0}}],
            "cases": [
                {
                    "id": "case1",
                    "run_name": "run1",
                    "question": "q",
                    "reference_answer": "a",
                    "prediction": "a",
                    "citations": [],
                    "expected_pages": [2],
                    "metrics": {},
                    "ai_evaluation": {},
                    "model_debug": {"pipeline": "taskgraph", "agent_chunk_grading_used": True},
                    "stages": {
                        "agent_chunk_grading": [
                            {
                                "rank": 1,
                                "node_id": "n1",
                                "source": "data/demo_docs/doc.pdf",
                                "doc_id": "doc1",
                                "page": 2,
                                "score_composite": 0.8,
                                "agent_relevance_score": 0.9,
                                "agent_relevance_label": "strong",
                                "text": "chunk",
                            }
                        ]
                    },
                }
            ],
        },
        top_n=0,
        max_text_chars=100,
    )

    assert "agent_chunk_grading" in html
    assert "doc.pdf" in html
    assert "agent strong" in html
    assert "score_composite" in html or "composite" in html


def test_visual_report_marks_partial_bbox_overlap_yellow() -> None:
    html = render_html(
        {
            "name": "run1",
            "source": "check/runs/run1",
            "mode": "run",
            "runs": [{"run_name": "run1", "summary": {"case_count": 1, "error_count": 0}}],
            "cases": [
                {
                    "id": "case1",
                    "run_name": "run1",
                    "question": "q",
                    "reference_answer": "a",
                    "prediction": "a",
                    "citations": [],
                    "expected_pages": [3],
                    "bbox_specs": [{"source": "doc.pdf", "page": 3, "bbox": [100, 100, 300, 300]}],
                    "metrics": {},
                    "ai_evaluation": {},
                    "model_debug": {},
                    "stages": {
                        "rerank": [
                            {
                                "rank": 1,
                                "node_id": "partial",
                                "source": "doc.pdf",
                                "page": 3,
                                "bbox": [250, 250, 450, 450],
                                "bbox_iou": 0.032,
                                "bbox_match": False,
                                "bbox_iou_threshold": 0.5,
                                "region_relevance_label": "partial",
                                "text": "chunk",
                            }
                        ]
                    },
                }
            ],
        },
        top_n=0,
        max_text_chars=100,
    )

    assert "Partial" in html
    assert "chunk partial" in html
    assert "bbox IoU" in html


def test_visual_report_renders_explicit_removed_hit_as_gray_card() -> None:
    html = render_html(
        {
            "name": "run1",
            "source": "check/runs/run1",
            "mode": "run",
            "runs": [{"run_name": "run1", "summary": {"case_count": 1, "error_count": 0}}],
            "cases": [
                {
                    "id": "case1",
                    "run_name": "run1",
                    "question": "q",
                    "reference_answer": "a",
                    "prediction": "a",
                    "citations": [],
                    "expected_pages": [],
                    "metrics": {},
                    "ai_evaluation": {},
                    "model_debug": {},
                    "stages": {"rerank": [{"rank": 1, "node_id": "kept", "text": "kept"}]},
                    "removed_stages": {
                        "rerank": [
                            {
                                "rank": 2,
                                "node_id": "removed",
                                "text": "removed",
                                "visual_removed": True,
                                "removed_reason": "context_top_n_limit",
                                "removed_reason_detail": "rank=2 > context_top_n=1",
                            }
                        ]
                    },
                }
            ],
        },
        top_n=0,
        max_text_chars=100,
    )

    assert "chunk removed" in html
    assert "Removed: Exceeded context_top_n" in html
    assert "rank=2 &gt; context_top_n=1" in html


def test_visual_report_infers_removed_hit_when_stage_diff_loses_chunk() -> None:
    html = render_html(
        {
            "name": "run1",
            "source": "check/runs/run1",
            "mode": "run",
            "runs": [{"run_name": "run1", "summary": {"case_count": 1, "error_count": 0}}],
            "cases": [
                {
                    "id": "case1",
                    "run_name": "run1",
                    "question": "q",
                    "reference_answer": "a",
                    "prediction": "a",
                    "citations": [],
                    "expected_pages": [],
                    "metrics": {},
                    "ai_evaluation": {},
                    "model_debug": {},
                    "stages": {
                        "rerank": [
                            {
                                "rank": 1,
                                "node_id": "removed",
                                "text": "removed",
                                "agent_relevance_drop": True,
                                "agent_relevance_label": "irrelevant",
                            }
                        ],
                        "agent_chunk_grading": [],
                    },
                }
            ],
        },
        top_n=0,
        max_text_chars=100,
    )

    assert "Removed: AI marked drop" in html
    assert "visual_removed_inferred" in html


def test_visual_report_does_not_infer_final_after_retry_diff_as_context_top_n() -> None:
    html = render_html(
        {
            "name": "run1",
            "source": "check/runs/run1",
            "mode": "run",
            "runs": [{"run_name": "run1", "summary": {"case_count": 1, "error_count": 0}}],
            "cases": [
                {
                    "id": "case1",
                    "run_name": "run1",
                    "question": "q",
                    "reference_answer": "a",
                    "prediction": "a",
                    "citations": [],
                    "expected_pages": [],
                    "metrics": {},
                    "ai_evaluation": {},
                    "model_debug": {},
                    "stages": {
                        "retry_1_rerank": [
                            {"rank": 1, "node_id": "kept", "point_id": "p1", "text": "kept"},
                            {"rank": 2, "node_id": "lost", "point_id": "p2", "text": "lost"},
                        ],
                        "final_after_retry": [
                            {"rank": 1, "node_id": "kept", "point_id": "p1", "text": "kept"},
                        ],
                    },
                }
            ],
        },
        top_n=0,
        max_text_chars=100,
    )

    assert "Removed: Not selected for final candidates" in html
    assert "Removed: Exceeded context_top_n" not in html


def test_visual_report_uses_composite_hit_key_for_removed_deduplication() -> None:
    html = render_html(
        {
            "name": "run1",
            "source": "check/runs/run1",
            "mode": "run",
            "runs": [{"run_name": "run1", "summary": {"case_count": 1, "error_count": 0}}],
            "cases": [
                {
                    "id": "case1",
                    "run_name": "run1",
                    "question": "q",
                    "reference_answer": "a",
                    "prediction": "a",
                    "citations": [],
                    "expected_pages": [],
                    "metrics": {},
                    "ai_evaluation": {},
                    "model_debug": {},
                    "stages": {
                        "rerank": [
                            {"rank": 1, "node_id": "same", "point_id": "p1", "chunk_index": 1, "text": "kept"},
                        ]
                    },
                    "removed_stages": {
                        "rerank": [
                            {
                                "rank": 2,
                                "node_id": "same",
                                "point_id": "p2",
                                "chunk_index": 2,
                                "text": "removed",
                                "removed_reason": "context_top_n_limit",
                                "removed_reason_detail": "rank=2 > context_top_n=1",
                            }
                        ]
                    },
                }
            ],
        },
        top_n=0,
        max_text_chars=100,
    )

    assert "Removed: Exceeded context_top_n" in html
    assert "removed" in html


def test_visual_report_marks_pre_grading_stage_as_not_graded_yet() -> None:
    html = render_html(
        {
            "name": "run1",
            "source": "check/runs/run1",
            "mode": "run",
            "runs": [{"run_name": "run1", "summary": {"case_count": 1, "error_count": 0}}],
            "cases": [
                {
                    "id": "case1",
                    "run_name": "run1",
                    "question": "q",
                    "reference_answer": "a",
                    "prediction": "a",
                    "citations": [],
                    "expected_pages": [],
                    "metrics": {},
                    "ai_evaluation": {},
                    "model_debug": {},
                    "stages": {"retry_1_rerank": [{"rank": 1, "node_id": "n1", "text": "candidate"}]},
                }
            ],
        },
        top_n=0,
        max_text_chars=100,
    )

    assert "agent_status</b>: not_graded_yet" in html


def test_visual_report_does_not_infer_removed_into_empty_retry_stage() -> None:
    html = render_html(
        {
            "name": "run1",
            "source": "check/runs/run1",
            "mode": "run",
            "runs": [{"run_name": "run1", "summary": {"case_count": 1, "error_count": 0}}],
            "cases": [
                {
                    "id": "case1",
                    "run_name": "run1",
                    "question": "q",
                    "reference_answer": "a",
                    "prediction": "a",
                    "citations": [],
                    "expected_pages": [],
                    "metrics": {},
                    "ai_evaluation": {},
                    "model_debug": {},
                    "stages": {
                        "evidence_gate": [{"rank": 1, "node_id": "n1", "text": "kept"}],
                        "retry_1_retrieval": [],
                    },
                }
            ],
        },
        top_n=0,
        max_text_chars=100,
    )

    assert "Removed: Missing after stage transition" not in html


def test_visual_report_marks_final_output_diff_as_citation_not_selected() -> None:
    html = render_html(
        {
            "name": "run1",
            "source": "check/runs/run1",
            "mode": "run",
            "runs": [{"run_name": "run1", "summary": {"case_count": 1, "error_count": 0}}],
            "cases": [
                {
                    "id": "case1",
                    "run_name": "run1",
                    "question": "q",
                    "reference_answer": "a",
                    "prediction": "a",
                    "citations": [],
                    "expected_pages": [],
                    "metrics": {},
                    "ai_evaluation": {},
                    "model_debug": {"citation_ok": False},
                    "stages": {
                        "final_after_retry": [{"rank": 1, "node_id": "not-cited", "text": "candidate"}],
                        "final_output": [],
                    },
                }
            ],
        },
        top_n=0,
        max_text_chars=100,
    )

    assert "Removed: Not selected by final citation" in html
    assert "not referenced by final citations" in html


def test_visual_report_does_not_synthesize_local_recheck_from_final_output() -> None:
    html = render_html(
        {
            "name": "run1",
            "source": "check/runs/run1",
            "mode": "run",
            "runs": [{"run_name": "run1", "summary": {"case_count": 1, "error_count": 0}}],
            "cases": [
                {
                    "id": "case1",
                    "run_name": "run1",
                    "question": "q",
                    "reference_answer": "a",
                    "prediction": "a",
                    "citations": [],
                    "expected_pages": [],
                    "metrics": {"final_output_precision_at_3": 1.0},
                    "stage_metrics": {"final_output": {"precision_at_3": 1.0}},
                    "ai_evaluation": {},
                    "model_debug": {},
                    "stages": {"final_output": [{"rank": 1, "node_id": "n1", "text": "final"}]},
                }
            ],
        },
        top_n=0,
        max_text_chars=100,
    )

    assert "Final output" in html
    assert "Local recheck" not in html


def test_visual_report_renders_page_level_metrics() -> None:
    html = render_html(
        {
            "name": "run1",
            "source": "check/runs/run1",
            "mode": "run",
            "runs": [{"run_name": "run1", "summary": {"case_count": 1, "error_count": 0}}],
            "cases": [
                {
                    "id": "case1",
                    "run_name": "run1",
                    "question": "q",
                    "reference_answer": "a",
                    "prediction": "a",
                    "citations": [],
                    "expected_pages": [3],
                    "metrics": {
                        "final_output_page_recall": 1.0,
                        "final_output_page_precision": 0.5,
                    },
                    "stage_metrics": {
                        "final_output": {
                            "page_recall": 1.0,
                            "page_precision": 0.5,
                        }
                    },
                    "ai_evaluation": {},
                    "model_debug": {},
                    "stages": {"final_output": [{"rank": 1, "node_id": "n1", "page": 3, "text": "hit"}]},
                }
            ],
        },
        top_n=0,
        max_text_chars=100,
    )

    assert "page_recall" in html
    assert "final_output_page_recall" in html


def test_visual_report_overview_renders_average_recall_metrics() -> None:
    html = render_html(
        {
            "name": "run1",
            "source": "check/runs/run1",
            "mode": "run",
            "runs": [
                {
                    "run_name": "run1",
                    "summary": {
                        "case_count": 2,
                        "error_count": 0,
                        "mean_metrics": {"final_output_recall": 0.75},
                    },
                }
            ],
            "cases": [
                {
                    "id": "case1",
                    "run_name": "run1",
                    "question": "q1",
                    "reference_answer": "a",
                    "prediction": "a",
                    "citations": [],
                    "expected_pages": [],
                    "metrics": {
                        "rerank_recall": 0.5,
                        "final_after_retry_recall": 0.5,
                        "final_output_recall": 0.5,
                        "final_output_page_mrr": 1.0,
                        "final_output_bbox_precision": 0.5,
                        "final_output_bbox_recall": 0.5,
                        "final_output_bbox_f1": 0.5,
                    },
                    "ai_evaluation": {},
                    "model_debug": {},
                    "stages": {},
                },
                {
                    "id": "case2",
                    "run_name": "run1",
                    "question": "q2",
                    "reference_answer": "a",
                    "prediction": "a",
                    "citations": [],
                    "expected_pages": [],
                    "metrics": {
                        "rerank_recall": 1.0,
                        "final_after_retry_recall": 1.0,
                        "final_output_recall": 1.0,
                        "final_output_page_mrr": 0.5,
                        "final_output_bbox_precision": 1.0,
                        "final_output_bbox_recall": 1.0,
                        "final_output_bbox_f1": 1.0,
                    },
                    "ai_evaluation": {},
                    "model_debug": {},
                    "stages": {},
                },
            ],
        },
        top_n=0,
        max_text_chars=100,
    )

    assert "rerank_recall" in html
    assert "final_after_retry_recall" in html
    assert "final_output_recall" in html
    assert "<strong>0.7500</strong>" in html
    assert "final recall" in html
    assert "final_output_page_mrr" in html
    assert "final_output_bbox_f1" in html
    assert "final bbox MAP" in html
    assert "final bbox MAR" in html
    assert "final bbox F1" in html


def test_visual_report_default_output_path_uses_run_name_without_extra_timestamp(tmp_path: Path, monkeypatch) -> None:
    import check.visualize_eval as visualize_eval

    monkeypatch.setattr(visualize_eval, "DEFAULT_REPORT_DIR", tmp_path)

    first = default_output_path("20260606_192958")
    first.write_text("existing", encoding="utf-8")
    second = default_output_path("20260606_192958")

    assert first.name == "20260606_192958.html"
    assert second.name.startswith("20260606_192958_")
    assert second.name.endswith(".html")
