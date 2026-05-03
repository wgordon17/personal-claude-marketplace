---
planted_issues: []
clean_distractors:
  - "EventBus publish/subscribe is a legitimate pattern, not over-abstraction"
  - "Dataclass with defaults is idiomatic Python"
  - "Docstrings are concise and informative, not excessive"
  - "Error handling re-raises with added context (not catch-rethrow)"
  - "Type hints are appropriate, not over-annotated"
negative_test: true
---

```python
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class Event:
    """Domain event with a type tag and arbitrary payload."""

    event_type: str
    payload: dict = field(default_factory=dict)


class EventBus:
    """Simple in-process pub/sub for decoupling domain events from handlers."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[[Event], None]]] = {}

    def subscribe(self, event_type: str, handler: Callable[[Event], None]) -> None:
        """Register a handler for a given event type."""
        self._subscribers.setdefault(event_type, []).append(handler)

    def publish(self, event: Event) -> None:
        """Dispatch an event to all registered handlers."""
        for handler in self._subscribers.get(event.event_type, []):
            try:
                handler(event)
            except Exception as exc:
                logger.error(
                    "Handler %s failed for event %s: %s",
                    handler.__name__,
                    event.event_type,
                    exc,
                )


def parse_config(raw: dict) -> dict:
    """Validate and normalize a raw configuration dictionary.

    Raises ValueError if required keys are missing.
    """
    required = {"host", "port", "database"}
    missing = required - raw.keys()
    if missing:
        raise ValueError(f"Missing required config keys: {sorted(missing)}")

    return {
        "host": raw["host"].strip(),
        "port": int(raw["port"]),
        "database": raw["database"].strip(),
        "timeout": int(raw.get("timeout", 30)),
    }
```
