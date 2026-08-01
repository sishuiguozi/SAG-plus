from __future__ import annotations

from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

from sag_api.core.model_providers import ModelProviderId
from sag_api.enums import SearchStrategy


class QuickModelSetupRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=500)

    @field_validator("api_key")
    @classmethod
    def normalize_api_key(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("API Key 不能为空")
        return value


class LocalModelDownloadRequest(BaseModel):
    """A user-selected subset of the supported local embedding files."""

    files: list[str] = Field(min_length=1, max_length=5)


class SystemPreferencesUpdate(BaseModel):
    timezone: str = Field(min_length=1, max_length=100)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("必须使用有效的 IANA 时区，例如 Asia/Shanghai") from error
        return normalized


class ModelConfigUpdate(BaseModel):
    """模型与知识库配置的部分更新（未出现的字段保持不变）。

    密钥字段留空表示「保持原值」（不清空）；base_url / dimensions 留空表示清除。
    """

    llm_provider: ModelProviderId | None = None
    llm_base_url: str | None = Field(default=None, max_length=500)
    llm_api_key: str | None = Field(default=None, max_length=500)
    llm_model: str | None = Field(default=None, min_length=1, max_length=200)
    llm_temperature: float | None = Field(default=None, ge=0, le=2)
    llm_max_tokens: int | None = Field(default=None, ge=1, le=32768)
    llm_context_window: int | None = Field(default=None, ge=1024, le=2_000_000)
    llm_timeout_ms: int | None = Field(default=None, ge=1_000, le=600_000)
    llm_max_retries: int | None = Field(default=None, ge=0, le=10)

    embedding_provider: Literal["api", "local"] | None = None
    embedding_local_model_file: str | None = Field(default=None, min_length=1, max_length=200)
    embedding_local_n_ctx: int | None = Field(default=None, ge=256, le=8192)
    embedding_local_n_threads: int | None = Field(default=None, ge=0, le=128)
    embedding_model: str | None = Field(default=None, min_length=1, max_length=200)
    embedding_base_url: str | None = Field(default=None, max_length=500)
    embedding_api_key: str | None = Field(default=None, max_length=500)
    embedding_dimensions: int | None = Field(default=None, ge=1, le=8192)

    document_parser: Literal["auto", "markitdown", "mineru"] | None = None
    mineru_base_url: str | None = Field(default=None, max_length=500)
    mineru_api_key: str | None = Field(default=None, max_length=500)
    mineru_version: Literal["2.0", "2.5"] | None = None
    job_concurrency: int | None = Field(default=None, ge=1, le=16)
    document_extract_concurrency: int | None = Field(default=None, ge=1, le=50)
    document_chunk_max_tokens: int | None = Field(default=None, ge=100, le=100_000)
    document_chunk_mode: Literal["standard", "heading_strict", "regex", "parent_child"] | None = None
    document_chunk_regex: str | None = Field(default=None, max_length=500)
    parent_chunk_max_tokens: int | None = Field(default=None, ge=200, le=20_000)
    parent_chunk_vectorize: bool | None = None

    search_strategy: SearchStrategy | None = None
    search_top_k: int | None = Field(default=None, ge=1, le=50)
    search_cache_ttl_seconds: int | None = Field(default=None, ge=0, le=600)
    lancedb_fts_enabled: bool | None = None
    search_llm_rerank_enabled: bool | None = None
    search_llm_rerank_candidates: int | None = Field(default=None, ge=3, le=20)
    sag_language: Literal["zh", "en"] | None = None
    # ── 向量索引与写入（SAG-OPT-30x，保存后对后续写入/检索即时生效）──
    lancedb_ann_enabled: bool | None = None
    lancedb_search_refine_factor: int | None = Field(default=None, ge=0, le=100)
    lancedb_search_nprobes: int | None = Field(default=None, ge=0, le=1024)
    vector_write_job_batch_size: int | None = Field(default=None, ge=100, le=500)
    vector_write_tail_flush_seconds: float | None = Field(default=None, ge=0.0, le=5.0)
    vector_append_new_enabled: bool | None = None
    vector_append_lookup_chunk_size: int | None = Field(default=None, ge=100, le=5000)
    aux_vector_deferred_enabled: bool | None = None
    source_chunk_vector_embedding_batch_size: int | None = Field(default=None, ge=1, le=100)
    source_chunk_vector_index_batch_size: int | None = Field(default=None, ge=1, le=200)

    # ── 磁盘分级保护（SAG-OPT-802，保存后下一个检查周期生效）──
    disk_guard_enabled: bool | None = None
    disk_warn_gb: float | None = Field(default=None, gt=0, le=1_000_000)
    disk_pause_aux_gb: float | None = Field(default=None, gt=0, le=1_000_000)
    disk_pause_vector_gb: float | None = Field(default=None, gt=0, le=1_000_000)
    disk_pause_ingest_gb: float | None = Field(default=None, gt=0, le=1_000_000)
    disk_check_interval_seconds: int | None = Field(default=None, ge=5, le=3600)

    # ── 性能指标（SAG-OPT-604，保存后对后续请求生效）──
    performance_slow_threshold_ms: int | None = Field(default=None, ge=100, le=600_000)
    performance_window: int | None = Field(default=None, ge=64, le=65_536)

    # ── 引擎与后台任务 ──
    engine_cache_size: int | None = Field(default=None, ge=1, le=128)
    engine_warmup_count: int | None = Field(default=None, ge=0, le=64)
    job_max_attempts: int | None = Field(default=None, ge=1, le=10)
    document_strict_filtering: bool | None = None

    # ── 检索细节（SAG-OPT-503，保存后对后续检索生效）──
    search_source_candidate_limit: int | None = Field(default=None, ge=1, le=256)
    search_source_concurrency: int | None = Field(default=None, ge=1, le=32)
    search_source_timeout: float | None = Field(default=None, ge=1.0, le=120.0)
    search_fallback_vector: bool | None = None

    # ── SQLite 调优（SAG-OPT-402；保存后需重启 API 才应用到连接）──
    database_sqlite_pragma_tuning_enabled: bool | None = None
    database_sqlite_synchronous: Literal["OFF", "NORMAL", "FULL", "EXTRA"] | None = None
    database_sqlite_cache_size: int | None = Field(default=None, ge=-1_048_576, le=0)
    database_sqlite_mmap_size: int | None = Field(default=None, ge=0, le=2**40)
    database_sqlite_temp_store: Literal["DEFAULT", "FILE", "MEMORY"] | None = None

    # ── MinerU 解析高级参数 ──
    mineru_parse_method: Literal["auto", "txt", "ocr"] | None = None
    mineru_request_timeout: float | None = Field(default=None, ge=1.0, le=600.0)
    mineru_poll_interval: float | None = Field(default=None, ge=0.5, le=60.0)
    mineru_poll_timeout: float | None = Field(default=None, ge=10.0, le=7200.0)
    mineru_result_max_mb: int | None = Field(default=None, ge=1, le=2048)

    # ── 知识宇宙预算（保存后刷新生效）──
    universe_manifest_source_limit: int | None = Field(default=None, ge=16, le=2048)
    universe_timeline_event_page_size: int | None = Field(default=None, ge=10, le=50)
    universe_event_entity_limit: int | None = Field(default=None, ge=4, le=8)
    universe_lod_orbit_px: int | None = Field(default=None, ge=24, le=240)
    universe_lod_near_px: int | None = Field(default=None, ge=64, le=640)
    universe_lod_deep_px: int | None = Field(default=None, ge=120, le=1200)
    universe_lod_hysteresis_px: int | None = Field(default=None, ge=4, le=120)
    universe_lod_debounce_ms: int | None = Field(default=None, ge=50, le=2000)
    universe_proxy_budget_desktop: int | None = Field(default=None, ge=256, le=16000)
    universe_proxy_budget_mobile: int | None = Field(default=None, ge=128, le=4800)
    universe_node_budget_desktop: int | None = Field(default=None, ge=450, le=1200)
    universe_node_budget_mobile: int | None = Field(default=None, ge=450, le=800)
    universe_edge_budget_desktop: int | None = Field(default=None, ge=600, le=1800)
    universe_edge_budget_mobile: int | None = Field(default=None, ge=600, le=1200)
    universe_planet_radius_min: float | None = Field(default=None, ge=12.0, le=160.0)
    universe_planet_radius_max: float | None = Field(default=None, ge=48.0, le=360.0)
    universe_planet_radius_scale: float | None = Field(default=None, ge=2.0, le=80.0)


    @field_validator("document_parser", "mineru_version")
    @classmethod
    def reject_null_parser_fields(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("解析器与 MinerU 版本不能为 null")
        return value

    @field_validator("job_concurrency", "document_extract_concurrency", "document_chunk_max_tokens", "parent_chunk_max_tokens")
    @classmethod
    def reject_null_document_numbers(cls, value: int | None) -> int:
        if value is None:
            raise ValueError("知识库解析参数不能为 null")
        return value

    @field_validator("document_chunk_mode")
    @classmethod
    def reject_null_chunk_mode(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("切片模式不能为 null")
        return value

    @field_validator("llm_timeout_ms", "llm_max_retries")
    @classmethod
    def reject_null_llm_resilience_fields(cls, value: int | None) -> int:
        if value is None:
            raise ValueError("模型超时与重试次数不能为 null")
        return value

    @field_validator("llm_provider")
    @classmethod
    def reject_null_llm_provider(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("模型接入方式不能为 null")
        return value
