---
name: docs-sync
description: PROACTIVE skill - Triggers after code modifications. Keeps GLOSSARY.md, TESTING.md, URLS.md, and docs/security/* in sync with code changes. Auto-updates with notification.
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash]
---

# Documentation Synchronization

## Purpose

Keep project documentation in sync with code changes. This skill detects when code modifications affect documented content and automatically updates the relevant documentation files, notifying the user of changes made.

## Documentation Files Covered

| File | Location | Tracks |
|------|----------|--------|
| **GLOSSARY.md** | `docs/development/` | Cryptographic terms, algorithms, model fields, encryption patterns |
| **TESTING.md** | `docs/development/` | Test fixtures, pytest markers, test patterns, debugging commands |
| **URLS.md** | `docs/development/` | URL patterns, view mappings, auth flow documentation |
| **SECURITY-CHECKLIST.md** | `docs/security/` | PII classification, encryption requirements |
| **SECURITY-MODULE-ARCHITECTURE.md** | `docs/security/` | Module structure, import boundaries, service layers |

## When to Trigger

This skill activates PROACTIVELY after:
- Editing files in `apps/encryption/` → Check GLOSSARY.md
- Editing files in `apps/security/` → Check GLOSSARY.md, SECURITY-*.md
- Adding/modifying test fixtures in `conftest.py` → Check TESTING.md
- Adding/modifying URL patterns in `urls.py` → Check URLS.md
- Adding/modifying views with `LoginRequiredMixin` → Check URLS.md
- Changes to models with encryption fields → Check GLOSSARY.md

## Workflow

### Step 1: Detect Code Changes

```bash
git diff --name-only HEAD~1  # Or git diff --staged for uncommitted
```

### Step 2: Map Changes to Documentation

Use the reference mapping in `references/doc-mapping.md` to identify which docs need review.

### Step 3: Analyze Documentation Impact

For each affected doc file:
1. Read the current documentation
2. Compare with code changes
3. Identify gaps or outdated content

### Step 4: Auto-Update with Notification

- Make documentation updates directly
- Notify user of what was changed and why
- Format: "Updated GLOSSARY.md: Added entry for [new term]"

## Documentation Challenge Protocol

When existing documentation conflicts with observed code behavior:

1. **Identify the discrepancy** — Quote both doc and code
2. **Determine truth source** — Code is usually correct for implementation details
3. **Auto-update if clear** — Update docs to match code behavior
4. **Notify user** — Explain what was changed and why
5. **Flag for review** — If ambiguous, ask user: "Update docs OR update code?"

## Update Patterns

### Adding New Cryptographic Terms (GLOSSARY.md)

When new encryption-related code is added:
```markdown
### [Term Name]

**What:** Brief description of what this is.

**Format:** Data format (e.g., "32 bytes", "base64-encoded")

**Storage:** Where it's stored (e.g., `User.field_name`)

**Code locations:**
- Implementation: `apps/encryption/module.py:function_name()`

**Security notes:**
- Key security considerations
```

### Adding New Test Fixtures (TESTING.md)

When new fixtures are added to `conftest.py`:
```markdown
| `fixture_name` | `ReturnType` | Brief description |
```

### Adding New URL Patterns (URLS.md)

When new views or URL patterns are added:
```markdown
| `/dashboard/path/` | `url_name` | `app.views.ViewName` |
```

## Integration

### Required Skills
- None (standalone skill)

### Called By
- `project-dev:orchestrator` — After feature implementation
- `project-dev:feature-writer` — After code generation
- `project-dev:refactor` — After refactoring

### Invokes
- None (makes direct file edits)

## Example Output

```
📄 Documentation Sync Complete

Updated files:
- docs/development/GLOSSARY.md
  + Added entry for "SessionMetadata" (new model field)
  + Updated "AES-256-GCM" section with new nonce handling

- docs/development/TESTING.md
  + Added fixture: `authenticated_page` (Playwright authenticated page)
  + Updated fixture table with new return types

No changes needed:
- docs/development/URLS.md (no URL changes detected)
- docs/security/SECURITY-CHECKLIST.md (no PII changes)
```

## Verification

After running, verify:
1. All modified doc files pass markdown lint
2. Cross-references in docs still resolve
3. Code examples in docs still match actual code
