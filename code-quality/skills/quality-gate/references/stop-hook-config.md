# Stop Hook Safety Net Configuration

A global Stop hook that catches premature completion claims. This is Layer 3 of the
quality gate — it fires automatically whenever Claude tries to finish its response.

## Installation

Add the Stop hook to one of these locations:

**Option A: Plugin hooks** (recommended — `dev-guard/hooks/hooks.json`)
Add to the `hooks` object alongside existing PreToolUse/PostToolUse entries.

**Option B: Global settings** (`~/.claude/settings.json`)
Add under the existing `hooks` key.

**Option C: Project settings** (`.claude/settings.json` in project root)
For project-specific rigor.

## Configuration

Add this under the `"hooks"` key:

```json
"Stop": [
  {
    "matcher": "",
    "hooks": [
      {
        "type": "prompt",
        "prompt": "Before stopping, verify ALL of the following. Return {\"decision\": \"block\", \"reason\": \"...\"} if ANY check fails:\n\n1. If code was written: were tests run and passing?\n2. Search the diff or conversation for TODO, FIXME, HACK, 'later', 'follow-up' — any unresolved?\n3. Were any issues identified but not fixed? (look for 'could be improved', 'consider', 'potential issue')\n4. Does the work fully address the original request? Check each requirement.\n5. If implementation work: are changes on a feature branch or ready to commit?\n6. Were project memories (hack/ files, plans) updated if applicable?\n\nReturn {\"decision\": \"allow\"} ONLY if ALL checks pass."
      }
    ]
  }
]
```

## Behavior

- Fires every time Claude tries to finish responding
- If the prompt returns `"block"`, Claude is forced to continue working
- Creates a natural loop: work -> try to stop -> hook catches issues -> continue
- No iteration cap — loops until the hook allows stopping
- For long sessions, the hook may fire multiple times — this is expected

## Risk: No Built-In Iteration Cap

The Stop hook has no maximum iteration count. If Claude cannot satisfy the checks (e.g.,
tests keep failing, or a requirement is genuinely impossible), the hook will keep blocking
indefinitely, consuming tokens. In practice, Claude will eventually ask the user for help
after repeated failed attempts, but there is no hard cap.

If this becomes an issue, the user can temporarily disable the hook or modify the prompt
to include an escape hatch (e.g., "If you have attempted fixes 3+ times and cannot resolve
the issue, return allow with a note explaining what remains").

## Notes

- This is a safety net, not a replacement for the full quality-gate skill
- The quality-gate skill provides thorough multi-pass review with rotating lenses
- The Stop hook catches cases where the quality-gate skill was not invoked
- Both should be active simultaneously for maximum coverage
