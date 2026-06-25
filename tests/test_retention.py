"""Tests for retention policies."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from respaldos_automagicos.app import RespaldosAutomagicosApplication, create_app
from respaldos_automagicos.config import AppSettings
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
from respaldos_automagicos.retention.service import RetentionService
from respaldos_automagicos.scheduler.pending import PendingDirectoryQueue
from respaldos_automagicos.services.backup_service import BackupService


def sqlite_url(path: Path) -> str:
    """Build a SQLite URL from a pytest temporary path."""
    return f"sqlite:///{path.as_posix()}"


def make_test_app(tmp_path: Path) -> RespaldosAutomagicosApplication:
    """Create an initialized app for retention tests."""
    settings = AppSettings(
        database_url=sqlite_url(tmp_path / "retention.db"),
        logs_dir=tmp_path / "logs",
    )
    app = create_app(settings)
    app.initialize_storage()
    return app


def create_group_and_project(
    app: RespaldosAutomagicosApplication,
    *,
    tmp_path: Path,
    backups_to_keep: int,
    days_to_keep: int,
) -> tuple[BackupGroup, WatchedDirectory]:
    """Create a backup group and watched directory."""
    root = tmp_path / "root"
    destination = tmp_path / "dest"
    (root / "ProyectoA").mkdir(parents=True)
    destination.mkdir()
    with app.session_factory() as session:
        group = BackupGroup(
            name="Grupo",
            root_directory=str(root),
            destination_directory=str(destination),
            backups_to_keep=backups_to_keep,
            days_to_keep=days_to_keep,
        )
        BackupGroupRepository(session).add(group)
        session.flush()
        watched = WatchedDirectoryRepository(session).get_or_create(
            group.id,
            "ProyectoA",
        )
        session.commit()
        return group, watched


def create_history(
    app: RespaldosAutomagicosApplication,
    *,
    group: BackupGroup,
    watched: WatchedDirectory,
    backup_path: Path,
    backup_time: datetime,
    status: str = AuditEvent.BACKUP_OK.value,
    create_file: bool = True,
) -> BackupHistory:
    """Create one backup history row and optional physical ZIP placeholder."""
    if create_file:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text("zip", encoding="utf-8")
    with app.session_factory() as session:
        record = BackupHistoryRepository(session).add_record(
            group_id=group.id,
            watched_directory_id=watched.id,
            backup_name=backup_path.name,
            backup_path=str(backup_path),
            backup_time=backup_time,
            backup_size_bytes=3,
            file_count=1,
            content_hash=backup_path.stem,
            status=status,
            duration_ms=1,
        )
        session.commit()
        return record


def retained_histories(app: RespaldosAutomagicosApplication) -> list[BackupHistory]:
    """Return histories ordered by backup time descending."""
    with app.session_factory() as session:
        return list(
            session.scalars(
                select(BackupHistory).order_by(
                    BackupHistory.backup_time.desc(),
                    BackupHistory.id.desc(),
                )
            )
        )


def audit_events(app: RespaldosAutomagicosApplication) -> list[AuditLog]:
    """Return audit events ordered by id."""
    with app.session_factory() as session:
        return list(session.scalars(select(AuditLog).order_by(AuditLog.id)))


def test_retention_keeps_n_most_recent_backups(tmp_path: Path) -> None:
    """Retention always preserves the configured newest backups."""
    app = make_test_app(tmp_path)
    group, watched = create_group_and_project(
        app,
        tmp_path=tmp_path,
        backups_to_keep=2,
        days_to_keep=365,
    )
    now = datetime(2026, 6, 24, tzinfo=UTC)
    paths = [tmp_path / "backups" / f"b{i}.zip" for i in range(3)]
    for index, path in enumerate(paths):
        create_history(
            app,
            group=group,
            watched=watched,
            backup_path=path,
            backup_time=now - timedelta(days=index),
        )

    RetentionService(app.session_factory).apply(group, watched, now=now)

    histories = retained_histories(app)
    assert [history.retained for history in histories] == [True, True, False]
    assert paths[0].exists()
    assert paths[1].exists()
    assert not paths[2].exists()


def test_retention_deletes_excess_by_count(tmp_path: Path) -> None:
    """Backups outside the protected set are deleted by count when still recent."""
    app = make_test_app(tmp_path)
    group, watched = create_group_and_project(
        app,
        tmp_path=tmp_path,
        backups_to_keep=1,
        days_to_keep=365,
    )
    now = datetime(2026, 6, 24, tzinfo=UTC)
    create_history(
        app,
        group=group,
        watched=watched,
        backup_path=tmp_path / "b0.zip",
        backup_time=now,
    )
    create_history(
        app,
        group=group,
        watched=watched,
        backup_path=tmp_path / "b1.zip",
        backup_time=now - timedelta(days=1),
    )

    RetentionService(app.session_factory).apply(group, watched, now=now)

    deleted = [history for history in retained_histories(app) if not history.retained]
    assert deleted[0].deletion_reason == AuditEvent.RETENTION_BY_COUNT.value
    assert audit_events(app)[0].action == AuditEvent.RETENTION_BY_COUNT.value


def test_retention_does_not_delete_old_backups_when_below_keep_count(
    tmp_path: Path,
) -> None:
    """Age retention never deletes protected backups below backups_to_keep."""
    app = make_test_app(tmp_path)
    group, watched = create_group_and_project(
        app,
        tmp_path=tmp_path,
        backups_to_keep=6,
        days_to_keep=30,
    )
    now = datetime(2026, 6, 24, tzinfo=UTC)
    paths = [tmp_path / f"old{i}.zip" for i in range(4)]
    for index, path in enumerate(paths):
        create_history(
            app,
            group=group,
            watched=watched,
            backup_path=path,
            backup_time=now - timedelta(days=365 + index),
        )

    RetentionService(app.session_factory).apply(group, watched, now=now)

    assert all(history.retained for history in retained_histories(app))
    assert all(path.exists() for path in paths)
    assert audit_events(app) == []


def test_retention_deletes_old_backups_only_outside_protected_set(
    tmp_path: Path,
) -> None:
    """Age retention applies only to backups outside the newest protected set."""
    app = make_test_app(tmp_path)
    group, watched = create_group_and_project(
        app,
        tmp_path=tmp_path,
        backups_to_keep=2,
        days_to_keep=30,
    )
    now = datetime(2026, 6, 24, tzinfo=UTC)
    for index in range(4):
        create_history(
            app,
            group=group,
            watched=watched,
            backup_path=tmp_path / f"backup{index}.zip",
            backup_time=now - timedelta(days=[0, 1, 40, 41][index]),
        )

    RetentionService(app.session_factory).apply(group, watched, now=now)

    histories = retained_histories(app)
    assert [history.retained for history in histories] == [True, True, False, False]
    assert {
        history.deletion_reason for history in histories if not history.retained
    } == {AuditEvent.RETENTION_BY_AGE.value}


def test_retention_ignores_non_successful_history_rows(tmp_path: Path) -> None:
    """Only BACKUP_OK rows participate in retention decisions."""
    app = make_test_app(tmp_path)
    group, watched = create_group_and_project(
        app,
        tmp_path=tmp_path,
        backups_to_keep=1,
        days_to_keep=365,
    )
    now = datetime(2026, 6, 24, tzinfo=UTC)
    skipped_path = tmp_path / "skipped.zip"
    create_history(
        app,
        group=group,
        watched=watched,
        backup_path=tmp_path / "ok.zip",
        backup_time=now,
    )
    create_history(
        app,
        group=group,
        watched=watched,
        backup_path=skipped_path,
        backup_time=now - timedelta(days=1),
        status=AuditEvent.NO_EFFECTIVE_CHANGES.value,
    )

    RetentionService(app.session_factory).apply(group, watched, now=now)

    histories = retained_histories(app)
    assert all(history.retained for history in histories)
    assert skipped_path.exists()


def test_retention_updates_history_and_audit_for_each_deleted_file(
    tmp_path: Path,
) -> None:
    """Deleted files are marked and audited."""
    app = make_test_app(tmp_path)
    group, watched = create_group_and_project(
        app,
        tmp_path=tmp_path,
        backups_to_keep=1,
        days_to_keep=365,
    )
    now = datetime(2026, 6, 24, tzinfo=UTC)
    for index in range(3):
        create_history(
            app,
            group=group,
            watched=watched,
            backup_path=tmp_path / f"backup{index}.zip",
            backup_time=now - timedelta(days=index),
        )

    result = RetentionService(app.session_factory).apply(group, watched, now=now)

    deleted = [history for history in retained_histories(app) if not history.retained]
    assert result.deleted_count == 2
    assert len(deleted) == 2
    assert all(history.deleted_at is not None for history in deleted)
    assert len(audit_events(app)) == 2


def test_retention_continues_when_physical_file_is_missing(tmp_path: Path) -> None:
    """Missing ZIP files are audited as warnings and do not stop retention."""
    app = make_test_app(tmp_path)
    group, watched = create_group_and_project(
        app,
        tmp_path=tmp_path,
        backups_to_keep=1,
        days_to_keep=365,
    )
    now = datetime(2026, 6, 24, tzinfo=UTC)
    create_history(
        app,
        group=group,
        watched=watched,
        backup_path=tmp_path / "kept.zip",
        backup_time=now,
    )
    create_history(
        app,
        group=group,
        watched=watched,
        backup_path=tmp_path / "missing.zip",
        backup_time=now - timedelta(days=1),
        create_file=False,
    )

    result = RetentionService(app.session_factory).apply(group, watched, now=now)

    assert result.missing_count == 1
    events = audit_events(app)
    assert events[0].action == AuditEvent.RETENTION_FILE_MISSING.value
    assert events[0].result == AuditEvent.WARNING.value


@dataclass(slots=True)
class FakeRetentionService:
    """Collects retention calls from BackupService."""

    calls: list[tuple[int, int]] = field(default_factory=list)

    def apply(
        self,
        group: BackupGroup,
        watched_directory: WatchedDirectory,
        *,
        now: datetime | None = None,
    ) -> object:
        """Record a retention call."""
        self.calls.append((group.id, watched_directory.id))
        return object()


def test_backup_service_invokes_retention_after_success(tmp_path: Path) -> None:
    """BackupService applies retention only after BACKUP_OK."""
    app = make_test_app(tmp_path)
    group, watched = create_group_and_project(
        app,
        tmp_path=tmp_path,
        backups_to_keep=10,
        days_to_keep=365,
    )
    project = Path(group.root_directory) / watched.relative_path
    (project / "main.py").write_text("print('ok')\n", encoding="utf-8")
    fake_retention = FakeRetentionService()
    service = BackupService(
        session_factory=app.session_factory,
        pending_queue=PendingDirectoryQueue(),
        settings=app.settings,
        retention_service=fake_retention,  # type: ignore[arg-type]
    )

    result = service.create_backup(group, watched)

    assert result.status == AuditEvent.BACKUP_OK.value
    assert fake_retention.calls == [(group.id, watched.id)]


def test_backup_service_does_not_invoke_retention_after_no_effective_changes(
    tmp_path: Path,
) -> None:
    """BackupService skips retention after NO_EFFECTIVE_CHANGES."""
    app = make_test_app(tmp_path)
    group, watched = create_group_and_project(
        app,
        tmp_path=tmp_path,
        backups_to_keep=10,
        days_to_keep=365,
    )
    project = Path(group.root_directory) / watched.relative_path
    (project / "main.py").write_text("print('ok')\n", encoding="utf-8")
    first = app.backup_service.create_backup(group, watched)
    assert first.status == AuditEvent.BACKUP_OK.value
    with app.session_factory() as session:
        stored = WatchedDirectoryRepository(session).get(watched.id)
        assert stored is not None
        WatchedDirectoryRepository(session).mark_pending(stored, datetime.now(UTC))
        session.commit()
    fake_retention = FakeRetentionService()
    service = BackupService(
        session_factory=app.session_factory,
        pending_queue=PendingDirectoryQueue(),
        settings=app.settings,
        retention_service=fake_retention,  # type: ignore[arg-type]
    )

    second = service.create_backup(group, watched)

    assert second.status == AuditEvent.NO_EFFECTIVE_CHANGES.value
    assert fake_retention.calls == []
