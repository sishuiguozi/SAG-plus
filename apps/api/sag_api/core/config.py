"""应用配置（pydantic-settings）。

所有配置项均可通过环境变量 `SAG_*` 或 `.env` 覆盖。设计上区分三类后端：

- **sag 元数据库**（用户 / 信源 / 文档 / 会话）：`database_url`
- **zleap-sag 存储**（分块 / 向量 / 事件图谱）：`sag_*` + `data_dir`
- **LLM / embedding**（抽取与答案生成）：`llm_*` / `embedding_*`
- **文档解析**（PDF / Office 等转 Markdown）：`document_parser` / `mineru_*`

默认零依赖：SQLite 元数据 + zleap-sag 本地 LanceDB。生产可整体切到 Postgres。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from sag_api.core.model_providers import ModelProviderId, get_model_provider
from sag_api.enums import (
    ReasoningHistoryCompat, JsonSchemaCompat,
    SearchStrategy,
    ToolChoiceStrategy,
    normalize_search_strategy,
)

_DEFAULT_LLM_PROVIDER = get_model_provider("openai")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SAG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── 应用 ────────────────────────────────────────────────────────────
    app_name: str = "sag"
    environment: Literal["dev", "prod"] = "dev"
    debug: bool = True
    secret_key: str = "dev-insecure-secret-change-me-in-production-0123456789"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 天
    # 业务展示时区；数据库与 API 时间戳始终使用 UTC。
    timezone: str = "Asia/Shanghai"
    # NoDecode 让逗号分隔值先进入下方 validator，避免 settings 源强制按 JSON 解码。
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:3000"])
    # 关闭后仅允许首个用户注册（部署引导），其余返回 403
    allow_registration: bool = True

    # ── sag 元数据库 ───────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./.data/sag.db"
    database_pool_size: int = Field(default=20, ge=1, le=100)
    database_max_overflow: int = Field(default=40, ge=0, le=200)
    database_pool_timeout: float = Field(default=60.0, ge=1.0, le=300.0)
    # SQLite 本地元数据库：桌面单进程并发有限，10+5 小池足够，避免原 20+40
    # 大池浪费内存与文件句柄（非 SQLite 后端仍用 database_pool_*）。
    database_sqlite_pool_size: int = Field(default=10, ge=1, le=32)
    database_sqlite_max_overflow: int = Field(default=5, ge=0, le=16)
    # SAG-OPT-402：SQLite PRAGMA 调优（connect event 应用到每个连接）。
    # 基础三项（foreign_keys / WAL / busy_timeout）始终保留；以下调优项
    # 可整体关闭回退。synchronous=NORMAL 在 WAL 下安全且减少 fsync；
    # cache_size 负值表示 KiB（-65536 ≈ 64 MiB）；mmap_size 单位字节；
    # temp_store=MEMORY 让临时表/排序走内存。
    database_sqlite_pragma_tuning_enabled: bool = True
    database_sqlite_synchronous: Literal["OFF", "NORMAL", "FULL", "EXTRA"] = "NORMAL"
    database_sqlite_cache_size: int = Field(default=-65536, ge=-1048576, le=0)
    database_sqlite_mmap_size: int = Field(default=268435456, ge=0, le=2**40)
    database_sqlite_temp_store: Literal["DEFAULT", "FILE", "MEMORY"] = "MEMORY"

    # ── 存储 ────────────────────────────────────────────────────────────
    # 可选数据根目录：设置后统一派生 database_url / data_dir / upload_dir
    # （models 位于 {data_root}/models）。适合桌面端切换知识库落盘位置。
    data_root: str | None = None
    data_dir: str = "./.data/engine"  # zleap-sag data_dir（LanceDB + SQLite）
    upload_dir: str = "./.data/uploads"  # 上传原始文件落盘
    max_upload_mb: int = 25  # 单文件上传上限
    job_concurrency: int = 2  # 后台处理并发
    vector_write_job_batch_size: int = Field(default=200, ge=100, le=500)  # 向量写入队列任务聚合批量
    vector_write_tail_flush_seconds: float = Field(default=1.5, ge=0.0, le=5.0)  # 未满批次短暂等待，吸收同源尾批
    vector_append_new_enabled: bool = True  # SAG-OPT-105：纯新增向量批次走 append(add)，避免每次写入都产生 merge 版本
    vector_append_lookup_chunk_size: int = Field(default=500, ge=100, le=5000)  # SAG-OPT-105：已存在 ID 预查询分块大小
    aux_vector_deferred_enabled: bool = True  # 入库高峰先延迟实体/事件-实体辅助向量，降低 LanceDB 碎片增长
    source_chunk_vector_embedding_batch_size: int = Field(default=20, ge=1, le=100)
    source_chunk_vector_index_batch_size: int = Field(default=200, ge=1, le=200)
    lancedb_ann_enabled: bool = True  # SAG-OPT-302：检索走 ANN 索引（精确检索可回退）
    lancedb_search_refine_factor: int = Field(default=8, ge=0, le=100)  # SAG-OPT-302：ANN 精排因子，0=关闭
    lancedb_search_nprobes: int = Field(default=16, ge=0, le=1024)  # SAG-OPT-302：IVF 探测数，0=上游默认
    # SAG-OPT-802：磁盘分级保护（GB）
    disk_guard_enabled: bool = True
    disk_warn_gb: float = 30.0
    disk_pause_aux_gb: float = 20.0
    disk_pause_vector_gb: float = 10.0
    disk_pause_ingest_gb: float = 5.0
    disk_check_interval_seconds: int = Field(default=300, ge=5, le=3600)
    # SAG-OPT-604：运行期性能指标
    performance_slow_threshold_ms: int = Field(default=2000, ge=100, le=600000)
    performance_window: int = Field(default=4096, ge=64, le=65536)
    document_extract_concurrency: int = Field(default=5, ge=1, le=50)  # 单文档 chunk 抽取并发
    document_chunk_max_tokens: int = Field(default=1_000, ge=100, le=100_000)
    document_chunk_mode: Literal["standard", "heading_strict", "regex", "parent_child"] = "standard"
    document_chunk_regex: str | None = None  # regex 分块分隔符（Python re 语法）
    # A4 父子分块：父块为目标大小的上下文块，子块继承 source_chunk_max_tokens 切分。
    parent_chunk_max_tokens: int = Field(default=1_024, ge=200, le=20_000)
    parent_chunk_vectorize: bool = True  # False 时父块仅入库、不生成向量
    tree_sitter_auto_download: bool = True  # 后台下载全部语言解析器；测试环境显式关闭
    # 上传文档已有独立的知识型过滤要求；默认关闭上游基于标题/摘要的严格过滤，
    # 避免无摘要或标题缺失的书籍正文被误判为噪音。
    document_strict_filtering: bool = False
    job_max_attempts: int = 3  # 可重试失败的最大尝试次数（含首次）
    engine_cache_size: int = 16  # 引擎槽 LRU 上限（超限逐出最久未用）
    engine_warmup_count: int = 4  # 启动时预热最近使用的信源引擎数
    # 允许上传的扩展名白名单（小写，含点）；空集合表示不限制
    allowed_upload_exts: set[str] = {
        ".md",
        ".markdown",
        ".txt",
        ".text",
        ".pdf",
        ".docx",
        ".pptx",
        ".xls",
        ".xlsx",
        ".csv",
        ".tsv",
        ".html",
        ".htm",
        ".json",
        ".epub",
    }

    # ── zleap-sag 后端选择 ─────────────────────────────────────────────
    # None → 零基础设施（LanceDB + 内置 SQLite，落在 data_dir）
    sag_vector_provider: Literal["lancedb", "es", "pgvector", "oceanbase"] = "lancedb"
    sag_relational_provider: Literal["sqlite", "postgres", "mysql", "oceanbase"] | None = None
    sag_language: Literal["zh", "en"] = "zh"

    # 生产单库（pgvector）时复用同一 Postgres —— 由这些字段拼装
    sag_pg_host: str = "localhost"
    sag_pg_port: int = 5432
    sag_pg_user: str = "sag"
    sag_pg_password: str = "sag"
    sag_pg_database: str = "sag"

    # ── LLM（答案生成 + 抽取）─────────────────────────────────────────
    # 协议、路由规则和技术默认值统一由 model_providers 注册表维护。
    llm_provider: ModelProviderId = _DEFAULT_LLM_PROVIDER.id
    llm_base_url: str | None = _DEFAULT_LLM_PROVIDER.default_base_url
    llm_api_key: str | None = None
    llm_model: str = _DEFAULT_LLM_PROVIDER.default_model
    llm_temperature: float = _DEFAULT_LLM_PROVIDER.default_temperature
    llm_max_tokens: int = 20_000
    llm_context_window: int = _DEFAULT_LLM_PROVIDER.default_context_window
    llm_timeout_ms: int = Field(default=60_000, ge=1_000, le=600_000)
    llm_max_retries: int = Field(default=2, ge=0, le=10)
    llm_tool_choice_strategy: ToolChoiceStrategy = "forced_no_thinking"
    llm_reasoning_history_compat: ReasoningHistoryCompat = "auto"
    # auto: only rewrite json_schema->json_object for known-incompatible routes
    # (OpenCode/Console Go style). always/off force the rewrite for every provider.
    llm_json_schema_compat: JsonSchemaCompat = "auto"
    # 透传给 chat/completions 的额外请求体（JSON），如 {"enable_thinking": false}；
    # 未配置时对 qwen 系模型通过 LiteLLM reasoning_effort=none 统一关闭思考。
    llm_extra_body: dict | None = None

    # ── Embedding（OpenAI-compatible；仅 OpenAI provider 可复用生成配置）───────
    embedding_provider: Literal["api", "local"] = "api"  # api=OpenAI 兼容；local=程序内嵌 bge-m3 q8_0
    embedding_local_model_file: str = "bge-m3-Q8_0.gguf"  # local 模式模型文件（{data_dir}/../models/bge-m3/）
    embedding_local_n_ctx: int = Field(default=2048, ge=256, le=8192)
    embedding_local_n_threads: int = Field(default=0, ge=0, le=128)  # 0=llama.cpp 自动
    embedding_model: str = "bge-large-en-v1.5"
    embedding_base_url: str | None = "https://api.302ai.cn/v1"
    embedding_api_key: str | None = None
    embedding_dimensions: int | None = None

    # ── 文档解析（进入 zleap-sag 前统一转为 Markdown）─────────────────
    # auto：PDF 优先 MinerU，未配置或 MinerU 失败时回退本地 MarkItDown。
    document_parser: Literal["auto", "markitdown", "mineru"] = "auto"
    mineru_base_url: str | None = "https://api.302ai.cn"
    mineru_api_key: str | None = None
    mineru_version: Literal["2.0", "2.5"] = "2.5"
    mineru_parse_method: Literal["auto", "txt", "ocr"] = "auto"
    mineru_request_timeout: float = 60.0
    mineru_poll_interval: float = 2.0
    mineru_poll_timeout: float = 300.0
    mineru_result_max_mb: int = 100

    # ── 检索默认 ────────────────────────────────────────────────────────
    search_strategy: SearchStrategy = "vector"
    search_top_k: int = 8
    # 全库检索先选有界信源候选；@ 显式范围同样受此硬上限保护。
    search_source_candidate_limit: int = Field(default=16, ge=1, le=256)
    search_source_concurrency: int = Field(default=4, ge=1, le=32)
    # 精确模式（multi）含查询侧 LLM 往返；超时/失败/空结果自动回退快速模式（vector）。
    search_source_timeout: float = 12.0
    search_fallback_vector: bool = True
    # A1：检索结果 TTL 缓存（秒，0=关闭）
    search_cache_ttl_seconds: int = Field(default=30, ge=0, le=600)
    # A2b：LanceDB FTS（BM25）独立词法召回
    lancedb_fts_enabled: bool = True
    # A3：LLM Rerank（默认关闭；开启后对候选做编号重排）
    search_llm_rerank_enabled: bool = False
    search_llm_rerank_candidates: int = Field(default=8, ge=3, le=20)
    # Rerank 来源：off=仅融合排序；local/API=Cross-Encoder；llm=旧兼容编号重排。
    search_rerank_mode: Literal["off", "local", "api", "llm"] = "off"
    search_rerank_candidates: int = Field(default=8, ge=3, le=20)
    search_local_rerank_model_file: str = "qwen3-reranker-0.6b-q8_0.gguf"
    search_rerank_api_url: str | None = None
    search_rerank_api_key: str | None = None
    search_rerank_api_model: str | None = None
    search_rerank_api_instruction: str | None = (
        "Given a web search query, retrieve relevant passages that answer the query."
    )
    search_rerank_api_timeout_ms: int = Field(default=30_000, ge=1_000, le=120_000)

    # ── 知识宇宙 ──────────────────────────────────────────────────────────
    # 服务端统一下发景深门与场景预算，前端不再散落硬编码阈值。
    universe_manifest_source_limit: int = Field(default=256, ge=16, le=2048)
    universe_timeline_event_page_size: int = Field(default=20, ge=10, le=50)
    # 时间线只返回事件的一屏事实投影；完整邻域由显式探索分页加载。
    universe_event_entity_limit: int = Field(default=8, ge=4, le=8)
    universe_lod_orbit_px: int = Field(default=72, ge=24, le=240)
    universe_lod_near_px: int = Field(default=180, ge=64, le=640)
    universe_lod_deep_px: int = Field(default=360, ge=120, le=1200)
    universe_lod_hysteresis_px: int = Field(default=24, ge=4, le=120)
    universe_lod_debounce_ms: int = Field(default=220, ge=50, le=2000)
    universe_proxy_budget_desktop: int = Field(default=15000, ge=256, le=16000)
    universe_proxy_budget_mobile: int = Field(default=4000, ge=128, le=4800)
    universe_node_budget_desktop: int = Field(default=700, ge=450, le=1200)
    universe_node_budget_mobile: int = Field(default=520, ge=450, le=800)
    universe_edge_budget_desktop: int = Field(default=1000, ge=600, le=1800)
    universe_edge_budget_mobile: int = Field(default=720, ge=600, le=1200)
    universe_planet_radius_min: float = Field(default=42.0, ge=12.0, le=160.0)
    universe_planet_radius_max: float = Field(default=132.0, ge=48.0, le=360.0)
    universe_planet_radius_scale: float = Field(default=22.0, ge=2.0, le=80.0)

    # ── Agent 循环 ──────────────────────────────────────────────────────
    agent_max_steps: int = 6  # 工具调用最大轮数（多轮检索的上界）
    history_keep_recent: int = 8  # 历史压缩时原文保留的最近消息数
    # 只装载最近有界窗口；更旧对话应进入滚动摘要，不做全表回放。
    history_load_limit: int = Field(default=200, ge=1, le=1000)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """允许用逗号分隔的字符串配置 CORS 源。"""
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            if v.startswith("["):
                return json.loads(v)
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator("search_strategy", mode="before")
    @classmethod
    def _normalize_legacy_search_strategy(cls, value: object) -> object:
        # 兼容升级前的环境变量；公开 API 已不再接受 atomic。
        return normalize_search_strategy(value) if isinstance(value, str) else value

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("timezone 必须是有效的 IANA 时区") from error
        return normalized

    @property
    def llm_configured(self) -> bool:
        """LLM 是否已配置（决定抽取 / 问答能否真正运行）。"""
        return bool(self.llm_api_key)

    @property
    def routed_llm_model(self) -> str:
        """统一调用链使用的 LiteLLM 路由名。"""
        return get_model_provider(self.llm_provider).route_model(self.llm_model)

    @property
    def effective_llm_temperature(self) -> float:
        """应用当前 provider 的采样能力约束。"""
        return get_model_provider(self.llm_provider).resolve_temperature(self.llm_temperature)

    @property
    def effective_embedding_api_key(self) -> str | None:
        """优先独立 embedding key；未配置时复用 LLM key。"""
        provider = get_model_provider(self.llm_provider)
        return self.embedding_api_key or (self.llm_api_key if provider.can_reuse_embedding_credentials else None)

    @property
    def effective_embedding_base_url(self) -> str | None:
        provider = get_model_provider(self.llm_provider)
        return self.embedding_base_url or (self.llm_base_url if provider.can_reuse_embedding_credentials else None)

    @model_validator(mode="after")
    def apply_data_root(self) -> "Settings":
        """When data_root is set, derive the SQLite/engine/upload layout under it."""
        raw = (self.data_root or "").strip()
        if not raw:
            return self
        root = Path(raw).expanduser().resolve()
        db_path = (root / "sag.db").resolve()
        # SQLAlchemy sqlite absolute URLs: sqlite+aiosqlite:///C:/path/db.sqlite
        db_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
        object.__setattr__(self, "data_root", str(root))
        object.__setattr__(self, "database_url", db_url)
        object.__setattr__(self, "data_dir", str(root / "engine"))
        object.__setattr__(self, "upload_dir", str(root / "uploads"))
        return self

    @property
    def mineru_configured(self) -> bool:
        """MinerU 是否具备可调用的端点与密钥。"""
        return bool(self.mineru_base_url and self.mineru_api_key)

    @property
    def effective_document_parser(self) -> Literal["markitdown", "mineru"]:
        """当前自动解析偏好；具体文件仍由解析服务按格式路由。"""
        if self.document_parser == "markitdown":
            return "markitdown"
        return "mineru" if self.mineru_configured else "markitdown"

    def embedding_local_model_path(self) -> str:
        """Return a new catalog path, while retaining the original BGE-M3 location."""
        engine_dir = Path(self.data_dir).resolve()
        root = engine_dir.parent / "models"
        preferred = root / "embedding" / self.embedding_local_model_file
        legacy = root / "bge-m3" / self.embedding_local_model_file
        return str(legacy if legacy.is_file() else preferred)

    @property
    def effective_search_rerank_mode(self) -> Literal["off", "local", "api", "llm"]:
        """Read legacy LLM rerank configs without overriding an explicit new mode."""
        if self.search_rerank_mode == "off" and self.search_llm_rerank_enabled:
            return "llm"
        return self.search_rerank_mode


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
