"""LanceDB BM25（FTS）稀疏召回：独立于向量通道的词法检索。

lancedb 的 FTS（tantivy 后端）为 source_chunks 的 content 建全文索引，按 BM25
打分。与向量通道互补：专有名词、编号、源码符号、精确短语等场景召回更准。

索引维护：
- 首次查询懒建（约 2~3 秒 / 4.6 万行）；
- 表行数增长 >10% 或距上次重建 >30 分钟时自动重建（replace）；
- 任何失败（tantivy 未装 / 索引不可用）静默回退到 grep 词法通道。
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

from sag_api.core.logging import get_logger

log = get_logger("sag.lancedb_fts")

_state: dict[str, Any] = {"indexed_rows": 0, "indexed_at": 0.0}
_lock = threading.Lock()


def _db_uri() -> str:
    from sag_api.core.config import settings

    return os.path.join(settings.data_dir, "lancedb")


def _table() -> Any:
    import lancedb

    return lancedb.connect(_db_uri()).open_table("source_chunks")


def _index_stale(tbl: Any) -> bool:
    if _state["indexed_rows"] <= 0:
        return True
    try:
        rows = tbl.count_rows()
    except Exception:  # noqa: BLE001
        return True
    grown = rows > _state["indexed_rows"] * 1.1
    stale = time.monotonic() - _state["indexed_at"] > 1800
    return bool(grown or stale)


def ensure_fts_index(*, force: bool = False) -> bool:
    """确保 source_chunks 的 FTS 索引存在（幂等 + 自动重建）。返回是否可用。"""
    try:
        import lancedb  # noqa: F401
    except ImportError:
        return False
    with _lock:
        try:
            tbl = _table()
            if not force and _state["indexed_rows"] > 0 and not _index_stale(tbl):
                return True
            tbl.create_fts_index("content", replace=True)
            _state["indexed_rows"] = int(tbl.count_rows())
            _state["indexed_at"] = time.monotonic()
            log.info("LanceDB FTS 索引就绪 rows=%s", _state["indexed_rows"])
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("LanceDB FTS 索引不可用（回退 grep）：%s", exc)
            return False


def fts_search(
    source_config_ids: list[str],
    query: str,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """BM25 稀疏召回（限定信源），返回 chunk 记录（含 BM25 分 _score）。"""
    if not query.strip():
        return []
    if not ensure_fts_index():
        return []
    try:
        tbl = _table()
        q = tbl.search(query, query_type="fts").limit(max(1, limit))
        if source_config_ids:
            ids = [str(x).replace("'", "''") for x in source_config_ids if x]
            if ids:
                q = q.where(
                    "source_config_id IN (" + ",".join(f"'{i}'" for i in ids) + ")",
                    prefilter=True,
                )
        rows = q.select(
            ["chunk_id", "heading", "content", "source_config_id", "_score"]
        ).to_list()
        return rows
    except Exception as exc:  # noqa: BLE001
        log.warning("FTS 检索失败（回退 grep）：%s", exc)
        return []
