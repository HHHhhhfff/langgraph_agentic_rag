# check 评测模块

`check/` 是独立评测目录，不改动 `src/` 主流程。它可以做两类评测：

- 离线评测：直接评测数据集里的 `prediction` / `answer` 字段。
- 端到端评测：调用当前项目的 RAG 查询链路，生成答案后再计算指标。
- 索引计时：通过独立 wrapper 调用现有建索引 CLI，记录索引构建时间。

## 数据集格式

支持 `.jsonl` 或 `.json`。每条样例至少需要：

```json
{"id":"case_001","question":"问题","reference_answer":"参考答案"}
```

可选字段：

- `prediction` 或 `answer`：离线评测时使用。
- `reference_keywords`：关键词召回率。
- `expected_sources` / `relevant_sources`：期望命中文档源，会和 citation / ranked hit 的 `source/title` 匹配。
- `relevant_ids`：期望命中的 `point_id` / `node_id` / `doc_id`。
- `relevant_docs`：更细粒度的相关文档列表，可包含 `point_id`、`node_id`、`doc_id`、`source`、`title`、`chunk_index`、`page`、`grade`。
- `ranked_hits`：离线评测排名指标时使用；端到端评测会自动从检索链路取得。
- `response_time_ms`、`token_usage`、`index_build_time_ms`：离线评测已有实验结果时可直接写入。
- `metadata_filter` 或 `filters`：传给 RAG 检索的过滤条件。

## 常用命令

离线评测示例，不调用模型和 Qdrant：

```powershell
py -3.11 -m check --dataset check/example_cases.jsonl --use-existing-answers --k 3
```

如果本机 `py -3.11` 不可用，可以使用项目虚拟环境：

```powershell
.\.venv\Scripts\python.exe -m check --dataset check/example_cases.jsonl --use-existing-answers --k 3
```

端到端评测，调用当前 RAG 链路：

```powershell
py -3.11 -m check --dataset check/example_cases.jsonl --k 10 --rank-source reranked
```

默认 `--pipeline simple` 使用简化评测链路：

```text
embedding -> retrieve -> rerank -> prompt -> generate
```

如果要评测完整 TaskGraph 链路，使用：

```powershell
py -3.11 -m check --dataset check/sciqa_2412_16030_cases.jsonl --pipeline taskgraph --limit 5 --k 10 --page-tolerance 1
```

新增参数：

- `--pipeline simple|taskgraph`
  - `simple`：快速简化链路。
  - `taskgraph`：完整 TaskGraph 链路，会采集 `agent_chunk_grading`、`evidence_gate`、`retry_*`、`final_after_retry`、`final_output` 等阶段快照。
- `--stage-metrics <stage1,stage2,...>`
  - 控制 `metrics_report` 输出哪些阶段指标。
- `--use-expected-source-filter`
  - 将 case 中第一个 `expected_source/expected_sources` 作为评测过滤条件。只建议单文档 benchmark 使用。
- `--page-tolerance N`
  - 页码评测容忍度。例如期望页为 5，`--page-tolerance 1` 时 page 4/5/6 都算命中。
  - 只影响评测指标，不影响检索和回答生成。
- `--limit N`
  - 只评测前 N 条 case。注意 `--k` 是 Top-K 指标 cutoff，不是样本数量。


```powershell
py -3.11 check/visualize_eval.py --run-dir check/runs/<run-name> --removed-mode both
```

- `--show-removed` / `--hide-removed`
  - 是否展示被剔除 chunk。默认展示。
- `--removed-mode explicit|inferred|both`
  - `explicit`：只展示 TaskGraph snapshot 中显式记录的 `removed_hits`。
  - `inferred`：只通过相邻阶段 diff 推断“上一阶段有、下一阶段消失”的 chunk。
  - `both`：优先使用显式 `removed_hits`，再用 diff 推断兜底；默认值。
  - ghost card 会标灰并放在当前阶段底部，展示 `removed_reason`、`removed_reason_detail`、上一阶段 rank、阈值或 top-k 限制等信息。


端到端评测会同时输出三组阶段指标：

- `initial_recall_*`：初步召回 / RRF 融合后的指标。
- `rerank_*`：rerank 后进入上下文候选的指标。
- `local_recheck_*`：局部重检 / 邻域扩展后的指标。

启用 LLM-as-judge 额外指标：

```powershell
py -3.11 -m check --dataset check/example_cases.jsonl --ai-judge
```

索引构建计时：

```powershell
.\.venv\Scripts\python.exe -m check.benchmark_index --docs data/demo_docs
```

输出会写入：

```text
check/runs/<run-name>/results.jsonl
check/runs/<run-name>/summary.json
check/runs/<run-name>/chunk_ids_by_case.jsonl
check/runs/<run-name>/run_metadata.json
check/runs/<run-name>/metrics_report.json
check/runs/<run-name>/metrics_report.md
check/query_records/<run-name>_query_stage_chunks.json
```

其中 `chunk_ids_by_case.jsonl` 会按样本记录三阶段 Top N 命中的
`node_id`、`point_id`、`chunk_index`、`page`、`modality`、`score` 和 `relevance_grade`。
默认记录 Top 10，可通过 `--chunk-log-top-n` 调整。

`run_metadata.json` 会保存本次评测参数、非敏感模型/检索/切分配置快照，以及 chunk-id 统计，便于复现实验。

`check/query_records/<run-name>_query_stage_chunks.json` 是专门的 query 追踪记录文件。它会按每条 query 保存：

- query / 标准答案 / 实际回答
- AI 评分和指标
- 初步召回 `initial_recall` 的 chunk 列表
- rerank 后 `rerank` 的 chunk 列表
- 最终用于生成输出 `final_output` 的 chunk 列表

每个 chunk 会记录 `node_id`、`point_id`、`chunk_index`、`page`、`modality`、`score`、`relevance_grade` 和 chunk 文本。默认保存每个阶段全部 chunk，可通过 `--query-record-top-n` 只保存 Top N。

`metrics_report.json` / `metrics_report.md` 会按标准指标输出：

- Hit Rate（命中率）
- MRR（Mean Reciprocal Rank）
- Precision（精确率，含 Precision@1、Precision@3、Precision@k）
- Recall（召回率）
- AP（Average Precision）
- nDCG（Normalized Discounted Cumulative Gain）
- AI 评分（correctness、completeness、relevance、overall、score_100）
- 索引构建时间以及响应时间
- token 消耗数

如果要把索引构建耗时合入报告，可以传入索引计时结果：

```powershell
.\.venv\Scripts\python.exe -m check --dataset check/example_cases.jsonl --index-summary check/runs/<index-run>/index_summary.json
```

## 已内置指标

- `hit_rate`
- `mrr`
- `precision_at_1`
- `precision_at_3`
- `precision`（当前阶段实际返回结果中的相关占比，即 relevant / returned；不是 `--k` 分母）
- `recall`（当前候选集内去重 `Chunk Recall@k`）
- `ap`
- `ndcg`
- `page_hit_rate`
- `page_mrr`
- `page_precision`
- `page_recall`

说明：

- `precision_at_1` / `precision_at_3` 是单 query 的 top-k 指标。
- `precision` 是单 query、单阶段的实际返回精确率，分母是当前阶段参与评测的返回 chunk 数。
- `recall` 当前定义为链路内去重 `Chunk Recall@k`：当前阶段 Top-k 中相关唯一 chunk 数 / 从初召回到最终所有记录阶段中出现过的相关唯一 chunk 数。
- `recall` 的分母只来自本次 query 已记录阶段的候选并集，不代表全库召回率；去重使用可用稳定字段组成的复合 key：`node_id`、`point_id`、`source/doc_id/title + page + chunk_index`，避免只按 `node_id` 合并同一 node 下的不同 chunk。
- 当标准答案只标到 `source/page/title` 级别时，`ap` / `ndcg` 没有可靠的理想排序，报告中显示为 `-`。
- 只有当标准答案提供 `node_id` / `point_id` / `chunk_index` 这类精确 chunk 级标注时，`ap` / `ndcg` 才会计算。
- `page_recall` 是页级召回率：命中的标准页数 / 标准页总数。它不把标准页内所有 chunk 都当作相关 chunk。
- `page_precision` 是页级精确率：命中的返回页数 / 返回页总数，按页去重计算。
- `exact_match`
- `token_precision`
- `token_recall`
- `token_f1`
- `keyword_recall`
- `expected_source_recall`
- `has_citation`
- `citation_count`
- `retrieved_count`
- `latency_ms`
- `response_time_ms`
- `index_build_time_ms`
- `prompt_tokens_est`
- `answer_tokens_est`
- `total_tokens_est`
- `prompt_tokens`
- `completion_tokens`
- `total_tokens`

`--judge` 会额外输出：

- `ai_correctness`
- `ai_completeness`
- `ai_relevance`
- `ai_overall`
- `ai_score_100`
- `ai_judge_prompt_tokens_est`
- `ai_judge_answer_tokens_est`
- `ai_judge_total_tokens_est`

每条样本的 `ai_evaluation` 字段会保存 AI 给出的评分理由 `ai_reason`。

### BBox / Region Metrics

When cases contain SciEGQA `rel_bbox` and retrieved chunks contain `metadata.bbox`, the check pipeline also reports:

- `bbox_hit_rate`: whether at least one expected region is hit by IoU.
- `bbox_precision`: matched unique returned chunks / comparable returned chunks with bbox.
- `bbox_recall`: expected regions covered by returned chunks / expected regions.
- `bbox_max_iou`: max IoU between returned chunk bbox and expected bbox.

The implementation uses 0-1000 normalized page coordinates. Existing indexes must be rebuilt after MinerU bbox metadata is added; old runs without chunk bbox will show these metrics as `-`.

For chunks that cover multiple MinerU regions, `bbox_items` stores each region bbox and `bbox` stores the union. Region metrics compare against `bbox_items` first, then fall back to `bbox`. If structural splitting creates `part` nodes from a parent text node with multiple bbox regions, the part nodes do not inherit the parent union bbox to avoid inflated IoU.

When a case provides bbox labels, relevance coloring and retrieval metrics use region-level matching first: same file + same page + IoU > 0.5 is green/relevant; 0 < IoU <= 0.5 is yellow/partial and is not counted as relevant; IoU = 0 or a different file/page is red. Cases without bbox labels keep the old source/page matching behavior.
