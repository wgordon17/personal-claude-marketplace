---
name: security-review
description: Comprehensive security audit following SECURITY-CHECKLIST.md and OWASP guidelines. Use before PRs or after security-sensitive changes.
allowed-tools: [Read, Grep, Glob, LSP, WebSearch]
---

# Security Review

## Purpose

Perform comprehensive security audits for Project code, validating against the project's zero-knowledge encryption architecture and OWASP guidelines.

## When to Use

- **Before PRs** — Mandatory for changes touching `apps/encryption/` or `apps/security/`
- **After security-sensitive changes** — Any auth, crypto, or PII-handling code
- **On demand** — When user requests `/security-review`

## Security Architecture Context

Project uses a **zero-knowledge encryption architecture**:
- Server can encrypt data but CANNOT decrypt user data
- Only users with private keys (from password or recovery codes) can decrypt
- Database breach exposes only encrypted blobs

**Critical constraint:** Any code that could expose plaintext private keys or bypass encryption is a CRITICAL security issue.

## Review Workflow

### Step 1: Identify Changed Files

```bash
git diff --name-only main...HEAD | grep -E "(encryption|security|accounts|views)"
```

### Step 2: PII Classification Check

Verify all PII fields are correctly classified per SECURITY-CHECKLIST.md:

| Classification | Examples | Requirement |
|---------------|----------|-------------|
| **CRITICAL** | Emails, tokens, private keys, recovery codes | MUST encrypt |
| **HIGH** | Names, account balances, transaction details | Encrypt OR document in privacy policy |
| **MEDIUM** | Timestamps, session metadata | Document retention policy |
| **LOW** | Aggregated stats, public preferences | Standard handling |

### Step 3: Encryption Pattern Validation

Check against GLOSSARY.md patterns:

- [ ] Private keys NEVER stored in plaintext
- [ ] Nonces are UNIQUE per encryption (never reused)
- [ ] DEKs are NOT reused across transaction batches
- [ ] KEKs are derived on-demand, never stored
- [ ] AES-256-GCM used with proper tag validation
- [ ] ECIES used for asymmetric operations

### Step 4: URL Security Enforcement

Verify URL security per URLS.md:

- [ ] All authenticated routes use `LoginRequiredMixin`
- [ ] Routes registered in `apps/security/url_enforcement.py`
- [ ] No accidental exposure of protected endpoints

### Step 5: OWASP Top 10 Scan

| Vulnerability | Check |
|--------------|-------|
| Injection | Raw SQL queries, unsanitized user input |
| Broken Auth | Session handling, token validation |
| Sensitive Data Exposure | Plaintext secrets, logging PII |
| XXE | XML parsing (usually N/A for Django) |
| Broken Access Control | Missing auth checks, IDOR |
| Security Misconfiguration | Debug mode, default secrets |
| XSS | Template escaping, safe filters |
| Insecure Deserialization | Pickle usage, untrusted JSON |
| Known Vulnerabilities | Outdated dependencies |
| Insufficient Logging | Missing audit trails |

### Step 6: Generate Findings Report

## Required Skills

- `/lsp-navigation` — Trace code paths, find references
- `/uv-python` — Run security scanners if needed

## Integration

### Called By
- `project-dev:orchestrator` — Before PR creation
- `project-dev:code-quality` — As part of quality gate
- `project-dev:pr-reviewer` — During PR review

### Invokes
- None (read-only analysis)

## Findings Report Format

```markdown
# Security Review Report

**Branch:** feature/new-feature
**Files Reviewed:** 12
**Issues Found:** 3

## CRITICAL Issues (0)
None

## HIGH Issues (1)

### [HIGH-001] Missing IP validation in session handler
- **File:** apps/security/services/session.py:145
- **Issue:** get_private_key() doesn't validate IP before returning key
- **Risk:** Session hijacking if cookie stolen
- **Remediation:** Add IP validation check before unwrapping private key

## MEDIUM Issues (2)

### [MED-001] Broad exception handling
- **File:** apps/security/services/session.py:89
- **Issue:** Catches all Exception types, may mask errors
- **Risk:** Silent failures, debugging difficulty
- **Remediation:** Catch specific exception types

### [MED-002] Missing docstring returns section
- **File:** apps/security/services/token.py:45
- **Issue:** Service method lacks return documentation
- **Risk:** Maintenance burden
- **Remediation:** Add Returns section to docstring

## LOW Issues (0)
None

## Recommendations
1. Address HIGH issues before merge
2. Schedule MEDIUM issues for follow-up
```

## Quick Reference: Security Anti-Patterns

### NEVER Do

```python
# ❌ Storing private key in session
request.session['private_key'] = private_key

# ❌ Logging sensitive data
logger.debug(f"User key: {user.encrypted_private_key}")

# ❌ Broad exception handling hiding crypto errors
try:
    decrypt(data)
except Exception:
    pass

# ❌ Reusing nonces
nonce = b'\x00' * 12  # Static nonce

# ❌ Hardcoded secrets
FERNET_KEY = "hardcoded-key-here"
```

### ALWAYS Do

```python
# ✅ Use SessionGrant for private key access
private_key = get_private_key_from_session_grant(request)

# ✅ Log without sensitive data
logger.info(f"User {user.id} logged in")

# ✅ Specific exception handling
try:
    decrypt(data)
except InvalidTag:
    raise DecryptionError("Tampered data")

# ✅ Random nonces
nonce = os.urandom(12)

# ✅ Environment-based secrets
FERNET_KEY = settings.FERNET_KDF_SALT
```

## Severity Definitions

| Severity | Definition | SLA |
|----------|------------|-----|
| **CRITICAL** | Data breach possible, must block merge | Immediate |
| **HIGH** | Security weakness, should block merge | Before merge |
| **MEDIUM** | Best practice violation, address soon | Next sprint |
| **LOW** | Minor improvement, nice to have | Backlog |
