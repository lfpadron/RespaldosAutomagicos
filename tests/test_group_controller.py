"""Tests for group controllers and repository workflows."""

from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from respaldos_automagicos.app import RespaldosAutomagicosApplication, create_app
from respaldos_automagicos.config import AppSettings
from respaldos_automagicos.controllers.groups import (
    BackupGroupFormData,
    GroupController,
    GroupValidationError,
)
from respaldos_automagicos.models.enums import AuditEvent, WatchedDirectoryStatus
from respaldos_automagicos.repositories.backup_groups import BackupGroupRepository
from respaldos_automagicos.repositories.watched_directories import (
    WatchedDirectoryRepository,
)


@dataclass(slots=True)
class FakeWatcher:
    """Collects watcher restart and stop calls."""

    restarted: list[int] = field(default_factory=list)
    stopped: list[int] = field(default_factory=list)

    def restart_group(self, group_id: int) -> None:
        """Record a group restart."""
        self.restarted.append(group_id)

    def stop_group(self, group_id: int) -> None:
        """Record a group stop."""
        self.stopped.append(group_id)


def sqlite_url(path: Path) -> str:
    """Build a SQLite URL from a pytest temporary path."""
    return f"sqlite:///{path.as_posix()}"


def make_test_app(tmp_path: Path) -> RespaldosAutomagicosApplication:
    """Create an initialized app for controller tests."""
    settings = AppSettings(
        database_url=sqlite_url(tmp_path / "groups.db"),
        logs_dir=tmp_path / "logs",
    )
    app = create_app(settings)
    app.initialize_storage()
    return app


def make_controller(
    app: RespaldosAutomagicosApplication,
    watcher: FakeWatcher,
) -> GroupController:
    """Create a group controller with a fake watcher."""
    return GroupController(
        session_factory=app.session_factory,
        backup_service=app.backup_service,
        watcher_service=watcher,  # type: ignore[arg-type]
    )


def form_data(
    root: Path,
    destination: Path,
    name: str = "Principal",
    timezone: str = "UTC",
) -> BackupGroupFormData:
    """Return valid group form data."""
    return BackupGroupFormData(
        name=name,
        root_directory=str(root),
        destination_directory=str(destination),
        timezone=timezone,
        scan_interval_minutes=15,
        stabilization_minutes=5,
        backups_to_keep=10,
        days_to_keep=30,
        compression_level=6,
        enabled=True,
    )


def test_group_controller_crud_and_observer_restart(tmp_path: Path) -> None:
    """Groups can be created, updated, toggled, and logically deleted."""
    root = tmp_path / "root"
    destination = tmp_path / "dest"
    root.mkdir()
    destination.mkdir()
    app = make_test_app(tmp_path)
    watcher = FakeWatcher()
    controller = make_controller(app, watcher)

    group = controller.create_group(form_data(root, destination))
    assert watcher.restarted == [group.id]

    controller.update_group(group.id, form_data(root, destination, name="Editado"))
    controller.deactivate_group(group.id)
    controller.activate_group(group.id)
    controller.delete_group(group.id)

    assert watcher.stopped == [group.id, group.id]
    assert watcher.restarted == [group.id, group.id, group.id]
    assert controller.list_groups() == []
    with app.session_factory() as session:
        stored = BackupGroupRepository(session).get(group.id)
        assert stored is not None
        assert stored.deleted_at is not None


def test_group_validation_reports_friendly_errors(tmp_path: Path) -> None:
    """Invalid form data raises user-facing validation messages."""
    destination = tmp_path / "dest"
    destination.mkdir()
    app = make_test_app(tmp_path)
    watcher = FakeWatcher()
    controller = make_controller(app, watcher)

    with pytest.raises(GroupValidationError) as exc_info:
        controller.create_group(
            BackupGroupFormData(
                name="",
                root_directory=str(tmp_path / "missing-root"),
                destination_directory=str(destination),
                scan_interval_minutes=4,
                stabilization_minutes=5,
                backups_to_keep=0,
                days_to_keep=0,
                compression_level=6,
                enabled=True,
            )
        )

    assert "El nombre es obligatorio." in exc_info.value.errors
    assert "El directorio raiz debe existir." in exc_info.value.errors
    assert (
        "El intervalo de escaneo debe ser de al menos 5 minutos."
        in exc_info.value.errors
    )
    assert "La estabilizacion debe ser menor que el intervalo." in exc_info.value.errors


def test_group_names_must_be_unique(tmp_path: Path) -> None:
    """The controller rejects duplicate active group names."""
    root = tmp_path / "root"
    destination = tmp_path / "dest"
    root.mkdir()
    destination.mkdir()
    app = make_test_app(tmp_path)
    controller = make_controller(app, FakeWatcher())

    controller.create_group(form_data(root, destination, name="Principal"))
    with pytest.raises(GroupValidationError) as exc_info:
        controller.create_group(form_data(root, destination, name="Principal"))

    assert "Ya existe un grupo con ese nombre." in exc_info.value.errors


def test_group_timezone_is_validated_and_canonicalized(tmp_path: Path) -> None:
    """Group time zones must exist in Python's zoneinfo database."""
    root = tmp_path / "root"
    destination = tmp_path / "dest"
    root.mkdir()
    destination.mkdir()
    app = make_test_app(tmp_path)
    controller = make_controller(app, FakeWatcher())

    group = controller.create_group(
        form_data(root, destination, timezone="america/mexico_city")
    )

    assert controller.get_form_data(group.id).timezone == "America/Mexico_City"
    with pytest.raises(GroupValidationError) as exc_info:
        controller.update_group(
            group.id,
            replace(
                controller.get_form_data(group.id),
                timezone="Americas/Mexico_city",
            ),
        )

    assert any("zona horaria" in error for error in exc_info.value.errors)


def test_scan_projects_creates_and_marks_missing_inactive(tmp_path: Path) -> None:
    """Scanning creates watched projects and marks disappeared projects inactive."""
    root = tmp_path / "root"
    destination = tmp_path / "dest"
    (root / "ProyectoA").mkdir(parents=True)
    (root / "ProyectoB").mkdir()
    destination.mkdir()
    app = make_test_app(tmp_path)
    controller = make_controller(app, FakeWatcher())
    group = controller.create_group(form_data(root, destination))

    first = controller.scan_projects(group.id)
    (root / "ProyectoB").rmdir()
    second = controller.scan_projects(group.id)

    assert first.created == 2
    assert second.deactivated == 1
    with app.session_factory() as session:
        proyecto_b = WatchedDirectoryRepository(session).get_by_group_and_relative_path(
            group.id,
            "ProyectoB",
        )
        assert proyecto_b is not None
        assert proyecto_b.status == WatchedDirectoryStatus.IGNORED.value


def test_duplicate_group_creates_unique_copy(tmp_path: Path) -> None:
    """Duplicating a group preserves settings and picks a unique name."""
    root = tmp_path / "root"
    destination = tmp_path / "dest"
    root.mkdir()
    destination.mkdir()
    app = make_test_app(tmp_path)
    watcher = FakeWatcher()
    controller = make_controller(app, watcher)
    group = controller.create_group(form_data(root, destination, name="Principal"))

    duplicate = controller.duplicate_group(group.id)

    assert duplicate.name == "Principal copia 1"
    assert duplicate.root_directory == str(root)
    assert watcher.restarted == [group.id, duplicate.id]


def test_manual_backup_runs_backup_service(tmp_path: Path) -> None:
    """Manual backup runs immediately for watched projects."""
    root = tmp_path / "root"
    destination = tmp_path / "dest"
    project = root / "ProyectoA"
    project.mkdir(parents=True)
    destination.mkdir()
    (project / "main.py").write_text("print('ok')\n", encoding="utf-8")
    app = make_test_app(tmp_path)
    controller = make_controller(app, FakeWatcher())
    group = controller.create_group(form_data(root, destination))
    controller.scan_projects(group.id)

    results = controller.backup_now(group.id)

    assert [result.status for result in results] == [AuditEvent.BACKUP_OK.value]
    assert list((destination / "Principal" / "ProyectoA").glob("*.zip"))


def test_backup_group_repository_search_and_logical_delete(tmp_path: Path) -> None:
    """Repository list, search, get, and logical delete operations work."""
    root = tmp_path / "root"
    destination = tmp_path / "dest"
    root.mkdir()
    destination.mkdir()
    app = make_test_app(tmp_path)

    with app.session_factory() as session:
        repository = BackupGroupRepository(session)
        group = repository.create(
            name="Principal",
            root_directory=str(root),
            destination_directory=str(destination),
            enabled=True,
            scan_interval_minutes=15,
            stabilization_minutes=5,
            backups_to_keep=10,
            days_to_keep=30,
            compression_level=6,
        )
        session.flush()
        assert repository.get_active(group.id) is not None
        assert [item.name for item in repository.search("Prin")] == ["Principal"]
        assert [item.name for item in repository.list_all()] == ["Principal"]
        repository.logical_delete(group)
        session.commit()
        assert repository.get_active(group.id) is None
