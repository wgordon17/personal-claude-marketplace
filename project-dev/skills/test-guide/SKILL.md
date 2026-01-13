---
name: test-guide
description: PROACTIVE skill - Activates when writing tests or debugging test failures. Quick reference for test patterns, fixtures, and commands from TESTING.md.
allowed-tools: [Read, Grep, Glob]
---

# Testing Reference Guide

## Purpose

Provide quick access to test patterns, fixtures, and debugging commands specific to Project's testing infrastructure.

## When to Trigger

This skill activates PROACTIVELY when:
- Writing new test files (`test_*.py`)
- Debugging test failures
- Adding test fixtures to `conftest.py`
- User asks about testing patterns

## Required Skills

- `/test-runner` — For efficient test execution patterns
- `/uv-python` — For pytest commands

## Quick Command Reference

```bash
# Run all tests
uv run pytest

# Run single test file
uv run pytest apps/accounts/tests/test_views.py

# Run single test by name
uv run pytest -k "test_login"

# Run last failed tests
uv run pytest --lf

# Debug with full output
uv run pytest -x -vv -s --tb=long apps/path/test.py::test_name

# Playwright debugging
PWDEBUG=1 uv run pytest -s apps/transactions/tests/test_frontend.py
```

## Fixture Selection Guide

### Which User Fixture?

| Scenario | Fixture | Returns |
|----------|---------|---------|
| Dashboard/view tests | `user_with_trial` | `User` with active trial |
| Subscription tier tests | `user_with_paid_subscription` | `User` with paid subscription |
| Encryption/decryption tests | `user_with_encryption` | `(User, private_key, recovery_codes)` |
| Simple key existence tests | `user_with_keys` | `User` with encrypted keys |

### Which Client Fixture?

| Scenario | Fixture | Returns |
|----------|---------|---------|
| Unauthenticated requests | `client_with_public_ip` | Django `Client` with IP |
| Dashboard access | `authenticated_dashboard_client` | Logged-in `Client` |
| Tests needing private_key | `authenticated_client_with_encryption` | `Client` with encryption |
| Browser tests | `authenticated_page` | Playwright `Page` |

### Encryption Fixtures

| Fixture | Returns | Use For |
|---------|---------|---------|
| `password` | `"SecurePassword123!"` | Authentication tests |
| `key_salt` | 16-byte `bytes` | Key derivation tests |
| `keypair` | `{"public_key": bytes, "private_key": bytes}` | Encryption tests |
| `kek` | 32-byte `bytes` | Key encryption tests |
| `dek_and_nonce` | `{"dek": bytes, "nonce": bytes}` | Transaction encryption |
| `recovery_codes` | `list[str]` (3 codes) | Recovery flow tests |

### Banking Fixtures

| Fixture | Returns | Depends On |
|---------|---------|------------|
| `financial_institution` | `FinancialInstitution` | `user_with_trial` |
| `bank_account` | `Account` | `financial_institution` |
| `transaction_batch` | `TransactionBatch` | `user_with_trial`, `dek_and_nonce` |

## Required Markers

```python
@pytest.mark.django_db
def test_user_creation(user_with_trial):
    """All tests touching models need this."""
    pass

@pytest.mark.django_db(transaction=True)
def test_atomic_operation():
    """Tests requiring transaction rollback."""
    pass

@pytest.mark.slow
def test_expensive_computation():
    """Skip with: uv run pytest -m "not slow" """
    pass

@pytest.mark.xdist_group(name="argon2_timing")
def test_timing_sensitive():
    """Forces sequential execution."""
    pass
```

## Test Naming Conventions

| Element | Pattern | Example |
|---------|---------|---------|
| Files | `test_<feature>.py` | `test_encryption.py` |
| Classes | `Test<Feature>` | `TestEncryption` |
| Functions | `test_<scenario>` | `test_encrypt_decrypt_roundtrip` |

## Common Patterns

### Testing Authenticated Views

```python
@pytest.mark.django_db
def test_dashboard_requires_auth(client_with_public_ip):
    response = client_with_public_ip.get("/dashboard/")
    assert response.status_code == 302
    assert "/accounts/login/" in response.url

@pytest.mark.django_db
def test_dashboard_accessible_when_authenticated(authenticated_dashboard_client):
    response = authenticated_dashboard_client.get("/dashboard/")
    assert response.status_code == 200
```

### Testing Encryption Roundtrip

```python
@pytest.mark.django_db
def test_encrypt_decrypt_roundtrip(keypair, dek_and_nonce):
    from apps.encryption.primitives.aes import encrypt_data, decrypt_data

    plaintext = b"sensitive data"
    encrypted = encrypt_data(plaintext, dek_and_nonce["dek"], dek_and_nonce["nonce"])
    decrypted = decrypt_data(encrypted, dek_and_nonce["dek"], dek_and_nonce["nonce"])

    assert decrypted == plaintext
```

### Testing with Time Freezing

```python
def test_session_expiry(authenticated_page, frozen_time):
    # Time is frozen to 2026-01-05 00:00 UTC
    # Date-based UI elements will be consistent
    authenticated_page.goto("/dashboard/")
```

## Troubleshooting

### SessionGrant Validation Failures

```python
# Use client_with_public_ip fixture, not bare Client()
def test_auth(client_with_public_ip):
    # REMOTE_ADDR is set to 8.8.8.1 (globally routable)
```

### Rate Limiting Interfering

```python
from django_smart_ratelimit import get_backend
get_backend().clear_all()  # Already done by clear_ratelimit_state fixture
```

### Parallel Test Failures

```bash
# Run sequentially to isolate
uv run pytest -n=0

# Or group timing-sensitive tests
@pytest.mark.xdist_group(name="timing_sensitive")
```

## Test Constants

Located in `apps/testing/constants.py`:

```python
TEST_PUBLIC_IP = "8.8.8.1"  # Globally routable for SessionGrant
TEST_PASSWORD = "SecurePassword123!"
DEFAULT_VIEWPORT = {"width": 1280, "height": 1024}
EMAIL_VIEWPORT = {"width": 600, "height": 800}
```

## Integration

### Called By
- `project-dev:test-writer` — When generating tests
- `project-dev:bug-fixer` — When adding regression tests

### References
- Full documentation: `docs/development/TESTING.md`
- Fixture quick reference: `references/fixture-quick-ref.md`
