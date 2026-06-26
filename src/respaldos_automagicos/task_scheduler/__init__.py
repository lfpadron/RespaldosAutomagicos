"""Windows Task Scheduler integration."""

from respaldos_automagicos.task_scheduler.service import (
    ScheduledTaskCommandError,
    ScheduledTaskCommandResult,
    TaskSchedulerService,
)

__all__ = [
    "ScheduledTaskCommandError",
    "ScheduledTaskCommandResult",
    "TaskSchedulerService",
]
