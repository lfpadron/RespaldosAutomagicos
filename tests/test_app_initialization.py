"""Initialization tests for the project skeleton."""

import asyncio
from pathlib import Path

from sqlalchemy import inspect
from textual.widgets import DataTable

from respaldos_automagicos.app import create_app
from respaldos_automagicos.config import AppSettings
from respaldos_automagicos.repositories.backup_groups import BackupGroupRepository
from respaldos_automagicos.tui.app import RespaldosAutomagicosTUI


def sqlite_url(path: Path) -> str:
    """Build a SQLite URL from a pytest temporary path."""
    return f"sqlite:///{path.as_posix()}"


def test_core_application_initializes_database(tmp_path: Path) -> None:
    """The core application should initialize the expected database tables."""
    settings = AppSettings(database_url=sqlite_url(tmp_path / "app.db"))
    app = create_app(settings)

    app.initialize_storage()

    tables = set(inspect(app.engine).get_table_names())
    assert {
        "audit_logs",
        "backup_groups",
        "backup_history",
        "watched_directories",
    }.issubset(tables)


def test_tui_can_be_constructed() -> None:
    """The Textual TUI should be constructable with explicit settings."""
    settings = AppSettings(app_version="1.0")
    app = RespaldosAutomagicosTUI(settings=settings)

    assert app.settings.app_name == "RespaldosAutomagicos"
    assert app.settings.app_version == "1.0"


def test_tui_group_refresh_preserves_cursor_row(tmp_path: Path) -> None:
    """Automatic group refresh should not jump focus back to the first row."""
    settings = AppSettings(database_url=sqlite_url(tmp_path / "tui.db"))
    core_app = create_app(settings)
    core_app.initialize_storage()
    root = tmp_path / "root"
    destination = tmp_path / "dest"
    root.mkdir()
    destination.mkdir()
    with core_app.session_factory() as session:
        repository = BackupGroupRepository(session)
        repository.create(
            name="A",
            root_directory=str(root),
            destination_directory=str(destination),
            enabled=True,
            scan_interval_minutes=15,
            stabilization_minutes=5,
            backups_to_keep=10,
            days_to_keep=30,
            compression_level=6,
        )
        repository.create(
            name="B",
            root_directory=str(root),
            destination_directory=str(destination),
            enabled=True,
            scan_interval_minutes=15,
            stabilization_minutes=5,
            backups_to_keep=10,
            days_to_keep=30,
            compression_level=6,
        )
        session.commit()

    async def run_scenario() -> None:
        app = RespaldosAutomagicosTUI(core_app=core_app)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.query_one("#groups-table", DataTable)
            table.move_cursor(row=1, animate=False)

            app.refresh_groups()

            assert table.cursor_row == 1

    asyncio.run(run_scenario())
