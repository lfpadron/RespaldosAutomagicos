"""SQLAlchemy database primitives."""

from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from respaldos_automagicos.config import AppSettings


class Base(DeclarativeBase):
    """Declarative base for all SQLAlchemy models."""


def ensure_sqlite_parent_directory(database_url: str) -> None:
    """Create the parent directory for a file-based SQLite database URL."""
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        return

    database = url.database
    if database is None or database in ("", ":memory:"):
        return

    Path(database).parent.mkdir(parents=True, exist_ok=True)


def create_engine_from_settings(settings: AppSettings) -> Engine:
    """Create a SQLAlchemy engine from application settings."""
    ensure_sqlite_parent_directory(settings.database_url)
    return create_engine(settings.database_url, future=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create the SQLAlchemy session factory used by repositories."""
    return sessionmaker(bind=engine, expire_on_commit=False)


def initialize_database(engine: Engine) -> None:
    """Create all known database tables.

    Alembic is the preferred mechanism for production migrations. This helper is
    intentionally small and useful for tests or first-run local development.
    """
    from respaldos_automagicos import models  # noqa: F401

    Base.metadata.create_all(engine)
