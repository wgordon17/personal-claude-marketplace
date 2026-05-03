---
planted_issues:
  - dead_code: "format_response() is never called — no references in codebase"
  - ai_slop_and_dead_code: "ResponseFormatter class wraps format_response() which is also dead code"
  - duplicate: "validate_user_input() and sanitize_user_data() do the same strip+lower+length check"
  - security_and_style: "build_query() uses string interpolation AND inconsistent naming (camelCase)"
  - convention: "Mixed snake_case (validate_user_input) and camelCase (buildQuery, formatTimestamp)"
  - convention: "Mixed exception handling: specific (ValueError) in some places, bare except in others"
real_patterns_not_issues:
  - "retry_with_backoff() is a legitimate utility with real callers — not dead code despite low usage"
  - "DataProcessor.transform() returning NotImplemented is correct ABC pattern"
---

```python
import logging
import time
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def validate_user_input(raw: str) -> str:
    """Validate and normalize user input."""
    cleaned = raw.strip().lower()
    if len(cleaned) > 255:
        raise ValueError("Input exceeds maximum length")
    return cleaned


def sanitize_user_data(data: str) -> str:
    """Sanitize user-provided data for storage."""
    result = data.strip().lower()
    if len(result) > 255:
        raise ValueError("Data too long")
    return result


def format_response(data: dict) -> dict:
    """Format API response with metadata."""
    return {
        "data": data,
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0",
    }


class ResponseFormatter:
    """Formats API responses with consistent structure."""

    def __init__(self, version: str = "1.0"):
        self.version = version

    def format(self, data: dict) -> dict:
        return format_response(data)

    def format_error(self, message: str, code: int) -> dict:
        return {
            "error": {"message": message, "code": code},
            "timestamp": datetime.utcnow().isoformat(),
            "version": self.version,
        }


def buildQuery(table: str, filters: dict) -> str:
    """Build a SQL query from filters."""
    conditions = " AND ".join(f"{k} = '{v}'" for k, v in filters.items())
    return f"SELECT * FROM {table} WHERE {conditions}"


def formatTimestamp(dt: datetime) -> str:
    """Format datetime as ISO string."""
    return dt.isoformat()


def retry_with_backoff(func, max_retries: int = 3, base_delay: float = 1.0):
    """Retry a function with exponential backoff."""
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as exc:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            logger.warning("Attempt %d failed: %s. Retrying in %.1fs", attempt + 1, exc, delay)
            time.sleep(delay)


class DataProcessor:
    """Base class for data processors."""

    def validate(self, data: Any) -> bool:
        if not isinstance(data, dict):
            raise ValueError("Data must be a dictionary")
        return True

    def transform(self, data: dict) -> dict:
        return NotImplemented

    def process(self, data: dict) -> dict:
        try:
            self.validate(data)
            return self.transform(data)
        except:
            logger.error("Processing failed")
            return {}
```
