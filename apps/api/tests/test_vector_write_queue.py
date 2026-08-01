from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from types import SimpleNamespace

from sag_api.db.models.vector_write import VectorWriteItem, VectorWriteJob
from sag_api.sag.vector_write_queue import (
    ACTIVE_JOB_STATUSES,
    INFLIGHT_JOB_STATUSES,
    PENDING_JOB_STATUSES,
    enqueue_event_vector_sync,
    enqueue_source_chunk_vector_sync,
    _merge_tail_payload,
    _aux_vector_sync_enabled,
    _filter_unscheduled_event_ids,
    _is_retryable_error,
    _lease_expires_at,
    _plan_pending_event_deduplication,
    _retry_delay_with_jitter,
    _split_event_ids,
    _split_retry_batches,
    VectorWriteQueue,
)


async def _create_vector_tables(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(VectorWriteJob.__table__.create)
        await conn.run_sync(VectorWriteItem.__table__.create)


def test_pending_event_deduplication_keeps_one_missing_reference():
    planned, scheduled = _plan_pending_event_deduplication(
        {"source-a": ["event-1", "event-2", "event-3"]},
        [
            (
                "job-running",
                "source-a",
                "running",
                {"event_ids": ["event-1"]},
            ),
            (
                "job-first",
                "source-a",
                "queued",
                {"event_ids": ["event-1", "event-2", "event-2", "indexed"]},
            ),
            (
                "job-later",
                "source-a",
                "queued",
                {"event_ids": ["event-2", "event-3"]},
            ),
        ],
    )

    assert planned == {
        "job-running": ["event-1"],
        "job-first": ["event-2"],
        "job-later": ["event-3"],
    }
    assert scheduled == {
        "source-a": {"event-1", "event-2", "event-3"},
    }


def test_status_groups_cover_retry_and_writing_states():
    assert PENDING_JOB_STATUSES == ("queued", "retry")
    assert INFLIGHT_JOB_STATUSES == ("writing", "running")
    assert ACTIVE_JOB_STATUSES == ("queued", "retry", "writing", "running")


def test_pending_event_deduplication_keeps_sources_independent():
    planned, scheduled = _plan_pending_event_deduplication(
        {
            "source-a": ["same-id"],
            "source-b": ["same-id"],
        },
        [
            ("job-a", "source-a", "queued", {"event_ids": ["same-id"]}),
            ("job-b", "source-b", "queued", {"event_ids": ["same-id"]}),
        ],
    )

    assert planned["job-a"] == ["same-id"]
    assert planned["job-b"] == ["same-id"]
    assert scheduled["source-a"] == {"same-id"}
    assert scheduled["source-b"] == {"same-id"}


def test_filter_unscheduled_event_ids_excludes_active_payload_refs():
    assert _filter_unscheduled_event_ids(
        ["event-1", "event-2", "event-2", "event-3"],
        [
            {"event_ids": ["event-1"]},
            {"event_ids": ["event-3", "event-4"]},
        ],
    ) == ["event-2"]


def test_split_event_ids_uses_configured_batch_size():
    batches = _split_event_ids([f"event-{index}" for index in range(5)], batch_size=2)

    assert batches == [
        ["event-0", "event-1"],
        ["event-2", "event-3"],
        ["event-4"],
    ]


def test_lease_expires_at_adds_expected_window():
    started = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)

    assert _lease_expires_at(started) == datetime(2026, 7, 30, 12, 15, tzinfo=UTC)


def test_retryable_error_classifier_prefers_transient_failures():
    assert _is_retryable_error(RuntimeError("database is locked")) is True
    assert _is_retryable_error(RuntimeError("HTTP 503 service unavailable")) is True
    assert _is_retryable_error(RuntimeError("HTTP 404 not found")) is False
    assert _is_retryable_error(RuntimeError("validation error: bad payload")) is False


def test_retry_delay_with_jitter_stays_near_base_delay(monkeypatch):
    monkeypatch.setattr("sag_api.sag.vector_write_queue.random.uniform", lambda a, b: 1.1)

    assert _retry_delay_with_jitter(1) == 11.0


def test_split_retry_batches_halves_large_payloads():
    assert _split_retry_batches(["event-1"]) == [["event-1"]]
    assert _split_retry_batches(["event-1", "event-2"]) == [["event-1"], ["event-2"]]
    assert _split_retry_batches(["event-1", "event-2", "event-3"]) == [
        ["event-1"],
        ["event-2", "event-3"],
    ]


def test_merge_tail_payload_fills_existing_tail_before_creating_more():
    merged, leftover = _merge_tail_payload(
        {
            "event_ids": ["event-1", "event-2"],
            "chunk_ids": ["chunk-a"],
            "tail_merged_count": 1,
        },
        {
            "event_ids": ["event-3", "event-4"],
            "chunk_ids": ["chunk-b"],
        },
        ["event-3", "event-4"],
        batch_size=3,
    )

    assert merged["event_ids"] == ["event-1", "event-2", "event-3"]
    assert merged["chunk_ids"] == ["chunk-a", "chunk-b"]
    assert merged["tail_merged_count"] == 2
    assert merged["tail_flush_pending"] is False
    assert leftover == ["event-4"]


def test_aux_vector_sync_respects_deferred_setting(monkeypatch):
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.aux_vector_deferred_enabled", True)
    assert _aux_vector_sync_enabled(True) is False

    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.aux_vector_deferred_enabled", False)
    assert _aux_vector_sync_enabled(True) is True
    assert _aux_vector_sync_enabled(False) is False


@pytest.mark.asyncio
async def test_enqueue_event_vector_sync_delays_and_merges_tail_batches(tmp_path, monkeypatch):
    db_path = tmp_path / "vector-write-tail.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.vector_write_job_batch_size", 3)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.vector_write_tail_flush_seconds", 1.5)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.aux_vector_deferred_enabled", True)
    try:
        await _create_vector_tables(engine)

        config = SimpleNamespace(
            source_config_id="source-a",
            chunk_ids=["chunk-a"],
            embedding_batch_size=10,
            index_batch_size=50,
            embedding_max_length=500,
            enable_entity_vector_sync=True,
            enable_event_entity_vector_sync=True,
        )
        first_events = [SimpleNamespace(id="event-1"), SimpleNamespace(id="event-2")]
        second_events = [SimpleNamespace(id="event-3")]

        await enqueue_event_vector_sync(session_factory, first_events, config)
        await enqueue_event_vector_sync(session_factory, second_events, config)

        async with session_factory() as session:
            rows = (
                await session.execute(select(VectorWriteJob).where(VectorWriteJob.source_config_id == "source-a"))
            ).scalars().all()
    finally:
        await engine.dispose()

    assert len(rows) == 1
    job = rows[0]
    assert job.status == "queued"
    assert job.record_count == 3
    assert job.next_run_at is None
    assert job.payload["event_ids"] == ["event-1", "event-2", "event-3"]
    assert job.payload["tail_merged_count"] == 1
    assert job.payload["enable_entity_vector_sync"] is False
    assert job.payload["enable_event_entity_vector_sync"] is False


@pytest.mark.asyncio
async def test_enqueue_source_chunk_vector_sync_deduplicates_active_job(tmp_path, monkeypatch):
    db_path = tmp_path / "source-chunk-vector.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def count_source_chunks(source_id, source_type):
        assert (source_id, source_type) == ("article-1", "ARTICLE")
        return "source-config", 3

    monkeypatch.setattr(
        "sag_api.sag.vector_write_queue._count_source_chunks",
        count_source_chunks,
    )
    try:
        await _create_vector_tables(engine)

        await enqueue_source_chunk_vector_sync(session_factory, "article-1", "ARTICLE")
        await enqueue_source_chunk_vector_sync(session_factory, "article-1", "ARTICLE")

        async with session_factory() as session:
            rows = (
                await session.execute(select(VectorWriteJob).where(VectorWriteJob.kind == "source_chunk_sync"))
            ).scalars().all()
    finally:
        await engine.dispose()

    assert len(rows) == 1
    job = rows[0]
    assert job.status == "queued"
    assert job.source_config_id == "source-config"
    assert job.record_count == 3
    assert job.payload["source_id"] == "article-1"
    assert job.payload["source_type"] == "ARTICLE"


@pytest.mark.asyncio
async def test_mark_failed_or_retry_splits_retryable_multi_event_batch(tmp_path):
    db_path = tmp_path / "vector-write-test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _create_vector_tables(engine)

        async with session_factory() as session:
            session.add(
                VectorWriteJob(
                    id="job-parent",
                    kind="event_sync",
                    status="running",
                    source_config_id="source-a",
                    payload={"event_ids": ["event-1", "event-2", "event-3"]},
                    attempts=1,
                    max_attempts=8,
                    embedding_version="default",
                    parent_batch_id="batch-parent",
                    record_count=3,
                )
            )
            await session.commit()

        queue = VectorWriteQueue(session_factory, None)  # type: ignore[arg-type]
        await queue._mark_failed_or_retry("job-parent", RuntimeError("HTTP 503 service unavailable"))

        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(VectorWriteJob).where(VectorWriteJob.source_config_id == "source-a")
                )
            ).scalars().all()
    finally:
        await engine.dispose()

    assert len(rows) == 3
    parent = next(row for row in rows if row.id == "job-parent")
    children = [row for row in rows if row.id != "job-parent"]

    assert parent.status == "succeeded"
    assert parent.superseded_by is not None
    assert parent.error is None
    assert parent.payload["split_reason"] == "retryable_batch_split"
    assert parent.payload["split_into_jobs"] == 2

    assert {child.status for child in children} == {"retry"}
    assert {child.embedding_version for child in children} == {"default"}
    assert {child.parent_batch_id for child in children} == {"batch-parent"}
    assert {child.payload["split_from_job_id"] for child in children} == {"job-parent"}
    assert sorted(child.record_count for child in children) == [1, 2]


@pytest.mark.asyncio
async def test_mark_failed_or_retry_moves_single_retryable_job_to_retry_status(tmp_path):
    db_path = tmp_path / "vector-write-single-retry.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _create_vector_tables(engine)

        async with session_factory() as session:
            session.add(
                VectorWriteJob(
                    id="job-retry",
                    kind="event_sync",
                    status="writing",
                    source_config_id="source-a",
                    payload={"event_ids": ["event-1"]},
                    attempts=1,
                    max_attempts=8,
                    embedding_version="default",
                    record_count=1,
                )
            )
            await session.commit()

        queue = VectorWriteQueue(session_factory, None)  # type: ignore[arg-type]
        await queue._mark_failed_or_retry("job-retry", RuntimeError("database is locked"))

        async with session_factory() as session:
            row = await session.get(VectorWriteJob, "job-retry")
    finally:
        await engine.dispose()

    assert row is not None
    assert row.status == "retry"
    assert row.next_run_at is not None
    assert "后重试" in (row.error or "")
