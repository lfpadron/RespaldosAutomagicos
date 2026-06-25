"""Tests for multi-select group actions and manual backup jobs."""

from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Thread

from sqlalchemy import select

from respaldos_automagicos.app import RespaldosAutomagicosApplication, create_app
from respaldos_automagicos.config import AppSettings
from respaldos_automagicos.controllers import (
    BackupGroupFormData,
    GroupController,
    GroupSelectionState,
    ManualBackupJobController,
    ManualBackupState,
)
from respaldos_automagicos.models.audit_log import AuditLog
from respaldos_automagicos.models.backup_group import BackupGroup
from respaldos_automagicos.models.enums import AuditEvent
from respaldos_automagicos.models.watched_directory import WatchedDirectory
from respaldos_automagicos.tui.app import _selection_marker


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


@dataclass(slots=True)
class FakeBackupResult:
    """Backup result with a status field."""

    status: str


@dataclass(slots=True)
class FakeBackupService:
    """Collects backup calls and can simulate failures or blocking."""

    fail_group_ids: set[int] = field(default_factory=set)
    result_status_by_group_id: dict[int, str] = field(default_factory=dict)
    started_event: Event | None = None
    release_event: Event | None = None
    calls: list[tuple[int, str]] = field(default_factory=list)

    def create_backup(
        self,
        group: BackupGroup,
        watched_directory: WatchedDirectory,
    ) -> object:
        """Record a backup call."""
        self.calls.append((group.id, watched_directory.relative_path))
        if self.started_event is not None:
            self.started_event.set()
        if self.release_event is not None:
            self.release_event.wait(timeout=5)
        if group.id in self.fail_group_ids:
            raise RuntimeError("fallo simulado")
        status = self.result_status_by_group_id.get(group.id)
        if status is not None:
            return FakeBackupResult(status=status)
        return object()


def sqlite_url(path: Path) -> str:
    """Build a SQLite URL from a pytest temporary path."""
    return f"sqlite:///{path.as_posix()}"


def make_test_app(tmp_path: Path) -> RespaldosAutomagicosApplication:
    """Create an initialized app for manual backup job tests."""
    settings = AppSettings(
        database_url=sqlite_url(tmp_path / "manual-backup.db"),
        logs_dir=tmp_path / "logs",
    )
    app = create_app(settings)
    app.initialize_storage()
    return app


def form_data(root: Path, destination: Path, name: str) -> BackupGroupFormData:
    """Return valid group form data."""
    return BackupGroupFormData(
        name=name,
        root_directory=str(root),
        destination_directory=str(destination),
        scan_interval_minutes=15,
        stabilization_minutes=5,
        backups_to_keep=10,
        days_to_keep=30,
        compression_level=6,
        enabled=True,
    )


def create_group(
    app: RespaldosAutomagicosApplication,
    tmp_path: Path,
    *,
    name: str,
    project_names: list[str],
) -> BackupGroup:
    """Create a backup group with project directories."""
    root = tmp_path / f"{name}-root"
    destination = tmp_path / f"{name}-dest"
    root.mkdir()
    destination.mkdir()
    for project_name in project_names:
        (root / project_name).mkdir()
    controller = GroupController(
        session_factory=app.session_factory,
        backup_service=app.backup_service,
        watcher_service=FakeWatcher(),  # type: ignore[arg-type]
    )
    return controller.create_group(form_data(root, destination, name))


def audit_actions(app: RespaldosAutomagicosApplication) -> list[str]:
    """Return audit actions ordered by id."""
    with app.session_factory() as session:
        return [
            event.action
            for event in session.scalars(select(AuditLog).order_by(AuditLog.id))
        ]


def test_group_selection_toggles_and_clears() -> None:
    """Group selection supports checkbox-style toggling."""
    selection = GroupSelectionState()

    selection.toggle(1)
    assert selection.is_selected(1)
    selection.toggle(1)
    assert not selection.is_selected(1)
    selection.select_all([1, 2, 3])
    assert selection.selected_ids == {1, 2, 3}
    selection.clear()
    assert selection.selected_ids == set()


def test_group_selection_marker_uses_visible_x() -> None:
    """Selected rows use a visible X marker."""
    assert _selection_marker(True) == "X"
    assert _selection_marker(False) == ""


def test_group_selection_falls_back_to_highlighted_group() -> None:
    """Empty selection falls back to the highlighted group."""
    selection = GroupSelectionState()

    assert selection.selected_or_fallback(7) == [7]
    selection.toggle(2)
    selection.toggle(1)

    assert selection.selected_or_fallback(7) == [1, 2]


def test_group_batch_actions_apply_to_selected_groups(tmp_path: Path) -> None:
    """Batch group actions operate on all selected groups."""
    app = make_test_app(tmp_path)
    watcher = FakeWatcher()
    controller = GroupController(
        session_factory=app.session_factory,
        backup_service=app.backup_service,
        watcher_service=watcher,  # type: ignore[arg-type]
    )
    group_a = create_group(app, tmp_path, name="GrupoA", project_names=[])
    group_b = create_group(app, tmp_path, name="GrupoB", project_names=[])
    selection = GroupSelectionState()
    selection.select_all([group_a.id, group_b.id])
    selected_group_ids = selection.selected_or_fallback(None)

    controller.toggle_groups(selected_group_ids)
    disabled = controller.list_groups()
    controller.delete_groups(selected_group_ids)

    assert [group.enabled for group in disabled] == [False, False]
    assert controller.list_groups() == []


def test_manual_backup_job_calculates_progress(tmp_path: Path) -> None:
    """Manual backup progress is based on processed projects over total projects."""
    app = make_test_app(tmp_path)
    group = create_group(
        app,
        tmp_path,
        name="Grupo",
        project_names=["A", "B", "C"],
    )
    fake_backup = FakeBackupService()
    controller = ManualBackupJobController(
        session_factory=app.session_factory,
        backup_service=fake_backup,
    )

    controller.run([group.id])

    progress = controller.snapshot()[group.id]
    assert progress.state == ManualBackupState.FINISHED
    assert progress.total_projects == 3
    assert progress.processed_projects == 3
    assert progress.progress_percent == 100
    assert len(fake_backup.calls) == 3


def test_manual_backup_job_rejects_duplicate_running_group(tmp_path: Path) -> None:
    """A second job cannot run the same group while it is already running."""
    app = make_test_app(tmp_path)
    group = create_group(app, tmp_path, name="Grupo", project_names=["A"])
    started = Event()
    release = Event()
    fake_backup = FakeBackupService(started_event=started, release_event=release)
    controller = ManualBackupJobController(
        session_factory=app.session_factory,
        backup_service=fake_backup,
    )
    first_results: list[object] = []
    thread = Thread(target=lambda: first_results.append(controller.run([group.id])))

    thread.start()
    assert started.wait(timeout=5)
    second = controller.run([group.id])
    release.set()
    thread.join(timeout=5)

    assert second.accepted_group_ids == ()
    assert second.skipped_group_ids == (group.id,)
    assert len(fake_backup.calls) == 1
    assert first_results


def test_manual_backup_job_finishes_empty_group_at_100_percent(
    tmp_path: Path,
) -> None:
    """A group with zero projects finishes without error and shows 100%."""
    app = make_test_app(tmp_path)
    group = create_group(app, tmp_path, name="Grupo", project_names=[])
    controller = ManualBackupJobController(
        session_factory=app.session_factory,
        backup_service=FakeBackupService(),
    )

    controller.run([group.id])

    progress = controller.snapshot()[group.id]
    assert progress.state == ManualBackupState.FINISHED
    assert progress.total_projects == 0
    assert progress.progress_percent == 100


def test_manual_backup_job_group_errors_do_not_stop_other_groups(
    tmp_path: Path,
) -> None:
    """A failed group does not prevent later selected groups from running."""
    app = make_test_app(tmp_path)
    group_a = create_group(app, tmp_path, name="GrupoA", project_names=["A"])
    group_b = create_group(app, tmp_path, name="GrupoB", project_names=["B"])
    fake_backup = FakeBackupService(fail_group_ids={group_a.id})
    controller = ManualBackupJobController(
        session_factory=app.session_factory,
        backup_service=fake_backup,
    )

    controller.run([group_a.id, group_b.id])

    snapshot = controller.snapshot()
    assert snapshot[group_a.id].state == ManualBackupState.ERROR
    assert snapshot[group_b.id].state == ManualBackupState.FINISHED
    assert fake_backup.calls == [(group_a.id, "A"), (group_b.id, "B")]


def test_manual_backup_job_treats_failed_project_result_as_group_error(
    tmp_path: Path,
) -> None:
    """A project-level ERROR_ZIP result marks the group as failed."""
    app = make_test_app(tmp_path)
    group = create_group(app, tmp_path, name="Grupo", project_names=["A"])
    fake_backup = FakeBackupService(
        result_status_by_group_id={group.id: AuditEvent.ERROR_ZIP.value}
    )
    controller = ManualBackupJobController(
        session_factory=app.session_factory,
        backup_service=fake_backup,
    )

    controller.run([group.id])

    progress = controller.snapshot()[group.id]
    assert progress.state == ManualBackupState.ERROR
    assert progress.processed_projects == 1
    assert progress.error_message is not None


def test_manual_backup_job_audits_events(tmp_path: Path) -> None:
    """Manual backup jobs write audit trail events."""
    app = make_test_app(tmp_path)
    group = create_group(app, tmp_path, name="Grupo", project_names=["A"])
    controller = ManualBackupJobController(
        session_factory=app.session_factory,
        backup_service=FakeBackupService(),
    )

    controller.run([group.id])

    assert audit_actions(app) == [
        AuditEvent.MANUAL_BACKUP_STARTED.value,
        AuditEvent.MANUAL_BACKUP_GROUP_STARTED.value,
        AuditEvent.MANUAL_BACKUP_GROUP_FINISHED.value,
        AuditEvent.MANUAL_BACKUP_FINISHED.value,
    ]
