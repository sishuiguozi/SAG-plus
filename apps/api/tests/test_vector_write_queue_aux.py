"""SAG-OPT-106: auxiliary vector queue (entity_vectors / event_entity_vectors).

Covers the split of ``EventSaver._sync_to_vector_store`` into a P0 event job
plus P1 auxiliary jobs, the aux enqueue/batch/tail-merge/dedup behavior and
the P0/P1 priority in ``_next_due_job_id``.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sag_api.db.models.vector_write import VectorWriteItem, VectorWriteJob
from sag_api.sag.vector_write_queue import (
    VectorWriteQueue,
    enqueue_entity_vector_sync,
    enqueue_event_entity_vector_sync,
    enqueue_event_vector_sync,
    install_event_vector_queue_patch,
    _extract_assoc_ids,
    _extract_entity_ids,
    _merge_tail_payload,
)


async def _create_vector_tables(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(VectorWriteJob.__table__.create)
        await conn.run_sync(VectorWriteItem.__table__.create)


def _event(event_id, assocs):
    return SimpleNamespace(
        id=event_id,
        event_associations=[
            SimpleNamespace(id=assoc_id, entity_id=entity_id)
            for assoc_id, entity_id in assocs
        ],
    )


def _config(entity=True, event_entity=True):
    return SimpleNamespace(
        source_config_id="source-a",
        chunk_ids=["chunk-a"],
        embedding_batch_size=10,
        index_batch_size=50,
        embedding_max_length=500,
        enable_entity_vector_sync=entity,
        enable_event_entity_vector_sync=event_entity,
    )


def _queue(session_factory):
    class FakeEngineManager:
        async def provision(self, source_config_id, source):
            pass

    return VectorWriteQueue(session_factory, FakeEngineManager())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# extractor helpers
# ---------------------------------------------------------------------------


def test_extract_entity_ids_dedup_across_events():
    events = [
        _event("event-1", [("assoc-1", "entity-1"), ("assoc-2", "entity-2")]),
        _event("event-2", [("assoc-3", "entity-1")]),
    ]

    assert _extract_entity_ids(events) == ["entity-1", "entity-2"]


def test_extract_assoc_ids_dedup_across_events():
    events = [
        _event("event-1", [("assoc-1", "entity-1"), ("assoc-2", "entity-2")]),
        _event("event-2", [("assoc-1", "entity-1")]),
    ]

    assert _extract_assoc_ids(events) == ["assoc-1", "assoc-2"]


def test_merge_tail_payload_aux_merges_record_key_and_event_ids():
    merged, leftover = _merge_tail_payload(
        {
            "event_ids": ["event-1"],
            "entity_ids": ["entity-1", "entity-2"],
            "chunk_ids": ["chunk-a"],
            "tail_merged_count": 1,
        },
        {
            "event_ids": ["event-2"],
            "entity_ids": ["entity-3", "entity-4"],
            "chunk_ids": ["chunk-b"],
        },
        ["entity-3", "entity-4"],
        batch_size=3,
        record_id_key="entity_ids",
    )

    assert merged["entity_ids"] == ["entity-1", "entity-2", "entity-3"]
    assert merged["event_ids"] == ["event-1", "event-2"]
    assert merged["chunk_ids"] == ["chunk-a", "chunk-b"]
    assert merged["tail_flush_pending"] is False
    assert leftover == ["entity-4"]


# ---------------------------------------------------------------------------
# aux enqueue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_entity_vector_sync_creates_job_and_items(tmp_path, monkeypatch):
    db_path = tmp_path / "aux-entity.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.aux_vector_deferred_enabled", False)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.vector_write_job_batch_size", 200)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.vector_write_tail_flush_seconds", 1.5)
    try:
        await _create_vector_tables(engine)
        events = [
            _event("event-1", [("assoc-1", "entity-1"), ("assoc-2", "entity-2")]),
            _event("event-2", [("assoc-3", "entity-1")]),
        ]
        await enqueue_entity_vector_sync(session_factory, events, _config())

        async with session_factory() as session:
            jobs = (
                (await session.execute(select(VectorWriteJob).where(VectorWriteJob.kind == "entity_sync")))
                .scalars()
                .all()
            )
            items = (
                await session.execute(
                    select(VectorWriteItem).where(VectorWriteItem.table_name == "entity_vectors")
                )
            ).scalars().all()
    finally:
        await engine.dispose()

    assert len(jobs) == 1
    job = jobs[0]
    assert job.status == "queued"
    assert job.source_config_id == "source-a"
    assert job.record_count == 2
    assert job.payload["entity_ids"] == ["entity-1", "entity-2"]
    assert job.payload["event_ids"] == ["event-1", "event-2"]
    assert {item.record_id for item in items} == {"entity-1", "entity-2"}
    assert {item.status for item in items} == {"queued"}
    assert {item.job_id for item in items} == {job.id}


@pytest.mark.asyncio
async def test_enqueue_event_entity_vector_sync_creates_job_and_items(tmp_path, monkeypatch):
    db_path = tmp_path / "aux-ee.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.aux_vector_deferred_enabled", False)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.vector_write_job_batch_size", 200)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.vector_write_tail_flush_seconds", 1.5)
    try:
        await _create_vector_tables(engine)
        events = [
            _event("event-1", [("assoc-1", "entity-1"), ("assoc-2", "entity-2")]),
            _event("event-2", [("assoc-3", "entity-1")]),
        ]
        await enqueue_event_entity_vector_sync(session_factory, events, _config())

        async with session_factory() as session:
            jobs = (
                (
                    await session.execute(
                        select(VectorWriteJob).where(VectorWriteJob.kind == "event_entity_sync")
                    )
                )
                .scalars()
                .all()
            )
            items = (
                await session.execute(
                    select(VectorWriteItem).where(VectorWriteItem.table_name == "event_entity_vectors")
                )
            ).scalars().all()
    finally:
        await engine.dispose()

    assert len(jobs) == 1
    job = jobs[0]
    assert job.payload["assoc_ids"] == ["assoc-1", "assoc-2", "assoc-3"]
    assert {item.record_id for item in items} == {"assoc-1", "assoc-2", "assoc-3"}


@pytest.mark.asyncio
async def test_enqueue_aux_vector_sync_skips_when_deferred(tmp_path, monkeypatch):
    db_path = tmp_path / "aux-deferred.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.aux_vector_deferred_enabled", True)
    try:
        await _create_vector_tables(engine)
        await enqueue_entity_vector_sync(
            session_factory, [_event("event-1", [("assoc-1", "entity-1")])], _config()
        )
        await enqueue_event_entity_vector_sync(
            session_factory, [_event("event-1", [("assoc-1", "entity-1")])], _config()
        )

        async with session_factory() as session:
            jobs = (await session.execute(select(VectorWriteJob))).scalars().all()
    finally:
        await engine.dispose()

    assert jobs == []


@pytest.mark.asyncio
async def test_enqueue_aux_vector_sync_skips_when_config_flag_false(tmp_path, monkeypatch):
    db_path = tmp_path / "aux-config-false.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.aux_vector_deferred_enabled", False)
    try:
        await _create_vector_tables(engine)
        await enqueue_entity_vector_sync(
            session_factory, [_event("event-1", [("assoc-1", "entity-1")])], _config(entity=False)
        )
        await enqueue_event_entity_vector_sync(
            session_factory, [_event("event-1", [("assoc-1", "entity-1")])], _config(event_entity=False)
        )

        async with session_factory() as session:
            jobs = (await session.execute(select(VectorWriteJob))).scalars().all()
    finally:
        await engine.dispose()

    assert jobs == []


@pytest.mark.asyncio
async def test_enqueue_aux_vector_sync_deduplicates_existing_records(tmp_path, monkeypatch):
    db_path = tmp_path / "aux-dedup.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.aux_vector_deferred_enabled", False)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.vector_write_job_batch_size", 200)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.vector_write_tail_flush_seconds", 0.0)
    try:
        await _create_vector_tables(engine)
        await enqueue_entity_vector_sync(
            session_factory, [_event("event-1", [("assoc-1", "entity-1")])], _config()
        )
        # Same entity referenced by a brand new event must not be re-enqueued.
        await enqueue_entity_vector_sync(
            session_factory, [_event("event-2", [("assoc-2", "entity-1")])], _config()
        )

        async with session_factory() as session:
            jobs = (
                (await session.execute(select(VectorWriteJob).where(VectorWriteJob.kind == "entity_sync")))
                .scalars()
                .all()
            )
            items = (
                await session.execute(
                    select(VectorWriteItem).where(VectorWriteItem.table_name == "entity_vectors")
                )
            ).scalars().all()
    finally:
        await engine.dispose()

    assert len(jobs) == 1
    assert jobs[0].payload["entity_ids"] == ["entity-1"]
    assert len(items) == 1


@pytest.mark.asyncio
async def test_enqueue_aux_vector_sync_merges_tail_batch(tmp_path, monkeypatch):
    db_path = tmp_path / "aux-tail.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.aux_vector_deferred_enabled", False)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.vector_write_job_batch_size", 3)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.vector_write_tail_flush_seconds", 1.5)
    try:
        await _create_vector_tables(engine)
        await enqueue_entity_vector_sync(
            session_factory,
            [_event("event-1", [("assoc-1", "entity-1"), ("assoc-2", "entity-2")])],
            _config(),
        )
        await enqueue_entity_vector_sync(
            session_factory,
            [_event("event-2", [("assoc-3", "entity-3")])],
            _config(),
        )

        async with session_factory() as session:
            jobs = (
                (await session.execute(select(VectorWriteJob).where(VectorWriteJob.kind == "entity_sync")))
                .scalars()
                .all()
            )
            items = (
                await session.execute(
                    select(VectorWriteItem).where(VectorWriteItem.table_name == "entity_vectors")
                )
            ).scalars().all()
    finally:
        await engine.dispose()

    assert len(jobs) == 1
    job = jobs[0]
    assert job.record_count == 3
    assert job.next_run_at is None
    assert job.payload["entity_ids"] == ["entity-1", "entity-2", "entity-3"]
    assert job.payload["event_ids"] == ["event-1", "event-2"]
    assert job.payload["tail_merged_count"] == 1
    assert len(items) == 3


@pytest.mark.asyncio
async def test_enqueue_aux_vector_sync_batches_over_limit(tmp_path, monkeypatch):
    db_path = tmp_path / "aux-batch.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.aux_vector_deferred_enabled", False)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.vector_write_job_batch_size", 2)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.vector_write_tail_flush_seconds", 1.5)
    try:
        await _create_vector_tables(engine)
        events = [
            _event(
                "event-1",
                [
                    ("assoc-1", "entity-1"),
                    ("assoc-2", "entity-2"),
                    ("assoc-3", "entity-3"),
                ],
            )
        ]
        await enqueue_entity_vector_sync(session_factory, events, _config())

        async with session_factory() as session:
            jobs = (
                (await session.execute(select(VectorWriteJob).where(VectorWriteJob.kind == "entity_sync")))
                .scalars()
                .all()
            )
            items = (
                await session.execute(
                    select(VectorWriteItem).where(VectorWriteItem.table_name == "entity_vectors")
                )
            ).scalars().all()
    finally:
        await engine.dispose()

    assert len(jobs) == 2
    assert sorted(job.record_count for job in jobs) == [1, 2]
    assert {job.payload["batch_index"] for job in jobs} == {0, 1}
    assert len(items) == 3


# ---------------------------------------------------------------------------
# event job now defers aux to the aux queue even when not deferred
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_event_vector_sync_never_writes_aux_inline(tmp_path, monkeypatch):
    db_path = tmp_path / "event-aux-off.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.vector_write_job_batch_size", 200)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.vector_write_tail_flush_seconds", 0.0)
    monkeypatch.setattr("sag_api.sag.vector_write_queue.settings.aux_vector_deferred_enabled", False)
    try:
        await _create_vector_tables(engine)
        await enqueue_event_vector_sync(
            session_factory, [_event("event-1", [("assoc-1", "entity-1")])], _config()
        )

        async with session_factory() as session:
            job = await session.scalar(select(VectorWriteJob).where(VectorWriteJob.kind == "event_sync"))
    finally:
        await engine.dispose()

    assert job is not None
    assert job.payload["enable_entity_vector_sync"] is False
    assert job.payload["enable_event_entity_vector_sync"] is False


# ---------------------------------------------------------------------------
# priority + dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_next_due_job_prefers_p0_event_over_p1_entity(tmp_path):
    db_path = tmp_path / "priority.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _create_vector_tables(engine)
        async with session_factory() as session:
            session.add(
                VectorWriteJob(
                    id="job-p1-entity",
                    kind="entity_sync",
                    status="queued",
                    source_config_id="source-a",
                    payload={"event_ids": ["event-1"], "entity_ids": ["entity-1"]},
                    embedding_version="default",
                    record_count=1,
                )
            )
            await session.commit()
        async with session_factory() as session:
            session.add(
                VectorWriteJob(
                    id="job-p0-event",
                    kind="event_sync",
                    status="queued",
                    source_config_id="source-a",
                    payload={"event_ids": ["event-2"]},
                    embedding_version="default",
                    record_count=1,
                )
            )
            await session.commit()

        queue = _queue(session_factory)
        assert await queue._next_due_job_id() == "job-p0-event"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_next_due_job_prefers_p0_source_chunk_over_p1(tmp_path):
    db_path = tmp_path / "priority-chunk.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _create_vector_tables(engine)
        async with session_factory() as session:
            session.add(
                VectorWriteJob(
                    id="job-p1-ee",
                    kind="event_entity_sync",
                    status="queued",
                    source_config_id="source-a",
                    payload={"event_ids": ["event-1"], "assoc_ids": ["assoc-1"]},
                    embedding_version="default",
                    record_count=1,
                )
            )
            session.add(
                VectorWriteJob(
                    id="job-p0-chunk",
                    kind="source_chunk_sync",
                    status="queued",
                    source_config_id="source-a",
                    payload={"source_id": "article-1", "source_type": "ARTICLE"},
                    embedding_version="default",
                    record_count=1,
                )
            )
            await session.commit()

        queue = _queue(session_factory)
        assert await queue._next_due_job_id() == "job-p0-chunk"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_process_dispatches_aux_kinds(tmp_path):
    db_path = tmp_path / "dispatch.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _create_vector_tables(engine)
        async with session_factory() as session:
            session.add(
                VectorWriteJob(
                    id="job-entity",
                    kind="entity_sync",
                    status="writing",
                    source_config_id="source-a",
                    payload={"event_ids": ["event-1"], "entity_ids": ["entity-1"]},
                    embedding_version="default",
                    record_count=1,
                )
            )
            session.add(
                VectorWriteJob(
                    id="job-ee",
                    kind="event_entity_sync",
                    status="writing",
                    source_config_id="source-a",
                    payload={"event_ids": ["event-1"], "assoc_ids": ["assoc-1"]},
                    embedding_version="default",
                    record_count=1,
                )
            )
            session.add(
                VectorWriteJob(
                    id="job-unknown",
                    kind="mystery_sync",
                    status="writing",
                    source_config_id="source-a",
                    payload={},
                    embedding_version="default",
                    record_count=0,
                )
            )
            await session.commit()

        calls = []
        queue = _queue(session_factory)

        async def fake_entities(job_id):
            calls.append(("entity_sync", job_id))

        async def fake_event_entities(job_id):
            calls.append(("event_entity_sync", job_id))

        queue._process_entities = fake_entities  # type: ignore[method-assign]
        queue._process_event_entities = fake_event_entities  # type: ignore[method-assign]

        await queue._process("job-entity")
        await queue._process("job-ee")
        with pytest.raises(RuntimeError, match="未知向量写入任务类型"):
            await queue._process("job-unknown")

        assert calls == [
            ("entity_sync", "job-entity"),
            ("event_entity_sync", "job-ee"),
        ]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mark_failed_or_retry_retries_aux_job_as_whole(tmp_path):
    db_path = tmp_path / "aux-retry.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _create_vector_tables(engine)
        async with session_factory() as session:
            session.add(
                VectorWriteJob(
                    id="job-aux",
                    kind="entity_sync",
                    status="writing",
                    source_config_id="source-a",
                    payload={
                        "event_ids": ["event-1", "event-2"],
                        "entity_ids": ["entity-1", "entity-2", "entity-3"],
                    },
                    attempts=1,
                    max_attempts=8,
                    embedding_version="default",
                    record_count=3,
                )
            )
            await session.commit()

        queue = _queue(session_factory)
        await queue._mark_failed_or_retry("job-aux", RuntimeError("HTTP 503 service unavailable"))

        async with session_factory() as session:
            rows = (await session.execute(select(VectorWriteJob))).scalars().all()
            row = await session.get(VectorWriteJob, "job-aux")
    finally:
        await engine.dispose()

    assert len(rows) == 1
    assert row is not None
    assert row.status == "retry"
    assert row.next_run_at is not None
    assert row.payload["entity_ids"] == ["entity-1", "entity-2", "entity-3"]
    assert "后重试" in (row.error or "")


# ---------------------------------------------------------------------------
# patch split behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_event_vector_queue_patch_splits_aux_enqueue(tmp_path, monkeypatch):
    from zleap.sag.modules.extract.saver import EventSaver

    db_path = tmp_path / "patch.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    original = EventSaver._sync_to_vector_store
    calls = []

    async def fake_event(session_factory_, events, config):
        calls.append(("event", len(events)))

    async def fake_entity(session_factory_, events, config):
        calls.append(("entity", len(events)))

    async def fake_event_entity(session_factory_, events, config):
        calls.append(("event_entity", len(events)))

    try:
        monkeypatch.setattr("sag_api.sag.vector_write_queue._PATCHED", False)
        if hasattr(EventSaver, "_sag_api_original_sync_to_vector_store"):
            delattr(EventSaver, "_sag_api_original_sync_to_vector_store")
        monkeypatch.setattr("sag_api.sag.vector_write_queue.enqueue_event_vector_sync", fake_event)
        monkeypatch.setattr("sag_api.sag.vector_write_queue.enqueue_entity_vector_sync", fake_entity)
        monkeypatch.setattr(
            "sag_api.sag.vector_write_queue.enqueue_event_entity_vector_sync",
            fake_event_entity,
        )

        install_event_vector_queue_patch(session_factory)
        # The replacement ignores self; pass a bare object.
        await EventSaver._sync_to_vector_store(object(), [_event("event-1", [("assoc-1", "entity-1")])], _config())
        assert calls == [("event", 1), ("entity", 1), ("event_entity", 1)]
    finally:
        EventSaver._sync_to_vector_store = original
        if hasattr(EventSaver, "_sag_api_original_sync_to_vector_store"):
            delattr(EventSaver, "_sag_api_original_sync_to_vector_store")
        monkeypatch.setattr("sag_api.sag.vector_write_queue._PATCHED", False)
        await engine.dispose()
