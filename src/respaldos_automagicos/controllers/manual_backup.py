"""Controller for multi-group manual backup jobs."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from respaldos_automagicos.models.backup_group import BackupGroup
from respaldos_automagicos.models.enums import AuditEvent, WatchedDirectoryStatus
from respaldos_automagicos.models.watched_directory import WatchedDirectory
from respaldos_automagicos.repositories.audit import AuditRepository
from respaldos_automagicos.repositories.backup_groups import BackupGroupRepository
from respaldos_automagicos.repositories.watched_directories import (
    WatchedDirectoryRepository,
)


class ManualBackupExecutor(Protocol):
    """Backup executor used by manual backup jobs."""

    def create_backup(
        self,
        group: BackupGroup,
        watched_directory: WatchedDirectory,
    ) -> object:
        """Create a backup for one watched directory."""


class ManualBackupState(StrEnum):
    """User-facing states for one manual backup group job."""

    WAITING = "En espera"
    SCANNING = "Escaneando"
    BACKING_UP = "Respaldando"
    FINISHED = "Finalizado"
    ERROR = "Error"


class ManualBackupJobError(RuntimeError):
    """Raised when a manual backup group cannot run."""


@dataclass(slots=True)
class GroupSelectionState:
    """Selection state for the groups table."""

    selected_ids: set[int] = field(default_factory=set)

    def toggle(self, group_id: int) -> None:
        """Toggle one group selection."""
        if group_id in self.selected_ids:
            self.selected_ids.remove(group_id)
        else:
            self.selected_ids.add(group_id)

    def select_all(self, group_ids: Sequence[int]) -> None:
        """Select all visible groups."""
        self.selected_ids = set(group_ids)

    def clear(self) -> None:
        """Clear all selected groups."""
        self.selected_ids.clear()

    def is_selected(self, group_id: int) -> bool:
        """Return whether a group is selected."""
        return group_id in self.selected_ids

    def selected_or_fallback(self, fallback_id: int | None) -> list[int]:
        """Return selected groups or the highlighted group as fallback."""
        if self.selected_ids:
            return sorted(self.selected_ids)
        if fallback_id is None:
            return []
        return [fallback_id]


@dataclass(frozen=True, slots=True)
class ManualBackupGroupProgress:
    """Progress snapshot for one group in a manual backup job."""

    group_id: int
    group_name: str
    state: ManualBackupState
    processed_projects: int
    total_projects: int
    error_message: str | None = None

    @property
    def progress_percent(self) -> int:
        """Return integer progress percentage."""
        if self.state == ManualBackupState.FINISHED and self.total_projects == 0:
            return 100
        if self.total_projects <= 0:
            return 0
        return min(100, int((self.processed_projects / self.total_projects) * 100))


@dataclass(frozen=True, slots=True)
class ManualBackupJobSummary:
    """Summary returned after a manual backup job run."""

    accepted_group_ids: tuple[int, ...]
    skipped_group_ids: tuple[int, ...]


class ManualBackupJobController:
    """Runs multi-group manual backups and exposes progress snapshots."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        backup_service: ManualBackupExecutor,
    ) -> None:
        """Create the manual backup job controller."""
        self._session_factory = session_factory
        self._backup_service = backup_service
        self._lock = Lock()
        self._running_group_ids: set[int] = set()
        self._statuses: dict[int, ManualBackupGroupProgress] = {}

    def run(self, group_ids: Sequence[int]) -> ManualBackupJobSummary:
        """Run backups for the provided groups synchronously."""
        started_at = perf_counter()
        accepted_group_ids, skipped_group_ids = self._reserve_groups(group_ids)
        if not accepted_group_ids:
            return ManualBackupJobSummary(
                accepted_group_ids=(),
                skipped_group_ids=tuple(skipped_group_ids),
            )

        self._audit(
            AuditEvent.MANUAL_BACKUP_STARTED.value,
            details=(
                f"Grupos={len(accepted_group_ids)}. "
                f"Omitidos_por_job_activo={len(skipped_group_ids)}."
            ),
        )
        try:
            for group_id in accepted_group_ids:
                self._run_group(group_id)
            self._audit(
                AuditEvent.MANUAL_BACKUP_FINISHED.value,
                details=(
                    f"Grupos={len(accepted_group_ids)}. "
                    f"Omitidos_por_job_activo={len(skipped_group_ids)}. "
                    f"Duracion_ms={_duration_ms(started_at)}."
                ),
            )
            return ManualBackupJobSummary(
                accepted_group_ids=tuple(accepted_group_ids),
                skipped_group_ids=tuple(skipped_group_ids),
            )
        finally:
            self._release_groups(accepted_group_ids)

    def snapshot(self) -> dict[int, ManualBackupGroupProgress]:
        """Return current progress by group id."""
        with self._lock:
            return dict(self._statuses)

    def is_group_running(self, group_id: int) -> bool:
        """Return whether a group currently has a running manual job."""
        with self._lock:
            return group_id in self._running_group_ids

    def _reserve_groups(
        self,
        group_ids: Sequence[int],
    ) -> tuple[list[int], list[int]]:
        unique_group_ids = list(dict.fromkeys(group_ids))
        accepted_group_ids: list[int] = []
        skipped_group_ids: list[int] = []
        with self._lock:
            for group_id in unique_group_ids:
                if group_id in self._running_group_ids:
                    skipped_group_ids.append(group_id)
                    continue
                self._running_group_ids.add(group_id)
                accepted_group_ids.append(group_id)
                self._statuses[group_id] = ManualBackupGroupProgress(
                    group_id=group_id,
                    group_name="",
                    state=ManualBackupState.WAITING,
                    processed_projects=0,
                    total_projects=0,
                )
        return accepted_group_ids, skipped_group_ids

    def _release_groups(self, group_ids: Sequence[int]) -> None:
        with self._lock:
            for group_id in group_ids:
                self._running_group_ids.discard(group_id)

    def _run_group(self, group_id: int) -> None:
        started_at = perf_counter()
        processed_projects = 0
        total_projects = 0
        group_name = ""
        try:
            self._set_status(
                group_id=group_id,
                group_name=group_name,
                state=ManualBackupState.SCANNING,
                processed_projects=processed_projects,
                total_projects=total_projects,
            )
            group, watched_directories = self._prepare_group(group_id)
            group_name = group.name
            total_projects = len(watched_directories)
            self._audit(
                AuditEvent.MANUAL_BACKUP_GROUP_STARTED.value,
                group_id=group_id,
                details=(
                    f"Grupo={group_name}. Proyectos={total_projects}. " "Procesados=0."
                ),
            )
            self._set_status(
                group_id=group_id,
                group_name=group_name,
                state=ManualBackupState.BACKING_UP,
                processed_projects=processed_projects,
                total_projects=total_projects,
            )
            for watched_directory in watched_directories:
                result = self._backup_service.create_backup(group, watched_directory)
                processed_projects += 1
                _ensure_project_backup_completed(result)
                self._set_status(
                    group_id=group_id,
                    group_name=group_name,
                    state=ManualBackupState.BACKING_UP,
                    processed_projects=processed_projects,
                    total_projects=total_projects,
                )
            self._set_status(
                group_id=group_id,
                group_name=group_name,
                state=ManualBackupState.FINISHED,
                processed_projects=processed_projects,
                total_projects=total_projects,
            )
            self._audit(
                AuditEvent.MANUAL_BACKUP_GROUP_FINISHED.value,
                group_id=group_id,
                details=(
                    f"Grupo={group_name}. Proyectos={total_projects}. "
                    f"Procesados={processed_projects}. "
                    f"Duracion_ms={_duration_ms(started_at)}."
                ),
            )
        except Exception as exc:
            self._set_status(
                group_id=group_id,
                group_name=group_name,
                state=ManualBackupState.ERROR,
                processed_projects=processed_projects,
                total_projects=total_projects,
                error_message=str(exc),
            )
            self._audit(
                AuditEvent.MANUAL_BACKUP_GROUP_ERROR.value,
                group_id=group_id,
                details=(
                    f"Grupo={group_name or group_id}. Proyectos={total_projects}. "
                    f"Procesados={processed_projects}. "
                    f"Duracion_ms={_duration_ms(started_at)}. Error={exc}."
                ),
            )

    def _prepare_group(
        self,
        group_id: int,
    ) -> tuple[BackupGroup, list[WatchedDirectory]]:
        with self._session_factory() as session:
            group = BackupGroupRepository(session).get_active(group_id)
            if group is None:
                raise ManualBackupJobError("El grupo no existe.")
            root = Path(group.root_directory)
            if not root.is_dir():
                raise ManualBackupJobError("El directorio raiz no existe.")

            watched_repository = WatchedDirectoryRepository(session)
            existing = {
                directory.relative_path: directory
                for directory in watched_repository.list_by_group(group_id)
            }
            found = sorted(path.name for path in root.iterdir() if path.is_dir())
            for relative_path in found:
                watched = existing.get(relative_path)
                if watched is None:
                    watched_repository.get_or_create(group_id, relative_path)
                elif watched.status == WatchedDirectoryStatus.IGNORED.value:
                    watched_repository.update_status(
                        watched,
                        WatchedDirectoryStatus.NORMAL,
                    )
            for relative_path, watched in existing.items():
                if relative_path not in found:
                    watched_repository.mark_inactive(watched)

            watched_directories = [
                directory
                for directory in watched_repository.list_by_group(group_id)
                if directory.status != WatchedDirectoryStatus.IGNORED.value
            ]
            session.commit()
            return group, watched_directories

    def _set_status(
        self,
        *,
        group_id: int,
        group_name: str,
        state: ManualBackupState,
        processed_projects: int,
        total_projects: int,
        error_message: str | None = None,
    ) -> None:
        with self._lock:
            previous = self._statuses.get(group_id)
            self._statuses[group_id] = ManualBackupGroupProgress(
                group_id=group_id,
                group_name=group_name or (previous.group_name if previous else ""),
                state=state,
                processed_projects=processed_projects,
                total_projects=total_projects,
                error_message=error_message,
            )

    def _audit(
        self,
        action: str,
        *,
        group_id: int | None = None,
        details: str,
    ) -> None:
        with self._session_factory() as session:
            AuditRepository(session).add_event(
                action,
                action,
                group_id=group_id,
                details=details,
            )
            session.commit()


def _duration_ms(started_at: float) -> int:
    return int((perf_counter() - started_at) * 1000)


def _ensure_project_backup_completed(result: object) -> None:
    status = getattr(result, "status", None)
    if status is None:
        return
    if status in (
        AuditEvent.BACKUP_OK.value,
        AuditEvent.NO_EFFECTIVE_CHANGES.value,
    ):
        return
    raise ManualBackupJobError(f"El respaldo del proyecto no se completo: {status}.")
