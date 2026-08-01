"""SAG-OPT-401：SQLite 连接池调整。

覆盖：
- API 元数据库 SQLite 引擎使用调优后的小连接池（默认 10+5，区间 8~12 / 4~8），
  不再沿用 20+40 大池。
- zleap-sag 嵌入 SQLite 引擎（`install_zleap_sag_sqlite_pool_compat`）同样采用
  设置驱动的小连接池，且可被 `SAG_DATABASE_SQLITE_*` 覆盖。
- 按进程内实际并发（而非线程数）压测：12 路并发读不触发 pool timeout。
- 连接关闭时的 `MissingGreenlet`：`reset_core_singletons` 在事件循环内改为异步 dispose。
- EngineManager 释放/LRU 逐出槽位时异步关闭引擎的生命周期。
"""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import text


def _pool_config(engine):
    """返回 (pool_size, max_overflow) 的可靠读取方式（避免依赖内部属性名）。"""
    pool = engine.sync_engine.pool
    max_overflow = getattr(pool, "_max_overflow", None)
    inner = getattr(pool, "_pool", None)
    pool_size = getattr(inner, "maxsize", None) if inner is not None else None
    return pool_size, max_overflow


def _reset_zleap_engine():
    from zleap.sag.db import base as db_base

    db_base._engine = None  # type: ignore[attr-defined]  # noqa: SLF001
    db_base._session_factory = None  # type: ignore[attr-defined]  # noqa: SLF001


def test_api_sqlite_engine_uses_tuned_pool():
    """API 元数据库 SQLite 引擎显式使用 8~12 / 4~8 区间的小连接池。"""
    from sag_api.core.config import get_settings
    from sag_api.core.db import engine

    if not str(engine.url).startswith("sqlite"):
        pytest.skip("本测试仅在 SQLite 元数据库下有意义")

    settings = get_settings()
    assert 8 <= settings.database_sqlite_pool_size <= 12
    assert 4 <= settings.database_sqlite_max_overflow <= 8

    pool_size, max_overflow = _pool_config(engine)
    assert pool_size == settings.database_sqlite_pool_size
    assert max_overflow == settings.database_sqlite_max_overflow
    # 明确不再使用 20+40 大池
    assert not (pool_size == 20 and max_overflow == 40)
    assert pool_size + max_overflow < 20 + 40


def test_zleap_sqlite_pool_compat_uses_tuned_defaults(monkeypatch, tmp_path):
    """zleap-sag 嵌入引擎默认也用 10+5 小池（默认设置），而非 20+40。"""
    from zleap.sag.db import base as db_base

    monkeypatch.setenv("DB_PROVIDER", "sqlite")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'zleap.db'}")

    from sag_api.core.config import get_settings

    get_settings.cache_clear()
    try:
        from sag_api.sag.compat import install_zleap_sag_sqlite_pool_compat

        _reset_zleap_engine()
        install_zleap_sag_sqlite_pool_compat()
        engine = db_base.get_engine()
        try:
            assert getattr(engine.dialect, "name", "") == "sqlite"
            pool_size, max_overflow = _pool_config(engine)
            assert pool_size == get_settings().database_sqlite_pool_size
            assert max_overflow == get_settings().database_sqlite_max_overflow
            assert not (pool_size == 20 and max_overflow == 40)
        finally:
            import asyncio as _asyncio

            _asyncio.run(engine.dispose())
            _reset_zleap_engine()
    finally:
        get_settings.cache_clear()


def test_zleap_sqlite_pool_compat_settings_override(monkeypatch, tmp_path):
    """zleap-sag 嵌入引擎连接池可被 SAG_DATABASE_SQLITE_* 覆盖。"""
    from zleap.sag.db import base as db_base

    monkeypatch.setenv("DB_PROVIDER", "sqlite")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'zleap2.db'}")
    monkeypatch.setenv("SAG_DATABASE_SQLITE_POOL_SIZE", "8")
    monkeypatch.setenv("SAG_DATABASE_SQLITE_MAX_OVERFLOW", "4")

    from sag_api.core.config import get_settings

    get_settings.cache_clear()
    try:
        from sag_api.sag.compat import install_zleap_sag_sqlite_pool_compat

        _reset_zleap_engine()
        install_zleap_sag_sqlite_pool_compat()
        engine = db_base.get_engine()
        try:
            pool_size, max_overflow = _pool_config(engine)
            assert pool_size == 8
            assert max_overflow == 4
        finally:
            import asyncio as _asyncio

            _asyncio.run(engine.dispose())
            _reset_zleap_engine()
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_api_sqlite_engine_realistic_concurrency_no_pool_timeout():
    """12 路并发读（超过 pool_size=10、未超 10+5 上限）不触发 pool timeout。"""
    from sag_api.core.db import engine

    if not str(engine.url).startswith("sqlite"):
        pytest.skip("本测试仅在 SQLite 元数据库下有意义")

    async def read_one():
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT 1"))
            assert result.scalar() == 1

    await asyncio.gather(*(read_one() for _ in range(12)))


def test_async_sqlite_reset_compat_disposes_outside_event_loop():
    """连接关闭 MissingGreenlet：reset 在事件循环内调度异步 dispose，而非同步 dispose。"""
    from zleap.sag import _bootstrap
    from zleap.sag.db import base as db_base

    from sag_api.sag.compat import install_zleap_sag_async_sqlite_reset_compat

    disposed: list[bool] = []

    class _FakeDialect:
        name = "sqlite"

    class _FakeEngine:
        dialect = _FakeDialect()
        url = "sqlite+aiosqlite:///./fake.db"

        async def dispose(self):
            disposed.append(True)

        @property
        def sync_engine(self):
            raise AssertionError("reset 兼容路径不得调用同步 dispose")

    install_zleap_sag_async_sqlite_reset_compat()
    db_base._engine = _FakeEngine()  # type: ignore[attr-defined]  # noqa: SLF001
    db_base._session_factory = object()  # type: ignore[attr-defined]  # noqa: SLF001

    async def scenario():
        _bootstrap.reset_core_singletons()
        assert db_base._engine is None  # type: ignore[attr-defined]  # noqa: SLF001
        assert db_base._session_factory is None  # type: ignore[attr-defined]  # noqa: SLF001
        await asyncio.sleep(0)
        assert disposed == [True]

    asyncio.run(scenario())
    _reset_zleap_engine()


@pytest.mark.asyncio
async def test_engine_release_closes_slot_engine():
    """EngineManager.release 关闭并摘除槽位引擎；重复释放幂等。"""
    from sag_api.core.config import settings
    from sag_api.sag.engine_manager import EngineManager, _Slot

    class FakeEngine:
        closed = False

        async def aclose(self):
            self.closed = True

    engine = FakeEngine()
    manager = EngineManager(settings)
    manager._slots["s1"] = _Slot(engine=engine)

    await manager.release("s1")
    assert engine.closed is True
    assert "s1" not in manager._slots

    await manager.release("s1")  # 幂等
    assert engine.closed is True
