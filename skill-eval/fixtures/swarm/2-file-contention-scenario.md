---
scenario: file-contention-config-refactor
difficulty: hard
tests:
  - detects shared file contention on config.py
  - does NOT mark subtasks as parallelizable
  - proposes sequential ordering
  - identifies risk of concurrent modifications
---

## Task

Refactor the configuration system to support environment-specific overrides, validation, and hot-reloading. Currently all configuration is in a single `config.py` file that every module imports directly.

## Codebase Summary

### Project Structure

```
src/
  config.py                # Global config — imported by EVERY module below
  server.py                # Imports config.py for host, port, debug settings
  database.py              # Imports config.py for DB_URL, POOL_SIZE, TIMEOUT
  cache.py                 # Imports config.py for REDIS_URL, CACHE_TTL
  email_service.py         # Imports config.py for SMTP_HOST, SMTP_PORT, FROM_ADDR
  scheduler.py             # Imports config.py for CRON schedules, WORKER_COUNT
  logging_setup.py         # Imports config.py for LOG_LEVEL, LOG_FORMAT
  auth.py                  # Imports config.py for JWT_SECRET, TOKEN_EXPIRY
  api/
    routes.py              # Imports config.py for API_PREFIX, RATE_LIMIT
    middleware.py           # Imports config.py for CORS_ORIGINS, ALLOWED_HOSTS
tests/
  conftest.py              # Patches config.py values for test isolation
  test_server.py
  test_database.py
  test_cache.py
```

### Key File: src/config.py

Every module in the project imports this file directly. It is the single source of truth for all configuration values:

```python
import os

# Server
HOST = os.getenv("APP_HOST", "0.0.0.0")
PORT = int(os.getenv("APP_PORT", "8080"))
DEBUG = os.getenv("APP_DEBUG", "false").lower() == "true"

# Database
DB_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/myapp")
POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "10"))
DB_TIMEOUT = int(os.getenv("DB_TIMEOUT", "30"))

# Cache
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))

# Email
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.example.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
FROM_ADDR = os.getenv("FROM_ADDR", "noreply@example.com")

# Auth
JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")
TOKEN_EXPIRY = int(os.getenv("TOKEN_EXPIRY", "3600"))

# Scheduler
WORKER_COUNT = int(os.getenv("WORKER_COUNT", "4"))
CRON_INTERVAL = int(os.getenv("CRON_INTERVAL", "60"))

# API
API_PREFIX = os.getenv("API_PREFIX", "/api/v1")
RATE_LIMIT = int(os.getenv("RATE_LIMIT", "100"))

# CORS / Security
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "*").split(",")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = os.getenv("LOG_FORMAT", "%(asctime)s %(levelname)s %(message)s")
```

### Subtask Breakdown (all modify config.py)

The following four subtasks have been identified. Note that **every subtask modifies `src/config.py`** because the refactoring fundamentally restructures how configuration is defined, loaded, validated, and consumed:

1. **Add environment-specific config loading** — Restructure `config.py` to load from YAML files per environment (`config/dev.yaml`, `config/prod.yaml`), with `config.py` becoming the loader that merges environment-specific values with defaults. Modifies `config.py` import interface.

2. **Add config validation with pydantic** — Replace bare `os.getenv` calls in `config.py` with a pydantic `Settings` model that validates types, ranges, and required fields at startup. Changes the entire structure of `config.py` from module-level variables to a pydantic model class.

3. **Add hot-reload support** — Add a file watcher that detects changes to config files and reloads `config.py` values at runtime without restarting. Modifies `config.py` to use a mutable config holder instead of module-level constants. Changes how every consumer reads from `config.py`.

4. **Update all consumers to use new config interface** — Migrate every module that does `from config import X` to use the new config access pattern (e.g., `config.get("X")` or `settings.X`). Touches `config.py` to finalize the public API and every importing module.

### Dependencies

- Python 3.13, Flask 3.0, pydantic 2.6, pyyaml 6.0, watchdog 4.0
- 12 modules import `config.py` directly
- `conftest.py` patches config values — must be updated to match new pattern
