from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

TreeSitterResourceState = Literal["missing", "downloading", "paused", "ready", "failed"]


class TreeSitterResourceStatus(BaseModel):
    version: str
    state: TreeSitterResourceState
    installed_languages: int
    total_languages: int
    downloaded_bytes: int
    total_bytes: int
    disk_bytes: int
    progress: int
    error: str | None = None
