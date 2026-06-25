"""Core application composition for RespaldosAutomagicos."""

from dataclasses import dataclass

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from respaldos_automagicos.config import AppSettings
from respaldos_automagicos.database import (
    create_engine_from_settings,
    create_session_factory,
    initialize_database,
)
from respaldos_automagicos.events import FileChangedEvent
from respaldos_automagicos.logging_config import configure_logging
from respaldos_automagicos.restore.service import RestoreService
from respaldos_automagicos.retention.service import RetentionService
from respaldos_automagicos.scheduler.pending import PendingDirectoryQueue
from respaldos_automagicos.scheduler.service import SchedulerService
from respaldos_automagicos.services.backup_service import BackupService
from respaldos_automagicos.services.event_bus import EventBus
from respaldos_automagicos.services.watched_directory import WatchedDirectoryService
from respaldos_automagicos.watcher.service import DirectoryWatcherService


@dataclass(frozen=True, slots=True)
class RespaldosAutomagicosApplication:
    """Container for core application dependencies.

    This class intentionally has no dependency on the Textual TUI. Interface
    adapters can import and use it, while domain services stay UI-agnostic.
    """

    settings: AppSettings
    engine: Engine
    session_factory: sessionmaker[Session]
    event_bus: EventBus
    pending_queue: PendingDirectoryQueue
    watched_directory_service: WatchedDirectoryService
    retention_service: RetentionService
    backup_service: BackupService
    restore_service: RestoreService
    scheduler_service: SchedulerService
    watcher_service: DirectoryWatcherService

    def initialize_storage(self) -> None:
        """Create database tables for local development and tests."""
        initialize_database(self.engine)


def create_app(settings: AppSettings | None = None) -> RespaldosAutomagicosApplication:
    """Create the core application container."""
    resolved_settings = settings or AppSettings()
    configure_logging(resolved_settings)
    engine = create_engine_from_settings(resolved_settings)
    session_factory = create_session_factory(engine)
    event_bus = EventBus()
    pending_queue = PendingDirectoryQueue()
    watched_directory_service = WatchedDirectoryService(
        session_factory=session_factory,
        pending_queue=pending_queue,
    )
    retention_service = RetentionService(session_factory)
    backup_service = BackupService(
        session_factory=session_factory,
        pending_queue=pending_queue,
        settings=resolved_settings,
        retention_service=retention_service,
    )
    restore_service = RestoreService(session_factory)
    event_bus.subscribe(FileChangedEvent, watched_directory_service.handle_file_changed)
    scheduler_service = SchedulerService(
        pending_queue=pending_queue,
        watched_directory_service=watched_directory_service,
        settings=resolved_settings,
        backup_executor=backup_service,
    )
    watcher_service = DirectoryWatcherService(
        session_factory=session_factory,
        event_bus=event_bus,
    )
    return RespaldosAutomagicosApplication(
        settings=resolved_settings,
        engine=engine,
        session_factory=session_factory,
        event_bus=event_bus,
        pending_queue=pending_queue,
        watched_directory_service=watched_directory_service,
        retention_service=retention_service,
        backup_service=backup_service,
        restore_service=restore_service,
        scheduler_service=scheduler_service,
        watcher_service=watcher_service,
    )
