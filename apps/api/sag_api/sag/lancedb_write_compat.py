"""LanceDB 追加/更新分离兼容层（SAG-OPT-105）。

上游 ``zleap.sag.core.storage.lancedb_store.LanceDBStore._write`` 对所有写入
统一执行 ``merge_insert("id").when_matched_update_all().when_not_matched_insert_all()``：
即使是整批全新记录也会创建一次 merge 版本，混合批次还会重复触发 match 逻辑，
长期运行会放大 LanceDB 版本与碎片增长。

本模块在应用边界 monkey-patch ``LanceDBStore``，不修改虚拟环境内文件：

- ``_write`` 先按 ``id IN (...)"`` 分块预查询已存在 ID（``AsyncQuery.select(["id"])``）。
- 纯新增子集走 ``AsyncTable.add``（一次 append，一个版本）。
- 已存在子集走 ``merge_insert("id").when_matched_update_all()``（一次 merge，不插入新行）。
- 同时新增公开的 ``bulk_append_new`` 方法面，供“调用方已确认缺失”的路径直接批量 append。
- 预查询/拆分逻辑任何意外异常都回退到上游原始实现，保证写入链路不中断；
  ``SAG_VECTOR_APPEND_NEW_ENABLED=false`` 可整体回退旧路径（功能开关）。
"""

from __future__ import annotations

from typing import Any

from sag_api.core.config import get_settings
from sag_api.core.logging import get_logger

log = get_logger("sag.lancedb_compat")

_PATCH_MARK = "_sag_api_append_vs_merge_patch"
_BULK_APPEND_METHOD = "bulk_append_new"


def _doc_id(doc: dict[str, Any], explicit: str | None = None) -> str | None:
    did = (
        explicit
        or doc.get("_id")
        or doc.get("id")
        or doc.get("chunk_id")
        or doc.get("entity_id")
        or doc.get("event_id")
    )
    return str(did) if did is not None else None


def _sql_str(value: Any) -> str:
    """SQL 字符串字面量（单引号转义），与上游 lancedb_store 一致。"""
    return "'" + str(value).replace("'", "''") + "'"


def _chunk_ids(ids: list[str], size: int) -> list[list[str]]:
    return [ids[i : i + size] for i in range(0, len(ids), size)]


async def _existing_ids(tbl: Any, ids: list[str], chunk_size: int) -> set[str]:
    """分块查询 batch 中已存在的记录 ID，避免超长 IN 子句。"""
    existing: set[str] = set()
    for chunk in _chunk_ids(ids, chunk_size):
        where = "id IN (" + ", ".join(_sql_str(item) for item in chunk) + ")"
        rows = await tbl.query().where(where).select(["id"]).to_arrow()
        existing.update(str(row["id"]) for row in rows.to_pylist())
    return existing


def _dedupe_by_id(normalized: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同批按 id 去重（保留最后一条），避免 add 路径产生重复行。"""
    seen: dict[str, dict[str, Any]] = {}
    for row in normalized:
        seen[row["id"]] = row
    return list(seen.values())


def install_zleap_sag_lancedb_append_vs_merge_patch() -> None:
    """安装追加/更新分离补丁（幂等；不修改 site-packages 文件）。"""

    from zleap.sag.core.storage.lancedb_store import LanceDBStore

    original = LanceDBStore._write
    if getattr(original, _PATCH_MARK, False):
        return

    async def _patched_write(self: Any, index: str, rows: list[tuple[str, dict[str, Any]]]) -> int:
        if not rows:
            return 0
        settings = get_settings()
        if not settings.vector_append_new_enabled:
            return await original(self, index, rows)

        tbl = await self._ensure_table(index, [doc for _, doc in rows])
        await self._cache_schema(tbl)
        normalized = _dedupe_by_id([self._normalize_row(tbl, did, doc) for did, doc in rows])
        try:
            existing = await _existing_ids(
                tbl,
                [row["id"] for row in normalized],
                int(settings.vector_append_lookup_chunk_size),
            )
            new_rows = [row for row in normalized if row["id"] not in existing]
            update_rows = [row for row in normalized if row["id"] in existing]
            if new_rows:
                await tbl.add(new_rows)
            if update_rows:
                await tbl.merge_insert("id").when_matched_update_all().execute(update_rows)
            return len(normalized)
        except Exception as error:  # noqa: BLE001 - 任何意外失败降级原始路径
            log.warning(
                "LanceDB 追加/更新分离失败，降级为上游 merge_insert 路径：%s",
                error,
            )
            return await original(self, index, rows)

    _patched_write._sag_api_append_vs_merge_patch = True  # type: ignore[attr-defined]
    LanceDBStore._write = _patched_write

    if not hasattr(LanceDBStore, _BULK_APPEND_METHOD):

        async def _bulk_append_new(self: Any, index: str, documents: list[dict[str, Any]]) -> int:
            """Append-only 批量写入：调用方必须已确认目标记录在表中不存在。

            与 ``bulk_index`` 同构（``_id`` 剥离、按 ``_doc_id`` 取主键），但只走
            ``AsyncTable.add``，不执行任何 merge。
            """
            rows: list[tuple[str, dict[str, Any]]] = []
            for doc in documents:
                did = _doc_id(doc)
                if did is None:
                    continue
                rows.append((did, {k: v for k, v in doc.items() if k != "_id"}))
            if not rows:
                return 0
            tbl = await self._ensure_table(index, [doc for _, doc in rows])
            await self._cache_schema(tbl)
            normalized = _dedupe_by_id([self._normalize_row(tbl, did, doc) for did, doc in rows])
            await tbl.add(normalized)
            return len(normalized)

        setattr(LanceDBStore, _BULK_APPEND_METHOD, _bulk_append_new)
        log.info("LanceDBStore.bulk_append_new 已安装（应用层补丁）")

    log.info("LanceDB 追加/更新分离补丁已安装")

