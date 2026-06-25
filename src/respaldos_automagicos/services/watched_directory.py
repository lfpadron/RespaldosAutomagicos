"""Service for watched directory state transitions."""

from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from respaldos_automagicos.events import FileChangedEvent
from respaldos_automagicos.models.backup_group import BackupGroup
from respaldos_automagicos.models.enums import WatchedDirectoryStatus
from respaldos_automagicos.repositories.backup_groups import BackupGroupRepository
from respaldos_automagicos.repositories.watched_directories import (
    WatchedDirectoryRepository,
)
from respaldos_automagicos.scheduler.pending import (
    BackupGroupSnapshot,
    PendingDirectory,
    PendingDirectoryQueue,
    WatchedDirectorySnapshot,
)


class WatchedDirectoryService:
    """Coordinates watched directory persistence and pending queue updates."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        pending_queue: PendingDirectoryQueue,
    ) -> None:
        """Create the service with persistence and in-memory queue dependencies."""
        self._session_factory = session_factory
        self._pending_queue = pending_queue

    def handle_file_changed(self, event: FileChangedEvent) -> None:
        """Handle a file change event published by the watcher."""
        with self._session_factory() as session:
            group = BackupGroupRepository(session).get(event.group_id)
            if group is None or not group.enabled:
                return
            self._mark_pending_in_session(
                session=session,
                group=group,
                relative_path=event.affected_relative_path,
                changed_at=event.occurred_at,
            )
            session.commit()

    def mark_pending(
        self,
        group: BackupGroup,
        relative_path: str,
        changed_at: datetime,
    ) -> PendingDirectory | None:
        """Mark a group subdirectory as pending."""
        with self._session_factory() as session:
            managed_group = session.merge(group)
            pending = self._mark_pending_in_session(
                session=session,
                group=managed_group,
                relative_path=relative_path,
                changed_at=changed_at,
            )
            session.commit()
            return pending

    def clear_pending(self, item: PendingDirectory) -> None:
        """Clear pending state for a planned directory."""
        with self._session_factory() as session:
            repository = WatchedDirectoryRepository(session)
            watched_directory = repository.get(item.watched_directory.id)
            if watched_directory is not None:
                repository.clear_pending(watched_directory)
                session.commit()
        self._pending_queue.remove(item.group.id, item.watched_directory.relative_path)

    def _mark_pending_in_session(
        self,
        *,
        session: Session,
        group: BackupGroup,
        relative_path: str,
        changed_at: datetime,
    ) -> PendingDirectory | None:
        repository = WatchedDirectoryRepository(session)
        watched_directory = repository.get_or_create(group.id, relative_path)
        if watched_directory.status == WatchedDirectoryStatus.IGNORED.value:
            return None

        repository.mark_pending(watched_directory, changed_at)
        session.flush()

        item = PendingDirectory(
            group=BackupGroupSnapshot.from_model(group),
            watched_directory=WatchedDirectorySnapshot.from_model(watched_directory),
            last_change_at=changed_at,
        )
        return self._pending_queue.upsert(item)
