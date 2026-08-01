from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from sag_api.db.base import Base, UTCDateTime


class MaintenanceLease(Base):
    """Exclusive operational lease for local storage maintenance tasks."""

    __tablename__ = "maintenance_leases"

    name: Mapped[str] = mapped_column(String(96), primary_key=True)
    owner: Mapped[str] = mapped_column(String(160), nullable=False)
    purpose: Mapped[str] = mapped_column(String(160), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    lease_metadata: Mapped[dict] = mapped_column("metadata_json", JSON, default=dict)
