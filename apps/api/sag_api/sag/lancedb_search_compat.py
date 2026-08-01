"""SAG-OPT-302：LanceDB ANN 检索参数兼容层。

上游 ``zleap.sag.core.storage.lancedb_store.LanceDBStore.vector_search`` 只设置
``distance_type("cosine")`` 与 ``limit``，未设置 IVF/HNSW 探测数与精排因子；在
ANN 索引（默认 nprobes 较低）下召回率不足（实测 Recall@10 ≈ 0.78，低于验收 95%）。

本模块在应用边界 monkey-patch ``LanceDBStore.vector_search``（不修改 site-packages）：

- ``refine_factor``：对 ANN 候选做精确距离精排，实测 nprobes=16 + refine_factor=5
  Recall@10 = 0.998、P95 ≈ 18ms。
- ``nprobes``：可配置 IVF 探测数（0 = 上游默认）。
- 功能开关：``SAG_LANCEDB_ANN_ENABLED=false`` 整体回退上游原始路径。
"""

from __future__ import annotations

from typing import Any

from sag_api.core.config import get_settings
from sag_api.core.logging import get_logger

log = get_logger("sag.lancedb_search_compat")

_PATCH_MARK = "_sag_api_ann_search_patch"


def install_zleap_sag_lancedb_ann_search_patch() -> None:
    """安装 ANN 检索参数补丁（幂等；不修改 site-packages 文件）。"""

    from zleap.sag.core.storage.lancedb_store import LanceDBStore

    original = LanceDBStore.vector_search
    if getattr(original, _PATCH_MARK, False):
        return

    async def _patched_vector_search(
        self: Any,
        index: str,
        field: str,
        vector: list[float],
        size: int = 10,
        filter_query: dict[str, Any] | None = None,
        routing: str | None = None,
        include_vector: bool = False,
    ) -> list[dict[str, Any]]:
        settings = get_settings()
        if not settings.lancedb_ann_enabled or not settings.lancedb_search_refine_factor:
            return await original(
                self, index, field, vector,
                size=size, filter_query=filter_query, routing=routing, include_vector=include_vector,
            )

        tbl = await self._open_table(index)
        if tbl is None:
            log.warning("向量检索:索引 '%s' 不存在,返回空结果", index)
            return []
        await self._cache_schema(tbl)
        q = tbl.query().nearest_to([float(x) for x in vector])
        from zleap.sag.core.storage.lancedb_store import _ident

        if _ident(field) in self._vector_columns(tbl):
            q = q.column(_ident(field))
        q = q.distance_type("cosine").limit(int(size))
        if int(settings.lancedb_search_nprobes) > 0:
            q = q.nprobes(int(settings.lancedb_search_nprobes))
        if int(settings.lancedb_search_refine_factor) > 0:
            q = q.refine_factor(int(settings.lancedb_search_refine_factor))
        where = self._translate_filter(tbl, filter_query)
        if where:
            q = q.where(where)
        if not include_vector:
            keep = [n for n, _ in self._schema_fields(tbl) if n not in self._vector_columns(tbl)]
            q = q.select([*keep, "_distance"])
        rows = await q.to_list()
        return [
            {**{k: v for k, v in r.items() if k != "_distance"}, "_score": 1.0 - r["_distance"]}
            for r in rows
        ]

    _patched_vector_search._sag_api_ann_search_patch = True  # type: ignore[attr-defined]
    LanceDBStore.vector_search = _patched_vector_search
    log.info("LanceDB ANN 检索参数补丁已安装（nprobes=%s, refine_factor=%s）",
             get_settings().lancedb_search_nprobes, get_settings().lancedb_search_refine_factor)
