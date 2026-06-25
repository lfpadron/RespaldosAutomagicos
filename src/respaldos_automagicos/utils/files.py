"""Project file collection utilities."""

from dataclasses import dataclass
from pathlib import Path

from respaldos_automagicos.ignore.service import AutomagicIgnore


@dataclass(frozen=True, slots=True)
class ProjectFile:
    """Included project file with normalized relative path."""

    absolute_path: Path
    relative_path: str


def collect_project_files(
    project_path: Path,
    ignore: AutomagicIgnore,
) -> list[ProjectFile]:
    """Return included files below a project path in stable order."""
    files: list[ProjectFile] = []
    for path in project_path.rglob("*"):
        relative_path = path.relative_to(project_path).as_posix()
        if path.is_dir():
            continue
        if ignore.is_ignored(relative_path):
            continue
        files.append(ProjectFile(absolute_path=path, relative_path=relative_path))
    return sorted(files, key=lambda file: file.relative_path)
