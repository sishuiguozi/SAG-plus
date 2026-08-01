from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from sag_api.db.base import Base, IDMixin, TimestampMixin, UTCDateTime


class UniverseGraphCache(IDMixin, TimestampMixin, Base):
    """Materialized interactive universe graph page.

    Stores source timeline pages, node expansions, and node details so the
    exploration UI can reuse stable pages and avoid competing with ingestion.
    """

    __tablename__ = "universe_graph_caches"
    __table_args__ = (
        UniqueConstraint("source_id", "cache_key", name="uq_universe_graph_cache_key"),
        Index("ix_universe_graph_cache_source_revision", "source_id", "revision"),
        Index("ix_universe_graph_cache_source_key", "source_id", "cache_key"),
        Index("ix_universe_graph_cache_kind", "source_id", "kind"),
    )

    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    source_config_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    cache_key: Mapped[str] = mapped_column(String(160))
    revision: Mapped[str] = mapped_column(String(160), index=True)
    request_hash: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict] = mapped_column("payload_json", JSON, default=dict)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    built_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
