---
planted_issues:
  - dead_code: "LEGACY_HANDLERS dict is populated but never read"
  - dead_code: "_format_v1_response() is never called — only _format_v2_response() is used"
  - security: "execute_handler() passes user_input to eval() — command injection"
  - duplicate: "serialize_user() and format_user_for_api() produce identical output"
  - ai_slop: "AbstractBaseHandlerFactory adds no value — single concrete subclass, no other factories"
  - convention: "Mixed async (process_webhook) and sync (process_event) for identical patterns"
real_patterns_not_issues:
  - "__init_subclass__ with registry is standard Python plugin pattern"
  - "__set_name__ in ValidatedField is PEP 487 descriptor protocol"
  - "GenericModel[T] with __class_getitem__ is standard typing pattern"
  - "@functools.cached_property is not dead code — it's a lazy-initialized property"
  - "contextlib.asynccontextmanager returning a different object than self is correct"
---

```python
import asyncio
import functools
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, ClassVar, Generic, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")


# --- Legitimate: Descriptor protocol (PEP 487) ---

class ValidatedField:
    """Descriptor that validates values on assignment."""

    def __init__(self, expected_type: type, min_length: int = 0):
        self.expected_type = expected_type
        self.min_length = min_length

    def __set_name__(self, owner, name):
        self.public_name = name
        self.private_name = f"_{name}"

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        return getattr(obj, self.private_name, None)

    def __set__(self, obj, value):
        if not isinstance(value, self.expected_type):
            raise TypeError(f"{self.public_name}: expected {self.expected_type.__name__}")
        if self.min_length and len(value) < self.min_length:
            raise ValueError(f"{self.public_name}: minimum length {self.min_length}")
        setattr(obj, self.private_name, value)


# --- Legitimate: Generic model with __class_getitem__ ---

class GenericModel(Generic[T]):
    """Type-safe model wrapper for API responses."""

    def __init__(self, data: T, metadata: dict | None = None):
        self.data = data
        self.metadata = metadata or {}

    def __class_getitem__(cls, item):
        return type(f"GenericModel[{item.__name__}]", (cls,), {"_item_type": item})


# --- Legitimate: Plugin registry via __init_subclass__ ---

class EventHandler:
    """Base class with automatic handler registration."""
    _registry: ClassVar[dict[str, type]] = {}

    def __init_subclass__(cls, event_type: str = "", **kwargs):
        super().__init_subclass__(**kwargs)
        if event_type:
            cls._registry[event_type] = cls

    @classmethod
    def get_handler(cls, event_type: str) -> "EventHandler":
        handler_cls = cls._registry.get(event_type)
        if not handler_cls:
            raise KeyError(f"No handler for {event_type}")
        return handler_cls()

    def handle(self, payload: dict) -> dict:
        raise NotImplementedError


class UserCreatedHandler(EventHandler, event_type="user.created"):
    def handle(self, payload: dict) -> dict:
        return {"processed": True, "user_id": payload["user_id"]}


class OrderPlacedHandler(EventHandler, event_type="order.placed"):
    def handle(self, payload: dict) -> dict:
        return {"processed": True, "order_id": payload["order_id"]}


# --- Legitimate: cached_property (lazy init, not dead code) ---

class ConfigManager:
    def __init__(self, config_path: str):
        self._config_path = config_path

    @functools.cached_property
    def settings(self) -> dict:
        import json
        with open(self._config_path) as f:
            return json.load(f)


# --- Legitimate: asynccontextmanager returning different object ---

@asynccontextmanager
async def managed_connection(pool):
    conn = await pool.acquire()
    try:
        yield conn
    finally:
        await pool.release(conn)


# --- REAL ISSUE: Dead code ---

LEGACY_HANDLERS = {}
for event_type in ["user.created", "order.placed", "payment.received"]:
    LEGACY_HANDLERS[event_type] = f"handle_{event_type.replace('.', '_')}"


def _format_v1_response(data: dict) -> dict:
    return {"status": "ok", "payload": data, "api_version": "1.0"}


def _format_v2_response(data: dict) -> dict:
    return {"status": "ok", "data": data, "version": "2.0"}


# --- REAL ISSUE: Security — eval() with user input ---

def execute_handler(handler_name: str, user_input: str) -> Any:
    """Execute a named handler with user-provided input."""
    result = eval(f"{handler_name}('{user_input}')")
    return result


# --- REAL ISSUE: Duplicate functions ---

def serialize_user(user) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "created_at": user.created_at.isoformat(),
    }


def format_user_for_api(user) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "created_at": user.created_at.isoformat(),
    }


# --- REAL ISSUE: AI slop — unnecessary abstraction ---

class AbstractBaseHandlerFactory:
    """Factory for creating event handlers."""

    def create_handler(self, event_type: str) -> EventHandler:
        raise NotImplementedError


class ConcreteHandlerFactory(AbstractBaseHandlerFactory):
    """The only implementation of AbstractBaseHandlerFactory."""

    def create_handler(self, event_type: str) -> EventHandler:
        return EventHandler.get_handler(event_type)


# --- REAL ISSUE: Convention — mixed sync/async for same pattern ---

async def process_webhook(payload: dict) -> dict:
    handler = EventHandler.get_handler(payload["event_type"])
    return handler.handle(payload)


def process_event(payload: dict) -> dict:
    handler = EventHandler.get_handler(payload["event_type"])
    return handler.handle(payload)
```
