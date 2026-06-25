"""Backup group persistence model."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from respaldos_automagicos.database import Base
from respaldos_automagicos.models.mixins import TimestampMixin
from respaldos_automagicos.utils.time import DEFAULT_TIMEZONE

if TYPE_CHECKING:
    from respaldos_automagicos.models.backup_history import BackupHistory
    from respaldos_automagicos.models.watched_directory import WatchedDirectory


class BackupGroup(TimestampMixin, Base):
    """Configurable group of directories that will be backed up together."""

    __tablename__ = "backup_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    root_directory: Mapped[str] = mapped_column(String(1024), nullable=False)
    destination_directory: Mapped[str] = mapped_column(String(1024), nullable=False)
    timezone: Mapped[str] = mapped_column(
        String(128),
        default=DEFAULT_TIMEZONE,
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    scan_interval_minutes: Mapped[int] = mapped_column(
        Integer,
        default=15,
        nullable=False,
    )
    stabilization_minutes: Mapped[int] = mapped_column(
        Integer,
        default=5,
        nullable=False,
    )
    backups_to_keep: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    days_to_keep: Mapped[int | None] = mapped_column(Integer, default=30)
    compression_level: Mapped[int] = mapped_column(Integer, default=6, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    watched_directories: Mapped[list["WatchedDirectory"]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
    )
    backup_history: Mapped[list["BackupHistory"]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
    )
