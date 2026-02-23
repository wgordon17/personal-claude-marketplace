# CMUX Integration Plugin

Bridges Claude Code hook events to CMUX terminal's CLI API for rich notifications, sidebar status pills, and activity logging. Replaces the basic cmux-notify.sh script from CMUX docs with comprehensive coverage of all relevant hook events.

## Event → CMUX Mapping

| Hook Event | Matcher | CMUX Action | Content |
|---|---|---|---|
| Notification | (all types) | `cmux notify` | title from notification_type, body from message field |
| SubagentStop | (all) | `cmux notify` + `cmux log` | "Agent [type] finished: [first sentence]" |
| Stop | (all) | `cmux set-status` + `cmux notify` | status pill "complete", notify with summary |
| SessionStart | (all) | `cmux set-status` + `cmux log` | status pill "session active", log entry |
| SessionEnd | (all) | `cmux clear-status` + `cmux log` | clear all status, log "session ended" |
| PreToolUse | Bash, Task | `cmux set-status` | activity pill "Running: [cmd]" or "Spawning: [agent]" |
| PostToolUse | Task | `cmux log` + `cmux clear-status` | log task result, clear activity |

## Prerequisites

- CMUX installed (see [CMUX Getting Started](https://www.cmux.dev/docs))
- `cmux` CLI symlink in PATH (per CMUX documentation)

The plugin degrades gracefully — if CMUX is not running or the CLI is not available, all hooks exit silently without impacting Claude Code operation.

## Installation

```bash
claude plugin marketplace add cmux-integration@personal-claude-marketplace
```

## Migration from Manual cmux-notify.sh

If you previously followed CMUX's notification docs and created `~/.claude/hooks/cmux-notify.sh` with corresponding `settings.json` entries, remove the script and delete the `Stop`/`PostToolUse` hook entries from `~/.claude/settings.json` to avoid duplicate notifications:

```bash
rm ~/.claude/hooks/cmux-notify.sh
# Then edit ~/.claude/settings.json to remove the cmux-notify.sh hook entries
```

The plugin auto-detects the legacy script at session start and warns if it's still present.

## Graceful Degradation

The plugin checks `cmux ping` on each hook invocation. If CMUX is not running or the CLI is not in PATH:

- All hooks exit with code 0 (success)
- No errors are printed
- Claude Code operation is unaffected
- No external communication occurs — all interaction is local via the `cmux` CLI

## Privacy Note

Notifications may include the first sentence of Claude's last response (from `SubagentStop` and `Stop` events). This content is already visible in your terminal but may appear in:

- Desktop notification history
- CMUX's sidebar log

No data is sent to external services — all communication is local via the cmux CLI.

## Author

wgordon17 - February 2026
