from __future__ import annotations

import re

from agentic_rag.config import Settings
from agentic_rag.ingestion.node_schema import Node


def split_oversized_text_nodes(nodes: list[Node], settings: Settings) -> list[Node]:
    """Split only oversized text nodes and preserve relationship compatibility."""

    if not settings.chunk_structural_split_enabled:
        return nodes
    max_chars = max(1, settings.chunk_hard_max_chars)
    result: list[Node] = []
    split_map: dict[str, list[str]] = {}
    for node in nodes:
        if node.modality != "text" or not node.text or len(node.text) <= max_chars:
            result.append(node)
            continue
        parts = _split_text(
            node.text,
            max_chars=max_chars,
            min_part_chars=max(1, settings.chunk_structural_split_min_part_chars),
            overlap=max(0, settings.chunk_structural_split_overlap),
        )
        if len(parts) <= 1:
            result.append(node)
            continue
        part_ids: list[str] = []
        for index, text in enumerate(parts):
            part_id = f"{node.node_id}:part:{index}"
            part_ids.append(part_id)
            relationships = dict(node.relationships)
            relationships["parent_id"] = node.node_id
            relationships["chunk_part_index"] = index
            relationships["chunk_part_count"] = len(parts)
            relationships["chunk_original_node_id"] = node.node_id
            relationships["chunk_original_length"] = len(node.text)
            if index > 0:
                relationships["prev_id"] = part_ids[index - 1]
                relationships["doc_prev_node_id"] = part_ids[index - 1]
            if index < len(parts) - 1:
                relationships["next_id"] = f"{node.node_id}:part:{index + 1}"
                relationships["doc_next_node_id"] = f"{node.node_id}:part:{index + 1}"
            result.append(
                node.model_copy(
                    deep=True,
                    update={
                        "node_id": part_id,
                        "text": text,
                        "relationships": relationships,
                    },
                )
            )
        split_map[node.node_id] = part_ids
    if not split_map:
        return result
    return [_rewrite_relationship_refs(node, split_map) for node in result]


def _split_text(text: str, *, max_chars: int, min_part_chars: int, overlap: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            boundary = _find_boundary(text, start=start, end=end, min_part_chars=min_part_chars)
            if boundary > start:
                end = boundary
        piece = text[start:end].strip()
        if piece:
            parts.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return parts


def _find_boundary(text: str, *, start: int, end: int, min_part_chars: int) -> int:
    window = text[start:end]
    lower_bound = max(min_part_chars, int(len(window) * 0.55))
    boundary_matches = list(re.finditer(r"(\n\n+|(?<=[.!?。！？])\s+)", window))
    for match in reversed(boundary_matches):
        if match.end() >= lower_bound:
            return start + match.end()
    newline = window.rfind("\n")
    if newline >= lower_bound:
        return start + newline + 1
    space = window.rfind(" ")
    if space >= lower_bound:
        return start + space + 1
    return end


def _rewrite_relationship_refs(node: Node, split_map: dict[str, list[str]]) -> Node:
    relationships = dict(node.relationships)
    changed = False
    for key, value in list(relationships.items()):
        if key in {"parent_id", "chunk_original_node_id"}:
            continue
        if isinstance(value, str) and value in split_map:
            relationships[key] = split_map[value][0]
            changed = True
        elif isinstance(value, list):
            rewritten: list[object] = []
            for item in value:
                if isinstance(item, str) and item in split_map:
                    rewritten.extend(split_map[item])
                    changed = True
                else:
                    rewritten.append(item)
            relationships[key] = rewritten
    if not changed:
        return node
    return node.model_copy(deep=True, update={"relationships": relationships})
