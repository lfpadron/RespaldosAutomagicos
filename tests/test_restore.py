"""Tests for safe restore workflows."""

import json
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZipFile

import pytest
from sqlalchemy import select

from respaldos_automagicos.app import RespaldosAutomagicosApplication, create_app
from respaldos_automagicos.config import AppSettings
from respaldos_automagicos.controllers.restore import (
    RestoreController,
    RestoreControllerError,
)
from respaldos_automagicos.models.audit_log import AuditLog
from respaldos_automagicos.models.backup_group import BackupGroup
from respaldos_automagicos.models.backup_history import BackupHistory
from respaldos_automagicos.models.enums import AuditEvent
from respaldos_automagicos.models.watched_directory import WatchedDirectory
from respaldos_automagicos.repositories.backup_groups import BackupGroupRepository
from respaldos_automagicos.repositories.backup_history import BackupHistoryRepository
from respaldos_automagicos.repositories.watched_directories import (
    WatchedDirectoryRepository,
)


def sqlite_url(path: Path) -> str:
    """Build a SQLite URL from a pytest temporary path."""
    return f"sqlite:///{path.as_posix()}"


def make_test_app(tmp_path: Path) -> RespaldosAutomagicosApplication:
    """Create an initialized application for restore tests."""
    settings = AppSettings(
        database_url=sqlite_url(tmp_path / "restore.db"),
        logs_dir=tmp_path / "logs",
    )
    app = create_app(settings)
    app.initialize_storage()
    return app


def create_group_and_project(
    app: RespaldosAutomagicosApplication,
    *,
    tmp_path: Path,
) -> tuple[BackupGroup, WatchedDirectory]:
    """Create a backup group and watched directory without project files."""
    root = tmp_path / "root"
    destination = tmp_path / "dest"
    root.mkdir()
    destination.mkdir()
    with app.session_factory() as session:
        group = BackupGroup(
            name="Grupo",
            root_directory=str(root),
            destination_directory=str(destination),
        )
        BackupGroupRepository(session).add(group)
        session.flush()
        watched = WatchedDirectoryRepository(session).get_or_create(
            group.id,
            "ProyectoA",
        )
        session.commit()
        return group, watched


def manifest_for(
    group: BackupGroup,
    *,
    relative_path: str = "ProyectoA",
) -> dict[str, object]:
    """Build a valid restore manifest."""
    return {
        "program": "RespaldosAutomagicos",
        "version": "1.0.0",
        "group": {"id": group.id, "name": group.name},
        "relative_path": relative_path,
        "content_hash": "abcdef1234567890",
        "backup_time": "2026-06-24T14:56:32+00:00",
        "rolling_counter": 7,
        "file_count": 2,
    }


def create_backup_zip(
    backup_path: Path,
    group: BackupGroup,
    *,
    relative_path: str = "ProyectoA",
    manifest: dict[str, object] | str | None = None,
    include_manifest: bool = True,
) -> None:
    """Create a ZIP backup fixture."""
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_data = manifest if manifest is not None else manifest_for(group)
    with ZipFile(backup_path, "w") as backup_zip:
        if include_manifest:
            encoded_manifest = (
                manifest_data
                if isinstance(manifest_data, str)
                else json.dumps(manifest_data)
            )
            backup_zip.writestr(
                f"{relative_path}/manifest.json",
                encoded_manifest,
            )
        backup_zip.writestr(f"{relative_path}/main.txt", "restaurado\n")


def create_history(
    app: RespaldosAutomagicosApplication,
    *,
    group: BackupGroup,
    watched: WatchedDirectory,
    backup_path: Path,
    backup_time: datetime,
) -> int:
    """Create one retained successful backup history row."""
    with app.session_factory() as session:
        record = BackupHistoryRepository(session).add_record(
            group_id=group.id,
            watched_directory_id=watched.id,
            backup_name=backup_path.name,
            backup_path=str(backup_path),
            backup_time=backup_time,
            backup_size_bytes=backup_path.stat().st_size if backup_path.exists() else 0,
            file_count=2,
            content_hash="abcdef1234567890",
            status=AuditEvent.BACKUP_OK.value,
            duration_ms=5,
        )
        session.flush()
        record_id = record.id
        session.commit()
        return record_id


def get_history(app: RespaldosAutomagicosApplication, record_id: int) -> BackupHistory:
    """Return a backup history row by id."""
    with app.session_factory() as session:
        record = session.get(BackupHistory, record_id)
        assert record is not None
        return record


def audit_events(app: RespaldosAutomagicosApplication) -> list[AuditLog]:
    """Return audit events ordered by id."""
    with app.session_factory() as session:
        return list(session.scalars(select(AuditLog).order_by(AuditLog.id)))


def test_restore_succeeds_and_updates_history_and_audit(tmp_path: Path) -> None:
    """A valid backup restores the project and records metadata."""
    app = make_test_app(tmp_path)
    group, watched = create_group_and_project(app, tmp_path=tmp_path)
    backup_path = tmp_path / "backups" / "ProyectoA_20260624_145632_R007.zip"
    create_backup_zip(backup_path, group)
    history_id = create_history(
        app,
        group=group,
        watched=watched,
        backup_path=backup_path,
        backup_time=datetime(2026, 6, 24, 14, 56, 32, tzinfo=UTC),
    )
    restore_time = datetime(2026, 6, 24, 15, 0, 0, tzinfo=UTC)

    result = app.restore_service.restore(history_id, restore_time=restore_time)

    restored_file = Path(group.root_directory) / "ProyectoA" / "main.txt"
    stored = get_history(app, history_id)
    actions = [event.action for event in audit_events(app)]
    assert result.status == AuditEvent.RESTORE_OK.value
    assert result.restored_path == Path(group.root_directory) / "ProyectoA"
    assert result.renamed_existing_path is None
    assert restored_file.read_text(encoding="utf-8") == "restaurado\n"
    assert stored.restore_count == 1
    assert stored.last_restored_at is not None
    assert actions == [
        AuditEvent.RESTORE_STARTED.value,
        AuditEvent.RESTORE_OK.value,
    ]


def test_restore_renames_existing_project_before_extracting(tmp_path: Path) -> None:
    """Existing project data is renamed instead of overwritten."""
    app = make_test_app(tmp_path)
    group, watched = create_group_and_project(app, tmp_path=tmp_path)
    existing_project = Path(group.root_directory) / "ProyectoA"
    existing_project.mkdir()
    (existing_project / "old.txt").write_text("anterior\n", encoding="utf-8")
    backup_path = tmp_path / "backups" / "ProyectoA_20260624_145632_R007.zip"
    create_backup_zip(backup_path, group)
    history_id = create_history(
        app,
        group=group,
        watched=watched,
        backup_path=backup_path,
        backup_time=datetime(2026, 6, 24, 14, 56, 32, tzinfo=UTC),
    )
    restore_time = datetime(2026, 6, 24, 16, 2, 3, tzinfo=UTC)

    result = app.restore_service.restore(history_id, restore_time=restore_time)

    assert result.status == AuditEvent.RESTORE_OK.value
    assert result.renamed_existing_path is not None
    assert result.renamed_existing_path.name == "ProyectoA.cucho_20260624_160203"
    assert (result.renamed_existing_path / "old.txt").read_text(
        encoding="utf-8"
    ) == "anterior\n"
    assert (existing_project / "main.txt").read_text(encoding="utf-8") == "restaurado\n"


def test_restore_aborts_when_zip_is_corrupt(tmp_path: Path) -> None:
    """A corrupt ZIP is rejected before extraction."""
    app = make_test_app(tmp_path)
    group, watched = create_group_and_project(app, tmp_path=tmp_path)
    backup_path = tmp_path / "backups" / "corrupt.zip"
    backup_path.parent.mkdir()
    backup_path.write_bytes(b"no es zip")
    history_id = create_history(
        app,
        group=group,
        watched=watched,
        backup_path=backup_path,
        backup_time=datetime(2026, 6, 24, 14, 56, 32, tzinfo=UTC),
    )

    result = app.restore_service.restore(history_id)

    assert result.status == AuditEvent.RESTORE_ABORTED.value
    assert not (Path(group.root_directory) / "ProyectoA").exists()
    assert audit_events(app)[-1].action == AuditEvent.RESTORE_ABORTED.value


def test_restore_aborts_when_manifest_is_missing(tmp_path: Path) -> None:
    """A ZIP without manifest.json is rejected."""
    app = make_test_app(tmp_path)
    group, watched = create_group_and_project(app, tmp_path=tmp_path)
    backup_path = tmp_path / "backups" / "sin_manifest.zip"
    create_backup_zip(backup_path, group, include_manifest=False)
    history_id = create_history(
        app,
        group=group,
        watched=watched,
        backup_path=backup_path,
        backup_time=datetime(2026, 6, 24, 14, 56, 32, tzinfo=UTC),
    )

    result = app.restore_service.restore(history_id)

    assert result.status == AuditEvent.RESTORE_ABORTED.value
    assert "manifest.json no existe" in result.message


def test_restore_aborts_when_manifest_json_is_invalid(tmp_path: Path) -> None:
    """A ZIP with invalid manifest JSON is rejected."""
    app = make_test_app(tmp_path)
    group, watched = create_group_and_project(app, tmp_path=tmp_path)
    backup_path = tmp_path / "backups" / "manifest_invalido.zip"
    create_backup_zip(backup_path, group, manifest="{invalido")
    history_id = create_history(
        app,
        group=group,
        watched=watched,
        backup_path=backup_path,
        backup_time=datetime(2026, 6, 24, 14, 56, 32, tzinfo=UTC),
    )

    result = app.restore_service.restore(history_id)

    assert result.status == AuditEvent.RESTORE_ABORTED.value
    assert "JSON valido" in result.message


def test_restore_aborts_when_manifest_lacks_required_fields(tmp_path: Path) -> None:
    """A manifest missing required fields is rejected."""
    app = make_test_app(tmp_path)
    group, watched = create_group_and_project(app, tmp_path=tmp_path)
    backup_path = tmp_path / "backups" / "manifest_incompleto.zip"
    incomplete_manifest = manifest_for(group)
    del incomplete_manifest["group"]
    create_backup_zip(backup_path, group, manifest=incomplete_manifest)
    history_id = create_history(
        app,
        group=group,
        watched=watched,
        backup_path=backup_path,
        backup_time=datetime(2026, 6, 24, 14, 56, 32, tzinfo=UTC),
    )

    result = app.restore_service.restore(history_id)

    assert result.status == AuditEvent.RESTORE_ABORTED.value
    assert "campos obligatorios" in result.message


def test_restore_accepts_legacy_manifest_group_fields(tmp_path: Path) -> None:
    """Legacy manifests with group_id and group_name can still be restored."""
    app = make_test_app(tmp_path)
    group, watched = create_group_and_project(app, tmp_path=tmp_path)
    backup_path = tmp_path / "backups" / "ProyectoA_20260624_145632_R007.zip"
    legacy_manifest = manifest_for(group)
    del legacy_manifest["group"]
    legacy_manifest["group_id"] = group.id
    legacy_manifest["group_name"] = group.name
    create_backup_zip(backup_path, group, manifest=legacy_manifest)
    history_id = create_history(
        app,
        group=group,
        watched=watched,
        backup_path=backup_path,
        backup_time=datetime(2026, 6, 24, 14, 56, 32, tzinfo=UTC),
    )

    result = app.restore_service.restore(history_id)

    assert result.status == AuditEvent.RESTORE_OK.value
    assert (Path(group.root_directory) / "ProyectoA" / "main.txt").exists()


def test_restore_controller_blocks_missing_zip(tmp_path: Path) -> None:
    """The controller marks missing physical ZIPs as unavailable."""
    app = make_test_app(tmp_path)
    group, watched = create_group_and_project(app, tmp_path=tmp_path)
    backup_path = tmp_path / "backups" / "no_existe.zip"
    history_id = create_history(
        app,
        group=group,
        watched=watched,
        backup_path=backup_path,
        backup_time=datetime(2026, 6, 24, 14, 56, 32, tzinfo=UTC),
    )
    controller = RestoreController(
        session_factory=app.session_factory,
        restore_service=app.restore_service,
    )

    versions = controller.list_versions(
        group_id=group.id,
        watched_directory_id=watched.id,
    )

    assert versions[0].available is False
    assert versions[0].availability_label == "NO DISPONIBLE"
    with pytest.raises(RestoreControllerError):
        controller.restore(history_id)


def test_restore_controller_lists_summary_and_restores(tmp_path: Path) -> None:
    """RestoreController exposes the complete UI workflow."""
    app = make_test_app(tmp_path)
    group, watched = create_group_and_project(app, tmp_path=tmp_path)
    backup_path = tmp_path / "backups" / "ProyectoA_20260624_145632_R007.zip"
    create_backup_zip(backup_path, group)
    history_id = create_history(
        app,
        group=group,
        watched=watched,
        backup_path=backup_path,
        backup_time=datetime(2026, 6, 24, 14, 56, 32, tzinfo=UTC),
    )
    controller = RestoreController(
        session_factory=app.session_factory,
        restore_service=app.restore_service,
    )

    groups = controller.list_groups()
    projects = controller.list_projects(group.id)
    versions = controller.list_versions(
        group_id=group.id,
        watched_directory_id=watched.id,
    )
    summary = controller.summary(history_id)
    result = controller.restore(history_id)

    assert [item.name for item in groups] == ["Grupo"]
    assert [item.relative_path for item in projects] == ["ProyectoA"]
    assert versions[0].rolling_label == "R007"
    assert versions[0].short_hash == "abcdef123456"
    assert summary.available is True
    assert summary.project_name == "ProyectoA"
    assert result.status == AuditEvent.RESTORE_OK.value
