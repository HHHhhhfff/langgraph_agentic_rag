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
    enable_formula_recognition: bool = Field(
        default=True,
        description="Extract LaTeX/MathML formulas from parsed text into formula nodes",
    )
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
        "mineru_poll_interval_sec",
        "mineru_poll_timeout_sec",
        "mineru_download_max_retries",
        "mineru_download_retry_interval_sec",
        "embedding_input_max_tokens",
        "embedding_input_safety_margin_tokens",
        "retrieval_eval_max_text_chars",
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
