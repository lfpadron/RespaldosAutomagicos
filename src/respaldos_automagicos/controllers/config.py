"""Controller for read-only configuration summaries."""

import platform
import subprocess
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from respaldos_automagicos.config import AppSettings
from respaldos_automagicos.repositories.backup_groups import BackupGroupRepository
from respaldos_automagicos.repositories.backup_history import BackupHistoryRepository
from respaldos_automagicos.repositories.watched_directories import (
    WatchedDirectoryRepository,
)


@dataclass(frozen=True, slots=True)
class ConfigSummary:
    """Read-only application configuration summary."""

    database_url: str
    version: str
    python_version: str
    uv_version: str
    group_count: int
    project_count: int
    backup_count: int


class ConfigController:
    """Coordinates read-only configuration data."""

    def __init__(
        self,
        *,
        settings: AppSettings,
        session_factory: sessionmaker[Session],
    ) -> None:
        """Create the config controller."""
        self._settings = settings
        self._session_factory = session_factory

    def summary(self) -> ConfigSummary:
        """Return configuration and aggregate counts."""
        with self._session_factory() as session:
            group_count = BackupGroupRepository(session).count_active()
            project_count = WatchedDirectoryRepository(session).count_all()
            backup_count = BackupHistoryRepository(session).count_all()
        return ConfigSummary(
            database_url=self._settings.database_url,
            version=self._settings.app_version,
            python_version=platform.python_version(),
            uv_version=_uv_version(),
            group_count=group_count,
            project_count=project_count,
            backup_count=backup_count,
        )


def _uv_version() -> str:
    try:
        completed = subprocess.run(
            ["uv", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except OSError:
        return "No disponible"
    return completed.stdout.strip() or "No disponible"
