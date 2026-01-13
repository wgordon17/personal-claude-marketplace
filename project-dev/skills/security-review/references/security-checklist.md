# Security Review Quick Reference

Condensed checklist from project security documentation.

## PII Classification

### CRITICAL — Must Encrypt

| Field | Model | Encryption Method |
|-------|-------|-------------------|
| `encrypted_private_key` | User | AES-256-GCM with KEK |
| `recovery_code_hashes` | User | Argon2id hash |
| `encrypted_private_key_recovery` | User | AES-256-GCM per recovery code |
| `access_token` | FinancialInstitution | Fernet |
| `wrapped_private_key` | SessionGrant | ECIES |
| `encrypted_metadata` | SessionGrant | ECIES |
| `encrypted_dek` | DEKGrant | ECIES |
| `encrypted_data` | Transaction | AES-256-GCM with DEK |

### HIGH — Encrypt or Document

| Field | Model | Current Handling |
|-------|-------|------------------|
| `email` | User | Django allauth handling |
| Account balances | Transaction.encrypted_data | Encrypted in blob |
| Transaction descriptions | Transaction.encrypted_data | Encrypted in blob |

### MEDIUM — Document Retention

| Field | Model | Retention Policy |
|-------|-------|------------------|
| `created_at` | All models | Indefinite |
| `last_activity` | SessionGrant | Session lifetime |
| `expires_at` | SessionGrant | Session lifetime |

## Encryption Requirements

### AES-256-GCM

- Key length: 32 bytes
- Nonce length: 12 bytes (NIST SP 800-38D)
- Tag length: 16 bytes
- **CRITICAL:** Nonce MUST be unique per encryption

### Argon2id (OWASP 2025)

| Parameter | Production | Testing |
|-----------|------------|---------|
| memory_cost | 65536 KiB | 1024 KiB |
| time_cost | 3 | 1 |
| parallelism | 4 | 1 |

### ECIES (secp256k1)

- Public key: 33 bytes (compressed)
- Private key: 32 bytes
- Uses ephemeral key per encryption (forward secrecy)

## Authentication Checklist

- [ ] View uses `LoginRequiredMixin` or `@login_required`
- [ ] URL registered in `apps/security/url_enforcement.py`
- [ ] SessionGrant validated for authenticated operations
- [ ] IP binding enforced for session access
- [ ] User-Agent validated for session access

## Session Security

- [ ] Session private key in HttpOnly cookie
- [ ] Cookie has Secure flag (HTTPS only)
- [ ] Cookie has SameSite=Lax
- [ ] Session expires correctly (7 days default)
- [ ] Idle timeout enforced (1 hour production)

## Code Review Checklist

### Models

- [ ] No plaintext storage of secrets
- [ ] BinaryField for encrypted data
- [ ] Proper field documentation

### Views

- [ ] Authentication required
- [ ] Authorization checked (user owns resource)
- [ ] Input validation
- [ ] Output escaping

### Services

- [ ] Specific exception handling
- [ ] No logging of sensitive data
- [ ] Proper cleanup of sensitive data

### Tests

- [ ] Test with valid encryption
- [ ] Test with invalid/tampered data
- [ ] Test authorization boundaries

## OWASP Top 10 Quick Check

| # | Vulnerability | Django/Project Check |
|---|--------------|------------------------|
| 1 | Injection | No raw SQL, ORM only |
| 2 | Broken Auth | SessionGrant + IP binding |
| 3 | Sensitive Data | Zero-knowledge encryption |
| 4 | XXE | N/A (no XML parsing) |
| 5 | Broken Access | LoginRequiredMixin everywhere |
| 6 | Misconfig | DEBUG=False, secrets from env |
| 7 | XSS | Django auto-escaping |
| 8 | Insecure Deserial | No pickle, JSON only |
| 9 | Known Vulns | pip-audit, pnpm audit |
| 10 | Logging | No PII in logs |

## Red Flags

Grep for these patterns:

```bash
# Potential secrets in code
grep -r "password\s*=" apps/
grep -r "secret\s*=" apps/
grep -r "key\s*=" apps/

# Debug/development code
grep -r "DEBUG" apps/
grep -r "print(" apps/
grep -r "pdb" apps/

# Dangerous patterns
grep -r "eval(" apps/
grep -r "exec(" apps/
grep -r "pickle" apps/
grep -r "subprocess" apps/
```

## Files Requiring Extra Scrutiny

| File | Why |
|------|-----|
| `apps/encryption/primitives/aes.py` | Core encryption |
| `apps/encryption/keys/derivation.py` | Key derivation |
| `apps/security/services/session.py` | Session handling |
| `apps/security/services/recovery.py` | Account recovery |
| `apps/accounts/views.py` | Login/signup flows |
| `apps/banking/tasks.py` | Background sync with tokens |
