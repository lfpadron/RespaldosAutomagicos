"""Internal domain events used to decouple adapters from services."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class FileChangeType(StrEnum):
    """File system event types relevant for backup planning."""

    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"
    MOVED = "moved"


@dataclass(frozen=True, slots=True)
class FileChangedEvent:
    """Event emitted when a watched root receives a relevant file change."""

    group_id: int
    group_name: str
    root_directory: str
    affected_relative_path: str
    changed_path: str
    change_type: FileChangeType
    occurred_at: datetime
