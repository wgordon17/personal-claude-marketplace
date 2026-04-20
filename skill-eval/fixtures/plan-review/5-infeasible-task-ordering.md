---
planted_issues:
  - parallel_file_contention: "Tasks 2 and 3 are marked parallel but both modify src/config.py"
  - implicit_ordering_conflict: "Task 3 adds config keys that Task 2 reads, but no explicit dependency declared"
difficulty: hard
type: positive
---

# Plan: Unified Feature Flag System

**Branch:** feat/feature-flags
**Status:** Draft
**Created:** 2026-04-11
**Goal:** Replace ad-hoc boolean checks scattered across the codebase with a unified feature flag system backed by a config file and runtime toggles.

**Cynefin Domain:** Complicated

**Iterations:**
- review-cycle: 0
- fix-cycle: 0

## File Structure

```
src/
  flags/
    flag_registry.py          # Central flag registration and lookup
    flag_evaluator.py         # Evaluate flag state with rollout rules
    flag_middleware.py         # Request-scoped flag context
  config.py                   # Application config (shared across modules)
  api/
    flag_endpoints.py         # Admin endpoints for flag management
  db/
    flag_models.py            # Flag override persistence
tests/
  test_flag_registry.py
  test_flag_evaluator.py
  test_flag_middleware.py
  test_flag_endpoints.py
```

## Key Decisions

- Flags defined in code (flag_registry.py) as the source of truth, with database overrides for runtime toggling.
- Three flag states: enabled (on for everyone), disabled (off for everyone), rollout (percentage-based).
- Config.py holds the DEFAULT_FLAGS dict and the FLAG_EVALUATION_ORDER list.

## Tasks

### Task 1: Flag Data Models
- **Files:** `src/db/flag_models.py`
- **Description:** Define SQLAlchemy model FlagOverride with fields: id, flag_name (unique), state (enum: enabled/disabled/rollout), rollout_percentage (nullable int 0-100), updated_by, updated_at. Create Alembic migration. Add index on flag_name for fast lookups.
- **Test command:** `uv run pytest tests/test_flag_models.py`
- **Dependencies:** None

### Task 2: Flag Registry and Default Config
- **Files:** `src/flags/flag_registry.py`, `src/config.py`
- **Description:** Create the FlagRegistry class that loads flag definitions from config. Register flags with name, description, and default state. Read the DEFAULT_FLAGS dict and FLAG_EVALUATION_ORDER list from src/config.py. Add the DEFAULT_FLAGS dictionary and FLAG_EVALUATION_ORDER list to config.py with initial flag entries for dark_mode, new_dashboard, and beta_search.
- **Parallel with:** Task 3
- **Test command:** `uv run pytest tests/test_flag_registry.py`
- **Dependencies:** Task 1

### Task 3: Flag Evaluator with Rollout Rules
- **Files:** `src/flags/flag_evaluator.py`, `src/config.py`
- **Description:** Implement the FlagEvaluator that determines a flag's effective state for a given user. Check order: database override first, then code default. For rollout state, hash the user ID with the flag name to get a deterministic percentage bucket. Add the FLAG_ROLLOUT_SALT and FLAG_CACHE_TTL settings to src/config.py.
- **Parallel with:** Task 2
- **Test command:** `uv run pytest tests/test_flag_evaluator.py`
- **Dependencies:** Task 1

### Task 4: Request-Scoped Flag Middleware
- **Files:** `src/flags/flag_middleware.py`
- **Description:** Flask middleware that evaluates all registered flags for the current user at request start and stores results in Flask's g object. Downstream code accesses flags via g.flags.is_enabled("flag_name"). Cache evaluated flags for the request duration to avoid repeated DB queries.
- **Test command:** `uv run pytest tests/test_flag_middleware.py`
- **Dependencies:** Task 2, Task 3

### Task 5: Admin Flag Management API
- **Files:** `src/api/flag_endpoints.py`
- **Description:** Admin-only REST endpoints: GET /flags (list all flags with current state), PATCH /flags/:name (update override state and rollout percentage), DELETE /flags/:name/override (remove override, revert to code default). Require admin role. Log all changes with actor and timestamp.
- **Test command:** `uv run pytest tests/test_flag_endpoints.py`
- **Dependencies:** Task 1, Task 4
