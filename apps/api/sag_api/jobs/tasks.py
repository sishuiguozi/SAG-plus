"""任务处理器 —— 按 JobType 分发。

处理器只关心「做什么」；状态机（queued/running/succeeded/failed）由队列 worker 统一维护。
处理器内部负责领域对象（Document/Source）的阶段状态与计数更新。
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.db import SessionLocal
from sag_api.core.errors import NotFoundError
from sag_api.core.logging import get_logger
from sag_api.db.models import Document, Job, Source
from sag_api.enums import DocumentStatus, JobType
from sag_api.jobs.control import JobPaused
from sag_api.parsing import prepare_document
from sag_api.sag import EngineManager
from sag_api.sag.dto import ProcessCheckpoint

log = get_logger("jobs")

TaskHandler = Callable[[AsyncSession, Job], Awaitable[None]]


async def process_document(
    session: AsyncSession, job: Job, *, engine_manager: EngineManager, job_queue=None
) -> None:
    """解析、入库并按 chunk 并发抽取；每个 chunk 完成即保存断点。"""
    from sag_api.services.document_service import (
        publish_code_replacement,
        rollback_code_replacement,
    )
    from sag_api.services.source_service import _read_source_code_config

    document = await session.get(Document, job.document_id) if job.document_id else None
    if document is None:
        raise NotFoundError("文档不存在")
    source = await session.get(Source, document.source_id)
    if source is None:
        raise NotFoundError("信源不存在")
    checkpoint = ProcessCheckpoint.from_payload(job.payload)
    replacement = dict((job.payload or {}).get("code_replacement") or {})
    process_storage_path = (
        str(replacement.get("pending_path") or "") if replacement else document.storage_path
    )
    # While a code replacement is in flight, keep Document counters on the old
    # authoritative revision. Checkpoint progress stays on the Job payload.
    preserve_old_revision = bool(replacement)

    async def refresh_payload() -> dict:
        await session.refresh(job, attribute_names=["payload"])
        return dict(job.payload or {})

    async def on_stage(stage: str) -> None:
        if not preserve_old_revision:
            if stage == "loading":
                document.status = DocumentStatus.LOADING
                document.progress = max(document.progress, 5)
                job.progress = document.progress / 100
            elif stage == "extracting":
                document.status = DocumentStatus.EXTRACTING
                completed = len(checkpoint.processed_chunk_ids)
                total = len(checkpoint.chunk_ids)
                document.progress = 20 + round(80 * completed / total) if total else 20
                job.progress = document.progress / 100
        else:
            # Keep Document READY/old until publish; only advance job progress.
            if stage == "loading":
                job.progress = max(job.progress or 0.0, 0.05)
            elif stage == "extracting":
                completed = len(checkpoint.processed_chunk_ids)
                total = len(checkpoint.chunk_ids)
                job.progress = 0.2 + (0.8 * completed / total if total else 0.0)
        await session.commit()

    async def on_parser_state(state: dict) -> None:
        if not preserve_old_revision:
            document.status = DocumentStatus.LOADING
            document.progress = max(document.progress, 10)
            job.progress = document.progress / 100
        else:
            job.progress = max(job.progress or 0.0, 0.1)
        job.payload = {**(await refresh_payload()), "document_parser": state}
        await session.commit()

    async def on_checkpoint(value: ProcessCheckpoint) -> None:
        nonlocal checkpoint
        checkpoint = value
        merged = value.merge_payload(await refresh_payload())
        if replacement:
            merged["code_replacement"] = replacement
        job.payload = merged
        if not preserve_old_revision:
            document.chunk_count = len(value.chunk_ids)
            document.event_count = value.event_count
            document.sag_source_id = value.source_id
            document.token_usage = value.token_usage
            total = len(value.chunk_ids)
            completed = len(value.processed_chunk_ids)
            document.progress = 20 + round(80 * completed / total) if total else 20
            job.progress = document.progress / 100
        else:
            total = len(value.chunk_ids)
            completed = len(value.processed_chunk_ids)
            job.progress = 0.2 + (0.8 * completed / total if total else 0.0)
        await session.commit()

    async def should_pause() -> bool:
        async with SessionLocal() as control_session:
            current_job = await control_session.get(Job, job.id)
            if current_job is None:
                return True
            return bool((current_job.payload or {}).get("pause_requested"))

    try:
        prepared = None
        if not checkpoint.chunk_ids:
            prepared = await prepare_document(
                process_storage_path,
                settings,
                state=(job.payload or {}).get("document_parser"),
                on_state=on_parser_state,
                relative_path=(
                    replacement.get("relative_path")
                    or document.relative_path
                    or Path(document.filename).name
                ),
            )
            if prepared.fallback_from:
                log.warning(
                    "文档解析已降级 doc=%s job=%s from=%s to=%s cached=%s error=%s",
                    document.id,
                    getattr(job, "id", None),
                    prepared.fallback_from,
                    prepared.provider,
                    prepared.cached,
                    prepared.fallback_error,
                )
            if prepared.provider == "tree_sitter" and not preserve_old_revision:
                document.relative_path = prepared.relative_path
                document.content_sha256 = prepared.content_sha256
                document.code_language = prepared.code_language
                await session.commit()
            elif prepared.provider == "tree_sitter" and preserve_old_revision:
                # Stash intended metadata on the job only until publish.
                payload = await refresh_payload()
                payload["code_ingest"] = {
                    "relative_path": prepared.relative_path,
                    "content_sha256": prepared.content_sha256,
                    "code_language": prepared.code_language,
                }
                if replacement:
                    payload["code_replacement"] = replacement
                job.payload = payload
                await session.commit()
        elif (
            (document.code_language or (replacement.get("new_code_language") if replacement else None))
            and (document.content_sha256 or (replacement.get("new_content_sha256") if replacement else None))
            and process_storage_path
        ):
            from sag_api.parsing.service import PreparedDocument

            prepared = PreparedDocument(
                path=process_storage_path,
                provider="tree_sitter",
                relative_path=(
                    (replacement.get("relative_path") if replacement else None)
                    or document.relative_path
                    or Path(document.filename).name
                ),
                content_sha256=(
                    (replacement.get("new_content_sha256") if replacement else None)
                    or document.content_sha256
                ),
                code_language=(
                    (replacement.get("new_code_language") if replacement else None)
                    or document.code_language
                ),
            )
        code_kwargs = (
            {"prepared_document": prepared}
            if prepared is not None and prepared.provider == "tree_sitter"
            else {}
        )
        code_kwargs["code_llm_extraction_mode"] = _read_source_code_config(source).llm_extraction_mode
        process_path = None
        if prepared is not None:
            process_path = str(prepared.path)
        elif not checkpoint.chunk_ids:
            process_path = process_storage_path
        outcome = await engine_manager.process_document(
            source.sag_source_config_id,
            process_path,
            source=source,
            on_stage=on_stage,
            checkpoint=checkpoint,
            on_checkpoint=on_checkpoint,
            should_pause=should_pause,
            max_concurrency=settings.document_extract_concurrency,
            document_title=Path(document.filename).stem.strip(),
            **code_kwargs,
        )
        if outcome.paused:
            if not preserve_old_revision:
                document.status = DocumentStatus.PAUSED
                document.error = None
            await session.commit()
            raise JobPaused()
    except JobPaused:
        raise
    except Exception as e:  # noqa: BLE001 - 记录到文档后再上抛给 worker
        if replacement:
            await rollback_code_replacement(document, replacement)
            # Keep old revision searchable after failed replacement.
            if document.status != DocumentStatus.READY:
                document.status = DocumentStatus.READY
            document.error = getattr(e, "message", None) or str(e)
        else:
            document.status = DocumentStatus.FAILED
            document.error = getattr(e, "message", None) or str(e)
        await session.commit()
        raise

    if replacement:
        if job_queue is None:
            raise RuntimeError("代码版本发布需要任务队列")
        await publish_code_replacement(
            session,
            document,
            source,
            replacement=replacement,
            outcome_source_id=outcome.source_id,
            outcome_chunk_count=outcome.chunk_count,
            outcome_event_count=outcome.event_count,
            outcome_token_usage=outcome.token_usage,
            job_queue=job_queue,
        )
        return

    document.status = DocumentStatus.READY
    document.chunk_count = outcome.chunk_count
    document.event_count = outcome.event_count
    document.sag_source_id = outcome.source_id
    document.progress = 100
    document.token_usage = outcome.token_usage
    document.error = None
    # 信源聚合计数用原子 SQL 更新，避免并发读改写丢失
    await session.execute(
        update(Source)
        .where(Source.id == source.id)
        .values(
            chunk_count=Source.chunk_count + outcome.chunk_count,
            event_count=Source.event_count + outcome.event_count,
        )
    )
    await session.commit()
    log.info(
        "文档处理完成 doc=%s chunks=%d events=%d",
        document.id,
        outcome.chunk_count,
        outcome.event_count,
    )


async def sync_source(session: AsyncSession, job: Job, *, engine_manager=None, job_queue=None) -> None:
    """动态连接器同步：discover → fetch → 登记文档并入队处理（复用 ingest→extract 管线）。"""
    # 延迟导入避免与 jobs 包的循环依赖
    from sag_api.connectors import registry
    from sag_api.core.config import settings
    from sag_api.services.document_service import create_document_from_upload

    source = await session.get(Source, job.source_id) if job.source_id else None
    if source is None:
        raise NotFoundError("信源不存在")

    connector = registry.get(source.connector_kind)
    discovered = await connector.discover(source.config or {})
    fetched = 0
    for d in discovered:
        try:
            local = await connector.fetch(source.config or {}, d)
            with open(local.path, "rb") as f:
                data = f.read()
        except Exception as e:  # noqa: BLE001 - 单篇失败不影响整体同步
            log.warning("同步抓取失败 %s：%s", d.external_id, getattr(e, "message", None) or e)
            continue
        await create_document_from_upload(
            session,
            source,
            filename=local.filename,
            content_type=local.content_type,
            data=data,
            upload_dir=settings.upload_dir,
            job_queue=job_queue,
        )
        try:
            os.remove(local.path)
        except OSError:
            pass
        fetched += 1

    job.progress = 1.0
    job.payload = {**(job.payload or {}), "discovered": len(discovered), "fetched": fetched}
    await session.commit()
    log.info("同步完成 source=%s 发现=%d 抓取=%d", source.id, len(discovered), fetched)


async def index_universe(
    session: AsyncSession, job: Job, *, engine_manager: EngineManager, job_queue=None
) -> None:
    """Rebuild one user's aggregate universe overview from authoritative graph data."""
    from sag_api.db.models import User
    from sag_api.services.universe_service import rebuild_universe_overview

    user_id = str((job.payload or {}).get("user_id") or "")
    if not user_id or await session.get(User, user_id) is None:
        raise NotFoundError("知识宇宙所属用户不存在")
    job.progress = 0.1
    await session.commit()
    overview = await rebuild_universe_overview(session, engine_manager, user_id)
    job.progress = 1.0
    job.payload = {**(job.payload or {}), "overview_id": overview.id}
    await session.commit()


async def cleanup_document_revision(
    session: AsyncSession, job: Job, *, engine_manager: EngineManager, job_queue=None
) -> None:
    """Delete an old code revision's derived sag data after a successful publish."""
    payload = dict(job.payload or {})
    document_id = str(payload.get("document_id") or job.document_id or "")
    old_sag_source_id = str(payload.get("old_sag_source_id") or "")
    new_hash = str(payload.get("new_content_sha256") or "")
    source_id = str(payload.get("source_id") or job.source_id or "")
    if not document_id or not old_sag_source_id or not new_hash:
        raise NotFoundError("版本清理任务缺少必要参数")

    document = await session.get(Document, document_id)
    if document is None:
        # Document gone: nothing authoritative to protect; still try cleanup once.
        source = await session.get(Source, source_id) if source_id else None
        if source is not None:
            await engine_manager.delete_document_data(
                source.sag_source_config_id,
                old_sag_source_id,
                source=source,
            )
        job.progress = 1.0
        await session.commit()
        return

    if document.content_sha256 != new_hash:
        # A newer revision may have been published or rolled back; skip delete.
        log.warning(
            "跳过版本清理 doc=%s expected_hash=%s actual_hash=%s old_sag=%s",
            document_id,
            new_hash,
            document.content_sha256,
            old_sag_source_id,
        )
        job.progress = 1.0
        await session.commit()
        return

    if document.sag_source_id == old_sag_source_id:
        # Safety: never delete the currently published revision.
        log.warning(
            "跳过版本清理：旧 sag_source 仍是当前版本 doc=%s sag=%s",
            document_id,
            old_sag_source_id,
        )
        job.progress = 1.0
        await session.commit()
        return

    source = await session.get(Source, document.source_id)
    if source is None:
        raise NotFoundError("信源不存在")
    await engine_manager.delete_document_data(
        source.sag_source_config_id,
        old_sag_source_id,
        source=source,
    )
    job.progress = 1.0
    await session.commit()
    log.info(
        "旧代码版本已清理 doc=%s old_sag=%s new_hash=%s",
        document_id,
        old_sag_source_id,
        new_hash,
    )


TASK_HANDLERS: dict[JobType, TaskHandler] = {
    JobType.PROCESS_DOCUMENT: process_document,
    JobType.SYNC_SOURCE: sync_source,
    JobType.INDEX_UNIVERSE: index_universe,
    JobType.CLEANUP_DOCUMENT_REVISION: cleanup_document_revision,
}
