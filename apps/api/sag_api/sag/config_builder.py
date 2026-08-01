"""由 sag 配置装配 zleap-sag 的 `EngineConfig`。

支持信源级覆盖（`overrides`）——目前支持 `language`，未来可扩展 `entity_types` 等。
"""

from __future__ import annotations

from typing import Any

from zleap.sag import EngineConfig
from zleap.sag.config import EmbeddingConfig, LLMConfig, RelationalConfig

from sag_api.core.config import Settings

# LLM 未配置时的占位符：允许 EngineConfig 构造 / start() 建 schema（离线路径），
# 真正的 ingest / extract / search 会在运行时因缺少凭证而报错（服务层已前置守卫）。
_PLACEHOLDER = "not-configured"


def build_engine_config(settings: Settings, *, overrides: dict[str, Any] | None = None) -> EngineConfig:
    overrides = overrides or {}

    llm = LLMConfig(
        api_key=settings.llm_api_key or _PLACEHOLDER,
        model=settings.routed_llm_model,
        provider="litellm",
        base_url=settings.llm_base_url,
        temperature=settings.effective_llm_temperature,
        max_tokens=settings.llm_max_tokens,
        timeout=max(1, (settings.llm_timeout_ms + 999) // 1000),
        max_retries=settings.llm_max_retries,
    )
    embedding = EmbeddingConfig(
        model=settings.embedding_model,
        base_url=settings.effective_embedding_base_url,
        api_key=settings.effective_embedding_api_key or _PLACEHOLDER,
        dimensions=settings.embedding_dimensions,
    )

    kwargs: dict[str, Any] = {
        "llm": llm,
        "embedding": embedding,
        "data_dir": settings.data_dir,
        "language": overrides.get("language", settings.sag_language),
        "vector_provider": settings.sag_vector_provider,
    }

    # 生产：切到关系型后端（如 Postgres），与 pgvector 单库统一
    if settings.sag_relational_provider:
        kwargs["relational"] = RelationalConfig(
            provider=settings.sag_relational_provider,
            host=settings.sag_pg_host,
            port=settings.sag_pg_port,
            user=settings.sag_pg_user,
            password=settings.sag_pg_password,
            database=settings.sag_pg_database,
        )

    return EngineConfig(**kwargs)
