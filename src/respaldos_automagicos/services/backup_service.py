"""Backup orchestration service."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from zipfile import BadZipFile, ZipFile

from sqlalchemy.orm import Session, sessionmaker

from respaldos_automagicos.config import AppSettings
from respaldos_automagicos.hashing.service import (
    ContentHashResult,
    ContentHashService,
    ContentReadError,
)
from respaldos_automagicos.ignore.service import AutomagicIgnore
from respaldos_automagicos.logging_config import get_logger
from respaldos_automagicos.models.backup_group import BackupGroup
from respaldos_automagicos.models.enums import AuditEvent
from respaldos_automagicos.models.mixins import utc_now
from respaldos_automagicos.models.watched_directory import WatchedDirectory
from respaldos_automagicos.repositories.audit import AuditRepository
from respaldos_automagicos.repositories.backup_groups import BackupGroupRepository
from respaldos_automagicos.repositories.backup_history import BackupHistoryRepository
from respaldos_automagicos.repositories.watched_directories import (
    WatchedDirectoryRepository,
)
from respaldos_automagicos.retention.service import RetentionService
from respaldos_automagicos.scheduler.pending import (
    PendingDirectory,
    PendingDirectoryQueue,
)
from respaldos_automagicos.utils.time import backup_timestamp
from respaldos_automagicos.zipper.service import (
    ZipBackupService,
    ZipCreationError,
)


@dataclass(frozen=True, slots=True)
class BackupResult:
    """Outcome of a backup attempt."""

    status: str
    backup_name: str
    backup_path: Path | None
    content_hash: str | None
    file_count: int | None


class BackupService:
    """Creates backups and records persistence side effects."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        pending_queue: PendingDirectoryQueue,
        settings: AppSettings,
        hashing_service: ContentHashService | None = None,
        zipper_service: ZipBackupService | None = None,
        retention_service: RetentionService | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Create the backup service."""
        self._session_factory = session_factory
        self._pending_queue = pending_queue
        self._settings = settings
        self._hashing_service = hashing_service or ContentHashService()
        self._zipper_service = zipper_service or ZipBackupService()
        self._retention_service = retention_service
        self._logger = logger or get_logger("backup")

    def run_for_pending(
        self,
        item: PendingDirectory,
        backup_time: datetime | None = None,
    ) -> BackupResult | None:
        """Run a backup for a pending directory snapshot."""
        with self._session_factory() as session:
            group = BackupGroupRepository(session).get(item.group.id)
            watched_directory = WatchedDirectoryRepository(session).get(
                item.watched_directory.id
            )
            if group is None or watched_directory is None:
                return None
            result = self._create_backup_in_session(
                session=session,
                group=group,
                watched_directory=watched_directory,
                backup_time=backup_time,
            )
            self._pending_queue.remove(group.id, watched_directory.relative_path)
            return result

    def create_backup(
        self,
        group: BackupGroup,
        watched_directory: WatchedDirectory,
        backup_time: datetime | None = None,
    ) -> BackupResult:
        """Create a backup for a group and watched directory."""
        with self._session_factory() as session:
            managed_group = session.merge(group)
            managed_watched_directory = session.merge(watched_directory)
            result = self._create_backup_in_session(
                session=session,
                group=managed_group,
                watched_directory=managed_watched_directory,
                backup_time=backup_time,
            )
            self._pending_queue.remove(
                managed_group.id,
                managed_watched_directory.relative_path,
            )
            return result

    def _create_backup_in_session(
        self,
        *,
        session: Session,
        group: BackupGroup,
        watched_directory: WatchedDirectory,
        backup_time: datetime | None,
    ) -> BackupResult:
        started_at = perf_counter()
        resolved_backup_time = backup_time or utc_now()
        watched_repository = WatchedDirectoryRepository(session)
        history_repository = BackupHistoryRepository(session)
        audit_repository = AuditRepository(session)
        watched_repository.mark_backup_running(watched_directory)
        session.flush()

        project_path = Path(group.root_directory) / watched_directory.relative_path
        backup_name = ""
        content_hash: str | None = None
        file_count: int | None = None

        try:
            if not project_path.is_dir():
                raise ContentReadError(f"El subdirectorio no existe: {project_path}")

            ignore = AutomagicIgnore.from_file(project_path / "automagic_ignore")
            hash_result = self._hashing_service.calculate(project_path, ignore)
            content_hash = hash_result.content_hash
            file_count = hash_result.file_count

            if content_hash == watched_directory.last_content_hash:
                result = self._record_no_effective_changes(
                    history_repository=history_repository,
                    audit_repository=audit_repository,
                    watched_repository=watched_repository,
                    group=group,
                    watched_directory=watched_directory,
                    backup_time=resolved_backup_time,
                    hash_result=hash_result,
                    duration_ms=_duration_ms(started_at),
                )
                session.commit()
                return result

            rolling_counter = (
                history_repository.count_by_group_and_status(
                    group.id,
                    AuditEvent.BACKUP_OK.value,
                )
                % 1000
            )
            backup_name = _backup_name(
                watched_directory.relative_path,
                resolved_backup_time,
                rolling_counter,
            )
            destination_directory = (
                Path(group.destination_directory)
                / group.name
                / watched_directory.relative_path
            )
            manifest = _manifest(
                settings=self._settings,
                group=group,
                watched_directory=watched_directory,
                backup_name=backup_name,
                backup_time=resolved_backup_time,
                rolling_counter=rolling_counter,
                hash_result=hash_result,
            )
            zip_result = self._zipper_service.create_backup(
                project_root_name=watched_directory.relative_path,
                destination_directory=destination_directory,
                backup_name=backup_name,
                files=hash_result.files,
                manifest=manifest,
                compression_level=group.compression_level,
            )
            _verify_created_backup(
                backup_path=zip_result.backup_path,
                expected_project_root_name=watched_directory.relative_path,
                expected_content_hash=hash_result.content_hash,
                expected_file_count=hash_result.file_count,
            )
            result = self._record_backup_ok(
                history_repository=history_repository,
                audit_repository=audit_repository,
                watched_repository=watched_repository,
                group=group,
                watched_directory=watched_directory,
                backup_time=resolved_backup_time,
                backup_name=backup_name,
                backup_path=zip_result.backup_path,
                backup_size_bytes=zip_result.backup_size_bytes,
                hash_result=hash_result,
                rolling_counter=rolling_counter,
                duration_ms=_duration_ms(started_at),
            )
            session.commit()
            if self._retention_service is not None:
                self._retention_service.apply(
                    group,
                    watched_directory,
                    now=resolved_backup_time,
                )
            return result
        except ContentReadError as exc:
            self._logger.exception(
                "Error leyendo proyecto para respaldo",
                extra={
                    "group": group.name,
                    "directory": watched_directory.relative_path,
                },
            )
            result = self._record_error(
                history_repository=history_repository,
                audit_repository=audit_repository,
                watched_repository=watched_repository,
                group=group,
                watched_directory=watched_directory,
                backup_time=resolved_backup_time,
                backup_name=backup_name,
                status=AuditEvent.ERROR_READ.value,
                duration_ms=_duration_ms(started_at),
                message=str(exc),
                content_hash=content_hash,
                file_count=file_count,
            )
            session.commit()
            return result
        except ZipCreationError as exc:
            self._logger.exception(
                "Error creando ZIP de respaldo",
                extra={
                    "group": group.name,
                    "directory": watched_directory.relative_path,
                },
            )
            result = self._record_error(
                history_repository=history_repository,
                audit_repository=audit_repository,
                watched_repository=watched_repository,
                group=group,
                watched_directory=watched_directory,
                backup_time=resolved_backup_time,
                backup_name=backup_name,
                status=AuditEvent.ERROR_ZIP.value,
                duration_ms=_duration_ms(started_at),
                message=str(exc),
                content_hash=content_hash,
                file_count=file_count,
            )
            session.commit()
            return result

    def _record_no_effective_changes(
        self,
        *,
        history_repository: BackupHistoryRepository,
        audit_repository: AuditRepository,
        watched_repository: WatchedDirectoryRepository,
        group: BackupGroup,
        watched_directory: WatchedDirectory,
        backup_time: datetime,
        hash_result: ContentHashResult,
        duration_ms: int,
    ) -> BackupResult:
        status = AuditEvent.NO_EFFECTIVE_CHANGES.value
        history_repository.add_record(
            group_id=group.id,
            watched_directory_id=watched_directory.id,
            backup_name="",
            backup_path="",
            backup_time=backup_time,
            backup_size_bytes=0,
            file_count=hash_result.file_count,
            content_hash=hash_result.content_hash,
            status=status,
            duration_ms=duration_ms,
            message="Sin cambios efectivos.",
        )
        audit_repository.add_event(
            AuditEvent.BACKUP_SKIPPED.value,
            status,
            group_id=group.id,
            watched_directory_id=watched_directory.id,
        )
        watched_directory.last_content_hash = hash_result.content_hash
        watched_repository.clear_pending(watched_directory)
        return BackupResult(
            status=status,
            backup_name="",
            backup_path=None,
            content_hash=hash_result.content_hash,
            file_count=hash_result.file_count,
        )

    def _record_backup_ok(
        self,
        *,
        history_repository: BackupHistoryRepository,
        audit_repository: AuditRepository,
        watched_repository: WatchedDirectoryRepository,
        group: BackupGroup,
        watched_directory: WatchedDirectory,
        backup_time: datetime,
        backup_name: str,
        backup_path: Path,
        backup_size_bytes: int,
        hash_result: ContentHashResult,
        rolling_counter: int,
        duration_ms: int,
    ) -> BackupResult:
        status = AuditEvent.BACKUP_OK.value
        history_repository.add_record(
            group_id=group.id,
            watched_directory_id=watched_directory.id,
            backup_name=backup_name,
            backup_path=str(backup_path),
            backup_time=backup_time,
            backup_size_bytes=backup_size_bytes,
            file_count=hash_result.file_count,
            content_hash=hash_result.content_hash,
            status=status,
            duration_ms=duration_ms,
            message=None,
        )
        audit_repository.add_event(
            status,
            status,
            group_id=group.id,
            watched_directory_id=watched_directory.id,
            details=str(backup_path),
        )
        watched_repository.clear_pending(watched_directory)
        watched_directory.last_backup_at = backup_time
        watched_directory.last_content_hash = hash_result.content_hash
        watched_directory.rolling_counter = (rolling_counter + 1) % 1000
        return BackupResult(
            status=status,
            backup_name=backup_name,
            backup_path=backup_path,
            content_hash=hash_result.content_hash,
            file_count=hash_result.file_count,
        )

    def _record_error(
        self,
        *,
        history_repository: BackupHistoryRepository,
        audit_repository: AuditRepository,
        watched_repository: WatchedDirectoryRepository,
        group: BackupGroup,
        watched_directory: WatchedDirectory,
        backup_time: datetime,
        backup_name: str,
        status: str,
        duration_ms: int,
        message: str,
        content_hash: str | None,
        file_count: int | None,
    ) -> BackupResult:
        history_repository.add_record(
            group_id=group.id,
            watched_directory_id=watched_directory.id,
            backup_name=backup_name,
            backup_path="",
            backup_time=backup_time,
            backup_size_bytes=None,
            file_count=file_count,
            content_hash=content_hash,
            status=status,
            duration_ms=duration_ms,
            message=message,
        )
        audit_repository.add_event(
            status,
            status,
            group_id=group.id,
            watched_directory_id=watched_directory.id,
            details=message,
        )
        watched_repository.mark_error(watched_directory)
        return BackupResult(
            status=status,
            backup_name=backup_name,
            backup_path=None,
            content_hash=content_hash,
            file_count=file_count,
        )


def _backup_name(
    relative_path: str,
    backup_time: datetime,
    rolling_counter: int,
) -> str:
    safe_name = relative_path.replace("\\", "_").replace("/", "_")
    return f"{safe_name}_{backup_timestamp(backup_time)}_R{rolling_counter:03d}.zip"


def _manifest(
    *,
    settings: AppSettings,
    group: BackupGroup,
    watched_directory: WatchedDirectory,
    backup_name: str,
    backup_time: datetime,
    rolling_counter: int,
    hash_result: ContentHashResult,
) -> dict[str, object]:
    return {
        "program": settings.app_name,
        "version": settings.app_version,
        "group": {"id": group.id, "name": group.name},
        "group_id": group.id,
        "group_name": group.name,
        "root_directory": group.root_directory,
        "relative_path": watched_directory.relative_path,
        "backup_name": backup_name,
        "backup_time": backup_time.isoformat(),
        "rolling_counter": rolling_counter,
        "content_hash": hash_result.content_hash,
        "file_count": hash_result.file_count,
        "compression_level": group.compression_level,
    }


def _verify_created_backup(
    *,
    backup_path: Path,
    expected_project_root_name: str,
    expected_content_hash: str,
    expected_file_count: int,
) -> None:
    if not backup_path.is_file():
        raise ZipCreationError(f"No se encontro el ZIP creado: {backup_path}")

    try:
        backup_size = backup_path.stat().st_size
    except OSError as exc:
        raise ZipCreationError(f"No se pudo leer el ZIP creado: {backup_path}") from exc

    if backup_size <= 0:
        raise ZipCreationError(f"El ZIP creado esta vacio: {backup_path}")

    manifest_name = f"{expected_project_root_name}/manifest.json"
    try:
        with ZipFile(backup_path) as backup_zip:
            corrupt_member = backup_zip.testzip()
            if corrupt_member is not None:
                raise ZipCreationError(
                    f"El ZIP creado contiene un archivo corrupto: {corrupt_member}"
                )
            names = set(backup_zip.namelist())
            if manifest_name not in names:
                raise ZipCreationError("El ZIP creado no contiene manifest.json")
            manifest = json.loads(backup_zip.read(manifest_name).decode("utf-8"))
            if not isinstance(manifest, dict):
                raise ZipCreationError("El manifest del ZIP creado es invalido")
            if manifest.get("relative_path") != expected_project_root_name:
                raise ZipCreationError("El manifest no coincide con el proyecto")
            if manifest.get("content_hash") != expected_content_hash:
                raise ZipCreationError("El hash del manifest no coincide")
            if manifest.get("file_count") != expected_file_count:
                raise ZipCreationError("El conteo de archivos del manifest no coincide")
    except BadZipFile as exc:
        raise ZipCreationError(
            f"El ZIP creado no puede abrirse: {backup_path}"
        ) from exc
    except RuntimeError as exc:
        raise ZipCreationError(
            f"El ZIP creado no puede leerse completamente: {backup_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ZipCreationError("El manifest del ZIP creado no es JSON valido") from exc
    except UnicodeDecodeError as exc:
        raise ZipCreationError(
            "El manifest del ZIP creado no usa UTF-8 valido"
        ) from exc
    except ZipCreationError:
        raise
    except OSError as exc:
        raise ZipCreationError(
            f"No se pudo verificar el ZIP creado: {backup_path}"
        ) from exc


def _duration_ms(started_at: float) -> int:
    return int((perf_counter() - started_at) * 1000)
