"""Audit log repository."""

from sqlalchemy import select

from respaldos_automagicos.models.audit_log import AuditLog
from respaldos_automagicos.repositories.base import BaseRepository


class AuditRepository(BaseRepository[AuditLog]):
    """Repository for audit log records."""

    def add_event(
        self,
        action: str,
        result: str,
        *,
        group_id: int | None = None,
        watched_directory_id: int | None = None,
        details: str | None = None,
    ) -> AuditLog:
        """Add a new audit event to the active session."""
        event = AuditLog(
            group_id=group_id,
            watched_directory_id=watched_directory_id,
            action=action,
            result=result,
            details=details,
        )
        return self.add(event)

    def list_recent(
        self,
        *,
        group_id: int | None = None,
        action: str | None = None,
        result: str | None = None,
        limit: int = 200,
    ) -> list[AuditLog]:
        """Return recent audit events using optional filters."""
        statement = select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit)
        if group_id is not None:
            statement = statement.where(AuditLog.group_id == group_id)
        if action:
            statement = statement.where(AuditLog.action == action)
        if result:
            statement = statement.where(AuditLog.result == result)
        return list(self.session.scalars(statement))
