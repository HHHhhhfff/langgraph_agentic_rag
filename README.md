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
- 若启用 LlamaParse：准备 `LLAMA_CLOUD_API_KEY`

### 安装

```powershell
cd d:\Agentic_RAG\langgraph_agentic_rag
py -3.11 -m venv .venv
.\.venv\Scripts\activate
py -3.11 -m pip install -U pip
py -3.11 -m pip install -r requirements.txt
py -3.11 -m pip install -e .
```

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
- `MINERU_ENABLE_TABLE=true|false`
  - 是否开启表格解析
- `MINERU_ENABLE_FORMULA=true|false`
  - 是否开启公式解析
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

### 3.3 图像向量配置（新增）

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

`IMAGE_EMBED_MODE` 与上述配置关系：
- 当 `IMAGE_EMBED_MODE=direct`：
  - 使用 `IMAGE_EMBED_*` 配置请求图像向量。
  - 若失败且 `IMAGE_EMBED_FALLBACK_TO_CAPTION=true`，自动回退到 caption 文本 embedding。
- 当 `IMAGE_EMBED_MODE=caption_text`：
  - 不调用图像向量接口，直接走文本 embedding（保留兼容行为）。

### 3.4 现有配置仍生效

- Qdrant：`QDRANT_*`
- Embedding：`EMBEDDING_*`
- Token 保护：
  - `EMBEDDING_INPUT_MAX_TOKENS`（默认 `8192`）
  - `EMBEDDING_INPUT_SAFETY_MARGIN_TOKENS`（默认 `256`）
- Rerank：`RERANK_*`
- LLM：`LLM_*`
- 检索：`RETRIEVAL_*` / `CONTEXT_TOP_N` / `PROMPT_MAX_CONTEXT_CHARS`

### 3.5 阶段日志观测配置（新增）

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
# PDF_PARSER=llamaparse
# LLAMAPARSE_FALLBACK_TO_UNSTRUCTURED=true

py -3.11 -m agentic_rag.cli.build_index --docs data/demo_docs
py -3.11 -m agentic_rag.cli.query "这个项目支持哪些检索过滤能力？"
```

> `build_index` 输出里会包含 `failed_files`，用于观察多模态解析失败文件数。

### 4.3 Agent 调用示例

```powershell
py -3.11 -m agentic_rag.cli.agent_demo "这个项目支持哪些检索过滤能力？"
```

---

## 5. 多模态 Node 统一结构

项目已落地统一 `Node`（`src/agentic_rag/ingestion/node_schema.py`）：

- `node_id`
- `modality: text|image|table`
- `text`
- `image_path`
- `table_markdown`
- `metadata`（source/doc_id/page/chunk_index/title/section/modality/parser_name）
- `relationships`

Qdrant payload 映射包含：

- `text`
- `image_path`
- `table_markdown`
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
- Node 构造并入库

### 6.4 表格

- 解析为 markdown
- 小表整体 chunk
- 大表按行组 chunk
- table node -> text embedding -> upsert

---

## 7. 测试

```powershell
py -3.11 -m pytest -q
```

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

---

## 8. 常见问题

### 8.1 `ModuleNotFoundError: No module named 'agentic_rag'`

执行：

```powershell
py -3.11 -m pip install -e .
```

### 8.2 `embedding_dimensions ... unable to parse string as an integer`

`EMBEDDING_DIMENSIONS=` 留空即可，代码会自动转 `None`。

### 8.3 `QdrantClient object has no attribute search`

已兼容新旧 API（`query_points`/`search`）。

### 8.4 LlamaParse 失败

检查：

- `LLAMA_CLOUD_API_KEY` 是否有效
- 网络是否可访问 Llama Cloud
- 是否开启 `LLAMAPARSE_FALLBACK_TO_UNSTRUCTURED=true`

### 8.5 Rerank 失败

不会中断主流程，会自动回退到未 rerank 结果。

### 8.6 PDF 中文解析效果一般

`pdfminer` 对中文文档（尤其复杂版式、双栏、跨页表格、扫描件 OCR 场景）通常不够友好。生产环境建议优先使用 `LlamaParse` 作为 PDF 主解析器；对于更高复杂度多模态文档，后续可评估 `MinerU`。

### 8.7 MinerU 接入失败排查

- `MINERU_MODE=precise` 时请确认 `MINERU_API_TOKEN` 已填写且有效。
- 检查服务地址：`MINERU_BASE_URL` 是否可访问。
- 若日志中出现 `A0202/A0211`，通常是 token 无效或过期。
- 若出现超限或队列错误（如 `-30001/-30003/-60009`），建议开启回退：`MINERU_FALLBACK_TO_EXISTING=true`。

### 8.8 `No module named 'unstructured_inference'`

说明：Unstructured 回退解析 PDF/复杂文档时需要该依赖。  
修复：

```powershell
py -3.11 -m pip install unstructured-inference
```

或直接重装完整依赖：

```powershell
py -3.11 -m pip install -r requirements.txt
```

### 8.9 Embedding 8192 tokens 超限

当前版本已加入两层保护：

- embedding 前 token 硬分片（文本按段落/句群，表格按行组）
- provider 返回 input length 超限时自动再分片并仅重试失败条目

可调参数：

- `EMBEDDING_INPUT_MAX_TOKENS=8192`
- `EMBEDDING_INPUT_SAFETY_MARGIN_TOKENS=256`

---

## 9. 架构扩展建议（下一步）

- Qdrant Named Vectors：为 text/image/table 分开向量空间
- 跨模态融合检索：向量召回 + 关键词召回 + RRF 融合
- 图谱化 relationships：支持父子块、页面顺序、章节层级检索
- Qdrant Named Vectors：为 text/image/table 分离向量字段并支持混合打分

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
