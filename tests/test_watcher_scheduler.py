"""Tests for watcher planning and scheduler behavior."""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from watchdog.events import FileModifiedEvent

from respaldos_automagicos.app import RespaldosAutomagicosApplication, create_app
from respaldos_automagicos.config import AppSettings
from respaldos_automagicos.events import FileChangedEvent, FileChangeType
from respaldos_automagicos.models.backup_group import BackupGroup
from respaldos_automagicos.models.enums import WatchedDirectoryStatus
from respaldos_automagicos.repositories.backup_groups import BackupGroupRepository
from respaldos_automagicos.repositories.watched_directories import (
    WatchedDirectoryRepository,
)
from respaldos_automagicos.scheduler.service import SchedulerService
from respaldos_automagicos.services.event_bus import EventBus
from respaldos_automagicos.watcher.paths import resolve_affected_directory
from respaldos_automagicos.watcher.service import BackupGroupEventHandler


def sqlite_url(path: Path) -> str:
    """Build a SQLite URL from a pytest temporary path."""
    return f"sqlite:///{path.as_posix()}"


def make_test_app(tmp_path: Path) -> RespaldosAutomagicosApplication:
    """Create an initialized application with an isolated SQLite database."""
    settings = AppSettings(
        database_url=sqlite_url(tmp_path / "engine.db"),
        logs_dir=tmp_path / "logs",
        scheduler_tick_seconds=0.01,
    )
    app = create_app(settings)
    app.initialize_storage()
    return app


def create_group(
    app: RespaldosAutomagicosApplication,
    *,
    name: str = "Principal",
    scan_interval_minutes: int = 0,
    stabilization_minutes: int = 5,
) -> BackupGroup:
    """Persist a backup group for service tests."""
    with app.session_factory() as session:
        group = BackupGroup(
            name=name,
            root_directory="C:/Scripts",
            destination_directory="D:/Respaldos",
            scan_interval_minutes=scan_interval_minutes,
            stabilization_minutes=stabilization_minutes,
        )
        BackupGroupRepository(session).add(group)
        session.commit()
        return group


def test_mark_pending_updates_database_and_queue(tmp_path: Path) -> None:
    """Marking a directory pending updates persistence and memory."""
    app = make_test_app(tmp_path)
    group = create_group(app)
    changed_at = datetime(2026, 6, 24, 10, 0, tzinfo=UTC)

    item = app.watched_directory_service.mark_pending(
        group,
        "ProyectoA",
        changed_at,
    )

    assert item is not None
    assert len(app.pending_queue) == 1
    with app.session_factory() as session:
        watched = WatchedDirectoryRepository(session).get_by_group_and_relative_path(
            group.id,
            "ProyectoA",
        )
        assert watched is not None
        assert watched.pending_backup is True
        assert watched.last_change_at == changed_at.replace(tzinfo=None)
        assert watched.status == WatchedDirectoryStatus.PENDING.value


def test_pending_queue_avoids_duplicates(tmp_path: Path) -> None:
    """Repeated events for the same project keep a single pending entry."""
    app = make_test_app(tmp_path)
    group = create_group(app)
    first_change = datetime(2026, 6, 24, 10, 0, tzinfo=UTC)
    second_change = first_change + timedelta(minutes=1)

    app.watched_directory_service.mark_pending(group, "ProyectoA", first_change)
    app.watched_directory_service.mark_pending(group, "ProyectoA", second_change)

    pending_items = app.pending_queue.list_pending()
    assert len(pending_items) == 1
    assert pending_items[0].last_change_at == second_change
    with app.session_factory() as session:
        watched = WatchedDirectoryRepository(session).get_by_group_and_relative_path(
            group.id,
            "ProyectoA",
        )
        assert watched is not None
        assert watched.last_change_at == second_change.replace(tzinfo=None)


def test_resolve_affected_directory_is_path_style_independent() -> None:
    """The watcher resolves the immediate project directory from a changed path."""
    assert (
        resolve_affected_directory(
            r"C:\Scripts",
            r"C:\Scripts\ProyectoA\src\main.py",
        )
        == "ProyectoA"
    )
    assert (
        resolve_affected_directory(
            "/srv/scripts",
            "/srv/scripts/proyecto-b/docs/readme.md",
        )
        == "proyecto-b"
    )
    assert resolve_affected_directory("/srv/scripts", "/srv/other/file.txt") is None


def test_watchdog_handler_publishes_file_changed_event(tmp_path: Path) -> None:
    """A mocked watchdog event is translated into an internal domain event."""
    app = make_test_app(tmp_path)
    group = create_group(app)
    occurred_at = datetime(2026, 6, 24, 10, 0, tzinfo=UTC)
    event_bus = EventBus()
    received_events: list[FileChangedEvent] = []
    event_bus.subscribe(FileChangedEvent, received_events.append)
    handler = BackupGroupEventHandler(
        group,
        event_bus,
        clock=lambda: occurred_at,
    )

    handler.on_any_event(FileModifiedEvent(r"C:\Scripts\ProyectoA\src\main.py"))

    assert len(received_events) == 1
    assert received_events[0].group_id == group.id
    assert received_events[0].affected_relative_path == "ProyectoA"
    assert received_events[0].change_type == FileChangeType.MODIFIED


def test_scheduler_detects_ready_projects(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The scheduler emits a ready plan once stabilization has elapsed."""
    app = make_test_app(tmp_path)
    group = create_group(app, stabilization_minutes=5)
    changed_at = datetime(2026, 6, 24, 10, 0, tzinfo=UTC)
    now = changed_at + timedelta(minutes=6)
    logger = logging.getLogger("tests.scheduler.ready")
    scheduler = SchedulerService(
        app.pending_queue,
        app.watched_directory_service,
        app.settings,
        logger=logger,
    )
    app.watched_directory_service.mark_pending(group, "ProyectoA", changed_at)

    caplog.set_level(logging.INFO, logger="tests.scheduler.ready")
    ready = scheduler.run_once(now)

    assert [plan.relative_path for plan in ready] == ["ProyectoA"]
    assert "Proyecto listo para respaldo" in caplog.text
    assert len(app.pending_queue) == 0
    with app.session_factory() as session:
        watched = WatchedDirectoryRepository(session).get_by_group_and_relative_path(
            group.id,
            "ProyectoA",
        )
        assert watched is not None
        assert watched.pending_backup is False
        assert watched.status == WatchedDirectoryStatus.NORMAL.value


def test_scheduler_waits_for_stabilization(tmp_path: Path) -> None:
    """The scheduler leaves recent changes pending until stabilization passes."""
    app = make_test_app(tmp_path)
    group = create_group(app, stabilization_minutes=5)
    changed_at = datetime(2026, 6, 24, 10, 0, tzinfo=UTC)
    now = changed_at + timedelta(minutes=4)
    app.watched_directory_service.mark_pending(group, "ProyectoA", changed_at)

    ready = app.scheduler_service.run_once(now)

    assert ready == []
    assert len(app.pending_queue) == 1
    with app.session_factory() as session:
        watched = WatchedDirectoryRepository(session).get_by_group_and_relative_path(
            group.id,
            "ProyectoA",
        )
        assert watched is not None
        assert watched.pending_backup is True
        assert watched.status == WatchedDirectoryStatus.PENDING.value
