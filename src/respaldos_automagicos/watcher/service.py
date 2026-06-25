"""Watchdog-backed directory watcher service."""

from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from respaldos_automagicos.events import FileChangedEvent, FileChangeType
from respaldos_automagicos.models.backup_group import BackupGroup
from respaldos_automagicos.models.mixins import utc_now
from respaldos_automagicos.repositories.backup_groups import BackupGroupRepository
from respaldos_automagicos.services.event_bus import EventBus
from respaldos_automagicos.watcher.paths import resolve_affected_directory


class ObserverLike(Protocol):
    """Protocol for watchdog observers used by the watcher service."""

    def schedule(
        self,
        event_handler: FileSystemEventHandler,
        path: str,
        *,
        recursive: bool,
    ) -> object:
        """Schedule a filesystem handler."""

    def start(self) -> None:
        """Start observing."""

    def stop(self) -> None:
        """Stop observing."""

    def join(self, timeout: float | None = None) -> None:
        """Wait for observer shutdown."""


ObserverFactory = Callable[[], ObserverLike]


class BackupGroupEventHandler(FileSystemEventHandler):
    """Converts watchdog events into internal file changed events."""

    _SUPPORTED_EVENTS = {
        FileChangeType.CREATED.value,
        FileChangeType.MODIFIED.value,
        FileChangeType.DELETED.value,
        FileChangeType.MOVED.value,
    }

    def __init__(
        self,
        group: BackupGroup,
        event_bus: EventBus,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        """Create a handler for one backup group."""
        super().__init__()
        self._group_id = group.id
        self._group_name = group.name
        self._root_directory = group.root_directory
        self._event_bus = event_bus
        self._clock = clock

    def on_any_event(self, event: FileSystemEvent) -> None:
        """Publish supported watchdog events to the internal event bus."""
        if event.event_type not in self._SUPPORTED_EVENTS:
            return

        affected_path = self._select_event_path(event)
        affected_directory = resolve_affected_directory(
            self._root_directory,
            affected_path,
        )
        if affected_directory is None:
            return

        self._event_bus.publish(
            FileChangedEvent(
                group_id=self._group_id,
                group_name=self._group_name,
                root_directory=self._root_directory,
                affected_relative_path=affected_directory,
                changed_path=affected_path,
                change_type=FileChangeType(event.event_type),
                occurred_at=self._clock(),
            )
        )

    @staticmethod
    def _select_event_path(event: FileSystemEvent) -> str:
        destination = getattr(event, "dest_path", "")
        if event.event_type == FileChangeType.MOVED.value and destination:
            return str(destination)
        return str(event.src_path)


class DirectoryWatcherService:
    """Coordinates watchdog observers for configured active backup groups."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        event_bus: EventBus,
        *,
        observer_factory: ObserverFactory | None = None,
    ) -> None:
        """Create the watcher service."""
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._observer_factory = observer_factory or Observer
        self._observers_by_group_id: dict[int, ObserverLike] = {}

    def start(self) -> None:
        """Start one recursive observer for each enabled backup group."""
        if self._observers_by_group_id:
            return

        with self._session_factory() as session:
            groups = BackupGroupRepository(session).list_enabled()

        for group in groups:
            self._start_group(group)

    def stop(self) -> None:
        """Stop all active observers."""
        for group_id in list(self._observers_by_group_id):
            self.stop_group(group_id)

    def restart_group(self, group_id: int) -> None:
        """Restart the observer for one backup group if needed."""
        self.stop_group(group_id)
        with self._session_factory() as session:
            group = BackupGroupRepository(session).get_active(group_id)
            if group is None or not group.enabled:
                return
            self._start_group(group)

    def stop_group(self, group_id: int) -> None:
        """Stop the observer for one backup group."""
        observer = self._observers_by_group_id.pop(group_id, None)
        if observer is None:
            return
        observer.stop()
        observer.join(timeout=5)

    def _start_group(self, group: BackupGroup) -> None:
        observer = self._observer_factory()
        observer.schedule(
            BackupGroupEventHandler(group, self._event_bus),
            group.root_directory,
            recursive=True,
        )
        observer.start()
        self._observers_by_group_id[group.id] = observer
