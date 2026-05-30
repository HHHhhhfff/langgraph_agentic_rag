from __future__ import annotations

from agentic_rag.config import Settings
from agentic_rag.schemas import Citation, SearchHit


class PromptBuilder:
    """Build final generation prompt and citation list."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def build_context(self, hits: list[SearchHit]) -> tuple[str, list[Citation]]:
        contexts: list[str] = []
        citations: list[Citation] = []
        used_chars = 0
        max_chars = max(1, self.settings.prompt_max_context_chars)

        for hit in hits:
            i = len(citations) + 1
            source = str(hit.metadata.get("source", "unknown"))
            title = str(hit.metadata.get("title", "untitled"))
            chunk_index = int(hit.metadata.get("chunk_index", -1))
            modality = str(hit.metadata.get("modality", hit.modality or "text"))
            tags = hit.metadata.get("tags", [])
            tags = tags if isinstance(tags, list) else []

            snippet = hit.text.strip()
            if modality == "image" and hit.image_path:
                snippet = snippet or f"[image] {hit.image_path}"
            if modality == "table" and hit.table_markdown:
                snippet = hit.table_markdown.strip()
            if modality == "formula" and hit.formula_latex:
                snippet = hit.formula_latex.strip()
            prefix = (
                f"[{i}] source={source}; title={title}; modality={modality}; chunk_index={chunk_index}; "
                f"score={hit.score:.4f}\n"
            )
            suffix = "\n"
            block = f"{prefix}{snippet}{suffix}"
            remaining = max_chars - used_chars
            if len(block) > remaining:
                if not self.settings.prompt_context_truncation_enabled:
                    continue
                trunc_suffix = "\n[truncated]\n"
                available = remaining - len(prefix) - len(trunc_suffix)
                min_chars = max(1, self.settings.prompt_min_chunk_chars)
                if available <= 0:
                    continue
                if available < min_chars and contexts:
                    continue
                snippet = snippet[:available].rstrip()
                if len(snippet) < min(80, min_chars) and contexts:
                    continue
                hit.metadata["prompt_context_truncated"] = True
                hit.metadata["prompt_context_chars"] = len(snippet)
                block = f"{prefix}{snippet}{trunc_suffix}"
            else:
                hit.metadata["prompt_context_truncated"] = False
                hit.metadata["prompt_context_chars"] = len(snippet)

            contexts.append(block)
            used_chars += len(block)
            citations.append(
                Citation(
                    index=i,
                    source=source,
                    title=title,
                    chunk_index=chunk_index,
                    score=float(hit.score),
                    tags=[str(x) for x in tags],
                )
            )

        return "\n".join(contexts), citations

    def build_prompt(self, question: str, context: str) -> str:
        citation_rule = "必须在结论句末使用引用标记，如 [1][2]。" if self.settings.require_citations else "尽量给出引用。"
        return (
            "你是一个严格的企业知识库问答助手。\n"
            "规则：\n"
            "1) 仅依据给定上下文回答，不得使用外部知识补全。\n"
            f"2) 若上下文不足或冲突，明确回复：{self.settings.uncertain_answer_text}\n"
            f"3) {citation_rule}\n"
            "4) 引用编号必须对应上下文分段编号。\n\n"
            f"用户问题：\n{question}\n\n"
            f"上下文：\n{context}\n\n"
            "请输出：\n"
            "- 先给出简洁答案\n"
            "- 保留引用编号\n"
        )
