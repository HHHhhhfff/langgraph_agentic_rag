# LangGraph Agentic RAG（增量多模态版）

本项目在原有 `legacy` 文本 RAG 基线上，增量加入了多模态 ingestion 能力（文字/图片/表格），并保持原 CLI 与查询链路可用。

## 1. 能力概览

- 保持原有能力：
  - `python -m agentic_rag.cli.build_index --docs ...`
  - `python -m agentic_rag.cli.query "..."`
  - LangGraph 查询图、Qdrant 检索、Rerank 失败回退
- 新增能力：
  - `INGESTION_ENGINE=legacy|multimodal` 双引擎
  - Unstructured 多模态解析（HTML/Word/PDF回退）
  - LlamaParse 作为 PDF 主解析器（失败自动回退 Unstructured）
  - MinerU 云端多模态解析（precise / agent，可配置回退）
  - LlamaIndex 切块策略切换：Sentence / Markdown / Hierarchical
  - 统一多模态 Node Schema 与 Qdrant payload 映射

---

## 2. 环境准备

- Python 3.11+
- 本地可访问 Qdrant：`http://localhost:6333`
- OpenAI-Compatible API（Embedding / Rerank / LLM）
  - 本项目当前已验证百炼/DashScope OpenAI 兼容接口。
- 若启用 LlamaParse：准备 `LLAMA_CLOUD_API_KEY`

### 安装

```powershell
cd <project-root>
py -3.11 -m venv .venv
.\.venv\Scripts\activate
py -3.11 -m pip install -U pip
py -3.11 -m pip install -r requirements.txt
py -3.11 -m pip install openai
py -3.11 -m pip install -e .
```

> 注意：如果本机曾经用 `pip install -e` 安装过另一个同名目录，务必在当前项目根目录重新执行
> `py -3.11 -m pip install -e .`，否则 `python -m agentic_rag...` 可能会导入旧项目。

### 多模态解析依赖安装（Unstructured）

如需稳定解析 PDF / Word / Excel / PPT / 图片 / HTML，建议安装：

```powershell
pip install "unstructured[all-docs]"
pip install unstructured-inference
```

该安装会覆盖常见解析依赖（包含但不限于）：

- `pdfminer.six`（PDF）
- `python-docx`（Word）
- `openpyxl`（Excel）
- `python-pptx`（PPT）
- `pillow`（图片）
- `lxml / bs4`（HTML）

已在 `requirements.txt` 中使用 `unstructured[all-docs]>=0.15.0` 进行统一声明。
同时显式声明了 `unstructured-inference`，用于避免回退到 Unstructured 时出现
`No module named 'unstructured_inference'`。

---

## 3. 配置说明（`.env`）

先复制：

```powershell
Copy-Item .env.example .env
```

### 3.1 关键开关

- `INGESTION_ENGINE=legacy|multimodal`
  - `legacy`：沿用旧文本管线
  - `multimodal`：启用多模态 ingestion
- `MULTIMODAL_ENABLED=true|false`
  - 全局多模态开关（建议和 `INGESTION_ENGINE` 配套）

### 3.2 多模态相关配置（新增）

- `PDF_PARSER=llamaparse|unstructured`
  - PDF 主解析器
- `TEXT_CHUNK_PARSER=sentence|markdown|hierarchical`
  - 文本切块策略
- `LLAMA_CLOUD_API_KEY=`
  - LlamaParse 密钥（可留空；留空时建议配回退）
- `LLAMAPARSE_FALLBACK_TO_UNSTRUCTURED=true|false`
  - LlamaParse 失败时回退开关
- `UNSTRUCTURED_STRATEGY=fast|hi_res|auto`
  - Unstructured 解析策略
- `ENABLE_MINERU=true|false`
  - 是否启用 MinerU 作为文档解析后端（默认 false）
- `MINERU_MODE=precise|agent`
  - `precise`：需要 token，支持高精度与复杂文档
  - `agent`：免 token 轻量接口，有限额
- `MINERU_BASE_URL`
  - MinerU 服务地址，默认 `https://mineru.net`
- `MINERU_API_TOKEN`
  - MinerU 精准模式 token（`MINERU_MODE=precise` 时必填）
- `MINERU_MODEL_VERSION=vlm|pipeline|MinerU-HTML`
  - MinerU 模型版本，默认 `vlm`
- `MINERU_POLL_INTERVAL_SEC`
  - 轮询间隔（秒）
- `MINERU_POLL_TIMEOUT_SEC`
  - 轮询超时（秒）
- `MINERU_POLL_WAIT_FOREVER=true|false`
  - 是否忽略 `MINERU_POLL_TIMEOUT_SEC`，一直轮询到 MinerU 返回 `done/failed`
- `MINERU_DOWNLOAD_WAIT_FOREVER=true|false`
  - 是否在 MinerU 结果 ZIP/Markdown 下载超时时持续重试，直到下载成功
- `MINERU_DOWNLOAD_MAX_RETRIES`
  - `MINERU_DOWNLOAD_WAIT_FOREVER=false` 时的结果下载最大重试次数
- `MINERU_DOWNLOAD_RETRY_INTERVAL_SEC`
  - MinerU 结果下载失败后的重试间隔（秒）
- `MINERU_ENABLE_TABLE=true|false`
  - 是否开启表格解析
- `MINERU_ENABLE_FORMULA=true|false`
  - 是否开启公式解析
- `MINERU_TABLE_STRUCTURED_FIRST=true|false`
  - 是否优先使用 MinerU `structured_content` 中的结构化表格。
- `MINERU_TABLE_MARKDOWN_FALLBACK=true|false`
  - 是否用 Markdown 表格作为结构化表格未覆盖时的 fallback。
- `MINERU_TABLE_SECOND_PASS_ENABLED=false`
  - 是否启用旧的 `clean_markdown` 二次表格扫描。默认关闭，避免同一 PDF 表格被多路径重复 chunk。
- `MINERU_STRUCTURED_TABLE_COALESCE_ENABLED=true|false`
  - 是否归并 MinerU `structured_content` 内部由多份 JSON 来源重复描述的同一物理表格。只在 MinerU 单次解析结果内部生效，不做全局表格去重。
- `MINERU_STRUCTURED_TABLE_COALESCE_KEEP_CHART=true|false`
  - structured table 归并时是否保留 chart/table-like block，避免把图表结构化结果误删。
- `MINERU_TABLE_EXACT_CONTENT_COALESCE_ENABLED=true|false`
  - 是否对最终 table Node 做内容级归并。仅当同一文档内表格 cell matrix 完全相同时合并，并在保留 Node 的 relationships 中记录 `merged_table_node_ids/merged_table_occurrences`。
- `MINERU_TABLE_EXACT_CONTENT_COALESCE_KEEP_CHART_SEPARATE=true|false`
  - 内容级归并时是否让 chart/table-like 表格保持独立。默认 false，表示完全相同内容只保留一个 table Node，但保留 occurrence 信息。
- `ENABLE_FORMULA_RECOGNITION=true|false`
  - 是否从解析后的 Markdown/文本中抽取 LaTeX/MathML 公式节点
- `FORMULA_NODE_MIN_CHARS=8`
  - 独立 formula Node 的最小公式长度。
- `FORMULA_INLINE_AS_TEXT_ONLY=true`
  - 是否让行内短公式保留在 text Node 中，不单独生成 formula Node。
- `FORMULA_SKIP_INLINE_REFERENCES=true`
  - 是否跳过 `$[12]$`、`$[3–8]$` 这类引用型公式候选。
- `FORMULA_SKIP_SUPERSCRIPT_NOTES=true`
  - 是否跳过 `$^{1,2,*}$`、`$^{3,\dagger}$` 这类作者脚注/上标。
- `FORMULA_GROUP_DISPLAY_ENABLED=true`
  - 是否合并连续 display formula，减少公式推导链被拆碎。
- `FORMULA_GROUP_MAX_GAP_LINES=2`
  - 连续 display formula 分组允许的最大空行数。
- `MINERU_IS_OCR=true|false`
  - 是否开启 OCR
- `MINERU_LANGUAGE`
  - 指定语言，默认 `ch`
- `MINERU_PAGE_RANGE`
  - 页码范围（可选）
- `MINERU_EXTRA_FORMATS`
  - 精准模式额外导出格式（如 `docx,html`）
- `MINERU_FALLBACK_TO_EXISTING=true|false`
  - MinerU 失败后是否回退现有解析链路（LlamaParse/Unstructured）
- `MINERU_CONTEXT_LINK_ENABLED=true|false`
  - 是否为 MinerU 生成的 Node 补充真实上下文关系，包括 `prev_id/next_id`、同页关系、表格/公式与前后文本关系。
- `MINERU_CONTEXT_WINDOW_CHARS=800`
  - 写入表格/公式上下文关系时记录的上下文窗口大小。
- `MINERU_SAME_PAGE_LINK_MAX_NODES=30`
  - 每个 Node 的 `same_page_node_ids` 最多保存多少个同页 Node ID。
- `ENABLE_IMAGE_CAPTION=true|false`
  - 图片是否生成 caption 文本
- `IMAGE_EMBED_MODE=direct|caption_text`
  - 图片向量化模式：
  - `direct`：图片节点优先调用专用图像向量模型（`qwen3-vl-embedding`）
  - `caption_text`：图片节点仅使用 caption 文本走文本 embedding
- `INGESTION_TIMEOUT_SEC`
  - 多模态 ingestion 单文件超时控制（预留）
- `INGESTION_MAX_RETRIES`
  - 多模态 ingestion 重试次数
- `INGESTION_BATCH_SIZE`
  - 多模态节点批量 upsert 大小

### 3.3 向量入库阶段观察器

用于只观察多模态解析、chunk 切块、Node 构建、metadata、relationships、本地索引预览和 Qdrant payload 预览，不写入 Qdrant，不污染现有索引。

- `INGESTION_INSPECT_OUTPUT_DIR=storage/ingestion_visualization`
  - inspect 报告输出根目录。
- `INGESTION_INSPECT_MAX_TEXT_CHARS=4000`
  - `nodes.jsonl` 和 HTML 中单个文本字段最大保留字符数。
- `INGESTION_INSPECT_INCLUDE_MINERU_RAW=true`
  - 保存 MinerU raw markdown、预处理 markdown、structured content 和 raw result manifest。
- `INGESTION_INSPECT_RENDER_PDF_PAGES=false`
  - 是否渲染 PDF 页面截图。需要额外安装 `PyMuPDF`：`pip install pymupdf`。
- `INGESTION_INSPECT_INCLUDE_EMBEDDING_PREVIEW=true`
  - 输出 `vector_name/vector_dim/embedding_text_excerpt`，不保存完整向量。
- `INGESTION_INSPECT_INCLUDE_FULL_VECTORS=false`
  - 保留开关，默认关闭，避免报告过大。
- `INGESTION_INSPECT_WRITE_QDRANT=false`
  - inspect 默认是否在可视化完成后写入 Qdrant。默认 false，只观察。
- `INGESTION_INSPECT_WRITE_LOCAL_INDEX=true`
  - inspect 写 Qdrant 时是否同步写本地 BM25/page/table index。
- `INGESTION_INSPECT_RECREATE_COLLECTION=false`
  - inspect 写 Qdrant 时是否复用 build_index 的清库/重建行为。默认 false，显式开启才 recreate collection。
- `INGESTION_INSPECT_SYNC_BUILD_INDEX_LOGS=true`
  - inspect 入库阶段是否输出与 build_index 对齐的 stage log / summary log。

命令示例：

```powershell
py -3.11 -m agentic_rag.cli.inspect_ingestion data/demo_docs/xxx.pdf
py -3.11 -m agentic_rag.cli.inspect_ingestion data/demo_docs/xxx.pdf --render-pages
py -3.11 -m agentic_rag.cli.inspect_ingestion data/demo_docs --output storage/ingestion_visualization
py -3.11 -m agentic_rag.cli.inspect_ingestion data/demo_docs --write-qdrant
py -3.11 -m agentic_rag.cli.inspect_ingestion data/demo_docs --write-qdrant --recreate-collection
```

输出目录：

```text
storage/ingestion_visualization/runs/{run_id}/
  manifest.json
  nodes.jsonl
  nodes.pretty.json
  chunks.html
  document_map.html
  mineru_raw/
  pages/
  previews/
    qdrant_payload_preview.jsonl
    retrieval_index_preview.json
    embedding_preview.jsonl
    block_map.json
```

说明：

- `chunks.html` 按 Node 展示 chunk，颜色区分 `text/table/formula/image`，并标记 `page=null`、`section=null`、`relationships={}`。
- `document_map.html` 按文档页或文本流展示 Node；PDF 可选页面截图，DOCX 只显示文本流。
- `mineru_raw/structured_content.json` 用于排查 MinerU 是否返回页码、表格、公式、图片和 bbox。
- `qdrant_payload_preview.jsonl` 模拟 Qdrant payload，但不包含真实向量。
- `retrieval_index_preview.json` 预览 BM25/page/table 本地索引来源。

### 3.4 图像向量配置（新增）

- `IMAGE_EMBED_BASE_URL`
  - 图像 embedding 服务地址（OpenAI-Compatible）。
  - 默认：`https://dashscope.aliyuncs.com/compatible-mode/v1`。
- `IMAGE_EMBED_API_KEY`
  - 图像 embedding 的 API Key。
- `IMAGE_EMBED_MODEL`
  - 图像 embedding 模型名，默认 `qwen3-vl-embedding`。
- `IMAGE_EMBED_TIMEOUT_SEC`
  - 单次图像 embedding 请求超时（秒），默认 `60`。
- `IMAGE_EMBED_MAX_RETRIES`
  - 图像 embedding 重试次数，默认 `3`。
- `IMAGE_EMBED_BATCH_SIZE`
  - 图像 embedding 批次大小，默认 `8`。
- `IMAGE_EMBED_FALLBACK_TO_CAPTION=true|false`
  - `IMAGE_EMBED_MODE=direct` 且图像向量失败时，是否回退到 caption 文本 embedding。
  - `true`：不中断批次，回退到文本 embedding。
  - `false`：该图片节点记为失败并跳过。
- `IMAGE_EMBED_REQUIRE_FILE_EXISTS=true|false`
  - 是否在发起图像 embedding 前校验本地文件存在，默认 `true`。
  - 开启可更快定位路径错误。
- `IMAGE_ENRICHMENT_ENABLED=true|false`
  - 是否为纯图片生成 caption/OCR/object 派生证据节点，默认关闭。
- `IMAGE_MINERU_ENRICH_ENABLED=true|false`
  - 是否用 MinerU 作为纯图片 OCR/结构解析增强源；需要同时开启 `ENABLE_MINERU=true`。
- `IMAGE_VLM_CAPTION_ENABLED=true|false`
  - 是否调用视觉模型生成图片 caption、可见文字摘要和对象描述。
- `IMAGE_VLM_PROVIDER=openai_compatible|dashscope_sdk`
  - 图片 VLM 调用后端。`openai_compatible` 使用 `/chat/completions` 流式输出，适合本地图片 base64 data URL；`dashscope_sdk` 使用 `dashscope.MultiModalConversation.call` 流式输出。
- `IMAGE_VLM_BASE_URL` / `IMAGE_VLM_DASHSCOPE_API_URL` / `IMAGE_VLM_API_KEY` / `IMAGE_VLM_MODEL`
  - 视觉理解模型配置，默认模型名为 `qwen3-vl-plus`。`openai_compatible` 使用 `IMAGE_VLM_BASE_URL`，`dashscope_sdk` 使用 `IMAGE_VLM_DASHSCOPE_API_URL`。
- `IMAGE_VLM_TIMEOUT_SEC` / `IMAGE_VLM_MAX_RETRIES`
  - 视觉理解模型请求超时和重试次数。
- `IMAGE_VLM_ENABLE_THINKING=true|false`
  - 是否向兼容的视觉理解模型传入 `enable_thinking` 参数，默认关闭。
  - 默认关闭时会显式传入 `enable_thinking=false`；开启时会随 `qwen3-vl-plus` 请求传入 `enable_thinking=true` 和 `thinking_budget=81920`，并忽略流式 chunk 中的 `reasoning_content`，只解析最终回复文本。
  - 建议索引构建阶段保持 `false`，避免 caption/OCR/object 增强产生额外延迟和不稳定输出。
- VLM 调用失败不会中断索引构建；系统会跳过 caption/OCR/object 派生节点并保留 whole image 节点。`scene_type` 支持中文返回归一化，例如 `网页截图 -> screenshot`。
- `IMAGE_OBJECT_MAX_ITEMS` / `IMAGE_CAPTION_MAX_CHARS` / `IMAGE_OCR_MAX_CHARS`
  - 控制图片派生节点数量和文本长度。

`IMAGE_EMBED_MODE` 与上述配置关系：
- 当 `IMAGE_EMBED_MODE=direct`：
  - 使用 `IMAGE_EMBED_*` 配置请求图像向量。
  - 若失败且 `IMAGE_EMBED_FALLBACK_TO_CAPTION=true`，自动回退到 caption 文本 embedding。
- 当 `IMAGE_EMBED_MODE=caption_text`：
  - 不调用图像向量接口，直接走文本 embedding（保留兼容行为）。

图片增强开启后，一张纯图片会保留 `whole_image` 整图节点，并可追加 `caption`、`ocr`、`object` 派生节点。派生节点仍使用 `modality=image`，开启 Qdrant named vectors 时统一写入 `image` named vector；`qwen3.6-plus` 只负责生成描述文本，实际向量化仍由 embedding 模型完成。推荐让 `EMBEDDING_MODEL` 与 `IMAGE_EMBED_MODEL` 都使用同一多模态 embedding 模型，例如 `qwen3-vl-embedding`。

### 3.4 百炼/DashScope 推荐配置（已验证）

如果使用阿里云百炼控制台创建的 API Key，需要同时切换 `BASE_URL` 到 DashScope；不要把百炼 Key 配到 `https://apirouter.ai/v1`，否则会返回 `401 无效的令牌`。

依赖安装已包含 DashScope SDK：

```bash
py -3.11 -m pip install -r requirements.txt
```

`qwen3-vl-embedding` 暂不支持 DashScope OpenAI-compatible `/embeddings`，需要使用 DashScope 原生多模态 embedding provider：

```env
EMBEDDING_PROVIDER_TYPE=dashscope_multimodal
IMAGE_EMBED_PROVIDER_TYPE=dashscope_multimodal
DASHSCOPE_API_KEY=<your-bailian-api-key>
DASHSCOPE_EMBEDDING_MODEL=qwen3-vl-embedding
DASHSCOPE_EMBEDDING_DIMENSION=1024
EMBEDDING_MODEL=qwen3-vl-embedding
IMAGE_EMBED_MODEL=qwen3-vl-embedding
EMBEDDING_DIMENSIONS=1024
```

说明：

- 文本、caption、OCR、object 派生节点走 `embed_texts()`。
- `whole_image` 整图节点走 `embed_images()`。
- 两者使用同一 `qwen3-vl-embedding` 模型，保证处于同一语义空间。
- 如果 `DASHSCOPE_EMBEDDING_DIMENSION` 与 `EMBEDDING_DIMENSIONS` 同时配置，二者必须一致。
- 如果从 `text-embedding-v4` 或 OpenAI embedding 切到 `qwen3-vl-embedding`，需要新 Qdrant collection，或临时设置 `QDRANT_RECREATE_COLLECTION=true` 后重建索引。

常见错误：

```text
Unsupported model `qwen3-vl-embedding` for OpenAI compatibility mode
```

原因是仍在使用 OpenAI-compatible `/embeddings`。解决方式是切换为 `EMBEDDING_PROVIDER_TYPE=dashscope_multimodal`，图片整图向量同时设置 `IMAGE_EMBED_PROVIDER_TYPE=dashscope_multimodal`。

当前项目已用如下配置跑通 demo 索引与查询：

```env
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION=agentic_rag_docs

INGESTION_ENGINE=multimodal
MULTIMODAL_ENABLED=true
PDF_PARSER=unstructured
ENABLE_MINERU=true
MINERU_FALLBACK_TO_EXISTING=true
ENABLE_FORMULA_RECOGNITION=true
MINERU_ENABLE_FORMULA=true

EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_API_KEY=<your-bailian-api-key>
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMENSIONS=1024
EMBEDDING_BATCH_SIZE=10

LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=<your-bailian-api-key>
LLM_MODEL=qwen-plus

RERANK_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
RERANK_API_KEY=<your-bailian-api-key>
RERANK_PROVIDER=dashscope
RERANK_MODEL=qwen3-rerank
RERANK_RETURN_DOCUMENTS=true
RERANK_ENABLE_MULTIMODAL=false
RERANK_INSTRUCT=Given a web search query, retrieve relevant passages that answer the query.
```

说明：

- `text-embedding-v4` 返回 1024 维向量，因此建议设置 `EMBEDDING_DIMENSIONS=1024`，并让 Qdrant collection 使用同样维度。
- DashScope embedding 单批上限为 10 条；`EMBEDDING_BATCH_SIZE` 请设置为 `10` 或更小。
- 如果更换 embedding 模型或维度，需要重建 Qdrant collection。可临时设置 `QDRANT_RECREATE_COLLECTION=true` 后重新构建索引。
- `qwen3-rerank` 和 `qwen3-vl-rerank` 使用 DashScope `TextReRank` SDK 调用，不走 OpenAI-compatible rerank endpoint。
- TaskGraph 会在 evidence gate 前执行 rerank；如果 rerank 失败，查询会回退到未 rerank 的候选集，并在 debug 中输出 `rerank_fallback_reason`。
- 使用 `qwen3-vl-rerank` 时设置 `RERANK_MODEL=qwen3-vl-rerank`，并设置 `RERANK_ENABLE_MULTIMODAL=true`。本地图片无法直接作为远程 rerank 文档时，会回退到 caption/OCR 文本。

Rerank 关键配置：

- `RERANK_PROVIDER=dashscope`
  - rerank 后端。`qwen3-rerank` / `qwen3-vl-rerank` 应使用 `dashscope`，只有接入其他兼容 `/rerank` endpoint 的服务时才设置为 `openai_compatible`。
- `RERANK_RETURN_DOCUMENTS=true`
  - 是否要求 DashScope rerank 返回命中的文档内容。主链路只依赖 `index` 和 `score`，但开启后更方便排查排序结果。
- `RERANK_ENABLE_MULTIMODAL=false`
  - 是否使用多模态 rerank payload。`qwen3-rerank` 保持 `false`，使用 `query=str` 和 `documents=list[str]`；`qwen3-vl-rerank` 设置为 `true`，使用 `query=dict` 和 `documents=list[dict]`。
- `RERANK_INSTRUCT=...`
  - 传给 `qwen3-rerank` 的检索排序指令，用于约束“按查询找相关段落”的排序目标。多模态 `qwen3-vl-rerank` 默认不传该字段。

### 3.5 现有配置仍生效

- Qdrant：`QDRANT_*`
- Embedding：`EMBEDDING_*`
- Token 保护：
  - `EMBEDDING_INPUT_MAX_TOKENS`（默认 `8192`）
  - `EMBEDDING_INPUT_SAFETY_MARGIN_TOKENS`（默认 `256`）
- Rerank：`RERANK_*`
- LLM：`LLM_*`
- 检索：`RETRIEVAL_*` / `CONTEXT_TOP_N` / `PROMPT_MAX_CONTEXT_CHARS`

### 3.6 检索索引持久化配置（新增）

- `RETRIEVAL_INDEX_DIR=storage/retrieval_indexes`
  - BM25 / page / table 本地检索索引保存目录。
  - 实际路径为 `{RETRIEVAL_INDEX_DIR}/{QDRANT_COLLECTION}/bm25.json|page.json|table.json`。
- `RETRIEVAL_INDEX_PERSIST_ENABLED=true|false`
  - `true`：`build_index` 成功 upsert 后同步写出本地检索索引。
  - `false`：不写本地索引，查询时按 fallback 策略处理。
- `RETRIEVAL_INDEX_FALLBACK_TO_SCROLL=true|false`
  - `true`：本地索引缺失或损坏时，查询阶段 fallback 到 Qdrant `scroll_hits()` 临时构建。
  - `false`：本地索引不可用时直接报错，便于生产环境暴露索引构建问题。
- `REL_EXPAND_EXPLICIT_RELATIONSHIPS=true|false`
  - 是否让 relationship expansion 消费显式关系字段，例如 `context_node_ids`、`related_table_node_ids`、`related_formula_node_ids`、`doc_prev_node_id`。
- `REL_EXPAND_SAME_PAGE_RELATIONSHIPS=true|false`
  - 是否让 relationship expansion 消费 `same_page_node_ids`。
- `ENABLE_NAMED_VECTORS=true|false`
  - 默认 `false`，保持旧单向量 Qdrant collection 兼容。
  - 开启后 Qdrant collection 使用 `text/table/image` 三个 named vectors。
  - 建议使用新 collection，或设置 `QDRANT_RECREATE_COLLECTION=true` 重建旧 collection。
- `NAMED_VECTOR_TEXT_NAME=text`
- `NAMED_VECTOR_TABLE_NAME=table`
- `NAMED_VECTOR_IMAGE_NAME=image`
  - 分别控制文本、表格、图片向量字段名。
- `NAMED_VECTOR_FALLBACK_TO_TEXT=true|false`
  - 未显式指定向量字段时是否回退到 text named vector。

### 3.7 召回历史日志配置（新增）

- `RETRIEVAL_EVAL_LOG_ENABLED=true|false`
  - 是否记录 TaskGraph query 的召回历史 JSONL。默认 `false`，不产生文件。
- `RETRIEVAL_EVAL_SNAPSHOTS_ENABLED=true|false`
  - 只在内存中收集 TaskGraph 阶段快照，不写 JSONL。`check --pipeline taskgraph` 会自动开启该能力。
- `RETRIEVAL_EVAL_LOG_DIR=storage/retrieval_eval`
  - 召回历史日志目录。
- `RETRIEVAL_EVAL_LOG_FILE=retrieval_history.jsonl`
  - 召回历史 JSONL 文件名。每次 query append 一行，不覆盖历史。
- `RETRIEVAL_EVAL_MAX_TEXT_CHARS=2000`
  - 每个 hit 记录的 chunk 文本最大长度。

该日志用于后续计算 Hit Rate、MRR、Precision、Recall、AP、nDCG 等检索评测指标。当前只记录 TaskGraph 路径。每条 JSONL record 可包含 `initial_retrieval`、`initial_expanded`、`rerank`、`agent_chunk_grading`、`evidence_gate`、`retry_*`、`final_after_retry`、`final_output` 等 snapshot，hit 结构接近 LlamaIndex Node，并预留 `eval.is_relevant`、`eval.relevance_label`、`eval.graded_relevance` 字段供后续标注。

`check` 评测模块支持两条管线：

```powershell
# 快速简化链路：embedding -> retrieve -> rerank -> prompt -> generate
py -3.11 -m check --dataset check/example_cases.jsonl --pipeline simple --k 10

# 完整 TaskGraph 链路：包含 agent_chunk_grading / evidence_gate / local_retry / citation_verify 等阶段
py -3.11 -m check --dataset check/sciqa_2412_16030_cases.jsonl --pipeline taskgraph --k 10 --page-tolerance 1
```

新增评测参数说明：

- `--pipeline simple|taskgraph`
  - `simple`：使用 `check.pipeline.EvaluationPipeline` 的简化链路，适合快速基线评测。
  - `taskgraph`：调用完整 `TaskGraphRAG`，用于观测真实生产链路；会自动开启内存阶段快照，不要求写入 `retrieval_history.jsonl`。
- `--stage-metrics initial_retrieval,initial_expanded,rerank,agent_chunk_grading,final_after_retry`
  - 指定 `metrics_report` 中输出哪些阶段的检索指标。
  - 不指定时使用默认 TaskGraph 兼容阶段集合。
- `--use-expected-source-filter`
  - 评测专用参数：把 case 里的第一个 `expected_source/expected_sources` 作为 metadata `source` 过滤条件。
  - 只适合单文档 benchmark 或消融实验，不应用于真实生产 query。
- `--page-tolerance N`
  - 评测页码匹配容忍度，只影响 Hit Rate/MRR/Precision/Page Recall 等指标计算。
  - 例如标准页 `page=5` 且 `--page-tolerance 1` 时，命中 `page=4/5/6` 都算相关。
  - 该参数不对应 `.env` 配置，也不改变检索、rerank、TaskGraph 或最终回答。

检索指标说明：

- `precision_at_1` / `precision_at_3` 是单 query 的 top-k 精确率。
- `precision` 是当前阶段实际返回结果的相关占比，分母是该阶段返回 chunk 数，不是 `--k`。
- `recall` 当前定义为候选集内去重 `Chunk Recall@k`：Top-k 中相关唯一 chunk 数 / 当前阶段候选集中所有相关唯一 chunk 数。
- `recall` 的分母只来自当前阶段已经记录的候选集，不代表全库召回率；同一个 `node_id/point_id/chunk_index` 不会重复计入分母。
- `ap` / `ndcg` 需要知道该 query 的理想排序；如果标准答案只标到 `source/page/title` 级别，报告中会显示为 `-`。
- 只有 `node_id` / `point_id` / `chunk_index` 等精确 chunk 级标注足够完整时，`ap` / `ndcg` 才会计算。
- `page_recall` 是页级召回率：命中的标准页数 / 标准页总数，不把标准页内所有 chunk 都当作相关 chunk。
- `page_precision` 是页级精确率：命中的返回页数 / 返回页总数，按页去重计算。

可视化报告支持展示“被剔除 chunk”的灰色 ghost card，仅用于诊断，不参与指标计算：

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

如果只想跑前 1 条 QA，需要使用 `--limit 1`；`--k 10` 表示排名指标只看 Top 10，不表示样本数。

召回评分保留 raw score，并额外写入统一综合分：

- `score_vector`：Qdrant 向量检索分。
- `score_bm25`：BM25 关键词分。
- `score_rrf`：多通道 RRF 融合分。
- `rerank_score`：reranker 返回分。
- `score_composite`：按配置归一化加权后的统一分，用于排序、阈值和可视化解释。

相关配置：

```env
RETRIEVAL_SCORE_VECTOR_WEIGHT=0.30
RETRIEVAL_SCORE_BM25_WEIGHT=0.10
RETRIEVAL_SCORE_RRF_WEIGHT=0.25
RETRIEVAL_SCORE_RERANK_WEIGHT=0.35
RETRIEVAL_SCORE_RELATIONSHIP_WEIGHT=1.00
RETRIEVAL_SCORE_AGENT_RELEVANCE_WEIGHT=0.20
RETRIEVAL_INITIAL_MIN_COMPOSITE_SCORE=0.0
RETRIEVAL_RERANK_MIN_COMPOSITE_SCORE=0.0
RETRIEVAL_FINAL_MIN_COMPOSITE_SCORE=0.20
RETRIEVAL_RERANK_REQUIRE_PRIOR_SCORE=true
RETRIEVAL_RERANK_PRIOR_MIN_COMPOSITE_SCORE=0.15
RETRIEVAL_RERANK_PRIOR_LOW_SCORE_PENALTY=0.70
```

- `RETRIEVAL_SCORE_VECTOR_WEIGHT`
  - `score_composite` 中向量相似度归一化分的权重。
- `RETRIEVAL_SCORE_BM25_WEIGHT`
  - `score_composite` 中 BM25 关键词归一化分的权重。
- `RETRIEVAL_SCORE_RRF_WEIGHT`
  - `score_composite` 中 RRF 融合归一化分的权重。
- `RETRIEVAL_SCORE_RERANK_WEIGHT`
  - `score_composite` 中 reranker 分数归一化后的权重；`RERANK_ENABLED=false` 时该组件自然为空。
- `RETRIEVAL_SCORE_RELATIONSHIP_WEIGHT`
  - 后续 composite recompute 阶段 relationship component 的权重。继承分本身由 `REL_EXPAND_*_WEIGHT` 计算；该配置不再被强制提升到 `1.0`，设置为 `0.30` 就会按 `0.30` 生效。 
  - 扩展的表格/公式/文本最终计入的权重，会先算REL_EXPAND_RELATED_TABLE_WEIGHT等这些配置的权重，最终再乘以该权重来计入最终的score_composite分数。
- `RETRIEVAL_SCORE_AGENT_RELEVANCE_WEIGHT`
  - Agent chunk grading 产生的 `agent_relevance_score` 在后续 composite recompute 中的权重。
- `RETRIEVAL_INITIAL_MIN_COMPOSITE_SCORE`
  - 初步召回/RRF 后的最低 `score_composite`。低于该值的 hit 会在 initial 阶段过滤。
- `RETRIEVAL_RERANK_MIN_COMPOSITE_SCORE`
  - rerank 后最低 `score_composite`。低于该值的 hit 会在 rerank 阶段过滤。
- `RETRIEVAL_FINAL_MIN_COMPOSITE_SCORE`
  - 最终进入 prompt/citation 的最低 `score_composite`。
- `RETRIEVAL_RERANK_REQUIRE_PRIOR_SCORE`
  - 是否启用 rerank prior 保护，防止 prior 分很低的 chunk 只靠 `rerank_score` 被拉高。
- `RETRIEVAL_RERANK_PRIOR_MIN_COMPOSITE_SCORE`
  - rerank 前 `score_composite` 的低分阈值。
- `RETRIEVAL_RERANK_PRIOR_LOW_SCORE_PENALTY`
  - prior 低于阈值时，对 rerank 后综合分施加的惩罚系数。 惩罚系数越低，对应的rerank分值越低，相当于权重。

表格/公式优先作为 text chunk 的关系上下文补充召回。高分 text 命中后，会根据 `related_table_node_ids`、`related_formula_node_ids` 派生相关 table/formula 分数，并记录扩展来源；这些关系扩展不依赖 query 必须显式包含“表格/公式”。

```env
REL_EXPAND_RELATED_MODALITY_ENABLED=true
REL_EXPAND_MIN_SEED_COMPOSITE_SCORE=0.30
REL_EXPAND_SEED_TOP_M=6
REL_EXPAND_MAX_RELATED_TABLES=5
REL_EXPAND_MAX_RELATED_FORMULAS=5
REL_EXPAND_RELATED_TABLE_WEIGHT=0.90
REL_EXPAND_RELATED_FORMULA_WEIGHT=0.85
REL_EXPAND_CONTEXT_TEXT_ENABLED=true
REL_EXPAND_CONTEXT_TEXT_WEIGHT=0.60
REL_EXPAND_PAGE_WINDOW_WEIGHT=0.30
REL_EXPAND_SAME_PAGE_WEIGHT=0.30
REL_EXPAND_PREV_NEXT_WEIGHT=0.50
REL_EXPAND_PARENT_CHILD_WEIGHT=0.60
REL_EXPAND_DOC_ADJACENT_WEIGHT=0.50
REL_EXPAND_CONTEXT_TEXT_MIN_SEED_SCORE=0.45
REL_EXPAND_CONTEXT_TEXT_SEED_TOP_M=4
REL_EXPAND_CONTEXT_TEXT_MAX_PER_SEED=2
RETRIEVAL_RELATED_EVIDENCE_MAX_TOTAL=8
RETRIEVAL_RELATED_EVIDENCE_MAX_PER_SEED=3
REL_EXPAND_ROUTED_MIN_SEED_SCORE=0.55
REL_EXPAND_ROUTED_SEED_TOP_M=3
REL_EXPAND_ROUTED_MAX_PER_SEED=5
REL_EXPAND_ROUTED_MAX_TOTAL=20
REL_EXPAND_ROUTED_ALLOWED_MODALITIES=text,table,formula
REL_EXPAND_RETRY_SAME_PAGE_ENABLED=true
REL_EXPAND_RETRY_MIN_SEED_SCORE=0.55
REL_EXPAND_RETRY_SEED_TOP_M=3
REL_EXPAND_RETRY_MAX_PER_SEED=4
REL_EXPAND_RETRY_MAX_TOTAL=12
```

- `REL_EXPAND_RELATED_MODALITY_ENABLED`
  - 是否根据高分 text seed 的 `related_table_node_ids` / `related_formula_node_ids` 扩展 table/formula。
- `REL_EXPAND_MIN_SEED_COMPOSITE_SCORE`
  - 触发 table/formula 关系扩展的 seed 最低综合分。
- `REL_EXPAND_SEED_TOP_M`
  - 只对排序前 M 的 seed 做 table/formula 关系扩展，控制扩展规模。
- `REL_EXPAND_MAX_RELATED_TABLES`
  - 单次扩展中 related table 的数量上限。
- `REL_EXPAND_MAX_RELATED_FORMULAS`
  - 单次扩展中 related formula 的数量上限。
- `REL_EXPAND_RELATED_TABLE_WEIGHT`
  - related table 继承 seed 分数的权重，派生分约为 `seed_score_composite * weight`。
- `REL_EXPAND_RELATED_FORMULA_WEIGHT`
  - related formula 继承 seed 分数的权重。
- `REL_EXPAND_CONTEXT_TEXT_ENABLED`
  - 是否扩展高分 text seed 的上下文 text，例如 `context_node_ids`、`prev_id`、`next_id`。
- `REL_EXPAND_CONTEXT_TEXT_WEIGHT`
  - context text 继承 seed 分数的权重，低于 table/formula，避免相邻文本被过度放大。
- `REL_EXPAND_PAGE_WINDOW_WEIGHT`
  - `page_window` 扩展继承 seed 分数的权重。
- `REL_EXPAND_SAME_PAGE_WEIGHT`
  - `same_page_node_ids` 扩展继承 seed 分数的权重。
- `REL_EXPAND_PREV_NEXT_WEIGHT`
  - `prev_id` / `next_id` 扩展继承 seed 分数的权重。
- `REL_EXPAND_PARENT_CHILD_WEIGHT`
  - `parent_id` / `child_ids` 扩展继承 seed 分数的权重。
- `REL_EXPAND_DOC_ADJACENT_WEIGHT`
  - `doc_prev_node_id` / `doc_next_node_id` 扩展继承 seed 分数的权重。
- `REL_EXPAND_CONTEXT_TEXT_MIN_SEED_SCORE`
  - 触发上下文 text 扩展的 seed 最低综合分。
- `REL_EXPAND_CONTEXT_TEXT_SEED_TOP_M`
  - 只对排序前 M 的 seed 做上下文 text 扩展。
- `REL_EXPAND_CONTEXT_TEXT_MAX_PER_SEED`
  - 每个 seed 最多扩展的上下文 text 数量。
- `RETRIEVAL_RELATED_EVIDENCE_MAX_TOTAL`
  - protected related evidence 的总数上限，避免一个高分 seed 带出过多 table/formula/context。
- `RETRIEVAL_RELATED_EVIDENCE_MAX_PER_SEED`
  - 单个 seed 可带出的 protected related evidence 数量上限。
- `REL_EXPAND_ROUTED_MIN_SEED_SCORE`
  - 任务路由明确需要 page/cross-doc relationship 扩展时，seed 的最低综合分。
- `REL_EXPAND_ROUTED_SEED_TOP_M`
  - routed 模式只允许前 M 个 seed 触发 `same_page_node_ids/page_window/parent/child/doc_prev/doc_next`。
- `REL_EXPAND_ROUTED_MAX_PER_SEED`
  - routed 模式每个 seed 最多扩展的节点数。
- `REL_EXPAND_ROUTED_MAX_TOTAL`
  - routed 模式单次 query 最多扩展的节点总数。
- `REL_EXPAND_ROUTED_ALLOWED_MODALITIES`
  - routed 模式允许扩展的 modality，默认 `text,table,formula`。
- `REL_EXPAND_RETRY_SAME_PAGE_ENABLED`
  - 局部重检 retry 模式是否允许 `same_page_node_ids/page_window` 扩展。
- `REL_EXPAND_RETRY_MIN_SEED_SCORE`
  - retry 模式 same-page/page-window 扩展的 seed 最低综合分。
- `REL_EXPAND_RETRY_SEED_TOP_M`
  - retry 模式只允许前 M 个 seed 做 same-page/page-window 扩展。
- `REL_EXPAND_RETRY_MAX_PER_SEED`
  - retry 模式每个 seed 最多扩展的 same-page/page-window 节点数。
- `REL_EXPAND_RETRY_MAX_TOTAL`
  - retry 模式单次 query 最多扩展的 same-page/page-window 节点总数。

关系扩展分三种模式：

- `auto`
  - 首轮自动扩展，只允许高分 text seed 扩展 `related_table_node_ids` / `related_formula_node_ids` 和严格受控的 text context。
  - 不消费 `same_page_node_ids/page_window/parent/child/doc_prev/doc_next`。
- `routed`
  - 任务路由明确需要页级或跨文档检索时使用，允许 same-page/page-window/父子/跨文档关系，但受 routed 阈值和数量限制。
- `retry`
  - 局部重检阶段使用，和 `agent_context_expand` 触发逻辑配合，只对少量高分 seed 做补充扩展。

关键词触发的 `table/formula` 直接通道仅作为弱补充：

```env
RETRIEVAL_AUTO_TABLE_CHANNEL_ENABLED=true
RETRIEVAL_AUTO_FORMULA_CHANNEL_ENABLED=true
RETRIEVAL_AUTO_IMAGE_CHANNEL_ENABLED=true
RETRIEVAL_TABLE_TRIGGER_KEYWORDS=表格,列表,table,tabular,row,column
RETRIEVAL_FORMULA_TRIGGER_KEYWORDS=公式,方程,表达式,formula,equation,ratio,sigma,theta,tau
RETRIEVAL_IMAGE_TRIGGER_KEYWORDS=image,figure,fig.,plot,graph,curve,axis,legend,panel,subplot,caption,图,图像,曲线
QUERY_VARIANTS_ENABLED=true
QUERY_VARIANTS_MAX=4
QUERY_VARIANT_BOOST_PER_HIT=0.03
QUERY_VARIANT_MAX_BOOST=0.12
QUERY_VARIANT_MIN_TOKEN_LEN=3
RERANK_GUARDRAIL_ENABLED=true
RERANK_GUARDRAIL_MAX_ANCHOR_HITS=3
RERANK_GUARDRAIL_MIN_ANCHOR_SCORE=0.20
IMAGE_QUERY_CONTEXT_EXPAND_ENABLED=true
IMAGE_QUERY_CONTEXT_MAX_TEXT_HITS=3
IMAGE_QUERY_CONTEXT_WEIGHT=0.70
```

- `RETRIEVAL_AUTO_TABLE_CHANNEL_ENABLED`
  - query 命中弱关键词时，是否额外加入 direct `table` 检索通道。
- `RETRIEVAL_AUTO_FORMULA_CHANNEL_ENABLED`
  - query 命中弱关键词时，是否额外加入 direct `formula` 检索通道。
- `RETRIEVAL_AUTO_IMAGE_CHANNEL_ENABLED`
  - query 命中 figure/plot/caption 等关键词时，是否额外加入 `image` 检索通道。
- `RETRIEVAL_TABLE_TRIGGER_KEYWORDS`
  - 触发 direct `table` channel 的逗号分隔关键词。该机制只是补充，不影响 relationship-based table expansion。
- `RETRIEVAL_FORMULA_TRIGGER_KEYWORDS`
  - 触发 direct `formula` channel 的逗号分隔关键词。该机制只是补充，不影响 relationship-based formula expansion。
- `RETRIEVAL_IMAGE_TRIGGER_KEYWORDS`
  - 触发 `image` channel 的逗号分隔关键词，用于 figure/caption/plot/curve 类问题。
- `QUERY_VARIANTS_ENABLED`
  - 对 BM25 类词法检索启用规则 query variant，包括符号归一化、关键词/figure/funding 变体；第一版不调用 LLM。
- `QUERY_VARIANTS_MAX`
  - 单个 query 最多生成多少条规则变体。
- `QUERY_VARIANT_BOOST_PER_HIT` / `QUERY_VARIANT_MAX_BOOST`
  - 同一 chunk 被多个 query variant 命中时，对 `score_composite` 做小幅加分，避免单 query embedding 漏掉符号/caption 类证据。
- `RERANK_GUARDRAIL_ENABLED`
  - reranker 未选中但与 query 有精确锚点重合、且 prior 分不低的 chunk，可被少量保护回流，防止关键实体/符号证据被 rerank 误删。
- `IMAGE_QUERY_CONTEXT_EXPAND_ENABLED`
  - image hit 被召回后，是否补入同页 text context 作为证据，补入分数为 `image_score * IMAGE_QUERY_CONTEXT_WEIGHT`。

局部重检阶段可基于高分 seed 追加 relationship/page-window 扩展：

```env
TG_AGENT_CONTEXT_EXPANSION_ENABLED=true
TG_AGENT_CONTEXT_EXPANSION_MIN_SEED_SCORE=0.55
TG_AGENT_CONTEXT_EXPANSION_SEED_TOP_M=3
TG_AGENT_CONTEXT_EXPANSION_MAX_ROUNDS=2
TG_AGENT_CONTEXT_EXPANSION_MAX_CONTEXT_HITS=4
```

- `TG_AGENT_CONTEXT_EXPANSION_ENABLED`
  - 局部重检时是否允许基于高分 seed 追加 relationship 扩展。当前实现为规则触发，不新增额外 LLM 调用。
- `TG_AGENT_CONTEXT_EXPANSION_MIN_SEED_SCORE`
  - 局部重检上下文扩展的 seed 最低综合分。
- `TG_AGENT_CONTEXT_EXPANSION_SEED_TOP_M`
  - 只检查 rerank/expanded hits 中前 M 个高分 seed。
- `TG_AGENT_CONTEXT_EXPANSION_MAX_ROUNDS`
  - 预留给后续多轮上下文扩展的最大轮数配置。
- `TG_AGENT_CONTEXT_EXPANSION_MAX_CONTEXT_HITS`
  - 预留给后续 Agent 判断上下文扩展时的上下文 hit 数量上限。

Agent chunk grading 可在 rerank 后、evidence_gate 前对单个 chunk 做语义相关性清洗。默认关闭，避免增加 LLM 成本。

```env
TG_AGENT_CHUNK_GRADING_ENABLED=false
TG_AGENT_CHUNK_GRADE_CACHE_ENABLED=true
TG_AGENT_CHUNK_GRADING_MODE=head_tail
TG_AGENT_CHUNK_GRADING_HEAD_M=6
TG_AGENT_CHUNK_GRADING_TAIL_N=2
TG_AGENT_CHUNK_GRADING_MAX_CHUNKS=8
TG_AGENT_CHUNK_LABELS=irrelevant,weak,relevant,strong
TG_AGENT_CHUNK_DROP_LABELS=irrelevant
TG_AGENT_CHUNK_DROP_SCORE_THRESHOLD=0.25
TG_AGENT_CHUNK_DROP_ENABLED=true
TG_AGENT_CHUNK_DROP_REQUIRE_LABEL_AND_SCORE=true
TG_AGENT_CHUNK_DROP_PROTECT_PRIOR_SCORE=0.55
TG_AGENT_CHUNK_DROP_PROTECT_RERANK_SCORE=0.70
TG_AGENT_CHUNK_DROP_PROTECT_ANCHORS=true
TG_AGENT_CHUNK_SCORE_ADJUST_ENABLED=true
TG_AGENT_CHUNK_LABEL_SCORE_DELTAS=irrelevant:-0.20,weak:-0.05,relevant:0.03,strong:0.10
TG_AGENT_CHUNK_CONTEXT_FOR_TABLE_FORMULA=true
TG_AGENT_CHUNK_RELATED_CONTEXT_ENABLED=true
TG_AGENT_CHUNK_RELATED_CONTEXT_ADD_LABELS=strong
TG_AGENT_CHUNK_RELATED_CONTEXT_TRIGGER_MODE=label_only
TG_AGENT_CHUNK_RELATED_CONTEXT_FIXED_SCORES=irrelevant:0.00,weak:0.40,relevant:0.65,strong:0.70
TG_AGENT_CHUNK_RELATED_CONTEXT_EXISTING_MODALITY_DELTAS=irrelevant:-0.10,weak:-0.03,relevant:0.03,strong:0.08
TG_AGENT_CHUNK_RELATED_CONTEXT_EXISTING_TEXT_DELTAS=irrelevant:0.00,weak:0.00,relevant:0.02,strong:0.06
TG_AGENT_CHUNK_RELATED_CONTEXT_ADDED_MODALITY_DELTAS=irrelevant:0.00,weak:0.00,relevant:0.03,strong:0.08
```

- `TG_AGENT_CHUNK_GRADING_ENABLED`
  - 是否启用逐 chunk Agent 相关性评定。
- `TG_AGENT_CHUNK_GRADE_CACHE_ENABLED`
  - 是否在同一次 TaskGraph 执行中缓存并继承 Agent 对同一 `node_id/point_id` 的评级。开启后，retry 中再次出现的 chunk 会复用已评定的 `agent_relevance_label`、`agent_relevance_score` 和 reasoning，不再重复调用 Agent；分数 delta 会基于该阶段当前 `score_composite` 重新计算。
- `TG_AGENT_CHUNK_GRADING_MODE`
  - 评定范围，支持 `all/head/head_tail/none`。
- `TG_AGENT_CHUNK_GRADING_HEAD_M` / `TG_AGENT_CHUNK_GRADING_TAIL_N`
  - `head_tail` 模式下评定前 M 个和后 N 个 chunk。
- `TG_AGENT_CHUNK_GRADING_MAX_CHUNKS`
  - 单次最多发送给 Agent 的 chunk 数；Agent 以 batch JSON 返回，但仍按 node_id 对每个 chunk 单独评定，不做整体充分性判断。
- `TG_AGENT_CHUNK_DROP_LABELS` / `TG_AGENT_CHUNK_DROP_SCORE_THRESHOLD`
  - Agent 低相关剔除的标签和分数阈值。
- `TG_AGENT_CHUNK_DROP_REQUIRE_LABEL_AND_SCORE`
  - 为 `true` 时，必须同时命中 drop label 且低于分数阈值才硬剔除，避免 Agent 单个 `drop=true` 误删高分证据。
- `TG_AGENT_CHUNK_DROP_PROTECT_PRIOR_SCORE` / `TG_AGENT_CHUNK_DROP_PROTECT_RERANK_SCORE`
  - prior `score_composite` 或 `rerank_score` 足够高时，即使 Agent 给出低相关，也只做分数下沉，不直接硬剔除。
- `TG_AGENT_CHUNK_DROP_PROTECT_ANCHORS`
  - chunk 与 query 存在实体、符号、数字等精确锚点重合时，保护其不被 Agent 硬剔除。
- `TG_AGENT_CHUNK_SCORE_ADJUST_ENABLED`
  - 是否启用按评级调整 `score_composite`。
- `TG_AGENT_CHUNK_LABEL_SCORE_DELTAS`
  - Agent 评级到分数增量的映射。计算为 `score_after = clamp(score_before + delta[label], 0, 1)`；支持负数，因此 `irrelevant/weak` 可以下沉，`relevant/strong` 可以上浮。
- `TG_AGENT_CHUNK_CONTEXT_FOR_TABLE_FORMULA`
  - 评定 table/formula 时是否把 linked text context 一起发给 Agent。
- `TG_AGENT_CHUNK_RELATED_CONTEXT_ENABLED`
  - 是否对 table/formula 的 linked text context 执行联动策略。
- `TG_AGENT_CHUNK_RELATED_CONTEXT_ADD_LABELS`
  - linked text context 不在当前 hits 中时，哪些 table/formula 评级允许补入 context。默认 `strong`，如需 `relevant` 也补入可设为 `relevant,strong`。
- `TG_AGENT_CHUNK_RELATED_CONTEXT_TRIGGER_MODE`
  - context 补入触发模式。当前实现只支持 `label_only`。
- `TG_AGENT_CHUNK_RELATED_CONTEXT_FIXED_SCORES`
  - linked text context 不在当前 hits 中且允许补入时，按 table/formula 评级给补入 context 设置固定 `score_composite`。
- `TG_AGENT_CHUNK_RELATED_CONTEXT_EXISTING_MODALITY_DELTAS`
  - linked text context 已在当前 hits 中时，对 table/formula 额外增加的分数增量。
- `TG_AGENT_CHUNK_RELATED_CONTEXT_EXISTING_TEXT_DELTAS`
  - linked text context 已在当前 hits 中时，对该 text context 额外增加的分数增量；不覆盖原分数，只做 delta 调整。
- `TG_AGENT_CHUNK_RELATED_CONTEXT_ADDED_MODALITY_DELTAS`
  - linked text context 不在当前 hits 中且被补入时，对 table/formula 额外增加的分数增量。
- `TG_AGENT_CHUNK_DROP_ENABLED`
  - 为 `true` 时，被 drop 的 chunk 会从 evidence gate 前的 hits 中移除；如果希望观察 `irrelevant/weak` 的负向 delta 下沉效果，可临时设为 `false`。

Prompt 上下文构建提供截断保护，避免第一个超长 chunk 直接让 `final_output` 为空：

```env
PROMPT_CONTEXT_TRUNCATION_ENABLED=true
PROMPT_MIN_CHUNK_CHARS=300
```

- `PROMPT_CONTEXT_TRUNCATION_ENABLED`
  - 单个 chunk 超过剩余 prompt 预算时，尝试截断该 chunk，而不是直接 `break` 丢弃后续证据。
- `PROMPT_MIN_CHUNK_CHARS`
  - 截断时尽量保留的最小正文字符数；预算不足时会跳过该 chunk 并继续尝试后续更短 chunk。

入库阶段提供第一版轻量长文本切分，默认关闭，避免未重建索引前改变线上行为：

```env
CHUNK_STRUCTURAL_SPLIT_ENABLED=false
CHUNK_HARD_MAX_CHARS=1800
CHUNK_STRUCTURAL_SPLIT_MIN_PART_CHARS=300
CHUNK_STRUCTURAL_SPLIT_OVERLAP=80
```

- `CHUNK_STRUCTURAL_SPLIT_ENABLED`
  - 只对超长 `text` node 生效，不拆 table/formula/image；启用后需要重建索引。
- `CHUNK_HARD_MAX_CHARS`
  - 超过该长度的 text node 会按段落/句子边界拆分为多个 part node。
- `CHUNK_STRUCTURAL_SPLIT_OVERLAP`
  - part 之间的字符重叠，用于保留跨边界上下文。

清空历史文件：

```powershell
py -3.11 scripts/clear_retrieval_history.py
```

也可以指定路径：

```powershell
py -3.11 scripts/clear_retrieval_history.py --path storage/retrieval_eval/retrieval_history.jsonl
```

### 3.8 召回可视化配置（新增）

- `RETRIEVAL_VIS_OUTPUT_DIR=storage/retrieval_visualization`
  - 召回可视化报告输出目录。
- `RETRIEVAL_VIS_MAX_TEXT_CHARS=4000`
  - HTML 和 raw record 中每个 hit 文本最大展示长度。
- `RETRIEVAL_VIS_INCLUDE_FULL_VECTORS=false`
  - 默认不展示完整向量字段，避免报告不可读。
- `RETRIEVAL_VIS_AUTO_WRITE=false`
  - 是否在每次 TaskGraph query 完成后自动生成召回可视化报告。

离线查看最新一条召回历史：

```powershell
py -3.11 -m agentic_rag.cli.inspect_retrieval --latest
```

查看指定 `query_id`：

```powershell
py -3.11 -m agentic_rag.cli.inspect_retrieval --query-id <query_id>
```

报告输出到：

```text
storage/retrieval_visualization/runs/{run_id}/
  manifest.json
  query.json
  snapshots.json
  chunks.html
  stage_compare.html
  rank_flow.html
  citations.html
  raw_record.json
```

第一版展示 `initial_retrieval`、`rerank`、`final_after_retry` 三个 snapshot，不展示 `vector/bm25/page/table` route 级原始命中。

### 3.9 TaskGraph 可选路径配置（新增）

- `TASKGRAPH_ENABLED=true|false`
  - `false`（默认）：继续走旧 `rag_graph.py` 兼容路径
  - `true`：启用 `task_graph.py` 路径（CLI 入口不变）
- `TASKGRAPH_EVIDENCE_GATE_ENABLED=true|false`
  - TaskGraph 证据校验消融开关。默认 `true`，保持原链路。关闭后不会执行 EvidenceGate / AgentEvidenceCritic，debug 中 `support_score=0.0`、`support_level=skipped` 仅作为占位值，不代表真实证据充分。
- `TASKGRAPH_LOCAL_RETRY_ENABLED=true|false`
  - TaskGraph 局部重检消融开关。默认 `true`，保持原链路。关闭后即使 EvidenceGate 或 citation verify 建议 retry，也不会进入 `local_retry`。
- `QUERY_PROGRESS_ENABLED=true|false`
  - 是否在普通 CLI query 输出中显示 TaskGraph 粗粒度进度。`--json` 会自动关闭，避免污染机器可读输出。
- `QUERY_PROGRESS_STYLE=plain`
  - 进度输出格式。当前仅支持 plain 文本，输出到 stderr。
- `QUERY_PROGRESS_SHOW_RETRY=true|false`
  - 是否显示局部重检阶段的 retry 次数。
- `TG_MAX_RETRIES`
  - 局部重检最大轮次，超过后直接 finalize
- `TG_BUDGET_TOKENS`
  - TaskGraph 估算 token 预算，超预算停止重检
- `TG_BUDGET_MS`
  - TaskGraph 时间预算（毫秒），超预算停止重检
- `TG_MIN_EVIDENCE_HITS`
  - 证据门控最低命中数
- `TG_MIN_COVERAGE_RATIO`
  - 证据关键词覆盖阈值（0~1）
- `TG_MIN_GAIN_THRESHOLD`
  - 连续重检时最小证据增益阈值
- `TG_MIN_SUPPORT_SCORE`
  - EvidenceGate 判定证据至少部分支持问题所需的最低支持度分数
- `TG_STRONG_SUPPORT_SCORE`
  - EvidenceGate 判定强支持证据的分数阈值
- `TG_SUPPORT_W_TOP_HIT`
  - 支持度分数中 top hit score 的权重，默认 `0.30`
- `TG_SUPPORT_W_AVG_TOP`
  - 支持度分数中 top hits 平均分的权重，默认 `0.20`
- `TG_SUPPORT_W_BM25_VECTOR`
  - 支持度分数中 BM25/vector 一致性的权重，默认 `0.15`
- `TG_SUPPORT_W_RERANK`
  - 支持度分数中 rerank 分数的权重，默认 `0.10`
- `TG_SUPPORT_W_SOURCE_DIVERSITY`
  - 支持度分数中来源多样性的权重，默认 `0.10`
- `TG_SUPPORT_W_SLOT_COVERAGE`
  - 支持度分数中 slot 覆盖的权重，默认 `0.10`
- `TG_SUPPORT_W_KEYWORD`
  - 支持度分数中关键词覆盖的权重，默认 `0.05`
- `TG_DEBUG_SUPPORT_FEATURES=true|false`
  - 是否在 CLI TaskGraph Debug 中打印 `support_features`、有效权重和贡献值，便于观察量纲和调权重。
- `TG_SUPPORT_SCORE_NORMALIZATION=rank|minmax|raw`
  - top hit / avg top 的归一化方式。推荐 `rank`，避免 RRF 原始分数 `0.01~0.05` 直接拉低支持度。
- `TG_SUPPORT_DISABLE_MISSING_RERANK_WEIGHT=true|false`
  - rerank 未执行或无 `rerank_score` 时，是否从分母移除 rerank 权重。推荐 `true`。
- `TG_SUPPORT_CONSISTENCY_MODE=overlap|score_span`
  - BM25/vector 一致性计算方式。推荐 `overlap`，按 node/source 交集判断，不直接比较 BM25/vector/RRF 原始分数。
- `TG_SUPPORT_SOURCE_DIVERSITY_MODE=auto|always|disabled`
  - source diversity 是否参与评分。`auto` 只在跨文档/对比/冲突类问题中使用真实多样性，普通介绍类问题不惩罚单文档命中。
- `TG_SUPPORT_SLOT_COVERAGE_HARD_ONLY=true|false`
  - slot coverage 是否只统计页码、来源、数值、模态等 hard slots。推荐 `true`，避免 keyword coverage 被重复惩罚。
- `TG_CONFLICT_NUMERIC_TOLERANCE`
  - 数值冲突判断容差，默认 `0.0` 表示不同数值严格视为冲突候选
- `TG_REQUIRED_SLOT_STRICT=true|false`
  - 是否要求页码、来源、数值、表格/图片/公式等必需槽位全部覆盖后才允许通过证据门控
- `TG_RETRY_TOP_K_MULTIPLIER`
  - TaskGraph `local_retry` 提高各检索通道 `top_k` 的倍率，默认 `1.5`
- `TG_RETRY_MAX_TOP_K`
  - TaskGraph `local_retry` 允许的最大 `top_k`，防止重检无限放大
- `TG_RETRY_PAGE_WINDOW_STEP`
  - 页码缺失或冲突重检时，每轮扩展的邻页窗口步长
- `TG_RETRY_MAX_PAGE_WINDOW`
  - relationship expansion 可使用的最大邻页窗口
- `TG_RETRY_REWRITE_ENABLED=true|false`
  - 是否启用规则版 query rewrite；当前不接 LLM rewrite
- `TG_RETRY_CARRY_FORWARD_ENABLED=true|false`
  - 是否在 retry 时把上一轮满足条件的候选证据补入本轮 `retry_*_expanded`，再与本轮新召回结果一起进入 rerank；不会写入 `retry_*_retrieval`。如果本轮也召回了同一 chunk，默认用上一轮 carry-forward 版本覆盖本轮版本，避免 retry 检索波动降低上一轮高质量候选。
- `TG_RETRY_CARRY_FORWARD_TOP_N`
  - 每轮 retry 最多额外补入多少个上一轮候选。该值不是合并后的总 top-k。
- `TG_RETRY_CARRY_FORWARD_MIN_SCORE`
  - carry-forward 的最低上一轮 `score_composite` 门槛。它是筛选阈值，不是补入后的固定分数。
- `TG_RETRY_CARRY_FORWARD_MIN_AGENT_LABEL`
  - carry-forward 的最低 Agent 评定等级，默认 `relevant`。候选必须同时满足分数门槛和等级门槛；没有 Agent 评级的 chunk 默认不补入。
- `TG_RETRY_CARRY_FORWARD_INCLUDE_CITATION_CANDIDATES=true|false`
  - citation retry 场景下，是否允许上一轮已经进入 prompt/final 候选的 chunk 参与 carry-forward。

Agent hard drop 会缓存到同一次 TaskGraph run 的 `agent_chunk_drop_cache`。如果上一轮被 Agent 硬剔除的 chunk 在 retry 中再次被召回，会在 `retry_*_retrieval` / `retry_*_expanded` 进入 rerank 前被过滤，并在评测可视化中显示为 `agent_drop_cache`。
- `TG_CITATION_VERIFY_ENABLED=true|false`
  - 是否启用 `citation_verify` 节点。关闭后该节点直接返回 `citation_ok=true`，不会因为引用标记问题触发局部重检。
- `TG_CITATION_STRICT=true|false`
  - 引用校验严格模式。`false` 时只校验已有 citation 是否能回溯到当前上下文，不强制答案文本必须包含 `[1]` 这类标记，也不因 citation 为空直接判失败；`true` 时会强制答案包含引用标记且 citation 非空。
  - `final_output` 只展示最终 citation 使用到的 chunk；`final_after_retry -> final_output` 消失的 chunk 会在评测可视化中标记为 `citation_not_selected`，表示未被最终引用选中、未生成有效 citation 或 citation 校验失败。
- `TG_GENERATE_ON_RETRY_EXHAUSTED=true|false`
  - retry 达到 `TG_MAX_RETRIES` 但仍有候选证据时，是否继续进入 `build_prompt -> generate_answer`。开启后不会因为证据门仍建议 retry 就直接 finalize。
- `TG_GENERATE_ON_RETRY_EXHAUSTED_MIN_HITS`
  - retry 用尽后允许生成所需的最少候选 hit 数量。默认 `1`。
- `TG_ALLOW_REFUSAL=true|false`
  - 证据冲突不可消解时是否允许拒答
- `TG_ROUTE_LLM_ENABLED=true|false`
  - 预留开关：是否启用 LLM 辅助路由（当前默认关闭）
- `TG_AGENT_ROUTE_ENABLED=true|false`
  - 是否启用受约束 LLM Agent 问题分析。默认关闭；`TG_ROUTE_LLM_ENABLED` 可作为兼容开关。
- `TG_AGENT_RETRIEVAL_PLANNER_ENABLED=true|false`
  - 是否启用 Agent 检索规划建议。Agent 只能建议 `RetrievalPlan`，不能直接执行检索。
- `TG_AGENT_EVIDENCE_CRITIC_ENABLED=true|false`
  - 是否启用 Agent 证据充分性判断。最终 gate 使用保守合并，不能绕过规则 EvidenceGate。
- `TG_AGENT_RETRY_ADVISOR_ENABLED=true|false`
  - 是否启用 Agent 局部重检建议。建议会经过 plan 校验，不能突破 top_k/page_window/retry 预算。
- `TG_AGENT_MAX_CONTEXT_HITS`
  - 发送给 Agent EvidenceCritic 的最大证据条数。
- `TG_AGENT_FALLBACK_TO_RULES=true|false`
  - Agent 输出非法、非 JSON 或 LLM 调用失败时是否回退现有规则路径。
- `TG_AGENT_MIN_ROUTE_CONFIDENCE`
  - Agent 路由结果被采纳的最低置信度。

Agent 接入原则：

- Agent 只提出问题分析、检索规划、证据批判和重检建议。
- 系统仍负责 schema 校验、预算控制和检索执行。
- `EvidenceGate` 与 `CitationVerify` 不可绕过。
- 默认关闭所有 Agent 开关，保证旧行为稳定。

TaskGraph query progress 会显示 6 个粗粒度阶段：

```text
Query progress:
[1/6] 问题分析 ... done
[2/6] 任务路由 ... done
[3/6] 检索 ... done
[4/6] 证据校验 ... done
[5/6] 局部重检 ... skipped
[6/6] 内容生成 ... done
```

如果触发局部重检，会显示类似 `[5/6] 局部重检 ... retry 1`。完整 6 阶段进度目前只覆盖 `TASKGRAPH_ENABLED=true` 的 TaskGraph 查询路径。

消融实验链路说明：

- 两个开关都开启：保持默认 TaskGraph 行为，`rerank -> evidence_gate -> local_retry 或 build_prompt`。
- `TASKGRAPH_EVIDENCE_GATE_ENABLED=false` 且 `TASKGRAPH_LOCAL_RETRY_ENABLED=true`：跳过证据校验，`rerank -> local_retry -> retrieve_fanout -> rrf -> relationship_expand -> rerank -> build_prompt`，最多按 retry 预算受控执行，避免无限循环。
- `TASKGRAPH_EVIDENCE_GATE_ENABLED=true` 且 `TASKGRAPH_LOCAL_RETRY_ENABLED=false`：正常执行证据校验，但不执行局部重检，EvidenceGate 建议 retry 时直接进入 `build_prompt`，便于比较无重检效果。
- 两个开关都关闭：`question_analyze -> task_router -> retrieve_fanout -> rrf -> relationship_expand -> rerank -> build_prompt -> generate_answer -> citation_verify -> finalize`。

证据充分性评分当前已拆成可配置权重，便于做消融实验：

- `TG_SUPPORT_W_TOP_HIT`
- `TG_SUPPORT_W_AVG_TOP`
- `TG_SUPPORT_W_BM25_VECTOR`
- `TG_SUPPORT_W_RERANK`
- `TG_SUPPORT_W_SOURCE_DIVERSITY`
- `TG_SUPPORT_W_SLOT_COVERAGE`
- `TG_SUPPORT_W_KEYWORD`

建议默认保持 `TG_SUPPORT_W_KEYWORD` 最低，只把它作为辅助项。

`support_score` 现在使用归一化后的同量纲特征计算。raw RRF/BM25/vector/rerank 分数会保留在 debug 中，但不会直接混算：

- `support_features.top_hit_score`：归一化后的最佳命中质量，优先使用 rerank/vector 分数，否则按 rank quality 归一化 RRF。
- `support_features.avg_top_score`：top hits 的归一化平均质量。
- `support_features.score_consistency`：BM25/vector 通道一致性，默认按 node/source overlap 计算。
- `support_features.rerank_top_score`：可用 rerank 分数；rerank 不可用时默认不计入分母。
- `support_features.source_diversity`：有效来源多样性；普通介绍类问题默认不因单文档命中扣分。
- `support_features.slot_coverage_ratio`：hard slot 覆盖率，默认不包含 keyword。
- `support_features.keyword_coverage`：关键词覆盖率，低权重辅助信号。

CLI debug 会输出：

```text
- support_features.top_hit_score=0.8123
- support_features.avg_top_score=0.7442
- support_features.score_consistency=0.5000
- support_feature_weights.top_hit_score=0.3000
- support_feature_contributions.top_hit_score=0.2437
```

### 3.8 阶段日志观测配置（新增）

- `ENABLE_STAGE_LOG=true|false`
  - 是否开启 ingestion 阶段结构化日志。关闭时不会输出阶段日志。
- `LOG_LEVEL=DEBUG|INFO|WARNING|ERROR`
  - 日志级别过滤；建议开发环境 `DEBUG/INFO`，生产 `INFO/WARNING`。
- `LOG_FORMAT=json|text`
  - 日志格式。`json` 便于日志平台检索与聚合；`text` 便于本地阅读。
- `LOG_FILE_PATH=`
  - 日志输出文件路径。为空时输出到控制台（stdout/stderr）。
- `LOG_INCLUDE_PAYLOAD_STATS=true|false`
  - 是否输出 payload 统计信息（如 upsert 批次的 modality 分布）。
- `LOG_SLOW_STAGE_MS=2000`
  - 慢阶段阈值（毫秒）。超过该值会额外输出 warning 日志。
- `ENABLE_CONSOLE_PROGRESS=true|false`
  - 是否开启控制台实时进度输出（面向人工观察的 text 进度行）。
- `CONSOLE_PROGRESS_MIN_INTERVAL_MS=150`
  - 控制台进度输出最小间隔（毫秒），用于节流避免刷屏。
- `CONSOLE_SHOW_STAGE_DONE=true|false`
  - 是否输出每个阶段完成提示（`status=done`）。
- `CONSOLE_SHOW_BATCH_PROGRESS=true|false`
  - 是否输出 embedding/upsert 等批次进度事件。

---

## 4. 启动与运行

### 4.0 启动 Qdrant

如果已有 `qdrant` 容器：

```powershell
docker start qdrant
Invoke-WebRequest http://localhost:6333/collections
```

如果还没有容器，请参考第 10 节创建。

### 4.1 legacy 模式（兼容原流程）

```powershell
# .env
# INGESTION_ENGINE=legacy
# MULTIMODAL_ENABLED=false

py -3.11 -m agentic_rag.cli.build_index --docs data/demo_docs
py -3.11 -m agentic_rag.cli.query "这个项目支持哪些检索过滤能力？"
```

### 4.2 multimodal 模式

```powershell
# .env
# INGESTION_ENGINE=multimodal
# MULTIMODAL_ENABLED=true
# EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
# EMBEDDING_MODEL=text-embedding-v4
# EMBEDDING_DIMENSIONS=1024
# EMBEDDING_BATCH_SIZE=10

py -3.11 -m agentic_rag.cli.build_index --docs data/demo_docs --json
py -3.11 -m agentic_rag.cli.query "这个项目支持哪些检索过滤能力？" --json
```

> `build_index` 输出里会包含 `failed_files`，用于观察多模态解析失败文件数。
> `query --json` 会输出完整 `debug`，适合脚本和回归检查。

已验证的 demo 运行结果：

- `build_index`：`chunks=142`、`vectors=142`、`upserted=142`、`failed_files=0`、`vector_size=1024`
- `query`：可返回答案与引用，`evidence_ok=true`、`citation_ok=true`

普通 `query` 输出在 `TASKGRAPH_ENABLED=true` 时会额外展示 `TaskGraph Debug` 摘要，例如：

```text
TaskGraph Debug:
- route=text_first
- executed_channels=vector,bm25
- retry_count=1
- gate_decision=pass
- evidence_ok=true
- support_level=strong
- support_score=0.8123
- retry_actions=[rewrite_query,increase_top_k]
- citation_ok=true
```

如果需要查看完整 `retry_history`、`slot_coverage`、`source_coverage` 等字段，请使用 `--json`。

### 4.3 Agent 调用示例

```powershell
py -3.11 -m agentic_rag.cli.agent_demo "这个项目支持哪些检索过滤能力？"
```

---

## 5. 多模态 Node 统一结构

项目已落地统一 `Node`（`src/agentic_rag/ingestion/node_schema.py`）：

- `node_id`
- `modality: text|image|table|formula`
- `text`
- `image_path`
- `table_markdown`
- `formula_latex`
- `metadata`（source/doc_id/page/chunk_index/title/section/modality/parser_name）
- `relationships`

Qdrant payload 映射包含：

- `text`
- `image_path`
- `table_markdown`
- `formula_latex`
- `relationships`
- metadata 扁平字段：`source/doc_id/page/chunk_index/title/section/modality/parser_name`
- 同时保留 `metadata` 嵌套对象用于兼容

---

## 6. 多模态处理流程

### 6.1 文本

- LlamaIndex 文本解析
- 按 `TEXT_CHUNK_PARSER` 切块
- Node 规范化
- text embedding
- upsert

### 6.2 图片

- 提取 metadata
- 生成 image node（保留 `image_path`）
- `direct` 模式：优先调用专用 image embedding provider
- 失败时按配置回退 `caption_text`
- upsert

### 6.3 PDF

- 若 `ENABLE_MINERU=true`，优先走 MinerU 云端解析
- MinerU 失败且 `MINERU_FALLBACK_TO_EXISTING=true` 时，回退到原有路由
- `PDF_PARSER=llamaparse` 时先走 LlamaParse
- 失败且 `LLAMAPARSE_FALLBACK_TO_UNSTRUCTURED=true` 时回退 Unstructured
- 元素分流 text/table/image
- 从 Markdown/LaTeX 片段中识别公式并生成 formula node
- Node 构造并入库

### 6.4 表格

- 解析为 markdown
- 小表整体 chunk
- 大表按行组 chunk
- table node(modality=table) -> text embedding -> upsert to table named vector

### 6.5 公式

- 识别 `$$...$$`、`\[...\]`、`\(...\)`、LaTeX 环境与 MathML
- 生成 formula node，保留 `formula_latex`
- formula node 复用文本 embedding 入库，可被向量/BM25 检索召回
- TaskGraph 对 formula evidence 做了专门兼容：当检索结果已包含 `modality=formula` 或 `formula_latex` 时，不会因为中文问题与 LaTeX 字符串关键词不重合而误判 `low_keyword_coverage`

公式识别烟测：

```powershell
py -3.11 -m pytest -q tests/test_formula_extractor.py `
  tests/test_prompt_builder.py::test_prompt_context_uses_formula_latex `
  tests/test_multimodal_image_embedding.py::test_formula_nodes_use_formula_latex_for_embedding `
  tests/test_evidence_gate.py::test_formula_evidence_does_not_fail_keyword_coverage
```

已验证可识别并入库：

- `$E=mc^2$`
- `$$\frac{a}{b}=c$$`
- `\begin{equation}x^2+y^2=z^2\end{equation}`

---

## 7. 测试

```powershell
py -3.11 -m pytest -q
```

当前覆盖包含离线单元测试和 TaskGraph 规则链路测试；默认不会访问真实 Qdrant、LLM 或外部网络。

当前覆盖：

- 既有：
  - chunker 基本行为
  - rerank 失败回退
  - prompt 引用拼装
- 新增：
  - 多模态 Node 规范化
  - PDF 解析回退
  - 表格 chunk 规则（小表/大表）
  - 文本 chunk 策略切换（sentence/markdown/hierarchical）
  - LaTeX/MathML 公式识别、公式节点入库、公式上下文引用
  - TaskGraph 公式证据门控兼容

### 7.1 评测集与回归指标

PR-7 新增了轻量离线评测框架，用于基于 `RAGResult.answer / citations / debug`
检查 RAG 输出是否满足固定预期。默认评测不调用真实 Qdrant/LLM，适合纳入回归测试。

固定样例位于：

```text
tests/eval_cases/taskgraph_eval_cases.jsonl
```

当前覆盖类别：

- `text_qa`：普通文本问答
- `table_qa`：表格问答
- `page_qa`：页级问答
- `cross_page_qa`：跨页上下文
- `cross_doc_qa`：跨文档综合/冲突
- `no_answer_refusal`：无答案拒答

离线评测需要传入已有结果 JSONL，每条记录可使用：

```json
{"case_id":"text_taskgraph_intro","result":{"answer":"...","citations":[],"debug":{}}}
```

运行离线评测：

```powershell
py -3.11 -m agentic_rag.cli.eval `
  --cases tests/eval_cases/taskgraph_eval_cases.jsonl `
  --results results.jsonl `
  --json
```

如需真实端到端评测，显式使用 `--live`。该模式会使用当前 `.env` 中的
Qdrant、Embedding 和 LLM 配置，可能访问外部服务：

```powershell
py -3.11 -m agentic_rag.cli.eval `
  --cases tests/eval_cases/taskgraph_eval_cases.jsonl `
  --live `
  --json
```

pytest 已注册 `live_eval` marker。真实 live eval 测试应保持显式 opt-in，例如：

```powershell
$env:RUN_LIVE_EVAL="true"
py -3.11 -m pytest -q -m live_eval
```

---

## 8. 常见问题

### 8.1 `ModuleNotFoundError: No module named 'agentic_rag'`

执行：

```powershell
py -3.11 -m pip install -e .
```

### 8.2 `embedding_dimensions ... unable to parse string as an integer`

`EMBEDDING_DIMENSIONS=` 留空即可，代码会自动转 `None`。

如果使用百炼 `text-embedding-v4`，建议显式配置：

```env
EMBEDDING_DIMENSIONS=1024
```

### 8.3 `QdrantClient object has no attribute search`

已兼容新旧 API（`query_points`/`search`）。

### 8.4 LlamaParse 失败

检查：

- `LLAMA_CLOUD_API_KEY` 是否有效
- 网络是否可访问 Llama Cloud
- 是否开启 `LLAMAPARSE_FALLBACK_TO_UNSTRUCTURED=true`

### 8.5 Rerank 失败

不会中断主流程，会自动回退到未 rerank 结果。
`qwen3-rerank` / `qwen3-vl-rerank` 应设置 `RERANK_PROVIDER=dashscope`。如果 debug 中 `used_rerank=false`，优先查看 `rerank_fallback_reason`，常见原因是未安装 `dashscope`、API Key 未配置或模型与 `RERANK_ENABLE_MULTIMODAL` 不匹配。

### 8.6 Embedding 返回 `401 无效的令牌`

优先检查 API Key 和 `BASE_URL` 是否属于同一个平台：

- 百炼/DashScope Key：`EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`
- OpenAI Key：`EMBEDDING_BASE_URL=https://api.openai.com/v1`
- apirouter Key：`EMBEDDING_BASE_URL=https://apirouter.ai/v1`

常见误配是“百炼 Key + apirouter URL”，服务端会直接返回 401。

### 8.7 DashScope embedding 批量大小报错

如果看到：

```text
batch size is invalid, it should not be larger than 10
```

将批量大小调小：

```env
EMBEDDING_BATCH_SIZE=10
```

### 8.8 PDF 中文解析效果一般

`pdfminer` 对中文文档（尤其复杂版式、双栏、跨页表格、扫描件 OCR 场景）通常不够友好。生产环境建议优先使用 `LlamaParse` 作为 PDF 主解析器；对于更高复杂度多模态文档，后续可评估 `MinerU`。

### 8.9 MinerU 接入失败排查

- `MINERU_MODE=precise` 时请确认 `MINERU_API_TOKEN` 已填写且有效。
- 检查服务地址：`MINERU_BASE_URL` 是否可访问。
- 若日志中出现 `A0202/A0211`，通常是 token 无效或过期。
- 若出现超限或队列错误（如 `-30001/-30003/-60009`），建议开启回退：`MINERU_FALLBACK_TO_EXISTING=true`。
- 如果任务状态已 `done`，但下载 `cdn-mineru...zip` 超时，可设置：

```env
MINERU_DOWNLOAD_WAIT_FOREVER=true
MINERU_DOWNLOAD_RETRY_INTERVAL_SEC=10
```

- 如果希望 MinerU 长任务一直等待到完成，可设置：

```env
MINERU_POLL_WAIT_FOREVER=true
```

- 注意：无限等待适合本地构建和重要文档解析；生产批处理建议仍设置有限超时，避免单个文件永久阻塞。

### 8.10 `No module named 'unstructured_inference'`

说明：Unstructured 回退解析 PDF/复杂文档时需要该依赖。  
修复：

```powershell
py -3.11 -m pip install unstructured-inference
```

或直接重装完整依赖：

```powershell
py -3.11 -m pip install -r requirements.txt
```

### 8.11 Embedding 8192 tokens 超限

当前版本已加入两层保护：

- embedding 前 token 硬分片（文本按段落/句群，表格按行组）
- provider 返回 input length 超限时自动再分片并仅重试失败条目

可调参数：

- `EMBEDDING_INPUT_MAX_TOKENS=8192`
- `EMBEDDING_INPUT_SAFETY_MARGIN_TOKENS=256`

### 8.12 公式识别误召回 Shell/命令表达式

当前公式识别是启发式规则，会识别包含 `= + - * / ^ _ < >`、LaTeX 命令、MathML 或公式环境的片段。Linux 命令文档中类似下面的表达式可能被误识别为 formula：

```text
-name "*.c" -o -name "*.h"
```

这不影响 LaTeX 公式链路，但如果生产数据里命令表达式很多，建议进一步收紧 `formula_extractor.py` 的规则，或对特定文档类型关闭 `ENABLE_FORMULA_RECOGNITION`。

---

## 9. 架构扩展建议（下一步）

- Qdrant Named Vectors：PR-6 后支持 text/image/table 分开向量空间
- 跨模态融合检索：向量召回 + 关键词召回 + RRF 融合 (已完成)
- 图谱化 relationships：支持父子块、页面顺序、章节层级检索 (已完成)
- Qdrant Named Vectors：PR-6 后支持按 text/table/image 通道独立召回并参与 RRF 融合

---

## 10. Qdrant 本地 Docker 安装与启动

### 10.1 前置说明

- 需本机已安装 Docker Desktop（Windows/macOS）或 Docker Engine（Linux）。
- 项目默认连接地址：`QDRANT_URL=http://localhost:6333`。

### 10.2 拉取镜像

```powershell
docker pull qdrant/qdrant:latest
```

### 10.3 启动容器（含持久化卷与端口）

```powershell
docker run -d --name qdrant `
  -p 6333:6333 -p 6334:6334 `
  -v qdrant_storage:/qdrant/storage `
  qdrant/qdrant:latest
```

### 10.4 查看状态与日志

```powershell
docker ps
docker logs qdrant --tail 100
```

### 10.5 停止 / 重启 / 删除容器

```powershell
docker stop qdrant
docker start qdrant
docker rm -f qdrant
```

### 10.6 健康检查

```powershell
Invoke-WebRequest http://localhost:6333/collections
```

返回 200 且包含 JSON（如 `{"result":{"collections":[]},...}`）表示服务可用。

### 10.7 `[WinError 10061]` 排查步骤

当出现 `connection refused` / `WinError 10061` 时，按顺序检查：

1. `docker ps` 是否有 `qdrant` 且状态为 `Up`。
2. 端口映射是否正确：`0.0.0.0:6333->6333`。
3. 本机端口连通：`Test-NetConnection localhost -Port 6333`。
4. `.env` 的 `QDRANT_URL` 是否与实际地址一致。
5. 容器日志是否异常：`docker logs qdrant --tail 200`。
