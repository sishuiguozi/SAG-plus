from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.db import SessionLocal, get_session
from sag_api.core.deps import get_current_user
from sag_api.core.errors import ApiError, ConflictError, ForbiddenError, ValidationError
from sag_api.core.logging import get_logger
from sag_api.core.model_providers import model_provider_catalog
from sag_api.db.models import Source, User
from sag_api.generation import LLMClient
from sag_api.mcp.server import MCP_TOOL_DETAILS, MCP_TOOL_NAMES
from sag_api.schemas.system import (
    LocalModelDownloadRequest,
    ModelConfigUpdate,
    QuickModelSetupRequest,
    SystemPreferencesUpdate,
)
from sag_api.services import settings_service

router = APIRouter(prefix="/system", tags=["system"])
log = get_logger("system")
_local_model_manager = None


def _get_local_model_manager():
    """Keep download state in-process while following a changed data directory."""
    from sag_api.sag.local_model_manager import LocalModelManager

    global _local_model_manager
    model_dir = Path(settings.embedding_local_model_path()).parent
    if _local_model_manager is None or _local_model_manager.model_dir != model_dir:
        _local_model_manager = LocalModelManager(model_dir)
    return _local_model_manager


def _capabilities() -> dict:
    from sag_api.sag.embedding_backend import local_embedding_status

    embedding = local_embedding_status(settings)
    return {
        "llm_configured": settings.llm_configured,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "context_window": settings.llm_context_window,
        "embedding_provider": settings.embedding_provider,
        "local_embedding": embedding,
        "embedding_model": settings.embedding_model,
        "document_parser": settings.document_parser,
        "effective_document_parser": settings.effective_document_parser,
        "mineru_configured": settings.mineru_configured,
        "vector_provider": settings.sag_vector_provider,
        "language": settings.sag_language,
        "search_strategy": settings.search_strategy,
        "timezone": settings.timezone,
        "max_upload_mb": settings.max_upload_mb,
        "allowed_upload_exts": sorted(settings.allowed_upload_exts),
    }


@router.get("/health")
async def health() -> dict:
    """存活探针：进程在跑即 200（不触碰依赖）。"""
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> JSONResponse:
    """就绪探针：数据库可连通才 200，否则 503（供 compose/K8s 健康检查）。"""
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        log.warning("就绪检查失败：%s", e)
        return JSONResponse(status_code=503, content={"status": "unavailable", "db": False})
    return JSONResponse(content={"status": "ready", "db": True})


@router.get("/capabilities")
async def capabilities() -> dict:
    """能力探测：供前端判断是否已配置 LLM、当前引擎后端等。"""
    return _capabilities()


@router.get("/metrics")
async def performance_metrics(
    since: int | None = Query(default=None, ge=60, le=86400),
    _user: User = Depends(get_current_user),
) -> dict:
    """SAG-OPT-604：运行期请求性能指标（P50/P95/P99、慢请求、按路由汇总）。"""
    from sag_api.core.performance import performance_ring

    return performance_ring.summary(since_seconds=float(since) if since else None)


def _sqlite_file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _sqlite_path_from_url(url: str, *, base: Path) -> Path | None:
    """把 sqlite+aiosqlite:/// 形式的 URL 解析为磁盘路径（相对 base）。"""
    marker = "sqlite+aiosqlite:///"
    plain = "sqlite:///"
    if url.startswith(marker):
        raw = url[len(marker):]
    elif url.startswith(plain):
        raw = url[len(plain):]
    else:
        return None
    if not raw or raw == ":memory:":
        return None
    path = Path(raw)
    return path if path.is_absolute() else base / path


@router.post("/checkpoint")
async def create_upgrade_checkpoint(request: Request) -> dict:
    """SAG-OPT-703：自动更新前创建数据库兼容性检查点（内部端点）。

    桌面运行时在应用更新安装前调用：把元数据库与引擎库做 SQLite 在线备份到
    ``<data_dir>/upgrade-checkpoints/<timestamp>/``，并写 manifest。
    校验 ``X-SAG-INTERNAL`` 头等于运行密钥，防止局域网内其它进程滥用。
    """
    import json
    import shutil
    import sqlite3
    from datetime import UTC, datetime

    internal = request.headers.get("x-sag-internal", "")
    if not settings.secret_key or not internal or internal != settings.secret_key:
        raise ForbiddenError("内部端点：X-SAG-INTERNAL 校验失败")

    data_dir = Path(settings.data_dir)
    checkpoint_root = data_dir / "upgrade-checkpoints"
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = checkpoint_root / f"pre-upgrade-{stamp}"
    target.mkdir(parents=True, exist_ok=True)

    engine_db = data_dir / "sag.db"
    meta_db = _sqlite_path_from_url(settings.database_url, base=data_dir)
    sources = {
        "engine": engine_db if engine_db.exists() else None,
        "metadata": meta_db if meta_db and meta_db.exists() else None,
    }
    files: list[dict[str, object]] = []
    for label, src in sources.items():
        if not src:
            continue
        dest = target / f"{label}.db"
        try:
            src_con = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True, timeout=60)
            dest_con = sqlite3.connect(str(dest))
            try:
                src_con.backup(dest_con)
            finally:
                dest_con.close()
                src_con.close()
            files.append({"label": label, "name": dest.name, "size_bytes": dest.stat().st_size})
            log.info("更新检查点：%s -> %s（%.1f MB）", label, dest, dest.stat().st_size / 1e6)
        except Exception as e:  # noqa: BLE001
            log.error("更新检查点失败：%s 备份异常：%s", label, e)
            files.append({"label": label, "name": dest.name if dest.exists() else label, "size_bytes": 0, "error": str(e)})

    lancedb_dir = data_dir / "lancedb"
    lancedb_bytes = 0
    if lancedb_dir.exists():
        for f in lancedb_dir.rglob("*"):
            if f.is_file():
                lancedb_bytes += f.stat().st_size

    queue = {}
    try:
        import sqlite3 as _sq
        qcon = _sq.connect(f"file:{meta_db.as_posix()}?mode=ro", uri=True, timeout=30) if meta_db else None
        if qcon is not None:
            qcon.row_factory = _sq.Row
            rows = qcon.execute(
                "SELECT status, count(*) n FROM vector_write_jobs GROUP BY status"
            ).fetchall()
            queue = {r["status"]: r["n"] for r in rows}
            qcon.close()
    except Exception as e:  # noqa: BLE001
        queue = {"error": str(e)}

    manifest = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "checkpoint_dir": str(target),
        "desktop_version": request.headers.get("x-sag-desktop-version") or None,
        "files": files,
        "vector_write_jobs": queue,
        "lancedb": {"path": str(lancedb_dir), "directory_bytes": lancedb_bytes},
        "engine_db_size_bytes": _sqlite_file_size(str(engine_db)) if engine_db.exists() else 0,
        "meta_db_size_bytes": _sqlite_file_size(str(meta_db)) if meta_db else 0,
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


@router.get("/model-config")
async def get_model_config(
    _user: User = Depends(get_current_user),
) -> dict:
    """当前生效的模型与检索配置（密钥脱敏为 *_set 布尔）。"""
    return settings_service.effective_model_config()


@router.get("/local-models")
async def get_local_model_status(
    _user: User = Depends(get_current_user),
) -> dict:
    """Local embedding model and llama-cpp-python installation status."""
    return _get_local_model_manager().status()


@router.post("/local-models/backend/install")
async def install_local_model_backend(
    _user: User = Depends(get_current_user),
) -> dict:
    """Install llama-cpp-python into this API's virtual environment in the background."""
    return await _get_local_model_manager().install_backend()


@router.post("/local-models/download")
async def download_local_models(
    body: LocalModelDownloadRequest,
    _user: User = Depends(get_current_user),
) -> dict:
    """Start downloads for selected BGE-M3 GGUF variants; status is polled separately."""
    try:
        return await _get_local_model_manager().download(body.files)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


@router.get("/model-providers")
async def get_model_providers(
    _user: User = Depends(get_current_user),
) -> list[dict[str, object]]:
    """前后端共享的模型接入能力与技术默认值。"""
    return model_provider_catalog()


@router.get("/preferences")
async def get_system_preferences(
    _user: User = Depends(get_current_user),
) -> dict[str, str]:
    """Presentation preferences shared by this local-first installation."""
    return settings_service.effective_system_preferences()


@router.put("/preferences")
async def update_system_preferences(
    body: SystemPreferencesUpdate,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    return await settings_service.save_system_preferences(
        session,
        body.model_dump(exclude_unset=True),
    )


@router.get("/model-setup")
async def get_model_setup_status(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """首次进入时判断是否需要展示快捷模型配置。"""
    return await settings_service.model_setup_status(session)


@router.get("/mcp")
async def knowledge_mcp_descriptor(
    request: Request,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """返回将整个 SAG 知识库挂入外部 MCP 宿主的连接信息。"""
    source_count = await session.scalar(select(func.count(Source.id))) or 0
    base = str(request.base_url).rstrip("/")
    return {
        "name": "SAG 知识库",
        "scope": "knowledge_base",
        "source_count": source_count,
        "tools": list(MCP_TOOL_NAMES),
        "tool_details": list(MCP_TOOL_DETAILS),
        "http": {
            "transport": "streamable-http",
            "url": f"{base}/mcp/",
            "headers": {"Authorization": "Bearer <SAG_TOKEN>"},
            "note": (
                "默认开放全部信源；Dify 等宿主请使用 streamable_http/Streamable HTTP 传输，"
                "可在 URL 添加 ?source_id=<id> 临时限定单个信源。"
            ),
        },
        "stdio": {
            "command": "python",
            "args": ["-m", "sag_api.mcp.server"],
            "env": {},
            "note": "默认开放全部信源；设置 SAG_MCP_SOURCE_ID 可限定单个信源。",
        },
    }


@router.post("/model-setup/302")
async def quick_setup_302(
    body: QuickModelSetupRequest,
    request: Request,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """只接收一个 302.AI Key，写入生成、向量、MinerU 与检索预设。"""
    status = await settings_service.model_setup_status(session)
    if not status["required"]:
        raise ConflictError("模型配置已存在，请在设置中修改")

    config = await settings_service.save_302_quick_setup(session, body.api_key)
    await request.app.state.engine_manager.aclose_all()
    return {"config": config, "capabilities": _capabilities()}


@router.get("/config/env")
async def env_only_config(_user: User = Depends(get_current_user)) -> dict:
    """仅能通过 .env / SAG_* 环境变量修改的配置清单（设置界面只读展示，重启后生效）。"""
    return settings_service.env_only_config()


@router.put("/model-config")
async def update_model_config(
    body: ModelConfigUpdate,
    request: Request,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """保存运行期配置；仅在模型/向量配置实际变化时安全重建引擎。"""
    patch = body.model_dump(exclude_unset=True)
    before = settings_service.effective_model_config()
    config = await settings_service.save_model_config(session, patch)

    # 解析器/检索参数保存无需打断暖引擎；只有引擎配置真的变化才安全重建。
    engine_fields = {
        "llm_provider",
        "llm_base_url",
        "llm_model",
        "llm_temperature",
        "llm_max_tokens",
        "llm_timeout_ms",
        "llm_max_retries",
        "embedding_model",
        "embedding_base_url",
        "embedding_dimensions",
        "sag_language",
    }
    engine_changed = any(before.get(key) != config.get(key) for key in engine_fields)
    engine_changed = engine_changed or bool(patch.get("llm_api_key") or patch.get("embedding_api_key"))
    if engine_changed:
        await request.app.state.engine_manager.aclose_all()
    if "job_concurrency" in patch and isinstance(patch["job_concurrency"], int):
        # 队列并发可运行期调整：增 worker 直接起，减 worker 发哨兵安全退出。
        job_queue = getattr(request.app.state, "job_queue", None)
        if job_queue is not None and hasattr(job_queue, "resize"):
            await job_queue.resize(patch["job_concurrency"])
    return {"config": config, "capabilities": _capabilities()}


@router.post("/model-config/mineru/302")
async def configure_302_mineru(
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """已有 302 LLM/Embedding 用户一键复用服务端保存的 Key 启用 MinerU。"""
    config = await settings_service.save_302_mineru_setup(session)
    return {"config": config, "capabilities": _capabilities()}


@router.post("/model-config/test")
async def test_model_config(
    request: Request,
    body: ModelConfigUpdate | None = None,
    _user: User = Depends(get_current_user),
) -> dict:
    """连接测试：优先验证表单草稿，不持久化也不修改运行期单例。"""
    llm: LLMClient
    active = settings
    if body is None:
        llm = request.app.state.llm
    else:
        patch = body.model_dump(exclude_unset=True)
        updates = {
            key: (None if key in {"llm_base_url"} and value == "" else value)
            for key, value in patch.items()
            if not (key == "llm_api_key" and not value)
        }
        active = settings.model_copy(update=updates)
        llm = LLMClient(active)
    if not llm.configured:
        return {"ok": False, "message": "尚未配置 API Key"}
    try:
        await llm.complete([{"role": "user", "content": "ping"}])
        return {
            "ok": True,
            "message": f"连接成功 · {active.llm_provider} / {active.llm_model}",
        }
    except ApiError as e:
        return {"ok": False, "message": e.message}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": str(e)}
