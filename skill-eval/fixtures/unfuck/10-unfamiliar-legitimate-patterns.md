---
planted_issues:
  - dead_import: "typing_extensions imported but never used"
  - unused_variable: "_sentinel in FieldDescriptor.__init__ assigned but only used in __set_name__"
real_patterns_not_issues:
  - "RegistryMeta metaclass is a standard Python pattern for auto-registration, not unnecessary abstraction"
  - "FieldDescriptor is a standard Python descriptor protocol implementation, not AI slop"
  - "register() classmethod triggered by __init_subclass__ is the recommended alternative to metaclass registration since Python 3.6"
  - "__set_name__ is part of the descriptor protocol PEP 487, not dead code"
---

```python
import typing_extensions
from abc import ABC, abstractmethod
from typing import Any, ClassVar


class RegistryMeta(type):
    """Metaclass that auto-registers subclasses in a class-level registry.

    This is a standard Python pattern for plugin systems and extensible
    architectures. Each concrete subclass is automatically registered
    when its class body is executed.
    """
    _registry: ClassVar[dict[str, type]] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not getattr(cls, '__abstractmethods__', None):
            cls._registry[cls.__name__] = cls

    @classmethod
    def get_registered(mcs) -> dict[str, type]:
        return dict(mcs._registry)


class FieldDescriptor:
    """Data descriptor implementing validation on attribute access.

    Uses the descriptor protocol (PEP 487) to intercept attribute
    get/set operations and enforce type constraints at assignment time.
    """

    def __init__(self, field_type: type, required: bool = True):
        self.field_type = field_type
        self.required = required
        self._sentinel = object()
        self.attr_name: str = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self.attr_name = f"_{name}"

    def __get__(self, obj: Any, objtype: type | None = None) -> Any:
        if obj is None:
            return self
        return getattr(obj, self.attr_name, self._sentinel)

    def __set__(self, obj: Any, value: Any) -> None:
        if value is None and self.required:
            raise ValueError(f"{self.attr_name[1:]} is required")
        if value is not None and not isinstance(value, self.field_type):
            raise TypeError(
                f"{self.attr_name[1:]} must be {self.field_type.__name__}, "
                f"got {type(value).__name__}"
            )
        setattr(obj, self.attr_name, value)


class BaseProcessor(ABC):
    """Abstract base for data processors with auto-registration."""

    _registry: ClassVar[dict[str, type["BaseProcessor"]]] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "__abstractmethods__", None):
            cls._registry[cls.__name__] = cls

    @classmethod
    def get_processor(cls, name: str) -> "BaseProcessor":
        if name not in cls._registry:
            raise KeyError(f"Unknown processor: {name}. Available: {list(cls._registry)}")
        return cls._registry[name]()

    @abstractmethod
    def process(self, data: dict) -> dict:
        ...

    @abstractmethod
    def validate(self, data: dict) -> bool:
        ...


class JsonProcessor(BaseProcessor):
    """Processes JSON-formatted data with schema validation."""

    format_name = FieldDescriptor(str, required=True)
    max_depth = FieldDescriptor(int, required=False)

    def __init__(self):
        self.format_name = "json"
        self.max_depth = 10

    def process(self, data: dict) -> dict:
        return {k: v for k, v in data.items() if v is not None}

    def validate(self, data: dict) -> bool:
        return isinstance(data, dict) and len(data) > 0


class CsvProcessor(BaseProcessor):
    """Processes CSV-formatted data with delimiter detection."""

    delimiter = FieldDescriptor(str, required=True)

    def __init__(self):
        self.delimiter = ","

    def process(self, data: dict) -> dict:
        return {k: str(v) for k, v in data.items()}

    def validate(self, data: dict) -> bool:
        return all(isinstance(k, str) for k in data.keys())


def get_available_processors() -> list[str]:
    """Return names of all registered processors."""
    return list(BaseProcessor._registry.keys())


def process_data(processor_name: str, data: dict) -> dict:
    """Process data using the named processor."""
    processor = BaseProcessor.get_processor(processor_name)
    if not processor.validate(data):
        raise ValueError(f"Invalid data for {processor_name}")
    return processor.process(data)
```
