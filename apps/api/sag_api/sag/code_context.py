"""Code retrieval helpers: revision filtering + compact parent context."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sag_api.core.logging import get_logger

log = get_logger("sag.code_context")

_CODE_PARENT = "code_parent"
_CODE_CHILD = "code_child"


def _meta_from_section(section: Any) -> dict:
    extra = getattr(section, "metadata", None)
    if isinstance(extra, dict):
        return extra
    extra = getattr(section, "extra_data", None)
    if isinstance(extra, dict):
        return extra
    return {}


async def _load_chunk_rows(chunk_ids: list[str]) -> dict[str, Any]:
    if not chunk_ids:
        return {}
    from sqlalchemy import select
    from zleap.sag.db import get_session_factory
    from zleap.sag.db.models import SourceChunk

    sf = get_session_factory()
    async with sf() as session:
        rows = (
            await session.execute(select(SourceChunk).where(SourceChunk.id.in_(chunk_ids)))
        ).scalars().all()
    return {row.id: row for row in rows}


async def _map_sag_config_to_app_source(config_ids: Iterable[str]) -> dict[str, str]:
    ids = sorted({str(x) for x in config_ids if x})
    if not ids:
        return {}
    from sqlalchemy import select

    from sag_api.core.db import SessionLocal
    from sag_api.db.models import Source

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Source.id, Source.sag_source_config_id).where(
                    Source.sag_source_config_id.in_(ids)
                )
            )
        ).all()
    return {
        str(sag_id): str(app_id)
        for app_id, sag_id in rows
        if app_id and sag_id
    }


async def _current_code_hash_map(pairs: Iterable[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """Map (app_source_id, relative_path) -> current content_sha256."""
    items = [(s, p) for s, p in pairs if s and p]
    if not items:
        return {}
    from sqlalchemy import select

    from sag_api.core.db import SessionLocal
    from sag_api.db.models import Document

    source_ids = sorted({s for s, _ in items})
    paths = sorted({p for _, p in items})
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Document.source_id, Document.relative_path, Document.content_sha256).where(
                    Document.source_id.in_(source_ids),
                    Document.relative_path.in_(paths),
                    Document.content_sha256.is_not(None),
                )
            )
        ).all()
    out: dict[tuple[str, str], str] = {}
    for source_id, relative_path, content_sha256 in rows:
        if source_id and relative_path and content_sha256:
            out[(str(source_id), str(relative_path))] = str(content_sha256)
    return out


def _section_app_source_id(
    section: Any,
    meta: dict,
    *,
    app_source_id: str | None,
    config_to_app: dict[str, str],
) -> str:
    if app_source_id:
        return str(app_source_id)
    direct = getattr(section, "app_source_id", None) or meta.get("app_source_id")
    if direct:
        return str(direct)
    config_id = (
        getattr(section, "source_config_id", None)
        or meta.get("source_config_id")
        or ""
    )
    return str(config_to_app.get(str(config_id), "") or "")


async def filter_stale_code_sections(sections: list, *, app_source_id: str | None = None) -> list:
    """Drop code hits whose content_sha256 is not the document's current hash."""
    if not sections:
        return sections
    chunk_ids = [s.chunk_id for s in sections if getattr(s, "chunk_id", None)]
    rows = await _load_chunk_rows(chunk_ids)

    config_ids: set[str] = set()
    prepared: list[tuple[Any, dict]] = []
    for section in sections:
        row = rows.get(getattr(section, "chunk_id", None) or "")
        meta = dict(getattr(row, "extra_data", None) or {}) if row is not None else _meta_from_section(section)
        prepared.append((section, meta))
        cfg = getattr(section, "source_config_id", None) or meta.get("source_config_id")
        if cfg:
            config_ids.add(str(cfg))
        if row is not None and getattr(row, "source_config_id", None):
            config_ids.add(str(row.source_config_id))

    config_to_app = {} if app_source_id else await _map_sag_config_to_app_source(config_ids)

    pairs: list[tuple[str, str]] = []
    for section, meta in prepared:
        rel = str(meta.get("relative_path") or "").strip()
        sha = str(meta.get("content_sha256") or "").strip()
        if not rel or not sha:
            continue
        sid = _section_app_source_id(section, meta, app_source_id=app_source_id, config_to_app=config_to_app)
        if sid:
            pairs.append((sid, rel))
    if not pairs:
        return sections

    current = await _current_code_hash_map(pairs)
    kept = []
    for section, meta in prepared:
        rel = str(meta.get("relative_path") or "").strip()
        sha = str(meta.get("content_sha256") or "").strip()
        if not rel or not sha:
            kept.append(section)
            continue
        sid = _section_app_source_id(section, meta, app_source_id=app_source_id, config_to_app=config_to_app)
        current_sha = current.get((sid, rel)) if sid else None
        if current_sha and current_sha != sha:
            continue
        kept.append(section)
    return kept


def _compact_parent_prefix(parent_row: Any, child_meta: dict) -> str:
    parent_meta = dict(getattr(parent_row, "extra_data", None) or {})
    rel = child_meta.get("relative_path") or parent_meta.get("relative_path") or ""
    ancestors = child_meta.get("ancestor_path") or parent_meta.get("ancestor_path") or []
    if isinstance(ancestors, str):
        ancestor_text = ancestors
    else:
        ancestor_text = " / ".join(str(x) for x in ancestors if x)
    parent_sig = (
        parent_meta.get("signature")
        or getattr(parent_row, "heading", None)
        or parent_meta.get("qualified_name")
        or ""
    )
    lines = []
    if rel:
        lines.append(f"File: {rel}")
    if ancestor_text:
        lines.append(f"Symbol path: {ancestor_text}")
    if parent_sig:
        lines.append(f"Parent: {parent_sig}")
    return "\n".join(lines).strip()


async def enrich_code_context(sections: list) -> list:
    """Prefix compact parent context for code children; keep exact child source."""
    if not sections:
        return sections
    chunk_ids = [s.chunk_id for s in sections if getattr(s, "chunk_id", None)]
    rows = await _load_chunk_rows(chunk_ids)

    child_to_parent: dict[str, str] = {}
    parent_ids: set[str] = set()
    for section in sections:
        cid = getattr(section, "chunk_id", None)
        if not cid:
            continue
        row = rows.get(cid)
        meta = dict(getattr(row, "extra_data", None) or {}) if row is not None else {}
        if meta.get("chunk_type") == _CODE_CHILD:
            parent_id = meta.get("parent_id")
            if parent_id:
                child_to_parent[cid] = str(parent_id)
                parent_ids.add(str(parent_id))
        elif meta.get("chunk_type") == _CODE_PARENT:
            parent_ids.add(cid)

    if not child_to_parent:
        return sections

    missing_parent_ids = [pid for pid in parent_ids if pid not in rows]
    if missing_parent_ids:
        rows.update(await _load_chunk_rows(missing_parent_ids))

    parent_hit_scores = {
        s.chunk_id: float(getattr(s, "score", 0.0) or 0.0)
        for s in sections
        if getattr(s, "chunk_id", None) in parent_ids
        and (
            dict(getattr(rows.get(s.chunk_id), "extra_data", None) or {}).get("chunk_type")
            == _CODE_PARENT
        )
    }

    enriched: list = []
    for section in sections:
        cid = getattr(section, "chunk_id", None) or ""
        row = rows.get(cid)
        meta = dict(getattr(row, "extra_data", None) or {}) if row is not None else {}
        chunk_type = meta.get("chunk_type")

        if chunk_type == _CODE_CHILD and cid in child_to_parent:
            parent_id = child_to_parent[cid]
            parent_score = parent_hit_scores.get(parent_id)
            child_score = float(getattr(section, "score", 0.0) or 0.0)
            if parent_score is not None and parent_score >= child_score:
                continue
            parent_row = rows.get(parent_id)
            child_body = (
                getattr(section, "content", None) or getattr(row, "content", None) or ""
            ).strip()
            if parent_row is not None and child_body:
                prefix = _compact_parent_prefix(parent_row, meta)
                content = f"{prefix}\n\n{child_body}".strip() if prefix else child_body
                heading = (
                    getattr(section, "heading", None)
                    or meta.get("qualified_name")
                    or getattr(row, "heading", "")
                    or ""
                )
                if hasattr(section, "model_copy"):
                    enriched.append(
                        section.model_copy(update={"content": content, "heading": heading})
                    )
                else:
                    section.content = content
                    section.heading = heading
                    enriched.append(section)
                continue

        if chunk_type == _CODE_PARENT and cid in parent_hit_scores:
            child_better = any(
                child_to_parent.get(getattr(other, "chunk_id", None) or "") == cid
                and float(getattr(other, "score", 0.0) or 0.0) > parent_hit_scores.get(cid, 0.0)
                for other in sections
            )
            if child_better:
                continue

        enriched.append(section)
    return enriched


async def apply_code_retrieval_pipeline(
    sections: list,
    *,
    app_source_id: str | None = None,
) -> list:
    """Filter stale code revisions, then attach compact parent context."""
    try:
        filtered = await filter_stale_code_sections(sections, app_source_id=app_source_id)
    except Exception:  # noqa: BLE001
        log.exception("code revision filter failed")
        filtered = sections
    try:
        return await enrich_code_context(filtered)
    except Exception:  # noqa: BLE001
        log.exception("code context enrich failed")
        return filtered
