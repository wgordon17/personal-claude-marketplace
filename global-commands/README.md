# Global Commands Plugin

Session management, project review, commit validation, and LSP status commands.

## Commands (6)

| Command | Description | When to Use |
|---------|-------------|-------------|
| `/session-start` | Load project context or initialize new project | At session start |
| `/session-end` | Sync project memory before ending | Before ending session |
| `/review-project` | Comprehensive TODO validation | Before PRs |
| `/review-commits` | AI-assisted commit review for PR readiness | Before creating PR |
| `/contributing` | Generate/update CONTRIBUTING.md | Project setup |
| `/lsp-status` | Check LSP server status | Debugging LSP issues |

## Usage

Invoke commands via slash syntax:

```
/session-start
/session-end
/review-commits
/lsp-status
```

## Workflow Integration

### Session Lifecycle

**Start of session:**
```
/session-start
```

Loads:
- `hack/NEXT.md` - Immediate focus
- `hack/PROJECT.md` - Project knowledge
- `hack/WORK_ETHIC.md` - Agent behavior rules (if exists)

**End of session:**
```
/session-end
```

Updates:
- `hack/TODO.md` - Marks completed items with date
- `hack/PROJECT.md` - Adds decisions and gotchas
- `hack/SESSIONS.md` - Appends 3-5 bullet summary

### Pre-PR Workflow

```
/review-commits   # Review commit history
/review-project   # Validate TODO items
# Create PR when ready
```

## Installation

```bash
claude plugin install global-commands@private-claude-marketplace
```

## Author

wgordon17 - January 2026
