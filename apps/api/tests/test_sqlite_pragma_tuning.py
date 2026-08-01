"""SAG-OPT-402：SQLite PRAGMA 调优（设置驱动、可回退）。

覆盖：
- 默认设置下生成基础三项 + 4 项调优 PRAGMA（顺序固定）。
- 关闭 ``database_sqlite_pragma_tuning_enabled`` 后仅保留基础三项（可回退）。
- mmap_size=0 时省略 mmap PRAGMA。
- 真实 SQLite 连接上应用后各 PRAGMA 生效。
- API 元数据库引擎的 connect event 确实把调优项应用到新连接。
"""


import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine


def _settings(**overrides):
    from sag_api.core.config import Settings

    return Settings(**overrides)


def test_pragma_statements_default_order():
    from sag_api.core.sqlite_pragmas import sqlite_pragma_statements

    statements = sqlite_pragma_statements(_settings())
    assert statements == [
        "PRAGMA foreign_keys=ON",
        "PRAGMA journal_mode=WAL",
        "PRAGMA busy_timeout=60000",
        "PRAGMA synchronous=NORMAL",
        "PRAGMA cache_size=-65536",
        "PRAGMA mmap_size=268435456",
        "PRAGMA temp_store=MEMORY",
    ]


def test_pragma_statements_tuning_disabled_fallback():
    from sag_api.core.sqlite_pragmas import sqlite_pragma_statements

    statements = sqlite_pragma_statements(_settings(database_sqlite_pragma_tuning_enabled=False))
    assert statements == [
        "PRAGMA foreign_keys=ON",
        "PRAGMA journal_mode=WAL",
        "PRAGMA busy_timeout=60000",
    ]


def test_pragma_statements_custom_values():
    from sag_api.core.sqlite_pragmas import sqlite_pragma_statements

    statements = sqlite_pragma_statements(
        _settings(
            database_sqlite_synchronous="FULL",
            database_sqlite_cache_size=-32768,
            database_sqlite_mmap_size=0,
            database_sqlite_temp_store="FILE",
        )
    )
    assert statements == [
        "PRAGMA foreign_keys=ON",
        "PRAGMA journal_mode=WAL",
        "PRAGMA busy_timeout=60000",
        "PRAGMA synchronous=FULL",
        "PRAGMA cache_size=-32768",
        "PRAGMA temp_store=FILE",
    ]


def test_apply_sqlite_pragmas_on_real_connection():
    """把语句应用到真实 SQLite 连接后，各 PRAGMA 实际生效。"""
    import sqlite3

    from sag_api.core.sqlite_pragmas import apply_sqlite_pragmas

    conn = sqlite3.connect(":memory:")
    try:
        apply_sqlite_pragmas(conn)
        cur = conn.cursor()
        assert cur.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert cur.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
        assert cur.execute("PRAGMA cache_size").fetchone()[0] == -65536
        # 当前 Windows 自带 SQLite 构建可能未编译 mmap 支持（读取返回空行），
        # 此时视为“生效但不可用”而非失败；支持时必须是配置值。
        mmap_val = cur.execute("PRAGMA mmap_size").fetchone()
        assert mmap_val is None or mmap_val[0] == 268435456
        assert cur.execute("PRAGMA temp_store").fetchone()[0] == 2  # MEMORY
        assert cur.execute("PRAGMA busy_timeout").fetchone()[0] == 60000
        cur.close()
    finally:
        conn.close()


def test_tuning_pragma_failure_does_not_block_connection():
    """调优项执行失败只降级记录，基础三项仍然生效，连接不被阻断。"""
    from sag_api.core.sqlite_pragmas import apply_sqlite_pragmas

    class _FakeCursor:
        def __init__(self):
            self.calls = []

        def execute(self, sql):
            self.calls.append(str(sql))
            if str(sql).startswith("PRAGMA synchronous="):
                raise RuntimeError("simulate unsupported platform pragma")
            return self

        def close(self):
            pass

    class _FakeConn:
        def __init__(self):
            self.cur = _FakeCursor()

        def cursor(self):
            return self.cur

    fake = _FakeConn()
    apply_sqlite_pragmas(fake)  # 不应抛异常
    assert any(c.startswith("PRAGMA synchronous=") for c in fake.cur.calls)
    # 后续调优语句继续执行，连接未被阻断
    assert "PRAGMA cache_size=-65536" in fake.cur.calls
    assert "PRAGMA mmap_size=268435456" in fake.cur.calls
    assert "PRAGMA temp_store=MEMORY" in fake.cur.calls
@pytest.mark.asyncio
async def test_engine_connect_event_applies_tuning(tmp_path):
    """引擎 connect event 使用 apply_sqlite_pragmas，新连接自动带上调优项。"""
    from sag_api.core.sqlite_pragmas import apply_sqlite_pragmas

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pragma.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def _apply(dbapi_conn, _record):
        apply_sqlite_pragmas(dbapi_conn)

    try:
        async with engine.connect() as connection:
            sync_val = (await connection.exec_driver_sql("PRAGMA synchronous")).scalar()
            cache_val = (await connection.exec_driver_sql("PRAGMA cache_size")).scalar()
            timeout_val = (await connection.exec_driver_sql("PRAGMA busy_timeout")).scalar()
        assert sync_val == 1  # NORMAL
        assert cache_val == -65536
        assert timeout_val == 60000
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_api_metadata_engine_applies_tuning():
    """回归：API 元数据库引擎（db.py）的 connect event 应用调优 PRAGMA。"""
    from sag_api.core.config import get_settings
    from sag_api.core.db import engine

    if not str(engine.url).startswith("sqlite"):
        pytest.skip("仅 SQLite 元数据库下有意义")
    settings = get_settings()
    if not settings.database_sqlite_pragma_tuning_enabled:
        pytest.skip("调优开关关闭时跳过")

    async with engine.connect() as connection:
        sync_val = (await connection.exec_driver_sql("PRAGMA synchronous")).scalar()
        timeout_val = (await connection.exec_driver_sql("PRAGMA busy_timeout")).scalar()
    assert sync_val == {"OFF": 0, "NORMAL": 1, "FULL": 2, "EXTRA": 3}[settings.database_sqlite_synchronous]
    assert timeout_val == 60000
