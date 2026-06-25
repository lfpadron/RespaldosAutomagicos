"""Watched directory persistence model."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from respaldos_automagicos.database import Base
from respaldos_automagicos.models.enums import WatchedDirectoryStatus
from respaldos_automagicos.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from respaldos_automagicos.models.backup_group import BackupGroup
    from respaldos_automagicos.models.backup_history import BackupHistory


class WatchedDirectory(TimestampMixin, Base):
    """Subdirectory tracked inside a backup group."""

    __tablename__ = "watched_directories"
    __table_args__ = (
        UniqueConstraint("group_id", "relative_path", name="uq_watched_directory_path"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("backup_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relative_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        default=WatchedDirectoryStatus.NORMAL.value,
        nullable=False,
    )
    pending_backup: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    backup_running: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_backup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_content_hash: Mapped[str | None] = mapped_column(String(128))
    rolling_counter: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    group: Mapped["BackupGroup"] = relationship(back_populates="watched_directories")
    backup_history: Mapped[list["BackupHistory"]] = relationship(
        back_populates="watched_directory",
    )
