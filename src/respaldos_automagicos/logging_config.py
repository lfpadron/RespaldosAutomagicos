"""Structured logging configuration."""

import logging
from pathlib import Path

from respaldos_automagicos.config import AppSettings


class ContextDefaultsFilter(logging.Filter):
    """Ensure structured log fields exist for every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Add default group and directory values when absent."""
        if not hasattr(record, "group"):
            record.group = "-"
        if not hasattr(record, "directory"):
            record.directory = "-"
        return True


def configure_logging(settings: AppSettings) -> None:
    """Configure console and file logging for the application."""
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("respaldos_automagicos")
    logger.setLevel(settings.log_level.upper())
    logger.propagate = False

    for handler in logger.handlers:
        logger.removeHandler(handler)

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s grupo=%(group)s directorio=%(directory)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    context_filter = ContextDefaultsFilter()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(context_filter)

    file_handler = logging.FileHandler(
        Path(settings.logs_dir) / "respaldos_automagicos.log",
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(context_filter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced RespaldosAutomagicos logger."""
    return logging.getLogger(f"respaldos_automagicos.{name}")
