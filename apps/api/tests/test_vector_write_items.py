"""Queue V2 record-level detail model tests (SAG-OPT-102)."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sag_api.db.models.vector_write import VectorWriteItem, VectorWriteJob
from sag_api.sag.vector_write_items import (
    ITEM_ACTIVE_STATUSES,
    ITEM_STATUSES,
    active_record_ids,
    claim_job_items,
    complete_job_items,
    item_status_for_job,
    reassign_job_items,
    register_job_items,
)
from sag_api.sag.vector_write_queue import (
    enqueue_event_vector_sync,
    VectorWriteQueue,
)


async def _create_tables(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(VectorWriteJob.__table__.create)
        await conn.run_sync(VectorWriteItem.__table__.create)


def test_item_status_constants_cover_v2_states():
    assert ITEM_STATUSES == (
        "queued",
        "embedding",
        "ready_to_write",
        "writing",
        "succeeded",
        "retry",
        "failed",
    )
    assert ITEM_ACTIVE_STATUSES == (
        "queued",
        "embedding",
        "ready_to_write",
        "writing",
        "retry",
    )


def test_item_status_for_job_maps_all_job_states():
    assert item_status_for_job("queued") == "queued"
    assert item_status_for_job("retry") == "retry"
    assert item_status_for_job("writing") == "writing"
    assert item_status_for_job("running") == "writing"
    assert item_status_for_job("succeeded") == "succeeded"
    assert item_status_for_job("failed") == "failed"
    assert item_status_for_job("unknown") == "queued"


@pytest.mark.asyncio
async def test_active_unique_constraint_allows_single_active_item_per_record(tmp_path):
    db_path = tmp_path / "items-unique.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _create_tables(engine)
        async with session_factory() as session:
            session.add(
                VectorWriteItem(
                    id="item-1",
                    job_id="job-1",
                    table_name="event_vectors",
                    record_id="event-1",
                    embedding_version="default",
                    source_config_id="source-a",
                    status="queued",
                )
            )
            await session.commit()

        async with session_factory() as session:
            session.add(
                VectorWriteItem(
                    id="item-2",
                    job_id="job-2",
                    table_name="event_vectors",
                    record_id="event-1",
                    embedding_version="default",
                    source_config_id="source-a",
                    status="queued",
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

        # Terminal rows free the unique key: a new active item is allowed again.
        async with session_factory() as session:
            row = await session.get(VectorWriteItem, "item-1")
            row.status = "succeeded"
            await session.commit()

        async with session_factory() as session:
            session.add(
                VectorWriteItem(
                    id="item-3",
                    job_id="job-2",
                    table_name="event_vectors",
                    record_id="event-1",
                    embedding_version="default",
                    source_config_id="source-a",
                    status="queued",
                )
            )
            await session.commit()
            rows = (await session.execute(select(VectorWriteItem))).scalars().all()
    finally:
        await engine.dispose()

    assert {row.id for row in rows} == {"item-1", "item-3"}
    assert {row.status for row in rows} == {"succeeded", "queued"}


@pytest.mark.asyncio
async def test_register_job_items_deduplicates_active_records(tmp_path):
    db_path = tmp_path / "items-register.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _create_tables(engine)
        async with session_factory() as session:
            created, skipped = await register_job_items(
                session,
                job_id="job-1",
                table_name="event_vectors",
                source_config_id="source-a",
                record_ids=["event-1", "event-2", "event-1"],
                embedding_version="default",
            )
            await session.commit()
            assert created == 2
            assert skipped == 0

        # Same job, same records: everything is skipped.
        async with session_factory() as session:
            created, skipped = await register_job_items(
                session,
                job_id="job-1",
                table_name="event_vectors",
                source_config_id="source-a",
                record_ids=["event-1", "event-2", "event-3"],
                embedding_version="default",
            )
            await session.commit()
            assert created == 1
            assert skipped == 2

        # Different table keeps records independent.
        async with session_factory() as session:
            created, skipped = await register_job_items(
                session,
                job_id="job-1",
                table_name="entity_vectors",
                source_config_id="source-a",
                record_ids=["event-1"],
                embedding_version="default",
            )
            await session.commit()
            assert created == 1
            assert skipped == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_register_job_items_deduplicates_across_jobs(tmp_path):
    db_path = tmp_path / "items-cross-job.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _create_tables(engine)
        async with session_factory() as session:
            await register_job_items(
                session,
                job_id="job-a",
                table_name="event_vectors",
                source_config_id="source-a",
                record_ids=["event-1"],
                embedding_version="default",
            )
            created, skipped = await register_job_items(
                session,
                job_id="job-b",
                table_name="event_vectors",
                source_config_id="source-b",
                record_ids=["event-1", "event-9"],
                embedding_version="default",
            )
            await session.commit()
            assert created == 1
            assert skipped == 1

        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(VectorWriteItem).where(VectorWriteItem.record_id == "event-1")
                )
            ).scalars().all()
    finally:
        await engine.dispose()

    assert len(rows) == 1
    assert rows[0].job_id == "job-a"


@pytest.mark.asyncio
async def test_claim_job_items_claims_only_pending(tmp_path):
    db_path = tmp_path / "items-claim.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _create_tables(engine)
        async with session_factory() as session:
            session.add(
                VectorWriteItem(
                    id="item-q",
                    job_id="job-1",
                    table_name="event_vectors",
                    record_id="event-1",
                    source_config_id="source-a",
                    status="queued",
                )
            )
            session.add(
                VectorWriteItem(
                    id="item-s",
                    job_id="job-1",
                    table_name="event_vectors",
                    record_id="event-2",
                    source_config_id="source-a",
                    status="succeeded",
                )
            )
            await session.commit()

        expires = datetime(2026, 7, 31, 12, 15, tzinfo=UTC)
        async with session_factory() as session:
            claimed = await claim_job_items(
                session,
                job_id="job-1",
                lease_owner="worker-1",
                lease_expires_at=expires,
            )
            await session.commit()
            assert claimed == 1

        async with session_factory() as session:
            queued_item = await session.get(VectorWriteItem, "item-q")
            done_item = await session.get(VectorWriteItem, "item-s")
    finally:
        await engine.dispose()

    assert queued_item.status == "writing"
    assert queued_item.lease_owner == "worker-1"
    assert queued_item.lease_expires_at == expires
    assert done_item.status == "succeeded"
    assert done_item.lease_owner is None


@pytest.mark.asyncio
async def test_complete_job_items_transitions_only_active_items(tmp_path):
    db_path = tmp_path / "items-complete.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _create_tables(engine)
        async with session_factory() as session:
            session.add(
                VectorWriteItem(
                    id="item-w",
                    job_id="job-1",
                    table_name="event_vectors",
                    record_id="event-1",
                    source_config_id="source-a",
                    status="writing",
                )
            )
            session.add(
                VectorWriteItem(
                    id="item-done",
                    job_id="job-1",
                    table_name="event_vectors",
                    record_id="event-2",
                    source_config_id="source-a",
                    status="succeeded",
                )
            )
            await session.commit()

        async with session_factory() as session:
            completed = await complete_job_items(
                session,
                job_id="job-1",
                status="succeeded",
            )
            await session.commit()
            assert completed == 1

        async with session_factory() as session:
            active = await active_record_ids(
                session,
                table_name="event_vectors",
                record_ids=["event-1", "event-2"],
                embedding_version="default",
            )
            item_w = await session.get(VectorWriteItem, "item-w")
    finally:
        await engine.dispose()

    assert active == set()
    assert item_w.status == "succeeded"


@pytest.mark.asyncio
async def test_complete_job_items_records_error_and_retry_due(tmp_path):
    db_path = tmp_path / "items-complete-retry.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _create_tables(engine)
        async with session_factory() as session:
            session.add(
                VectorWriteItem(
                    id="item-r",
                    job_id="job-1",
                    table_name="event_vectors",
                    record_id="event-1",
                    source_config_id="source-a",
                    status="writing",
                    lease_owner="worker-1",
                    lease_expires_at=datetime(2026, 7, 31, 12, 15, tzinfo=UTC),
                )
            )
            await session.commit()

        due = datetime(2026, 7, 31, 12, 1, tzinfo=UTC)
        async with session_factory() as session:
            await complete_job_items(
                session,
                job_id="job-1",
                status="retry",
                error="第 1 次失败，10s 后重试：database is locked",
                next_run_at=due,
            )
            await session.commit()

        async with session_factory() as session:
            row = await session.get(VectorWriteItem, "item-r")
    finally:
        await engine.dispose()

    assert row.status == "retry"
    assert row.next_run_at == due
    assert "database is locked" in (row.last_error or "")
    assert row.lease_owner is None
    assert row.lease_expires_at is None


@pytest.mark.asyncio
async def test_reassign_job_items_moves_split_records_to_children(tmp_path):
    db_path = tmp_path / "items-reassign.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _create_tables(engine)
        async with session_factory() as session:
            for index, record_id in enumerate(["event-1", "event-2", "event-3"]):
                session.add(
                    VectorWriteItem(
                        id=f"item-{index}",
                        job_id="job-parent",
                        table_name="event_vectors",
                        record_id=record_id,
                        source_config_id="source-a",
                        status="writing",
                    )
                )
            await session.commit()

        due = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)
        async with session_factory() as session:
            moved = await reassign_job_items(
                session,
                from_job_id="job-parent",
                to_job_id="job-child-1",
                record_ids=["event-1"],
                status="retry",
                next_run_at=due,
            )
            assert moved == 1
            await session.commit()

        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(VectorWriteItem).where(VectorWriteItem.job_id == "job-child-1")
                )
            ).scalars().all()
    finally:
        await engine.dispose()

    assert len(rows) == 1
    assert rows[0].record_id == "event-1"
    assert rows[0].status == "retry"
    assert rows[0].next_run_at == due


@pytest.mark.asyncio
async def test_enqueue_event_vector_sync_registers_record_items(tmp_path, monkeypatch):
    db_path = tmp_path / "items-enqueue.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.vector_write_job_batch_size", 3)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.vector_write_tail_flush_seconds", 1.5)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.aux_vector_deferred_enabled", True)
    try:
        await _create_tables(engine)
        config = SimpleNamespace(
            source_config_id="source-a",
            chunk_ids=["chunk-a"],
            embedding_batch_size=10,
            index_batch_size=50,
            embedding_max_length=500,
            enable_entity_vector_sync=True,
            enable_event_entity_vector_sync=True,
        )
        await enqueue_event_vector_sync(
            session_factory,
            [SimpleNamespace(id="event-1"), SimpleNamespace(id="event-2")],
            config,
        )
        await enqueue_event_vector_sync(
            session_factory,
            [SimpleNamespace(id="event-3")],
            config,
        )

        async with session_factory() as session:
            jobs = (
                (await session.execute(select(VectorWriteJob))).scalars().all()
            )
            items = (
                (await session.execute(select(VectorWriteItem))).scalars().all()
            )
    finally:
        await engine.dispose()

    assert len(jobs) == 1
    assert len(items) == 3
    assert {item.record_id for item in items} == {"event-1", "event-2", "event-3"}
    assert {item.table_name for item in items} == {"event_vectors"}
    assert {item.embedding_version for item in items} == {"default"}
    assert {item.status for item in items} == {"queued"}
    assert {item.job_id for item in items} == {jobs[0].id}


@pytest.mark.asyncio
async def test_mark_succeeded_completes_record_items(tmp_path):
    db_path = tmp_path / "items-succeed.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _create_tables(engine)
        async with session_factory() as session:
            session.add(
                VectorWriteJob(
                    id="job-1",
                    kind="event_sync",
                    status="writing",
                    source_config_id="source-a",
                    payload={"event_ids": ["event-1", "event-2"]},
                    embedding_version="default",
                    record_count=2,
                )
            )
            for record_id in ("event-1", "event-2"):
                session.add(
                    VectorWriteItem(
                        job_id="job-1",
                        table_name="event_vectors",
                        record_id=record_id,
                        source_config_id="source-a",
                        status="writing",
                    )
                )
            await session.commit()

        queue = VectorWriteQueue(session_factory, None)  # type: ignore[arg-type]
        await queue._mark_succeeded("job-1", indexed=2)

        async with session_factory() as session:
            items = (
                (await session.execute(select(VectorWriteItem))).scalars().all()
            )
    finally:
        await engine.dispose()

    assert {item.status for item in items} == {"succeeded"}
    assert all(item.lease_owner is None for item in items)


@pytest.mark.asyncio
async def test_split_reassigns_record_items_to_children(tmp_path):
    db_path = tmp_path / "items-split.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _create_tables(engine)
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
                    record_count=3,
                )
            )
            for record_id in ("event-1", "event-2", "event-3"):
                session.add(
                    VectorWriteItem(
                        job_id="job-parent",
                        table_name="event_vectors",
                        record_id=record_id,
                        source_config_id="source-a",
                        status="writing",
                    )
                )
            await session.commit()

        queue = VectorWriteQueue(session_factory, None)  # type: ignore[arg-type]
        await queue._mark_failed_or_retry("job-parent", RuntimeError("HTTP 503 service unavailable"))

        async with session_factory() as session:
            children = (
                (await session.execute(
                    select(VectorWriteJob).where(VectorWriteJob.status == "retry")
                ))
                .scalars()
                .all()
            )
            items = (await session.execute(select(VectorWriteItem))).scalars().all()
    finally:
        await engine.dispose()

    child_ids = {child.id for child in children}
    assert len(child_ids) == 2
    assert {item.job_id for item in items} == child_ids
    assert {item.status for item in items} == {"retry"}
    by_child = {child.id: set(child.payload["event_ids"]) for child in children}
    for item in items:
        assert item.record_id in by_child[item.job_id]


@pytest.mark.asyncio
async def test_final_failure_marks_record_items_failed(tmp_path):
    db_path = tmp_path / "items-failed.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _create_tables(engine)
        async with session_factory() as session:
            session.add(
                VectorWriteJob(
                    id="job-1",
                    kind="event_sync",
                    status="writing",
                    source_config_id="source-a",
                    payload={"event_ids": ["event-1"]},
                    attempts=8,
                    max_attempts=8,
                    embedding_version="default",
                    record_count=1,
                )
            )
            session.add(
                VectorWriteItem(
                    job_id="job-1",
                    table_name="event_vectors",
                    record_id="event-1",
                    source_config_id="source-a",
                    status="writing",
                )
            )
            await session.commit()

        queue = VectorWriteQueue(session_factory, None)  # type: ignore[arg-type]
        await queue._mark_failed_or_retry("job-1", RuntimeError("HTTP 404 not found"))

        async with session_factory() as session:
            job = await session.get(VectorWriteJob, "job-1")
            items = (await session.execute(select(VectorWriteItem))).scalars().all()
    finally:
        await engine.dispose()

    assert job.status == "failed"
    assert len(items) == 1
    assert items[0].status == "failed"
    assert "HTTP 404 not found" in (items[0].last_error or "")


@pytest.mark.asyncio
async def test_recover_running_resets_record_items(tmp_path):
    db_path = tmp_path / "items-recover.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _create_tables(engine)
        async with session_factory() as session:
            session.add(
                VectorWriteJob(
                    id="job-1",
                    kind="event_sync",
                    status="running",
                    source_config_id="source-a",
                    payload={"event_ids": ["event-1"]},
                    embedding_version="default",
                    record_count=1,
                )
            )
            session.add(
                VectorWriteItem(
                    job_id="job-1",
                    table_name="event_vectors",
                    record_id="event-1",
                    source_config_id="source-a",
                    status="writing",
                    lease_owner="dead-worker",
                    lease_expires_at=datetime(2026, 7, 31, 12, 15, tzinfo=UTC),
                )
            )
            await session.commit()

        queue = VectorWriteQueue(session_factory, None)  # type: ignore[arg-type]
        await queue._recover_running()

        async with session_factory() as session:
            job = await session.get(VectorWriteJob, "job-1")
            items = (await session.execute(select(VectorWriteItem))).scalars().all()
    finally:
        await engine.dispose()

    assert job.status == "retry"
    assert job.next_run_at is not None
    assert len(items) == 1
    assert items[0].status == "retry"
    assert items[0].next_run_at is not None
    assert items[0].lease_owner is None
    assert "Recovered unfinished" in (items[0].last_error or "")
