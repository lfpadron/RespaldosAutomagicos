"""Audit service."""

from sqlalchemy.orm import Session, sessionmaker

from respaldos_automagicos.models.audit_log import AuditLog
from respaldos_automagicos.repositories.audit import AuditRepository


class AuditService:
    """Coordinates audit event recording."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        """Create the audit service."""
        self._session_factory = session_factory

    def record(
        self,
        action: str,
        result: str,
        *,
        group_id: int | None = None,
        watched_directory_id: int | None = None,
        details: str | None = None,
    ) -> AuditLog:
        """Record an audit event."""
        with self._session_factory() as session:
            event = AuditRepository(session).add_event(
                action=action,
                result=result,
                group_id=group_id,
                watched_directory_id=watched_directory_id,
                details=details,
            )
            session.commit()
            return event
