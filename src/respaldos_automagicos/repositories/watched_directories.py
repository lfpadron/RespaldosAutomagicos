"""Watched directory repository."""

from datetime import datetime

from sqlalchemy import func, select

from respaldos_automagicos.models.enums import WatchedDirectoryStatus
from respaldos_automagicos.models.watched_directory import WatchedDirectory
from respaldos_automagicos.repositories.base import BaseRepository


class WatchedDirectoryRepository(BaseRepository[WatchedDirectory]):
    """Repository for watched directories."""

    def get(self, watched_directory_id: int) -> WatchedDirectory | None:
        """Return a watched directory by id."""
        return self.session.get(WatchedDirectory, watched_directory_id)

    def get_by_group_and_relative_path(
        self,
        group_id: int,
        relative_path: str,
    ) -> WatchedDirectory | None:
        """Return a watched directory for a group and relative path."""
        statement = select(WatchedDirectory).where(
            WatchedDirectory.group_id == group_id,
            WatchedDirectory.relative_path == relative_path,
        )
        return self.session.scalar(statement)

    def get_or_create(
        self,
        group_id: int,
        relative_path: str,
    ) -> WatchedDirectory:
        """Return a watched directory, creating it when missing."""
        watched_directory = self.get_by_group_and_relative_path(group_id, relative_path)
        if watched_directory is not None:
            return watched_directory

        watched_directory = WatchedDirectory(
            group_id=group_id,
            relative_path=relative_path,
        )
        self.session.add(watched_directory)
        self.session.flush()
        return watched_directory

    def mark_pending(
        self,
        watched_directory: WatchedDirectory,
        changed_at: datetime,
    ) -> WatchedDirectory:
        """Mark a watched directory as pending after a detected change."""
        watched_directory.pending_backup = True
        watched_directory.backup_running = False
        watched_directory.last_change_at = changed_at
        watched_directory.status = WatchedDirectoryStatus.PENDING.value
        return watched_directory

    def clear_pending(self, watched_directory: WatchedDirectory) -> WatchedDirectory:
        """Clear the pending flag and return the directory to normal state."""
        watched_directory.pending_backup = False
        watched_directory.backup_running = False
        watched_directory.status = WatchedDirectoryStatus.NORMAL.value
        return watched_directory

    def mark_backup_running(
        self,
        watched_directory: WatchedDirectory,
    ) -> WatchedDirectory:
        """Mark a watched directory as actively being backed up."""
        watched_directory.backup_running = True
        watched_directory.status = WatchedDirectoryStatus.BACKING_UP.value
        return watched_directory

    def mark_error(self, watched_directory: WatchedDirectory) -> WatchedDirectory:
        """Mark a watched directory as failed."""
        watched_directory.pending_backup = False
        watched_directory.backup_running = False
        watched_directory.status = WatchedDirectoryStatus.ERROR.value
        return watched_directory

    def update_status(
        self,
        watched_directory: WatchedDirectory,
        status: WatchedDirectoryStatus,
    ) -> WatchedDirectory:
        """Update a watched directory status."""
        watched_directory.status = status.value
        return watched_directory

    def list_pending(self) -> list[WatchedDirectory]:
        """Return watched directories marked as pending in the database."""
        statement = select(WatchedDirectory).where(
            WatchedDirectory.pending_backup.is_(True)
        )
        return list(self.session.scalars(statement))

    def list_by_group(self, group_id: int) -> list[WatchedDirectory]:
        """Return watched directories for a group."""
        statement = (
            select(WatchedDirectory)
            .where(WatchedDirectory.group_id == group_id)
            .order_by(WatchedDirectory.relative_path)
        )
        return list(self.session.scalars(statement))

    def mark_inactive(self, watched_directory: WatchedDirectory) -> WatchedDirectory:
        """Mark a disappeared watched directory as inactive."""
        watched_directory.pending_backup = False
        watched_directory.backup_running = False
        watched_directory.status = WatchedDirectoryStatus.IGNORED.value
        return watched_directory

    def count_all(self) -> int:
        """Return the number of watched directory rows."""
        statement = select(func.count()).select_from(WatchedDirectory)
        return int(self.session.scalar(statement) or 0)
