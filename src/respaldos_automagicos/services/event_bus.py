"""Small synchronous event bus for in-process domain events."""

from collections.abc import Callable
from typing import TypeVar

EventT = TypeVar("EventT")


class EventBus:
    """Dispatches in-process events to subscribed handlers."""

    def __init__(self) -> None:
        """Create an empty event bus."""
        self._subscribers: dict[type[object], list[Callable[[object], None]]] = {}

    def subscribe(
        self,
        event_type: type[EventT],
        handler: Callable[[EventT], None],
    ) -> None:
        """Subscribe a handler to events of the given type."""

        def wrapped(event: object) -> None:
            if isinstance(event, event_type):
                handler(event)

        self._subscribers.setdefault(event_type, []).append(wrapped)

    def publish(self, event: object) -> None:
        """Publish an event to all matching subscribers."""
        for event_type, subscribers in self._subscribers.items():
            if isinstance(event, event_type):
                for subscriber in subscribers:
                    subscriber(event)
