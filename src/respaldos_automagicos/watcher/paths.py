"""Path helpers for watcher events."""

import posixpath


def resolve_affected_directory(root_directory: str, changed_path: str) -> str | None:
    """Return the immediate child directory affected by a filesystem event."""
    root_parts = _path_parts(root_directory)
    changed_parts = _path_parts(changed_path)

    if len(changed_parts) <= len(root_parts):
        return None

    if _casefold_parts(changed_parts[: len(root_parts)]) != _casefold_parts(root_parts):
        return None

    return changed_parts[len(root_parts)]


def _path_parts(path: str) -> list[str]:
    normalized = posixpath.normpath(path.replace("\\", "/"))
    if normalized in (".", "/"):
        return []
    return [part for part in normalized.split("/") if part not in ("", ".")]


def _casefold_parts(parts: list[str]) -> list[str]:
    return [part.casefold() for part in parts]
