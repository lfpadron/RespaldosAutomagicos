"""In-memory pending directory queue."""

from dataclasses import dataclass, replace
from datetime import datetime

from respaldos_automagicos.models.backup_group import BackupGroup
from respaldos_automagicos.models.watched_directory import WatchedDirectory


@dataclass(frozen=True, slots=True)
class BackupGroupSnapshot:
    """Small immutable backup group representation stored in memory."""

    id: int
    name: str
    scan_interval_minutes: int
    stabilization_minutes: int

    @classmethod
    def from_model(cls, group: BackupGroup) -> "BackupGroupSnapshot":
        """Build a snapshot from a SQLAlchemy backup group model."""
        return cls(
            id=group.id,
            name=group.name,
            scan_interval_minutes=group.scan_interval_minutes,
            stabilization_minutes=group.stabilization_minutes,
        )


@dataclass(frozen=True, slots=True)
class WatchedDirectorySnapshot:
    """Small immutable watched directory representation stored in memory."""

    id: int
    relative_path: str
    status: str

    @classmethod
    def from_model(
        cls,
        watched_directory: WatchedDirectory,
    ) -> "WatchedDirectorySnapshot":
        """Build a snapshot from a SQLAlchemy watched directory model."""
        return cls(
            id=watched_directory.id,
            relative_path=watched_directory.relative_path,
            status=watched_directory.status,
        )


@dataclass(frozen=True, slots=True)
class PendingDirectory:
    """Directory awaiting stabilization before backup planning."""

    group: BackupGroupSnapshot
    watched_directory: WatchedDirectorySnapshot
    last_change_at: datetime


class PendingDirectoryQueue:
    """Keeps one pending entry per group and watched directory."""

    def __init__(self) -> None:
        """Create an empty pending queue."""
        self._items: dict[tuple[int, str], PendingDirectory] = {}

    def upsert(self, item: PendingDirectory) -> PendingDirectory:
        """Insert or update a pending directory without creating duplicates."""
        key = self._key(item.group.id, item.watched_directory.relative_path)
        existing = self._items.get(key)
        if existing is None:
            self._items[key] = item
            return item

        updated = replace(
            existing,
            group=item.group,
            watched_directory=item.watched_directory,
            last_change_at=item.last_change_at,
        )
        self._items[key] = updated
        return updated

    def remove(self, group_id: int, relative_path: str) -> None:
        """Remove a pending directory if it exists."""
        self._items.pop(self._key(group_id, relative_path), None)

    def list_pending(self) -> list[PendingDirectory]:
        """Return pending directories ordered by group and relative path."""
        return sorted(
            self._items.values(),
            key=lambda item: (item.group.id, item.watched_directory.relative_path),
        )

    def __len__(self) -> int:
        """Return the number of unique pending directories."""
        return len(self._items)

    @staticmethod
    def _key(group_id: int, relative_path: str) -> tuple[int, str]:
        return (group_id, relative_path.casefold())
