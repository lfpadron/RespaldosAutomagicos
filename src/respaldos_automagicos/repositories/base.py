"""Base repository primitives."""

from typing import TypeVar

from sqlalchemy.orm import Session

ModelT = TypeVar("ModelT")


class BaseRepository[ModelT]:
    """Small typed base for future SQLAlchemy repositories."""

    def __init__(self, session: Session) -> None:
        """Store the SQLAlchemy session used by concrete repositories."""
        self._session = session

    @property
    def session(self) -> Session:
        """Return the active SQLAlchemy session."""
        return self._session

    def add(self, model: ModelT) -> ModelT:
        """Add a model to the active session and return it."""
        self._session.add(model)
        return model
