from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from sag_api.core.deps import get_current_user
from sag_api.db.models import User
from sag_api.schemas.tree_sitter import TreeSitterResourceStatus

router = APIRouter(prefix="/system/tree-sitter", tags=["system"])


def _manager(request: Request):
    return request.app.state.tree_sitter_manager


@router.get("", response_model=TreeSitterResourceStatus)
async def status(
    request: Request,
    _user: User = Depends(get_current_user),
) -> TreeSitterResourceStatus:
    return _manager(request).status()


@router.post("/download", response_model=TreeSitterResourceStatus)
async def download(
    request: Request,
    _user: User = Depends(get_current_user),
) -> TreeSitterResourceStatus:
    manager = _manager(request)
    await manager.start_download()
    return manager.status()


@router.post("/pause", response_model=TreeSitterResourceStatus)
async def pause(
    request: Request,
    _user: User = Depends(get_current_user),
) -> TreeSitterResourceStatus:
    manager = _manager(request)
    await manager.pause()
    return manager.status()


@router.post("/resume", response_model=TreeSitterResourceStatus)
async def resume(
    request: Request,
    _user: User = Depends(get_current_user),
) -> TreeSitterResourceStatus:
    manager = _manager(request)
    await manager.resume()
    return manager.status()


@router.post("/repair", response_model=TreeSitterResourceStatus)
async def repair(
    request: Request,
    _user: User = Depends(get_current_user),
) -> TreeSitterResourceStatus:
    manager = _manager(request)
    await manager.repair()
    return manager.status()
