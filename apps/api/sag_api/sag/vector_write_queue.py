from __future__ import annotations

import asyncio
import random
import time
import tracemalloc
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from sag_api.core.config import settings
from sag_api.core.disk_guard import DiskGuard
from sag_api.core.logging import get_logger
from sag_api.db.models import VectorWriteJob
from sag_api.db.models.vector_write import VectorWriteItem
from sag_api.sag import EngineManager
from sag_api.sag.vector_write_items import (
    claim_job_items,
    complete_job_items,
    reassign_job_items,
    register_job_items,
)

log = get_logger("sag.vector_writer")

disk_guard = DiskGuard(settings.data_dir, settings)  # SAG-OPT-802

_WRITE_LOCK = asyncio.Lock()
_PATCHED = False
_SOURCE_CHUNK_PATCHED = False
_POLL_SECONDS = 3.0
_BACKOFF_SECONDS = (10, 30, 90, 180, 300, 600, 900, 1800)
_RECONCILE_BATCH_SIZE = 200
_LEASE_SECONDS = 15 * 60
PENDING_JOB_STATUSES = ("queued", "retry")
INFLIGHT_JOB_STATUSES = ("writing", "running")
ACTIVE_JOB_STATUSES = PENDING_JOB_STATUSES + INFLIGHT_JOB_STATUSES


def _now() -> datetime:
    return datetime.now(UTC)


def _error_text(error: BaseException) -> str:
    return (getattr(error, "message", None) or str(error))[:4000]


def _retry_delay(attempts: int) -> float:
    index = max(0, min(attempts - 1, len(_BACKOFF_SECONDS) - 1))
    return float(_BACKOFF_SECONDS[index])


def _retry_delay_with_jitter(attempts: int) -> float:
    base = _retry_delay(attempts)
    return max(1.0, base * random.uniform(0.85, 1.15))


def _lease_expires_at(now: datetime | None = None) -> datetime:
    return (now or _now()) + timedelta(seconds=_LEASE_SECONDS)


def _is_retryable_error(error: BaseException) -> bool:
    message = _error_text(error).lower()
    retryable_markers = (
        "timeout",
        "timed out",
        "rate limit",
        "too many requests",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
        "connection refused",
        "connection error",
        "server disconnected",
        "database is locked",
        "database is busy",
        "busy timeout",
        " 429",
        " 500",
        " 502",
        " 503",
        " 504",
    )
    non_retryable_markers = (
        " 400",
        " 401",
        " 403",
        " 404",
        "validation error",
        "schema validation",
        "invalid api key",
        "unauthorized",
        "forbidden",
        "not found",
    )
    if any(marker in message for marker in non_retryable_markers):
        return False
    return any(marker in message for marker in retryable_markers)


def _plan_pending_event_deduplication(
    missing_by_source: dict[str, list[str]],
    jobs: list[tuple[str, str, str, dict[str, Any]]],
) -> tuple[dict[str, list[str]], dict[str, set[str]]]:
    """Keep one pending reference for each currently missing event.

    Running jobs are immutable because their vector write may already be in
    flight. Callers must order running jobs before queued jobs so queued copies
    yield to the active writer.
    """
    missing = {
        source_config_id: set(event_ids)
        for source_config_id, event_ids in missing_by_source.items()
    }
    scheduled: dict[str, set[str]] = defaultdict(set)
    planned: dict[str, list[str]] = {}
    for job_id, source_config_id, status, payload in jobs:
        event_ids = list(
            dict.fromkeys(
                str(value)
                for value in (payload.get("event_ids") or [])
                if value
            )
        )
        if status in INFLIGHT_JOB_STATUSES:
            planned[job_id] = event_ids
            scheduled[source_config_id].update(event_ids)
            continue
        allowed = missing.get(source_config_id, set())
        kept = [
            event_id
            for event_id in event_ids
            if event_id in allowed
            and event_id not in scheduled[source_config_id]
        ]
        planned[job_id] = kept
        scheduled[source_config_id].update(kept)
    return planned, scheduled


def _filter_unscheduled_event_ids(
    event_ids: list[str],
    pending_payloads: list[dict[str, Any]],
) -> list[str]:
    scheduled: set[str] = set()
    for payload in pending_payloads:
        scheduled.update(str(value) for value in (payload.get("event_ids") or []) if value)
    return [
        event_id
        for event_id in dict.fromkeys(event_ids)
        if event_id and event_id not in scheduled
    ]


def _split_event_ids(event_ids: list[str], batch_size: int = 200) -> list[list[str]]:
    return [
        event_ids[offset : offset + batch_size]
        for offset in range(0, len(event_ids), batch_size)
    ]


def _split_retry_batches(event_ids: list[str]) -> list[list[str]]:
    if len(event_ids) <= 1:
        return [list(event_ids)]
    midpoint = max(1, len(event_ids) // 2)
    return [
        batch
        for batch in (event_ids[:midpoint], event_ids[midpoint:])
        if batch
    ]


def _extract_entity_ids(events: list[Any]) -> list[str]:
    """Collect unique entity ids referenced by the events' associations."""
    ids: list[str] = []
    seen: set[str] = set()
    for event in events:
        for assoc in getattr(event, "event_associations", None) or []:
            entity_id = str(getattr(assoc, "entity_id", "") or "")
            if entity_id and entity_id not in seen:
                seen.add(entity_id)
                ids.append(entity_id)
    return ids


def _extract_assoc_ids(events: list[Any]) -> list[str]:
    """Collect unique event-entity association ids from the events."""
    ids: list[str] = []
    seen: set[str] = set()
    for event in events:
        for assoc in getattr(event, "event_associations", None) or []:
            assoc_id = str(getattr(assoc, "id", "") or "")
            if assoc_id and assoc_id not in seen:
                seen.add(assoc_id)
                ids.append(assoc_id)
    return ids


def _payload_signature(payload: dict[str, Any]) -> tuple[Any, ...]:
    return (
        int(payload.get("embedding_batch_size") or 10),
        int(payload.get("index_batch_size") or 50),
        int(payload.get("embedding_max_length") or 500),
        bool(payload.get("enable_entity_vector_sync", True)),
        bool(payload.get("enable_event_entity_vector_sync", True)),
        str(payload.get("reason") or ""),
    )


def _aux_vector_sync_enabled(value: Any) -> bool:
    if settings.aux_vector_deferred_enabled:
        return False
    return bool(value)


def _merge_tail_payload(
    existing_payload: dict[str, Any],
    incoming_payload: dict[str, Any],
    incoming_record_ids: list[str],
    *,
    batch_size: int,
    record_id_key: str = "event_ids",
) -> tuple[dict[str, Any], list[str]]:
    existing_ids = [str(value) for value in (existing_payload.get(record_id_key) or []) if value]
    unique_existing_ids = list(dict.fromkeys(existing_ids))
    room = max(0, batch_size - len(unique_existing_ids))
    accepted = incoming_record_ids[:room]
    leftover = incoming_record_ids[room:]
    merged_payload = dict(existing_payload)
    merged_payload[record_id_key] = unique_existing_ids + accepted
    if record_id_key != "event_ids":
        merged_payload["event_ids"] = list(
            dict.fromkeys(
                [str(value) for value in (existing_payload.get("event_ids") or []) if value]
                + [str(value) for value in (incoming_payload.get("event_ids") or []) if value]
            )
        )
    merged_payload["chunk_ids"] = list(
        dict.fromkeys(
            [str(value) for value in (existing_payload.get("chunk_ids") or []) if value]
            + [str(value) for value in (incoming_payload.get("chunk_ids") or []) if value]
        )
    )
    merged_payload["tail_merged_at"] = _now().isoformat()
    merged_payload["tail_merged_count"] = int(existing_payload.get("tail_merged_count") or 0) + len(accepted)
    if len(merged_payload[record_id_key]) >= batch_size:
        merged_payload["tail_flush_pending"] = False
        merged_payload["tail_flush_completed_at"] = _now().isoformat()
    else:
        merged_payload["tail_flush_pending"] = True
    return merged_payload, leftover


async def enqueue_event_vector_sync(
    session_factory: async_sessionmaker,
    events: list[Any],
    config: Any,
) -> None:
    if not disk_guard.allow_vector():
        log.warning("磁盘保护：跳过事件向量入队 source=%s level=%s free=%.1fGB",
                    getattr(config, "source_config_id", "?"), disk_guard.current().level,
                    disk_guard.current().free_gb)
        return
    event_ids = [str(getattr(event, "id", "") or "") for event in events]
    event_ids = [event_id for event_id in event_ids if event_id]
    if not event_ids:
        return
    source_config_id = str(getattr(config, "source_config_id", "") or "")
    if not source_config_id:
        return
    payload = {
        "event_ids": event_ids,
        "chunk_ids": list(getattr(config, "chunk_ids", []) or []),
        "embedding_batch_size": int(getattr(config, "embedding_batch_size", 10) or 10),
        "index_batch_size": int(getattr(config, "index_batch_size", 50) or 50),
        "embedding_max_length": int(getattr(config, "embedding_max_length", 500) or 500),
        # SAG-OPT-106：entity_vectors / event_entity_vectors 改由独立辅助队列
        # 写入（enqueue_entity_vector_sync / enqueue_event_entity_vector_sync），
        # 事件任务本身不再内联写辅助表；旧队列中已含 True 的任务仍按旧逻辑执行。
        "enable_entity_vector_sync": False,
        "enable_event_entity_vector_sync": False,
    }
    async with session_factory() as session:
        pending_jobs = (
            (
                await session.execute(
                    select(VectorWriteJob)
                    .where(
                        VectorWriteJob.kind == "event_sync",
                        VectorWriteJob.source_config_id == source_config_id,
                        VectorWriteJob.status.in_(ACTIVE_JOB_STATUSES),
                    )
                    .order_by(VectorWriteJob.created_at.asc(), VectorWriteJob.id.asc())
                )
            )
            .scalars()
            .all()
        )
        event_ids = _filter_unscheduled_event_ids(
            event_ids,
            [dict(job.payload or {}) for job in pending_jobs],
        )
        if not event_ids:
            log.debug(
                "事件向量写入跳过入队 source_config_id=%s reason=already_pending",
                source_config_id,
            )
            return
        batches = _split_event_ids(event_ids, batch_size=settings.vector_write_job_batch_size)
        parent_batch_id = uuid.uuid4().hex
        flush_window = max(0.0, float(settings.vector_write_tail_flush_seconds or 0.0))
        queued_tail_jobs = [
            job
            for job in pending_jobs
            if job.status in PENDING_JOB_STATUSES
            and job.record_count < settings.vector_write_job_batch_size
            and job.source_config_id == source_config_id
            and _payload_signature(dict(job.payload or {})) == _payload_signature(payload)
        ]
        owned_jobs: list[tuple[str, list[str]]] = []
        for index, batch in enumerate(batches):
            batch_payload = dict(payload)
            batch_payload["event_ids"] = batch
            batch_payload["batch_index"] = index
            batch_payload["batch_count"] = len(batches)
            is_tail_batch = (
                flush_window > 0
                and index == len(batches) - 1
                and len(batch) < settings.vector_write_job_batch_size
            )
            remaining_batch = list(batch)
            if is_tail_batch:
                cutoff = _now() - timedelta(seconds=flush_window)
                for tail_job in reversed(queued_tail_jobs):
                    if not remaining_batch:
                        break
                    if tail_job.created_at < cutoff:
                        continue
                    tail_payload = dict(tail_job.payload or {})
                    existing_ids = [str(value) for value in (tail_payload.get("event_ids") or []) if value]
                    room = max(
                        0,
                        settings.vector_write_job_batch_size
                        - len(list(dict.fromkeys(existing_ids))),
                    )
                    accepted = remaining_batch[:room]
                    merged_payload, remaining_batch = _merge_tail_payload(
                        tail_payload,
                        batch_payload,
                        remaining_batch,
                        batch_size=settings.vector_write_job_batch_size,
                    )
                    tail_job.payload = merged_payload
                    tail_job.record_count = len(merged_payload["event_ids"])
                    if tail_job.record_count >= settings.vector_write_job_batch_size:
                        tail_job.next_run_at = None
                    elif flush_window > 0:
                        tail_job.next_run_at = tail_job.next_run_at or (_now() + timedelta(seconds=flush_window))
                    if accepted:
                        owned_jobs.append((tail_job.id, accepted))
                if not remaining_batch:
                    continue
                batch_payload["event_ids"] = remaining_batch
                batch_payload["tail_flush_pending"] = True
                batch_payload["tail_flush_due_at"] = (_now() + timedelta(seconds=flush_window)).isoformat()
            job_id = uuid.uuid4().hex
            session.add(
                VectorWriteJob(
                    id=job_id,
                    kind="event_sync",
                    status="queued",
                    source_config_id=source_config_id,
                    payload=batch_payload,
                    embedding_version="default",
                    parent_batch_id=parent_batch_id,
                    record_count=len(batch_payload["event_ids"]),
                    max_attempts=8,
                    next_run_at=(
                        (_now() + timedelta(seconds=flush_window))
                        if is_tail_batch and flush_window > 0
                        else None
                    ),
                )
            )
            owned_jobs.append((job_id, list(batch_payload["event_ids"])))
        if owned_jobs:
            await session.flush()
            registered = 0
            skipped_active = 0
            for job_id, record_ids in owned_jobs:
                created, skipped = await register_job_items(
                    session,
                    job_id=job_id,
                    table_name="event_vectors",
                    source_config_id=source_config_id,
                    record_ids=record_ids,
                    embedding_version="default",
                    payload={"kind": "event_sync"},
                )
                registered += created
                skipped_active += skipped
            log.debug(
                "事件向量记录级明细已入队 source_config_id=%s created=%d skipped_active=%d",
                source_config_id,
                registered,
                skipped_active,
            )
        await session.commit()
    log.debug(
        "事件向量写入已入队 source_config_id=%s events=%d jobs=%d",
        source_config_id,
        len(event_ids),
        len(batches),
    )


async def _enqueue_aux_vector_sync(
    session_factory: async_sessionmaker,
    *,
    kind: str,
    table_name: str,
    record_id_key: str,
    events: list[Any],
    config: Any,
) -> None:
    """Enqueue an auxiliary vector table (entity_vectors / event_entity_vectors).

    Auxiliary jobs are P1 and share the single writer with P0 event/source-chunk
    jobs. Record-level items are registered on ``table_name`` with the extracted
    record ids so deduplication, retry and audit work per record instead of per
    event.
    """
    if not _aux_vector_sync_enabled(True):
        return
    if not disk_guard.allow_aux():
        log.warning("磁盘保护：跳过辅助向量入队 kind=%s source=%s level=%s free=%.1fGB",
                    kind, getattr(config, "source_config_id", "?"),
                    disk_guard.current().level, disk_guard.current().free_gb)
        return
    event_ids = [str(getattr(event, "id", "") or "") for event in events]
    event_ids = [event_id for event_id in event_ids if event_id]
    if not event_ids:
        return
    source_config_id = str(getattr(config, "source_config_id", "") or "")
    if not source_config_id:
        return
    record_ids = (
        _extract_entity_ids(events)
        if record_id_key == "entity_ids"
        else _extract_assoc_ids(events)
    )
    if not record_ids:
        return
    payload = {
        "event_ids": event_ids,
        record_id_key: record_ids,
        "chunk_ids": list(getattr(config, "chunk_ids", []) or []),
        "embedding_batch_size": int(getattr(config, "embedding_batch_size", 10) or 10),
        "index_batch_size": int(getattr(config, "index_batch_size", 50) or 50),
        "embedding_max_length": int(getattr(config, "embedding_max_length", 500) or 500),
        "enable_entity_vector_sync": False,
        "enable_event_entity_vector_sync": False,
    }
    async with session_factory() as session:
        pending_jobs = (
            (
                await session.execute(
                    select(VectorWriteJob)
                    .where(
                        VectorWriteJob.kind == kind,
                        VectorWriteJob.source_config_id == source_config_id,
                        VectorWriteJob.status.in_(ACTIVE_JOB_STATUSES),
                    )
                    .order_by(VectorWriteJob.created_at.asc(), VectorWriteJob.id.asc())
                )
            )
            .scalars()
            .all()
        )
        scheduled_ids = {
            str(value)
            for job in pending_jobs
            for value in (dict(job.payload or {}).get(record_id_key) or [])
            if value
        }
        record_ids = [
            record_id
            for record_id in dict.fromkeys(record_ids)
            if record_id not in scheduled_ids
        ]
        if not record_ids:
            log.debug(
                "辅助向量写入跳过入队 kind=%s source_config_id=%s reason=already_pending",
                kind,
                source_config_id,
            )
            return
        batches = _split_event_ids(record_ids, batch_size=settings.vector_write_job_batch_size)
        parent_batch_id = uuid.uuid4().hex
        flush_window = max(0.0, float(settings.vector_write_tail_flush_seconds or 0.0))
        queued_tail_jobs = [
            job
            for job in pending_jobs
            if job.status in PENDING_JOB_STATUSES
            and job.record_count < settings.vector_write_job_batch_size
            and job.source_config_id == source_config_id
            and _payload_signature(dict(job.payload or {})) == _payload_signature(payload)
        ]
        owned_jobs: list[tuple[str, list[str]]] = []
        for index, batch in enumerate(batches):
            batch_payload = dict(payload)
            batch_payload[record_id_key] = batch
            batch_payload["batch_index"] = index
            batch_payload["batch_count"] = len(batches)
            is_tail_batch = (
                flush_window > 0
                and index == len(batches) - 1
                and len(batch) < settings.vector_write_job_batch_size
            )
            remaining_batch = list(batch)
            if is_tail_batch:
                cutoff = _now() - timedelta(seconds=flush_window)
                for tail_job in reversed(queued_tail_jobs):
                    if not remaining_batch:
                        break
                    if tail_job.created_at < cutoff:
                        continue
                    tail_payload = dict(tail_job.payload or {})
                    existing_ids = [
                        str(value) for value in (tail_payload.get(record_id_key) or []) if value
                    ]
                    room = max(
                        0,
                        settings.vector_write_job_batch_size
                        - len(list(dict.fromkeys(existing_ids))),
                    )
                    accepted = remaining_batch[:room]
                    merged_payload, remaining_batch = _merge_tail_payload(
                        tail_payload,
                        batch_payload,
                        remaining_batch,
                        batch_size=settings.vector_write_job_batch_size,
                        record_id_key=record_id_key,
                    )
                    tail_job.payload = merged_payload
                    tail_job.record_count = len(merged_payload[record_id_key])
                    if tail_job.record_count >= settings.vector_write_job_batch_size:
                        tail_job.next_run_at = None
                    elif flush_window > 0:
                        tail_job.next_run_at = tail_job.next_run_at or (
                            _now() + timedelta(seconds=flush_window)
                        )
                    if accepted:
                        owned_jobs.append((tail_job.id, accepted))
                if not remaining_batch:
                    continue
                batch_payload[record_id_key] = remaining_batch
                batch_payload["tail_flush_pending"] = True
                batch_payload["tail_flush_due_at"] = (
                    _now() + timedelta(seconds=flush_window)
                ).isoformat()
            job_id = uuid.uuid4().hex
            session.add(
                VectorWriteJob(
                    id=job_id,
                    kind=kind,
                    status="queued",
                    source_config_id=source_config_id,
                    payload=batch_payload,
                    embedding_version="default",
                    parent_batch_id=parent_batch_id,
                    record_count=len(batch_payload[record_id_key]),
                    max_attempts=8,
                    next_run_at=(
                        (_now() + timedelta(seconds=flush_window))
                        if is_tail_batch and flush_window > 0
                        else None
                    ),
                )
            )
            owned_jobs.append((job_id, list(batch_payload[record_id_key])))
        if owned_jobs:
            await session.flush()
            registered = 0
            skipped_active = 0
            for job_id, record_ids in owned_jobs:
                created, skipped = await register_job_items(
                    session,
                    job_id=job_id,
                    table_name=table_name,
                    source_config_id=source_config_id,
                    record_ids=record_ids,
                    embedding_version="default",
                    payload={"kind": kind},
                )
                registered += created
                skipped_active += skipped
            log.debug(
                "辅助向量记录级明细已入队 kind=%s source_config_id=%s created=%d skipped_active=%d",
                kind,
                source_config_id,
                registered,
                skipped_active,
            )
        await session.commit()
    log.debug(
        "辅助向量写入已入队 kind=%s source_config_id=%s records=%d jobs=%d",
        kind,
        source_config_id,
        len(record_ids),
        len(batches),
    )


async def enqueue_entity_vector_sync(
    session_factory: async_sessionmaker,
    events: list[Any],
    config: Any,
) -> None:
    """Enqueue entity_vectors writes for the events' unique entities."""
    if not _aux_vector_sync_enabled(getattr(config, "enable_entity_vector_sync", True)):
        return
    await _enqueue_aux_vector_sync(
        session_factory,
        kind="entity_sync",
        table_name="entity_vectors",
        record_id_key="entity_ids",
        events=list(events or []),
        config=config,
    )


async def enqueue_event_entity_vector_sync(
    session_factory: async_sessionmaker,
    events: list[Any],
    config: Any,
) -> None:
    """Enqueue event_entity_vectors writes for the events' associations."""
    if not _aux_vector_sync_enabled(getattr(config, "enable_event_entity_vector_sync", True)):
        return
    await _enqueue_aux_vector_sync(
        session_factory,
        kind="event_entity_sync",
        table_name="event_entity_vectors",
        record_id_key="assoc_ids",
        events=list(events or []),
        config=config,
    )


async def _count_source_chunks(source_id: str, source_type: str) -> tuple[str, int]:
    from zleap.sag.db import SourceChunk, get_session_factory

    session_factory = get_session_factory()
    async with session_factory() as session:
        row = await session.execute(
            select(SourceChunk.source_config_id, func.count(SourceChunk.id))
            .where(
                SourceChunk.source_id == source_id,
                SourceChunk.source_type == source_type,
            )
            .group_by(SourceChunk.source_config_id)
            .limit(1)
        )
        value = row.first()
    if value is None:
        return "", 0
    return str(value[0] or ""), int(value[1] or 0)


async def enqueue_source_chunk_vector_sync(
    session_factory: async_sessionmaker,
    source_id: str,
    source_type: str,
) -> None:
    if not disk_guard.allow_vector():
        log.warning("磁盘保护：跳过 source_chunk 向量入队 source_id=%s level=%s free=%.1fGB",
                    source_id, disk_guard.current().level, disk_guard.current().free_gb)
        return
    source_id = str(source_id or "")
    source_type = str(source_type or "")
    if not source_id or not source_type:
        return
    source_config_id, record_count = await _count_source_chunks(source_id, source_type)
    if not source_config_id or record_count <= 0:
        log.warning(
            "SourceChunk 向量写入跳过 source_id=%s source_type=%s reason=no_chunks",
            source_id,
            source_type,
        )
        return
    payload = {
        "source_id": source_id,
        "source_type": source_type,
        "embedding_batch_size": settings.source_chunk_vector_embedding_batch_size,
        "index_batch_size": settings.source_chunk_vector_index_batch_size,
    }
    async with session_factory() as session:
        active_job = await session.scalar(
            select(VectorWriteJob)
            .where(
                VectorWriteJob.kind == "source_chunk_sync",
                VectorWriteJob.source_config_id == source_config_id,
                VectorWriteJob.status.in_(ACTIVE_JOB_STATUSES),
                VectorWriteJob.payload["source_id"].as_string() == source_id,
                VectorWriteJob.payload["source_type"].as_string() == source_type,
            )
            .order_by(VectorWriteJob.created_at.desc(), VectorWriteJob.id.desc())
            .limit(1)
        )
        if active_job is not None:
            active_payload = dict(active_job.payload or {})
            active_payload.update(payload)
            active_payload["deduplicated_at"] = _now().isoformat()
            active_job.payload = active_payload
            active_job.record_count = record_count
            log.debug(
                "SourceChunk 向量写入跳过入队 source_id=%s reason=already_pending",
                source_id,
            )
            await session.commit()
            return
        session.add(
            VectorWriteJob(
                kind="source_chunk_sync",
                status="queued",
                source_config_id=source_config_id,
                payload=payload,
                embedding_version="default",
                parent_batch_id=uuid.uuid4().hex,
                record_count=record_count,
                max_attempts=8,
            )
        )
        await session.commit()
    log.debug(
        "SourceChunk 向量写入已入队 source_config_id=%s source_id=%s chunks=%d",
        source_config_id,
        source_id,
        record_count,
    )


def install_event_vector_queue_patch(session_factory: async_sessionmaker) -> None:
    """Make zleap EventSaver enqueue vector writes instead of doing them inline."""

    global _PATCHED
    if _PATCHED:
        return
    from zleap.sag.modules.extract.saver import EventSaver

    current = getattr(EventSaver, "_sync_to_vector_store", None)
    if not callable(current):
        return
    if getattr(current, "_sag_api_vector_queue", False):
        _PATCHED = True
        return
    if not hasattr(EventSaver, "_sag_api_original_sync_to_vector_store"):
        EventSaver._sag_api_original_sync_to_vector_store = current  # type: ignore[attr-defined]

    async def queued_sync_to_vector_store(self, events, config):  # noqa: ANN001
        events = list(events or [])
        await enqueue_event_vector_sync(session_factory, events, config)
        # SAG-OPT-106：辅助向量独立入 P1 队列；deferred 开启时由入队函数内部跳过。
        await enqueue_entity_vector_sync(session_factory, events, config)
        await enqueue_event_entity_vector_sync(session_factory, events, config)

    queued_sync_to_vector_store._sag_api_vector_queue = True  # type: ignore[attr-defined]
    EventSaver._sync_to_vector_store = queued_sync_to_vector_store
    _PATCHED = True
    log.info("事件向量写入队列补丁已启用")


def install_source_chunk_vector_queue_patch(session_factory: async_sessionmaker) -> None:
    """Make zleap DocumentLoader enqueue SourceChunk vector writes."""

    global _SOURCE_CHUNK_PATCHED
    if _SOURCE_CHUNK_PATCHED:
        return
    from zleap.sag.modules.load.loader import BaseLoader

    current = getattr(BaseLoader, "_index_source_chunks_to_es", None)
    if not callable(current):
        return
    if getattr(current, "_sag_api_source_chunk_vector_queue", False):
        _SOURCE_CHUNK_PATCHED = True
        return
    if not hasattr(BaseLoader, "_sag_api_original_index_source_chunks_to_es"):
        BaseLoader._sag_api_original_index_source_chunks_to_es = current  # type: ignore[attr-defined]

    async def queued_index_source_chunks_to_es(self, source_id, source_type):  # noqa: ANN001
        await enqueue_source_chunk_vector_sync(session_factory, source_id, source_type)

    queued_index_source_chunks_to_es._sag_api_source_chunk_vector_queue = True  # type: ignore[attr-defined]
    BaseLoader._index_source_chunks_to_es = queued_index_source_chunks_to_es
    _SOURCE_CHUNK_PATCHED = True
    log.info("SourceChunk 向量写入队列补丁已启用")


class VectorWriteQueue:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        engine_manager: EngineManager,
    ) -> None:
        self._session_factory = session_factory
        self._engine_manager = engine_manager
        self._worker_id = f"vector-writer:{uuid.uuid4().hex}"
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._stopping = False

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        await self._recover_running()
        await self._enqueue_missing_event_vectors()
        self._task = asyncio.create_task(self._loop(), name="sag-vector-writer")
        log.info("事件向量后台写入队列已启动")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def wake(self) -> None:
        self._wake.set()

    async def _recover_running(self) -> None:
        recovered_at = _now()
        async with self._session_factory() as session:
            await session.execute(
                update(VectorWriteJob)
                .where(VectorWriteJob.status.in_(INFLIGHT_JOB_STATUSES))
                .values(
                    status="retry",
                    started_at=None,
                    next_run_at=recovered_at,
                    lease_owner=None,
                    lease_expires_at=None,
                    error="Recovered unfinished vector write job after process restart; retrying.",
                )
            )
            await session.execute(
                update(VectorWriteItem)
                .where(VectorWriteItem.status == "writing")
                .values(
                    status="retry",
                    next_run_at=recovered_at,
                    lease_owner=None,
                    lease_expires_at=None,
                    last_error="Recovered unfinished vector write item after process restart; retrying.",
                )
            )
            await session.commit()

    async def _enqueue_missing_event_vectors(self) -> None:
        """Startup reconciliation for the tiny crash window before enqueue.

        Event extraction writes authoritative rows into zleap's relational DB,
        then enqueues vector writes into SAG's metadata DB. If the process is
        killed exactly between those two commits, startup reconciliation compares
        active SourceEvent ids with event_vectors ids and enqueues only missing
        items. Existing successful vector rows are left untouched.
        """

        try:
            missing_by_source = await asyncio.to_thread(_missing_event_vector_ids)
        except Exception as error:  # noqa: BLE001
            log.warning("事件向量启动自检跳过：%s", _error_text(error))
            return
        created = 0
        trimmed = 0
        completed = 0
        async with self._session_factory() as session:
            pending_jobs = (
                (
                    await session.execute(
                        select(VectorWriteJob)
                        .where(
                            VectorWriteJob.kind == "event_sync",
                            VectorWriteJob.status.in_(ACTIVE_JOB_STATUSES),
                        )
                        .order_by(
                            case((VectorWriteJob.status.in_(INFLIGHT_JOB_STATUSES), 0), else_=1),
                            VectorWriteJob.created_at.asc(),
                            VectorWriteJob.id.asc(),
                        )
                    )
                )
                .scalars()
                .all()
            )
            planned, scheduled = _plan_pending_event_deduplication(
                missing_by_source,
                [
                    (
                        job.id,
                        job.source_config_id,
                        job.status,
                        dict(job.payload or {}),
                    )
                    for job in pending_jobs
                ],
            )
            for job in pending_jobs:
                if job.status in INFLIGHT_JOB_STATUSES:
                    continue
                payload = dict(job.payload or {})
                original_ids = [
                    str(value)
                    for value in (payload.get("event_ids") or [])
                    if value
                ]
                kept_ids = planned.get(job.id, [])
                if kept_ids == original_ids:
                    continue
                payload["event_ids"] = kept_ids
                payload["deduplicated_at"] = _now().isoformat()
                payload["deduplicated_removed"] = len(original_ids) - len(kept_ids)
                job.payload = payload
                trimmed += 1
                if not kept_ids:
                    payload["indexed"] = 0
                    payload["deduplicated_empty"] = True
                    job.payload = payload
                    job.status = "succeeded"
                    job.finished_at = _now()
                    job.next_run_at = None
                    job.error = None
                    completed += 1

            reconcile_jobs: list[tuple[str, str, list[str]]] = []
            for source_config_id, event_ids in sorted(missing_by_source.items()):
                unscheduled = [
                    event_id
                    for event_id in event_ids
                    if event_id not in scheduled[source_config_id]
                ]
                for offset in range(0, len(unscheduled), _RECONCILE_BATCH_SIZE):
                    batch = unscheduled[offset : offset + _RECONCILE_BATCH_SIZE]
                    if not batch:
                        continue
                    job_id = uuid.uuid4().hex
                    session.add(
                        VectorWriteJob(
                            id=job_id,
                            kind="event_sync",
                            status="queued",
                            source_config_id=source_config_id,
                            payload={
                                "event_ids": batch,
                                "chunk_ids": ["startup-reconcile"],
                                "embedding_batch_size": 10,
                                "index_batch_size": 50,
                                "embedding_max_length": 500,
                                "enable_entity_vector_sync": not settings.aux_vector_deferred_enabled,
                                "enable_event_entity_vector_sync": not settings.aux_vector_deferred_enabled,
                                "reason": "startup_reconcile_missing_event_vectors",
                            },
                            embedding_version="default",
                            parent_batch_id=uuid.uuid4().hex,
                            record_count=len(batch),
                            max_attempts=8,
                        )
                    )
                    reconcile_jobs.append((job_id, source_config_id, batch))
                    created += 1
            if reconcile_jobs:
                await session.flush()
                for job_id, source_config_id, batch in reconcile_jobs:
                    await register_job_items(
                        session,
                        job_id=job_id,
                        table_name="event_vectors",
                        source_config_id=source_config_id,
                        record_ids=batch,
                        embedding_version="default",
                        payload={"kind": "event_sync", "reason": "startup_reconcile_missing_event_vectors"},
                    )
            await session.commit()
        if trimmed:
            log.warning(
                "事件向量启动自检已去重 jobs=%d emptied=%d",
                trimmed,
                completed,
            )
        if created:
            log.warning("事件向量启动自检发现缺失，已补队列 jobs=%d", created)

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                worked = await self._run_one_due()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                log.exception("事件向量后台写入循环异常：%s", error)
                worked = False
            if worked:
                continue
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=_POLL_SECONDS)
                self._wake.clear()
            except TimeoutError:
                pass

    async def _next_due_job_id(self) -> str | None:
        now = _now()
        async with self._session_factory() as session:
            job = await session.scalar(
                select(VectorWriteJob)
                .where(
                    VectorWriteJob.status.in_(PENDING_JOB_STATUSES),
                    (VectorWriteJob.next_run_at.is_(None) | (VectorWriteJob.next_run_at <= now)),
                )
                .order_by(
                    case(
                        (VectorWriteJob.kind.in_(("event_sync", "source_chunk_sync")), 0),
                        else_=1,
                    ),
                    VectorWriteJob.created_at.asc(),
                    VectorWriteJob.id.asc(),
                )
                .limit(1)
            )
            return job.id if job is not None else None

    async def _run_one_due(self) -> bool:
        job_id = await self._next_due_job_id()
        if not job_id:
            return False
        started_at = _now()
        async with self._session_factory() as session:
            claim = await session.execute(
                update(VectorWriteJob)
                .where(VectorWriteJob.id == job_id, VectorWriteJob.status.in_(PENDING_JOB_STATUSES))
                .values(
                    status="writing",
                    attempts=VectorWriteJob.attempts + 1,
                    started_at=started_at,
                    next_run_at=None,
                    lease_owner=self._worker_id,
                    lease_expires_at=_lease_expires_at(started_at),
                    finished_at=None,
                    error=None,
                )
            )
            if claim.rowcount == 1:
                await claim_job_items(
                    session,
                    job_id=job_id,
                    lease_owner=self._worker_id,
                    lease_expires_at=_lease_expires_at(started_at),
                )
            await session.commit()
            if claim.rowcount != 1:
                return True

        try:
            await self._process(job_id)
        except Exception as error:  # noqa: BLE001
            await self._mark_failed_or_retry(job_id, error)
        return True

    async def _process(self, job_id: str) -> None:
        async with self._session_factory() as session:
            job = await session.get(VectorWriteJob, job_id)
            if job is None or job.status not in INFLIGHT_JOB_STATUSES:
                return
            kind = job.kind

        if kind == "source_chunk_sync":
            await self._process_source_chunks(job_id)
            return
        if kind == "event_sync":
            await self._process_events(job_id)
            return
        if kind == "entity_sync":
            await self._process_entities(job_id)
            return
        if kind == "event_entity_sync":
            await self._process_event_entities(job_id)
            return
        raise RuntimeError(f"未知向量写入任务类型: {kind}")

    async def _process_source_chunks(self, job_id: str) -> None:
        from zleap.sag.modules.load.loader import BaseLoader, DocumentLoader

        async with self._session_factory() as session:
            job = await session.get(VectorWriteJob, job_id)
            if job is None or job.status not in INFLIGHT_JOB_STATUSES:
                return
            payload = dict(job.payload or {})
            source_config_id = job.source_config_id

        source_id = str(payload.get("source_id") or "")
        source_type = str(payload.get("source_type") or "ARTICLE")
        if not source_id:
            await self._mark_succeeded(job_id, indexed=0)
            return

        await self._engine_manager.provision(source_config_id, None)
        loader = DocumentLoader()
        loader._embedding_batch_size = int(
            payload.get("embedding_batch_size") or settings.source_chunk_vector_embedding_batch_size
        )
        loader._es_bulk_index_size = int(
            payload.get("index_batch_size") or settings.source_chunk_vector_index_batch_size
        )
        original = getattr(
            BaseLoader,
            "_sag_api_original_index_source_chunks_to_es",
            BaseLoader._index_source_chunks_to_es,
        )
        log.info(
            "SourceChunk 向量后台写入开始 job=%s source_config_id=%s source_id=%s",
            job_id,
            source_config_id,
            source_id,
        )
        async with _WRITE_LOCK:
            await original(loader, source_id, source_type)
        _, indexed = await _count_source_chunks(source_id, source_type)
        await self._mark_succeeded(job_id, indexed=indexed)
        log.info(
            "SourceChunk 向量后台写入完成 job=%s source_config_id=%s indexed=%d",
            job_id,
            source_config_id,
            indexed,
        )

    async def _process_events(self, job_id: str) -> None:
        from zleap.sag.modules.extract.config import ExtractConfig
        from zleap.sag.modules.extract.saver import EventSaver

        async with self._session_factory() as session:
            job = await session.get(VectorWriteJob, job_id)
            if job is None or job.status not in INFLIGHT_JOB_STATUSES:
                return
            payload = dict(job.payload or {})
            source_config_id = job.source_config_id

        event_ids = [str(value) for value in payload.get("event_ids", []) if value]
        if not event_ids:
            await self._mark_succeeded(job_id, indexed=0)
            return

        # Ensure zleap storage clients and schemas are initialized before loading
        # and writing vector data. ``source=None`` is enough for read/write paths
        # that use global settings.
        await self._engine_manager.provision(source_config_id, None)

        saver = EventSaver()
        log.info(
            "事件向量后台写入开始 job=%s source_config_id=%s events=%d",
            job_id,
            source_config_id,
            len(event_ids),
        )
        events = await saver._load_events_with_relations(event_ids)  # noqa: SLF001
        if not events:
            log.info(
                "事件向量后台写入跳过 job=%s source_config_id=%s reason=no_events",
                job_id,
                source_config_id,
            )
            await self._mark_succeeded(job_id, indexed=0)
            return

        config = ExtractConfig(
            source_config_id=source_config_id,
            chunk_ids=list(payload.get("chunk_ids") or ["vector-write-queue"]),
            embedding_batch_size=int(payload.get("embedding_batch_size") or 10),
            index_batch_size=int(payload.get("index_batch_size") or 50),
            embedding_max_length=int(payload.get("embedding_max_length") or 500),
            enable_entity_vector_sync=bool(payload.get("enable_entity_vector_sync", True)),
            enable_event_entity_vector_sync=bool(
                payload.get("enable_event_entity_vector_sync", True)
            ),
        )
        original = getattr(
            EventSaver,
            "_sag_api_original_sync_to_vector_store",
            EventSaver._sync_to_vector_store,
        )
        async with _WRITE_LOCK:
            await original(saver, events, config)
        await self._mark_succeeded(job_id, indexed=len(events))
        log.info(
            "事件向量后台写入完成 job=%s source_config_id=%s indexed=%d",
            job_id,
            source_config_id,
            len(events),
        )

    async def _job_context(self, job_id: str) -> tuple[dict[str, Any], str] | None:
        async with self._session_factory() as session:
            job = await session.get(VectorWriteJob, job_id)
            if job is None or job.status not in INFLIGHT_JOB_STATUSES:
                return None
            return dict(job.payload or {}), job.source_config_id

    async def _process_entities(self, job_id: str) -> None:
        from zleap.sag.modules.extract.config import ExtractConfig
        from zleap.sag.modules.extract.saver import EventSaver

        context = await self._job_context(job_id)
        if context is None:
            return
        payload, source_config_id = context
        event_ids = [str(value) for value in payload.get("event_ids", []) if value]
        entity_ids = [str(value) for value in payload.get("entity_ids", []) if value]
        if not event_ids or not entity_ids:
            await self._mark_succeeded(job_id, indexed=0)
            return

        await self._engine_manager.provision(source_config_id, None)
        saver = EventSaver()
        log.info(
            "实体向量后台写入开始 job=%s source_config_id=%s entities=%d",
            job_id,
            source_config_id,
            len(entity_ids),
        )
        events = await saver._load_events_with_relations(event_ids)  # noqa: SLF001
        if not events:
            await self._mark_succeeded(job_id, indexed=0)
            return
        entities = list((await saver._collect_unique_entities(events)).values())  # noqa: SLF001
        expected = set(entity_ids)
        entities = [
            entity
            for entity in entities
            if str(getattr(entity, "id", "") or "") in expected
        ]
        if not entities:
            await self._mark_succeeded(job_id, indexed=0)
            return
        config = ExtractConfig(
            source_config_id=source_config_id,
            chunk_ids=list(payload.get("chunk_ids") or ["vector-write-queue"]),
            embedding_batch_size=int(payload.get("embedding_batch_size") or 10),
            index_batch_size=int(payload.get("index_batch_size") or 50),
            embedding_max_length=int(payload.get("embedding_max_length") or 500),
            enable_entity_vector_sync=True,
            enable_event_entity_vector_sync=False,
        )
        async with _WRITE_LOCK:
            await saver._sync_entities(entities, config)  # noqa: SLF001
        await self._mark_succeeded(job_id, indexed=len(entities))
        log.info(
            "实体向量后台写入完成 job=%s source_config_id=%s indexed=%d",
            job_id,
            source_config_id,
            len(entities),
        )

    async def _process_event_entities(self, job_id: str) -> None:
        from zleap.sag.modules.extract.config import ExtractConfig
        from zleap.sag.modules.extract.saver import EventSaver

        context = await self._job_context(job_id)
        if context is None:
            return
        payload, source_config_id = context
        event_ids = [str(value) for value in payload.get("event_ids", []) if value]
        assoc_ids = [str(value) for value in payload.get("assoc_ids", []) if value]
        if not event_ids or not assoc_ids:
            await self._mark_succeeded(job_id, indexed=0)
            return

        await self._engine_manager.provision(source_config_id, None)
        saver = EventSaver()
        log.info(
            "事件-实体关联向量后台写入开始 job=%s source_config_id=%s assocs=%d",
            job_id,
            source_config_id,
            len(assoc_ids),
        )
        events = await saver._load_events_with_relations(event_ids)  # noqa: SLF001
        if not events:
            await self._mark_succeeded(job_id, indexed=0)
            return
        config = ExtractConfig(
            source_config_id=source_config_id,
            chunk_ids=list(payload.get("chunk_ids") or ["vector-write-queue"]),
            embedding_batch_size=int(payload.get("embedding_batch_size") or 10),
            index_batch_size=int(payload.get("index_batch_size") or 50),
            embedding_max_length=int(payload.get("embedding_max_length") or 500),
            enable_entity_vector_sync=False,
            enable_event_entity_vector_sync=True,
        )
        async with _WRITE_LOCK:
            await saver._sync_event_entities(events, config)  # noqa: SLF001
        expected = set(assoc_ids)
        indexed = sum(
            1
            for event in events
            for assoc in getattr(event, "event_associations", None) or []
            if str(getattr(assoc, "id", "") or "") in expected
        )
        await self._mark_succeeded(job_id, indexed=indexed)
        log.info(
            "事件-实体关联向量后台写入完成 job=%s source_config_id=%s indexed=%d",
            job_id,
            source_config_id,
            indexed,
        )

    async def _mark_succeeded(self, job_id: str, *, indexed: int) -> None:
        async with self._session_factory() as session:
            job = await session.get(VectorWriteJob, job_id)
            if job is None:
                return
            payload = dict(job.payload or {})
            payload["indexed"] = indexed
            job.payload = payload
            job.status = "succeeded"
            job.finished_at = _now()
            job.lease_owner = None
            job.lease_expires_at = None
            job.error = None
            await complete_job_items(session, job_id=job_id, status="succeeded")
            await session.commit()

    async def _mark_failed_or_retry(self, job_id: str, error: Exception) -> None:
        async with self._session_factory() as session:
            job = await session.get(VectorWriteJob, job_id)
            if job is None:
                return
            message = _error_text(error)
            retryable = _is_retryable_error(error)
            payload = dict(job.payload or {})
            # SAG-OPT-106：只有 event_sync 拆批重试；aux 任务（entity_sync /
            # event_entity_sync）批量已按记录数限制，整批重试即可，避免跨事件
            # 集合拆分后无法按记录明细继续跟踪。
            record_key = (
                "entity_ids"
                if job.kind == "entity_sync"
                else "assoc_ids"
                if job.kind == "event_entity_sync"
                else "event_ids"
            )
            record_ids = [str(value) for value in payload.get(record_key, []) if value]
            if retryable and job.kind == "event_sync" and len(record_ids) > 1:
                split_group_id = uuid.uuid4().hex
                split_batches = _split_retry_batches(record_ids)
                parent_batch_id = job.parent_batch_id or split_group_id
                for index, batch in enumerate(split_batches):
                    child_id = uuid.uuid4().hex
                    child_payload = dict(payload)
                    child_payload["event_ids"] = batch
                    child_payload["split_from_job_id"] = job.id
                    child_payload["split_from_attempt"] = job.attempts
                    child_payload["split_reason"] = "retryable_batch_split"
                    child_payload["split_batch_index"] = index
                    child_payload["split_batch_count"] = len(split_batches)
                    child_payload["split_group_id"] = split_group_id
                    session.add(
                        VectorWriteJob(
                            id=child_id,
                            kind=job.kind,
                            status="retry",
                            source_config_id=job.source_config_id,
                            payload=child_payload,
                            attempts=job.attempts,
                            max_attempts=job.max_attempts,
                            embedding_version=job.embedding_version,
                            parent_batch_id=parent_batch_id,
                            record_count=len(batch),
                            next_run_at=_now(),
                        )
                    )
                    await reassign_job_items(
                        session,
                        from_job_id=job.id,
                        to_job_id=child_id,
                        record_ids=batch,
                        status="retry",
                        next_run_at=_now(),
                    )
                payload["split_at"] = _now().isoformat()
                payload["split_reason"] = "retryable_batch_split"
                payload["split_group_id"] = split_group_id
                payload["split_error"] = message
                payload["split_into_jobs"] = len(split_batches)
                payload["indexed"] = 0
                job.payload = payload
                job.status = "succeeded"
                job.finished_at = _now()
                job.next_run_at = None
                job.superseded_by = split_group_id
                job.lease_owner = None
                job.lease_expires_at = None
                job.error = None
                log.warning(
                    "事件向量写入失败后拆批重试 job=%s attempts=%d split_jobs=%d err=%s",
                    job_id,
                    job.attempts,
                    len(split_batches),
                    message,
                )
                # 兜底：父任务中未被 reassign 覆盖的残余 active 明细（例如
                # 明细与 payload 不一致的旧数据）收尾为 failed，避免它们永远
                # 卡在 active 状态影响 active_record_ids 去重。
                await complete_job_items(
                    session,
                    job_id=job_id,
                    status="failed",
                    error="superseded by split",
                )
            elif retryable and job.attempts < job.max_attempts:
                delay = _retry_delay_with_jitter(job.attempts)
                job.status = "retry"
                job.next_run_at = _now() + timedelta(seconds=delay)
                job.lease_owner = None
                job.lease_expires_at = None
                job.error = f"第 {job.attempts} 次失败，{delay:.0f}s 后重试：{message}"
                await complete_job_items(
                    session,
                    job_id=job_id,
                    status="retry",
                    next_run_at=job.next_run_at,
                    error=f"第 {job.attempts} 次失败，{delay:.0f}s 后重试：{message}",
                )
                log.warning("事件向量写入将重试 job=%s attempts=%d err=%s", job_id, job.attempts, message)
            else:
                job.status = "failed"
                job.finished_at = _now()
                job.lease_owner = None
                job.lease_expires_at = None
                job.error = message
                await complete_job_items(
                    session,
                    job_id=job_id,
                    status="failed",
                    error=message,
                )
                log.error("事件向量写入最终失败 job=%s attempts=%d err=%s", job_id, job.attempts, message)
            await session.commit()


def _missing_event_vector_ids() -> dict[str, list[str]]:
    import sqlite3
    from collections import defaultdict
    from pathlib import Path

    started = time.perf_counter()
    tracemalloc_started = False
    if not tracemalloc.is_tracing():
        tracemalloc.start()
        tracemalloc_started = True
    data_dir = Path(settings.data_dir)
    engine_db = data_dir / "sag.db"
    vector_scan_rows = 0
    active_rows: list[tuple[Any, Any]] = []
    missing: dict[str, list[str]] = defaultdict(list)
    con = sqlite3.connect(engine_db, timeout=60)
    try:
        con.execute("PRAGMA busy_timeout=60000")
        active_rows = con.execute(
            """
            select id, source_config_id
            from source_event
            where status is null or status != 'DELETED'
            """
        ).fetchall()
    finally:
        con.close()
    try:
        if not active_rows:
            return {}

        vector_ids: set[str] = set()
        try:
            import lancedb

            table = lancedb.connect(str(data_dir / "lancedb")).open_table("event_vectors")
            schema_columns = {field.name for field in table.schema}
            id_column = (
                "event_id"
                if "event_id" in schema_columns
                else "id"
                if "id" in schema_columns
                else None
            )
            if id_column:
                offset = 0
                batch_size = 100_000
                while True:
                    arrow = (
                        table.search()
                        .select([id_column])
                        .limit(batch_size)
                        .offset(offset)
                        .to_arrow()
                    )
                    vector_scan_rows += int(arrow.num_rows)
                    if arrow.num_rows == 0:
                        break
                    vector_ids.update(
                        str(value) for value in arrow.column(id_column).to_pylist() if value
                    )
                    if arrow.num_rows < batch_size:
                        break
                    offset += batch_size
        except Exception:
            vector_ids = set()

        for event_id, source_config_id in active_rows:
            event_id = str(event_id or "")
            if event_id and event_id not in vector_ids:
                missing[str(source_config_id or "")].append(event_id)
        return dict(missing)
    finally:
        current_bytes, peak_bytes = tracemalloc.get_traced_memory()
        if tracemalloc_started:
            tracemalloc.stop()
        del current_bytes
        elapsed = time.perf_counter() - started
        missing_count = sum(len(ids) for ids in missing.values())
        log.info(
            "事件向量启动自检统计 active_events=%d vector_ids=%d scanned_vector_rows=%d "
            "missing=%d elapsed=%.2fs peak_memory=%.2fMB",
            len(active_rows),
            len(locals().get("vector_ids", set())),
            vector_scan_rows,
            missing_count,
            elapsed,
            peak_bytes / 1024**2,
        )
