"""Scheduler service for pending backup planning."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from typing import Protocol

from respaldos_automagicos.config import AppSettings
from respaldos_automagicos.logging_config import get_logger
from respaldos_automagicos.models.mixins import utc_now
from respaldos_automagicos.scheduler.pending import (
    PendingDirectory,
    PendingDirectoryQueue,
)
from respaldos_automagicos.services.watched_directory import WatchedDirectoryService


@dataclass(frozen=True, slots=True)
class ReadyBackupPlan:
    """Directory that passed stabilization and is ready for future backup work."""

    group_id: int
    group_name: str
    watched_directory_id: int
    relative_path: str
    ready_at: datetime


class BackupExecutor(Protocol):
    """Protocol for services that execute ready backup plans."""

    def run_for_pending(
        self,
        item: PendingDirectory,
        backup_time: datetime | None = None,
    ) -> object | None:
        """Execute backup work for a pending directory."""


class SchedulerService:
    """Reviews pending directories and plans future backup jobs."""

    def __init__(
        self,
        pending_queue: PendingDirectoryQueue,
        watched_directory_service: WatchedDirectoryService,
        settings: AppSettings,
        *,
        backup_executor: BackupExecutor | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Create the scheduler service."""
        self._pending_queue = pending_queue
        self._watched_directory_service = watched_directory_service
        self._settings = settings
        self._backup_executor = backup_executor
        self._logger = logger or get_logger("scheduler")
        self._last_scan_by_group: dict[int, datetime] = {}
        self._stop_event = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        """Start the scheduler background loop."""
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = Thread(
            target=self._run_loop,
            name="respaldos-scheduler",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the scheduler background loop."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def run_once(self, now: datetime | None = None) -> list[ReadyBackupPlan]:
        """Run one scheduler cycle and return projects ready for backup."""
        current_time = _aware_datetime(now or utc_now())
        pending_items = self._pending_queue.list_pending()
        due_group_ids = {
            item.group.id
            for item in pending_items
            if self._is_scan_due(item, current_time)
        }

        for group_id in due_group_ids:
            self._last_scan_by_group[group_id] = current_time

        ready: list[ReadyBackupPlan] = []
        for item in pending_items:
            if item.group.id not in due_group_ids:
                continue
            if not self._is_stabilized(item, current_time):
                continue

            self._logger.info(
                "Proyecto listo para respaldo",
                extra={
                    "group": item.group.name,
                    "directory": item.watched_directory.relative_path,
                },
            )
            ready.append(
                ReadyBackupPlan(
                    group_id=item.group.id,
                    group_name=item.group.name,
                    watched_directory_id=item.watched_directory.id,
                    relative_path=item.watched_directory.relative_path,
                    ready_at=current_time,
                )
            )
            if self._backup_executor is None:
                self._watched_directory_service.clear_pending(item)
            else:
                self._backup_executor.run_for_pending(item, current_time)
        return ready

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self.run_once()
            self._stop_event.wait(self._settings.scheduler_tick_seconds)

    def _is_scan_due(self, item: PendingDirectory, now: datetime) -> bool:
        last_scan = self._last_scan_by_group.get(item.group.id)
        if last_scan is None:
            return True
        interval = timedelta(minutes=item.group.scan_interval_minutes)
        return now - last_scan >= interval

    @staticmethod
    def _is_stabilized(item: PendingDirectory, now: datetime) -> bool:
        required_age = timedelta(minutes=item.group.stabilization_minutes)
        return now - _aware_datetime(item.last_change_at) >= required_age


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
