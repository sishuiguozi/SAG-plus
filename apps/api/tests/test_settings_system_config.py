"""系统/高级配置：新设置项（向量/磁盘/性能/引擎/检索/SQLite/MinerU/宇宙预算）持久化与生效。

全程离线且不留全局副作用：`finally` 删除 settings 表行 + 还原被改的 `settings` 单例字段。
"""

import httpx
import pytest
from pydantic import ValidationError

from sag_api.core.config import Settings, settings
from sag_api.schemas.system import ModelConfigUpdate

# 本测试会改动的 settings 单例字段（finally 全部还原）
_TOUCHED = (
    "llm_tool_choice_strategy",
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
    "disk_guard_enabled",
    "disk_warn_gb",
    "disk_pause_aux_gb",
    "disk_pause_vector_gb",
    "disk_pause_ingest_gb",
    "disk_check_interval_seconds",
    "performance_slow_threshold_ms",
    "performance_window",
    "engine_cache_size",
    "engine_warmup_count",
    "job_max_attempts",
    "document_strict_filtering",
    "parent_chunk_max_tokens",
    "parent_chunk_vectorize",
    "search_source_candidate_limit",
    "search_source_concurrency",
    "search_source_timeout",
    "search_fallback_vector",
    "database_sqlite_pragma_tuning_enabled",
    "database_sqlite_synchronous",
    "database_sqlite_cache_size",
    "database_sqlite_mmap_size",
    "database_sqlite_temp_store",
    "mineru_parse_method",
    "mineru_request_timeout",
    "mineru_poll_interval",
    "mineru_poll_timeout",
    "mineru_result_max_mb",
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
)


def test_tool_choice_strategy_default_and_schema_values() -> None:
    assert Settings(_env_file=None).llm_tool_choice_strategy == "forced_no_thinking"
    for value in (
        "forced_no_thinking",
        "forced_with_thinking",
        "auto",
        "all_no_thinking",
    ):
        assert ModelConfigUpdate(llm_tool_choice_strategy=value).llm_tool_choice_strategy == value
    with pytest.raises(ValidationError):
        ModelConfigUpdate(llm_tool_choice_strategy="sometimes")


async def _register(c, email):
    r = await c.post("/api/v1/auth/register", json={"email": email, "password": "password123"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_system_config_groups_persist_and_apply():
    from sqlalchemy import delete

    from sag_api.core.db import SessionLocal
    from sag_api.db.models import Setting
    from sag_api.main import app

    snapshot = {k: getattr(settings, k) for k in _TOUCHED}
    transport = httpx.ASGITransport(app=app)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                A = await _register(c, "syscfg@t.com")

                # GET 返回新字段（读 settings 单例，含运行期覆盖值）
                body = (await c.get("/api/v1/system/model-config", headers=A)).json()
                for key in _TOUCHED:
                    assert key in body, f"GET missing {key}"

                # PUT 一组新字段 → 200、config 回显、settings 单例即时覆盖
                patch = {
                    # LLM 工具调用
                    "llm_tool_choice_strategy": "all_no_thinking",
                    # 向量
                    "lancedb_ann_enabled": False,
                    "lancedb_search_refine_factor": 0,
                    "lancedb_search_nprobes": 32,
                    "vector_write_job_batch_size": 300,
                    "vector_write_tail_flush_seconds": 2.5,
                    "vector_append_new_enabled": False,
                    "vector_append_lookup_chunk_size": 800,
                    "aux_vector_deferred_enabled": False,
                    "source_chunk_vector_embedding_batch_size": 30,
                    "source_chunk_vector_index_batch_size": 150,
                    # 磁盘
                    "disk_guard_enabled": False,
                    "disk_warn_gb": 25.0,
                    "disk_pause_aux_gb": 15.0,
                    "disk_pause_vector_gb": 8.0,
                    "disk_pause_ingest_gb": 4.0,
                    "disk_check_interval_seconds": 120,
                    # 性能
                    "performance_slow_threshold_ms": 1500,
                    "performance_window": 2048,
                    # 引擎
                    "engine_cache_size": 24,
                    "engine_warmup_count": 2,
                    "job_max_attempts": 5,
                    "document_strict_filtering": True,
                    # A4 父子分块
                    "parent_chunk_max_tokens": 1536,
                    "parent_chunk_vectorize": False,
                    # 检索
                    "search_source_candidate_limit": 32,
                    "search_source_concurrency": 8,
                    "search_source_timeout": 20.0,
                    "search_fallback_vector": False,
                    # SQLite
                    "database_sqlite_pragma_tuning_enabled": False,
                    "database_sqlite_synchronous": "FULL",
                    "database_sqlite_cache_size": -131072,
                    "database_sqlite_mmap_size": 134217728,
                    "database_sqlite_temp_store": "FILE",
                    # MinerU
                    "mineru_parse_method": "ocr",
                    "mineru_request_timeout": 90.0,
                    "mineru_poll_interval": 3.0,
                    "mineru_poll_timeout": 600.0,
                    "mineru_result_max_mb": 200,
                    # 知识宇宙预算
                    "universe_manifest_source_limit": 512,
                    "universe_timeline_event_page_size": 30,
                    "universe_event_entity_limit": 6,
                    "universe_lod_orbit_px": 90,
                    "universe_lod_near_px": 200,
                    "universe_lod_deep_px": 400,
                    "universe_lod_hysteresis_px": 30,
                    "universe_lod_debounce_ms": 300,
                    "universe_proxy_budget_desktop": 12000,
                    "universe_proxy_budget_mobile": 3000,
                    "universe_node_budget_desktop": 800,
                    "universe_node_budget_mobile": 600,
                    "universe_edge_budget_desktop": 1200,
                    "universe_edge_budget_mobile": 900,
                    "universe_planet_radius_min": 50.0,
                    "universe_planet_radius_max": 160.0,
                    "universe_planet_radius_scale": 30.0,
                }
                r = await c.put("/api/v1/system/model-config", headers=A, json=patch)
                assert r.status_code == 200, r.text
                cfg = r.json()["config"]
                for key, value in patch.items():
                    assert cfg[key] == value, f"{key}: {cfg[key]} != {value}"
                    assert getattr(settings, key) == value, f"singleton {key} not applied"

                # GET 返回持久化后的值
                again = (await c.get("/api/v1/system/model-config", headers=A)).json()
                for key, value in patch.items():
                    assert again[key] == value, f"GET {key} mismatch"

                # env-only 只读清单：分组完整、不含密钥
                env = (await c.get("/api/v1/system/config/env", headers=A)).json()
                group_keys = {g["key"] for g in env["groups"]}
                assert group_keys == {"app", "database", "storage", "zleap", "llm"}
                items = {i["key"] for g in env["groups"] for i in g["items"]}
                assert {"database_url", "data_dir", "sag_vector_provider", "cors_origins", "llm_extra_body"} <= items
                assert "password" not in items
                assert all("password" not in (i["key"] + i["env"]).lower() for g in env["groups"] for i in g["items"])

                # 非法值 → 422
                for invalid in (
                    {"llm_tool_choice_strategy": "sometimes"},
                    {"lancedb_search_nprobes": -1},
                    {"lancedb_search_refine_factor": 101},
                    {"vector_write_job_batch_size": 50},
                    {"disk_check_interval_seconds": 2},
                    {"performance_window": 32},
                    {"search_source_concurrency": 0},
                    {"parent_chunk_max_tokens": 50},
                    {"search_source_timeout": 0.5},
                    {"database_sqlite_synchronous": "NOPE"},
                    {"database_sqlite_cache_size": 100},
                    {"database_sqlite_temp_store": "RAM"},
                    {"mineru_parse_method": "scan"},
                    {"mineru_request_timeout": 0},
                    {"mineru_result_max_mb": 0},
                    {"universe_event_entity_limit": 2},
                    {"universe_lod_deep_px": 100},
                    {"universe_node_budget_desktop": 100},
                    {"universe_planet_radius_max": 1000},
                ):
                    resp = await c.put("/api/v1/system/model-config", headers=A, json=invalid)
                    assert resp.status_code == 422, f"{invalid} -> {resp.status_code}"
    finally:
        async with SessionLocal() as s:
            await s.execute(
                delete(Setting).where(
                    Setting.scope == "global",
                    Setting.key.in_(["model_config", "system_preferences"]),
                )
            )
            await s.commit()
        for key, value in snapshot.items():
            setattr(settings, key, value)
