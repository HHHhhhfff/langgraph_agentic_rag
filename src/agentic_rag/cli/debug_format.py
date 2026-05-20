from __future__ import annotations

from typing import Any

from agentic_rag.schemas import RAGResult


def build_query_debug_lines(result: RAGResult, settings: Any) -> list[str]:
    """Build human-readable query debug lines for normal CLI output."""

    lines = ["Debug:", *format_basic_debug(result, settings)]
    if bool(getattr(settings, "taskgraph_enabled", False)):
        lines.extend(["", "TaskGraph Debug:", *format_taskgraph_debug(result.debug or {})])
    return lines


def format_basic_debug(result: RAGResult, settings: Any) -> list[str]:
    """Format debug fields shared by legacy RAGGraph and TaskGraph."""

    return [
        f"- retrieved_count={result.retrieved_count}",
        f"- used_rerank={_format_bool(result.used_rerank)}",
        f"- fallback_used={_format_bool(result.fallback_used)}",
        f"- uncertain_answer_text={getattr(settings, 'uncertain_answer_text', None)}",
    ]


def format_taskgraph_debug(debug: dict[str, Any], *, max_items: int = 6) -> list[str]:
    """Format TaskGraph debug fields safely, even when keys are missing."""

    debug = debug or {}
    lines = [
        f"- route={debug.get('route')}",
        f"- executed_channels={_format_channels(debug.get('executed_channels'))}",
        f"- retry_count={debug.get('retry_count', 0)}",
        f"- gate_decision={debug.get('gate_decision')}",
        f"- evidence_ok={_format_bool(debug.get('evidence_ok', False))}",
        f"- support_level={debug.get('support_level', 'none')}",
        f"- support_score={_format_float(debug.get('support_score', 0.0))}",
    ]
    if bool(debug.get("support_features")):
        lines.extend(_format_support_feature_debug(debug))
    lines.extend(
        [
            f"- evidence_gaps={_format_sequence(debug.get('evidence_gaps'), max_items=max_items)}",
            f"- missing_slots={_format_sequence(debug.get('missing_slots'), max_items=max_items)}",
            f"- gate_reasons={_format_sequence(debug.get('gate_reasons'), max_items=max_items)}",
            f"- conflict_level={debug.get('conflict_level', 'none')}",
            f"- conflict_reasons={_format_sequence(debug.get('conflict_reasons'), max_items=max_items)}",
            f"- retry_actions={_format_sequence(debug.get('retry_actions'), max_items=max_items)}",
            f"- rewritten_query_text={debug.get('rewritten_query_text')}",
            f"- page_window={debug.get('page_window')}",
            f"- citation_ok={_format_bool(debug.get('citation_ok', False))}",
            f"- refusal={_format_bool(debug.get('refusal', False))}",
            f"- refusal_reason={debug.get('refusal_reason')}",
            f"- agent_route_used={_format_bool(debug.get('agent_route_used', False))}",
            f"- agent_plan_used={_format_bool(debug.get('agent_plan_used', False))}",
            f"- agent_evidence_used={_format_bool(debug.get('agent_evidence_used', False))}",
            f"- agent_retry_used={_format_bool(debug.get('agent_retry_used', False))}",
            f"- agent_gate_decision={debug.get('agent_gate_decision')}",
            f"- unsupported_claims={_format_sequence(debug.get('unsupported_claims'), max_items=max_items)}",
            f"- agent_fallback_reason={debug.get('agent_fallback_reason')}",
        ]
    )
    last_retry = _last_retry(debug.get("retry_history"))
    if last_retry:
        lines.extend(
            [
                f"- last_retry.actions={_format_sequence(last_retry.get('actions'), max_items=max_items)}",
                f"- last_retry.channels={_format_sequence(last_retry.get('channels'), max_items=max_items)}",
                f"- last_retry.top_k={_format_mapping(last_retry.get('top_k'), max_items=max_items)}",
            ]
        )
    return lines


def _format_support_feature_debug(debug: dict[str, Any]) -> list[str]:
    lines = [f"- rerank_available={_format_bool(debug.get('rerank_available', False))}"]
    for prefix, key in (
        ("support_features", "support_features"),
        ("support_feature_weights", "support_feature_weights"),
        ("support_feature_contributions", "support_feature_contributions"),
    ):
        values = debug.get(key)
        if not isinstance(values, dict):
            continue
        for name in (
            "top_hit_score",
            "avg_top_score",
            "score_consistency",
            "rerank_top_score",
            "source_diversity",
            "slot_coverage_ratio",
            "keyword_coverage",
        ):
            if name in values:
                lines.append(f"- {prefix}.{name}={_format_float(values.get(name))}")
    return lines


def _format_bool(value: Any) -> str:
    return "true" if bool(value) else "false"


def _format_float(value: Any) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "0.0000"


def _format_channels(value: Any) -> str:
    if not value:
        return "[]"
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(item) for item in value)
    return str(value)


def _format_sequence(value: Any, *, max_items: int = 6) -> str:
    if not value:
        return "[]"
    if isinstance(value, (str, bytes)):
        return f"[{value}]"
    if not isinstance(value, (list, tuple, set)):
        return f"[{value}]"
    items = [str(item) for item in list(value)[:max_items]]
    if len(value) > max_items:
        items.append(f"...(+{len(value) - max_items})")
    return "[" + ",".join(items) + "]"


def _format_mapping(value: Any, *, max_items: int = 6) -> str:
    if not value:
        return "{}"
    if not isinstance(value, dict):
        return str(value)
    items = list(value.items())
    rendered = [f"{key}:{val}" for key, val in items[:max_items]]
    if len(items) > max_items:
        rendered.append(f"...(+{len(items) - max_items})")
    return "{" + ",".join(rendered) + "}"


def _last_retry(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, list) or not value:
        return None
    last = value[-1]
    return last if isinstance(last, dict) else None
