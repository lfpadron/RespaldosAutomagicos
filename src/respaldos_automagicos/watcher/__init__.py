"""Directory watching module for future watchdog integration."""

from respaldos_automagicos.watcher.paths import resolve_affected_directory
from respaldos_automagicos.watcher.service import (
    BackupGroupEventHandler,
    DirectoryWatcherService,
)

__all__ = [
    "BackupGroupEventHandler",
    "DirectoryWatcherService",
    "resolve_affected_directory",
]
