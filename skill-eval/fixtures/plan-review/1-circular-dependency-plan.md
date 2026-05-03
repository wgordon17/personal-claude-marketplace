---
planted_issues:
  - circular_dependency: "Task 3 depends on Task 5 output (config schema), Task 5 depends on Task 3 output (migration schema)"
difficulty: hard
type: positive
---

# Plan: Database Migration with Config-Driven Validation

**Branch:** feat/config-driven-migration
**Status:** Draft
**Created:** 2026-04-10
**Goal:** Migrate legacy user records to the new schema while adding config-driven field validation rules that enforce data integrity during and after migration.

**Cynefin Domain:** Complicated

**Iterations:**
- review-cycle: 0
- fix-cycle: 0

## File Structure

```
src/
  migration/
    legacy_adapter.py         # Reads legacy DB format
    schema_transformer.py     # Converts legacy rows to new schema
    migration_runner.py       # Orchestrates batch migration
  config/
    validation_rules.py       # Loads validation config from YAML
    field_validators.py       # Per-field validation functions
  db/
    models.py                 # SQLAlchemy models for new schema
tests/
  test_legacy_adapter.py
  test_schema_transformer.py
  test_migration_runner.py
  test_validation_rules.py
  test_field_validators.py
config/
  validation.yaml             # Validation rule definitions
```

## Key Decisions

- Use YAML for validation rules rather than hardcoded Python to allow ops team to adjust rules without code deploys.
- Batch size of 500 rows per migration chunk to stay within memory limits.
- Validation runs both during migration (transform-time) and as a post-migration integrity check.

## Tasks

### Task 1: Legacy Database Adapter
- **Files:** `src/migration/legacy_adapter.py`
- **Description:** Build read-only adapter for the legacy PostgreSQL schema. Support paginated reads with cursor-based iteration. Handle nullable fields and legacy encoding (Latin-1 to UTF-8 conversion). Return normalized dicts ready for the transformer.
- **Test command:** `uv run pytest tests/test_legacy_adapter.py`
- **Dependencies:** None

### Task 2: New Schema Models
- **Files:** `src/db/models.py`
- **Description:** Define SQLAlchemy models for the new user schema: User, UserProfile, UserPreferences. Add proper indexes, constraints, and relationships. Create Alembic migration script.
- **Test command:** `uv run pytest tests/test_models.py`
- **Dependencies:** None

### Task 3: Schema Transformer
- **Files:** `src/migration/schema_transformer.py`
- **Description:** Convert legacy adapter output dicts into new schema model instances. Map legacy field names to new field names. Apply type coercions (string dates to datetime, string booleans to bool). Uses config from Step 5.2 (the loaded validation rule set) to determine which coercions are safe and which require manual review flagging. Populate default values for fields that have no legacy equivalent.
- **Test command:** `uv run pytest tests/test_schema_transformer.py`
- **Dependencies:** Task 1, Task 2

### Task 4: Migration Runner
- **Files:** `src/migration/migration_runner.py`
- **Description:** Orchestrate the full migration in configurable batch sizes. Read from legacy via adapter, transform via schema_transformer, write to new DB. Track progress in a migration_state table. Support resume-from-checkpoint on failure. Log per-batch statistics.
- **Test command:** `uv run pytest tests/test_migration_runner.py`
- **Dependencies:** Task 3

### Task 5: Config-Driven Validation Rules
- **Files:** `src/config/validation_rules.py`, `src/config/field_validators.py`, `config/validation.yaml`
- **Description:** Load validation rules from YAML config. Each rule specifies: field name, validation type (regex, range, enum, custom), error level (warn/reject). Build validator registry that maps field names to validation functions. Requires schema from Task 3 migration output format to know the exact field names and types that validators will check against. Expose a validate_record(record) function used by the migration runner.
- **Test command:** `uv run pytest tests/test_validation_rules.py tests/test_field_validators.py`
- **Dependencies:** Task 3

## Open Questions

- Should we keep a full audit trail of every transformed row, or just log failures?
- What is the rollback strategy if migration fails at 80% completion?
