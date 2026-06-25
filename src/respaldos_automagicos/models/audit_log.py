"""Audit log persistence model."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from respaldos_automagicos.database import Base
from respaldos_automagicos.models.mixins import utc_now


class AuditLog(Base):
    """Event captured for traceability of backup and restore activity."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("backup_groups.id", ondelete="SET NULL"),
        index=True,
    )
    watched_directory_id: Mapped[int | None] = mapped_column(
        ForeignKey("watched_directories.id", ondelete="SET NULL"),
        index=True,
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[str | None] = mapped_column(Text)
