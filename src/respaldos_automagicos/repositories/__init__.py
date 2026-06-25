"""Repository abstractions for persistence access."""

from respaldos_automagicos.repositories.audit import AuditRepository
from respaldos_automagicos.repositories.backup_groups import (
    BackupGroupRepository,
    BackupGroupSummary,
)
from respaldos_automagicos.repositories.backup_history import BackupHistoryRepository
from respaldos_automagicos.repositories.base import BaseRepository
from respaldos_automagicos.repositories.watched_directories import (
    WatchedDirectoryRepository,
)

__all__ = [
    "AuditRepository",
    "BackupGroupRepository",
    "BackupGroupSummary",
    "BackupHistoryRepository",
    "BaseRepository",
    "WatchedDirectoryRepository",
]
