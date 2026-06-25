"""Content hashing service."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from respaldos_automagicos.ignore.service import AutomagicIgnore
from respaldos_automagicos.utils.files import ProjectFile, collect_project_files


class ContentReadError(OSError):
    """Raised when project content cannot be read for hashing."""


@dataclass(frozen=True, slots=True)
class ContentHashResult:
    """Result of hashing included project files."""

    content_hash: str
    file_count: int
    files: tuple[ProjectFile, ...]


class ContentHashService:
    """Calculates deterministic content hashes for watched directories."""

    def calculate(
        self,
        project_path: Path,
        ignore: AutomagicIgnore,
    ) -> ContentHashResult:
        """Calculate a hash from relative paths and file bytes."""
        files = collect_project_files(project_path, ignore)
        digest = sha256()
        try:
            for project_file in files:
                digest.update(project_file.relative_path.encode("utf-8"))
                digest.update(b"\0")
                with project_file.absolute_path.open("rb") as file:
                    for chunk in iter(lambda: file.read(1024 * 1024), b""):
                        digest.update(chunk)
                digest.update(b"\0")
        except OSError as exc:
            raise ContentReadError(str(exc)) from exc

        return ContentHashResult(
            content_hash=digest.hexdigest(),
            file_count=len(files),
            files=tuple(files),
        )
