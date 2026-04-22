---
mode: bridged
research_question: "Should we switch JWT library from PyJWT to python-jose?"
---

## Research Task: Evaluate JWT Library Migration

### Internal Investigation
Our current JWT implementation in `src/auth/tokens.py` uses PyJWT:

{codebase:dirty-flask-app/src/auth/tokens.py}

**Finding:** The current implementation accepts `algorithms=["HS256", "RS256", "none"]`, which is an algorithm confusion vulnerability. The fix requires enforcing a single algorithm.

### External Search Results (simulated)

#### PyJWT (current)
- Latest version: 2.8.0 (Dec 2023)
- Stars: 4.9k | Monthly downloads: 45M
- Supports: HS256, RS256, ES256, EdDSA
- Algorithm enforcement: `algorithms` parameter in `decode()` — explicit opt-in required
- Known CVE: CVE-2022-29217 (algorithm confusion when `algorithms` not specified)
- Maintenance: Active, 2-3 releases/year

#### python-jose
- Latest version: 3.3.0 (Jan 2022)
- Stars: 1.5k | Monthly downloads: 12M
- Supports: HS256, RS256, ES256, PS256
- Algorithm enforcement: `algorithms` required by default since 3.0
- Known issues: No CVEs but fewer maintainers, slower release cadence
- Maintenance: Sporadic, last release 2+ years ago

#### Comparison
| Feature | PyJWT | python-jose |
|---------|-------|-------------|
| Algorithm enforcement | Opt-in | Required by default |
| Release cadence | Active | Stale |
| Community size | Larger | Smaller |
| JWK support | Via PyJWK | Built-in |
