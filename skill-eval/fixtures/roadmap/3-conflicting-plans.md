---
# Fixture metadata (stripped by loader)
scenario: "Two plans modifying the same critical file (src/config.py) with ordering constraint"
expected_phases: 2
expected_ordering: "A in Phase 1 (restructure), B in Phase 2 (add keys)"
conflict_file: "src/config.py"
---

### Plan A: Refactor Config Loader

**Goal:** Restructure the configuration loader to use a validated schema instead of raw dict access.
**Cynefin Domain:** Complicated
**Architecture Summary:** Replaces the internal implementation of the config loader. Changes how config keys are stored, accessed, and validated. All downstream consumers continue using the same public API but the internals change significantly.

## File Structure

```
src/
  config.py              # Config loader (MAJOR REFACTOR - new schema-based internals)
  config_schema.py       # New: Pydantic schema for config validation
tests/
  test_config.py         # Updated tests for new config internals
  test_config_schema.py  # New: Schema validation tests
```

## Task 1: Define Config Schema

**Files:** `src/config_schema.py`

Create a Pydantic BaseSettings model that defines all known configuration keys with types, defaults, and validation rules. This replaces the current raw dict approach where config keys are accessed as strings.

## Task 2: Refactor Config Loader Internals

**Files:** `src/config.py`

Replace the internal `_config_data` dict with an instance of the new Pydantic schema. Change `load_config()` to parse the YAML file through the schema validator. Change `get(key)` to use attribute access on the schema instance instead of dict lookup. Remove the old `_parse_raw_value()` helper that did manual type coercion. Rename internal keys from snake_case to match the schema field names (e.g., `db_host` stays but `db-host` alias is removed).

## Task 3: Update Config Tests

**Files:** `tests/test_config.py`, `tests/test_config_schema.py`

Rewrite config tests to validate schema-based loading. Add schema validation tests for type coercion, missing required keys, and default values. Remove tests for the deleted `_parse_raw_value()` helper.

---

### Plan B: Add New Config Section

**Goal:** Add a new notifications configuration section to support the upcoming notification feature.
**Cynefin Domain:** Clear
**Architecture Summary:** Adds new configuration keys to the YAML config file and corresponding access code in the config loader. The new keys control notification behavior (email provider, rate limits, retry policy).

## File Structure

```
src/
  config.py              # Config loader (ADD new notification keys and accessor methods)
  notifications/
    settings.py           # Notification-specific settings that read from config
config/
  defaults.yaml          # Default config file (ADD notification section)
tests/
  test_config.py         # Add tests for new notification config keys
  test_notification_settings.py  # Tests for notification settings module
```

## Task 1: Add Notification Config Keys

**Files:** `src/config.py`, `config/defaults.yaml`

Add a new `notifications` section to the YAML defaults with keys: `email_provider` (str, default "smtp"), `rate_limit_per_minute` (int, default 60), `retry_max_attempts` (int, default 3), `retry_backoff_seconds` (float, default 1.5). Add `get_notification_config()` method to the config loader that returns the notification section as a dict.

## Task 2: Create Notification Settings Module

**Files:** `src/notifications/settings.py`

Create a settings module that imports from `src/config.py` using `get('notifications.email_provider')` and `get('notifications.rate_limit_per_minute')` to access the new keys. Provide typed accessors for notification-specific settings.

## Task 3: Add Config and Settings Tests

**Files:** `tests/test_config.py`, `tests/test_notification_settings.py`

Test that new notification keys load from YAML with correct types and defaults. Test the settings module typed accessors. Test override behavior via environment variables.
