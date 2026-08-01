"""文档领域逻辑：上传落盘 → 登记 → 入队处理。"""

from __future__ import annotations

import os

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.disk_guard import DiskGuard
from sag_api.core.errors import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationError,
)
from sag_api.db.base import new_id
from sag_api.db.models import Document, Job, Source
from sag_api.enums import DocumentStatus, JobStatus, JobType
from sag_api.jobs import JobQueue
from sag_api.sag import EngineManager

# SAG-OPT-802：磁盘分级保护（<5GB 暂停新文档解析）
_ingest_disk_guard = DiskGuard(settings.data_dir, settings)


def _ensure_ingest_disk() -> None:
    if not _ingest_disk_guard.allow_ingest():
        level = _ingest_disk_guard.current()
        raise ServiceUnavailableError(
            f"磁盘剩余空间不足（{level.free_gb:.1f}GB，阈值 {level.threshold_gb:.1f}GB），"
            "已暂停新文档解析以保护数据库"
        )


async def recent_document_activity(
    session: AsyncSession,
    source_id: str,
    limit: int = 30,
) -> list[dict]:
    """SAG-OPT-502：该信源最近更新的文档快照（状态 / 进度 / 错误摘要）。

    旧状态不做持久化，由前端轮询快照对比推导，避免每次状态流转都写事件表。
    """
    rows = await session.scalars(
        select(Document)
        .where(Document.source_id == source_id)
        .order_by(Document.updated_at.desc())
        .limit(limit)
    )
    return [
        {
            "document_id": doc.id,
            "filename": doc.filename,
            "status": doc.status.value,
            "progress": doc.progress,
            "error": doc.error,
            "updated_at": doc.updated_at,
        }
        for doc in rows
    ]


async def list_documents(
    session: AsyncSession,
    source_id: str,
    *,
    limit: int | None = None,
    offset: int = 0,
    status: DocumentStatus | None = None,
    keyword: str | None = None,
) -> list[Document]:
    """列出文档：按 source 过滤，可选 status 过滤与 filename 服务端搜索。"""
    statement = select(Document).where(Document.source_id == source_id).order_by(Document.created_at.desc())
    if status is not None:
        statement = statement.where(Document.status == status)
    if keyword and keyword.strip():
        statement = statement.where(Document.filename.ilike(f"%{keyword.strip()}%"))
    if offset:
        statement = statement.offset(offset)
    if limit is not None:
        statement = statement.limit(limit)
    rows = await session.execute(statement)
    return list(rows.scalars().all())


async def get_document(session: AsyncSession, source: Source, document_id: str) -> Document:
    doc = await session.get(Document, document_id)
    if doc is None or doc.source_id != source.id:
        raise NotFoundError("文档不存在")
    return doc


async def rename_document(
    session: AsyncSession,
    source: Source,
    document_id: str,
    *,
    filename: str,
) -> Document:
    """Rename a document's display filename. Does not reparse or touch storage."""
    document = await get_document(session, source, document_id)
    safe_name = os.path.basename((filename or "").strip()) or ""
    if not safe_name:
        raise ValidationError("文件名不能为空")
    if len(safe_name) > 512:
        raise ValidationError("文件名过长")
    if safe_name != document.filename:
        document.filename = safe_name
        await session.commit()
        await session.refresh(document)
    return document


async def create_document_from_upload(
    session: AsyncSession,
    source: Source,
    *,
    filename: str,
    content_type: str,
    data: bytes,
    upload_dir: str,
    job_queue: JobQueue,
) -> tuple[Document, Job]:
    _ensure_ingest_disk()
    doc_id = new_id()
    safe_name = os.path.basename(filename) or "upload"
    dest_dir = os.path.join(upload_dir, source.id)
    os.makedirs(dest_dir, exist_ok=True)
    storage_path = os.path.join(dest_dir, f"{doc_id}_{safe_name}")
    with open(storage_path, "wb") as f:
        f.write(data)

    document = Document(
        id=doc_id,
        source_id=source.id,
        filename=safe_name,
        content_type=content_type or "application/octet-stream",
        size_bytes=len(data),
        storage_path=storage_path,
        status=DocumentStatus.PENDING,
    )
    session.add(document)
    await session.execute(
        update(Source).where(Source.id == source.id).values(document_count=Source.document_count + 1)
    )
    job = Job(
        type=JobType.PROCESS_DOCUMENT,
        source_id=source.id,
        document_id=doc_id,
        status=JobStatus.QUEUED,
    )
    session.add(job)
    await session.commit()
    await session.refresh(document)
    await session.refresh(job)

    await job_queue.enqueue(job.id)
    return document, job


def _format_messages(messages: list[dict]) -> str:
    lines = ["# 消息", ""]
    for m in messages:
        who = m.get("author") or m.get("role") or "消息"
        ts = f"（{m['ts']}）" if m.get("ts") else ""
        lines.append(f"**{who}**{ts}：{m.get('text') or ''}")
    return "\n\n".join(lines)


async def ingest_content(
    session: AsyncSession,
    source: Source,
    *,
    text: str | None = None,
    title: str | None = None,
    messages: list[dict] | None = None,
    upload_dir: str,
    job_queue: JobQueue,
) -> Document:
    """统一写入：把文本 / 一批消息归一为文档 → 复用 ingest/extract 管线（持续写入）。"""
    _ensure_ingest_disk()
    from sag_api.core.errors import ValidationError

    if messages:
        content = _format_messages(messages)
        filename = f"{title or f'消息-{len(messages)}条'}.md"
    elif text:
        content = (f"# {title}\n\n" if title else "") + text
        filename = f"{title or '文本'}.md"
    else:
        raise ValidationError("请提供 text 或 messages")

    document, _job = await create_document_from_upload(
        session,
        source,
        filename=filename,
        content_type="text/markdown",
        data=content.encode("utf-8"),
        upload_dir=upload_dir,
        job_queue=job_queue,
    )
    return document


async def reprocess_document(
    session: AsyncSession,
    source: Source,
    document_id: str,
    *,
    job_queue: JobQueue,
    engine_manager: EngineManager,
    allow_ready: bool = False,
) -> Job:
    _ensure_ingest_disk()
    document = await get_document(session, source, document_id)
    latest = await session.scalar(
        select(Job).where(Job.document_id == document.id).order_by(Job.created_at.desc())
    )
    if latest is not None and latest.status in {
        JobStatus.QUEUED,
        JobStatus.RUNNING,
        JobStatus.PAUSED,
    }:
        if document.status == DocumentStatus.FAILED:
            if latest.status == JobStatus.QUEUED:
                document.status = DocumentStatus.PENDING
            elif latest.status == JobStatus.RUNNING:
                document.status = DocumentStatus.EXTRACTING
            else:
                document.status = DocumentStatus.PAUSED
            document.error = None
            await session.commit()
            await session.refresh(latest)
        if latest.status == JobStatus.QUEUED:
            await job_queue.enqueue(latest.id)
        return latest
    restart_from_scratch = document.status == DocumentStatus.READY
    if restart_from_scratch and not allow_ready:
        raise ConflictError("已入库文档不会通过重试重新处理，请先确认需要强制重建")
    if restart_from_scratch:
        derived_source_ids = {
            value
            for value in [
                document.sag_source_id,
                *[
                    _checkpoint_source_id(candidate.payload)
                    for candidate in (
                        await session.scalars(
                            select(Job).where(Job.document_id == document.id)
                        )
                    ).all()
                ],
            ]
            if value
        }
        # 历史版本的“重新处理”每次都会新建 Article；从所有历史 Job 断点收集
        # source_id，既清理当前记录，也清理此前已经遗留的重复派生数据。
        for derived_source_id in sorted(derived_source_ids):
            await engine_manager.delete_document_data(
                source.sag_source_config_id,
                derived_source_id,
                source=source,
            )

    document.status = DocumentStatus.PENDING
    document.error = None
    if restart_from_scratch:
        document.progress = 0
        document.chunk_count = 0
        document.event_count = 0
        document.token_usage = 0
        document.sag_source_id = None
        await session.flush()
        await _refresh_source_counts(session, source)
    payload = dict(latest.payload or {}) if latest is not None and not restart_from_scratch else {}
    payload.pop("pause_requested", None)
    payload.pop("resume_requested", None)
    job = Job(
        type=JobType.PROCESS_DOCUMENT,
        source_id=source.id,
        document_id=document.id,
        status=JobStatus.QUEUED,
        # 上次失败若已创建 MinerU 任务，重新处理应继续轮询而不是再次计费。
        payload=payload,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    await job_queue.enqueue(job.id)
    return job


def _checkpoint_source_id(payload: dict | None) -> str | None:
    checkpoint = (payload or {}).get("process_checkpoint")
    value = checkpoint.get("source_id") if isinstance(checkpoint, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


async def _refresh_source_counts(session: AsyncSession, source: Source) -> None:
    document_count, chunk_count, event_count = (
        await session.execute(
            select(
                func.count(Document.id),
                func.coalesce(func.sum(Document.chunk_count), 0),
                func.coalesce(func.sum(Document.event_count), 0),
            ).where(Document.source_id == source.id)
        )
    ).one()
    source.document_count = int(document_count)
    source.chunk_count = int(chunk_count)
    source.event_count = int(event_count)


async def pause_document(session: AsyncSession, source: Source, document_id: str) -> Job:
    """协作式暂停：已开始的分块跑完并保存断点，不再领取新分块。"""
    document = await get_document(session, source, document_id)
    if document.status not in {
        DocumentStatus.PENDING,
        DocumentStatus.LOADING,
        DocumentStatus.EXTRACTING,
    }:
        raise ConflictError("只能停止待抽取或正在抽取的文档")
    job = await session.scalar(
        select(Job)
        .where(
            Job.document_id == document.id,
            Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    if job is None:
        raise ConflictError("当前文档没有可停止的抽取任务")

    if job.status == JobStatus.QUEUED:
        paused = await session.execute(
            update(Job)
            .where(Job.id == job.id, Job.status == JobStatus.QUEUED)
            .values(status=JobStatus.PAUSED)
        )
        if paused.rowcount == 1:
            document.status = DocumentStatus.PAUSED
            await session.commit()
            await session.refresh(job)
            return job
        await session.refresh(job)

    if job.status != JobStatus.RUNNING:
        raise ConflictError("抽取任务已经结束，无法停止")
    job.payload = {**(job.payload or {}), "pause_requested": True}
    await session.commit()
    await session.refresh(job)
    return job


async def resume_document(
    session: AsyncSession,
    source: Source,
    document_id: str,
    *,
    job_queue: JobQueue,
) -> Job:
    """把暂停任务原样重新入队，处理器会跳过断点中已完成的分块。"""
    document = await get_document(session, source, document_id)
    if document.status != DocumentStatus.PAUSED:
        raise ConflictError("只能继续已暂停的文档")
    job = await session.scalar(
        select(Job)
        .where(Job.document_id == document.id, Job.status == JobStatus.PAUSED)
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    if job is None:
        raise ConflictError("当前文档没有可继续的暂停任务")

    payload = dict(job.payload or {})
    payload.pop("pause_requested", None)
    payload["resume_requested"] = True
    job.payload = payload
    job.status = JobStatus.QUEUED
    job.finished_at = None
    job.error = None
    document.status = (
        DocumentStatus.EXTRACTING if payload.get("process_checkpoint") else DocumentStatus.PENDING
    )
    document.error = None
    await session.commit()
    await session.refresh(job)
    await job_queue.enqueue(job.id)
    return job


async def delete_document(
    session: AsyncSession,
    source: Source,
    document_id: str,
    *,
    engine_manager: EngineManager,
    job_queue: JobQueue | None = None,
) -> None:
    document = await get_document(session, source, document_id)
    path = document.storage_path
    sag_source_id = document.sag_source_id

    active_jobs = list(
        (
            await session.scalars(
                select(Job).where(
                    Job.document_id == document.id,
                    Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                )
            )
        ).all()
    )
    for job in active_jobs:
        job.payload = {**(job.payload or {}), "pause_requested": True}
        if job.status == JobStatus.QUEUED:
            job.status = JobStatus.PAUSED
    if active_jobs:
        await session.commit()

    if sag_source_id:
        await engine_manager.delete_document_data(
            source.sag_source_config_id,
            sag_source_id,
            source=source,
        )

    await session.delete(document)
    await session.flush()
    await _refresh_source_counts(session, source)
    await session.commit()
    if path:
        from sag_api.parsing.service import parsed_sidecar_paths

        for candidate in [path, *parsed_sidecar_paths(path)]:
            try:
                if os.path.exists(candidate):
                    os.remove(candidate)
            except OSError:
                pass
    from sag_api.services.universe_service import schedule_universe_refresh

    await schedule_universe_refresh(
        session,
        job_queue,
        source_id=source.id,
        reason="document_deleted",
    )
