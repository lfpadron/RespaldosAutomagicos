"""Controllers for backup group workflows."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

from sqlalchemy.orm import Session, sessionmaker

from respaldos_automagicos.models.backup_group import BackupGroup
from respaldos_automagicos.models.enums import WatchedDirectoryStatus
from respaldos_automagicos.repositories.backup_groups import (
    BackupGroupRepository,
)
from respaldos_automagicos.repositories.watched_directories import (
    WatchedDirectoryRepository,
)
from respaldos_automagicos.services.backup_service import BackupResult, BackupService
from respaldos_automagicos.utils.time import DEFAULT_TIMEZONE, normalize_timezone_name
from respaldos_automagicos.watcher.service import DirectoryWatcherService


class GroupValidationError(ValueError):
    """Raised when a backup group form is invalid."""

    def __init__(self, errors: list[str]) -> None:
        """Create a validation error with user-facing messages."""
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True, slots=True)
class BackupGroupFormData:
    """Data accepted by the group create and edit workflows."""

    name: str
    root_directory: str
    destination_directory: str
    scan_interval_minutes: int
    stabilization_minutes: int
    backups_to_keep: int
    days_to_keep: int
    compression_level: int
    enabled: bool
    timezone: str = DEFAULT_TIMEZONE


@dataclass(frozen=True, slots=True)
class BackupGroupListItem:
    """Group row displayed by the TUI."""

    id: int
    name: str
    enabled: bool
    timezone: str
    root_directory: str
    destination_directory: str
    project_count: int
    pending_count: int
    last_backup_at: datetime | None
    next_scan_at: datetime | None


@dataclass(frozen=True, slots=True)
class ProjectScanResult:
    """Result of scanning immediate projects under a group root."""

    created: int
    reactivated: int
    deactivated: int
    active_projects: int


class GroupController:
    """Coordinates group workflows for UI clients."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        backup_service: BackupService,
        watcher_service: DirectoryWatcherService,
    ) -> None:
        """Create the group controller."""
        self._session_factory = session_factory
        self._backup_service = backup_service
        self._watcher_service = watcher_service

    def list_groups(self) -> list[BackupGroupListItem]:
        """Return backup groups for list screens."""
        with self._session_factory() as session:
            summaries = BackupGroupRepository(session).list_summaries()
        return [
            BackupGroupListItem(
                id=summary.id,
                name=summary.name,
                enabled=summary.enabled,
                timezone=summary.timezone,
                root_directory=summary.root_directory,
                destination_directory=summary.destination_directory,
                project_count=summary.project_count,
                pending_count=summary.pending_count,
                last_backup_at=summary.last_backup_at,
                next_scan_at=summary.next_scan_at,
            )
            for summary in summaries
        ]

    def get_form_data(self, group_id: int) -> BackupGroupFormData:
        """Return editable form data for a group."""
        with self._session_factory() as session:
            group = BackupGroupRepository(session).get_active(group_id)
            if group is None:
                raise GroupValidationError(["El grupo no existe."])
            return BackupGroupFormData(
                name=group.name,
                root_directory=group.root_directory,
                destination_directory=group.destination_directory,
                timezone=group.timezone,
                scan_interval_minutes=group.scan_interval_minutes,
                stabilization_minutes=group.stabilization_minutes,
                backups_to_keep=group.backups_to_keep,
                days_to_keep=group.days_to_keep or 1,
                compression_level=group.compression_level,
                enabled=group.enabled,
            )

    def create_group(self, data: BackupGroupFormData) -> BackupGroup:
        """Create a backup group after validation."""
        with self._session_factory() as session:
            self._validate(session, data)
            group = BackupGroupRepository(session).create(
                name=data.name.strip(),
                root_directory=data.root_directory,
                destination_directory=data.destination_directory,
                timezone=normalize_timezone_name(data.timezone),
                enabled=data.enabled,
                scan_interval_minutes=data.scan_interval_minutes,
                stabilization_minutes=data.stabilization_minutes,
                backups_to_keep=data.backups_to_keep,
                days_to_keep=data.days_to_keep,
                compression_level=data.compression_level,
            )
            session.flush()
            group_id = group.id
            session.commit()
        if data.enabled:
            self._watcher_service.restart_group(group_id)
        return group

    def update_group(
        self,
        group_id: int,
        data: BackupGroupFormData,
    ) -> BackupGroup:
        """Update an existing backup group after validation."""
        with self._session_factory() as session:
            repository = BackupGroupRepository(session)
            group = repository.get_active(group_id)
            if group is None:
                raise GroupValidationError(["El grupo no existe."])
            self._validate(session, data, current_group_id=group_id)
            repository.update(
                group,
                name=data.name.strip(),
                root_directory=data.root_directory,
                destination_directory=data.destination_directory,
                timezone=normalize_timezone_name(data.timezone),
                enabled=data.enabled,
                scan_interval_minutes=data.scan_interval_minutes,
                stabilization_minutes=data.stabilization_minutes,
                backups_to_keep=data.backups_to_keep,
                days_to_keep=data.days_to_keep,
                compression_level=data.compression_level,
            )
            session.commit()
        self._watcher_service.restart_group(group_id)
        return group

    def delete_group(self, group_id: int) -> None:
        """Logically delete a backup group."""
        with self._session_factory() as session:
            group = BackupGroupRepository(session).get_active(group_id)
            if group is None:
                raise GroupValidationError(["El grupo no existe."])
            BackupGroupRepository(session).logical_delete(group)
            session.commit()
        self._watcher_service.stop_group(group_id)

    def delete_groups(self, group_ids: list[int]) -> None:
        """Logically delete multiple backup groups."""
        for group_id in group_ids:
            self.delete_group(group_id)

    def activate_group(self, group_id: int) -> None:
        """Activate a backup group and restart its observer."""
        self._set_group_enabled(group_id, True)
        self._watcher_service.restart_group(group_id)

    def deactivate_group(self, group_id: int) -> None:
        """Deactivate a backup group and stop its observer."""
        self._set_group_enabled(group_id, False)
        self._watcher_service.stop_group(group_id)

    def toggle_groups(self, group_ids: list[int]) -> None:
        """Toggle multiple backup groups independently."""
        for group_id in group_ids:
            selected = self.get_form_data(group_id)
            if selected.enabled:
                self.deactivate_group(group_id)
            else:
                self.activate_group(group_id)

    def duplicate_group(self, group_id: int) -> BackupGroup:
        """Duplicate a backup group with a unique name."""
        with self._session_factory() as session:
            repository = BackupGroupRepository(session)
            original = repository.get_active(group_id)
            if original is None:
                raise GroupValidationError(["El grupo no existe."])
            name = self._duplicate_name(repository, original.name)
            duplicate = repository.create(
                name=name,
                root_directory=original.root_directory,
                destination_directory=original.destination_directory,
                timezone=original.timezone,
                enabled=original.enabled,
                scan_interval_minutes=original.scan_interval_minutes,
                stabilization_minutes=original.stabilization_minutes,
                backups_to_keep=original.backups_to_keep,
                days_to_keep=original.days_to_keep or 1,
                compression_level=original.compression_level,
            )
            session.flush()
            duplicate_id = duplicate.id
            session.commit()
        if duplicate.enabled:
            self._watcher_service.restart_group(duplicate_id)
        return duplicate

    def scan_projects(self, group_id: int) -> ProjectScanResult:
        """Scan immediate subdirectories and update watched directory rows."""
        with self._session_factory() as session:
            group = BackupGroupRepository(session).get_active(group_id)
            if group is None:
                raise GroupValidationError(["El grupo no existe."])
            root = Path(group.root_directory)
            if not root.is_dir():
                raise GroupValidationError(["El directorio raiz no existe."])

            repository = WatchedDirectoryRepository(session)
            existing = {
                directory.relative_path: directory
                for directory in repository.list_by_group(group_id)
            }
            found = sorted(path.name for path in root.iterdir() if path.is_dir())
            created = 0
            reactivated = 0

            for relative_path in found:
                watched = existing.get(relative_path)
                if watched is None:
                    repository.get_or_create(group_id, relative_path)
                    created += 1
                elif watched.status == WatchedDirectoryStatus.IGNORED.value:
                    repository.update_status(watched, WatchedDirectoryStatus.NORMAL)
                    reactivated += 1

            for relative_path, watched in existing.items():
                if relative_path not in found:
                    repository.mark_inactive(watched)

            deactivated = len(
                [
                    watched
                    for relative_path, watched in existing.items()
                    if relative_path not in found
                    and watched.status == WatchedDirectoryStatus.IGNORED.value
                ]
            )
            session.commit()
            return ProjectScanResult(
                created=created,
                reactivated=reactivated,
                deactivated=deactivated,
                active_projects=len(found),
            )

    def scan_groups(self, group_ids: list[int]) -> list[ProjectScanResult]:
        """Scan projects for multiple groups."""
        return [self.scan_projects(group_id) for group_id in group_ids]

    def backup_now(self, group_id: int) -> list[BackupResult]:
        """Run backups immediately for active projects in a group."""
        with self._session_factory() as session:
            group = BackupGroupRepository(session).get_active(group_id)
            if group is None:
                raise GroupValidationError(["El grupo no existe."])
            watched_directories = [
                directory
                for directory in WatchedDirectoryRepository(session).list_by_group(
                    group_id
                )
                if directory.status != WatchedDirectoryStatus.IGNORED.value
            ]
        return [
            self._backup_service.create_backup(group, watched_directory)
            for watched_directory in watched_directories
        ]

    def _set_group_enabled(self, group_id: int, enabled: bool) -> None:
        with self._session_factory() as session:
            group = BackupGroupRepository(session).get_active(group_id)
            if group is None:
                raise GroupValidationError(["El grupo no existe."])
            BackupGroupRepository(session).set_enabled(group, enabled)
            session.commit()

    def _validate(
        self,
        session: Session,
        data: BackupGroupFormData,
        *,
        current_group_id: int | None = None,
    ) -> None:
        errors: list[str] = []
        name = data.name.strip()
        if not name:
            errors.append("El nombre es obligatorio.")
        existing = BackupGroupRepository(session).get_by_name(name)
        if existing is not None and existing.id != current_group_id:
            errors.append("Ya existe un grupo con ese nombre.")
        if not Path(data.root_directory).is_dir():
            errors.append("El directorio raiz debe existir.")
        if not Path(data.destination_directory).is_dir():
            errors.append("El directorio destino debe existir.")
        try:
            normalize_timezone_name(data.timezone)
        except ZoneInfoNotFoundError:
            errors.append(
                "La zona horaria debe existir en Python. "
                "Ejemplo: America/Mexico_City."
            )
        if data.scan_interval_minutes < 5:
            errors.append("El intervalo de escaneo debe ser de al menos 5 minutos.")
        if data.scan_interval_minutes > 1440:
            errors.append("El intervalo de escaneo no puede exceder 1440 minutos.")
        if data.stabilization_minutes < 0:
            errors.append("La estabilizacion no puede ser negativa.")
        if data.stabilization_minutes >= data.scan_interval_minutes:
            errors.append("La estabilizacion debe ser menor que el intervalo.")
        if data.backups_to_keep < 1:
            errors.append("Los respaldos a conservar deben ser al menos 1.")
        if data.days_to_keep < 1:
            errors.append("Los dias de conservacion deben ser al menos 1.")
        if errors:
            raise GroupValidationError(errors)

    @staticmethod
    def _duplicate_name(repository: BackupGroupRepository, base_name: str) -> str:
        suffix = 1
        while True:
            candidate = f"{base_name} copia {suffix}"
            if repository.get_by_name(candidate) is None:
                return candidate
            suffix += 1
