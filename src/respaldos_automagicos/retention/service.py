"""Retention policy service."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from respaldos_automagicos.logging_config import get_logger
from respaldos_automagicos.models.backup_group import BackupGroup
from respaldos_automagicos.models.backup_history import BackupHistory
from respaldos_automagicos.models.enums import AuditEvent
from respaldos_automagicos.models.mixins import utc_now
from respaldos_automagicos.models.watched_directory import WatchedDirectory
from respaldos_automagicos.repositories.audit import AuditRepository
from respaldos_automagicos.repositories.backup_groups import BackupGroupRepository
from respaldos_automagicos.repositories.backup_history import BackupHistoryRepository
from respaldos_automagicos.repositories.watched_directories import (
    WatchedDirectoryRepository,
)


@dataclass(frozen=True, slots=True)
class RetentionResult:
    """Result of applying retention to one project."""

    deleted_count: int
    missing_count: int
    error_count: int


class RetentionService:
    """Applies count and age retention rules per group and project."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        """Create the retention service."""
        self._session_factory = session_factory
        self._logger = logger or get_logger("retention")

    def apply(
        self,
        group: BackupGroup,
        watched_directory: WatchedDirectory,
        *,
        now: datetime | None = None,
    ) -> RetentionResult:
        """Apply retention to retained successful backups for one project."""
        with self._session_factory() as session:
            managed_group = BackupGroupRepository(session).get(group.id)
            managed_watched_directory = WatchedDirectoryRepository(session).get(
                watched_directory.id
            )
            if managed_group is None or managed_watched_directory is None:
                return RetentionResult(deleted_count=0, missing_count=0, error_count=0)
            result = self._apply_in_session(
                session=session,
                group=managed_group,
                watched_directory=managed_watched_directory,
                now=_aware_datetime(now or utc_now()),
            )
            session.commit()
            return result

    def _apply_in_session(
        self,
        *,
        session: Session,
        group: BackupGroup,
        watched_directory: WatchedDirectory,
        now: datetime,
    ) -> RetentionResult:
        history_repository = BackupHistoryRepository(session)
        audit_repository = AuditRepository(session)
        backups = history_repository.list_retained_successful_for_project(
            group_id=group.id,
            watched_directory_id=watched_directory.id,
        )
        keep_count = max(1, group.backups_to_keep)
        protected_ids = {backup.id for backup in backups[:keep_count]}
        cutoff = now - timedelta(days=max(1, group.days_to_keep or 1))

        deleted_count = 0
        missing_count = 0
        error_count = 0

        for backup in backups:
            if backup.id in protected_ids:
                continue

            reason = _deletion_reason(backup, cutoff)
            try:
                deletion_outcome = self._delete_backup_file(
                    backup=backup,
                    group=group,
                    watched_directory=watched_directory,
                    reason=reason,
                    audit_repository=audit_repository,
                )
                if deletion_outcome == AuditEvent.RETENTION_FILE_MISSING.value:
                    missing_count += 1
                else:
                    deleted_count += 1
                history_repository.mark_deleted(
                    backup,
                    deleted_at=now,
                    deletion_reason=reason,
                )
            except OSError as exc:
                error_count += 1
                self._logger.exception(
                    "Error aplicando retencion",
                    extra={
                        "group": group.name,
                        "directory": watched_directory.relative_path,
                    },
                )
                audit_repository.add_event(
                    AuditEvent.RETENTION_ERROR.value,
                    AuditEvent.RETENTION_ERROR.value,
                    group_id=group.id,
                    watched_directory_id=watched_directory.id,
                    details=(
                        f"No se pudo eliminar {backup.backup_path}: {exc}. "
                        f"Razon: {reason}."
                    ),
                )

        return RetentionResult(
            deleted_count=deleted_count,
            missing_count=missing_count,
            error_count=error_count,
        )

    def _delete_backup_file(
        self,
        *,
        backup: BackupHistory,
        group: BackupGroup,
        watched_directory: WatchedDirectory,
        reason: str,
        audit_repository: AuditRepository,
    ) -> str:
        backup_path = Path(backup.backup_path)
        if backup_path.exists():
            backup_path.unlink()
            audit_repository.add_event(
                reason,
                reason,
                group_id=group.id,
                watched_directory_id=watched_directory.id,
                details=f"Eliminado {backup_path}. Razon: {reason}.",
            )
            return reason

        audit_repository.add_event(
            AuditEvent.RETENTION_FILE_MISSING.value,
            AuditEvent.WARNING.value,
            group_id=group.id,
            watched_directory_id=watched_directory.id,
            details=f"Archivo no encontrado: {backup_path}. Razon: {reason}.",
        )
        return AuditEvent.RETENTION_FILE_MISSING.value


def _deletion_reason(backup: BackupHistory, cutoff: datetime) -> str:
    backup_time = _aware_datetime(backup.backup_time)
    if backup_time < cutoff:
        return AuditEvent.RETENTION_BY_AGE.value
    return AuditEvent.RETENTION_BY_COUNT.value


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
