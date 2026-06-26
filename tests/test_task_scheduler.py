"""Tests for Windows Task Scheduler integration."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from respaldos_automagicos.app import RespaldosAutomagicosApplication, create_app
from respaldos_automagicos.audit.service import AuditService
from respaldos_automagicos.config import AppSettings
from respaldos_automagicos.controllers.task_scheduler import (
    TaskSchedulerController,
    TaskSchedulerControllerError,
)
from respaldos_automagicos.models.audit_log import AuditLog
from respaldos_automagicos.models.enums import AuditEvent
from respaldos_automagicos.task_scheduler.service import (
    ScheduledTaskCommandResult,
    TaskSchedulerService,
)


def sqlite_url(path: Path) -> str:
    """Build a SQLite URL from a pytest temporary path."""
    return f"sqlite:///{path.as_posix()}"


def make_test_app(tmp_path: Path) -> RespaldosAutomagicosApplication:
    """Create an initialized app for task scheduler tests."""
    settings = AppSettings(
        database_url=sqlite_url(tmp_path / "task-scheduler.db"),
        logs_dir=tmp_path / "logs",
    )
    app = create_app(settings)
    app.initialize_storage()
    return app


@dataclass(slots=True)
class FakeRunner:
    """Collects schtasks commands."""

    fail_on: str | None = None
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def __call__(self, args: object) -> ScheduledTaskCommandResult:
        """Record a command and return a fake result."""
        command = tuple(str(arg) for arg in args)  # type: ignore[union-attr]
        self.calls.append(command)
        failed = self.fail_on is not None and self.fail_on in command
        return ScheduledTaskCommandResult(
            args=command,
            returncode=1 if failed else 0,
            stdout="",
            stderr="fallo" if failed else "",
        )


def audit_events(app: RespaldosAutomagicosApplication) -> list[AuditLog]:
    """Return audit events ordered by id."""
    with app.session_factory() as session:
        return list(session.scalars(select(AuditLog).order_by(AuditLog.id)))


def test_task_scheduler_service_creates_boot_task_and_runs_now(
    tmp_path: Path,
) -> None:
    """Activating now creates, enables, removes resume task, and runs main task."""
    runner = FakeRunner()
    service = TaskSchedulerService(
        working_directory=tmp_path,
        python_executable=tmp_path / ".venv" / "Scripts" / "python.exe",
        runner=runner,
    )

    service.activate_now()

    assert runner.calls[0][:7] == (
        "schtasks",
        "/Create",
        "/TN",
        "RespaldosAutomagicos",
        "/SC",
        "ONSTART",
        "/TR",
    )
    assert "run-service" in runner.calls[0][7]
    assert runner.calls[-1] == (
        "schtasks",
        "/Run",
        "/TN",
        "RespaldosAutomagicos",
    )


def test_task_scheduler_service_temporarily_disables_and_schedules_resume(
    tmp_path: Path,
) -> None:
    """Temporary disable creates a one-time resume task."""
    runner = FakeRunner()
    service = TaskSchedulerService(
        working_directory=tmp_path,
        python_executable=tmp_path / "python.exe",
        runner=runner,
        clock=lambda: datetime(2026, 6, 25, 10, 15),
    )

    resume_at = service.disable_for(timedelta(minutes=30))

    assert resume_at == datetime(2026, 6, 25, 10, 45)
    assert ("schtasks", "/Change", "/TN", "RespaldosAutomagicos", "/Disable") in (
        runner.calls
    )
    resume_command = runner.calls[-1]
    assert "/SC" in resume_command
    assert "ONCE" in resume_command
    assert "/ST" in resume_command
    assert "10:45" in resume_command
    assert "task-resume --run-now" in resume_command[resume_command.index("/TR") + 1]


def test_task_scheduler_controller_audits_success_and_failure(tmp_path: Path) -> None:
    """Task Scheduler actions are audited with success and error results."""
    app = make_test_app(tmp_path)
    runner = FakeRunner()
    controller = TaskSchedulerController(
        task_scheduler_service=TaskSchedulerService(runner=runner),
        audit_service=AuditService(app.session_factory),
    )

    result = controller.activate_on_boot()

    assert result.message == "Task Scheduler activado al encender."
    assert audit_events(app)[0].action == AuditEvent.TASK_SCHEDULER_ENABLE_BOOT.value
    assert audit_events(app)[0].result == AuditEvent.TASK_SCHEDULER_OK.value

    failing = TaskSchedulerController(
        task_scheduler_service=TaskSchedulerService(
            runner=FakeRunner(fail_on="/Create")
        ),
        audit_service=AuditService(app.session_factory),
    )
    with pytest.raises(TaskSchedulerControllerError):
        failing.activate_on_boot()

    assert audit_events(app)[-1].action == AuditEvent.TASK_SCHEDULER_ENABLE_BOOT.value
    assert audit_events(app)[-1].result == AuditEvent.TASK_SCHEDULER_ERROR.value
