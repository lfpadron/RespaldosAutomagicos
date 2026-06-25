"""ZIP backup module."""

from respaldos_automagicos.zipper.service import (
    ZipBackupResult,
    ZipBackupService,
    ZipCreationError,
)

__all__ = ["ZipBackupResult", "ZipBackupService", "ZipCreationError"]
