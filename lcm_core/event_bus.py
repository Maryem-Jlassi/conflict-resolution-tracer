"""Global event bus — LCM Core publishes; Inspector subscribes.

Design rules:
  • LCM Core never imports from the inspector layer.
  • Listener failures are silently swallowed — the inspector must NEVER
    break Core pipeline execution.
  • Subscribers register at startup (e.g. when the FastAPI app starts).
"""

from typing import Callable, List
from .events import LCMEvent


class EventBus:
    """Simple synchronous pub/sub bus.

    Usage
    -----
    # Publisher (lcm_core internals):
        bus = get_event_bus()
        bus.publish(LCMEvent(EventType.CONFLICT_RESOLVED, datetime.utcnow(), {...}))

    # Subscriber (inspector backend):
        bus = get_event_bus()
        bus.subscribe(my_store.record)
    """

    def __init__(self) -> None:
        self._listeners: List[Callable[[LCMEvent], None]] = []

    def subscribe(self, callback: Callable[[LCMEvent], None]) -> None:
        """Register a listener. Typically called once at Inspector startup."""
        if callback not in self._listeners:
            self._listeners.append(callback)

    def unsubscribe(self, callback: Callable[[LCMEvent], None]) -> None:
        """De-register a listener."""
        self._listeners = [l for l in self._listeners if l is not callback]

    def publish(self, event: LCMEvent) -> None:
        """Deliver an event to every subscriber.

        Any exception raised by a listener is caught and discarded so that
        Inspector problems cannot propagate back into LCM Core.
        """
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                pass

    @property
    def listener_count(self) -> int:
        """Number of active subscribers (useful for health checks)."""
        return len(self._listeners)


# ---------------------------------------------------------------------------
# Module-level singleton  — import `get_event_bus()` everywhere.
# ---------------------------------------------------------------------------

_bus = EventBus()


def get_event_bus() -> EventBus:
    """Return the process-global EventBus singleton."""
    return _bus
