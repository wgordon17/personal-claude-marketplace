# Fixture Quick Reference

Complete fixture inventory for Project test suite.

## Root Fixtures (conftest.py)

### User Fixtures

| Fixture | Returns | Scope | Description |
|---------|---------|-------|-------------|
| `user_with_trial` | `User` | function | User with active trial, encrypted keys |
| `user_with_paid_subscription` | `User` | function | User with paid monthly subscription |
| `user_with_keys` | `User` | function | User with encrypted keys (no subscription) |
| `user_with_encryption` | `tuple` | function | `(user, private_key, recovery_codes)` |

### Encryption Fixtures

| Fixture | Returns | Scope | Description |
|---------|---------|-------|-------------|
| `password` | `str` | function | `"SecurePassword123!"` |
| `key_salt` | `bytes` | function | 16-byte random salt |
| `keypair` | `dict` | function | `{"public_key": bytes, "private_key": bytes}` |
| `kek` | `bytes` | function | 32-byte Key Encryption Key |
| `dek_and_nonce` | `dict` | function | `{"dek": bytes, "nonce": bytes}` |
| `recovery_codes` | `list[str]` | function | 3 recovery codes (reduced for speed) |

### Client Fixtures

| Fixture | Returns | Scope | Description |
|---------|---------|-------|-------------|
| `client_with_public_ip` | `Client` | function | REMOTE_ADDR="8.8.8.1" |
| `authenticated_dashboard_client` | `Client` | function | Logged in with SessionGrant |
| `authenticated_client_with_encryption` | `Client` | function | With user_with_encryption |

### Banking Fixtures

| Fixture | Returns | Scope | Depends On |
|---------|---------|-------|------------|
| `financial_institution` | `FinancialInstitution` | function | `user_with_trial` |
| `bank_account` | `Account` | function | `financial_institution` |
| `transaction_batch` | `TransactionBatch` | function | `user_with_trial`, `dek_and_nonce` |
| `transaction_with_notes` | `int` | function | `user_with_trial`, `dek_and_nonce` |

### Mock Fixtures

| Fixture | Returns | Scope | Description |
|---------|---------|-------|-------------|
| `sample_transactions` | `list[dict]` | function | 3 sample transaction dicts |
| `mock_teller_accounts` | `list[dict]` | function | Mock Teller API accounts |
| `mock_teller_transactions` | `list[dict]` | function | Mock Teller API transactions |
| `mock_httpx_client` | `MagicMock` | function | Mocked httpx.Client |

### Playwright Fixtures

| Fixture | Returns | Scope | Description |
|---------|---------|-------|-------------|
| `browser_type_launch_args` | `dict` | session | CI: Chrome, Local: Chromium |
| `frozen_time` | (yields) | function | Time frozen to 2026-01-05 00:00 UTC |
| `playwright_test_user` | `User` | function | Test user for Playwright |
| `playwright_test_data` | `dict` | function | Deterministic transaction data |
| `authenticated_page` | `Page` | function | Logged-in Playwright page |

### Auto-use Fixtures

| Fixture | Scope | Purpose |
|---------|-------|---------|
| `_log_database_config` | session | Logs DB config, warns if SQLite |
| `clear_ratelimit_state` | function | Clears rate limits between tests |
| `debug_playwright_in_ci` | function | Captures console/network for failures |
| `_sync_db_for_playwright` | function | Fixes DB teardown with async Playwright |

## App-Specific Fixtures

### apps/core/tests/conftest.py

Adds CLI options:
- `--storyboard-generate` — Generate screenshots
- `--storyboard-check` — Validate screenshots

### project-dev/loaders/tests/conftest.py

| Fixture | Purpose |
|---------|---------|
| `reset_loader_state` | Resets 1Password loader state |

### tests/functional/conftest.py

| Fixture | Purpose |
|---------|---------|
| `test_vault_name` | Returns "testing" vault name |
| `project_root` | Returns project root path |

## Decision Tree

```
What are you testing?

├── Authentication/Views?
│   ├── Unauthenticated → client_with_public_ip
│   └── Authenticated → authenticated_dashboard_client
│
├── Encryption/Crypto?
│   ├── Need private key → user_with_encryption (tuple)
│   ├── Just keys exist → user_with_keys
│   └── Primitives only → keypair, kek, dek_and_nonce
│
├── Subscription tiers?
│   ├── Trial → user_with_trial
│   └── Paid → user_with_paid_subscription
│
├── Banking models?
│   └── Use: financial_institution → bank_account → transaction_batch
│
└── Browser/E2E?
    └── authenticated_page
```

## Common Combinations

```python
# Dashboard view test
def test_dashboard(authenticated_dashboard_client):
    response = authenticated_dashboard_client.get("/dashboard/")

# Encryption roundtrip
def test_encrypt(user_with_encryption):
    user, private_key, recovery_codes = user_with_encryption

# Transaction sync
def test_sync(transaction_batch, mock_httpx_client):
    pass

# Browser test with time freeze
def test_ui(authenticated_page, frozen_time):
    pass
```
