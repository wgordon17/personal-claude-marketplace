---
name: trust
description: Manage trusted rules for dev-guard ask prompts
arguments:
  - name: action
    description: "add, remove, or list"
    required: true
  - name: rule_name
    description: "Rule name shown in the ask prompt (e.g. stash-drop, config-global-write)"
    required: false
---

# Dev-Guard Trust Management

Manage trusted rules so that repeated ask prompts for known-safe operations are automatically approved.

## Your Task

Run the tool-selection-guard script with the `--trust` flag to manage trust rules.

### Trust a rule (always)

```bash
uv run ${CLAUDE_PLUGIN_ROOT}/hooks/tool-selection-guard.py --trust add <rule_name> --scope always
```

### Trust a rule (current session only)

```bash
uv run ${CLAUDE_PLUGIN_ROOT}/hooks/tool-selection-guard.py --trust add <rule_name> --scope session
```

### Trust a rule with a specific session ID

```bash
uv run ${CLAUDE_PLUGIN_ROOT}/hooks/tool-selection-guard.py --trust add <rule_name> --scope session --session-id <session_id>
```

### Trust a rule with a match pattern

```bash
uv run ${CLAUDE_PLUGIN_ROOT}/hooks/tool-selection-guard.py --trust add <rule_name> --match <pattern> --scope always
```

### Remove trust for a rule

```bash
uv run ${CLAUDE_PLUGIN_ROOT}/hooks/tool-selection-guard.py --trust remove <rule_name>
```

### Remove trust for a specific match pattern

```bash
uv run ${CLAUDE_PLUGIN_ROOT}/hooks/tool-selection-guard.py --trust remove <rule_name> --match <pattern>
```

### List all trusted rules

```bash
uv run ${CLAUDE_PLUGIN_ROOT}/hooks/tool-selection-guard.py --trust list
```

## How Trust Works

- When a rule is trusted, future `ask` prompts for that rule auto-approve without prompting.
- The decision shows `[trusted]` in the reason, and the event is logged as `action="trusted"`.
- Only `ask`-type rules can be trusted. Block rules are safety-critical and cannot be bypassed via trust.

## Caveats

- `--match <pattern>` is a **case-insensitive substring** match against the full command string, not a regex.
- `--session` requires a prior guard invocation in the current session (it reads the session ID from the database), or an explicit `--session-id`.
- Trust entries are validated against known ask-type rules. Unknown rule names are rejected.
