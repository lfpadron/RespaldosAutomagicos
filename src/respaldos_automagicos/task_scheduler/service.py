"""Windows Task Scheduler command service."""

import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_TASK_NAME = "RespaldosAutomagicos"
DEFAULT_RESUME_TASK_NAME = "RespaldosAutomagicosReactivar"


@dataclass(frozen=True, slots=True)
class ScheduledTaskCommandResult:
    """Result of one scheduler command."""

    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class ScheduledTaskCommandError(RuntimeError):
    """Raised when Task Scheduler rejects an operation."""

    def __init__(self, result: ScheduledTaskCommandResult) -> None:
        """Create the error from a failed command result."""
        self.result = result
        message = result.stderr.strip() or result.stdout.strip()
        super().__init__(message or f"Comando fallido: {' '.join(result.args)}")


TaskCommandRunner = Callable[[Sequence[str]], ScheduledTaskCommandResult]


class TaskSchedulerService:
    """Manage the Windows scheduled task used by the background service."""

    def __init__(
        self,
        *,
        task_name: str = DEFAULT_TASK_NAME,
        resume_task_name: str = DEFAULT_RESUME_TASK_NAME,
        working_directory: Path | None = None,
        python_executable: Path | None = None,
        runner: TaskCommandRunner | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Create the Task Scheduler service."""
        self.task_name = task_name
        self.resume_task_name = resume_task_name
        self._working_directory = working_directory or Path.cwd()
        self._python_executable = python_executable or Path(sys.executable)
        self._runner = runner or _subprocess_runner
        self._clock = clock or datetime.now

    def activate_on_boot(self) -> None:
        """Create or update the boot task and enable it."""
        self._create_main_task()
        self._enable_main_task()
        self._delete_resume_task()

    def activate_now(self) -> None:
        """Ensure the boot task exists and start it immediately."""
        self.activate_on_boot()
        self._run_main_task()

    def disable(self) -> None:
        """Stop the running task, disable future starts, and remove resume tasks."""
        self._end_main_task()
        self._disable_main_task()
        self._delete_resume_task()

    def disable_for(self, duration: timedelta) -> datetime:
        """Disable the task temporarily and schedule a reactivation task."""
        if duration <= timedelta():
            raise ValueError("La duracion debe ser mayor que cero.")
        resume_at = self._clock() + duration
        self._end_main_task()
        self._disable_main_task()
        self._create_resume_once_task(resume_at)
        return resume_at

    def disable_until_next_boot(self) -> None:
        """Disable the task and reactivate it at the next system boot."""
        self._end_main_task()
        self._disable_main_task()
        self._create_resume_boot_task()

    def resume(self, *, run_now: bool) -> None:
        """Enable the main task and optionally start it."""
        self._enable_main_task()
        self._delete_resume_task()
        if run_now:
            self._run_main_task()

    def _create_main_task(self) -> None:
        self._run_required(
            [
                "schtasks",
                "/Create",
                "/TN",
                self.task_name,
                "/SC",
                "ONSTART",
                "/TR",
                self._module_action("run-service"),
                "/F",
            ]
        )

    def _create_resume_once_task(self, resume_at: datetime) -> None:
        self._run_required(
            [
                "schtasks",
                "/Create",
                "/TN",
                self.resume_task_name,
                "/SC",
                "ONCE",
                "/ST",
                resume_at.strftime("%H:%M"),
                "/SD",
                resume_at.strftime("%m/%d/%Y"),
                "/TR",
                self._module_action("task-resume", "--run-now"),
                "/F",
            ]
        )

    def _create_resume_boot_task(self) -> None:
        self._run_required(
            [
                "schtasks",
                "/Create",
                "/TN",
                self.resume_task_name,
                "/SC",
                "ONSTART",
                "/TR",
                self._module_action("task-resume", "--run-now"),
                "/F",
            ]
        )

    def _enable_main_task(self) -> None:
        self._run_required(["schtasks", "/Change", "/TN", self.task_name, "/Enable"])

    def _disable_main_task(self) -> None:
        self._run_required(["schtasks", "/Change", "/TN", self.task_name, "/Disable"])

    def _run_main_task(self) -> None:
        self._run_required(["schtasks", "/Run", "/TN", self.task_name])

    def _end_main_task(self) -> None:
        self._run_optional(["schtasks", "/End", "/TN", self.task_name])

    def _delete_resume_task(self) -> None:
        self._run_optional(["schtasks", "/Delete", "/TN", self.resume_task_name, "/F"])

    def _run_required(self, args: Sequence[str]) -> ScheduledTaskCommandResult:
        result = self._runner(args)
        if result.returncode != 0:
            raise ScheduledTaskCommandError(result)
        return result

    def _run_optional(self, args: Sequence[str]) -> ScheduledTaskCommandResult:
        return self._runner(args)

    def _module_action(self, *module_args: str) -> str:
        arguments = " ".join(module_args)
        return (
            'cmd.exe /d /c "'
            f'cd /d ""{self._working_directory}"" && '
            f'""{self._python_executable}"" -m respaldos_automagicos {arguments}'
            '"'
        )


def _subprocess_runner(args: Sequence[str]) -> ScheduledTaskCommandResult:
    try:
        completed = subprocess.run(
            list(args),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except OSError as exc:
        return ScheduledTaskCommandResult(
            args=tuple(args),
            returncode=1,
            stdout="",
            stderr=str(exc),
        )
    return ScheduledTaskCommandResult(
        args=tuple(args),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
