"""SAG-OPT-107: 辅助向量索引补齐状态信号测试。"""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sag_api.db.models.vector_write import VectorWriteItem, VectorWriteJob
from sag_api.sag.vector_write_items import (
    aggregate_aux_index_status,
    aux_index_backfill_status,
)
from sag_api.schemas.search import AuxIndexOut, SearchResponse
from sag_api.schemas.source import SourceOut, VectorBackfillOut


async def _create_tables(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(VectorWriteJob.__table__.create)
        await conn.run_sync(VectorWriteItem.__table__.create)


async def _make_session(tmp_path, name="status.db"):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    await _create_tables(engine)
    return engine, session_factory


async def _add_item(
    session,
    *,
    item_id,
    table_name,
    record_id,
    source_config_id="sc-a",
    status="queued",
    job_id="job-a",
):
    session.add(
        VectorWriteItem(
            id=item_id,
            job_id=job_id,
            table_name=table_name,
            record_id=record_id,
            embedding_version="default",
            source_config_id=source_config_id,
            status=status,
        )
    )


async def test_aux_status_complete_when_no_active_items(tmp_path):
    engine, session_factory = await _make_session(tmp_path)
    try:
        async with session_factory() as session:
            result = await aux_index_backfill_status(
                session, ["sc-a"], aux_vector_deferred_enabled=False
            )
            assert result["sc-a"]["status"] == "complete"
            assert result["sc-a"]["pending_records"] == 0
            assert result["sc-a"]["by_table"] == {
                "entity_vectors": 0,
                "event_entity_vectors": 0,
            }
    finally:
        await engine.dispose()


async def test_aux_status_backfilling_with_active_items(tmp_path):
    engine, session_factory = await _make_session(tmp_path)
    try:
        async with session_factory() as session:
            await _add_item(
                session, item_id="i1", table_name="entity_vectors", record_id="ent-1"
            )
            await _add_item(
                session,
                item_id="i2",
                table_name="event_entity_vectors",
                record_id="assoc-1",
                status="retry",
            )
            await _add_item(
                session,
                item_id="i3",
                table_name="event_entity_vectors",
                record_id="assoc-2",
                status="writing",
                source_config_id="sc-b",
            )
            await session.commit()
        async with session_factory() as session:
            result = await aux_index_backfill_status(
                session,
                ["sc-a", "sc-b"],
                aux_vector_deferred_enabled=False,
            )
            assert result["sc-a"]["status"] == "backfilling"
            assert result["sc-a"]["pending_records"] == 2
            assert result["sc-a"]["by_table"] == {
                "entity_vectors": 1,
                "event_entity_vectors": 1,
            }
            assert result["sc-b"]["status"] == "backfilling"
            assert result["sc-b"]["pending_records"] == 1
    finally:
        await engine.dispose()


async def test_aux_status_ignores_terminal_and_core_items(tmp_path):
    engine, session_factory = await _make_session(tmp_path)
    try:
        async with session_factory() as session:
            await _add_item(
                session, item_id="i1", table_name="entity_vectors", record_id="ent-1", status="succeeded"
            )
            await _add_item(
                session, item_id="i2", table_name="event_vectors", record_id="ev-1"
            )
            await _add_item(
                session, item_id="i3", table_name="source_chunks", record_id="chunk-1"
            )
            await session.commit()
        async with session_factory() as session:
            result = await aux_index_backfill_status(
                session, ["sc-a"], aux_vector_deferred_enabled=False
            )
            # 核心表（event_vectors / source_chunks）与终态明细不计入辅助补齐信号
            assert result["sc-a"]["status"] == "complete"
            assert result["sc-a"]["pending_records"] == 0
    finally:
        await engine.dispose()


async def test_aux_status_deferred_when_flag_on(tmp_path):
    engine, session_factory = await _make_session(tmp_path)
    try:
        async with session_factory() as session:
            result = await aux_index_backfill_status(
                session, ["sc-a"], aux_vector_deferred_enabled=True
            )
            assert result["sc-a"]["status"] == "deferred"
    finally:
        await engine.dispose()


async def test_aux_status_deferred_flag_keeps_backfilling_signal(tmp_path):
    engine, session_factory = await _make_session(tmp_path)
    try:
        async with session_factory() as session:
            await _add_item(
                session, item_id="i1", table_name="entity_vectors", record_id="ent-1"
            )
            await session.commit()
        async with session_factory() as session:
            result = await aux_index_backfill_status(
                session, ["sc-a"], aux_vector_deferred_enabled=True
            )
            # 存量未完成任务优先于 deferred 状态
            assert result["sc-a"]["status"] == "backfilling"
            assert result["sc-a"]["pending_records"] == 1
    finally:
        await engine.dispose()


async def test_aux_status_unknown_for_missing_config(tmp_path):
    engine, session_factory = await _make_session(tmp_path)
    try:
        async with session_factory() as session:
            result = await aux_index_backfill_status(
                session, ["", None], aux_vector_deferred_enabled=False
            )
            assert result == {}
            result = await aux_index_backfill_status(
                session, ["sc-a"], aux_vector_deferred_enabled=False
            )
            assert result["sc-a"]["status"] == "complete"
    finally:
        await engine.dispose()


def test_aggregate_aux_index_status_merges_sources():
    per_source = {
        "sc-a": {
            "source_config_id": "sc-a",
            "status": "backfilling",
            "pending_records": 3,
            "by_table": {"entity_vectors": 2, "event_entity_vectors": 1},
        },
        "sc-b": {
            "source_config_id": "sc-b",
            "status": "complete",
            "pending_records": 0,
            "by_table": {"entity_vectors": 0, "event_entity_vectors": 0},
        },
    }
    merged = aggregate_aux_index_status(per_source)
    assert merged["status"] == "backfilling"
    assert merged["pending_records"] == 3
    assert merged["by_table"] == {"entity_vectors": 2, "event_entity_vectors": 1}


def test_aggregate_aux_index_status_all_deferred_and_empty():
    per_source = {
        "sc-a": {
            "source_config_id": "sc-a",
            "status": "deferred",
            "pending_records": 0,
            "by_table": {"entity_vectors": 0, "event_entity_vectors": 0},
        }
    }
    assert aggregate_aux_index_status(per_source)["status"] == "deferred"
    assert aggregate_aux_index_status({})["status"] == "unknown"
    assert aggregate_aux_index_status({})["sources"] == 0


def test_source_out_schema_has_vector_backfill_default():
    schema = SourceOut.model_fields["vector_backfill"]
    assert schema.default is not None
    sample = VectorBackfillOut()
    assert sample.status == "unknown"
    assert sample.pending_records == 0
    assert sample.by_table == {}


def test_search_response_schema_has_aux_index_default():
    schema = SearchResponse.model_fields["aux_index"]
    assert schema.default is not None
    sample = AuxIndexOut()
    assert sample.status == "unknown"
    assert sample.sources == 0
