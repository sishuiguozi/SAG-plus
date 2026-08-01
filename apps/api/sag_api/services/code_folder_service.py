"""Plan and upload helpers for recursive local code-folder import."""

from __future__ import annotations

import hashlib
import os
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.code_ingest.file_policy import route_file
from sag_api.core.config import settings
from sag_api.core.errors import ConflictError, ValidationError
from sag_api.db.models import Document, Source
from sag_api.jobs import JobQueue
from sag_api.schemas.code_folder import (
    CodeFolderPlanItemIn,
    CodeFolderPlanItemOut,
    CodeFolderPlanRequest,
    CodeFolderPlanResponse,
    CodeFolderUploadOut,
)
from sag_api.services.document_service import (
    find_document_by_relative_path,
    stage_code_document_upload,
)


_MAX_PLAN_ITEMS = 20_000
_MAX_PATH_LEN = 1024
_MAX_DECLARED_TOTAL_BYTES = 5 * 1024 * 1024 * 1024  # 5 GiB declared volume


def _normalize_root_name(root_name: str) -> str:
    name = (root_name or "").replace("\\", "/").strip().strip("/")
    if not name or "/" in name or name in {".", ".."}:
        raise ValidationError("根目录名无效")
    return name


def _normalize_relative_path(path: str) -> str:
    value = (path or "").replace("\\", "/").strip()
    while value.startswith("./"):
        value = value[2:]
    value = value.lstrip("/")
    if not value or ".." in value.split("/") or any(part == "" for part in value.split("/")):
        raise ValidationError(f"非法相对路径: {path}")
    if len(value) > _MAX_PATH_LEN:
        raise ValidationError("相对路径过长")
    return value


def _ensure_under_root(relative_path: str, root_name: str) -> str:
    rel = _normalize_relative_path(relative_path)
    root = _normalize_root_name(root_name)
    if rel == root or not (rel == root or rel.startswith(root + "/")):
        # must begin with root directory name
        if not rel.startswith(root + "/") and rel != root:
            raise ValidationError(f"路径必须以根目录名开头: {root}/...")
    if rel == root:
        raise ValidationError("不能上传根目录本身")
    return rel


async def _existing_code_map(session: AsyncSession, source_id: str) -> dict[str, tuple[str | None, str]]:
    rows = (
        await session.execute(
            select(Document.id, Document.relative_path, Document.content_sha256).where(
                Document.source_id == source_id,
                Document.relative_path.is_not(None),
            )
        )
    ).all()
    out: dict[str, tuple[str | None, str]] = {}
    for doc_id, relative_path, content_sha256 in rows:
        if relative_path:
            out[str(relative_path)] = (content_sha256, str(doc_id))
    return out


def _reject_reason(
    relative_path: str,
    size_bytes: int,
    sample: bytes | None = None,
    *,
    enforce_default_selection: bool = True,
) -> str | None:
    if size_bytes > settings.max_upload_mb * 1024 * 1024:
        return f"超过单文件上限 {settings.max_upload_mb}MB"
    decision = route_file(
        relative_path,
        context="code_folder",
        content_sample=sample if sample is not None else b"",
    )
    if decision.route == "skip":
        return decision.reason or "文件不适合入库"
    if (
        enforce_default_selection
        and not decision.default_selected
        and decision.route in {"mineru", "markitdown"}
    ):
        # PDF/Office stay unselected in plan; explicit upload may still accept them.
        return decision.reason or "默认不导入此类文件，请手动确认后上传"
    return None


async def plan_code_folder(
    session: AsyncSession,
    source: Source,
    request: CodeFolderPlanRequest,
) -> CodeFolderPlanResponse:
    root = _normalize_root_name(request.root_name)
    if len(request.items) > _MAX_PLAN_ITEMS:
        raise ValidationError(f"清单项过多，最多 {_MAX_PLAN_ITEMS}")
    total = sum(int(item.size_bytes or 0) for item in request.items)
    if total > _MAX_DECLARED_TOTAL_BYTES:
        raise ValidationError("声明的文件夹总体积过大")

    existing = await _existing_code_map(session, source.id)
    out_items: list[CodeFolderPlanItemOut] = []
    for raw in request.items:
        try:
            rel = _ensure_under_root(raw.relative_path, root)
            sha = (raw.sha256 or "").strip().lower()
            if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha):
                raise ValidationError("sha256 格式无效")
            reason = _reject_reason(rel, int(raw.size_bytes or 0))
            if reason:
                out_items.append(
                    CodeFolderPlanItemOut(
                        relative_path=rel,
                        sha256=sha,
                        size_bytes=int(raw.size_bytes or 0),
                        status="rejected",
                        reason=reason,
                    )
                )
                continue
            prev = existing.get(rel)
            if prev is None:
                status = "new"
                doc_id = None
            else:
                prev_sha, doc_id = prev
                status = "unchanged" if prev_sha and prev_sha.lower() == sha else "changed"
            out_items.append(
                CodeFolderPlanItemOut(
                    relative_path=rel,
                    sha256=sha,
                    size_bytes=int(raw.size_bytes or 0),
                    status=status,
                    document_id=doc_id,
                )
            )
        except ValidationError as exc:
            out_items.append(
                CodeFolderPlanItemOut(
                    relative_path=str(raw.relative_path),
                    sha256=str(raw.sha256 or ""),
                    size_bytes=int(raw.size_bytes or 0),
                    status="rejected",
                    reason=str(getattr(exc, "message", None) or exc),
                )
            )

    counts = Counter(item.status for item in out_items)
    return CodeFolderPlanResponse(
        root_name=root,
        items=out_items,
        counts={
            "new": int(counts.get("new", 0)),
            "changed": int(counts.get("changed", 0)),
            "unchanged": int(counts.get("unchanged", 0)),
            "rejected": int(counts.get("rejected", 0)),
        },
    )


async def upload_code_folder_file(
    session: AsyncSession,
    source: Source,
    *,
    relative_path: str,
    root_name: str,
    declared_sha256: str,
    data: bytes,
    filename: str | None,
    content_type: str | None,
    job_queue: JobQueue,
) -> CodeFolderUploadOut:
    rel = _ensure_under_root(relative_path, root_name)
    if not data:
        raise ValidationError("文件内容为空")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise ValidationError(f"文件超过 {settings.max_upload_mb}MB 上限")
    digest = hashlib.sha256(data).hexdigest()
    declared = (declared_sha256 or "").strip().lower()
    if declared and declared != digest:
        raise ValidationError("文件内容与声明的 sha256 不一致")
    reason = _reject_reason(rel, len(data), sample=data[:8192], enforce_default_selection=False)
    if reason:
        raise ValidationError(reason)

    decision = route_file(rel, context="code_folder", content_sample=data[:8192])
    language = decision.language
    try:
        document, job = await stage_code_document_upload(
            session,
            source,
            filename=filename or os.path.basename(rel) or "code",
            content_type=content_type or "application/octet-stream",
            data=data,
            upload_dir=settings.upload_dir,
            job_queue=job_queue,
            relative_path=rel,
            code_language=language,
        )
    except ConflictError:
        existing = await find_document_by_relative_path(session, source.id, rel)
        if existing is None:
            raise
        return CodeFolderUploadOut(
            document_id=existing.id,
            job_id=None,
            relative_path=rel,
            content_sha256=digest,
            status="unchanged",
        )

    status = "queued_replacement" if (job.payload or {}).get("code_replacement") else "created"
    return CodeFolderUploadOut(
        document_id=document.id,
        job_id=job.id,
        relative_path=rel,
        content_sha256=digest,
        status=status,
    )
