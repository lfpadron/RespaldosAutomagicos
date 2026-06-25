"""Basic automagic_ignore parser and matcher."""

from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path


@dataclass(frozen=True, slots=True)
class IgnoreRule:
    """One parsed automagic_ignore rule."""

    pattern: str
    negated: bool = False
    directory_only: bool = False


class AutomagicIgnore:
    """Applies supported automagic_ignore rules to project-relative paths."""

    def __init__(self, rules: list[IgnoreRule] | None = None) -> None:
        """Create an ignore matcher from parsed rules."""
        self._rules = rules or []

    @classmethod
    def from_file(cls, path: Path) -> "AutomagicIgnore":
        """Load ignore rules from an automagic_ignore file if it exists."""
        if not path.exists():
            return cls()
        return cls.from_text(path.read_text(encoding="utf-8"))

    @classmethod
    def from_text(cls, text: str) -> "AutomagicIgnore":
        """Parse supported automagic_ignore syntax from text."""
        rules: list[IgnoreRule] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            negated = line.startswith("!")
            if negated:
                line = line[1:].strip()
            if not line:
                continue

            directory_only = line.endswith("/")
            pattern = line.rstrip("/").replace("\\", "/")
            if pattern:
                rules.append(
                    IgnoreRule(
                        pattern=pattern,
                        negated=negated,
                        directory_only=directory_only,
                    )
                )
        return cls(rules)

    def is_ignored(self, relative_path: str, *, is_dir: bool = False) -> bool:
        """Return whether a project-relative path is ignored."""
        normalized = normalize_relative_path(relative_path)
        ignored = False
        for rule in self._rules:
            if _rule_matches(rule, normalized, is_dir=is_dir):
                ignored = not rule.negated
        return ignored

    @property
    def rules(self) -> tuple[IgnoreRule, ...]:
        """Return parsed rules."""
        return tuple(self._rules)


def normalize_relative_path(relative_path: str | Path) -> str:
    """Normalize a project-relative path to POSIX form."""
    return Path(relative_path).as_posix().strip("/")


def _rule_matches(rule: IgnoreRule, relative_path: str, *, is_dir: bool) -> bool:
    parts = [part for part in relative_path.split("/") if part]
    if rule.directory_only:
        return _directory_rule_matches(
            rule.pattern, relative_path, parts, is_dir=is_dir
        )
    return _glob_rule_matches(rule.pattern, relative_path, parts)


def _directory_rule_matches(
    pattern: str,
    relative_path: str,
    parts: list[str],
    *,
    is_dir: bool,
) -> bool:
    if "/" in pattern:
        return (
            relative_path == pattern
            or relative_path.startswith(f"{pattern}/")
            or (is_dir and fnmatchcase(relative_path, pattern))
        )
    return pattern in parts


def _glob_rule_matches(pattern: str, relative_path: str, parts: list[str]) -> bool:
    if fnmatchcase(relative_path, pattern):
        return True
    if "/" in pattern:
        return False
    return any(fnmatchcase(part, pattern) for part in parts)
