"""Backup group repository."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from respaldos_automagicos.models.backup_group import BackupGroup
from respaldos_automagicos.models.enums import WatchedDirectoryStatus
from respaldos_automagicos.models.mixins import utc_now
from respaldos_automagicos.repositories.base import BaseRepository
from respaldos_automagicos.utils.time import DEFAULT_TIMEZONE


@dataclass(frozen=True, slots=True)
class BackupGroupSummary:
    """Read model used by the TUI group overview."""

    id: int
    name: str
    enabled: bool
    timezone: str
    root_directory: str
    destination_directory: str
    project_count: int
    pending_count: int
    last_change_at: datetime | None
    last_backup_at: datetime | None
    next_scan_at: datetime | None


class BackupGroupRepository(BaseRepository[BackupGroup]):
    """Repository for backup groups."""

    def get(self, group_id: int) -> BackupGroup | None:
        """Return a backup group by id."""
        return self.session.get(BackupGroup, group_id)

    def get_active(self, group_id: int) -> BackupGroup | None:
        """Return a non-deleted backup group by id."""
        statement = select(BackupGroup).where(
            BackupGroup.id == group_id,
            BackupGroup.deleted_at.is_(None),
        )
        return self.session.scalar(statement)

    def get_by_name(self, name: str) -> BackupGroup | None:
        """Return a non-deleted backup group by name."""
        statement = select(BackupGroup).where(
            BackupGroup.name == name,
            BackupGroup.deleted_at.is_(None),
        )
        return self.session.scalar(statement)

    def create(
        self,
        *,
        name: str,
        root_directory: str,
        destination_directory: str,
        enabled: bool,
        scan_interval_minutes: int,
        stabilization_minutes: int,
        backups_to_keep: int,
        days_to_keep: int,
        compression_level: int,
        timezone: str = DEFAULT_TIMEZONE,
    ) -> BackupGroup:
        """Create a backup group."""
        group = BackupGroup(
            name=name,
            root_directory=root_directory,
            destination_directory=destination_directory,
            timezone=timezone,
            enabled=enabled,
            scan_interval_minutes=scan_interval_minutes,
            stabilization_minutes=stabilization_minutes,
            backups_to_keep=backups_to_keep,
            days_to_keep=days_to_keep,
            compression_level=compression_level,
        )
        return self.add(group)

    def update(
        self,
        group: BackupGroup,
        *,
        name: str,
        root_directory: str,
        destination_directory: str,
        enabled: bool,
        scan_interval_minutes: int,
        stabilization_minutes: int,
        backups_to_keep: int,
        days_to_keep: int,
        compression_level: int,
        timezone: str = DEFAULT_TIMEZONE,
    ) -> BackupGroup:
        """Update a backup group."""
        group.name = name
        group.root_directory = root_directory
        group.destination_directory = destination_directory
        group.timezone = timezone
        group.enabled = enabled
        group.scan_interval_minutes = scan_interval_minutes
        group.stabilization_minutes = stabilization_minutes
        group.backups_to_keep = backups_to_keep
        group.days_to_keep = days_to_keep
        group.compression_level = compression_level
        return group

    def logical_delete(self, group: BackupGroup) -> BackupGroup:
        """Mark a backup group as deleted without removing its history."""
        group.enabled = False
        group.deleted_at = utc_now()
        return group

    def set_enabled(self, group: BackupGroup, enabled: bool) -> BackupGroup:
        """Activate or deactivate a backup group."""
        group.enabled = enabled
        return group

    def list_all(self, *, include_deleted: bool = False) -> list[BackupGroup]:
        """Return backup groups."""
        statement = select(BackupGroup).order_by(BackupGroup.name)
        if not include_deleted:
            statement = statement.where(BackupGroup.deleted_at.is_(None))
        return list(self.session.scalars(statement))

    def search(self, query: str) -> list[BackupGroup]:
        """Search non-deleted backup groups by name."""
        statement = (
            select(BackupGroup)
            .where(
                BackupGroup.deleted_at.is_(None),
                BackupGroup.name.ilike(f"%{query}%"),
            )
            .order_by(BackupGroup.name)
        )
        return list(self.session.scalars(statement))

    def list_enabled(self) -> list[BackupGroup]:
        """Return all enabled backup groups."""
        statement = select(BackupGroup).where(
            BackupGroup.enabled.is_(True),
            BackupGroup.deleted_at.is_(None),
        )
        return list(self.session.scalars(statement))

    def list_summaries(self) -> list[BackupGroupSummary]:
        """Return group summaries for display."""
        statement = (
            select(BackupGroup)
            .options(selectinload(BackupGroup.watched_directories))
            .where(BackupGroup.deleted_at.is_(None))
            .order_by(BackupGroup.name)
        )
        groups = self.session.scalars(statement).all()
        summaries: list[BackupGroupSummary] = []
        for group in groups:
            directories = list(group.watched_directories)
            active_directories = [
                directory
                for directory in directories
                if directory.status != WatchedDirectoryStatus.IGNORED.value
            ]
            last_change = max(
                (
                    directory.last_change_at
                    for directory in directories
                    if directory.last_change_at is not None
                ),
                default=None,
            )
            last_backup = max(
                (
                    directory.last_backup_at
                    for directory in directories
                    if directory.last_backup_at is not None
                ),
                default=None,
            )
            summaries.append(
                BackupGroupSummary(
                    id=group.id,
                    name=group.name,
                    enabled=group.enabled,
                    timezone=group.timezone,
                    root_directory=group.root_directory,
                    destination_directory=group.destination_directory,
                    project_count=len(active_directories),
                    pending_count=sum(
                        1 for directory in directories if directory.pending_backup
                    ),
                    last_change_at=last_change,
                    last_backup_at=last_backup,
                    next_scan_at=(
                        utc_now() + timedelta(minutes=group.scan_interval_minutes)
                        if group.enabled
                        else None
                    ),
                )
            )
        return summaries

    def count_active(self) -> int:
        """Return the number of non-deleted groups."""
        statement = (
            select(func.count())
            .select_from(BackupGroup)
            .where(BackupGroup.deleted_at.is_(None))
        )
        return int(self.session.scalar(statement) or 0)
