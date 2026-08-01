"""Compatibility shims for dependency-owned zleap-sag behavior.

These patches live at the application boundary so we can keep user workflows
working while waiting for upstream package releases.
"""

from __future__ import annotations

import asyncio
import copy
import os
from contextlib import contextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session
from sqlalchemy.sql.sqltypes import JSON, BigInteger, Integer

from sag_api.core.config import get_settings as get_sag_settings
from sag_api.core.logging import get_logger

log = get_logger("sag.compat")

_SQLITE_INT_MIN = -(2**63)
_SQLITE_INT_MAX = 2**63 - 1
_BOOL_ENV_VALUES = {"0", "1", "false", "true", "f", "t", "no", "yes", "n", "y", "off", "on"}


@contextmanager
def _without_invalid_zleap_debug_env():
    """Hide unrelated DEBUG values that zleap-sag treats as its bool debug flag."""

    removed: dict[str, str] = {}
    for name in ("debug", "DEBUG"):
        value = os.environ.get(name)
        if value is not None and value.strip().lower() not in _BOOL_ENV_VALUES:
            removed[name] = value
            os.environ.pop(name, None)
    try:
        yield
    finally:
        for name, value in removed.items():
            os.environ[name] = value


def _is_sqlite_safe_int(value: int) -> bool:
    return _SQLITE_INT_MIN <= value <= _SQLITE_INT_MAX


def _sanitize_sqlite_json_ints(value: Any) -> tuple[Any, int]:
    """Convert integers SQLite cannot bind into strings inside JSON payloads."""

    if isinstance(value, bool):
        return value, 0
    if isinstance(value, int):
        if _is_sqlite_safe_int(value):
            return value, 0
        return str(value), 1
    if isinstance(value, list):
        changed = 0
        sanitized = []
        for item in value:
            next_value, count = _sanitize_sqlite_json_ints(item)
            sanitized.append(next_value)
            changed += count
        return (sanitized, changed) if changed else (value, 0)
    if isinstance(value, tuple):
        changed = 0
        sanitized = []
        for item in value:
            next_value, count = _sanitize_sqlite_json_ints(item)
            sanitized.append(next_value)
            changed += count
        return (sanitized, changed) if changed else (value, 0)
    if isinstance(value, dict):
        changed = 0
        sanitized = {}
        for key, item in value.items():
            next_key, key_count = _sanitize_sqlite_json_ints(key)
            next_value, value_count = _sanitize_sqlite_json_ints(item)
            sanitized[next_key] = next_value
            changed += key_count + value_count
        return (sanitized, changed) if changed else (value, 0)
    return value, 0


def _coerce_sqlite_integer_column(obj: Any, attr_name: str, *, nullable: bool) -> int:
    value = getattr(obj, attr_name, None)
    if isinstance(value, bool) or not isinstance(value, int) or _is_sqlite_safe_int(value):
        return 0

    if nullable:
        setattr(obj, attr_name, None)
    else:
        setattr(obj, attr_name, _SQLITE_INT_MAX if value > 0 else _SQLITE_INT_MIN)

    # Keep the original machine constant searchable when zleap-sag stores a typed
    # entity value, while avoiding SQLite's 64-bit INTEGER binding failure.
    if attr_name == "int_value" and hasattr(obj, "value_raw") and not getattr(obj, "value_raw", None):
        obj.value_raw = str(value)
    return 1


def _sanitize_zleap_sqlite_ints(obj: Any) -> int:
    if not obj.__class__.__module__.startswith("zleap.sag.db"):
        return 0
    mapper = getattr(obj, "__mapper__", None)
    if mapper is None:
        return 0

    changed = 0
    for column in mapper.columns:
        attr_name = column.key
        column_type = column.type
        if isinstance(column_type, JSON):
            value = getattr(obj, attr_name, None)
            sanitized, count = _sanitize_sqlite_json_ints(value)
            if count:
                setattr(obj, attr_name, sanitized)
                changed += count
            continue

        if isinstance(column_type, BigInteger | Integer):
            changed += _coerce_sqlite_integer_column(obj, attr_name, nullable=column.nullable)
    return changed


def _without_required_field(node: dict[str, Any], field: str) -> bool:
    required = node.get("required")
    if not isinstance(required, list) or field not in required:
        return False
    node["required"] = [item for item in required if item != field]
    return True


def _looks_like_extract_response_schema(schema: Any) -> bool:
    if not isinstance(schema, dict):
        return False
    data = schema.get("properties", {}).get("data")
    if not isinstance(data, dict):
        return False
    data_props = data.get("properties")
    return (
        schema.get("type") == "object"
        and schema.get("properties", {}).get("type", {}).get("const") == "response"
        and isinstance(data_props, dict)
        and "items" in data_props
        and "meta" in data_props
    )


def _relax_extract_schema(schema: dict[str, Any]) -> dict[str, Any]:
    relaxed = copy.deepcopy(schema)
    data = relaxed.get("properties", {}).get("data")
    if isinstance(data, dict):
        _without_required_field(data, "meta")
        meta = data.get("properties", {}).get("meta")
        if isinstance(meta, dict):
            _without_required_field(meta, "reason")
    event = relaxed.get("definitions", {}).get("event")
    if isinstance(event, dict):
        _without_required_field(event, "is_valid")
    return relaxed


def _repair_extract_response(result: Any) -> set[str]:
    repaired: set[str] = set()
    if not isinstance(result, dict) or result.get("type") != "response":
        return repaired
    data = result.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return repaired
    meta = data.get("meta")
    if not isinstance(meta, dict):
        data["meta"] = {"reason": "model omitted data.meta; filled by SAG compatibility layer"}
        meta = data["meta"]
        repaired.add("data.meta")
    reason = meta.get("reason")
    if not isinstance(reason, str):
        meta["reason"] = ""
        repaired.add("data.meta.reason")

    def repair_item(item: Any) -> None:
        if not isinstance(item, dict):
            return
        if "is_valid" not in item:
            item["is_valid"] = True
            repaired.add("data.items[].is_valid")
        children = item.get("children")
        if isinstance(children, list):
            for child in children:
                repair_item(child)

    for item in data["items"]:
        repair_item(item)
    return repaired


def install_zleap_sag_extract_compat() -> None:
    """Allow event extraction to accept minor omissions in model output.

    Some OpenAI-compatible models produce valid event ``data.items`` but omit
    telemetry-only ``data.meta`` or the boolean ``is_valid`` flag.  Upstream
    zleap-sag validates these fields before its parser can use the extracted
    events, even though the parser already treats missing ``is_valid`` as true.
    We keep strict validation for title/content/references and restore the
    compatible defaults before zleap-sag's own output validator runs.
    """

    from zleap.sag.modules.extract.processor import EventProcessor

    current = EventProcessor._call_llm_with_retry
    if getattr(current, "_sag_api_extract_meta_compat", False):
        return

    async def _patched_call_llm_with_retry(self, messages, schema):  # type: ignore[no-untyped-def]
        active_schema = schema
        if _looks_like_extract_response_schema(schema):
            active_schema = _relax_extract_schema(schema)
        result = await current(self, messages, active_schema)
        repaired = _repair_extract_response(result)
        if repaired:
            log.info("已兼容补齐 zleap-sag 事项抽取响应字段：%s", ", ".join(sorted(repaired)))
        return result

    _patched_call_llm_with_retry._sag_api_extract_meta_compat = True  # type: ignore[attr-defined]
    EventProcessor._call_llm_with_retry = _patched_call_llm_with_retry


def install_zleap_sag_sqlite_integer_compat() -> None:
    """Prevent oversized Python integers from failing zleap-sag SQLite writes.

    SQLite INTEGER values are signed 64-bit. Source-code corpora can contain
    constants larger than that, and LLM extraction may preserve them as JSON
    numbers or typed entity ``int_value`` fields. aiosqlite then raises
    ``Python int too large to convert to SQLite INTEGER`` during flush.

    Keep normal integers unchanged. For JSON columns, stringify only the
    oversized integers. For INTEGER/BIGINTEGER columns, nullable fields become
    NULL (with ``Entity.value_raw`` preserving the original literal when
    available); non-nullable counters are clamped as a last-resort safeguard.
    """

    if getattr(Session, "_sag_api_sqlite_integer_compat", False):
        return

    @event.listens_for(Session, "before_flush")
    def _sanitize_before_flush(session, _flush_context, _instances):  # noqa: ANN001
        total_changed = 0
        for obj in tuple(session.new) + tuple(session.dirty):
            total_changed += _sanitize_zleap_sqlite_ints(obj)
        if total_changed:
            log.warning(
                "已清理 zleap-sag SQLite 超大整数字段: count=%d",
                total_changed,
            )

    Session._sag_api_sqlite_integer_compat = True  # type: ignore[attr-defined]


def install_zleap_sag_async_sqlite_reset_compat() -> None:
    """Avoid sync disposal of zleap-sag's aiosqlite engine inside the event loop.

    Upstream ``DataEngine.start()`` calls a synchronous singleton reset before
    each engine is created. For ``sqlite+aiosqlite`` that reset disposes the
    async pool through ``sync_engine.dispose()``, which can log
    ``MissingGreenlet`` while closing adapted async connections. We keep the
    reset semantics but schedule the async dispose on the running loop.
    """

    from zleap.sag import _bootstrap

    current = _bootstrap.reset_core_singletons
    if getattr(current, "_sag_api_async_sqlite_reset_compat", False):
        return

    def _patched_reset_core_singletons() -> None:
        from zleap.sag.core.storage.client import reset_es_client
        from zleap.sag.db import base as db_base

        engine = getattr(db_base, "_engine", None)
        if (
            engine is not None
            and getattr(engine.dialect, "name", "") == "sqlite"
            and str(engine.url).startswith("sqlite+aiosqlite")
        ):
            db_base._engine = None  # type: ignore[attr-defined]  # noqa: SLF001
            db_base._session_factory = None  # type: ignore[attr-defined]  # noqa: SLF001
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(engine.dispose())
            else:
                loop.create_task(engine.dispose(), name="zleap-sag-sqlite-dispose")
            reset_es_client()
            return

        current()

    _patched_reset_core_singletons._sag_api_async_sqlite_reset_compat = True  # type: ignore[attr-defined]
    _bootstrap.reset_core_singletons = _patched_reset_core_singletons


def install_zleap_sag_sqlite_pool_compat() -> None:
    """Give zleap-sag's embedded SQLite engine a tuned async connection pool.

    zleap-sag's sqlite path calls ``create_async_engine()`` without pool
    settings, which defaults to a tiny QueuePool on current SQLAlchemy. During
    extraction a single chunk can touch entity types, entities, event links and
    source status in separate sessions; with concurrent jobs the default
    5+10 pool is easy to exhaust. SAG-OPT-401: size the pool from actual
    per-process concurrency instead of thread count, so we keep a modest
    10+5 (default) rather than the oversized 20+40 that burned memory and
    file handles on the desktop SQLite path. Keep this patch at the
    application boundary.
    """

    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import create_async_engine
    from zleap.sag.core.config import get_settings
    from zleap.sag.db import base as db_base

    current = db_base.get_engine
    if getattr(current, "_sag_api_sqlite_pool_compat", False):
        return

    def _patched_get_engine():
        engine = getattr(db_base, "_engine", None)
        if engine is not None:
            return engine

        with _without_invalid_zleap_debug_env():
            settings = get_settings()
        provider = (settings.db_provider or "mysql").lower()
        if provider != "sqlite":
            return current()

        url = settings.database_url
        echo = settings.log_level == "DEBUG"
        # SAG-OPT-401：按进程内实际并发调池，不再硬编码 20+40 大池。
        # 桌面 SQLite 并发有限，10+5 已覆盖 2 个后台任务 × 5 路 chunk 抽取
        # 的会话需求，且内存/句柄占用显著下降。
        sag_settings = get_sag_settings()
        engine = create_async_engine(
            url,
            echo=echo,
            pool_pre_ping=True,
            pool_size=sag_settings.database_sqlite_pool_size,
            max_overflow=sag_settings.database_sqlite_max_overflow,
            pool_timeout=60,
        )

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
            from sag_api.core.sqlite_pragmas import apply_sqlite_pragmas

            apply_sqlite_pragmas(dbapi_conn)
        db_base.logger.info(  # type: ignore[attr-defined]  # noqa: SLF001
            "数据库引擎创建完成",
            extra={"provider": provider, "database": settings.mysql_database},
        )
        return engine

    _patched_get_engine._sag_api_sqlite_pool_compat = True  # type: ignore[attr-defined]
    db_base.get_engine = _patched_get_engine
