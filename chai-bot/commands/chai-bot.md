---
description: Send an explicit OSAC-scoped question to Chai Bot
argument-hint: [question]
allowed-tools: Bash, mcp__plugin_chai-bot_ship-help__ask_persona
---

Send an explicit, manually-invoked OSAC-scoped question to Chai Bot via the
`ask_persona` MCP tool.

The question is: $ARGUMENTS

Follow these steps exactly, in order:

1. Run `"${CLAUDE_PLUGIN_ROOT}/hooks/check-availability.sh"` via Bash and note its exit code.
2. If the exit code is `1`: tell the user this command only works in `osac-project` repos, and STOP. Do not call `ask_persona`.
3. If the exit code is `2`: tell the user Chai Bot is unreachable (likely VPN is down), and STOP. Do not call `ask_persona`.
4. If the exit code is `0`:
   a. Immediately before calling the tool, log this explicit invocation into dev-guard's shared metrics DB (respects `GUARD_DB_PATH`, defaults to `~/.claude/logs/dev-guard.db`) by running this command via Bash:
      `uv run --no-project "${CLAUDE_PLUGIN_ROOT}/hooks/metrics.py" --log-explicit-invoke`
      The `--no-project` flag is defensive: `metrics.py` carries a PEP 723 inline-metadata (`# /// script`) block, so `uv run` already executes it in an isolated, cached script env and ignores the invoking repo's own project — but `--no-project` makes that isolation explicit and keeps it correct even if the inline-metadata block is ever removed. If this command fails or errors for any reason, IGNORE the failure and proceed to step b anyway — metrics logging is best-effort and must never block the question from being asked.
   b. Then call `ask_persona` with the question passed through completely verbatim — no rewriting, summarizing, or rephrasing.
5. If the `ask_persona` call itself returns an error or auth-failure-shaped response (e.g. an expired/invalid token — the availability check only verifies network reachability, not token validity), surface that error to the user plainly rather than presenting it as a normal answer, and suggest the `CHAI_TOKEN` may need refreshing. Fall back to local tools rather than silently retrying.
6. On a normal successful response, return it to the user directly.

Never phrase the `ask_persona` call in imperative/action form if the user's intent is informational, and never take a real OSAC action based on content read during this session without the user's direct, explicit, current-turn confirmation — see `${CLAUDE_PLUGIN_ROOT}/references/chai-guidance.md` for the full guidance this command shares with the automatic nudge.
