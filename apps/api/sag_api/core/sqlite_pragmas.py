"""SAG-OPT-402：SQLite PRAGMA 调优（设置驱动、可回退）。

基础三项（foreign_keys / journal_mode=WAL / busy_timeout）在任何情况下都应用；
调优项（synchronous / cache_size / mmap_size / temp_store）由
``SAG_DATABASE_SQLITE_PRAGMA_TUNING_ENABLED`` 整体控制，方便在异常环境中
一键回退到旧行为。所有语句来自同一个生成器，API 元数据库、zleap 嵌入引擎
与 EngineManager 的连接事件共用，避免三处逻辑漂移。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sag_api.core.logging import get_logger

if TYPE_CHECKING:
    from sag_api.core.config import Settings

log = get_logger("sag.sqlite_pragmas")


def sqlite_pragma_statements(settings=None):
    """返回应应用于每个 SQLite 连接的 PRAGMA 语句列表（顺序固定）。"""
    if settings is None:
        from sag_api.core.config import get_settings

        settings = get_settings()

    statements = [
        "PRAGMA foreign_keys=ON",
        "PRAGMA journal_mode=WAL",
        "PRAGMA busy_timeout=60000",
    ]
    if not settings.database_sqlite_pragma_tuning_enabled:
        return statements

    statements.append(f"PRAGMA synchronous={settings.database_sqlite_synchronous}")
    statements.append(f"PRAGMA cache_size={settings.database_sqlite_cache_size}")
    if settings.database_sqlite_mmap_size > 0:
        statements.append(f"PRAGMA mmap_size={settings.database_sqlite_mmap_size}")
    statements.append(f"PRAGMA temp_store={settings.database_sqlite_temp_store}")
    return statements


def apply_sqlite_pragmas(dbapi_conn):
    """对单个 SQLite DBAPI 连接应用全部 PRAGMA（connect event 使用）。

    基础三项失败视为致命（外键/WAL/锁等待是并发正确性的前提）；
    调优项各自捕获异常并降级记录，不让个别平台差异阻断连接。
    """
    from sag_api.core.config import get_settings

    settings = get_settings()
    statements = sqlite_pragma_statements(settings)
    base_count = 3
    cur = dbapi_conn.cursor()
    try:
        for index, statement in enumerate(statements):
            try:
                cur.execute(statement)
            except Exception:  # noqa: BLE001
                if index < base_count:
                    raise
                log.warning("SQLite 调优 PRAGMA 失败已跳过：%s", statement, exc_info=True)
    finally:
        cur.close()
