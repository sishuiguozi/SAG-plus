"""sag-api 应用入口。"""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.gzip import GZipMiddleware

from sag_agent import AgentRuntime
from sag_api import __version__
from sag_api.api.v1 import api_router
from sag_api.branding import PRODUCT_NAME
from sag_api.code_ingest import TreeSitterResourceManager
from sag_api.core.config import settings
from sag_api.core.db import SessionLocal, dispose_db, init_db
from sag_api.core.errors import ApiError
from sag_api.core.litellm_policy import install_litellm_policy, uninstall_litellm_policy
from sag_api.core.logging import RequestContextMiddleware, configure_logging, get_logger
from sag_api.generation import LLMClient
from sag_api.jobs import InProcessAsyncQueue
from sag_api.sag import EngineManager
from sag_api.sag.chunking_compat import install_structural_chunking_patch
from sag_api.sag.compat import (
    install_zleap_sag_async_sqlite_reset_compat,
    install_zleap_sag_extract_compat,
    install_zleap_sag_sqlite_integer_compat,
    install_zleap_sag_sqlite_pool_compat,
)
from sag_api.sag.embedding_backend import install_embedding_backend
from sag_api.sag.lancedb_search_compat import install_zleap_sag_lancedb_ann_search_patch
from sag_api.sag.lancedb_write_compat import install_zleap_sag_lancedb_append_vs_merge_patch
from sag_api.sag.parent_child import install_parent_child_loader_patch
from sag_api.sag.vector_write_queue import (
    VectorWriteQueue,
    install_event_vector_queue_patch,
    install_source_chunk_vector_queue_patch,
)

log = get_logger("app")


# 已知不安全的默认密钥（生产环境拒绝启动）
_INSECURE_SECRETS = {
    "dev-insecure-secret-change-me-in-production-0123456789",
    "please-change-this-in-production-0123456789",
    "dev-secret-change-me",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging("DEBUG" if settings.debug else "INFO")
    if settings.environment == "prod" and settings.secret_key in _INSECURE_SECRETS:
        raise RuntimeError(
            "生产环境禁止使用默认 SAG_SECRET_KEY。请设置强随机值（≥32 字节），例如：openssl rand -hex 32"
        )
    os.makedirs(settings.data_dir, exist_ok=True)
    os.makedirs(settings.upload_dir, exist_ok=True)

    await init_db()

    # 把 DB 里保存的模型配置覆盖到 settings 单例（在构建 LLM/引擎之前）
    from sag_api.services.settings_service import apply_startup_overrides

    await apply_startup_overrides(SessionLocal)

    # 播种默认 agent（开箱即用的主对话入口；幂等）
    from sag_api.services.agent_domain import get_default_agent

    async with SessionLocal() as _session:
        await get_default_agent(_session)

    # zleap-sag 内部也调用 LiteLLM；全局 pre-call policy 让它与 Muse 生成链
    # 共享相同的 provider 参数，而不修改依赖包。
    install_zleap_sag_async_sqlite_reset_compat()
    install_zleap_sag_sqlite_pool_compat()
    install_zleap_sag_sqlite_integer_compat()
    install_zleap_sag_extract_compat()
    litellm_policy = install_litellm_policy(settings)
    from sag_api.core.disk_guard import DiskGuard

    app.state.disk_guard = DiskGuard(settings.data_dir, settings)
    app.state.engine_manager = EngineManager(settings)
    install_event_vector_queue_patch(SessionLocal)
    install_source_chunk_vector_queue_patch(SessionLocal)
    install_zleap_sag_lancedb_append_vs_merge_patch()
    install_zleap_sag_lancedb_ann_search_patch()
    install_structural_chunking_patch()
    install_parent_child_loader_patch()
    install_embedding_backend(settings)
    if settings.embedding_provider == "local":
        # 启动等待：本地向量模型加载 + 预热完成后再开放服务（desktop 等 /ready 后才进入）
        from sag_api.sag.embedding_backend import ensure_embedding_ready

        await asyncio.to_thread(ensure_embedding_ready, settings)
    app.state.vector_write_queue = VectorWriteQueue(SessionLocal, app.state.engine_manager)
    await app.state.vector_write_queue.start()
    app.state.llm = LLMClient(settings)
    app.state.agent_runtime = AgentRuntime()
    await app.state.agent_runtime.start()
    app.state.job_queue = InProcessAsyncQueue(
        SessionLocal, app.state.engine_manager, concurrency=settings.job_concurrency
    )
    await app.state.job_queue.start()

    tree_sitter_manager = TreeSitterResourceManager(Path(settings.data_dir) / "tree-sitter")
    app.state.tree_sitter_manager = tree_sitter_manager
    tree_sitter_manager.activate_if_ready()
    if settings.tree_sitter_auto_download:
        await tree_sitter_manager.start_download()

    # 后台预热最近使用的信源引擎（不阻塞启动；失败不影响服务）
    warmup_task = asyncio.create_task(_warmup_engines(app.state.engine_manager))

    log.info(
        "sag-api 已启动 · env=%s · llm_configured=%s · vector=%s",
        settings.environment,
        settings.llm_configured,
        settings.sag_vector_provider,
    )
    source_mcp = getattr(app.state, "source_mcp", None)
    try:
        # MCP 端点的会话管理器需在 lifespan 内运行；失败仅关闭 /mcp，不影响其余服务
        async with AsyncExitStack() as stack:
            if source_mcp is not None:
                try:
                    await stack.enter_async_context(source_mcp.session_manager.run())
                    log.info("MCP 端点已就绪 · /mcp/（全库）· 可选 ?source_id=<信源 id>")
                except Exception as e:  # noqa: BLE001
                    log.warning("MCP 会话管理器启动失败（/mcp 不可用）：%s", e)
            yield
    finally:
        try:
            warmup_task.cancel()
            with suppress(asyncio.CancelledError):
                await warmup_task
            await app.state.agent_runtime.stop()
            await app.state.job_queue.stop()
            await app.state.vector_write_queue.stop()
            await tree_sitter_manager.close()
            await app.state.engine_manager.aclose_all()
            await dispose_db()
        finally:
            uninstall_litellm_policy(litellm_policy)


async def _warmup_engines(engine_manager: EngineManager) -> None:
    """预热最近更新的信源引擎，缩短用户首个操作的等待。"""
    if settings.engine_warmup_count <= 0:
        return
    try:
        from sqlalchemy import select

        from sag_api.db.models import Source

        async with SessionLocal() as session:
            rows = (
                (
                    await session.execute(
                        select(Source).order_by(Source.updated_at.desc()).limit(settings.engine_warmup_count)
                    )
                )
                .scalars()
                .all()
            )
        for source in rows:
            try:
                await engine_manager.provision(source.sag_source_config_id, source)
            except Exception as e:  # noqa: BLE001
                log.warning("预热引擎失败 source=%s: %s", source.id, e)
        if rows:
            log.info("已预热 %d 个信源引擎", len(rows))
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("引擎预热任务异常：%s", e)


def create_app() -> FastAPI:
    app = FastAPI(
        title=f"{PRODUCT_NAME} API",
        version=__version__,
        summary="开源知识库平台 · 从信息源到知识问答",
        lifespan=lifespan,
    )

    cors_kwargs: dict = {
        "allow_origins": settings.cors_origins,
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
        "expose_headers": ["X-Request-Id"],
    }
    # 开发环境放行局域网前端（如 http://192.168.x.x:3000），避免本机 IP 访问时 CORS 拦截
    if settings.environment == "dev":
        cors_kwargs["allow_origin_regex"] = (
            r"https?://("
            r"localhost|"
            r"127\.0\.0\.1|"
            r"192\.168\.\d{1,3}\.\d{1,3}|"
            r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
            r"172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
            r")(:\d+)?"
        )
    app.add_middleware(CORSMiddleware, **cors_kwargs)
    # 图谱响应可能包含数百个节点和关系；只压缩较大的响应，小接口保持原样。
    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
    # 请求追踪（放在 CORS 之后添加 → 更外层执行，最先分配 request_id）
    app.add_middleware(RequestContextMiddleware)
    # SAG-OPT-604：请求耗时采集 + 慢请求日志（放在最内层，统计真实处理耗时）
    from sag_api.core.performance import PerformanceMiddleware, PerformanceRing

    app.add_middleware(
        PerformanceMiddleware,
        ring=PerformanceRing(
            window=settings.performance_window,
            slow_threshold_ms=float(settings.performance_slow_threshold_ms),
        ),
    )

    @app.exception_handler(ApiError)
    async def _handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        log.exception("未处理异常：%s", exc)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "服务器内部错误"}},
        )

    app.include_router(api_router)

    # 信源即 MCP：挂载 Streamable-HTTP 端点（失败不阻断应用启动）
    try:
        from sag_api.mcp.mount import attach_source_mcp

        app.state.source_mcp = attach_source_mcp(app)
    except Exception as e:  # noqa: BLE001
        app.state.source_mcp = None
        log.warning("MCP 端点挂载失败：%s", e)

    @app.get("/", tags=["system"])
    async def root() -> dict:
        return {"name": PRODUCT_NAME, "version": __version__, "docs": "/docs"}

    return app


app = create_app()
