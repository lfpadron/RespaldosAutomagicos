"""Controller for safe restore workflows."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from respaldos_automagicos.models.backup_history import BackupHistory
from respaldos_automagicos.models.enums import WatchedDirectoryStatus
from respaldos_automagicos.repositories.backup_groups import BackupGroupRepository
from respaldos_automagicos.repositories.backup_history import BackupHistoryRepository
from respaldos_automagicos.repositories.watched_directories import (
    WatchedDirectoryRepository,
)
from respaldos_automagicos.restore.service import RestoreResult, RestoreService
from respaldos_automagicos.utils.time import DEFAULT_TIMEZONE


class RestoreControllerError(ValueError):
    """Raised when a restore workflow cannot continue."""


@dataclass(frozen=True, slots=True)
class RestoreGroupItem:
    """Group row available for restore selection."""

    id: int
    name: str


@dataclass(frozen=True, slots=True)
class RestoreProjectItem:
    """Project row available for restore selection."""

    id: int
    relative_path: str
    status: str


@dataclass(frozen=True, slots=True)
class RestoreVersionItem:
    """Backup version row available for restore selection."""

    id: int
    backup_name: str
    backup_time: datetime
    timezone: str
    rolling_label: str
    short_hash: str
    backup_size_bytes: int | None
    file_count: int | None
    available: bool
    availability_label: str


@dataclass(frozen=True, slots=True)
class RestoreSummary:
    """Summary shown before confirming a restore."""

    id: int
    group_name: str
    project_name: str
    backup_name: str
    backup_path: str
    backup_time: datetime
    timezone: str
    content_hash: str | None
    backup_size_bytes: int | None
    file_count: int | None
    available: bool
    availability_label: str


class RestoreController:
    """Coordinates restore workflows for UI clients."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        restore_service: RestoreService | None = None,
    ) -> None:
        """Create the restore controller."""
        self._session_factory = session_factory
        self._restore_service = restore_service or RestoreService(session_factory)

    def list_groups(self) -> list[RestoreGroupItem]:
        """Return non-deleted groups for restore selection."""
        with self._session_factory() as session:
            groups = BackupGroupRepository(session).list_all()
            return [RestoreGroupItem(id=group.id, name=group.name) for group in groups]

    def list_projects(self, group_id: int) -> list[RestoreProjectItem]:
        """Return active watched directories in a group."""
        with self._session_factory() as session:
            group = BackupGroupRepository(session).get_active(group_id)
            if group is None:
                raise RestoreControllerError("El grupo no existe.")
            directories = WatchedDirectoryRepository(session).list_by_group(group_id)
            return [
                RestoreProjectItem(
                    id=directory.id,
                    relative_path=directory.relative_path,
                    status=directory.status,
                )
                for directory in directories
                if directory.status != WatchedDirectoryStatus.IGNORED.value
            ]

    def list_versions(
        self,
        *,
        group_id: int,
        watched_directory_id: int,
    ) -> list[RestoreVersionItem]:
        """Return retained successful backups for one project."""
        with self._session_factory() as session:
            group = BackupGroupRepository(session).get(group_id)
            timezone = group.timezone if group is not None else DEFAULT_TIMEZONE
            rows = BackupHistoryRepository(session).list_available_for_project(
                group_id=group_id,
                watched_directory_id=watched_directory_id,
            )
            return [self._to_version_item(row, timezone=timezone) for row in rows]

    def summary(self, backup_history_id: int) -> RestoreSummary:
        """Return a restore summary for one retained successful backup."""
        with self._session_factory() as session:
            history = BackupHistoryRepository(session).get_available_successful(
                backup_history_id
            )
            if (
                history is None
                or history.group is None
                or history.watched_directory is None
            ):
                raise RestoreControllerError(
                    "El respaldo no esta disponible para restauracion."
                )
            available = _zip_available(history.backup_path)
            return RestoreSummary(
                id=history.id,
                group_name=history.group.name,
                project_name=history.watched_directory.relative_path,
                backup_name=history.backup_name,
                backup_path=history.backup_path,
                backup_time=history.backup_time,
                timezone=history.group.timezone,
                content_hash=history.content_hash,
                backup_size_bytes=history.backup_size_bytes,
                file_count=history.file_count,
                available=available,
                availability_label=_availability_label(available),
            )

    def restore(
        self,
        backup_history_id: int,
        *,
        restore_time: datetime | None = None,
    ) -> RestoreResult:
        """Restore a backup after controller-level availability checks."""
        summary = self.summary(backup_history_id)
        if not summary.available:
            raise RestoreControllerError("El ZIP fisico no esta disponible.")
        return self._restore_service.restore(
            backup_history_id,
            restore_time=restore_time,
        )

    @staticmethod
    def _to_version_item(
        history: BackupHistory,
        *,
        timezone: str,
    ) -> RestoreVersionItem:
        available = _zip_available(history.backup_path)
        return RestoreVersionItem(
            id=history.id,
            backup_name=history.backup_name,
            backup_time=history.backup_time,
            timezone=timezone,
            rolling_label=_rolling_label(history.backup_name),
            short_hash=(history.content_hash or "-")[:12],
            backup_size_bytes=history.backup_size_bytes,
            file_count=history.file_count,
            available=available,
            availability_label=_availability_label(available),
        )


def _zip_available(backup_path: str) -> bool:
    return Path(backup_path).is_file()


def _availability_label(available: bool) -> str:
    return "Sí" if available else "NO DISPONIBLE"


def _rolling_label(backup_name: str) -> str:
    for part in reversed(Path(backup_name).stem.split("_")):
        if len(part) == 4 and part.startswith("R") and part[1:].isdigit():
            return part
    return "-"
