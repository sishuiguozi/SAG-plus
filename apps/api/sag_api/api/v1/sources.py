from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.connectors import registry
from sag_api.core.config import settings
from sag_api.core.db import get_session
from sag_api.core.deps import get_current_user, get_engine_manager, get_job_queue
from sag_api.core.errors import ForbiddenError
from sag_api.core.security import verify_password
from sag_api.db.models import User
from sag_api.jobs import JobQueue
from sag_api.mcp.server import MCP_TOOL_DETAILS, MCP_TOOL_NAMES
from sag_api.sag import EngineManager
from sag_api.sag.vector_write_items import aux_index_backfill_status
from sag_api.schemas.common import Ok
from sag_api.schemas.document import DocumentActivityResponse
from sag_api.schemas.job import JobOut
from sag_api.schemas.source import (
    ConnectorOut,
    IngestStatsOut,
    SourceCodeConfig,
    SourceCreate,
    SourceDeleteRequest,
    SourceOut,
    SourceUpdate,
    VectorBackfillOut,
)
from sag_api.services.document_service import recent_document_activity
from sag_api.services.source_service import (
    create_source,
    delete_source,
    get_source,
    get_source_code_config,
    ingest_stats,
    list_sources,
    source_document_status_counts,
    sync_source,
    update_source,
    update_source_code_config,
)

router = APIRouter(prefix="/sources", tags=["sources"])


def _source_out(
    source,
    counts: dict[str, int] | None = None,
    vector_backfill: dict | None = None,
) -> SourceOut:
    counts = counts or {}
    return SourceOut.model_validate(source).model_copy(
        update={
            "document_count": int(counts.get("total", source.document_count or 0)),
            "ready_document_count": int(counts.get("ready", 0)),
            "pending_document_count": int(counts.get("pending", 0)),
            "paused_document_count": int(counts.get("paused", 0)),
            "failed_document_count": int(counts.get("failed", 0)),
            "vector_backfill": VectorBackfillOut.model_validate(vector_backfill or {}),
        }
    )


# 注意：静态路由须在 /{source_id} 之前声明
@router.get("/connectors", response_model=list[ConnectorOut])
async def list_connectors() -> list[ConnectorOut]:
    return [ConnectorOut(**c.meta.to_public()) for c in registry.all()]


@router.get("", response_model=list[SourceOut])
async def list_(
    _user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> list[SourceOut]:
    sources = await list_sources(session)
    counts = await source_document_status_counts(session, [source.id for source in sources])
    backfills = await aux_index_backfill_status(
        session,
        [source.sag_source_config_id for source in sources],
        aux_vector_deferred_enabled=settings.aux_vector_deferred_enabled,
    )
    return [
        _source_out(source, counts.get(source.id), backfills.get(source.sag_source_config_id))
        for source in sources
    ]


@router.get("/ingest-stats", response_model=IngestStatsOut)
async def stats_(
    _user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> IngestStatsOut:
    return await ingest_stats(session)


@router.get("/{source_id}/activity", response_model=DocumentActivityResponse)
async def source_activity(
    source_id: str,
    limit: int = Query(default=30, ge=1, le=100),
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> DocumentActivityResponse:
    """SAG-OPT-502：该信源最近更新的文档快照，前端轮询对比生成状态事件。"""
    await get_source(session, source_id)  # 404 检查
    events = await recent_document_activity(session, source_id, limit)
    return DocumentActivityResponse(events=events)


@router.post("", response_model=SourceOut, status_code=201)
async def create(
    body: SourceCreate,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
) -> SourceOut:
    source = await create_source(session, body, engine_manager=engine_manager)
    return _source_out(source)


@router.get("/{source_id}", response_model=SourceOut)
async def get_(
    source_id: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SourceOut:
    source = await get_source(session, source_id)
    counts = await source_document_status_counts(session, [source.id])
    backfills = await aux_index_backfill_status(
        session,
        [source.sag_source_config_id],
        aux_vector_deferred_enabled=settings.aux_vector_deferred_enabled,
    )
    return _source_out(source, counts.get(source.id), backfills.get(source.sag_source_config_id))


@router.get("/{source_id}/code-config", response_model=SourceCodeConfig)
async def get_code_config(
    source_id: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SourceCodeConfig:
    return await get_source_code_config(session, source_id)


@router.patch("/{source_id}/code-config", response_model=SourceCodeConfig)
async def update_code_config(
    source_id: str,
    body: SourceCodeConfig,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SourceCodeConfig:
    return await update_source_code_config(session, source_id, body)


@router.patch("/{source_id}", response_model=SourceOut)
async def update_(
    source_id: str,
    body: SourceUpdate,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    job_queue: JobQueue = Depends(get_job_queue),
) -> SourceOut:
    source = await update_source(session, source_id, body, job_queue=job_queue)
    counts = await source_document_status_counts(session, [source.id])
    backfills = await aux_index_backfill_status(
        session,
        [source.sag_source_config_id],
        aux_vector_deferred_enabled=settings.aux_vector_deferred_enabled,
    )
    return _source_out(source, counts.get(source.id), backfills.get(source.sag_source_config_id))


@router.delete("/{source_id}", response_model=Ok)
async def delete_(
    source_id: str,
    body: SourceDeleteRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
    job_queue: JobQueue = Depends(get_job_queue),
) -> Ok:
    """删除信源：需账户密码确认（防误删/防他人删库）。"""
    from sag_api.core.config import settings

    if not verify_password(body.password, user.password_hash):
        raise ForbiddenError("密码不正确，无法删除信源")

    await delete_source(
        session,
        source_id,
        engine_manager=engine_manager,
        upload_dir=settings.upload_dir,
        job_queue=job_queue,
    )
    return Ok(detail="信源已删除")


@router.get("/{source_id}/chunks/{chunk_id}")
async def get_chunk(
    source_id: str,
    chunk_id: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
) -> dict:
    """引用溯源：读取某分块的完整原文。"""
    from sag_api.core.errors import NotFoundError

    source = await get_source(session, source_id)
    chunk = await engine_manager.get_chunk(source.sag_source_config_id, chunk_id, source=source)
    if chunk is None:
        raise NotFoundError("原文分块不存在")
    return {**chunk.model_dump(), "source_id": source.id, "source_name": source.name}


@router.get("/{source_id}/mcp")
async def mcp_descriptor(
    source_id: str,
    request: Request,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """信源即 MCP：返回把该信源挂进外部宿主（Claude Desktop / Cursor）的连接信息。"""
    source = await get_source(session, source_id)
    base = str(request.base_url).rstrip("/")
    return {
        "source_id": source.id,
        "source_name": source.name,
        "tools": list(MCP_TOOL_NAMES),
        "tool_details": list(MCP_TOOL_DETAILS),
        "http": {
            "transport": "streamable-http",
            "url": f"{base}/mcp/?source_id={source.id}",
            "headers": {"Authorization": "Bearer <SAG_TOKEN>"},
            "note": (
                "在支持 Streamable HTTP MCP 的宿主中填此 URL；"
                "Dify 配置可使用 transport=streamable_http，并在 Authorization 头携带 Bearer <token>。"
            ),
        },
        "stdio": {
            "command": "python",
            "args": ["-m", "sag_api.mcp.server"],
            "env": {"SAG_MCP_SOURCE_ID": source.id},
            "note": "面向仅支持 stdio 的宿主；需在 apps/api 的 Python 环境下运行。",
        },
    }


@router.post("/{source_id}/sync", response_model=JobOut)
async def sync(
    source_id: str,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    job_queue: JobQueue = Depends(get_job_queue),
) -> JobOut:
    job = await sync_source(session, source_id, job_queue=job_queue)
    return JobOut.model_validate(job)
