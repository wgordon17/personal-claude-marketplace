# OMP Compatibility Reference

This is the marketplace's single detailed reference for running these plugins under
[OMP](https://omp.sh) (`@oh-my-pi/pi-coding-agent`) in addition to Claude Code. It covers
dev-guard's bridge (`dev-guard/omp-extension.ts`) in full, since dev-guard's `hooks.json` is
by far the most complex of the three bridged plugins. git-tools and github-mcp ship their own,
much simpler single-hook bridges — see their own READMEs for a short description; this file
does not duplicate that content.

All findings below come from a live verification spike against a real, unpinned OMP install
(`omp v17.4.2`, tested 2026-08-21). The spike combined live behavioral tests with static
ground-truth reads of OMP's shipped TypeScript type definitions and embedded docs. No OMP
version pinning is used anywhere in this design — results may shift as OMP evolves; re-run the
manual verification checklist at the bottom of this file before each dev-guard release that
touches `omp-extension.ts`. See "OMP version follow-up" below for a 2026-08-28 partial
re-verification against OMP v18.0.4/v18.0.6.

## Tool name and field name mapping

OMP's built-in tool names (`BUILTIN_TOOL_NAMES`) are lowercase and mostly, but not perfectly,
analogous to Claude Code's tool set:

```
read, bash, edit, ast_grep, ast_edit, ask, debug, eval, github, glob, grep, lsp,
inspect_image, browser, computer, checkpoint, rewind, security_scan, task, hub,
todo, web_search, write, memory_edit, retain, recall, reflect, learn, manage_skill
```

| Claude Code tool | OMP tool | Field-name mapping | Status |
|---|---|---|---|
| `Bash` | `bash` | OMP: `command`, `env`, `timeout`, `cwd`, `async`, `pty` → guard only reads `command` (identical name) | Confirmed |
| `Read` | `read` | OMP: `path` → Claude Code: `file_path` | Confirmed — bridge translates |
| `WebFetch` | `read` (same tool, URL-shaped `path`) | OMP: `path` (URL) → Claude Code: `url` | Confirmed — OMP has no standalone fetch tool; `omp read <url>` handles URLs through the same `read` tool. The bridge disambiguates by testing `path` against `^https?://` |
| `Write` | `write` | `WriteToolInput` not enumerated this pass | **Unverified** — bridge passes input through unchanged |
| `Edit` | `edit` | **Untyped** (`Record<string, unknown>`) — OMP's edit tool uses a hashline-based protocol, not a simple old/new-string diff | **Materially different tool** — bridge passes input through unchanged; no known field rename exists |
| `Grep`/`Glob` | `grep`/`glob` | `pattern`, `path` (legacy alias `paths`) — same field names both sides | Confirmed, but **dev-guard's bridge has no dispatch for these** — `tool-selection-guard.py` has no Grep/Glob-specific guard logic to bridge (its own `grep`/`find`/`ls` command-redirection rules were removed in v1.63.0 once Claude Code itself routed search through Bash) |
| `WebSearch` | `web_search` | Best-effort guess: `query`, `allowed_domains`, `blocked_domains` | **Not live-verified against OMP** — re-check per the manual checklist below |
| `TodoWrite` | `todo` (nominal) | n/a | Nominal mapping only — live testing found OMP does not currently grant `todo` to plugin-provided subagents regardless of their declared `tools:` list (see "Subagent `tools:` frontmatter enforcement" below) |
| `AskUserQuestion` | *(none built in)* | n/a | No OMP built-in exists. dev-guard's own bridge registers a **custom tool literally named `AskUserQuestion`** — confirmed permitted with no collision against OMP's built-in `ask` tool. Only available where dev-guard's own bridge is installed and active. |
| `SendMessage` | *(none)* | n/a | **No OMP equivalent at all.** Multi-agent teammate messaging (Claude Code Agent Teams) has no analog in OMP's tool surface. |
| `NotebookEdit` | *(none)* | n/a | No OMP `tool_call` event exists for a notebook-editing equivalent. |
| `EnterPlanMode` | *(none)* | n/a | OMP's plan mode is a slash-command modality (see `docs/extensions.md`'s `plan-mode.ts` example), not a gated tool call — there is no `tool_call` event to intercept. |

**Two Claude Code `PreToolUse` matchers have no OMP dispatch path at all**: `NotebookEdit` and
`EnterPlanMode`. This is a documented gap, not a silent misclassification — `omp-extension.ts`
simply has no handler for either.

## MCP tool-name re-encoding

Claude Code's MCP tool-call convention is `mcp__<server>__<tool>` (double underscore both
places). OMP's live convention, confirmed against a real install across 6 different MCP
servers, is **`mcp__<sanitized_server>_<tool>`** — single underscore before the tool name, any
`plugin_` prefix segment dropped, hyphens replaced with underscores:

| Server | Claude Code tool name | OMP tool name (live-verified) |
|---|---|---|
| github-mcp | `mcp__plugin_github-mcp_github__list_issues` | `mcp__github_mcp_github_list_issues` |
| claude-mem | `mcp__plugin_claude-mem_mcp-search__build_corpus` | `mcp__claude_mem_mcp_search_build_corpus` |
| serena | `mcp__serena__find_symbol` | `mcp__serena_find_symbol` |
| sequential-thinking | `mcp__sequential-thinking__sequentialthinking` | `mcp__sequential_thinking_sequentialthinking` |
| fetchaller-mcp | `mcp__plugin_fetchaller-mcp_fetchaller__fetch` | `mcp__fetchaller_mcp_fetchaller_fetch` |
| **context7** | `mcp__context7__resolve-library-id` | **`mcp__context_resolve_library_id`** |

**context7 is the critical exception**: OMP drops the trailing digit (`context7` → `context`),
which is **not** explained by the general sanitization rule and could not be confirmed from
source (likely bundled/minified in OMP's `cli.js`). A bridge that assumes a pure mechanical
hyphen-to-underscore transform gets this one wrong.

**Design consequence**: `omp-extension.ts` does **not** hand-derive the re-encoded name from a
formula. It hard-codes the OMP-sanitized form for every server dev-guard's own
`MCP_READ_ONLY` allowlist (`mcp_constants.py`) actually cares about (`reencodeMcpToolName()` in
`omp-extension.ts`). The 6 servers above are live-verified; `playwright`,
`plugin_jira_mcp-atlassian-prod`, and `metadata-service` are **best-effort, unverified**
against a live install — derived by applying the same "`plugin_` prefix stripped, hyphens →
underscores" pattern the verified servers all followed. If wrong for one of these three, the
effect is degraded convenience (that server's allowlist entries won't auto-approve under
OMP, falling through to `settings.json` passthrough) — not a security hole, since it's the
strictly narrower outcome versus mis-approving something. An unknown server (not in the
mapping table at all) falls through to a best-effort single-underscore→double-underscore split
at the first boundary, which very likely won't match any allowlist entry — again, safe but
degraded.

context7's tool names also need a per-tool override, independent of the server-name issue:
`resolve_library_id` → `resolve-library-id`, `query_docs` → `query-docs` (context7's tool names
use npm-idiomatic hyphens; every other verified server's tool names are already
underscore-native or camelCase).

**Contract-test coverage**: `dev-guard/tests/test_omp_bridge_contract.py` includes context7's
irregular sanitization as a named regression case specifically because of this finding — see
`TestMcpReencodingPayloadShape::test_context7_resolve_library_id_reencoded_correctly`.

## Event mapping

| OMP event | Claude Code hook event(s) | Status |
|---|---|---|
| `session_start` | `SessionStart` (all 3: `--validate`, `shared-feedback.md`, `token-efficiency.md`) | Confirmed live — fires once per session, before the first turn |
| `session_shutdown` | `SessionEnd` (`--session-end`) | Confirmed live |
| `tool_call` | `PreToolUse` | Confirmed live — event shape `{ type, toolName, toolCallId, input }` |
| `tool_result` | `PostToolUse` | Confirmed live — event shape `{ type, toolName, toolCallId, input, content, details, isError }` |
| `agent_end` | `Stop` **and** `SubagentStop` (same event, dispatched twice) | `Stop` confirmed live; `SubagentStop` is a **best-effort approximation** — see below |

5 distinct OMP events cover all 6 Claude Code hook event types dev-guard registers for
(`SessionStart`/`SessionEnd`/`PreToolUse`/`PostToolUse`/`Stop`/`SubagentStop`) — `agent_end`
does double duty for the last two, since no confirmed OMP event distinguishes a subagent's
`agent_end` from the main session's.

**Batching note**: in a single turn with multiple tool calls, all `tool_call` events fire
before any `tool_result` fires (confirmed live: `session_start → agent_start → tool_call ×2 →
tool_result ×2 → agent_end → session_shutdown`). Sequencing logic must key off `toolCallId`, not
call order — this does not affect correctness (each `tool_result` handler still receives its
own event), but do not assume strict `call→result→call→result` interleaving.

### `SubagentStop` limitation

No confirmed OMP event exists that distinguishes a subagent's `agent_end` from the main
session's own `agent_end`. `omp-extension.ts` dispatches both `stop-hook.py` (Claude Code's
`Stop` semantics) and `subagent-stop-hook.py` (Claude Code's `SubagentStop` semantics) on
*every* `agent_end` firing, as a documented best-effort approximation — not a confirmed
mapping. This ships without further mitigation because `subagent-stop-hook.py` is a
quality/completeness control (it validates `FixSummary` structural completeness only), not a
security gate — a wrong or silent guess here degrades usefulness, not safety, and the hook
already fails open by design on its own crash/loop-guard paths. A lightweight, session-scoped
telemetry counter (`agentEndWithSubagentSignal` / `agentEndWithoutSubagentSignal`, logged via
`pi.logger.debug` on `session_shutdown`) tracks how often `agent_end` fires with any available
subagent-context signal versus without — purely observational, not a gate.

### `AskUserQuestion` naming

The literal tool name `AskUserQuestion` is confirmed permitted under OMP with **no collision**
against the built-in `ask` tool — `omp-extension.ts` registers a custom tool with this exact
name, so `decision-persistence.py`'s hardcoded `tool_name == "AskUserQuestion"`
string-equality check is satisfied without any name translation. No skill-prompt updates were
needed.

The dialog UI itself is feature-detected at call time: `ctx.ui.askDialog()` when reachable
(TUI mode only — confirmed absent in print/RPC/subagent modes), falling back to
`ctx.ui.select()` (single-question, single-select) otherwise. Multi-select questions have no
clean fallback in non-TUI modes — this is a documented degradation, not a bug. Answers are
matched by question **text**, not array position, so a batch answered out of order still maps
correctly to the right stored decision.

**Unverified**: the live human-interaction round-trip for `ctx.ui.askDialog()` itself (actual
dialog rendering, blocking behavior) was not tested in an interactive TUI session during the
spike (no real terminal was available). Re-verify manually before relying on it — see the
checklist below.

**Abort/timeout handling**: neither `ctx.ui.askDialog()` nor `ctx.ui.select()` (OMP's shipped
UI primitives) expose a cancellation parameter of their own. `collectAnswers()` races the UI
call against the tool call's own `AbortSignal` instead — if the signal fires first (the tool
call is cancelled, times out, or the session ends while a question is pending), it resolves to
an empty answer set rather than leaving the tool call hung on a UI response that will never
arrive. An empty answer set flows through `decision-persistence.py`'s normal PostToolUse path
like any other unanswered batch (no decisions recorded, no crash).

## CLAUDE_PLUGIN_ROOT

OMP has **no equivalent** of Claude Code's `CLAUDE_PLUGIN_ROOT` environment variable — confirmed
absent both as an env var (`process.env.CLAUDE_PLUGIN_ROOT` reads `null`) and as an `ExtensionContext`
field. `inject-reference.sh`, `stop-hook.py`, and `subagent-stop-hook.py` all read this variable
directly and silently no-op (not error) if unset, which would otherwise be an invisible, total
failure of the bridge's `SessionStart`/`Stop` handling. `omp-extension.ts` resolves its own
plugin root once (`new URL(".", import.meta.url).pathname.replace(/\/$/, "")`, since the
extension file lives at the plugin root next to `package.json`) and injects
`CLAUDE_PLUGIN_ROOT` manually into **every** subprocess call's environment via a shared
`runScript()` helper.

## Subprocess execution: `Bun.spawn()`, not `pi.exec()`

`pi.exec()`'s typed `ExecOptions` (`{ signal?, timeout?, cwd? }`) has **no `stdin` field at
all** — confirmed both from the type definitions and empirically (`{ stdin: ... }` and
`{ input: ... }` as untyped overrides both silently no-op; the child sees immediate EOF, not
the payload). This invalidated the plan's original working assumption for how the bridge would
hand a JSON payload to the existing Python/shell scripts.

**Mitigation**: OMP extensions run in-process in the same Bun runtime (no isolation), so
`Bun.spawn()` — a Bun global, no import needed — is reachable directly and correctly pipes
stdin. `omp-extension.ts` uses `Bun.spawn()` uniformly for every script invocation (including
the stdin-free `--validate`/`--session-end`/`inject-reference.sh` calls, for one consistent
code path rather than two).

## claudelint `--strict` compatibility

Both sub-questions resolved favorably — **no CI blocker**:

1. A zero-dependency `package.json` (`{"name": ..., "private": true, "omp": {"extensions":
   [...]}}`) plus a `.ts` file added to a plugin directory: `uvx claudelint --strict` exits 0,
   no new warnings or errors.
2. An agent file's frontmatter rewritten to OMP-compatible values (`tools: read, bash, lsp,
   web_search`, `model: claude-opus-5`) in place: also exits 0.

Root cause: `claudelint --list-rules` shows the `agent-frontmatter` rule's actual scope is
"Agent files must have valid frontmatter with name and description" — it only validates the
*presence* of `name`/`description`, never inspecting `tools:`/`model:` values against any
allowlist. This fully resolves the plan's original open question about whether Claude Code's
own CI would reject OMP-compatible frontmatter values.

## `allowed-tools:` (SKILL.md) enforcement

**Confirmed NOT enforced by OMP**, matching the same behavior already documented for Claude
Code itself. Live-tested: invoking a skill with a restrictive `allowed-tools:` list, then
having the model call a tool explicitly excluded from that list — the excluded call succeeded
with no blocking or error. `allowed-tools:` is informational only under both harnesses.

## Subagent `tools:` frontmatter enforcement — significant finding

This is the most consequential finding for Task 4 (code-quality/jira agent frontmatter). Live
testing spawned the actual installed `code-quality` plugin's `test-runner` subagent
(frontmatter: `tools: Bash, Read, TodoWrite`) and asked it to list its own available tools. The
result: its **actual** active tool set was `bash, read, yield, hub` **plus all installed MCP
tools, completely unfiltered** — not restricted to the Bash/Read/TodoWrite-equivalents at all
(and `todo`, `TodoWrite`'s nominal OMP counterpart, was not granted either).

**Conclusion**: the `tools:` frontmatter field does not currently restrict — or even correctly
map — a plugin-discovered subagent's tool access under this OMP version. This is not "unmapped
names silently dropped, mapped names honored" (one of three anticipated outcomes going into the
spike); it is closer to "the whole restriction mechanism doesn't engage for plugin-provided
agents at all," with MCP tools in particular granted unconditionally regardless of any
restriction list.

**Consequence for Task 4**: `code-quality/agents/*.md` and `jira/agents/jira-agent.md` keep
their `tools:` values as literal, unchanged Claude Code tool names — rewriting them to OMP's
lowercase convention would have bought **zero** real restriction under OMP (per this finding)
while actively breaking Claude Code's own enforcement (Claude Code's tool-name matching is
case-sensitive; an unrecognized lowercase name matches nothing, effectively zeroing out that
agent's tool access under Claude Code). Each of those 9 files instead carries a one-line HTML
comment in its markdown body (after the frontmatter's closing `---`, since YAML has no comment
syntax compatible with inline annotations of this kind) cross-referencing this document and
naming which of its own listed tools have no OMP equivalent:

| File | Tools with no OMP equivalent |
|---|---|
| `architect.md` | `WebFetch` |
| `code-reviewer.md`, `code-simplifier.md`, `performance.md`, `qa.md`, `security.md` | none — every listed tool has a direct equivalent |
| `plan-adherence.md` | `AskUserQuestion` (no built-in; only reachable via dev-guard's bridge), `SendMessage` (no equivalent at all) |
| `test-runner.md` | `TodoWrite` (nominal `todo` equivalent exists but is not actually granted, per the finding above) |
| `jira-agent.md` | 19 MCP identifiers using OMP's differently-shaped `mcp__<server>_<tool>` re-encoding — the Jira/Atlassian server mapping is unverified against a live OMP install (not one of the 6 live-verified servers above) |

`model:` frontmatter (`opus`/`sonnet`/`haiku`) is unaffected by any of this — confirmed live
that OMP's model-alias resolution recognizes and maps these bare Claude Code aliases correctly
for agent frontmatter. No action was needed there.

## Multiline Bash

OMP has its own **separate, native** multi-command interceptor (`docs/bash-tool-runtime.md`):
when enabled, it splits a Bash command into fragments on unquoted `&&`, `||`, `;`, `|`, `|&`,
`&`, or newlines, and checks each fragment against OMP's own configured interception rules. This
is structurally similar to (arguably more sophisticated than) `tool-selection-guard.py`'s
`_handle_bash_command` multiline-allow override, but is completely separate and has no
awareness of dev-guard's `GIT_DENY_RULES` or any other dev-guard rule content — it does not
replace dev-guard's own handling.

**Conclusion (architectural, not live end-to-end tested)**: the underlying problem
`_handle_bash_command`'s override addresses — a single Bash call containing multiple
newline/operator-separated commands needing per-fragment classification against dev-guard's own
rules — is not Claude-Code-specific. `BashToolInput.command` is still a single free-form string
under OMP, and nothing prevents a model from putting multiple commands in one call. The
multiline-bash override logic is ported unchanged in the bridge, since it operates on the
`command` string the bridge already translates field-for-field regardless.

## `package.json` dependency resolution

Confirmed low risk: linking a plugin with a zero-dependency `package.json` added produces **no
`node_modules` inside the plugin's own directory** and **no network activity** — OMP just
symlinks the plugin into a shared `node_modules` tree so its `omp.extensions` entry resolves.
`dev-guard/package.json` and the git-tools/github-mcp equivalents can all safely stay
zero-dependency.

## Command namespacing (informational)

Not a bridge concern, but worth knowing when reasoning about OMP compatibility more broadly:
commands from installed plugins are **not** namespaced as `<plugin>:<command>` under OMP the
way Claude Code does it. Skill-derived commands are flattened into a single `skill:<skill-name>`
namespace regardless of source plugin (e.g. `skill:fix`, `skill:git-history`), and MCP-server
prompts get a 3-level `<plugin>:<mcp-server>:<command>` form (e.g.
`github-mcp:github:AssignCodingAgent`).

## Fail-open / fail-closed policy

The bridge's dispatch table classifies matchers by whole matcher **type**, not by inspecting
individual command content (there is no way to classify a single Bash command as
"git-deny-relevant" versus "advisory" without reimplementing `GIT_DENY_RULES` matching in
TypeScript, which this design explicitly declines to do). This classification governs ONLY
what happens when the bridge's own guard subprocess call itself fails or times out
(`spawnFailed`/`timedOut`) — the exit-code-2 hard-block check always runs first, before this
policy is ever consulted, so normal-operation blocking is unaffected either way:

- **Fail closed** (block on subprocess failure): the entire `Bash` tool-call matcher and the
  entire MCP tool-call matcher — these dispatch to guard functions that can hard-block
  (`GIT_DENY_RULES`, the fetchaller mutating-call gate).
- **Fail open** (allow on subprocess failure): the entire `Write`/`Edit`/`Read`/`WebSearch`
  tool-call matchers — NOT because these only reach advisory-only checks. `Write`/`Edit`/
  `NotebookEdit` all have live hard-block paths of their own (`_guard_tmp_path`,
  `_guard_comment_narration`), and WebFetch/WebSearch/read-as-URL route through
  `_check_url_rules`, whose `BLOCKED_URL_RULES` entries default to a `"block"` action (~17 of
  26; the rest override to `"ask"`) — a real hard block is reachable here during normal
  operation too. Only a plain Read of a file path is genuinely advisory-only (its one guard,
  `_guard_claire_typo`, only ever corrects the path or allows, never blocks). Fail-open is
  accepted specifically for the subprocess-failure/timeout window on these four matchers, not
  a claim about what they do the rest of the time.

No native TypeScript fallback exists for `GIT_DENY_RULES` — fail-closed-on-subprocess-failure
alone is the accepted mitigation, to avoid a second, unsandboxed source of truth for
security-relevant logic that could drift from the Python original.

## `ask`-type decisions have no interactive path under OMP

Claude Code's `PreToolUse` `hookSpecificOutput` protocol has three permission decisions:
`allow`, `ask`, and `deny`. `ask` normally pauses the tool call and shows the user an
interactive confirmation prompt they can approve in the moment — `tool-selection-guard.py`'s
own `_exit_with_decision()` `ask` branch is built around that round trip, including its trust
hint ("To trust: `/dev-guard trust add <rule>`...") shown alongside the prompt.

OMP's `ToolCallEventResult` (the bridge's `tool_call` return type) has no field for "pause and
show an interactive confirmation" — only `block` (with a `reason`), `input` (an in-place
correction), or no-op (allow). `omp-extension.ts`'s `tool_call` handler therefore folds `ask`
into the same branch as `deny`:

```ts
if (output?.permissionDecision === "ask" || output?.permissionDecision === "deny") {
    return { block: true, reason: output.permissionDecisionReason ?? "Blocked by dev-guard." };
}
```

This is intentional and security-conservative — fail-closed rather than silently downgrading
`ask` to `allow` — but it changes the remediation path. Under Claude Code, an `ask`-type rule
firing mid-session can be approved on the spot. **Under OMP it cannot**: the only way to
unblock an `ask`-type rule is to pre-establish trust *before* the call, via `/dev-guard trust
add <rule-name> [--match <pattern>] [--scope session|--scope always]` — the same trust store
`_check_trust()` reads for `ask` decisions under both harnesses. OMP users who hit a hard block
from what was designed as an interactive confirmation should reach for that command rather
than expect a retry-and-approve prompt.

## OMP version follow-up (v18.0.4 / v18.0.6, 2026-08-28)

The verification spike above was run against OMP v17.4.2. OMP has since had a major version
bump (18.x); this is a follow-up re-verification, not a full re-run of the original spike.

**Changelog review**: every OMP changelog entry from v17.4.2 through v18.0.6 was reviewed
(v18.0.0, v18.0.1, v18.0.3, v18.0.4, v18.0.5, v18.0.6 — v18.0.2 was never published). No
changes were found to `pi.on`, `pi.exec`/subprocess execution, `ctx.ui.*`, MCP tool-naming
conventions, or `package.json#omp.extensions` loading — the surfaces this bridge actually
depends on. Two "Breaking Changes" entries fall in this range (v18.0.0 in `pi-tui`, v18.0.5 in
`pi-ai`/`pi-tui`), both scoped to internal TUI/provider-retry APIs, not the extension surface
these bridges touch.

**Adjacent, not confirmed relevant**: v18.0.5's changelog also lists a fix for
"marketplace-installed plugins failing to discover their `rules/` directories" — the same
general subsystem (marketplace plugin discovery) as this bridge's `package.json#omp.extensions`
loading path, though a different specific feature. None of the three bridges here use a
`rules/` directory, so this fix is not expected to affect them; noted because it's the closest
adjacent finding in the reviewed range, not because it's known to matter.

**Live re-verification performed**: `omp --plugin-dir` against the actual bridge code, run
under OMP v18.0.4, confirmed `session_start` dispatch fires correctly and injects the expected
content for all three plugins — verified by inspecting the constructed API request body
directly. Each plugin's expected session-start text (dev-guard's `shared-feedback.md`/
`token-efficiency.md` content, git-tools' Git Workflow Instructions, github-mcp's
`GITHUB_MCP_HINT` string) appeared in the request.

**Not re-verified**: `tool_call`/`tool_result` dispatch (Bash/Write/Edit/MCP handling) remains
unverified against v18.0.4 specifically. Reaching that requires an actual completed model turn,
which was blocked in this environment by a Vertex AI organization policy
(`constraints/vertexai.allowedPartnerModelFeatures`) unrelated to OMP itself. This is a real,
unresolved gap, not a formality — the manual verification checklist's items 2-4 below still
need re-running against live OMP when unblocked model access is available. This follow-up
closes only the session-lifecycle item, not the rest of the checklist.

## Manual verification checklist

As of 2026-08-28, `dev-guard/tests/omp-extension.test.ts` (plus
`git-tools/tests/omp-extension.test.ts` and `github-mcp/tests/omp-extension.test.ts`) provides
real `bun test` coverage for the bridge's own pure translation/parsing logic —
`translateToolNameForGuard`, `translateToolInputForGuard`, `classifyReadTool`,
`reencodeMcpToolName`, `isMcpTool`, `getFailPolicy`, `parseHookOutput`, `parseStopDecision`,
`bashToolResponseForGuard`, `readToolResponseForGuard`, and `lastAssistantText` are exercised
directly, plus an internal-consistency check between `mcp_constants.py`'s `MCP_READ_ONLY`
server set and this file's `OMP_TO_CLAUDE_CODE_MCP_SERVER` table. This closes the specific gap
that previously let a Stop/SubagentStop block-decision parsing bug slip through review
undetected: nothing exercised the TypeScript translation logic itself, only the Python guard
scripts' tolerance of already-correct payloads (`dev-guard/tests/test_omp_bridge_contract.py`).
What `bun test` still cannot do, with no live OMP install in CI, is exercise the actual
`pi.on(...)` dispatch wiring, `ctx.ui.*` calls, or subprocess round-trips end to end — that
requires a real OMP runtime. The checklist below remains the mitigation for that remaining gap,
transcribed verbatim from `test_omp_bridge_contract.py`'s module docstring — re-run it against
a live OMP install before each dev-guard release that touches `omp-extension.ts`.

1. **Session lifecycle**: start a session under OMP with dev-guard installed. Confirm
   `session_start` dispatches `--validate` and both `inject-reference.sh` calls
   (`shared-feedback.md`, `token-efficiency.md`) concurrently via `Promise.all` — verify all
   three deliver content into the model's context (ask it to quote injected text back
   verbatim), and that `shared-feedback.md`'s content still lands before
   `token-efficiency.md`'s (message order is preserved by `Promise.all`'s result-array
   ordering, not by which subprocess resolves first). Confirm session end fires
   `session_shutdown` → `--session-end` with no crash, and the `session_state` DB row is
   updated. **Double-confirmed**: re-verified against OMP v18.0.4 on 2026-08-28 (see "OMP
   version follow-up" above). Every other item in this checklist is still single-verified from
   the original v17.4.2 spike only.
2. **tool_call/tool_result dispatch**, one round-trip per tool type:
   - `bash`: a blocked case (a `printf`/`echo-noop`-style rule) fires with exit code 2, and the
     block reason reaches the model as a tool error, not a passthrough. An allowed case (e.g.
     `ls`) succeeds. Separately, confirm a `permissionDecision: ask` response (e.g. from a
     git-ask-rule like `git stash drop`) is correctly folded into a hard block, not passed
     through as an allow.
   - `write`, `edit`: an allowed case succeeds. Separately, a case that WOULD hit one of
     `_guard_tmp_path`/`_guard_comment_narration`'s real hard-block paths still succeeds if the
     bridge's own subprocess call is broken (fail-open on subprocess failure, not because these
     checks are advisory).
   - `read`: both a plain file path (Read-shaped) and a `https://...` path (WebFetch-shaped)
     route to the correct Claude Code `tool_name` in the guard's stdin payload. Confirm a
     `.claire/...` path is rewritten to `.claude/...` and the corrected path is what the tool
     actually reads (not just what the guard's stdout computed —
     `ToolCallEventResult.input` must carry it).
   - an MCP tool from a server listed in the re-encoding table above (e.g. context7's
     `resolve-library-id`) auto-approves with no ask prompt; an MCP tool from an unrecognized
     server passes through without crashing.
3. **AskUserQuestion round-trip**: register/call the custom tool under OMP, confirm no name
   collision with the built-in `ask` tool, confirm `ctx.ui.askDialog()` is used when reachable
   (TUI mode) and `ctx.ui.select()` as a fallback otherwise (print/RPC/subagent modes). Confirm
   a two-question batch answered in reverse order records each decision against the correct
   fingerprint (matched by question text, not array position).
4. **Stop/SubagentStop**: confirm `agent_end` dispatches both `stop-hook.py` and
   `subagent-stop-hook.py` without crashing, and that the `agent_end` telemetry counters (logged
   via `pi.logger.debug` on `session_shutdown`) increment as expected.

Last run in full against: OMP v17.4.2 (see `hack/research/omp-spike-findings.md`,
gitignored/local-only, for the full live-verification evidence trail this document
summarizes). Item 1 (session lifecycle) only was additionally re-verified against OMP v18.0.4
on 2026-08-28 — see "OMP version follow-up" above. Items 2-4 remain single-verified from the
original v17.4.2 spike and still need re-running against current OMP.
