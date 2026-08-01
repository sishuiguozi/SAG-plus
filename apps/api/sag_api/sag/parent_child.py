"""A4 父子分块：入库关联回填 + 父块向量过滤 + 检索父块上下文。

增量启用原则（用户选定方案）：
- 仅当 document_chunk_mode == "parent_child" 时，新入库文档才会生成父子块
  （切分在 chunking_compat._parent_child_assemble_chunks 完成）；
- 本模块负责三件事：
  1. 入库回填：_save_to_database 后把子块的 parent_id 写进 extra_data
     （chunk_id 由 zleap 在入库时生成 uuid，故在入库完成后回填）；
  2. 向量过滤：parent_chunk_vectorize=False 时父块只入库、不生成向量；
  3. 检索增强：命中子块时用父块内容替换（旧数据无标记则跳过，完全兼容）。
"""

from __future__ import annotations

from typing import Any

from sag_api.core.logging import get_logger

log = get_logger("sag.parent_child")

_patch_installed = False
_original: dict[str, Any] = {}


# ───────────────────────── 1. 入库 parent_id 回填 ─────────────────────────
def _extract_chunking_result(args: tuple, kwargs: dict) -> Any | None:
    if "chunking_result" in kwargs:
        return kwargs["chunking_result"]
    # _save_to_database(self, title, content, source_config_id, article_id, chunking_result, ...)
    if len(args) >= 5:
        return args[4]
    return None


async def _backfill_parent_ids(chunk_ids: list[str], chunking_result: Any) -> None:
    """把子块 extra_data.parent_id 回填为父块 uuid（同批，按 parent_group 序号关联）。"""
    if not chunk_ids or chunking_result is None:
        return
    drafts = list(getattr(chunking_result, "source_chunks", None) or [])
    if not drafts or len(drafts) != len(chunk_ids):
        return

    parent_ids: dict[Any, str] = {}
    for chunk_id, draft in zip(chunk_ids, drafts):
        meta = draft.metadata or {}
        if meta.get("chunk_type") == "parent" and meta.get("parent_group") is not None:
            parent_ids[meta["parent_group"]] = chunk_id
    if not parent_ids:
        return

    updates: list[tuple[str, str]] = []
    for chunk_id, draft in zip(chunk_ids, drafts):
        meta = draft.metadata or {}
        if meta.get("chunk_type") != "child":
            continue
        parent_id = parent_ids.get(meta.get("parent_group"))
        if parent_id and parent_id != chunk_id:
            updates.append((chunk_id, parent_id))
    if not updates:
        return

    try:
        from sqlalchemy import select
        from zleap.sag.db import get_session_factory
        from zleap.sag.db.models import SourceChunk

        ids = [chunk_id for chunk_id, _ in updates]
        sf = get_session_factory()
        async with sf() as session:
            rows = (
                await session.execute(select(SourceChunk).where(SourceChunk.id.in_(ids)))
            ).scalars().all()
            by_id = {row.id: row for row in rows}
            for chunk_id, parent_id in updates:
                row = by_id.get(chunk_id)
                if row is None:
                    continue
                extra = dict(row.extra_data or {})
                extra["parent_id"] = parent_id
                row.extra_data = extra
            await session.commit()
        log.info("父子分块入库关联回填完成 chunks=%d", len(updates))
    except Exception as exc:  # noqa: BLE001 - 回填失败不影响入库本身
        log.warning("父子分块 parent_id 回填失败（可忽略，检索将跳过父上下文）：%s", exc)


# ───────────────────────── 2. 父块向量过滤 ─────────────────────────
def _filtered_batch_index_chunks(
    self: Any,
    chunks: list,
    repo: Any,
    es_client: Any,
    embedding_batch_size: int,
    es_bulk_size: int,
    source_config_id: str,
) -> Any:
    """parent_chunk_vectorize=False 时父块只入库、不生成向量。"""
    from sag_api.core.config import settings

    original = _original.get("batch_index_chunks")
    if original is None:
        return chunks
    if settings.parent_chunk_vectorize is False:
        chunks = [
            c
            for c in chunks
            if (getattr(c, "extra_data", None) or {}).get("chunk_type") != "parent"
        ]
    return original(
        self,
        chunks,
        repo,
        es_client,
        embedding_batch_size,
        es_bulk_size,
        source_config_id,
    )


# ───────────────────────── 3. 检索父块上下文 ─────────────────────────
async def enrich_parent_context(sections: list) -> list:
    """命中子块时用父块内容替换，提供完整上下文。

    增量安全：旧数据 / 非父子数据没有 chunk_type/parent_id 标记，原样返回；
    任何 DB 异常也回退原结果，不影响检索可用性。
    """
    candidates = [s for s in sections if getattr(s, "chunk_id", None)]
    if not candidates:
        return sections
    ids = [s.chunk_id for s in candidates]
    try:
        from sqlalchemy import select
        from zleap.sag.db import get_session_factory
        from zleap.sag.db.models import SourceChunk

        sf = get_session_factory()
        async with sf() as session:
            rows = (
                await session.execute(select(SourceChunk).where(SourceChunk.id.in_(ids)))
            ).scalars().all()
        info: dict[str, tuple[dict, str, str]] = {
            row.id: (
                dict(row.extra_data or {}),
                (row.heading or "").strip(),
                (row.content or row.raw_content or "").strip(),
            )
            for row in rows
        }
    except Exception:  # noqa: BLE001
        return sections

    parent_id_by_child: dict[str, str] = {}
    for sec in candidates:
        extra = info.get(sec.chunk_id, ({}, "", ""))[0]
        if extra.get("chunk_type") == "child" and extra.get("parent_id"):
            parent_id_by_child[sec.chunk_id] = str(extra["parent_id"])
    if not parent_id_by_child:
        return sections

    parent_ids: set[str] = set(parent_id_by_child.values())
    # 结果中已存在的父块也要取内容（用于去重判断）
    for sec in candidates:
        extra = info.get(sec.chunk_id, ({}, "", ""))[0]
        if extra.get("chunk_type") == "parent" and sec.chunk_id:
            parent_ids.add(sec.chunk_id)

    parent_info: dict[str, tuple[str, str]] = {}
    try:
        from sqlalchemy import select
        from zleap.sag.db import get_session_factory
        from zleap.sag.db.models import SourceChunk

        sf = get_session_factory()
        async with sf() as session:
            rows = (
                await session.execute(
                    select(SourceChunk).where(SourceChunk.id.in_(list(parent_ids)))
                )
            ).scalars().all()
        parent_info = {
            row.id: ((row.heading or "").strip(), (row.content or row.raw_content or "").strip())
            for row in rows
        }
    except Exception:  # noqa: BLE001
        pass

    parent_hits = {
        sec.chunk_id
        for sec in candidates
        if sec.chunk_id in parent_ids
        and (info.get(sec.chunk_id, ({}, "", ""))[0]).get("chunk_type") == "parent"
    }

    enriched: list = []
    for sec in sections:
        parent_id = parent_id_by_child.get(sec.chunk_id or "")
        if parent_id and parent_id in parent_hits:
            # 父块已作为独立命中存在，丢弃重复子块
            continue
        if parent_id and parent_id in parent_info:
            heading, content = parent_info[parent_id]
            if content:
                enriched.append(
                    sec.model_copy(update={"heading": heading or sec.heading, "content": content})
                )
                continue
        enriched.append(sec)
    return enriched


# ───────────────────────── 安装 / 卸载 ─────────────────────────
def install_parent_child_loader_patch() -> None:
    """安装 A4 入库回填与向量过滤补丁（应用层，幂等）。"""
    global _patch_installed
    if _patch_installed:
        return
    from zleap.sag.modules.load.loader import BaseLoader, DocumentLoader

    # DocumentLoader 自己定义了 _save_to_database（覆盖 BaseLoader），两者都要处理：
    # 实际入库走 DocumentLoader，因此其独立定义必须一并包装；BaseLoader 兜底其他子类。
    for owner in (DocumentLoader, BaseLoader):
        current_save = owner.__dict__.get("_save_to_database") or getattr(owner, "_save_to_database", None)
        if not callable(current_save) or getattr(current_save, "_sag_api_parent_child", False):
            continue
        if "_save_to_database" in owner.__dict__:
            _original[f"save_to_database::{owner.__name__}"] = current_save
        else:
            _original.setdefault("save_to_database", current_save)

        async def patched_save_to_database(self: Any, *args: Any, **kwargs: Any) -> Any:
            original = (
                _original.get(f"save_to_database::{type(self).__name__}")
                or _original.get("save_to_database")
            )
            result = await original(self, *args, **kwargs)
            chunk_ids = result[1] if isinstance(result, tuple) and len(result) >= 2 else []
            chunking_result = _extract_chunking_result(args, kwargs)
            await _backfill_parent_ids(chunk_ids, chunking_result)
            return result

        patched_save_to_database._sag_api_parent_child = True  # type: ignore[attr-defined]
        setattr(owner, "_save_to_database", patched_save_to_database)

    index_current = getattr(BaseLoader, "_batch_index_chunks", None)
    if callable(index_current) and not getattr(index_current, "_sag_api_parent_child_filter", False):
        _original["batch_index_chunks"] = index_current

        def patched_batch_index_chunks(self: Any, chunks: list, repo: Any, es_client: Any,
                                       embedding_batch_size: int, es_bulk_size: int,
                                       source_config_id: str) -> Any:
            return _filtered_batch_index_chunks(
                self, chunks, repo, es_client, embedding_batch_size, es_bulk_size, source_config_id
            )

        patched_batch_index_chunks._sag_api_parent_child_filter = True  # type: ignore[attr-defined]
        BaseLoader._batch_index_chunks = patched_batch_index_chunks

    _patch_installed = True
    log.info("父子分块入库补丁已启用（parent_id 回填 + 父块向量过滤）")


def uninstall_parent_child_loader_patch() -> None:
    """卸载 A4 补丁（主要用于测试）。"""
    global _patch_installed
    if not _patch_installed:
        return
    from zleap.sag.modules.load.loader import BaseLoader

    from zleap.sag.modules.load.loader import BaseLoader, DocumentLoader

    for owner in (DocumentLoader, BaseLoader):
        saved = _original.get(f"save_to_database::{owner.__name__}")
        if saved is not None:
            owner._save_to_database = saved
    if _original.get("save_to_database") is not None:
        BaseLoader._save_to_database = _original["save_to_database"]
    if _original.get("batch_index_chunks") is not None:
        BaseLoader._batch_index_chunks = _original["batch_index_chunks"]
    _patch_installed = False
    log.info("父子分块入库补丁已卸载")
