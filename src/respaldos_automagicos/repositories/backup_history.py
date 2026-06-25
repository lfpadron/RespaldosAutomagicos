"""Backup history repository."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from respaldos_automagicos.models.backup_history import BackupHistory
from respaldos_automagicos.repositories.base import BaseRepository


class BackupHistoryRepository(BaseRepository[BackupHistory]):
    """Repository for backup history records."""

    def add_record(
        self,
        *,
        group_id: int,
        watched_directory_id: int | None,
        backup_name: str,
        backup_path: str,
        backup_time: datetime,
        backup_size_bytes: int | None,
        file_count: int | None,
        content_hash: str | None,
        status: str,
        duration_ms: int | None,
        message: str | None = None,
    ) -> BackupHistory:
        """Add a backup history row."""
        record = BackupHistory(
            group_id=group_id,
            watched_directory_id=watched_directory_id,
            backup_name=backup_name,
            backup_path=backup_path,
            backup_time=backup_time,
            backup_size_bytes=backup_size_bytes,
            file_count=file_count,
            content_hash=content_hash,
            status=status,
            duration_ms=duration_ms,
            message=message,
        )
        return self.add(record)

    def list_by_group(self, group_id: int) -> list[BackupHistory]:
        """Return backup history rows for a group."""
        statement = (
            select(BackupHistory)
            .where(BackupHistory.group_id == group_id)
            .order_by(BackupHistory.backup_time.desc())
        )
        return list(self.session.scalars(statement))

    def list_retained_successful_for_project(
        self,
        *,
        group_id: int,
        watched_directory_id: int,
    ) -> list[BackupHistory]:
        """Return retained successful backups for one group/project newest first."""
        statement = (
            select(BackupHistory)
            .where(
                BackupHistory.group_id == group_id,
                BackupHistory.watched_directory_id == watched_directory_id,
                BackupHistory.status == "BACKUP_OK",
                BackupHistory.retained.is_(True),
            )
            .order_by(BackupHistory.backup_time.desc(), BackupHistory.id.desc())
        )
        return list(self.session.scalars(statement))

    def mark_deleted(
        self,
        backup_history: BackupHistory,
        *,
        deleted_at: datetime,
        deletion_reason: str,
    ) -> BackupHistory:
        """Mark a history row as deleted by retention."""
        backup_history.retained = False
        backup_history.deleted_at = deleted_at
        backup_history.deletion_reason = deletion_reason
        return backup_history

    def get_available_successful(self, backup_history_id: int) -> BackupHistory | None:
        """Return a retained successful backup by id."""
        statement = (
            select(BackupHistory)
            .options(
                selectinload(BackupHistory.group),
                selectinload(BackupHistory.watched_directory),
            )
            .where(
                BackupHistory.id == backup_history_id,
                BackupHistory.status == "BACKUP_OK",
                BackupHistory.retained.is_(True),
            )
        )
        return self.session.scalar(statement)

    def list_available_for_project(
        self,
        *,
        group_id: int,
        watched_directory_id: int,
    ) -> list[BackupHistory]:
        """Return retained successful backups for restore selection."""
        statement = (
            select(BackupHistory)
            .where(
                BackupHistory.group_id == group_id,
                BackupHistory.watched_directory_id == watched_directory_id,
                BackupHistory.status == "BACKUP_OK",
                BackupHistory.retained.is_(True),
            )
            .order_by(BackupHistory.backup_time.desc(), BackupHistory.id.desc())
        )
        return list(self.session.scalars(statement))

    def mark_restored(
        self,
        backup_history: BackupHistory,
        *,
        restored_at: datetime,
    ) -> BackupHistory:
        """Update restore metadata after a successful restore."""
        backup_history.last_restored_at = restored_at
        backup_history.restore_count += 1
        return backup_history

    def list_recent(
        self,
        *,
        group_id: int | None = None,
        limit: int = 200,
    ) -> list[BackupHistory]:
        """Return recent backup history rows."""
        statement = (
            select(BackupHistory)
            .options(
                selectinload(BackupHistory.group),
                selectinload(BackupHistory.watched_directory),
            )
            .order_by(BackupHistory.backup_time.desc())
            .limit(limit)
        )
        if group_id is not None:
            statement = statement.where(BackupHistory.group_id == group_id)
        return list(self.session.scalars(statement))

    def count_by_group_and_status(self, group_id: int, status: str) -> int:
        """Return the number of history rows for a group and status."""
        statement = (
            select(func.count())
            .select_from(BackupHistory)
            .where(
                BackupHistory.group_id == group_id,
                BackupHistory.status == status,
            )
        )
        return int(self.session.scalar(statement) or 0)

    def count_all(self) -> int:
        """Return the number of backup history rows."""
        statement = select(func.count()).select_from(BackupHistory)
        return int(self.session.scalar(statement) or 0)
