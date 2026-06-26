"""Controller for Windows Task Scheduler actions."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from respaldos_automagicos.audit.service import AuditService
from respaldos_automagicos.models.enums import AuditEvent
from respaldos_automagicos.task_scheduler.service import TaskSchedulerService


class TaskSchedulerControllerError(RuntimeError):
    """Raised when a task scheduler action fails."""


@dataclass(frozen=True, slots=True)
class TaskSchedulerActionResult:
    """User-facing result of a task scheduler action."""

    message: str


class TaskSchedulerController:
    """Coordinates Task Scheduler actions and audit records."""

    def __init__(
        self,
        *,
        task_scheduler_service: TaskSchedulerService,
        audit_service: AuditService,
    ) -> None:
        """Create the controller."""
        self._task_scheduler_service = task_scheduler_service
        self._audit_service = audit_service

    def activate_on_boot(self) -> TaskSchedulerActionResult:
        """Enable the background service when the user logs in to Windows."""
        return self._execute(
            action=AuditEvent.TASK_SCHEDULER_ENABLE_BOOT,
            details="Activar al iniciar sesion.",
            operation=self._task_scheduler_service.activate_on_boot,
            success_message="Task Scheduler activado al iniciar sesion.",
        )

    def activate_now(self) -> TaskSchedulerActionResult:
        """Start the background service now."""
        return self._execute(
            action=AuditEvent.TASK_SCHEDULER_RUN_NOW,
            details="Activar ahora.",
            operation=self._task_scheduler_service.activate_now,
            success_message="Task Scheduler activado ahora.",
        )

    def disable_for_minutes(self, minutes: int) -> TaskSchedulerActionResult:
        """Temporarily disable the background task."""
        if minutes <= 0:
            raise TaskSchedulerControllerError("Los minutos deben ser mayores que cero.")
        return self._execute(
            action=AuditEvent.TASK_SCHEDULER_DISABLE_TEMPORARY,
            details=f"Desactivar por {minutes} minutos.",
            operation=lambda: self._task_scheduler_service.disable_for(
                timedelta(minutes=minutes)
            ),
            success_message=f"Task Scheduler desactivado por {minutes} minutos.",
        )

    def disable_for_hours(self, hours: int) -> TaskSchedulerActionResult:
        """Temporarily disable the background task for whole hours."""
        if hours <= 0:
            raise TaskSchedulerControllerError("Las horas deben ser mayores que cero.")
        return self.disable_for_minutes(hours * 60)

    def disable_until_next_boot(self) -> TaskSchedulerActionResult:
        """Disable the background task until the next user logon."""
        return self._execute(
            action=AuditEvent.TASK_SCHEDULER_DISABLE_UNTIL_BOOT,
            details="Desactivar hasta el siguiente inicio de sesion.",
            operation=self._task_scheduler_service.disable_until_next_boot,
            success_message=(
                "Task Scheduler desactivado hasta el siguiente inicio de sesion."
            ),
        )

    def disable(self) -> TaskSchedulerActionResult:
        """Disable the background task."""
        return self._execute(
            action=AuditEvent.TASK_SCHEDULER_DISABLE,
            details="Desactivar.",
            operation=self._task_scheduler_service.disable,
            success_message="Task Scheduler desactivado.",
        )

    def resume_from_task(self, *, run_now: bool) -> TaskSchedulerActionResult:
        """Resume the background task from a scheduled helper task."""
        return self._execute(
            action=AuditEvent.TASK_SCHEDULER_RESUME,
            details=f"Reactivar desde tarea temporal. Ejecutar={run_now}.",
            operation=lambda: self._task_scheduler_service.resume(run_now=run_now),
            success_message="Task Scheduler reactivado.",
        )

    def _execute(
        self,
        *,
        action: AuditEvent,
        details: str,
        operation: Callable[[], object],
        success_message: str,
    ) -> TaskSchedulerActionResult:
        try:
            operation()
        except Exception as exc:
            self._audit_service.record(
                action.value,
                AuditEvent.TASK_SCHEDULER_ERROR.value,
                details=f"{details} Error={exc}",
            )
            raise TaskSchedulerControllerError(str(exc)) from exc
        self._audit_service.record(
            action.value,
            AuditEvent.TASK_SCHEDULER_OK.value,
            details=details,
        )
        return TaskSchedulerActionResult(success_message)
