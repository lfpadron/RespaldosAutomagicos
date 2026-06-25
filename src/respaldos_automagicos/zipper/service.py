"""ZIP backup creation service."""

import json
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from respaldos_automagicos.utils.files import ProjectFile


class ZipCreationError(OSError):
    """Raised when a ZIP backup cannot be created."""


@dataclass(frozen=True, slots=True)
class ZipBackupResult:
    """Result of creating a ZIP backup."""

    backup_path: Path
    backup_size_bytes: int


class ZipBackupService:
    """Creates ZIP backup artifacts."""

    def create_backup(
        self,
        *,
        project_root_name: str,
        destination_directory: Path,
        backup_name: str,
        files: tuple[ProjectFile, ...],
        manifest: dict[str, object],
        compression_level: int,
    ) -> ZipBackupResult:
        """Create a ZIP file with project contents and manifest."""
        destination_directory.mkdir(parents=True, exist_ok=True)
        backup_path = destination_directory / backup_name
        compresslevel = max(0, min(9, compression_level))

        try:
            with ZipFile(
                backup_path,
                mode="w",
                compression=ZIP_DEFLATED,
                compresslevel=compresslevel,
                strict_timestamps=False,
            ) as backup_zip:
                for project_file in files:
                    archive_name = (
                        f"{project_root_name}/{project_file.relative_path}"
                    ).replace("\\", "/")
                    backup_zip.write(project_file.absolute_path, archive_name)

                manifest_name = f"{project_root_name}/manifest.json"
                backup_zip.writestr(
                    manifest_name,
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                )
        except (OSError, ValueError) as exc:
            raise ZipCreationError(str(exc)) from exc

        return ZipBackupResult(
            backup_path=backup_path,
            backup_size_bytes=backup_path.stat().st_size,
        )
