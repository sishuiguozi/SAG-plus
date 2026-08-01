from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sag_api.enums import ConnectorKind, SourceStatus, SourceType


class ConnectorOut(BaseModel):
    kind: str
    title: str
    description: str
    supports_sync: bool
    config_fields: list[dict[str, Any]]


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    connector_kind: ConnectorKind = ConnectorKind.FILE_UPLOAD
    config: dict[str, Any] = Field(default_factory=dict)


class SourceUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    status: SourceStatus | None = None


class SourceCodeConfig(BaseModel):
    """Per-knowledge-base policy for code entity/event extraction."""

    llm_extraction_mode: Literal["off", "comments", "all"] = "comments"


class VectorBackfillOut(BaseModel):
    """辅助向量索引补齐信号（SAG-OPT-107）。

    ``status``:
      - ``deferred``    -- 辅助向量写入被整体延迟（SAG_AUX_VECTOR_DEFERRED_ENABLED）。
      - ``backfilling`` -- 核心关系数据已写入，辅助索引仍在补齐。
      - ``complete``    -- 无待补辅助向量记录。
      - ``unknown``     -- 该信源尚未关联引擎配置。
    """

    status: Literal["deferred", "backfilling", "complete", "unknown"] = "unknown"
    pending_records: int = 0
    by_table: dict[str, int] = Field(default_factory=dict)


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str
    source_type: SourceType
    connector_kind: ConnectorKind
    status: SourceStatus
    document_count: int
    ready_document_count: int = 0
    pending_document_count: int = 0
    paused_document_count: int = 0
    failed_document_count: int = 0
    chunk_count: int
    event_count: int
    vector_backfill: VectorBackfillOut = Field(default_factory=VectorBackfillOut)
    created_at: datetime
    updated_at: datetime


class SourceDeleteRequest(BaseModel):
    """删除信源需要账户密码确认。"""

    password: str = Field(min_length=1, max_length=200)


class IngestStatsOut(BaseModel):
    total_files: int = 0
    indexed_files: int = 0
    pending_files: int = 0
    failed_files: int = 0
    paused_files: int = 0
    loading_files: int = 0
    extracting_files: int = 0
    active_files: int = 0
    queued_jobs: int = 0
    running_jobs: int = 0
    docs_per_minute: float = 0.0
    docs_per_hour: float = 0.0
    chunks_per_minute: float = 0.0
    events_per_minute: float = 0.0
    vector_items_per_minute: float = 0.0
    eta_seconds: int | None = None
    stalled_reason: str | None = None
    sample_window_minutes: int = 10
