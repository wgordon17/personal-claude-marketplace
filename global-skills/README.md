# Global Skills Plugin

Essential skills for code quality, git operations, LSP navigation, test execution, Python tooling, and bug investigation.

## ⚠️ Development Warning

**DO NOT edit files in `~/.claude/plugins/`!** Always edit the source repository. See marketplace README for details.

## Skills (8)

| Skill | Description | Type |
|-------|-------------|------|
| `/bug-investigation` | Interactive bug hunting with background agents | PROACTIVE |
| `/file-audit` | Deep code quality audit system | Manual |
| `/git-history` | Git history manipulation (git-branchless) | Manual |
| `/git-hooks-install` | Install defense-in-depth git hooks | Manual |
| `/git-hooks-uninstall` | Remove git hooks from project | Manual |
| `/lsp-navigation` | Semantic code navigation | PROACTIVE |
| `/test-runner` | Efficient test execution patterns | Manual |
| `/uv-python` | Python tooling enforcement (uv over pip) | PROACTIVE |

## Usage

Skills are invoked automatically (PROACTIVE) or manually via slash command:

```
/bug-investigation
/file-audit
/git-history
/lsp-navigation
/test-runner
/uv-python
```

## Proactive Skills

**bug-investigation** - Automatically activates when:
- User wants to report bugs/issues for investigation
- User describes a bug and `hack/BUGS.md` exists
- User asks to "hunt bugs", "audit the UI", or "investigate issues"

**lsp-navigation** - Automatically activates when:
- Navigating code
- Understanding symbol definitions
- Finding references
- Exploring call hierarchies

**uv-python** - Automatically activates when:
- Creating .py files
- Writing bash scripts with Python
- Running terminal commands
- ANY mention of python/pip/python3

## Installation

```bash
claude plugin install global-skills@private-claude-marketplace
```

## Dependencies

- **git-branchless**: `brew install git-branchless` (for git-history skill)
- **uv**: `brew install uv` (for uv-python skill)
- **pre-commit**: Installed via uv in Python projects (for git-hooks skills)

## Author

wgordon - January 2026
