"""Application service contracts."""

from respaldos_automagicos.services.backup_service import BackupResult, BackupService
from respaldos_automagicos.services.base import Service
from respaldos_automagicos.services.event_bus import EventBus
from respaldos_automagicos.services.watched_directory import WatchedDirectoryService

__all__ = [
    "BackupResult",
    "BackupService",
    "EventBus",
    "Service",
    "WatchedDirectoryService",
]
