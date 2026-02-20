# Global Commands Plugin

Session management, project review, commit validation, and LSP status commands.

## Commands (6)

| Command | Description | When to Use |
|---------|-------------|-------------|
| `/global-commands:session-start` | Load project context or initialize new project | At session start |
| `/global-commands:session-end` | Sync project memory before ending | Before ending session |
| `/global-commands:review-project` | Comprehensive TODO validation | Before PRs |
| `/global-commands:review-commits` | AI-assisted commit review for PR readiness | Before creating PR |
| `/global-commands:contributing` | Generate/update CONTRIBUTING.md | Project setup |
| `/global-commands:lsp-status` | Check LSP server status | Debugging LSP issues |

## Usage

Invoke commands via slash syntax:

```
/global-commands:session-start
/global-commands:session-end
/global-commands:review-commits
/global-commands:lsp-status
```

## Workflow Integration

### Session Lifecycle

**Start of session:**
```
/global-commands:session-start
```

Loads:
- `hack/NEXT.md` - Immediate focus
- `hack/PROJECT.md` - Project knowledge
- `hack/WORK_ETHIC.md` - Agent behavior rules (if exists)

**End of session:**
```
/global-commands:session-end
```

Updates:
- `hack/TODO.md` - Marks completed items with date
- `hack/PROJECT.md` - Adds decisions and gotchas
- `hack/SESSIONS.md` - Appends 3-5 bullet summary

### Pre-PR Workflow

```
/global-commands:review-commits   # Review commit history
/global-commands:review-project   # Validate TODO items
# Create PR when ready
```

## Installation

```bash
claude plugin install global-commands@personal-claude-marketplace
```

## Author

wgordon17 - January 2026
