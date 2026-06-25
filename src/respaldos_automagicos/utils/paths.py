"""Path utility helpers."""

from pathlib import Path


def normalize_path_text(path: str | Path) -> str:
    """Return a normalized string representation for a filesystem path."""
    return str(Path(path).expanduser())
