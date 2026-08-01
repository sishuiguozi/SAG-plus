"""HTTP API for local code-folder plan + upload."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.db import get_session
from sag_api.core.deps import get_current_user, get_job_queue
from sag_api.db.models import User
from sag_api.jobs import JobQueue
from sag_api.schemas.code_folder import CodeFolderPlanRequest, CodeFolderPlanResponse, CodeFolderUploadOut
from sag_api.services.code_folder_service import plan_code_folder, upload_code_folder_file
from sag_api.services.source_service import get_source

router = APIRouter(prefix="/sources/{source_id}/code-folder", tags=["code-folder"])


@router.post("/plan", response_model=CodeFolderPlanResponse)
async def plan_(
    source_id: str,
    body: CodeFolderPlanRequest,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CodeFolderPlanResponse:
    source = await get_source(session, source_id)
    return await plan_code_folder(session, source, body)


@router.post("/upload", response_model=CodeFolderUploadOut, status_code=201)
async def upload_(
    source_id: str,
    relative_path: str = Form(...),
    root_name: str = Form(...),
    sha256: str = Form(...),
    file: UploadFile = File(...),
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    job_queue: JobQueue = Depends(get_job_queue),
) -> CodeFolderUploadOut:
    source = await get_source(session, source_id)
    data = await file.read()
    return await upload_code_folder_file(
        session,
        source,
        relative_path=relative_path,
        root_name=root_name,
        declared_sha256=sha256,
        data=data,
        filename=file.filename,
        content_type=file.content_type,
        job_queue=job_queue,
    )
