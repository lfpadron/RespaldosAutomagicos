"""Controller for backup history views."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from respaldos_automagicos.repositories.backup_history import BackupHistoryRepository
from respaldos_automagicos.utils.time import DEFAULT_TIMEZONE


@dataclass(frozen=True, slots=True)
class HistoryItem:
    """Read model for one backup history row."""

    backup_time: datetime
    timezone: str
    group_name: str
    project_name: str
    status: str
    duration_ms: int | None
    backup_size_bytes: int | None
    content_hash: str | None
    retained: bool
    deletion_reason: str | None


class HistoryController:
    """Coordinates backup history queries for UI clients."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        """Create the history controller."""
        self._session_factory = session_factory

    def list_history(
        self,
        *,
        group_id: int | None = None,
        limit: int = 200,
    ) -> list[HistoryItem]:
        """Return recent backup history."""
        with self._session_factory() as session:
            rows = BackupHistoryRepository(session).list_recent(
                group_id=group_id,
                limit=limit,
            )
            return [
                HistoryItem(
                    backup_time=row.backup_time,
                    timezone=(
                        row.group.timezone
                        if row.group is not None
                        else DEFAULT_TIMEZONE
                    ),
                    group_name=row.group.name if row.group is not None else "-",
                    project_name=(
                        row.watched_directory.relative_path
                        if row.watched_directory is not None
                        else "-"
                    ),
                    status=row.status,
                    duration_ms=row.duration_ms,
                    backup_size_bytes=row.backup_size_bytes,
                    content_hash=row.content_hash,
                    retained=row.retained,
                    deletion_reason=row.deletion_reason,
                )
                for row in rows
            ]
