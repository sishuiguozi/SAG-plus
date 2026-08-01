from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from sag_api.db.base import Base, IDMixin, TimestampMixin, UTCDateTime


class VectorWriteJob(IDMixin, TimestampMixin, Base):
    """Durable queue item for local vector-store writes.

    Document extraction commits authoritative events/entities to the relational
    zleap database first. Vector writes are retried here so transient LanceDB or
    embedding failures do not poison the document-processing checkpoint.
    """

    __tablename__ = "vector_write_jobs"
    __table_args__ = (
        Index("ix_vector_write_status_next", "status", "next_run_at"),
        Index("ix_vector_write_source_status", "source_config_id", "status"),
    )

    kind: Mapped[str] = mapped_column(String(32), default="event_sync", index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    source_config_id: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column("payload_json", JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=8)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(160), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    embedding_version: Mapped[str] = mapped_column(String(64), default="default")
    parent_batch_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    superseded_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


# Record-level queue V2 states. ``embedding``/``ready_to_write`` are reserved
# for the future split of embedding and LanceDB write phases; the writer still
# keeps job-level states today, and item states mirror the owning job.
VECTOR_ITEM_STATUSES = (
    "queued",
    "embedding",
    "ready_to_write",
    "writing",
    "succeeded",
    "retry",
    "failed",
)
VECTOR_ITEM_ACTIVE_STATUSES = ("queued", "embedding", "ready_to_write", "writing", "retry")


class VectorWriteItem(IDMixin, TimestampMixin, Base):
    """Record-level detail for the durable vector-write queue (V2).

    One row represents one pending write of ``(table_name, record_id,
    embedding_version)``. The partial unique index guarantees at most one
    active row per record so retries and duplicate submissions never double
    write the same vector; terminal rows (succeeded/failed) are kept for audit
    and never physically deleted.
    """

    __tablename__ = "vector_write_items"
    __table_args__ = (
        Index("ix_vector_write_items_job_status", "job_id", "status"),
        Index("ix_vector_write_items_table_status", "table_name", "status"),
        Index("ix_vector_write_items_source_status", "source_config_id", "status"),
        Index(
            "uq_vector_write_items_active",
            "table_name",
            "record_id",
            "embedding_version",
            unique=True,
            sqlite_where=text(
                "status IN ('queued','embedding','ready_to_write','writing','retry')"
            ),
        ),
    )

    job_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("vector_write_jobs.id"), index=True
    )
    table_name: Mapped[str] = mapped_column(String(64), index=True)
    record_id: Mapped[str] = mapped_column(String(128), index=True)
    embedding_version: Mapped[str] = mapped_column(String(64), default="default")
    source_config_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_run_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True, index=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(160), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    payload: Mapped[dict] = mapped_column("payload_json", JSON, default=dict)
