"""运行期模型与知识库配置 —— DB 覆盖层叠加在 env 默认（`settings` 单例）之上。

单用户本地示范：把「模型与检索」配置存进 `settings` 表（scope=global, key=model_config）。
启动时与保存后**就地覆盖 `settings` 单例**的相应字段，端点再重建 `LLMClient` / 重置暖引擎，
使配置改动**无需重启即生效**。api_key 明文入库（本地单用户可接受），读取时脱敏（只返回是否已设）。
"""

from __future__ import annotations

import json
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sag_api.core.config import Settings
from sag_api.core.config import settings as _settings
from sag_api.core.errors import ConfigurationError
from sag_api.core.logging import get_logger
from sag_api.core.model_providers import get_model_provider
from sag_api.db.models import Setting
from sag_api.enums import SEARCH_STRATEGIES, normalize_search_strategy

_SCOPE = "global"
_KEY = "model_config"
_PREFERENCES_KEY = "system_preferences"
log = get_logger("settings")

# 允许运行期覆盖的字段（值已由请求 schema 校验/转型）
_FIELDS = frozenset(
    {
        "llm_provider",
        "llm_base_url",
        "llm_api_key",
        "llm_model",
        "llm_temperature",
        "llm_max_tokens",
        "llm_context_window",
        "llm_timeout_ms",
        "llm_max_retries",
        "embedding_provider",
        "embedding_local_model_file",
        "embedding_local_n_ctx",
        "embedding_local_n_threads",
        "embedding_model",
        "embedding_base_url",
        "embedding_api_key",
        "embedding_dimensions",
        "document_parser",
        "mineru_base_url",
        "mineru_api_key",
        "mineru_version",
        "job_concurrency",
        "document_extract_concurrency",
        "document_chunk_max_tokens",
        "document_chunk_mode",
        "document_chunk_regex",
        "parent_chunk_max_tokens",
        "parent_chunk_vectorize",
        "search_strategy",
        "search_top_k",
        "search_cache_ttl_seconds",
        "lancedb_fts_enabled",
        "search_llm_rerank_enabled",
        "search_llm_rerank_candidates",
        "search_rerank_mode",
        "search_rerank_candidates",
        "search_local_rerank_model_file",
        "search_rerank_api_url",
        "search_rerank_api_key",
        "search_rerank_api_model",
        "search_rerank_api_instruction",
        "search_rerank_api_timeout_ms",
        "sag_language",
        # 向量索引与写入（SAG-OPT-30x）
        "lancedb_ann_enabled",
        "lancedb_search_refine_factor",
        "lancedb_search_nprobes",
        "vector_write_job_batch_size",
        "vector_write_tail_flush_seconds",
        "vector_append_new_enabled",
        "vector_append_lookup_chunk_size",
        "aux_vector_deferred_enabled",
        "source_chunk_vector_embedding_batch_size",
        "source_chunk_vector_index_batch_size",
        # 磁盘分级保护（SAG-OPT-802）
        "disk_guard_enabled",
        "disk_warn_gb",
        "disk_pause_aux_gb",
        "disk_pause_vector_gb",
        "disk_pause_ingest_gb",
        "disk_check_interval_seconds",
        # 性能指标（SAG-OPT-604）
        "performance_slow_threshold_ms",
        "performance_window",
        # 引擎与后台任务
        "engine_cache_size",
        "engine_warmup_count",
        "job_max_attempts",
        "document_strict_filtering",
        # 检索细节（SAG-OPT-503）
        "search_source_candidate_limit",
        "search_source_concurrency",
        "search_source_timeout",
        "search_fallback_vector",
        # SQLite 调优（SAG-OPT-402）
        "database_sqlite_pragma_tuning_enabled",
        "database_sqlite_synchronous",
        "database_sqlite_cache_size",
        "database_sqlite_mmap_size",
        "database_sqlite_temp_store",
        # MinerU 解析高级参数
        "mineru_parse_method",
        "mineru_request_timeout",
        "mineru_poll_interval",
        "mineru_poll_timeout",
        "mineru_result_max_mb",
        # 知识宇宙预算
        "universe_manifest_source_limit",
        "universe_timeline_event_page_size",
        "universe_event_entity_limit",
        "universe_lod_orbit_px",
        "universe_lod_near_px",
        "universe_lod_deep_px",
        "universe_lod_hysteresis_px",
        "universe_lod_debounce_ms",
        "universe_proxy_budget_desktop",
        "universe_proxy_budget_mobile",
        "universe_node_budget_desktop",
        "universe_node_budget_mobile",
        "universe_edge_budget_desktop",
        "universe_edge_budget_mobile",
        "universe_planet_radius_min",
        "universe_planet_radius_max",
        "universe_planet_radius_scale",
    }
)
_SECRET_FIELDS = frozenset({"llm_api_key", "embedding_api_key", "mineru_api_key", "search_rerank_api_key"})
_NULLABLE_FIELDS = frozenset({"llm_base_url", "embedding_base_url", "embedding_dimensions", "mineru_base_url", "search_rerank_api_url"})

_OPENAI_COMPATIBLE = get_model_provider("openai")

QUICK_SETUP_302 = {
    "llm_provider": _OPENAI_COMPATIBLE.id,
    "llm_base_url": _OPENAI_COMPATIBLE.default_base_url,
    "llm_model": _OPENAI_COMPATIBLE.default_model,
    "llm_temperature": _OPENAI_COMPATIBLE.default_temperature,
    "llm_max_tokens": 20_000,
    "llm_context_window": _OPENAI_COMPATIBLE.default_context_window,
    "llm_timeout_ms": 60_000,
    "llm_max_retries": 2,
    "embedding_model": "Qwen/Qwen3-Embedding-4B",
    "embedding_base_url": "https://api.302ai.cn/v1",
    "embedding_dimensions": 1024,
    "document_parser": "auto",
    "mineru_base_url": "https://api.302ai.cn",
    "mineru_version": "2.5",
    "document_extract_concurrency": 5,
    "document_chunk_max_tokens": 1_000,
    "document_chunk_mode": "standard",
    "parent_chunk_max_tokens": 1_024,
    "parent_chunk_vectorize": True,
    "search_strategy": "vector",
    "search_top_k": 8,
    "sag_language": "zh",
}

_LEGACY_302_BASE_URLS = {
    "https://api.302.ai": "https://api.302ai.cn",
    "https://api.302.ai/v1": "https://api.302ai.cn/v1",
}


async def _load_row(session: AsyncSession, key: str = _KEY) -> Setting | None:
    return await session.scalar(select(Setting).where(Setting.scope == _SCOPE, Setting.key == key))


def _normalize_overrides(overrides: dict) -> dict:
    """清理持久化配置，确保已下线或非法策略不会进入运行时。"""
    normalized = dict(overrides)
    for field in ("llm_base_url", "embedding_base_url", "mineru_base_url"):
        value = normalized.get(field)
        if isinstance(value, str):
            normalized[field] = _LEGACY_302_BASE_URLS.get(value.rstrip("/"), value)
    strategy = normalized.get("search_strategy")
    if strategy == "atomic":
        normalized["search_strategy"] = normalize_search_strategy(strategy)
        log.warning("旧检索策略 atomic 已迁移为精确模式 multi")
    elif strategy is not None and strategy not in SEARCH_STRATEGIES:
        normalized.pop("search_strategy", None)
        log.warning("忽略非法的持久化检索策略：%s", strategy)
    return normalized


async def load_overrides(session: AsyncSession) -> dict:
    row = await _load_row(session)
    raw = dict(row.value) if row and isinstance(row.value, dict) else {}
    return _normalize_overrides(raw)


async def model_setup_status(session: AsyncSession) -> dict[str, bool]:
    """判断是否需要首次模型配置，不受运行期 DB 覆盖后的 settings 单例干扰。"""
    row = await _load_row(session)
    environment_configured = Settings().llm_configured
    database_configured = bool(row and isinstance(row.value, dict) and row.value.get("llm_api_key"))
    return {
        "required": not environment_configured and not database_configured,
        "environment_configured": environment_configured,
        "database_configured": database_configured,
    }


def apply_overrides(settings: Settings, overrides: dict) -> None:
    """把存储的覆盖值就地写回 settings 单例（请求 schema 已保证类型合法）。"""
    for key, value in _normalize_overrides(overrides).items():
        if key in _FIELDS:
            setattr(settings, key, value)


async def apply_startup_overrides(session_factory: async_sessionmaker) -> None:
    """启动时：把 DB 里的模型配置覆盖到 settings 单例（在构建 LLMClient 之前调用）。"""
    async with session_factory() as session:
        row = await _load_row(session)
        raw = dict(row.value) if row and isinstance(row.value, dict) else {}
        overrides = _normalize_overrides(raw)
        if row is not None and overrides != raw:
            # JSON 列未使用 MutableDict，必须整体重新赋值才能可靠持久化。
            row.value = overrides
            await session.commit()
        apply_overrides(_settings, overrides)
        preferences = await _load_row(session, _PREFERENCES_KEY)
        preference_values = dict(preferences.value) if preferences and isinstance(preferences.value, dict) else {}
        timezone = preference_values.get("timezone")
        if isinstance(timezone, str):
            # Stored values were validated on write. Settings assignment is kept
            # explicit so model configuration and presentation preferences remain separate.
            try:
                ZoneInfo(timezone)
            except (ZoneInfoNotFoundError, ValueError):
                log.warning("忽略非法的持久化时区：%s", timezone)
            else:
                _settings.timezone = timezone


def recommended_config() -> dict:
    """每个可运行期覆盖字段的代码默认值（即推荐值），不受 env / DB 覆盖影响。

    读取 Settings 类字段默认值（Field 默认），密钥字段不提供推荐值。
    """
    defaults = Settings.model_fields
    return {
        key: defaults[key].default
        for key in sorted(_FIELDS)
        if key in defaults and key not in _SECRET_FIELDS
    }


def effective_model_config() -> dict:
    """当前生效的模型配置（读 settings 单例；密钥脱敏为 *_set 布尔）。"""
    return {
        "llm_provider": _settings.llm_provider,
        "llm_base_url": _settings.llm_base_url,
        "llm_model": _settings.llm_model,
        "llm_temperature": _settings.llm_temperature,
        "llm_max_tokens": _settings.llm_max_tokens,
        "llm_context_window": _settings.llm_context_window,
        "llm_timeout_ms": _settings.llm_timeout_ms,
        "llm_max_retries": _settings.llm_max_retries,
        "llm_api_key_set": bool(_settings.llm_api_key),
        "embedding_provider": _settings.embedding_provider,
        "embedding_local_model_file": _settings.embedding_local_model_file,
        "embedding_local_n_ctx": _settings.embedding_local_n_ctx,
        "embedding_local_n_threads": _settings.embedding_local_n_threads,
        "embedding_model": _settings.embedding_model,
        "embedding_base_url": _settings.embedding_base_url,
        "embedding_dimensions": _settings.embedding_dimensions,
        "embedding_api_key_set": bool(_settings.embedding_api_key),
        "document_parser": _settings.document_parser,
        "effective_document_parser": _settings.effective_document_parser,
        "mineru_base_url": _settings.mineru_base_url,
        "mineru_version": _settings.mineru_version,
        "mineru_api_key_set": bool(_settings.mineru_api_key),
        "job_concurrency": _settings.job_concurrency,
        "document_extract_concurrency": _settings.document_extract_concurrency,
        "document_chunk_max_tokens": _settings.document_chunk_max_tokens,
        "document_chunk_mode": _settings.document_chunk_mode,
        "document_chunk_regex": _settings.document_chunk_regex,
        "parent_chunk_max_tokens": _settings.parent_chunk_max_tokens,
        "parent_chunk_vectorize": _settings.parent_chunk_vectorize,
        "search_strategy": _settings.search_strategy,
        "search_top_k": _settings.search_top_k,
        "search_cache_ttl_seconds": _settings.search_cache_ttl_seconds,
        "lancedb_fts_enabled": _settings.lancedb_fts_enabled,
        "search_llm_rerank_enabled": _settings.search_llm_rerank_enabled,
        "search_llm_rerank_candidates": _settings.search_llm_rerank_candidates,
        "search_rerank_mode": _settings.effective_search_rerank_mode,
        "search_rerank_candidates": _settings.search_rerank_candidates,
        "search_local_rerank_model_file": _settings.search_local_rerank_model_file,
        "search_rerank_api_url": _settings.search_rerank_api_url,
        "search_rerank_api_key_set": bool(_settings.search_rerank_api_key),
        "search_rerank_api_model": _settings.search_rerank_api_model,
        "search_rerank_api_instruction": _settings.search_rerank_api_instruction,
        "search_rerank_api_timeout_ms": _settings.search_rerank_api_timeout_ms,
        "sag_language": _settings.sag_language,
        # 向量索引与写入
        "lancedb_ann_enabled": _settings.lancedb_ann_enabled,
        "lancedb_search_refine_factor": _settings.lancedb_search_refine_factor,
        "lancedb_search_nprobes": _settings.lancedb_search_nprobes,
        "vector_write_job_batch_size": _settings.vector_write_job_batch_size,
        "vector_write_tail_flush_seconds": _settings.vector_write_tail_flush_seconds,
        "vector_append_new_enabled": _settings.vector_append_new_enabled,
        "vector_append_lookup_chunk_size": _settings.vector_append_lookup_chunk_size,
        "aux_vector_deferred_enabled": _settings.aux_vector_deferred_enabled,
        "source_chunk_vector_embedding_batch_size": _settings.source_chunk_vector_embedding_batch_size,
        "source_chunk_vector_index_batch_size": _settings.source_chunk_vector_index_batch_size,
        # 磁盘分级保护
        "disk_guard_enabled": _settings.disk_guard_enabled,
        "disk_warn_gb": _settings.disk_warn_gb,
        "disk_pause_aux_gb": _settings.disk_pause_aux_gb,
        "disk_pause_vector_gb": _settings.disk_pause_vector_gb,
        "disk_pause_ingest_gb": _settings.disk_pause_ingest_gb,
        "disk_check_interval_seconds": _settings.disk_check_interval_seconds,
        # 性能指标
        "performance_slow_threshold_ms": _settings.performance_slow_threshold_ms,
        "performance_window": _settings.performance_window,
        # 引擎与后台任务
        "engine_cache_size": _settings.engine_cache_size,
        "engine_warmup_count": _settings.engine_warmup_count,
        "job_max_attempts": _settings.job_max_attempts,
        "document_strict_filtering": _settings.document_strict_filtering,
        # 检索细节
        "search_source_candidate_limit": _settings.search_source_candidate_limit,
        "search_source_concurrency": _settings.search_source_concurrency,
        "search_source_timeout": _settings.search_source_timeout,
        "search_fallback_vector": _settings.search_fallback_vector,
        # SQLite 调优
        "database_sqlite_pragma_tuning_enabled": _settings.database_sqlite_pragma_tuning_enabled,
        "database_sqlite_synchronous": _settings.database_sqlite_synchronous,
        "database_sqlite_cache_size": _settings.database_sqlite_cache_size,
        "database_sqlite_mmap_size": _settings.database_sqlite_mmap_size,
        "database_sqlite_temp_store": _settings.database_sqlite_temp_store,
        # MinerU 解析高级参数
        "mineru_parse_method": _settings.mineru_parse_method,
        "mineru_request_timeout": _settings.mineru_request_timeout,
        "mineru_poll_interval": _settings.mineru_poll_interval,
        "mineru_poll_timeout": _settings.mineru_poll_timeout,
        "mineru_result_max_mb": _settings.mineru_result_max_mb,
        # 知识宇宙预算
        "universe_manifest_source_limit": _settings.universe_manifest_source_limit,
        "universe_timeline_event_page_size": _settings.universe_timeline_event_page_size,
        "universe_event_entity_limit": _settings.universe_event_entity_limit,
        "universe_lod_orbit_px": _settings.universe_lod_orbit_px,
        "universe_lod_near_px": _settings.universe_lod_near_px,
        "universe_lod_deep_px": _settings.universe_lod_deep_px,
        "universe_lod_hysteresis_px": _settings.universe_lod_hysteresis_px,
        "universe_lod_debounce_ms": _settings.universe_lod_debounce_ms,
        "universe_proxy_budget_desktop": _settings.universe_proxy_budget_desktop,
        "universe_proxy_budget_mobile": _settings.universe_proxy_budget_mobile,
        "universe_node_budget_desktop": _settings.universe_node_budget_desktop,
        "universe_node_budget_mobile": _settings.universe_node_budget_mobile,
        "universe_edge_budget_desktop": _settings.universe_edge_budget_desktop,
        "universe_edge_budget_mobile": _settings.universe_edge_budget_mobile,
        "universe_planet_radius_min": _settings.universe_planet_radius_min,
        "universe_planet_radius_max": _settings.universe_planet_radius_max,
        "universe_planet_radius_scale": _settings.universe_planet_radius_scale,
        "recommended": recommended_config(),
    }


def env_only_config() -> dict:
    """仅能通过 .env / SAG_* 环境变量修改的配置（设置界面只读展示，重启后生效）。

    连接串、部署与安全项不适合运行期覆盖；此处仅为「所有可配置项在设置中有体现」。
    """
    return {
        "groups": [
            {
                "key": "app",
                "items": [
                    {"key": "app_name", "env": "SAG_APP_NAME", "value": _settings.app_name},
                    {"key": "environment", "env": "SAG_ENVIRONMENT", "value": _settings.environment},
                    {"key": "debug", "env": "SAG_DEBUG", "value": _settings.debug},
                    {"key": "access_token_expire_minutes", "env": "SAG_ACCESS_TOKEN_EXPIRE_MINUTES", "value": _settings.access_token_expire_minutes},
                    {"key": "allow_registration", "env": "SAG_ALLOW_REGISTRATION", "value": _settings.allow_registration},
                    {"key": "cors_origins", "env": "SAG_CORS_ORIGINS", "value": ",".join(_settings.cors_origins)},
                ],
            },
            {
                "key": "database",
                "items": [
                    {"key": "database_url", "env": "SAG_DATABASE_URL", "value": _settings.database_url},
                    {"key": "database_pool_size", "env": "SAG_DATABASE_POOL_SIZE", "value": _settings.database_pool_size},
                    {"key": "database_max_overflow", "env": "SAG_DATABASE_MAX_OVERFLOW", "value": _settings.database_max_overflow},
                    {"key": "database_pool_timeout", "env": "SAG_DATABASE_POOL_TIMEOUT", "value": _settings.database_pool_timeout},
                    {"key": "database_sqlite_pool_size", "env": "SAG_DATABASE_SQLITE_POOL_SIZE", "value": _settings.database_sqlite_pool_size},
                    {"key": "database_sqlite_max_overflow", "env": "SAG_DATABASE_SQLITE_MAX_OVERFLOW", "value": _settings.database_sqlite_max_overflow},
                ],
            },
            {
                "key": "storage",
                "items": [
                    {"key": "data_dir", "env": "SAG_DATA_DIR", "value": _settings.data_dir},
                    {"key": "upload_dir", "env": "SAG_UPLOAD_DIR", "value": _settings.upload_dir},
                    {"key": "max_upload_mb", "env": "SAG_MAX_UPLOAD_MB", "value": _settings.max_upload_mb},
                    {"key": "allowed_upload_exts", "env": "SAG_ALLOWED_UPLOAD_EXTS", "value": " ".join(sorted(_settings.allowed_upload_exts))},
                ],
            },
            {
                "key": "zleap",
                "items": [
                    {"key": "sag_vector_provider", "env": "SAG_VECTOR_PROVIDER", "value": _settings.sag_vector_provider},
                    {"key": "sag_relational_provider", "env": "SAG_RELATIONAL_PROVIDER", "value": _settings.sag_relational_provider},
                    {"key": "sag_pg_host", "env": "SAG_PG_HOST", "value": _settings.sag_pg_host},
                    {"key": "sag_pg_port", "env": "SAG_PG_PORT", "value": _settings.sag_pg_port},
                    {"key": "sag_pg_user", "env": "SAG_PG_USER", "value": _settings.sag_pg_user},
                    {"key": "sag_pg_database", "env": "SAG_PG_DATABASE", "value": _settings.sag_pg_database},
                ],
            },
            {
                "key": "llm",
                "items": [
                    {"key": "llm_extra_body", "env": "SAG_LLM_EXTRA_BODY", "value": json.dumps(_settings.llm_extra_body, ensure_ascii=False) if _settings.llm_extra_body is not None else None},
                ],
            },
        ],
    }


def effective_system_preferences() -> dict[str, str]:
    return {"timezone": _settings.timezone}


async def save_system_preferences(session: AsyncSession, patch: dict) -> dict[str, str]:
    row = await _load_row(session, _PREFERENCES_KEY)
    stored = dict(row.value) if row and isinstance(row.value, dict) else {}
    timezone = patch.get("timezone")
    if isinstance(timezone, str):
        stored["timezone"] = timezone

    if row is None:
        session.add(Setting(scope=_SCOPE, key=_PREFERENCES_KEY, value=stored))
    else:
        row.value = stored
    await session.commit()

    if isinstance(stored.get("timezone"), str):
        _settings.timezone = stored["timezone"]
    return effective_system_preferences()


async def save_model_config(session: AsyncSession, patch: dict) -> dict:
    """合并保存模型配置：入库 + 覆盖 settings 单例；返回生效配置（脱敏）。

    约定（配合 `exclude_unset`）：
    - 字段未出现 → 保持不变；
    - 密钥字段值为空 → 忽略（保留原密钥，避免误清空）；空值仅经显式非空覆盖；
    - 可空字段（base_url / dimensions）值为空 → 置 None（清除）。
    """
    row = await _load_row(session)
    raw = dict(row.value) if row and isinstance(row.value, dict) else {}
    stored = _normalize_overrides(raw)

    for key, value in patch.items():
        if key not in _FIELDS:
            continue
        if key in _SECRET_FIELDS:
            if value:  # 仅非空才更新；空/None 保留原值
                stored[key] = str(value)
            continue
        if key in _NULLABLE_FIELDS and (value is None or value == ""):
            stored[key] = None
            continue
        stored[key] = value

    if "search_rerank_mode" in patch:
        stored["search_llm_rerank_enabled"] = patch["search_rerank_mode"] == "llm"

    stored = _normalize_overrides(stored)

    if row is None:
        session.add(Setting(scope=_SCOPE, key=_KEY, value=stored))
    else:
        row.value = stored
    await session.commit()

    apply_overrides(_settings, stored)
    # embedding 后端随 provider（api/local）切换即时生效
    from sag_api.sag.embedding_backend import install_embedding_backend

    install_embedding_backend(_settings)
    return effective_model_config()


async def save_302_quick_setup(session: AsyncSession, api_key: str) -> dict:
    """用单个 302.AI Key 写入生成、向量、MinerU 与快速检索预设。"""
    return await save_model_config(
        session,
        {
            **QUICK_SETUP_302,
            "llm_api_key": api_key,
            "embedding_api_key": api_key,
            "mineru_api_key": api_key,
        },
    )


async def save_302_mineru_setup(session: AsyncSession) -> dict:
    """为已有 302 模型配置复用现有 Key，不把密钥回传给浏览器。"""
    candidates = (
        (_settings.llm_base_url, _settings.llm_api_key),
        (_settings.effective_embedding_base_url, _settings.effective_embedding_api_key),
    )
    for base_url, api_key in candidates:
        parsed = urlparse(base_url or "")
        host = (parsed.hostname or "").lower()
        if host not in {"api.302.ai", "api.302ai.cn"} or not api_key:
            continue
        return await save_model_config(
            session,
            {
                "document_parser": "auto",
                "mineru_base_url": "https://api.302ai.cn",
                "mineru_api_key": api_key,
                "mineru_version": "2.5",
            },
        )
    raise ConfigurationError("未找到可复用的 302.AI 模型 API Key")
