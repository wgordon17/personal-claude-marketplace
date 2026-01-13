# Code → Documentation Mapping

Quick reference for which code changes require which documentation updates.

## GLOSSARY.md Triggers

| Code Path | Trigger Pattern | Update Action |
|-----------|-----------------|---------------|
| `apps/encryption/primitives/*.py` | New function or class | Add algorithm/primitive entry |
| `apps/encryption/keys/*.py` | New key type or derivation | Add key entry with lifecycle |
| `apps/encryption/constants.py` | New constant | Add parameter reference |
| `apps/security/services/*.py` | New service method | Add service documentation |
| `apps/security/config.py` | Config changes | Update configuration table |
| `apps/accounts/models.py` | Encryption fields | Update model documentation |
| `apps/banking/models.py` | Encryption fields | Update model documentation |

### Specific Patterns

```python
# Any of these in code → GLOSSARY.md update
"AES", "ECIES", "Argon2", "KEK", "DEK", "Fernet"
"encrypt_", "decrypt_", "derive_", "generate_"
"BinaryField", "key_salt", "encrypted_"
```

## TESTING.md Triggers

| Code Path | Trigger Pattern | Update Action |
|-----------|-----------------|---------------|
| `conftest.py` (root) | `@pytest.fixture` | Add to fixture table |
| `apps/*/tests/conftest.py` | `@pytest.fixture` | Add to app-specific section |
| `pyproject.toml` | `[tool.pytest]` changes | Update config section |
| Any test file | New marker usage | Document marker if new |

### Fixture Detection

```python
# Look for new fixtures
@pytest.fixture
def fixture_name(...) -> ReturnType:
    """Docstring describing purpose."""
```

Extract:
- Fixture name
- Return type annotation
- Docstring (first line)
- Scope (if not default)
- Dependencies (other fixtures used)

## URLS.md Triggers

| Code Path | Trigger Pattern | Update Action |
|-----------|-----------------|---------------|
| `apps/*/urls.py` | `path(...)` | Add URL to table |
| `apps/*/views.py` | `LoginRequiredMixin` | Document auth requirement |
| `apps/*/views_settings.py` | Any view class | Add settings URL |
| `project-dev/urls.py` | Root URL changes | Update root patterns |

### URL Pattern Detection

```python
# Look for new URL patterns
path("settings/feature/", ViewName.as_view(), name="feature"),
```

Extract:
- URL path
- URL name
- View class/function
- Authentication requirement

## docs/security/ Triggers

### SECURITY-CHECKLIST.md

| Code Path | Trigger Pattern | Update Action |
|-----------|-----------------|---------------|
| Any model | New PII field | Add PII classification |
| `apps/encryption/` | Algorithm changes | Update encryption requirements |
| `apps/security/` | Auth changes | Update authentication section |

### SECURITY-MODULE-ARCHITECTURE.md

| Code Path | Trigger Pattern | Update Action |
|-----------|-----------------|---------------|
| `apps/encryption/__init__.py` | Export changes | Update public API |
| `apps/security/__init__.py` | Export changes | Update public API |
| New module in `apps/encryption/` | New file | Update module structure |
| New module in `apps/security/` | New file | Update module structure |

## Detection Commands

### Find Recent Changes

```bash
# Files changed in current branch
git diff --name-only main...HEAD

# Files changed but not committed
git diff --name-only

# Files in last commit
git diff --name-only HEAD~1
```

### Pattern Matching

```bash
# New fixtures added
grep -n "@pytest.fixture" conftest.py

# New URL patterns
grep -n "path(" apps/*/urls.py

# New encryption functions
grep -n "def encrypt\|def decrypt\|def derive" apps/encryption/**/*.py
```

## Priority Order

When multiple docs need updates, process in this order:

1. **GLOSSARY.md** — Core terminology must be accurate first
2. **SECURITY-MODULE-ARCHITECTURE.md** — Architecture must reflect reality
3. **SECURITY-CHECKLIST.md** — Security requirements must be current
4. **URLS.md** — URL documentation
5. **TESTING.md** — Test infrastructure last (usually less critical)
