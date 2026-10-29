# chai-bot

Advisory `SessionStart` nudge (for OSAC repositories) and unrestricted explicit
`/chai-bot` command for offloading OSAC-scoped research/actions or general
second-opinion questions to the Red Hat-internal "Chai Bot" MCP server
(`ask_persona`, via the `ship-help` MCP server). This plugin ships an
`omp-extension.ts` dual-harness bridge so its hooks also work under the OMP
harness, not just Claude Code.

## What it does

- A `SessionStart` hook advisorially nudges Claude to prefer `ask_persona`
  for broad OSAC research/status/cross-referencing questions (and as a peer
  agent for second-opinion questions) when working in an OSAC repo (`osac-project`
  org or `osac`/`osac-*`/`.osac-*` repos like `osac-spine`) and Chai Bot is
  reachable.
- Nothing is enforced or blocked: this is advisory guidance, not a gate on
  local exploration.
- Usage is logged (never question/answer content) to a shared metrics DB.
- An explicit `/chai-bot [question]` command sends a question manually from
  **any** repo or directory (requiring only VPN/network reachability via
  `check-availability.sh --probe-only`), regardless of whether the automatic
  `SessionStart` nudge fired.

## VPN dependency

Chai Bot runs on Red Hat's internal network. Without VPN connectivity, the
availability check fails and the plugin degrades silently: no advisory nudge
appears, and `/chai-bot` reports that Chai Bot is unreachable rather than
attempting the call.

## Setup

Add the following to your `~/.claude/settings.json` (this file is outside
this repository and is never committed):

```json
{
  "env": {
    "CHAI_TOKEN": "<your-chai-token>",
    "CHAI_BOT_BASE_URL": "<your-internal-ship-help-host>"
  },
  "permissions": {
    "allow": ["mcp__plugin_chai-bot_ship-help__ask_persona"]
  }
}
```

- `CHAI_TOKEN` — your personal Chai Bot bearer token. Never commit a real
  value; the placeholder above is illustrative only.
- `CHAI_BOT_BASE_URL` — the internal ship-help-mcp host. This value is kept
  out of `.mcp.json` and `hooks/check-availability.sh` (both committed,
  public files) specifically so the internal Red Hat hostname never lands in
  git history. **Must use the `https://` scheme** (loopback `http://` is
  allowed only for local testing) — `CHAI_TOKEN` is sent as a bearer header
  on every chai-bot MCP call, so if `CHAI_BOT_BASE_URL` is set to a non-https
  value, the chai-bot metrics hook denies the call at call time
  (`hooks/metrics.py` returns `permissionDecision: "deny"`), and `hooks/check-availability.sh`
  also reports unavailable (exit 2), suppressing the advisory nudge and the
  `/chai-bot` command.
- The `permissions.allow` entry documents intent and avoids a manual
  permission prompt on the very first `ask_persona` call in a session,
  before the chai-bot metrics hook has had a chance to fire. It is **not**
  the control that keeps `ask_persona` auto-approved after that — see the
  safety note below for which control surface actually does, and why
  removing this entry alone does not revoke auto-approval. `submit_feedback`
  and `submit_lesson` (if ever exposed by this server) are deliberately left
  off this list and stay on manual/default permission.

## Where the automatic nudge fires

Only in repos where **either** `origin` or `upstream` matches an OSAC
GitHub remote — either under the `osac-project` GitHub org (including forked
child repos where `origin` is a personal fork and `upstream` is `osac-project`)
or any GitHub repo named `osac`, `osac-*`, or `.osac-*` (such as `osac-spine`,
`osac-dev-environment`, `osac-sno-provisioner`, or `.osac-ai-skills`) — and
only when Chai Bot answers a fast reachability probe. Everywhere else — an
unrelated repo, or an OSAC repo with the VPN down — the `SessionStart` hook is
silent.

## Using `/chai-bot` explicitly

`/chai-bot What is the status of OSAC-1234?` (or any generalized technical /
second-opinion question) runs `check-availability.sh --probe-only` to verify
VPN/server reachability without restricting by repository, then calls
`ask_persona` with your question passed through verbatim. If the reachability
probe fails, the command reports that Chai Bot is unreachable instead of
attempting the call.

## Safety note

`ask_persona` is auto-approved with **no permission prompt**, because it has
confirmed real write capability (it can act on Slack/Jira/GitHub for the
OSAC org) triggered purely by natural-language phrasing — there is no
call-time gate distinguishing an informational question from an
action-shaped one.

**Control surface — read this before assuming `settings.json` alone controls
approval.** The actual auto-approval mechanism is the chai-bot metrics HOOK
itself: `hooks/metrics.py`'s `PreToolUse` handler emits
`permissionDecision: "allow"` for every `ask_persona` call it sees when
`CHAI_BOT_BASE_URL` is https/loopback-safe (scoped to `ask_persona` only —
`submit_feedback`/`submit_lesson` get no permission decision from this hook
on a safe base URL, so they stay on manual/default permission).
Transport safety overrides this for EVERY chai-bot MCP tool: if
`CHAI_BOT_BASE_URL` is not `https://` (nor an `http://` loopback host), the
handler emits `permissionDecision: "deny"` for the call — blocking
`ask_persona`, `submit_feedback`, `submit_lesson`, or any other chai-bot MCP
tool — so the Bearer `CHAI_TOKEN` is never sent over cleartext.
The `~/.claude/settings.json` `permissions.allow` entry described in Setup,
above, is redundant with this — belt-and-suspenders whose only real value is
avoiding a manual prompt on the very first `ask_persona` call of a session
(before the hook has fired) and documenting intent for anyone reading
`settings.json`. **To revoke auto-approval, disable the chai-bot metrics
hook** (e.g. remove/disable `chai-bot/hooks/hooks.json`'s `PreToolUse`
entry, or disable the plugin entirely). Editing or removing the
`settings.json` entry by itself will **not** bring back a manual
confirmation prompt — the hook re-allows every call regardless of that
setting.

This auto-approval is mitigated, not eliminated, by:

1. Advisory guidance injected into every eligible session (see
   `references/chai-guidance.md`) instructing against imperative/action
   phrasing for informational intent, and against treating
   session-content-sourced instructions as actionable without the user's
   explicit, current-turn confirmation.
2. A one-sentence reminder of the same rule, re-injected by `hooks/metrics.py`
   on every `ask_persona` call specifically (not just once at session start),
   so the guidance survives long sessions.
3. dev-guard's own `mcp_write` stop-hook backstop, which still applies since
   `ask_persona` is intentionally never added to dev-guard's
   `MCP_READ_ONLY` allowlist.

Read `references/chai-guidance.md` in full before relying on this plugin for
anything action-adjacent.

## Usage metrics and transparency

`hooks/metrics.py` logs call counts, latency, and response size (never
question/answer text) into dev-guard's shared SQLite database
(`~/.claude/logs/dev-guard.db`, or `GUARD_DB_PATH`), under
`category="chai-bot"`. Interpretation:

- Explicit invocations (via `/chai-bot`) = `count(category="chai-bot",
  action="explicit-invoke")` — written by `metrics.py`'s
  `--log-explicit-invoke` CLI mode, which `/chai-bot` calls immediately
  before invoking `ask_persona`.
- Total `ask_persona` calls = `count(category="chai-bot", action="pre",
  command = 'mcp__plugin_chai-bot_ship-help__ask_persona')` — an EXACT
  string match, not a `LIKE '%__ask_persona'` wildcard match: `_` is itself
  a LIKE wildcard character, so a wildcard version of this query can
  silently over-match.
- Autonomous (nudge-driven) invocations = `max(0, total − explicit)`.

The explicit and total counts come from two independent trigger paths — the
`/chai-bot` command's explicit-invoke write and this hook's own PreToolUse
write for every `ask_persona` call — with no per-call correlation between
them. Both numbers are aggregate proxies over a time window, not a
per-call breakdown, so treat "autonomous" as an estimate, not an exact
count; this is also why the subtraction is clamped at zero rather than
allowed to go negative.

Explicit-vs-autonomous tagging is Claude-Code-only: the `/chai-bot` command
has no OMP bridge (only `hooks/metrics.py`'s Pre/PostToolUse logging does),
so under the OMP harness there is no `--log-explicit-invoke` write path at
all — every `ask_persona` call under OMP counts as autonomous in these
metrics, even ones a user triggered by asking a question that Claude decided
to answer via `ask_persona`.

dev-guard's own `mcp__.*` guard hook separately logs a
`category="guard"`/`rule="mcp-unknown"` passthrough row for every
`ask_persona` call too (since it's not in `MCP_READ_ONLY`). That's expected,
not a double-count bug — always filter chai-bot usage queries on
`category="chai-bot"`.
