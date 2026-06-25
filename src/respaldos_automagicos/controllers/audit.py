"""Controller for audit log views."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from respaldos_automagicos.models.backup_group import BackupGroup
from respaldos_automagicos.repositories.audit import AuditRepository
from respaldos_automagicos.utils.time import DEFAULT_TIMEZONE


@dataclass(frozen=True, slots=True)
class AuditLogItem:
    """Read model for one audit event."""

    timestamp: datetime
    timezone: str
    group_id: int | None
    watched_directory_id: int | None
    action: str
    result: str
    details: str | None


class AuditController:
    """Coordinates audit log queries for UI clients."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        """Create the audit controller."""
        self._session_factory = session_factory

    def list_audit(
        self,
        *,
        group_id: int | None = None,
        action: str | None = None,
        result: str | None = None,
        limit: int = 200,
    ) -> list[AuditLogItem]:
        """Return recent audit events."""
        with self._session_factory() as session:
            rows = AuditRepository(session).list_recent(
                group_id=group_id,
                action=action,
                result=result,
                limit=limit,
            )
            group_ids = {
                row.group_id for row in rows if row.group_id is not None
            }
            timezone_by_group_id: dict[int, str] = {}
            if group_ids:
                groups = session.scalars(
                    select(BackupGroup).where(BackupGroup.id.in_(group_ids))
                )
                timezone_by_group_id = {
                    group.id: group.timezone for group in groups
                }
            return [
                AuditLogItem(
                    timestamp=row.timestamp,
                    timezone=(
                        timezone_by_group_id.get(row.group_id, DEFAULT_TIMEZONE)
                        if row.group_id is not None
                        else DEFAULT_TIMEZONE
                    ),
                    group_id=row.group_id,
                    watched_directory_id=row.watched_directory_id,
                    action=row.action,
                    result=row.result,
                    details=row.details,
                )
                for row in rows
            ]
