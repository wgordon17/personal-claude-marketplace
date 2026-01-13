---
name: migrations
description: PROACTIVE skill - Activates when models are modified. Safe Django migration creation, review, and execution guidance.
allowed-tools: [Read, Grep, Glob, Bash, LSP]
---

# Database Migration Skill

## Purpose

Ensure Django migrations are created safely, reviewed for dangerous operations, and executed with zero-downtime considerations.

## When to Trigger

This skill activates PROACTIVELY when:
- Editing model files (`models.py`)
- Running `makemigrations` or `migrate`
- Reviewing existing migrations
- User asks about migrations

## Required Skills

- `/uv-python` — For Django management commands

## Migration Safety Workflow

### Step 1: Detect Model Changes

```bash
git diff --name-only | grep models.py
```

### Step 2: Generate Migration

```bash
uv run python manage.py makemigrations --dry-run
uv run python manage.py makemigrations
```

### Step 3: Review Migration

Check for dangerous operations in `references/migration-safety.md`.

### Step 4: Test Migration

```bash
# Forward migration
uv run python manage.py migrate

# Backward migration (verify reversibility)
uv run python manage.py migrate app_name previous_migration_number
```

## Dangerous Operations

| Operation | Risk | Mitigation |
|-----------|------|------------|
| `RemoveField` | Data loss | Data migration first |
| `DeleteModel` | Data loss | Archive data first |
| `AlterField` (type change) | Data loss | Multi-step migration |
| `AddField` (non-nullable, no default) | Fails on existing data | Add default or make nullable |
| `RenameField` | May break queries | Update code first |
| `RenameModel` | May break FK references | Update code first |
| `AlterField` on large table | Long lock | During maintenance window |

## Safe Migration Patterns

### Adding a Non-Nullable Field

**Wrong:**
```python
# This will fail if table has existing rows
operations = [
    migrations.AddField(
        model_name='user',
        name='new_field',
        field=models.CharField(max_length=100),  # No default!
    ),
]
```

**Right:**
```python
# Step 1: Add as nullable
operations = [
    migrations.AddField(
        model_name='user',
        name='new_field',
        field=models.CharField(max_length=100, null=True),
    ),
]

# Step 2: Data migration to populate
def populate_new_field(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    User.objects.filter(new_field__isnull=True).update(new_field='default')

operations = [
    migrations.RunPython(populate_new_field, migrations.RunPython.noop),
]

# Step 3: Make non-nullable
operations = [
    migrations.AlterField(
        model_name='user',
        name='new_field',
        field=models.CharField(max_length=100),
    ),
]
```

### Renaming a Field

**Right:**
```python
# Use RenameField, not RemoveField + AddField
operations = [
    migrations.RenameField(
        model_name='user',
        old_name='old_field',
        new_name='new_field',
    ),
]
```

### Removing a Field

**Right:**
```python
# Step 1: Remove field from code (but keep in DB)
# Step 2: Deploy code that doesn't use the field
# Step 3: Remove from database
operations = [
    migrations.RemoveField(
        model_name='user',
        name='unused_field',
    ),
]
```

## Migration Naming Convention

```
NNNN_<app>_<action>_<what>.py
```

Examples:
- `0015_accounts_add_two_factor_field.py`
- `0016_accounts_populate_two_factor.py`
- `0017_accounts_make_two_factor_required.py`

## Zero-Downtime Checklist

- [ ] No `RemoveField` on fields still referenced in code
- [ ] No `AlterField` type changes without data migration
- [ ] All new fields have defaults or are nullable
- [ ] No `RenameField` until all code uses new name
- [ ] Indexes added in separate migration from data changes
- [ ] Large table operations scheduled for maintenance window

## Commands Reference

```bash
# Create migration
uv run python manage.py makemigrations [app_name]

# Show migration plan
uv run python manage.py showmigrations

# Show SQL for migration
uv run python manage.py sqlmigrate app_name migration_number

# Run migrations
uv run python manage.py migrate

# Rollback migration
uv run python manage.py migrate app_name previous_number

# Fake migration (mark as run without executing)
uv run python manage.py migrate --fake app_name migration_number
```

## Integration

### Called By
- `project-dev:feature-writer` — After model changes
- `project-dev:orchestrator` — During feature implementation

### Invokes
- `project-dev:migration-reviewer` — For safety review
