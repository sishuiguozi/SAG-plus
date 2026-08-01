"""Schemas for local code-folder incremental import."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CodeFolderPlanItemIn(BaseModel):
    relative_path: str = Field(min_length=1, max_length=1024)
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0, le=1024 * 1024 * 1024)


class CodeFolderPlanRequest(BaseModel):
    root_name: str = Field(min_length=1, max_length=255)
    items: list[CodeFolderPlanItemIn] = Field(default_factory=list, max_length=20_000)


class CodeFolderPlanItemOut(BaseModel):
    relative_path: str
    sha256: str
    size_bytes: int
    status: Literal["new", "changed", "unchanged", "rejected"]
    reason: str = ""
    document_id: str | None = None


class CodeFolderPlanResponse(BaseModel):
    root_name: str
    items: list[CodeFolderPlanItemOut]
    counts: dict[str, int]


class CodeFolderUploadOut(BaseModel):
    document_id: str
    job_id: str | None = None
    relative_path: str
    content_sha256: str
    status: Literal["created", "unchanged", "queued_replacement"]
