"""Backup history persistence model."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from respaldos_automagicos.database import Base
from respaldos_automagicos.models.mixins import utc_now

if TYPE_CHECKING:
    from respaldos_automagicos.models.backup_group import BackupGroup
    from respaldos_automagicos.models.watched_directory import WatchedDirectory


class BackupHistory(Base):
    """Record of a generated or attempted backup."""

    __tablename__ = "backup_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("backup_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    watched_directory_id: Mapped[int | None] = mapped_column(
        ForeignKey("watched_directories.id", ondelete="SET NULL"),
        index=True,
    )
    backup_name: Mapped[str] = mapped_column(String(255), nullable=False)
    backup_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    backup_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    backup_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    file_count: Mapped[int | None] = mapped_column(Integer)
    content_hash: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str | None] = mapped_column(Text)
    retained: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_reason: Mapped[str | None] = mapped_column(String(64))
    last_restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    restore_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    group: Mapped["BackupGroup"] = relationship(back_populates="backup_history")
    watched_directory: Mapped["WatchedDirectory | None"] = relationship(
        back_populates="backup_history",
    )
