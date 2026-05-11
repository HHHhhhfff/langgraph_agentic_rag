---
title: LangGraph Agentic RAG 基线说明
tags:
  - langgraph
  - rag
  - qdrant
owner: platform-team
---

# 项目目标

这个 Demo 用于演示：

1. Markdown 文档入库（切块、Embedding、Qdrant upsert）
2. 查询时可按 metadata 过滤
3. 检索结果可 rerank，失败可自动回退
4. 回答必须附带引用 [1][2]

# 检索策略

当前采用向量检索为主，支持：

- `source` 过滤
- `tags` 过滤
- 过滤结果为空时可自动回退到无过滤检索

后续可扩展混合检索，把关键词召回接入到统一检索接口。

# 回答策略

回答必须仅依据上下文。如果上下文不充分，返回统一文案：

无法根据已检索到的资料确定答案。
