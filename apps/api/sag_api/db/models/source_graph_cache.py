from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from sag_api.db.base import Base, IDMixin, TimestampMixin, UTCDateTime


class SourceGraphCache(IDMixin, TimestampMixin, Base):
    """Materialized source graph slice for fast 2D/3D graph loading."""

    __tablename__ = "source_graph_caches"
    __table_args__ = (
        UniqueConstraint("source_id", "cache_key", name="uq_source_graph_cache_key"),
        Index("ix_source_graph_cache_source_revision", "source_id", "revision"),
        Index("ix_source_graph_cache_source_key", "source_id", "cache_key"),
    )

    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    source_config_id: Mapped[str] = mapped_column(String(64), index=True)
    cache_key: Mapped[str] = mapped_column(String(96))
    revision: Mapped[str] = mapped_column(String(160), index=True)
    document_limit: Mapped[int] = mapped_column(Integer)
    event_limit: Mapped[int] = mapped_column(Integer)
    entity_limit: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column("payload_json", JSON, default=dict)
    built_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
