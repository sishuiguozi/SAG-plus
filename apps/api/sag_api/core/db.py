from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from sag_api.core.config import settings
from sag_api.db.base import Base


def _ensure_sqlite_dir(url: str) -> None:
    """SQLite 文件所在目录不存在时先创建。"""
    marker = "sqlite+aiosqlite:///"
    if url.startswith(marker):
        path = url[len(marker) :]
        if path and path not in (":memory:",):
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)


_ensure_sqlite_dir(settings.database_url)

_ENGINE_KWARGS = {
    "echo": False,
    "future": True,
    "pool_pre_ping": True,
}
if settings.database_url.startswith("sqlite"):
    # SAG-OPT-401：SQLite 桌面单进程并发有限，显式用小连接池（10+5），
    # 不再沿用 20+40 大池，降低内存与文件句柄占用。
    _ENGINE_KWARGS.update(
        {
            "pool_size": settings.database_sqlite_pool_size,
            "max_overflow": settings.database_sqlite_max_overflow,
            "pool_timeout": settings.database_pool_timeout,
        }
    )
else:
    _ENGINE_KWARGS.update(
        {
            "pool_size": settings.database_pool_size,
            "max_overflow": settings.database_max_overflow,
            "pool_timeout": settings.database_pool_timeout,
        }
    )

engine: AsyncEngine = create_async_engine(settings.database_url, **_ENGINE_KWARGS)


# SQLite：外键约束 + 并发友好（WAL 读写并行，busy_timeout 让写入等待而非立即报锁）。
# SAG-OPT-402：基础三项 + 调优项（synchronous/cache_size/mmap_size/temp_store）
# 由 sqlite_pragmas.apply_sqlite_pragmas 统一应用到每个新连接。
if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        from sag_api.core.sqlite_pragmas import apply_sqlite_pragmas

        apply_sqlite_pragmas(dbapi_conn)


SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


# 已存在的表需要补的新列（dev 轻量增量迁移；生产用 Alembic）。
# create_all 只建新表、不改旧表，故对演进列做幂等 ADD COLUMN。
_COLUMN_UPGRADES: dict[str, dict[str, str]] = {
    "agents": {"is_default": "BOOLEAN NOT NULL DEFAULT 0"},
    "documents": {
        "progress": "INTEGER NOT NULL DEFAULT 0",
        "token_usage": "BIGINT NOT NULL DEFAULT 0",
        "relative_path": "VARCHAR(1024)",
        "content_sha256": "VARCHAR(64)",
        "code_language": "VARCHAR(64)",
    },
    "threads": {"archived": "BOOLEAN NOT NULL DEFAULT 0"},
    "messages": {
        "attachments_json": "JSON",
        "steps_json": "JSON",
        "prompt_preview": "TEXT NOT NULL DEFAULT ''",
    },
    "universe_dirty_sources": {"revision": "INTEGER NOT NULL DEFAULT 1"},
    "vector_write_jobs": {
        "lease_owner": "VARCHAR(160)",
        "lease_expires_at": "DATETIME",
        "embedding_version": "VARCHAR(64) NOT NULL DEFAULT 'default'",
        "parent_batch_id": "VARCHAR(32)",
        "superseded_by": "VARCHAR(32)",
        "record_count": "INTEGER NOT NULL DEFAULT 0",
    },
}

# Existing tables also need newly introduced hot-path indexes. Keep these
# idempotent for local/embedded upgrades; production deployments can express
# the same DDL in their migration runner.
_INDEX_UPGRADES = (
    "CREATE INDEX IF NOT EXISTS ix_messages_thread_created_id ON messages (thread_id, created_at, id)",
    "CREATE INDEX IF NOT EXISTS ix_documents_source_sag_source ON documents (source_id, sag_source_id)",
    "CREATE INDEX IF NOT EXISTS ix_documents_source_status ON documents (source_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_documents_source_relative_path ON documents (source_id, relative_path)",
    "CREATE INDEX IF NOT EXISTS ix_documents_source_code_language ON documents (source_id, code_language)",
    "CREATE INDEX IF NOT EXISTS ix_documents_source_graph_sample "
    "ON documents (source_id, event_count, sag_source_id, status, created_at, id)",
    "CREATE INDEX IF NOT EXISTS ix_source_graph_cache_source_revision ON source_graph_caches (source_id, revision)",
    "CREATE INDEX IF NOT EXISTS ix_source_graph_cache_source_key ON source_graph_caches (source_id, cache_key)",
    "CREATE INDEX IF NOT EXISTS ix_universe_graph_cache_source_revision ON universe_graph_caches (source_id, revision)",
    "CREATE INDEX IF NOT EXISTS ix_universe_graph_cache_source_key ON universe_graph_caches (source_id, cache_key)",
    "CREATE INDEX IF NOT EXISTS ix_universe_graph_cache_kind ON universe_graph_caches (source_id, kind)",
    "CREATE INDEX IF NOT EXISTS ix_vector_write_status_next ON vector_write_jobs (status, next_run_at)",
    "CREATE INDEX IF NOT EXISTS ix_vector_write_source_status ON vector_write_jobs (source_config_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_vector_write_lease ON vector_write_jobs (lease_owner, lease_expires_at)",
    "CREATE INDEX IF NOT EXISTS ix_vector_write_embedding_status ON vector_write_jobs (embedding_version, status)",
    # Queue V2 record-level detail table (created by create_all as a new table;
    # the DDL below is an idempotent safety net for pre-existing databases).
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_vector_write_items_active "
    "ON vector_write_items (table_name, record_id, embedding_version) "
    "WHERE status IN ('queued','embedding','ready_to_write','writing','retry')",
    "CREATE INDEX IF NOT EXISTS ix_vector_write_items_job_status ON vector_write_items (job_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_vector_write_items_table_status ON vector_write_items (table_name, status)",
    "CREATE INDEX IF NOT EXISTS ix_vector_write_items_source_status ON vector_write_items (source_config_id, status)",
)


async def _ensure_columns() -> None:
    from sqlalchemy import inspect as sa_inspect

    def _existing(sync_conn, table: str) -> set[str] | None:
        insp = sa_inspect(sync_conn)
        if not insp.has_table(table):
            return None
        return {c["name"] for c in insp.get_columns(table)}

    async with engine.begin() as conn:
        for table, cols in _COLUMN_UPGRADES.items():
            existing = await conn.run_sync(_existing, table)
            if existing is None:
                continue
            for col, ddl in cols.items():
                if col not in existing:
                    await conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


async def _ensure_indexes() -> None:
    async with engine.begin() as conn:
        for ddl in _INDEX_UPGRADES:
            await conn.exec_driver_sql(ddl)


async def init_db() -> None:
    """开发态建表（生产用 Alembic）。导入 models 以注册到 metadata。"""
    from sag_api.db import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _ensure_columns()
    await _ensure_indexes()


async def dispose_db() -> None:
    await engine.dispose()
