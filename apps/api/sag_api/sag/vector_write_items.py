"""Record-level detail helpers for the durable vector-write queue (V2).

A ``VectorWriteItem`` describes one pending write of ``(table_name,
record_id, embedding_version)``. Job payloads stay the unit of work for the
single writer; item rows add per-record deduplication, observability and
state recovery.  Terminal rows (succeeded/failed) are never deleted so the
audit trail survives retries, splits and superseded batches.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Iterable

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.db.models.vector_write import (
    VECTOR_ITEM_ACTIVE_STATUSES,
    VectorWriteItem,
)

ITEM_STATUSES = (
    "queued",
    "embedding",
    "ready_to_write",
    "writing",
    "succeeded",
    "retry",
    "failed",
)
ITEM_ACTIVE_STATUSES = VECTOR_ITEM_ACTIVE_STATUSES

_CLAIMABLE_ITEM_STATUSES = ("queued", "retry")


def _now() -> datetime:
    return datetime.now(UTC)


def item_status_for_job(status: str) -> str:
    """Map a job status to the equivalent record-item status."""
    if status in ("writing", "running"):
        return "writing"
    if status in ("queued", "retry"):
        return status
    if status == "succeeded":
        return "succeeded"
    if status == "failed":
        return "failed"
    return "queued"


async def active_record_ids(
    session: AsyncSession,
    *,
    table_name: str,
    record_ids: Iterable[str],
    embedding_version: str,
) -> set[str]:
    """Return the subset of ``record_ids`` that already have an active item."""
    ids = [str(value) for value in record_ids if value]
    if not ids:
        return set()
    rows = (
        (
            await session.execute(
                select(VectorWriteItem.record_id)
                .where(
                    VectorWriteItem.table_name == table_name,
                    VectorWriteItem.embedding_version == embedding_version,
                    VectorWriteItem.status.in_(ITEM_ACTIVE_STATUSES),
                    VectorWriteItem.record_id.in_(ids),
                )
            )
        )
        .scalars()
        .all()
    )
    return {str(value) for value in rows}


async def register_job_items(
    session: AsyncSession,
    *,
    job_id: str,
    table_name: str,
    source_config_id: str,
    record_ids: Iterable[str],
    embedding_version: str = "default",
    payload: dict[str, Any] | None = None,
) -> tuple[int, int]:
    """Idempotently register record-level items for a job.

    Returns ``(created, skipped)``. Records that already have an active item
    (same table/record/embedding version) are skipped, so a record can never
    be pending more than once regardless of which job references it.
    """
    ids = list(dict.fromkeys(str(value) for value in record_ids if value))
    if not ids:
        return 0, 0
    existing = await active_record_ids(
        session,
        table_name=table_name,
        record_ids=ids,
        embedding_version=embedding_version,
    )
    item_payload = dict(payload or {})
    created = 0
    skipped = 0
    for record_id in ids:
        if record_id in existing:
            skipped += 1
            continue
        session.add(
            VectorWriteItem(
                job_id=job_id,
                table_name=table_name,
                record_id=record_id,
                embedding_version=embedding_version,
                source_config_id=source_config_id,
                status="queued",
                payload=item_payload,
            )
        )
        created += 1
    return created, skipped


async def claim_job_items(
    session: AsyncSession,
    *,
    job_id: str,
    lease_owner: str,
    lease_expires_at: datetime,
) -> int:
    """Claim pending items of a job (queued/retry -> writing)."""
    result = await session.execute(
        update(VectorWriteItem)
        .where(
            VectorWriteItem.job_id == job_id,
            VectorWriteItem.status.in_(_CLAIMABLE_ITEM_STATUSES),
        )
        .values(
            status="writing",
            lease_owner=lease_owner,
            lease_expires_at=lease_expires_at,
        )
    )
    return int(result.rowcount or 0)


async def complete_job_items(
    session: AsyncSession,
    *,
    job_id: str,
    status: str,
    error: str | None = None,
    next_run_at: datetime | None = None,
) -> int:
    """Move every active item of a job to ``status``.

    ``status`` must be a terminal or pending-retry state (succeeded, failed,
    retry). ``error`` and ``next_run_at`` are recorded on the items for audit
    and recovery.
    """
    if status not in ("succeeded", "failed", "retry"):
        raise ValueError(f"invalid item completion status: {status}")
    values: dict[str, Any] = {
        "status": status,
        "last_error": error,
        "next_run_at": next_run_at,
        "lease_owner": None,
        "lease_expires_at": None,
    }
    result = await session.execute(
        update(VectorWriteItem)
        .where(
            VectorWriteItem.job_id == job_id,
            VectorWriteItem.status.in_(ITEM_ACTIVE_STATUSES),
        )
        .values(**values)
    )
    return int(result.rowcount or 0)


# Auxiliary vector tables written by the SAG-OPT-106 P1 queue. ``event_vectors``
# and ``source_chunks`` are P0 (core retrieval), so they are excluded from the
# "index backfill" signal: a pending core item is a real availability problem,
# while pending aux items just mean "core done / aux index still filling".
AUX_VECTOR_TABLES = ("entity_vectors", "event_entity_vectors")


def _default_backfill_status(source_config_id: str) -> dict[str, Any]:
    return {
        "source_config_id": source_config_id,
        "status": "unknown" if not source_config_id else "complete",
        "pending_records": 0,
        "by_table": {"entity_vectors": 0, "event_entity_vectors": 0},
    }


async def aux_index_backfill_status(
    session: AsyncSession,
    source_config_ids: Iterable[str],
    *,
    aux_vector_deferred_enabled: bool,
) -> dict[str, dict[str, Any]]:
    """Return the aux-vector index backfill signal per source config.

    The signal is derived from the record-level queue (``vector_write_items``)
    for the P1 aux tables only. A record is "pending" while its item is in an
    active state (queued/embedding/ready_to_write/writing/retry).

    ``status``:
      - ``backfilling`` -- at least one active aux item exists for the source
        (core relation data already written, aux index still filling).
      - ``deferred``    -- ``aux_vector_deferred_enabled`` is on and no aux
        item is pending; the app has deliberately paused aux vector writes, so
        the index must not be presented as "complete".
      - ``complete``    -- no pending aux items and deferred mode is off.
      - ``unknown``     -- no source config id was provided.
    """
    ids = list(dict.fromkeys(str(value) for value in source_config_ids if value))
    statuses: dict[str, dict[str, Any]] = {
        value: _default_backfill_status(value) for value in ids
    }
    if not ids:
        return statuses
    rows = await session.execute(
        select(
            VectorWriteItem.source_config_id,
            VectorWriteItem.table_name,
            func.count(VectorWriteItem.id),
        )
        .where(
            VectorWriteItem.source_config_id.in_(ids),
            VectorWriteItem.table_name.in_(AUX_VECTOR_TABLES),
            VectorWriteItem.status.in_(ITEM_ACTIVE_STATUSES),
        )
        .group_by(VectorWriteItem.source_config_id, VectorWriteItem.table_name)
    )
    for source_config_id, table_name, amount in rows.all():
        entry = statuses[str(source_config_id)]
        entry["by_table"][str(table_name)] = int(amount or 0)
        entry["pending_records"] += int(amount or 0)
        entry["status"] = "backfilling"
    if aux_vector_deferred_enabled:
        for entry in statuses.values():
            if entry["status"] != "backfilling":
                entry["status"] = "deferred"
    return statuses


def aggregate_aux_index_status(
    per_source: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Merge per-source signals into one workspace-level search signal."""
    entries = list(per_source.values())
    if not entries:
        merged = _default_backfill_status("")
        merged["sources"] = 0
        return merged
    total = {
        "source_config_id": "",
        "status": "complete",
        "pending_records": 0,
        "by_table": {"entity_vectors": 0, "event_entity_vectors": 0},
        "sources": len(entries),
    }
    statuses_seen: set[str] = set()
    for entry in entries:
        statuses_seen.add(entry["status"])
        total["pending_records"] += int(entry.get("pending_records") or 0)
        for table_name, amount in (entry.get("by_table") or {}).items():
            total["by_table"][table_name] = total["by_table"].get(table_name, 0) + int(
                amount or 0
            )
    if "backfilling" in statuses_seen:
        total["status"] = "backfilling"
    elif statuses_seen == {"deferred"}:
        total["status"] = "deferred"
    elif statuses_seen <= {"complete", "unknown"}:
        total["status"] = "complete" if "complete" in statuses_seen else "unknown"
    else:
        total["status"] = "deferred"
    return total


async def reassign_job_items(
    session: AsyncSession,
    *,
    from_job_id: str,
    to_job_id: str,
    record_ids: Iterable[str],
    status: str = "retry",
    next_run_at: datetime | None = None,
) -> int:
    """Re-point active items of a split parent batch to a child job."""
    ids = [str(value) for value in record_ids if value]
    if not ids:
        return 0
    result = await session.execute(
        update(VectorWriteItem)
        .where(
            VectorWriteItem.job_id == from_job_id,
            VectorWriteItem.record_id.in_(ids),
            VectorWriteItem.status.in_(ITEM_ACTIVE_STATUSES),
        )
        .values(
            job_id=to_job_id,
            status=status,
            next_run_at=next_run_at,
            lease_owner=None,
            lease_expires_at=None,
        )
    )
    return int(result.rowcount or 0)
