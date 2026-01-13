# Migration Safety Quick Reference

## Danger Level Classification

### CRITICAL — Block Migration

| Operation | Why Dangerous | Alternative |
|-----------|---------------|-------------|
| `RemoveField` on active field | Data loss, code breaks | Remove from code first, wait, then migrate |
| `DeleteModel` without backup | Permanent data loss | Archive to separate table first |
| Type change `CharField` → `IntegerField` | Data conversion failure | Multi-step with data migration |
| `NOT NULL` without default on populated table | Migration fails | Add nullable first, populate, then constrain |

### HIGH — Review Carefully

| Operation | Risk | Mitigation |
|-----------|------|------------|
| `AlterField` on large table | Table lock during ALTER | Off-hours deployment |
| `AddIndex` on large table | Build time, possible lock | Use `CONCURRENTLY` via RunSQL |
| `RenameField` | FK references may break | Rename in code first |
| `RenameModel` | Table name changes | Update all references |

### MEDIUM — Proceed with Caution

| Operation | Risk | Mitigation |
|-----------|------|------------|
| `AddField` with default | Minor lock time | Acceptable for small tables |
| `AlterField` (same type) | Usually safe | Test with production data copy |
| `RunPython` | Custom code risk | Ensure reversible, test thoroughly |

### LOW — Usually Safe

| Operation | Notes |
|-----------|-------|
| `AddField` nullable | No data modification |
| `AddIndex` on small table | Quick operation |
| `CreateModel` | New table, no existing data |

## Pre-Migration Checklist

```markdown
- [ ] Migration is reversible (has reverse function or noop)
- [ ] No RemoveField on fields used in code
- [ ] No type changes without data migration
- [ ] All new fields have defaults or are nullable
- [ ] Large table operations scheduled for maintenance
- [ ] Tested on production data copy
```

## Detection Commands

```bash
# Check for dangerous operations in migration
grep -E "(RemoveField|DeleteModel|RenameField|RenameModel)" apps/*/migrations/*.py

# Check migration SQL before running
uv run python manage.py sqlmigrate app_name NNNN

# Dry run migration
uv run python manage.py migrate --plan
```

## Safe Patterns

### Adding Non-Nullable Field

```python
# Migration 1: Add nullable
migrations.AddField('model', 'field', models.Field(null=True))

# Migration 2: Populate data
def populate(apps, schema_editor):
    Model = apps.get_model('app', 'Model')
    Model.objects.filter(field__isnull=True).update(field='default')

migrations.RunPython(populate, migrations.RunPython.noop)

# Migration 3: Make non-nullable
migrations.AlterField('model', 'field', models.Field())
```

### Removing Field Safely

```
1. Remove field usage from code
2. Deploy code (field still in DB, unused)
3. Create RemoveField migration
4. Deploy migration
```

### Renaming Field Safely

```
1. Add new field (copy of old)
2. Data migration: copy old → new
3. Update code to use new field
4. Deploy code
5. Remove old field
```

## Project-Specific Considerations

### Encryption Fields

When migrating `BinaryField` for encrypted data:
- Old encrypted data may not be readable after migration
- Consider re-encryption strategy
- Never expose plaintext during migration

### SessionGrant Changes

Changes to SessionGrant model may:
- Invalidate existing sessions
- Plan for user re-authentication

### User Model Changes

Changes to User model:
- Test with existing encrypted keys
- Verify key derivation still works
- Don't change `key_salt` field behavior
