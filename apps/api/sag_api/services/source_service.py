"""信源领域逻辑（单用户，扁平）。"""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.connectors import registry
from sag_api.core.config import settings
from sag_api.core.errors import ApiError, NotFoundError, ValidationError
from sag_api.core.logging import get_logger
from sag_api.db.base import new_id
from sag_api.db.models import AgentBinding, Document, Job, Source, VectorWriteItem
from sag_api.enums import (
    CONNECTOR_SOURCE_TYPE,
    BindingTargetType,
    DocumentStatus,
    JobStatus,
    JobType,
    SourceType,
)
from sag_api.jobs import JobQueue
from sag_api.sag import EngineManager
from sag_api.schemas.source import IngestStatsOut, SourceCodeConfig, SourceCreate, SourceUpdate

log = get_logger("services.source")


async def list_sources(session: AsyncSession) -> list[Source]:
    rows = await session.execute(select(Source).order_by(Source.created_at.desc()))
    return list(rows.scalars().all())


async def source_document_status_counts(
    session: AsyncSession, source_ids: list[str]
) -> dict[str, dict[str, int]]:
    """Return exact per-source document counts for the knowledge source list.

    `pending` intentionally means "not fully indexed yet and not failed", so it
    includes queued, loading, extracting, and paused documents.
    """
    unique_ids = list(dict.fromkeys(source_ids))
    if not unique_ids:
        return {}

    counts = {
        source_id: {"total": 0, "ready": 0, "pending": 0, "paused": 0, "failed": 0}
        for source_id in unique_ids
    }
    rows = await session.execute(
        select(Document.source_id, Document.status, func.count(Document.id))
        .where(Document.source_id.in_(unique_ids))
        .group_by(Document.source_id, Document.status)
    )
    for source_id, status, count in rows.all():
        bucket = counts.setdefault(
            source_id, {"total": 0, "ready": 0, "pending": 0, "paused": 0, "failed": 0}
        )
        amount = int(count or 0)
        status_value = getattr(status, "value", str(status)).lower()
        bucket["total"] += amount
        if status_value == DocumentStatus.READY.value:
            bucket["ready"] += amount
        elif status_value == DocumentStatus.FAILED.value:
            bucket["failed"] += amount
        elif status_value == DocumentStatus.PAUSED.value:
            bucket["paused"] += amount
            bucket["pending"] += amount
        else:
            bucket["pending"] += amount
    return counts


def _engine_db_path() -> Path | None:
    """引擎库路径（data_dir/sag.db）；不存在返回 None。"""
    path = Path(settings.data_dir) / "sag.db"
    return path if path.exists() else None


def _recent_engine_counts(path: Path, window_start: datetime) -> tuple[float, float]:
    """引擎库近窗口分块/事件写入速率（只读，失败降级 0）。"""
    import sqlite3

    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=15)
        con.row_factory = sqlite3.Row
        try:
            counts = {"chunks": 0, "events": 0}
            for key, table, col in (
                ("chunks", "article_section", "created_time"),
                ("events", "source_event", "created_time"),
            ):
                try:
                    has_col = con.execute(
                        f"PRAGMA table_info({table})"
                    ).fetchall()
                    cols = {r["name"] for r in has_col}
                    if col not in cols:
                        continue
                    value = int(
                        con.execute(
                            f"SELECT count(*) FROM {table} WHERE {col} >= ?",
                            (_to_utc_text(window_start),),
                        ).fetchone()[0]
                    )
                    counts[key] = value
                except sqlite3.Error:
                    continue
            return float(counts["chunks"]), float(counts["events"])
        finally:
            con.close()
    except Exception:  # noqa: BLE001 - 引擎库只读统计失败不影响主流程
        return 0.0, 0.0


def _to_utc_text(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _derive_stalled_reason(
    *,
    docs_per_minute: float,
    total_files: int,
    pending_files: int,
    paused_files: int,
    queued_jobs: int,
    running_jobs: int,
) -> str | None:
    """速度为 0 时的原因：paused / queued_waiting / running_in_progress / no_worker / idle。"""
    executable_pending = pending_files - paused_files
    if docs_per_minute == 0 and executable_pending > 0:
        if queued_jobs > 0:
            return "queued_waiting"
        if running_jobs > 0:
            return "running_in_progress"
        if paused_files > 0:
            return "paused"
        return "no_worker"
    if docs_per_minute == 0 and total_files > 0 and executable_pending <= 0:
        return "idle"
    return None


async def ingest_stats(session: AsyncSession) -> IngestStatsOut:
    rows = await session.execute(
        select(Document.status, func.count(Document.id)).group_by(Document.status)
    )
    counts = {
        "total_files": 0,
        "indexed_files": 0,
        "pending_files": 0,
        "failed_files": 0,
        "paused_files": 0,
        "loading_files": 0,
        "extracting_files": 0,
    }
    for status, count in rows.all():
        status_value = getattr(status, "value", str(status)).lower()
        amount = int(count or 0)
        counts["total_files"] += amount
        if status_value == DocumentStatus.READY.value:
            counts["indexed_files"] += amount
        elif status_value == DocumentStatus.FAILED.value:
            counts["failed_files"] += amount
        elif status_value == DocumentStatus.PAUSED.value:
            counts["paused_files"] += amount
            counts["pending_files"] += amount
        elif status_value == DocumentStatus.LOADING.value:
            counts["loading_files"] += amount
            counts["pending_files"] += amount
        elif status_value == DocumentStatus.EXTRACTING.value:
            counts["extracting_files"] += amount
            counts["pending_files"] += amount
        else:
            counts["pending_files"] += amount

    job_rows = await session.execute(
        select(Job.status, func.count(Job.id))
        .where(Job.type == JobType.PROCESS_DOCUMENT)
        .group_by(Job.status)
    )
    queued_jobs = 0
    running_jobs = 0
    for status, count in job_rows.all():
        amount = int(count or 0)
        if status == JobStatus.QUEUED:
            queued_jobs += amount
        elif status == JobStatus.RUNNING:
            running_jobs += amount

    sample_window_minutes = 10
    window_start = datetime.now(UTC) - timedelta(minutes=sample_window_minutes)
    completed_recent = int(
        (
            await session.execute(
                select(func.count(Job.id)).where(
                    Job.type == JobType.PROCESS_DOCUMENT,
                    Job.status == JobStatus.SUCCEEDED,
                    Job.finished_at.is_not(None),
                    Job.finished_at >= window_start,
                )
            )
        ).scalar_one()
        or 0
    )
    docs_per_minute = completed_recent / sample_window_minutes
    docs_per_hour = docs_per_minute * 60.0
    remaining_for_eta = counts["loading_files"] + counts["extracting_files"]
    pending_queue = counts["pending_files"] - counts["paused_files"] - remaining_for_eta
    remaining_for_eta += max(0, pending_queue)
    eta_seconds = (
        int(round((remaining_for_eta / docs_per_minute) * 60))
        if docs_per_minute > 0 and remaining_for_eta > 0
        else None
    )

    # SAG-OPT-503：向量记录速率（vector_write_items 近窗口 succeeded 明细，元数据库）
    vector_items_recent = 0
    try:
        vector_items_recent = int(
            (
                await session.execute(
                    select(func.count(VectorWriteItem.id)).where(
                        VectorWriteItem.status == "succeeded",
                        VectorWriteItem.updated_at >= window_start,
                    )
                )
            ).scalar_one()
            or 0
        )
    except Exception:  # noqa: BLE001 - items 表可能未启用/不存在
        vector_items_recent = 0
    vector_items_per_minute = vector_items_recent / sample_window_minutes

    # SAG-OPT-503：分块/事件速率（引擎库只读）
    chunks_recent, events_recent = _recent_engine_counts(
        _engine_db_path(), window_start
    ) if _engine_db_path() else (0, 0)
    chunks_per_minute = chunks_recent / sample_window_minutes
    events_per_minute = events_recent / sample_window_minutes

    # SAG-OPT-503：速度为 0 时的原因说明（暂停/等待/运行中/无 worker/空闲）
    stalled_reason = _derive_stalled_reason(
        docs_per_minute=docs_per_minute,
        total_files=counts["total_files"],
        pending_files=counts["pending_files"],
        paused_files=counts["paused_files"],
        queued_jobs=queued_jobs,
        running_jobs=running_jobs,
    )

    return IngestStatsOut(
        **counts,
        active_files=counts["loading_files"] + counts["extracting_files"],
        queued_jobs=queued_jobs,
        running_jobs=running_jobs,
        docs_per_minute=docs_per_minute,
        docs_per_hour=docs_per_hour,
        chunks_per_minute=chunks_per_minute,
        events_per_minute=events_per_minute,
        vector_items_per_minute=vector_items_per_minute,
        eta_seconds=eta_seconds,
        stalled_reason=stalled_reason,
        sample_window_minutes=sample_window_minutes,
    )


async def search_source_candidates(
    session: AsyncSession,
    source_ids: list[str] | None = None,
) -> list[Source]:
    """Select a bounded retrieval scope without materializing the source table.

    Explicit `source_ids` preserve the user's @ order. An implicit global search
    uses data density and recency as the cheap partition router until a dedicated
    source-level semantic index is available.
    """
    limit = settings.search_source_candidate_limit
    if source_ids:
        ordered_ids = list(dict.fromkeys(source_ids))
        if len(ordered_ids) > limit:
            raise ValidationError(
                f"单次最多检索 {limit} 个信息源，请通过 @ 缩小范围",
                code="too_many_search_sources",
            )
        rows = await session.execute(select(Source).where(Source.id.in_(ordered_ids)))
        by_id = {source.id: source for source in rows.scalars().all()}
        return [by_id[source_id] for source_id in ordered_ids if source_id in by_id]

    rows = await session.execute(
        select(Source)
        .order_by(
            Source.chunk_count.desc(),
            Source.event_count.desc(),
            Source.updated_at.desc(),
            Source.id,
        )
        .limit(limit)
    )
    return list(rows.scalars().all())


async def get_source(session: AsyncSession, source_id: str) -> Source:
    source = await session.get(Source, source_id)
    if source is None:
        raise NotFoundError("信源不存在")
    return source


def _read_source_code_config(source: Source) -> SourceCodeConfig:
    raw = source.config if isinstance(source.config, dict) else {}
    code_ingest = raw.get("code_ingest")
    if not isinstance(code_ingest, dict):
        return SourceCodeConfig()
    return SourceCodeConfig.model_validate(code_ingest)


async def get_source_code_config(
    session: AsyncSession, source_id: str
) -> SourceCodeConfig:
    return _read_source_code_config(await get_source(session, source_id))


async def update_source_code_config(
    session: AsyncSession,
    source_id: str,
    data: SourceCodeConfig,
) -> SourceCodeConfig:
    source = await get_source(session, source_id)
    config = dict(source.config) if isinstance(source.config, dict) else {}
    config["code_ingest"] = data.model_dump()
    source.config = config
    await session.commit()
    await session.refresh(source)
    return _read_source_code_config(source)


async def create_source(
    session: AsyncSession, data: SourceCreate, *, engine_manager: EngineManager
) -> Source:
    connector = registry.get(data.connector_kind)
    connector.validate_config(data.config)
    source_type = CONNECTOR_SOURCE_TYPE.get(data.connector_kind, SourceType.DOCUMENT)

    source = Source(
        name=data.name,
        description=data.description,
        source_type=source_type,
        connector_kind=data.connector_kind,
        sag_source_config_id=f"src_{new_id()[:16]}",
        config=data.config or {},
    )
    session.add(source)
    await session.commit()
    await session.refresh(source)

    # 预建引擎 schema（幂等）；失败不阻断创建，处理文档时会重试
    try:
        await engine_manager.provision(source.sag_source_config_id, source)
    except ApiError as e:
        log.warning("信源引擎预建失败 %s：%s", source.sag_source_config_id, e.message)
    return source


async def update_source(
    session: AsyncSession,
    source_id: str,
    data: SourceUpdate,
    *,
    job_queue: JobQueue | None = None,
) -> Source:
    source = await get_source(session, source_id)
    if data.name is not None:
        source.name = data.name
    if data.description is not None:
        source.description = data.description
    if data.status is not None:
        source.status = data.status
    await session.commit()
    await session.refresh(source)
    from sag_api.services.universe_service import schedule_universe_refresh

    await schedule_universe_refresh(
        session,
        job_queue,
        source_id=source.id,
        reason="source_updated",
    )
    return source


async def delete_source(
    session: AsyncSession,
    source_id: str,
    *,
    engine_manager: EngineManager,
    upload_dir: str,
    job_queue: JobQueue | None = None,
) -> None:
    """删除信源并收尾：移除悬挂绑定、关闭引擎槽、清理上传文件。"""
    source = await get_source(session, source_id)
    sag_id = source.sag_source_config_id

    # 悬挂绑定清理（target_id 为普通字符串，无 FK 级联）
    await session.execute(
        AgentBinding.__table__.delete().where(
            AgentBinding.target_type == BindingTargetType.SOURCE,
            AgentBinding.target_id == source.id,
        )
    )
    await session.delete(source)
    await session.commit()

    # 引擎槽关闭 + 上传目录清理（尽力而为，不阻断删除）
    await engine_manager.release(sag_id)
    shutil.rmtree(os.path.join(upload_dir, source_id), ignore_errors=True)
    from sag_api.services.universe_service import schedule_universe_refresh

    await schedule_universe_refresh(
        session,
        job_queue,
        source_id=None,
        reason="source_deleted",
    )


async def sync_source(session: AsyncSession, source_id: str, *, job_queue: JobQueue) -> Job:
    """触发一次动态连接器同步（如网页抓取）。"""
    source = await get_source(session, source_id)
    connector = registry.get(source.connector_kind)
    if not connector.meta.supports_sync:
        raise ValidationError("该连接器不支持同步")
    job = Job(type=JobType.SYNC_SOURCE, source_id=source.id, status=JobStatus.QUEUED)
    session.add(job)
    await session.commit()
    await session.refresh(job)
    await job_queue.enqueue(job.id)
    return job
