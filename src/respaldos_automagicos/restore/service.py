"""Safe ZIP restore service."""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from zipfile import BadZipFile, ZipFile

from sqlalchemy.orm import Session, sessionmaker

from respaldos_automagicos.models.backup_group import BackupGroup
from respaldos_automagicos.models.backup_history import BackupHistory
from respaldos_automagicos.models.enums import AuditEvent
from respaldos_automagicos.models.mixins import utc_now
from respaldos_automagicos.models.watched_directory import WatchedDirectory
from respaldos_automagicos.repositories.audit import AuditRepository
from respaldos_automagicos.repositories.backup_history import BackupHistoryRepository
from respaldos_automagicos.utils.time import backup_timestamp


class RestoreValidationError(ValueError):
    """Raised when a backup cannot be restored safely."""


@dataclass(frozen=True, slots=True)
class RestoreResult:
    """Result of a restore attempt."""

    status: str
    restored_path: Path | None
    renamed_existing_path: Path | None
    message: str
    duration_ms: int


class RestoreService:
    """Validates and restores ZIP backups without overwriting existing data."""

    _REQUIRED_MANIFEST_FIELDS = {
        "program",
        "version",
        "group",
        "relative_path",
        "content_hash",
        "backup_time",
        "rolling_counter",
        "file_count",
    }

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        """Create the restore service."""
        self._session_factory = session_factory

    def restore(
        self,
        backup_history_id: int,
        *,
        restore_time: datetime | None = None,
    ) -> RestoreResult:
        """Restore a retained successful backup by id."""
        started_at = perf_counter()
        restored_at = restore_time or utc_now()
        with self._session_factory() as session:
            history = BackupHistoryRepository(session).get_available_successful(
                backup_history_id
            )
            if (
                history is None
                or history.group is None
                or history.watched_directory is None
            ):
                return self._abort_missing_history(
                    session=session,
                    backup_history_id=backup_history_id,
                    duration_ms=_duration_ms(started_at),
                )

            group = history.group
            watched_directory = history.watched_directory
            audit_repository = AuditRepository(session)
            audit_repository.add_event(
                AuditEvent.RESTORE_STARTED.value,
                AuditEvent.RESTORE_STARTED.value,
                group_id=group.id,
                watched_directory_id=watched_directory.id,
                details=(
                    f"Grupo={group.name}. Proyecto={watched_directory.relative_path}. "
                    f"Respaldo={history.backup_name}. Motivo=inicio. Duracion_ms=0."
                ),
            )
            session.commit()

            try:
                manifest = self._validate_backup(history, group, watched_directory)
                restored_path, renamed_path = self._extract_backup(
                    history=history,
                    group=group,
                    watched_directory=watched_directory,
                    restore_time=restored_at,
                )
            except RestoreValidationError as exc:
                return self._record_failure(
                    session=session,
                    group=group,
                    watched_directory=watched_directory,
                    history=history,
                    status=AuditEvent.RESTORE_ABORTED.value,
                    message=str(exc),
                    duration_ms=_duration_ms(started_at),
                )
            except OSError as exc:
                return self._record_failure(
                    session=session,
                    group=group,
                    watched_directory=watched_directory,
                    history=history,
                    status=AuditEvent.RESTORE_ERROR.value,
                    message=str(exc),
                    duration_ms=_duration_ms(started_at),
                )

            BackupHistoryRepository(session).mark_restored(
                history,
                restored_at=restored_at,
            )
            duration_ms = _duration_ms(started_at)
            audit_repository.add_event(
                AuditEvent.RESTORE_OK.value,
                AuditEvent.RESTORE_OK.value,
                group_id=group.id,
                watched_directory_id=watched_directory.id,
                details=(
                    f"Grupo={group.name}. Proyecto={watched_directory.relative_path}. "
                    f"Respaldo={history.backup_name}. Motivo=restauracion completada. "
                    f"Destino={restored_path}. Duracion_ms={duration_ms}. "
                    f"Manifest={manifest['backup_time']}."
                ),
            )
            session.commit()
            return RestoreResult(
                status=AuditEvent.RESTORE_OK.value,
                restored_path=restored_path,
                renamed_existing_path=renamed_path,
                message="Restauracion completada.",
                duration_ms=duration_ms,
            )

    def _validate_backup(
        self,
        history: BackupHistory,
        group: BackupGroup,
        watched_directory: WatchedDirectory,
    ) -> dict[str, object]:
        backup_path = Path(history.backup_path)
        if not backup_path.is_file():
            raise RestoreValidationError(f"El ZIP no existe: {backup_path}.")

        try:
            with ZipFile(backup_path) as backup_zip:
                names = backup_zip.namelist()
                corrupt_member = backup_zip.testzip()
                if corrupt_member is not None:
                    raise RestoreValidationError(
                        f"El ZIP contiene un archivo corrupto: {corrupt_member}."
                    )
                manifest_name = f"{watched_directory.relative_path}/manifest.json"
                if manifest_name not in names:
                    raise RestoreValidationError("manifest.json no existe en el ZIP.")
                manifest = json.loads(backup_zip.read(manifest_name).decode("utf-8"))
                if not isinstance(manifest, dict):
                    raise RestoreValidationError(
                        "manifest.json no es un objeto valido."
                    )
                manifest = self._normalize_manifest(manifest)
                missing = self._REQUIRED_MANIFEST_FIELDS.difference(manifest)
                if missing:
                    raise RestoreValidationError(
                        "manifest.json no contiene campos obligatorios: "
                        + ", ".join(sorted(missing))
                    )
                if manifest["relative_path"] != watched_directory.relative_path:
                    raise RestoreValidationError(
                        "El manifest no coincide con el proyecto seleccionado."
                    )
                self._validate_zip_root(names, watched_directory.relative_path)
                self._validate_group_manifest(manifest, group)
                return manifest
        except BadZipFile as exc:
            raise RestoreValidationError(
                "El ZIP esta corrupto o no puede abrirse."
            ) from exc
        except RuntimeError as exc:
            raise RestoreValidationError(
                "El ZIP no puede abrirse completamente."
            ) from exc
        except json.JSONDecodeError as exc:
            raise RestoreValidationError("manifest.json no es JSON valido.") from exc
        except UnicodeDecodeError as exc:
            raise RestoreValidationError("manifest.json no usa UTF-8 valido.") from exc

    @staticmethod
    def _normalize_manifest(manifest: dict[str, object]) -> dict[str, object]:
        if (
            "group" not in manifest
            and "group_id" in manifest
            and "group_name" in manifest
        ):
            return {
                **manifest,
                "group": {
                    "id": manifest["group_id"],
                    "name": manifest["group_name"],
                },
            }
        return manifest

    @staticmethod
    def _validate_zip_root(names: list[str], relative_path: str) -> None:
        expected_prefix = f"{relative_path}/"
        for name in names:
            normalized = name.replace("\\", "/")
            if (
                normalized.startswith("/")
                or ".." in Path(normalized).parts
                or any(":" in part for part in Path(normalized).parts)
            ):
                raise RestoreValidationError("El ZIP contiene rutas inseguras.")
            if not normalized.startswith(expected_prefix):
                raise RestoreValidationError(
                    "El directorio raiz del ZIP no coincide con el proyecto."
                )

    @staticmethod
    def _validate_group_manifest(
        manifest: dict[str, object],
        group: BackupGroup,
    ) -> None:
        group_value = manifest["group"]
        if not isinstance(group_value, dict):
            raise RestoreValidationError("El campo group del manifest es invalido.")
        if group_value.get("id") != group.id or group_value.get("name") != group.name:
            raise RestoreValidationError("El manifest no coincide con el grupo.")

    def _extract_backup(
        self,
        *,
        history: BackupHistory,
        group: BackupGroup,
        watched_directory: WatchedDirectory,
        restore_time: datetime,
    ) -> tuple[Path, Path | None]:
        root_directory = Path(group.root_directory)
        project_path = root_directory / watched_directory.relative_path
        renamed_path: Path | None = None

        if project_path.exists():
            renamed_path = _available_cucho_path(project_path, restore_time)
            project_path.rename(renamed_path)

        with ZipFile(history.backup_path) as backup_zip:
            for member in backup_zip.infolist():
                normalized = member.filename.replace("\\", "/")
                target_path = root_directory / normalized
                resolved_target = target_path.resolve()
                resolved_root = root_directory.resolve()
                if (
                    resolved_target != resolved_root
                    and resolved_root not in resolved_target.parents
                ):
                    raise RestoreValidationError(
                        "El ZIP intenta extraer fuera de la raiz."
                    )
                if member.is_dir():
                    target_path.mkdir(parents=True, exist_ok=True)
                else:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with (
                        backup_zip.open(member) as source,
                        target_path.open("wb") as target,
                    ):
                        target.write(source.read())

        if not project_path.is_dir():
            raise RestoreValidationError(
                "La extraccion no produjo el proyecto esperado."
            )
        return project_path, renamed_path

    def _abort_missing_history(
        self,
        *,
        session: Session,
        backup_history_id: int,
        duration_ms: int,
    ) -> RestoreResult:
        AuditRepository(session).add_event(
            AuditEvent.RESTORE_ABORTED.value,
            AuditEvent.RESTORE_ABORTED.value,
            details=(
                f"No se encontro respaldo disponible para restaurar. "
                f"Respaldo={backup_history_id}. Motivo=no disponible. "
                f"Duracion_ms={duration_ms}."
            ),
        )
        session.commit()
        return RestoreResult(
            status=AuditEvent.RESTORE_ABORTED.value,
            restored_path=None,
            renamed_existing_path=None,
            message="No se encontro respaldo disponible.",
            duration_ms=duration_ms,
        )

    def _record_failure(
        self,
        *,
        session: Session,
        group: BackupGroup,
        watched_directory: WatchedDirectory,
        history: BackupHistory,
        status: str,
        message: str,
        duration_ms: int,
    ) -> RestoreResult:
        AuditRepository(session).add_event(
            status,
            status,
            group_id=group.id,
            watched_directory_id=watched_directory.id,
            details=(
                f"Grupo={group.name}. Proyecto={watched_directory.relative_path}. "
                f"Respaldo={history.backup_name}. Motivo={message}. Duracion_ms={duration_ms}."
            ),
        )
        session.commit()
        return RestoreResult(
            status=status,
            restored_path=None,
            renamed_existing_path=None,
            message=message,
            duration_ms=duration_ms,
        )


def _available_cucho_path(project_path: Path, restore_time: datetime) -> Path:
    base = project_path.with_name(
        f"{project_path.name}.cucho_{backup_timestamp(restore_time)}"
    )
    candidate = base
    suffix = 1
    while candidate.exists():
        candidate = base.with_name(f"{base.name}_{suffix}")
        suffix += 1
    return candidate


def _duration_ms(started_at: float) -> int:
    return int((perf_counter() - started_at) * 1000)
