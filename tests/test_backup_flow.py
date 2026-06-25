"""Tests for backup generation, ignore rules, hashing, and ZIP output."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zipfile import ZipFile

from sqlalchemy import select

from respaldos_automagicos.app import RespaldosAutomagicosApplication, create_app
from respaldos_automagicos.config import AppSettings
from respaldos_automagicos.hashing.service import ContentHashService
from respaldos_automagicos.ignore.service import AutomagicIgnore
from respaldos_automagicos.models.audit_log import AuditLog
from respaldos_automagicos.models.backup_group import BackupGroup
from respaldos_automagicos.models.backup_history import BackupHistory
from respaldos_automagicos.models.enums import AuditEvent, WatchedDirectoryStatus
from respaldos_automagicos.repositories.backup_groups import BackupGroupRepository
from respaldos_automagicos.repositories.watched_directories import (
    WatchedDirectoryRepository,
)
from respaldos_automagicos.scheduler.pending import PendingDirectory
from respaldos_automagicos.scheduler.service import SchedulerService
from respaldos_automagicos.services.backup_service import BackupService
from respaldos_automagicos.utils.files import collect_project_files
from respaldos_automagicos.zipper.service import ZipBackupResult, ZipBackupService


def sqlite_url(path: Path) -> str:
    """Build a SQLite URL from a pytest temporary path."""
    return f"sqlite:///{path.as_posix()}"


def make_test_app(tmp_path: Path) -> RespaldosAutomagicosApplication:
    """Create an initialized application for backup tests."""
    settings = AppSettings(
        database_url=sqlite_url(tmp_path / "backup-flow.db"),
        logs_dir=tmp_path / "logs",
        scheduler_tick_seconds=0.01,
    )
    app = create_app(settings)
    app.initialize_storage()
    return app


def create_project(path: Path) -> None:
    """Create a small project tree."""
    (path / "src").mkdir(parents=True)
    (path / "src" / "main.py").write_text("print('hola')\n", encoding="utf-8")
    (path / "README.md").write_text("# ProyectoA\n", encoding="utf-8")


def create_group_and_watched_directory(
    app: RespaldosAutomagicosApplication,
    *,
    root_directory: Path,
    destination_directory: Path,
) -> tuple[BackupGroup, object]:
    """Persist a backup group and watched directory."""
    with app.session_factory() as session:
        group = BackupGroup(
            name="Scripts Python",
            root_directory=str(root_directory),
            destination_directory=str(destination_directory),
            scan_interval_minutes=0,
            stabilization_minutes=0,
        )
        BackupGroupRepository(session).add(group)
        session.flush()
        watched = WatchedDirectoryRepository(session).get_or_create(
            group.id,
            "ProyectoA",
        )
        WatchedDirectoryRepository(session).mark_pending(
            watched,
            datetime(2026, 6, 24, 14, 0, tzinfo=UTC),
        )
        session.commit()
        return group, watched


class BrokenZipBackupService:
    """Creates an unreadable ZIP artifact for verification tests."""

    def create_backup(
        self,
        *,
        project_root_name: str,
        destination_directory: Path,
        backup_name: str,
        files: tuple[object, ...],
        manifest: dict[str, object],
        compression_level: int,
    ) -> ZipBackupResult:
        """Return a corrupt ZIP path."""
        destination_directory.mkdir(parents=True, exist_ok=True)
        backup_path = destination_directory / backup_name
        backup_path.write_text("no es zip", encoding="utf-8")
        return ZipBackupResult(
            backup_path=backup_path,
            backup_size_bytes=backup_path.stat().st_size,
        )


def test_automagic_ignore_rules() -> None:
    """automagic_ignore handles comments, globs, directories, and negation."""
    ignore = AutomagicIgnore.from_text("""
        # comentario

        __pycache__/
        *.pyc
        .venv/
        *.log
        !important.log
        """)

    assert ignore.is_ignored("__pycache__/module.pyc")
    assert ignore.is_ignored("src/app.pyc")
    assert ignore.is_ignored(".venv/Lib/site.py")
    assert ignore.is_ignored("debug.log")
    assert not ignore.is_ignored("important.log")
    assert not ignore.is_ignored("src/main.py")


def test_hashing_is_deterministic_and_ignores_metadata_and_ignored_files(
    tmp_path: Path,
) -> None:
    """Hashing is based on included relative paths and bytes only."""
    project = tmp_path / "ProyectoA"
    create_project(project)
    (project / "automagic_ignore").write_text("*.log\n", encoding="utf-8")
    (project / "debug.log").write_text("ignored\n", encoding="utf-8")
    ignore = AutomagicIgnore.from_file(project / "automagic_ignore")
    service = ContentHashService()

    first = service.calculate(project, ignore).content_hash
    second = service.calculate(project, ignore).content_hash
    os.utime(project / "src" / "main.py", None)
    metadata_only = service.calculate(project, ignore).content_hash
    (project / "debug.log").write_text("ignored but changed\n", encoding="utf-8")
    ignored_change = service.calculate(project, ignore).content_hash
    (project / "src" / "main.py").write_text("print('cambio')\n", encoding="utf-8")
    content_change = service.calculate(project, ignore).content_hash

    assert first == second
    assert first == metadata_only
    assert first == ignored_change
    assert first != content_change


def test_zipper_creates_project_root_manifest_and_respects_exclusions(
    tmp_path: Path,
) -> None:
    """ZIP creation uses the expected project-root structure."""
    project = tmp_path / "ProyectoA"
    destination = tmp_path / "backups"
    create_project(project)
    (project / "automagic_ignore").write_text("*.log\n", encoding="utf-8")
    (project / "debug.log").write_text("ignored\n", encoding="utf-8")
    ignore = AutomagicIgnore.from_file(project / "automagic_ignore")
    files = tuple(collect_project_files(project, ignore))
    manifest = {"program": "RespaldosAutomagicos", "file_count": len(files)}

    result = ZipBackupService().create_backup(
        project_root_name="ProyectoA",
        destination_directory=destination,
        backup_name="ProyectoA_20260624_145632_R000.zip",
        files=files,
        manifest=manifest,
        compression_level=6,
    )

    with ZipFile(result.backup_path) as backup_zip:
        names = set(backup_zip.namelist())
        loaded_manifest = json.loads(
            backup_zip.read("ProyectoA/manifest.json").decode("utf-8")
        )

    assert "ProyectoA/src/main.py" in names
    assert "ProyectoA/README.md" in names
    assert "ProyectoA/manifest.json" in names
    assert "ProyectoA/debug.log" not in names
    assert loaded_manifest["program"] == "RespaldosAutomagicos"


def test_zipper_accepts_files_with_timestamps_before_1980(tmp_path: Path) -> None:
    """ZIP creation clamps old file timestamps to the ZIP-supported range."""
    project = tmp_path / "ProyectoA"
    destination = tmp_path / "backups"
    create_project(project)
    old_timestamp = datetime(1979, 1, 1, 12, tzinfo=UTC).timestamp()
    os.utime(project / "src" / "main.py", (old_timestamp, old_timestamp))
    files = tuple(collect_project_files(project, AutomagicIgnore.from_text("")))
    manifest = {"program": "RespaldosAutomagicos", "file_count": len(files)}

    result = ZipBackupService().create_backup(
        project_root_name="ProyectoA",
        destination_directory=destination,
        backup_name="ProyectoA_20260624_145632_R000.zip",
        files=files,
        manifest=manifest,
        compression_level=6,
    )

    with ZipFile(result.backup_path) as backup_zip:
        old_file_info = backup_zip.getinfo("ProyectoA/src/main.py")

    assert old_file_info.date_time >= (1980, 1, 1, 0, 0, 0)


def test_backup_service_generates_backup_and_records_side_effects(
    tmp_path: Path,
) -> None:
    """A changed project produces a ZIP, history, audit, and state updates."""
    app = make_test_app(tmp_path)
    root = tmp_path / "roots"
    project = root / "ProyectoA"
    destination = tmp_path / "dest"
    create_project(project)
    group, watched = create_group_and_watched_directory(
        app,
        root_directory=root,
        destination_directory=destination,
    )
    backup_time = datetime(2026, 6, 24, 14, 56, 32, tzinfo=UTC)

    result = app.backup_service.create_backup(group, watched, backup_time)

    assert result.status == AuditEvent.BACKUP_OK.value
    assert result.backup_name == "ProyectoA_20260624_145632_R000.zip"
    assert result.backup_path is not None
    assert result.backup_path.exists()
    with app.session_factory() as session:
        stored = WatchedDirectoryRepository(session).get_by_group_and_relative_path(
            group.id,
            "ProyectoA",
        )
        histories = session.scalars(select(BackupHistory)).all()
        audits = session.scalars(select(AuditLog)).all()

    assert stored is not None
    assert stored.pending_backup is False
    assert stored.backup_running is False
    assert stored.status == WatchedDirectoryStatus.NORMAL.value
    assert stored.last_content_hash == result.content_hash
    assert stored.rolling_counter == 1
    assert len(histories) == 1
    assert histories[0].status == AuditEvent.BACKUP_OK.value
    assert histories[0].backup_path == str(result.backup_path)
    assert len(audits) == 1
    assert audits[0].action == AuditEvent.BACKUP_OK.value


def test_backup_service_verifies_created_zip_before_success(
    tmp_path: Path,
) -> None:
    """A corrupt ZIP returned by the zipper is recorded as ERROR_ZIP."""
    app = make_test_app(tmp_path)
    root = tmp_path / "roots"
    project = root / "ProyectoA"
    destination = tmp_path / "dest"
    create_project(project)
    group, watched = create_group_and_watched_directory(
        app,
        root_directory=root,
        destination_directory=destination,
    )
    service = BackupService(
        session_factory=app.session_factory,
        pending_queue=app.pending_queue,
        settings=app.settings,
        zipper_service=BrokenZipBackupService(),  # type: ignore[arg-type]
    )

    result = service.create_backup(group, watched)

    assert result.status == AuditEvent.ERROR_ZIP.value
    assert result.backup_path is None
    with app.session_factory() as session:
        histories = session.scalars(select(BackupHistory)).all()
        stored = WatchedDirectoryRepository(session).get_by_group_and_relative_path(
            group.id,
            "ProyectoA",
        )

    assert [history.status for history in histories] == [AuditEvent.ERROR_ZIP.value]
    assert stored is not None
    assert stored.status == WatchedDirectoryStatus.ERROR.value


def test_backup_service_omits_backup_without_effective_changes(
    tmp_path: Path,
) -> None:
    """A repeated backup with the same hash records NO_EFFECTIVE_CHANGES."""
    app = make_test_app(tmp_path)
    root = tmp_path / "roots"
    project = root / "ProyectoA"
    destination = tmp_path / "dest"
    create_project(project)
    group, watched = create_group_and_watched_directory(
        app,
        root_directory=root,
        destination_directory=destination,
    )
    first_time = datetime(2026, 6, 24, 14, 56, 32, tzinfo=UTC)
    second_time = first_time + timedelta(minutes=1)

    first = app.backup_service.create_backup(group, watched, first_time)
    with app.session_factory() as session:
        stored = WatchedDirectoryRepository(session).get_by_group_and_relative_path(
            group.id,
            "ProyectoA",
        )
        assert stored is not None
        WatchedDirectoryRepository(session).mark_pending(stored, second_time)
        session.commit()

    second = app.backup_service.create_backup(group, stored, second_time)

    assert first.backup_path is not None
    assert second.status == AuditEvent.NO_EFFECTIVE_CHANGES.value
    assert second.backup_path is None
    assert len(list((destination / group.name / "ProyectoA").glob("*.zip"))) == 1
    with app.session_factory() as session:
        histories = session.scalars(
            select(BackupHistory).order_by(BackupHistory.id)
        ).all()
        audits = session.scalars(select(AuditLog).order_by(AuditLog.id)).all()
        stored = WatchedDirectoryRepository(session).get_by_group_and_relative_path(
            group.id,
            "ProyectoA",
        )

    assert [history.status for history in histories] == [
        AuditEvent.BACKUP_OK.value,
        AuditEvent.NO_EFFECTIVE_CHANGES.value,
    ]
    assert [audit.result for audit in audits] == [
        AuditEvent.BACKUP_OK.value,
        AuditEvent.NO_EFFECTIVE_CHANGES.value,
    ]
    assert stored is not None
    assert stored.pending_backup is False
    assert stored.status == WatchedDirectoryStatus.NORMAL.value


def test_scheduler_invokes_backup_executor_when_project_is_ready(
    tmp_path: Path,
) -> None:
    """The scheduler delegates ready projects to the backup executor."""
    app = make_test_app(tmp_path)
    root = tmp_path / "roots"
    project = root / "ProyectoA"
    create_project(project)
    group, _watched = create_group_and_watched_directory(
        app,
        root_directory=root,
        destination_directory=tmp_path / "dest",
    )
    changed_at = datetime(2026, 6, 24, 14, 0, tzinfo=UTC)
    app.watched_directory_service.mark_pending(group, "ProyectoA", changed_at)
    calls: list[PendingDirectory] = []

    class FakeBackupExecutor:
        """Collects backup calls from the scheduler."""

        def run_for_pending(
            self,
            item: PendingDirectory,
            backup_time: datetime | None = None,
        ) -> object | None:
            calls.append(item)
            return None

    scheduler = SchedulerService(
        app.pending_queue,
        app.watched_directory_service,
        app.settings,
        backup_executor=FakeBackupExecutor(),
    )

    scheduler.run_once(changed_at)

    assert [item.watched_directory.relative_path for item in calls] == ["ProyectoA"]
