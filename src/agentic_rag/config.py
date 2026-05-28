from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Qdrant connection settings
    qdrant_url: str = Field(default="http://localhost:6333", description="Qdrant HTTP URL")
    qdrant_api_key: str | None = Field(default=None, description="Optional Qdrant API key")
    qdrant_collection: str = Field(default="agentic_rag_docs", description="Target Qdrant collection name")
    qdrant_distance: Literal["cosine", "dot", "euclid"] = Field(
        default="cosine", description="Vector distance metric"
    )
    qdrant_recreate_collection: bool = Field(
        default=False,
        description="When true, force recreate collection during indexing",
    )
    qdrant_timeout_sec: float = Field(default=30.0, description="Qdrant request timeout in seconds")

    # Embedding provider settings
    embedding_provider_type: Literal["openai_compatible", "dashscope_multimodal"] = Field(
        default="openai_compatible",
        description="Embedding provider backend",
    )
    embedding_base_url: str = Field(
        default="https://api.openai.com/v1", description="Embedding API base URL"
    )
    embedding_api_key: str = Field(default="", description="Embedding API key")
    embedding_model: str = Field(
        default="text-embedding-3-small",
        description="Embedding model, e.g. text-embedding-3-small / qwen3-vl-embedding",
    )
    embedding_dimensions: int | None = Field(
        default=None,
        description="Optional embedding dimension override for compatible models",
    )
    vector_mismatch_strategy: Literal["truncate", "error"] = Field(
        default="truncate",
        description="When returned embedding dims > target dims: truncate or raise error",
    )
    embedding_batch_size: int = Field(default=16, description="Batch size for embedding requests")
    embedding_timeout_sec: float = Field(default=60.0, description="Embedding request timeout")
    embedding_max_retries: int = Field(default=3, description="Embedding retry count")
    embedding_input_max_tokens: int = Field(
        default=8192,
        description="Max input tokens accepted by embedding endpoint",
    )
    embedding_input_safety_margin_tokens: int = Field(
        default=256,
        description="Safety margin tokens reserved under embedding max input limit",
    )

    # Rerank provider settings
    rerank_enabled: bool = Field(default=True, description="Enable rerank stage")
    rerank_provider: Literal["", "dashscope", "openai_compatible"] = Field(
        default="dashscope",
        description="Rerank backend provider. Empty means auto-detect from model name.",
    )
    rerank_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        description="Rerank API base URL",
    )
    rerank_api_key: str = Field(default="", description="Rerank API key")
    rerank_model: str = Field(
        default="qwen3-rerank", description="Rerank model, e.g. qwen3-rerank"
    )
    rerank_top_n: int = Field(default=8, description="How many reranked chunks to keep")
    rerank_timeout_sec: float = Field(default=30.0, description="Rerank request timeout")
    rerank_max_retries: int = Field(default=2, description="Rerank retry count")
    rerank_return_documents: bool = Field(default=True, description="Ask rerank provider to return documents")
    rerank_enable_multimodal: bool = Field(default=False, description="Use multimodal DashScope rerank payloads")
    rerank_instruct: str = Field(
        default="Given a web search query, retrieve relevant passages that answer the query.",
        description="Optional DashScope qwen3-rerank instruction",
    )

    # LLM generation provider settings (OpenAI-compatible)
    llm_base_url: str = Field(default="https://api.openai.com/v1", description="LLM API base URL")
    llm_api_key: str = Field(default="", description="LLM API key")
    llm_model: str = Field(default="gpt-4.1-mini", description="LLM model name")
    llm_temperature: float = Field(default=0.1, description="Generation temperature")
    llm_max_tokens: int = Field(default=700, description="Max tokens for final answer")
    llm_timeout_sec: float = Field(default=90.0, description="LLM request timeout")
    llm_max_retries: int = Field(default=2, description="LLM retry count")

    # Ingestion and retrieval behavior
    chunk_size: int = Field(default=900, description="Max characters per chunk")
    chunk_overlap: int = Field(default=120, description="Chunk overlap characters")
    chunk_min_length: int = Field(default=80, description="Minimum chunk length")
    ingestion_engine: Literal["legacy", "multimodal"] = Field(
        default="legacy",
        description="Ingestion engine mode: legacy text-only or multimodal",
    )
    multimodal_enabled: bool = Field(default=False, description="Enable multimodal ingestion pipeline")
    pdf_parser: Literal["llamaparse", "unstructured"] = Field(
        default="llamaparse",
        description="Primary PDF parser backend",
    )
    text_chunk_parser: Literal["sentence", "markdown", "hierarchical"] = Field(
        default="sentence",
        description="LlamaIndex text chunk parser strategy",
    )
    llama_cloud_api_key: str = Field(default="", description="Llama Cloud API key for LlamaParse")
    llamaparse_fallback_to_unstructured: bool = Field(
        default=True,
        description="Fallback to Unstructured parser when LlamaParse fails",
    )
    unstructured_strategy: Literal["fast", "hi_res", "auto"] = Field(
        default="auto",
        description="Unstructured parse strategy",
    )
    enable_mineru: bool = Field(default=False, description="Enable MinerU parser in multimodal pipeline")
    mineru_mode: Literal["precise", "agent"] = Field(
        default="precise",
        description="MinerU API mode: precise(token) or agent(lightweight)",
    )
    mineru_base_url: str = Field(default="https://mineru.net", description="MinerU API base URL")
    mineru_api_token: str = Field(default="", description="MinerU token for precise API mode")
    mineru_model_version: Literal["pipeline", "vlm", "MinerU-HTML"] = Field(
        default="vlm",
        description="MinerU model version",
    )
    mineru_poll_interval_sec: int = Field(default=3, description="MinerU poll interval seconds")
    mineru_poll_timeout_sec: int = Field(default=600, description="MinerU poll timeout seconds")
    mineru_poll_wait_forever: bool = Field(
        default=False,
        description="When true, keep polling MinerU task status without applying mineru_poll_timeout_sec",
    )
    mineru_download_wait_forever: bool = Field(
        default=False,
        description="When true, keep retrying MinerU result download until it succeeds",
    )
    mineru_download_max_retries: int = Field(default=3, description="MinerU result download retry count")
    mineru_download_retry_interval_sec: int = Field(
        default=10,
        description="Seconds to wait between MinerU result download retry attempts",
    )
    mineru_enable_table: bool = Field(default=True, description="Enable MinerU table extraction")
    mineru_enable_formula: bool = Field(default=True, description="Enable MinerU formula extraction")
    mineru_table_structured_first: bool = Field(
        default=True,
        description="Prefer structured MinerU table blocks before markdown table fallback",
    )
    mineru_table_markdown_fallback: bool = Field(
        default=True,
        description="Build table nodes from markdown tables not covered by structured MinerU table blocks",
    )
    mineru_table_second_pass_enabled: bool = Field(
        default=False,
        description="Enable legacy second-pass markdown table scan after extract_table_blocks",
    )
    mineru_structured_table_coalesce_enabled: bool = Field(
        default=True,
        description="Coalesce duplicate table blocks that come from multiple MinerU structured JSON sources",
    )
    mineru_structured_table_coalesce_keep_chart: bool = Field(
        default=True,
        description="Keep chart/table-like structured blocks separate during MinerU table coalescing",
    )
    mineru_table_exact_content_coalesce_enabled: bool = Field(
        default=True,
        description="Coalesce final MinerU table nodes with exactly identical table cell content",
    )
    mineru_table_exact_content_coalesce_keep_chart_separate: bool = Field(
        default=False,
        description="Keep chart/table-like table nodes separate during exact table content coalescing",
    )
    enable_formula_recognition: bool = Field(
        default=True,
        description="Extract LaTeX/MathML formulas from parsed text into formula nodes",
    )
    formula_node_min_chars: int = Field(default=8, description="Minimum formula length for standalone formula nodes")
    formula_inline_as_text_only: bool = Field(
        default=True,
        description="Keep inline formulas inside text nodes instead of standalone formula nodes",
    )
    formula_skip_inline_references: bool = Field(default=True, description="Skip citation-like inline formulas")
    formula_skip_superscript_notes: bool = Field(default=True, description="Skip author-note superscript formulas")
    formula_group_display_enabled: bool = Field(default=True, description="Group adjacent display formulas")
    formula_group_max_gap_lines: int = Field(default=2, description="Maximum blank lines between display formulas to group")
    mineru_is_ocr: bool = Field(default=False, description="Enable MinerU OCR")
    mineru_language: str = Field(default="ch", description="MinerU language")
    mineru_page_range: str = Field(default="", description="MinerU page range")
    mineru_extra_formats: str = Field(
        default="",
        description="Comma-separated extra formats for precise mode, e.g. docx,html",
    )
    mineru_fallback_to_existing: bool = Field(
        default=True,
        description="Fallback to existing parsers when MinerU fails",
    )
    mineru_context_link_enabled: bool = Field(
        default=True,
        description="Add explicit prev/next/page/context relationships to MinerU nodes",
    )
    mineru_context_window_chars: int = Field(
        default=800,
        description="Context window size recorded for MinerU table/formula relationship links",
    )
    mineru_same_page_link_max_nodes: int = Field(
        default=30,
        description="Maximum same-page node ids stored in MinerU node relationships",
    )
    enable_image_caption: bool = Field(
        default=True,
        description="Enable generating caption text for image nodes",
    )
    image_embed_mode: Literal["direct", "caption_text"] = Field(
        default="caption_text",
        description="Image embedding mode: direct placeholder or caption text embedding",
    )
    image_embed_provider_type: Literal["openai_compatible", "dashscope_multimodal"] = Field(
        default="openai_compatible",
        description="Image embedding provider backend",
    )
    image_embed_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        description="Image embedding API base URL (OpenAI-Compatible)",
    )
    image_embed_api_key: str = Field(default="", description="Image embedding API key")
    image_embed_model: str = Field(
        default="qwen3-vl-embedding",
        description="Image embedding model name",
    )
    image_embed_timeout_sec: float = Field(default=60.0, description="Image embedding request timeout")
    image_embed_max_retries: int = Field(default=3, description="Image embedding retry count")
    image_embed_batch_size: int = Field(default=8, description="Image embedding batch size")
    image_embed_fallback_to_caption: bool = Field(
        default=True,
        description="Fallback to caption text embedding when direct image embedding fails",
    )
    image_embed_require_file_exists: bool = Field(
        default=True,
        description="Validate image file existence before image embedding request",
    )
    dashscope_api_key: str = Field(default="", description="DashScope API key for native SDK providers")
    dashscope_embedding_model: str = Field(
        default="qwen3-vl-embedding",
        description="DashScope native multimodal embedding model",
    )
    dashscope_embedding_dimension: int | None = Field(
        default=None,
        description="Optional DashScope multimodal embedding dimension",
    )
    image_enrichment_enabled: bool = Field(
        default=False,
        description="Enable optional pure-image enrichment nodes for caption/OCR/object evidence",
    )
    image_mineru_enrich_enabled: bool = Field(
        default=False,
        description="Use MinerU as an additional pure-image enrichment source",
    )
    image_vlm_caption_enabled: bool = Field(
        default=False,
        description="Use a VLM to generate image caption/object descriptions",
    )
    image_vlm_provider: Literal["openai_compatible", "dashscope_sdk"] = Field(
        default="openai_compatible",
        description="Image VLM provider backend",
    )
    image_vlm_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        description="Image VLM OpenAI-compatible API base URL",
    )
    image_vlm_dashscope_api_url: str = Field(
        default="https://dashscope.aliyuncs.com/api/v1",
        description="Image VLM DashScope SDK API URL",
    )
    image_vlm_api_key: str = Field(default="", description="Image VLM API key")
    image_vlm_model: str = Field(default="qwen3-vl-plus", description="Image VLM model name")
    image_vlm_timeout_sec: float = Field(default=60.0, description="Image VLM request timeout")
    image_vlm_max_retries: int = Field(default=2, description="Image VLM retry count")
    image_vlm_enable_thinking: bool = Field(
        default=False,
        description="Pass enable_thinking to compatible image VLM providers",
    )
    image_object_max_items: int = Field(default=12, description="Maximum object descriptions per image")
    image_caption_max_chars: int = Field(default=1200, description="Maximum stored image caption length")
    image_ocr_max_chars: int = Field(default=4000, description="Maximum stored image OCR text length")
    ingestion_timeout_sec: float = Field(default=120.0, description="Multimodal ingestion per-file timeout")
    ingestion_max_retries: int = Field(default=2, description="Multimodal ingestion retry count")
    ingestion_batch_size: int = Field(default=32, description="Multimodal upsert batch size")
    ingestion_inspect_output_dir: str = Field(
        default="storage/ingestion_visualization",
        description="Output directory for inspect-only ingestion visualization reports",
    )
    ingestion_inspect_max_text_chars: int = Field(
        default=4000,
        description="Max text chars stored per node in ingestion inspection reports",
    )
    ingestion_inspect_include_mineru_raw: bool = Field(
        default=True,
        description="Save MinerU raw markdown and structured content in ingestion inspection reports",
    )
    ingestion_inspect_render_pdf_pages: bool = Field(
        default=False,
        description="Render PDF pages in ingestion inspection reports when PyMuPDF is installed",
    )
    ingestion_inspect_include_embedding_preview: bool = Field(
        default=True,
        description="Include embedding text/vector metadata preview without full vectors",
    )
    ingestion_inspect_include_full_vectors: bool = Field(
        default=False,
        description="Reserved switch for full vector export; defaults to false to avoid large reports",
    )
    ingestion_inspect_write_qdrant: bool = Field(
        default=False,
        description="When true, inspect_ingestion writes inspected nodes to Qdrant after report generation",
    )
    ingestion_inspect_write_local_index: bool = Field(
        default=True,
        description="When inspect writes Qdrant, also persist local retrieval indexes",
    )
    ingestion_inspect_recreate_collection: bool = Field(
        default=False,
        description="When inspect writes Qdrant, recreate the target collection before upsert",
    )
    ingestion_inspect_sync_build_index_logs: bool = Field(
        default=True,
        description="Emit build_index-compatible logs during inspect write stage",
    )
    enable_stage_log: bool = Field(default=False, description="Enable structured stage logging")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Stage log level",
    )
    log_format: Literal["json", "text"] = Field(
        default="json",
        description="Stage log output format",
    )
    log_file_path: str = Field(default="", description="Optional stage log output file path")
    log_include_payload_stats: bool = Field(
        default=True,
        description="Include payload statistics in stage logs",
    )
    log_slow_stage_ms: int = Field(default=2000, description="Warn when stage latency exceeds threshold")
    enable_console_progress: bool = Field(
        default=False,
        description="Enable human-readable console progress lines for ingestion stages",
    )
    console_progress_min_interval_ms: int = Field(
        default=150,
        description="Minimum interval between console progress lines (ms)",
    )
    console_show_stage_done: bool = Field(
        default=True,
        description="Whether to print stage done events in console progress",
    )
    console_show_batch_progress: bool = Field(
        default=True,
        description="Whether to print batch-level progress events in console progress",
    )
    retrieval_eval_log_enabled: bool = Field(default=False, description="Append retrieval history JSONL for later eval")
    retrieval_eval_log_dir: str = Field(default="storage/retrieval_eval", description="Retrieval eval log directory")
    retrieval_eval_log_file: str = Field(default="retrieval_history.jsonl", description="Retrieval eval JSONL file name")
    retrieval_eval_max_text_chars: int = Field(default=2000, description="Max chars stored per retrieved hit text")
    retrieval_vis_output_dir: str = Field(
        default="storage/retrieval_visualization",
        description="Retrieval visualization output directory",
    )
    retrieval_vis_max_text_chars: int = Field(default=4000, description="Max chars shown per retrieval hit")
    retrieval_vis_include_full_vectors: bool = Field(
        default=False,
        description="Include full vector-like fields in retrieval visualization raw records",
    )
    retrieval_vis_auto_write: bool = Field(
        default=False,
        description="Automatically write retrieval visualization after each TaskGraph query",
    )
    query_progress_enabled: bool = Field(default=True, description="Show coarse TaskGraph query progress in CLI")
    query_progress_style: Literal["plain"] = Field(default="plain", description="Query progress output style")
    query_progress_show_retry: bool = Field(default=True, description="Show local retry events in query progress")
    retrieval_top_k: int = Field(default=12, description="Vector retrieval top-k")
    retrieval_min_score: float = Field(default=0.15, description="Minimum accepted vector score")
    retrieval_filter_fallback: bool = Field(
        default=True,
        description="Retry retrieval without metadata filter when filtered results are empty",
    )
    bm25_enabled: bool = Field(default=True, description="Enable BM25 keyword retrieval")
    bm25_top_k: int = Field(default=12, description="BM25 retrieval top-k")
    page_top_k: int = Field(default=8, description="Page-level retrieval top-k")
    table_top_k: int = Field(default=8, description="Table retrieval top-k")
    retrieval_index_dir: str = Field(
        default="storage/retrieval_indexes",
        description="Local directory for persisted BM25/page/table retrieval indexes",
    )
    retrieval_index_persist_enabled: bool = Field(
        default=True,
        description="Persist BM25/page/table retrieval indexes during index build",
    )
    retrieval_index_fallback_to_scroll: bool = Field(
        default=True,
        description="Fallback to Qdrant scroll when local retrieval index is missing or invalid",
    )
    rrf_k: int = Field(default=60, description="RRF smoothing parameter")
    rrf_top_k: int = Field(default=12, description="RRF fused top-k")
    rel_expand_steps: int = Field(default=1, description="Relationship expansion steps")
    rel_expand_pages: int = Field(default=1, description="Page expansion window")
    rel_expand_explicit_relationships: bool = Field(
        default=True,
        description="Expand retrieval hits using explicit node relationship fields",
    )
    rel_expand_same_page_relationships: bool = Field(
        default=True,
        description="Expand retrieval hits using same_page_node_ids relationship fields",
    )
    retrieval_score_vector_weight: float = Field(default=0.30, description="Composite score vector weight")
    retrieval_score_bm25_weight: float = Field(default=0.10, description="Composite score BM25 weight")
    retrieval_score_rrf_weight: float = Field(default=0.25, description="Composite score RRF weight")
    retrieval_score_rerank_weight: float = Field(default=0.35, description="Composite score rerank weight")
    retrieval_score_relationship_weight: float = Field(default=1.0, description="Composite score relationship weight")
    retrieval_score_agent_relevance_weight: float = Field(default=0.20, description="Composite score agent relevance weight")
    retrieval_initial_min_composite_score: float = Field(default=0.0, description="Initial retrieval composite threshold")
    retrieval_rerank_min_composite_score: float = Field(default=0.0, description="Rerank composite threshold")
    retrieval_final_min_composite_score: float = Field(default=0.20, description="Final retrieval composite threshold")
    retrieval_rerank_require_prior_score: bool = Field(
        default=True,
        description="Penalize reranked hits whose prior composite score is too low",
    )
    retrieval_rerank_prior_min_composite_score: float = Field(default=0.15, description="Low prior score cutoff")
    retrieval_rerank_prior_low_score_penalty: float = Field(default=0.70, description="Low prior rerank penalty")
    rel_expand_related_modality_enabled: bool = Field(default=True, description="Expand related table/formula nodes")
    rel_expand_min_seed_composite_score: float = Field(default=0.30, description="Min seed score for related modality expansion")
    rel_expand_seed_top_m: int = Field(default=6, description="Max ranked seeds for related modality expansion")
    rel_expand_max_related_tables: int = Field(default=5, description="Max related tables to add")
    rel_expand_max_related_formulas: int = Field(default=5, description="Max related formulas to add")
    rel_expand_related_table_weight: float = Field(default=0.90, description="Related table inherited score weight")
    rel_expand_related_formula_weight: float = Field(default=0.85, description="Related formula inherited score weight")
    rel_expand_context_text_enabled: bool = Field(default=True, description="Expand high-score context text nodes")
    rel_expand_context_text_weight: float = Field(default=0.60, description="Context text inherited score weight")
    rel_expand_page_window_weight: float = Field(default=0.30, description="Page-window inherited score weight")
    rel_expand_same_page_weight: float = Field(default=0.30, description="Same-page inherited score weight")
    rel_expand_prev_next_weight: float = Field(default=0.50, description="Prev/next inherited score weight")
    rel_expand_parent_child_weight: float = Field(default=0.60, description="Parent/child inherited score weight")
    rel_expand_doc_adjacent_weight: float = Field(default=0.50, description="Document adjacent inherited score weight")
    rel_expand_context_text_min_seed_score: float = Field(default=0.45, description="Min seed score for context expansion")
    rel_expand_context_text_seed_top_m: int = Field(default=4, description="Max ranked seeds for context expansion")
    rel_expand_context_text_max_per_seed: int = Field(default=2, description="Max context text nodes per seed")
    retrieval_related_evidence_max_total: int = Field(default=8, description="Max protected related evidence nodes")
    retrieval_related_evidence_max_per_seed: int = Field(default=3, description="Max protected related evidence per seed")
    rel_expand_routed_min_seed_score: float = Field(default=0.55, description="Min seed score for routed relationship expansion")
    rel_expand_routed_seed_top_m: int = Field(default=3, description="Max ranked seeds for routed relationship expansion")
    rel_expand_routed_max_per_seed: int = Field(default=5, description="Max routed relationship nodes per seed")
    rel_expand_routed_max_total: int = Field(default=20, description="Max routed relationship nodes per query")
    rel_expand_routed_allowed_modalities: str = Field(
        default="text,table,formula",
        description="Comma-separated modalities allowed for routed relationship expansion",
    )
    rel_expand_retry_same_page_enabled: bool = Field(default=True, description="Enable same-page expansion during retry")
    rel_expand_retry_min_seed_score: float = Field(default=0.55, description="Min seed score for retry relationship expansion")
    rel_expand_retry_seed_top_m: int = Field(default=3, description="Max ranked seeds for retry relationship expansion")
    rel_expand_retry_max_per_seed: int = Field(default=4, description="Max retry relationship nodes per seed")
    rel_expand_retry_max_total: int = Field(default=12, description="Max retry relationship nodes per query")
    retrieval_auto_table_channel_enabled: bool = Field(default=True, description="Enable weak keyword-triggered table channel")
    retrieval_auto_formula_channel_enabled: bool = Field(default=True, description="Enable weak keyword-triggered formula channel")
    retrieval_table_trigger_keywords: str = Field(default="表格,列表", description="Comma-separated table trigger keywords")
    retrieval_formula_trigger_keywords: str = Field(default="公式,方程,表达式", description="Comma-separated formula trigger keywords")
    tg_agent_context_expansion_enabled: bool = Field(default=True, description="Enable future agent-guided retry context expansion")
    tg_agent_context_expansion_min_seed_score: float = Field(default=0.55, description="Agent context expansion seed score")
    tg_agent_context_expansion_seed_top_m: int = Field(default=3, description="Agent context expansion max seeds")
    tg_agent_context_expansion_max_rounds: int = Field(default=2, description="Agent context expansion max rounds")
    tg_agent_context_expansion_max_context_hits: int = Field(default=4, description="Agent context expansion max hits")
    tg_agent_chunk_grading_enabled: bool = Field(default=False, description="Enable agent per-chunk relevance grading")
    tg_agent_chunk_grading_mode: str = Field(default="head_tail", description="Chunk grading selection mode: all/head/head_tail/none")
    tg_agent_chunk_grading_head_m: int = Field(default=6, description="Head chunks sent to agent chunk grader")
    tg_agent_chunk_grading_tail_n: int = Field(default=2, description="Tail chunks sent to agent chunk grader")
    tg_agent_chunk_grading_max_chunks: int = Field(default=8, description="Maximum chunks sent to agent chunk grader")
    tg_agent_chunk_labels: str = Field(default="irrelevant,weak,relevant,strong", description="Allowed chunk relevance labels")
    tg_agent_chunk_drop_labels: str = Field(default="irrelevant", description="Labels dropped by agent chunk grading")
    tg_agent_chunk_drop_score_threshold: float = Field(default=0.25, description="Drop chunks below this agent relevance score")
    tg_agent_chunk_drop_enabled: bool = Field(default=True, description="Drop irrelevant chunks after agent grading")
    tg_agent_chunk_score_adjust_enabled: bool = Field(default=True, description="Apply label-based score deltas after agent grading")
    tg_agent_chunk_label_score_deltas: str = Field(default="irrelevant:-0.20,weak:-0.05,relevant:0.03,strong:0.10", description="Label to score delta map for agent chunk grading")
    tg_agent_chunk_context_for_table_formula: bool = Field(default=True, description="Include linked text context when grading table/formula")
    tg_agent_chunk_related_context_enabled: bool = Field(default=True, description="Apply linked text context policy for graded table/formula chunks")
    tg_agent_chunk_related_context_add_labels: str = Field(default="strong", description="Labels that allow adding missing linked text context")
    tg_agent_chunk_related_context_trigger_mode: str = Field(default="label_only", description="Linked context add trigger mode")
    tg_agent_chunk_related_context_fixed_scores: str = Field(default="irrelevant:0.00,weak:0.40,relevant:0.65,strong:0.70", description="Fixed scores for agent-added linked text context by label")
    tg_agent_chunk_related_context_existing_modality_deltas: str = Field(default="irrelevant:-0.10,weak:-0.03,relevant:0.03,strong:0.08", description="Table/formula deltas when linked context already exists")
    tg_agent_chunk_related_context_existing_text_deltas: str = Field(default="irrelevant:0.00,weak:0.00,relevant:0.02,strong:0.06", description="Linked text deltas when already present")
    tg_agent_chunk_related_context_added_modality_deltas: str = Field(default="irrelevant:0.00,weak:0.00,relevant:0.03,strong:0.08", description="Table/formula deltas when linked context is added")
    enable_named_vectors: bool = Field(default=False, description="Enable Qdrant named vectors abstraction")
    named_vector_text_name: str = Field(default="text", description="Qdrant named vector key for text nodes")
    named_vector_table_name: str = Field(default="table", description="Qdrant named vector key for table nodes")
    named_vector_image_name: str = Field(default="image", description="Qdrant named vector key for image nodes")
    named_vector_fallback_to_text: bool = Field(default=True, description="Fallback unsupported vector channels to text named vector")
    retrieval_prepare_taskgraph: bool = Field(default=True, description="Expose retrieval prep interfaces for future TaskGraph")
    taskgraph_enabled: bool = Field(default=False, description="Enable TaskGraph execution path for query")
    taskgraph_evidence_gate_enabled: bool = Field(
        default=True,
        description="Enable TaskGraph evidence gate for ablation experiments",
    )
    taskgraph_local_retry_enabled: bool = Field(
        default=True,
        description="Enable TaskGraph local retry for ablation experiments",
    )
    tg_max_retries: int = Field(default=2, description="Max local retry loops in TaskGraph")
    tg_budget_tokens: int = Field(default=12000, description="Estimated token budget for one TaskGraph run")
    tg_budget_ms: int = Field(default=30000, description="Time budget in milliseconds for one TaskGraph run")
    tg_min_evidence_hits: int = Field(default=2, description="Minimum evidence hit count required by evidence gate")
    tg_min_coverage_ratio: float = Field(default=0.5, description="Minimum keyword coverage ratio for evidence gate")
    tg_min_gain_threshold: float = Field(default=0.05, description="Minimum evidence gain threshold across retries")
    tg_min_support_score: float = Field(default=0.45, description="Minimum support score for evidence gate")
    tg_strong_support_score: float = Field(default=0.75, description="Support score threshold for strong evidence")
    tg_support_w_top_hit: float = Field(default=0.30, description="Support score weight for top hit score")
    tg_support_w_avg_top: float = Field(default=0.20, description="Support score weight for average top scores")
    tg_support_w_bm25_vector: float = Field(default=0.15, description="Support score weight for bm25/vector consistency")
    tg_support_w_rerank: float = Field(default=0.10, description="Support score weight for rerank score")
    tg_support_w_source_diversity: float = Field(default=0.10, description="Support score weight for source diversity")
    tg_support_w_slot_coverage: float = Field(default=0.10, description="Support score weight for slot coverage")
    tg_support_w_keyword: float = Field(default=0.05, description="Support score weight for keyword coverage")
    tg_debug_support_features: bool = Field(default=True, description="Print support-score feature details in CLI debug")
    tg_support_score_normalization: Literal["rank", "minmax", "raw"] = Field(
        default="rank", description="Normalize retrieval scores for support_score"
    )
    tg_support_disable_missing_rerank_weight: bool = Field(
        default=True, description="Remove rerank weight when rerank scores are unavailable"
    )
    tg_support_consistency_mode: Literal["overlap", "score_span"] = Field(
        default="overlap", description="BM25/vector consistency calculation mode"
    )
    tg_support_source_diversity_mode: Literal["auto", "always", "disabled"] = Field(
        default="auto", description="When source diversity contributes to support_score"
    )
    tg_support_slot_coverage_hard_only: bool = Field(
        default=True, description="Only hard slots contribute to support slot coverage"
    )
    tg_conflict_numeric_tolerance: float = Field(default=0.0, description="Numeric tolerance for evidence conflict checks")
    tg_required_slot_strict: bool = Field(default=True, description="Require all extracted evidence slots to be covered")
    tg_retry_top_k_multiplier: float = Field(default=1.5, description="Top-k multiplier applied by TaskGraph local retry")
    tg_retry_max_top_k: int = Field(default=32, description="Maximum top-k allowed during TaskGraph local retry")
    tg_retry_page_window_step: int = Field(default=1, description="Page-window increment applied by TaskGraph local retry")
    tg_retry_max_page_window: int = Field(default=3, description="Maximum relationship page window during TaskGraph local retry")
    tg_retry_rewrite_enabled: bool = Field(default=True, description="Enable rule-based query rewrite during local retry")
    tg_citation_strict: bool = Field(default=True, description="Require answer to include citation markers")
    tg_allow_refusal: bool = Field(default=True, description="Allow refusal when evidence remains insufficient")
    tg_route_llm_enabled: bool = Field(default=False, description="Use LLM-assisted route analysis (off by default)")
    tg_agent_route_enabled: bool = Field(default=False, description="Enable constrained LLM route analysis")
    tg_agent_retrieval_planner_enabled: bool = Field(default=False, description="Enable constrained LLM retrieval planning")
    tg_agent_evidence_critic_enabled: bool = Field(default=False, description="Enable constrained LLM evidence critique")
    tg_agent_retry_advisor_enabled: bool = Field(default=False, description="Enable constrained LLM retry advice")
    tg_agent_max_context_hits: int = Field(default=8, description="Max evidence hits sent to TaskGraph agent critic")
    tg_agent_timeout_sec: float = Field(default=30.0, description="Reserved timeout for TaskGraph agent calls")
    tg_agent_max_retries: int = Field(default=1, description="Reserved retry count for TaskGraph agent calls")
    tg_agent_strict_schema: bool = Field(default=True, description="Require strict schema validation for TaskGraph agent outputs")
    tg_agent_fallback_to_rules: bool = Field(default=True, description="Fallback to rule logic when TaskGraph agent fails")
    tg_agent_debug_prompts: bool = Field(default=False, description="Include agent prompt debug details")
    tg_agent_min_route_confidence: float = Field(default=0.55, description="Minimum LLM route confidence to merge route decision")
    context_top_n: int = Field(default=6, description="How many chunks enter prompt context")
    prompt_max_context_chars: int = Field(default=12000, description="Max context characters in prompt")

    # Answer and tracing behavior
    uncertain_answer_text: str = Field(
        default="无法根据已检索到的资料确定答案。",
        description="Fallback answer when reliable context is unavailable",
    )
    require_citations: bool = Field(
        default=True,
        description="Force answer to include citation tags like [1][2]",
    )

    @field_validator("chunk_overlap")
    @classmethod
    def validate_chunk_overlap(cls, value: int, info):
        size = info.data.get("chunk_size", 0)
        if value >= size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return value

    @field_validator("embedding_dimensions", "dashscope_embedding_dimension", mode="before")
    @classmethod
    def empty_embedding_dimensions_to_none(cls, value):
        if value == "" or value is None:
            return None
        return value

    @field_validator(
        "retrieval_top_k",
        "context_top_n",
        "bm25_top_k",
        "rerank_top_n",
        "rerank_max_retries",
        "page_top_k",
        "table_top_k",
        "rrf_k",
        "rrf_top_k",
        "rel_expand_steps",
        "rel_expand_pages",
        "rel_expand_seed_top_m",
        "rel_expand_max_related_tables",
        "rel_expand_max_related_formulas",
        "rel_expand_context_text_seed_top_m",
        "rel_expand_context_text_max_per_seed",
        "retrieval_related_evidence_max_total",
        "retrieval_related_evidence_max_per_seed",
        "rel_expand_routed_seed_top_m",
        "rel_expand_routed_max_per_seed",
        "rel_expand_routed_max_total",
        "rel_expand_retry_seed_top_m",
        "rel_expand_retry_max_per_seed",
        "rel_expand_retry_max_total",
        "tg_agent_context_expansion_seed_top_m",
        "tg_agent_context_expansion_max_rounds",
        "tg_agent_context_expansion_max_context_hits",
        "tg_agent_chunk_grading_head_m",
        "tg_agent_chunk_grading_tail_n",
        "tg_agent_chunk_grading_max_chunks",
        "tg_max_retries",
        "tg_budget_tokens",
        "tg_budget_ms",
        "tg_min_evidence_hits",
        "tg_retry_max_top_k",
        "tg_retry_page_window_step",
        "tg_retry_max_page_window",
        "tg_agent_max_context_hits",
        "tg_agent_max_retries",
        "embedding_batch_size",
        "image_embed_batch_size",
        "image_embed_max_retries",
        "image_vlm_max_retries",
        "image_object_max_items",
        "image_caption_max_chars",
        "image_ocr_max_chars",
        "formula_node_min_chars",
        "formula_group_max_gap_lines",
        "mineru_poll_interval_sec",
        "mineru_poll_timeout_sec",
        "mineru_download_max_retries",
        "mineru_download_retry_interval_sec",
        "mineru_context_window_chars",
        "mineru_same_page_link_max_nodes",
        "embedding_input_max_tokens",
        "embedding_input_safety_margin_tokens",
        "retrieval_eval_max_text_chars",
        "retrieval_vis_max_text_chars",
        "ingestion_inspect_max_text_chars",
    )
    @classmethod
    def validate_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("value must be > 0")
        return value

    @field_validator("ingestion_batch_size", "ingestion_max_retries")
    @classmethod
    def validate_ingestion_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("value must be > 0")
        return value

    @field_validator("log_slow_stage_ms")
    @classmethod
    def validate_log_slow_stage_ms(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("log_slow_stage_ms must be > 0")
        return value

    @field_validator("console_progress_min_interval_ms")
    @classmethod
    def validate_console_progress_min_interval(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("console_progress_min_interval_ms must be > 0")
        return value

    @field_validator("image_embed_timeout_sec")
    @classmethod
    def validate_image_embed_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("image_embed_timeout_sec must be > 0")
        return value

    @field_validator("image_vlm_timeout_sec")
    @classmethod
    def validate_image_vlm_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("image_vlm_timeout_sec must be > 0")
        return value

    @field_validator("dashscope_embedding_dimension")
    @classmethod
    def validate_dashscope_embedding_dimension(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("dashscope_embedding_dimension must be > 0")
        return value

    @field_validator(
        "tg_min_coverage_ratio",
        "tg_min_gain_threshold",
        "tg_min_support_score",
        "tg_strong_support_score",
        "tg_support_w_top_hit",
        "tg_support_w_avg_top",
        "tg_support_w_bm25_vector",
        "tg_support_w_rerank",
        "tg_support_w_source_diversity",
        "tg_support_w_slot_coverage",
        "tg_support_w_keyword",
        "tg_conflict_numeric_tolerance",
        "tg_agent_min_route_confidence",
    )
    @classmethod
    def validate_ratio(cls, value: float) -> float:
        if value < 0:
            raise ValueError("ratio must be >= 0")
        return value

    @field_validator("tg_retry_top_k_multiplier")
    @classmethod
    def validate_retry_top_k_multiplier(cls, value: float) -> float:
        if value <= 1:
            raise ValueError("tg_retry_top_k_multiplier must be > 1")
        return value

    @field_validator("tg_agent_timeout_sec")
    @classmethod
    def validate_agent_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("tg_agent_timeout_sec must be > 0")
        return value

    @model_validator(mode="after")
    def validate_dashscope_embedding_dimension_match(self):
        if (
            self.dashscope_embedding_dimension is not None
            and self.embedding_dimensions is not None
            and self.dashscope_embedding_dimension != self.embedding_dimensions
        ):
            raise ValueError(
                "dashscope_embedding_dimension must match embedding_dimensions when both are set"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings instance."""

    return Settings()
