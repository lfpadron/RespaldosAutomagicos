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
        """Create or update the logon task and enable it."""
        self._create_main_task()
        self._enable_main_task()
        self._delete_resume_task()

    def activate_now(self) -> None:
        """Ensure the logon task exists and start it immediately."""
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
        """Disable the task and reactivate it at the next user logon."""
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
        self._run_powershell_required(
            self._register_task_script(
                task_name=self.task_name,
                command_argument=self._module_command_argument("run-service"),
                trigger="$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $User",
            )
        )

    def _create_resume_once_task(self, resume_at: datetime) -> None:
        resume_at_text = resume_at.strftime("%Y-%m-%d %H:%M:%S")
        self._run_powershell_required(
            self._register_task_script(
                task_name=self.resume_task_name,
                command_argument=self._module_command_argument(
                    "task-resume",
                    "--run-now",
                ),
                trigger=(
                    "$Trigger = New-ScheduledTaskTrigger -Once -At "
                    f"([datetime]::ParseExact({_ps_quote(resume_at_text)}, "
                    "'yyyy-MM-dd HH:mm:ss', "
                    "[Globalization.CultureInfo]::InvariantCulture))"
                ),
            )
        )

    def _create_resume_boot_task(self) -> None:
        self._run_powershell_required(
            self._register_task_script(
                task_name=self.resume_task_name,
                command_argument=self._module_command_argument(
                    "task-resume",
                    "--run-now",
                ),
                trigger="$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $User",
            )
        )

    def _enable_main_task(self) -> None:
        self._run_powershell_required(
            f"Enable-ScheduledTask -TaskName {_ps_quote(self.task_name)} | Out-Null"
        )

    def _disable_main_task(self) -> None:
        self._run_powershell_required(
            f"Disable-ScheduledTask -TaskName {_ps_quote(self.task_name)} | Out-Null"
        )

    def _run_main_task(self) -> None:
        self._run_powershell_required(
            f"Start-ScheduledTask -TaskName {_ps_quote(self.task_name)} | Out-Null"
        )

    def _end_main_task(self) -> None:
        self._run_powershell_optional(
            f"Stop-ScheduledTask -TaskName {_ps_quote(self.task_name)} | Out-Null"
        )

    def _delete_resume_task(self) -> None:
        self._run_powershell_optional(
            "Unregister-ScheduledTask "
            f"-TaskName {_ps_quote(self.resume_task_name)} "
            "-Confirm:$false | Out-Null"
        )

    def _register_task_script(
        self,
        *,
        task_name: str,
        command_argument: str,
        trigger: str,
    ) -> str:
        return "\n".join(
            [
                "$ErrorActionPreference = 'Stop'",
                "$User = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name",
                (
                    "$Action = New-ScheduledTaskAction "
                    "-Execute 'cmd.exe' "
                    f"-Argument {_ps_quote(command_argument)}"
                ),
                trigger,
                (
                    "$Principal = New-ScheduledTaskPrincipal "
                    "-UserId $User "
                    "-LogonType Interactive "
                    "-RunLevel Limited"
                ),
                (
                    "Register-ScheduledTask "
                    f"-TaskName {_ps_quote(task_name)} "
                    "-Action $Action "
                    "-Trigger $Trigger "
                    "-Principal $Principal "
                    "-Force | Out-Null"
                ),
            ]
        )

    def _run_powershell_required(self, script: str) -> ScheduledTaskCommandResult:
        return self._run_required(_powershell_args(script))

    def _run_powershell_optional(self, script: str) -> ScheduledTaskCommandResult:
        return self._run_optional(_powershell_args(script))

    def _run_required(self, args: Sequence[str]) -> ScheduledTaskCommandResult:
        result = self._runner(args)
        if result.returncode != 0:
            raise ScheduledTaskCommandError(result)
        return result

    def _run_optional(self, args: Sequence[str]) -> ScheduledTaskCommandResult:
        return self._runner(args)

    def _module_command_argument(self, *module_args: str) -> str:
        arguments = " ".join(module_args)
        return (
            '/d /c "'
            f'cd /d ""{self._working_directory}"" && '
            f'""{self._python_executable}"" -m respaldos_automagicos {arguments}'
            '"'
        )


def _powershell_args(script: str) -> list[str]:
    return [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


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
