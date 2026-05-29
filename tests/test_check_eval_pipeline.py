from __future__ import annotations

from pathlib import Path

from check.pipeline import snapshot_hits_by_stage
from check.run_eval import compute_stage_ranking_metrics
from check.visualize_eval import render_html


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
    assert annotated["rerank"][0]["relevance_grade"] == 1.0
    assert annotated["rerank"][0]["relevance_reason"] == "source+page_tolerance"


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
