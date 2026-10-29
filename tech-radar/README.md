# Tech Radar

Scheduled personal tech radar: new features you could use, compromises, lifecycle risks, superior replacements and relevant new tools across your machine, projects and AI harness

## Overview

`tech-radar` is a scheduled, memory-backed personal technology radar for macOS and T3 Code (`claudeAgent_bifrost` / `gemini-4-argon`). It splits responsibilities strictly between deterministic Python code and the LLM skill ([`skills/radar/SKILL.md`](skills/radar/SKILL.md)):

- **Deterministic script (`radar.py` + `tech_radar` package)** owns all facts, local inventory collection, external HTTP/CLI fetches, supply-chain and slopsquatting verification gates, scoring math, privacy aliasing, SQLite (`radar.db`) state, Markdown/HTML/JSON rendering, and out-of-band confirmation codes.
- **LLM skill (`/tech-radar:radar`)** owns relevance filtering, stack-fit judgment, comparative superiority reasoning, and digest prose. The LLM never fetches facts the script can fetch, cannot override a gate, cannot read private project or package names, and can only **stage** decisions, feedback, or taste updates.
- **Hourly compromise check (`com.user.tech-radar.hourly`)** runs an LLM-free check every hour via `launchd`, executing a stdlib-only inline verifier (`/usr/bin/python3 -I -c`) that validates `MANIFEST.sha256` and every file in `~/.local/share/tech-radar/app` before invoking `uv run --script --locked --isolated`.

## Prerequisites

1. **Chrome for Testing + `BROWSER_EXECUTABLE_PATH`** (required by `doctor`, Spec 06 T4):
   `fetchaller-mcp` must never attach to your everyday logged-in Google Chrome profile. `doctor` refuses an unset `BROWSER_EXECUTABLE_PATH` or any path whose `realpath` resolves under `/Applications/Google Chrome.app`.
   ```bash
   npx @puppeteer/browsers install chrome@stable --path ~/Applications/ChromeForTesting
   ```
   Set `BROWSER_EXECUTABLE_PATH` in `~/.claude/settings.json` under `"env"` to the installed Chrome for Testing binary path.
2. **Xcode Command Line Tools (`/usr/bin/python3`)**:
   Required so the hourly `launchd` job can run the pinned inline verifier via `/usr/bin/python3 -I -c`. Checked by `doctor`.
3. **`uv`** (`brew install uv`):
   Runs `radar.py` and `reddit_pull.py` in locked, isolated PEP 723 script environments (`uv run --script --locked --isolated`).
4. **`gh` CLI (`brew install gh` and `gh auth login`)**:
   Used for GitHub REST and GraphQL queries. All calls pass through `procs.gh`, which enforces GET-only REST endpoints (matching the `$`-anchored allowlist regex in Spec 12 A33) and read-only GraphQL `query` bodies.
5. **macOS notification permission**:
   Required to receive urgent confirmed security notifications (`compromise`, `incident`, `kev`, `app_integrity`) and 6-character confirmation codes (`request-confirm`). Test and grant permission with `notify-test` (uses `terminal-notifier` if installed, otherwise `/usr/bin/osascript`).
6. **`osv-scanner` (optional, `brew install osv-scanner`)**:
   Scans non-Python lockfiles during daily `collect` when all direct and locked packages in a project are classified `public` (Python lockfiles use `uv audit --frozen --output-format json` with `UV_MALWARE_CHECK=1` against an isolated copy under `~/.local/share/tech-radar/audit/`).

## Bootstrap Sequence (Spec 12 A43)

First-time setup is split between an interactive terminal (for privileged `†` commands) and a T3 thread, with **`bootstrap lockdown` run last** so schedule creation is never blocked by `dontAsk` rules:

1. **Terminal — Initialize directories, DB, app copy, hourly job, and baseline inventory:**
   ```bash
   uv run --script --locked --isolated ~/.claude/plugins/cache/personal-claude-marketplace/tech-radar/0.1.0/skills/radar/scripts/radar.py bootstrap init --channel cli
   ```
   This creates `~/.local/share/tech-radar` (`0700`), `~/.local/share/tech-radar-run` (`0700`), `~/SynologyDrive/ActiveProjects/personal/tech-radar-data` (`0700`), and `~/.local/state/tech-radar` (`0700`); initializes `radar.db` (`0600`); writes `TASTE_PROFILE.md` and `WATCHLIST.md` templates; writes `~/.local/share/tech-radar-run/RADAR_CMD`; syncs the verified app copy (`app sync`) and installs the hourly `launchd` agent (`launchd install`); runs `collect --inventory-only`; and writes `~/.local/share/tech-radar-run/runs/bootstrap/interview-context.json` (containing public names and `private-project-N` aliases only).
2. **T3 Thread — Run `/tech-radar:radar bootstrap`:**
   In an interactive T3 thread (`runtimeMode: "full-access"` so `t3_project_create` and `t3_project_update` are permitted), invoke `/tech-radar:radar bootstrap`. The agent:
   - Reads `~/.local/share/tech-radar-run/RADAR_CMD` as `$R` and runs `$R doctor` (guiding you through any missing precondition) and `$R notify-test`.
   - Reads `runs/bootstrap/interview-context.json` and interviews you via `AskUserQuestion` (confirming languages, categories, anti-interests, preferences, taste-makers, watchlist, initial `hold` vs `adopt` rings for top in-use public tools, and AI subscriptions).
   - Writes `~/.local/share/tech-radar-run/runs/bootstrap/answers.json`.
   - Checks `$R credentials status` and shows `$R credentials guide producthunt` if unconfigured.
   - Creates the T3 run project rooted at `~/.local/share/tech-radar-run` (default provider `claudeAgent_bifrost`, model `gemini-4-argon`), launches a thread in it to register the daily `07:00` `schedule_task` (`bindToCurrentThread: false`, prompt `/tech-radar:radar run`), and reads back the next scheduled run time.
3. **Terminal — Apply interview answers and register private scopes:**
   Run in your terminal (using the command prefix in `~/.local/share/tech-radar-run/RADAR_CMD`):
   ```bash
   /opt/homebrew/bin/uv run --script --locked --isolated ~/.local/share/tech-radar/app/radar.py bootstrap apply --answers ~/.local/share/tech-radar-run/runs/bootstrap/answers.json --channel cli
   /opt/homebrew/bin/uv run --script --locked --isolated ~/.local/share/tech-radar/app/radar.py profile private-scopes add <your-private-org-or-scope> --channel cli
   ```
   `bootstrap apply` validates and ingests `answers.json`, records initial decisions (`channel='bootstrap'`), and deletes `answers.json`. `profile private-scopes` stores private GitHub orgs, npm scopes, or index names exclusively in `radar.db` (`kv['private_scopes']`) so they never appear in chat transcripts or synced Markdown files.
4. **Terminal (LAST) — Run `bootstrap lockdown`:**
   ```bash
   /opt/homebrew/bin/uv run --script --locked --isolated ~/.local/share/tech-radar/app/radar.py bootstrap lockdown --channel cli
   ```
   `bootstrap lockdown` runs last after the T3 project and schedule exist. When `ENFORCEMENT_HONOURED` is true, it expands `run-project-settings.json` and writes `~/.local/share/tech-radar-run/.claude/settings.json`; when `ENFORCEMENT_HONOURED` is false (current T3 behavior), it prints `enforcement: not honoured` and writes nothing.
5. **First Run:**
   Wait for the daily `07:00` scheduled run or let the bootstrap agent trigger `run_scheduled_task_now`. The first `collect` that executes external sources sets `kv['baseline_done']` (suppressing baseline notifications for `lifecycle`, `feature`, and `chatter` alerts while still notifying on confirmed `compromise`, `kev`, `app_integrity`, and recent `<30d` `incident` alerts).

## CLI Commands (Spec 01)

Every subcommand prints exactly **one JSON status line** to `stdout` (`{"ok": bool, ...}`).
Exit codes: `0` ok, `2` validation error, `3` degraded (some sources failed), `4` refused (precondition failure, terminal-only command without TTY, or invalid/expired confirmation code), `1` unexpected error.

Commands marked **†** are **terminal-only**: they require `--channel cli` **and** an interactive TTY on both `stdin` and `stdout` (`sys.stdin.isatty() and sys.stdout.isatty()`), with no environment-variable bypass.

| Subcommand | Purpose | Writes |
|---|---|---|
| `doctor` | Check preconditions: Chrome for Testing (`BROWSER_EXECUTABLE_PATH`), tools, Keychain items, sync dir writable, schema version, `launchd` status, `/usr/bin/python3 -I -c pass`, and settings prefix match | — |
| `bootstrap init --channel cli` † | Create `state`/`run_root`/`sync`/`logs` dirs, DB, templates, `RADAR_CMD`; run `app sync`, `launchd install`, `collect --inventory-only`; write `runs/bootstrap/interview-context.json` | state, run_root, sync |
| `bootstrap apply --answers FILE --channel cli` † | Validate and ingest interview answers JSON into `TASTE_PROFILE.md`, `WATCHLIST.md`, and initial ring decisions, then delete `answers.json` | sync, db |
| `bootstrap lockdown --channel cli` † | Write `{run_root}/.claude/settings.json` from template iff `ENFORCEMENT_HONOURED` | run_root |
| `credentials status` / `credentials guide NAME` | Show which keyed sources have Keychain items; print setup guide (never reads or prints secret values) | — |
| `plan-run --requested daily [--now ISO] [--thread-id ID]` | Run `app sync`; stage `DECISIONS.md` edits (`decisions_md`); select effective mode (`monthly` ⊃ `weekly` ⊃ `daily`, catch-up); acquire `run.lock`; create `runs` row | db, state/app |
| `collect --run ID` / `collect --inventory-only` | Run inventory, due sources, security/lifecycle/feature evaluators, candidate promotion/gates, and write brief files (`--inventory-only` runs inventory + in-use promotion only) | db, runs/ID/brief/* |
| `enrich --run ID` | Read `runs/ID/shortlist.json`, enforce privacy pre-check (A5/A8), verify proposals live, fetch per-item metrics, and write `brief/enriched.jsonl` | db, runs/ID/brief/* |
| `verdict apply --run ID` | Validate `runs/ID/verdicts.json` against DB state and Spec 05/A6 rules; apply valid verdicts or return field errors (exit 2) | db |
| `render --run ID` | Render digest (`runs/ID/digest.md` + `digests` table), `TECH_RADAR.md`, `DECISIONS.md`, `INVENTORY.md`, `project-alerts.json`, weekly/monthly `radar.byor.json` and `radar.html`, and `radar.snapshot.db`; finalize run and release `run.lock` | sync, state, run_root |
| `decide ITEM VERB [--ttl 90d] [--reason TEXT] [--wake JSON] --channel cli` † | Apply a decision (`adopt\|trial\|assess\|hold\|reject\|snooze`) directly from a terminal | db, DECISIONS.md |
| `decide-pending ITEM VERB [--ttl 90d] [--reason TEXT] [--wake JSON]` | Stage a decision from a user thread reply (`pending`, `channel='thread'`) | db |
| `feedback ITEM useful\|not_useful\|acted` | Stage digest-item feedback (`pending`) | db |
| `taste diff` / `taste propose` / `taste apply --channel cli` † | Diff `TASTE_PROFILE.proposed.md` vs `TASTE_PROFILE.md`; stage applying it (with frozen SHA-256); apply directly from a terminal | sync (apply), db (propose) |
| `request-confirm [--run ID]` | Batch all staged entries; send 6-char confirmation code **only** via macOS notification listing staged IDs/verbs; print aliased summary + expiry (never the code) | db, notification |
| `pending list --channel cli` † | Print the full staged batch (IDs, verbs, reasons, taste diff) for out-of-band terminal inspection before confirming | — |
| `profile private-scopes add\|remove\|list SCOPE… --channel cli` † | Manage private orgs, npm scopes, and index names stored only in `radar.db` `kv['private_scopes']` | db |
| `confirm CODE` | Apply the matching unexpired batch (`actor='user'`); wrong/expired code exits 4; 5 wrong attempts cancel the batch | db, DECISIONS.md, sync |
| `decisions apply --channel cli` † | Apply staged `DECISIONS.md` edits directly from a terminal without a confirmation code | db, DECISIONS.md |
| `alert ack\|resolve\|false-positive ALERT_ID --channel cli` † | Transition an alert's lifecycle state from a terminal | db |
| `exposure PURL --versions V[,V]` / `exposure --show ALERT_ID` | Run SEC-03 read-only lockfile history and `.pth`/`sitecustomize.py` exposure check, or display an alert's exposure report | state/exposure/*.md |
| `hourly` | LLM-free hourly compromise check (invoked by the pinned inline verifier in `launchd`) | db, notifications |
| `launchd install\|uninstall\|status --channel cli` † | Install, uninstall, or inspect the `com.user.tech-radar.hourly` LaunchAgent | ~/Library/LaunchAgents, state/app |
| `app sync [--from S --channel cli]` | Verify plugin scripts against marketplace git commit and sync to `{state}/app` (`--from` is terminal-only † for development) | state/app, ~/Library/LaunchAgents |
| `notify-test` | Send one test macOS notification | — |
| `sources list\|check [--live]` | List registered sources and health status, or validate parsers (`--live` executes each enabled source once) | — |
| `search QUERY` | FTS5 search over `signals` and `digests` (output aliased for private items) | — |
| `usage record --run ID --json FILE` | Record token usage JSON for a run | db |

## Confirmation Codes (Spec 12 A41)

- **Staging only for the agent:** The agent can never directly modify `decisions`, `digest_items.feedback`, or `TASTE_PROFILE.md`. Commands callable by the agent (`decide-pending`, `feedback`, `taste propose`, plus `DECISIONS.md` edits detected during `plan-run`) only insert rows into `pending`. Content in `TASTE_PROFILE.proposed.md` is frozen with its SHA-256 digest when `taste propose` runs.
- **Out-of-band notification delivery:** Running `$R request-confirm` groups all staged rows into a batch in `confirm_batches`, generates a 6-character code from `ABCDEFGHJKMNPQRSTUVWXYZ23456789`, stores only `sha256(salt + code)` in SQLite, and sends the plaintext code **exclusively** via a macOS notification (`tech-radar code <CODE>: R-0042 trial, ...`). The code is never written to `stdout`, logs, `run_root`, or any file on disk.
- **Verification and limits:** Inspect the full staged batch in your terminal with `<RADAR_CMD> pending list --channel cli`, then reply `confirm <code>` in the T3 thread (or run `<RADAR_CMD> confirm <code>` in a terminal). Codes expire after **24 hours**, and **5 failed attempts** permanently cancel the batch (`status='cancelled'`, exit code `4`).

## Data Locations & Sync-Folder Access Rules

| Location | Default Path | Env Override | Contents |
|---|---|---|---|
| **State dir** (`0700`) | `~/.local/share/tech-radar` | `TECH_RADAR_STATE` | `radar.db` (`0600`), `cache.db` (`0600`, 14d HTTP cache), `app/` (verified script copy + `MANIFEST.sha256`), `exposure/<alert_id>.md`, `audit/`, `wafer_cache/`, `project-alerts.json`, `run.lock`, `hourly.lock` |
| **Run root** (`0700`) | `~/.local/share/tech-radar-run` | `TECH_RADAR_RUN_ROOT` | T3 project root: `RADAR_CMD`, `.claude/settings.json` (when enforcement is honoured), `runs/<run_id>/` (`brief/*`, `shortlist.json`, `verdicts.json`, `digest.md`, aliased `radar.html`), `runs/bootstrap/` |
| **Sync dir** (`0700`) | `~/SynologyDrive/ActiveProjects/personal/tech-radar-data` | `TECH_RADAR_SYNC` | `TASTE_PROFILE.md`, `TASTE_PROFILE.proposed.md`, `WATCHLIST.md`, `TECH_RADAR.md`, `INVENTORY.md`, `DECISIONS.md`, `radar.byor.json`, `radar.html`, `radar.snapshot.db`, `project-alerts.json` |
| **Logs dir** (`0700`) | `~/.local/state/tech-radar` | `TECH_RADAR_LOGS` | `hourly.out.log`, `hourly.err.log` |
| **LaunchAgents** (`0700`) | `~/Library/LaunchAgents` | Derived from `TECH_RADAR_HOME` | `com.user.tech-radar.hourly.plist` (`0600`, `fsguard` carve-out) |
| **Project roots** | `~/SynologyDrive/ActiveProjects` (+ launchd/dotfiles roots) | `TECH_RADAR_PROJECT_ROOTS` | Scanned read-only for active git repositories and manifests |

**Which sync files the agent can read (Spec 12 A8, A25):**
- **Agent-readable in `{sync}`:** `TASTE_PROFILE.md`, `WATCHLIST.md`, `TECH_RADAR.md`, and `TASTE_PROFILE.proposed.md` (and the agent may `Write` only `TASTE_PROFILE.proposed.md` plus `{run_root}/runs/**`).
- **Denied to the agent:** `INVENTORY.md`, `DECISIONS.md`, `radar.snapshot.db`, `project-alerts.json`, `radar.byor.json`, and `radar.html` in `{sync}`, plus everything under `{state}` (`~/.local/share/tech-radar/**`). `INVENTORY.md` and `DECISIONS.md` contain real private repository/package names and the `private-project-N` alias map; all files under `{run_root}` and `TECH_RADAR.md` replace private projects with `private-project-N` aliases and omit private package names.

## Hourly Compromise Check (`launchd`)

`bootstrap init` automatically syncs `{state}/app` and installs `~/Library/LaunchAgents/com.user.tech-radar.hourly.plist` (`StartInterval = 3600`). To manage the hourly job manually from an interactive terminal:

```bash
# Check launchd status and pinned manifest hash
/opt/homebrew/bin/uv run --script --locked --isolated ~/.local/share/tech-radar/app/radar.py launchd status --channel cli

# Reinstall or update the LaunchAgent
/opt/homebrew/bin/uv run --script --locked --isolated ~/.local/share/tech-radar/app/radar.py launchd install --channel cli

# Uninstall the LaunchAgent
/opt/homebrew/bin/uv run --script --locked --isolated ~/.local/share/tech-radar/app/radar.py launchd uninstall --channel cli
```

## Keychain Credentials Guide

Keyed sources (currently `producthunt`) read API tokens from the macOS Keychain under service `tech-radar`:

```bash
# Check which keyed sources are configured
/opt/homebrew/bin/uv run --script --locked --isolated ~/.local/share/tech-radar/app/radar.py credentials status

# Print setup instructions for Product Hunt
/opt/homebrew/bin/uv run --script --locked --isolated ~/.local/share/tech-radar/app/radar.py credentials guide producthunt

# Add or update the Product Hunt token interactively in your terminal (prompts for secret)
security add-generic-password -U -s tech-radar -a producthunt -w
```

Tokens are read via `/usr/bin/security find-generic-password -s tech-radar -a <account> -w` into collector process memory only; they are never logged, written to disk, or passed into `reddit_pull.py`.

## Reddit ToS Note

Daily Reddit collection (`community.reddit_top`) runs `scripts/reddit_pull.py` in an isolated subprocess (`wafer-py==0.7.7` without `[browser]`, restricted env `{PATH, HOME, TECH_RADAR_STATE}`, `follow_redirects=False`, `rate_limit=6.0`, `rate_jitter=0.2`, 540 s internal deadline, 600 s subprocess timeout) to fetch `top.json?t=day&limit=25&raw_json=1` across 25 configured subreddits; weekly practitioner deep-dives use `fetchaller-mcp` (`browse_reddit`, `search_reddit`). Unauthenticated Reddit scraping carries Terms of Service and rate-limiting/blocking exposure, which is accepted for personal use.

## Test-Only Environment Hooks

Two environment variables exist strictly for offline subprocess and pytest suites:

- `TECH_RADAR_REPLAY_DIR`: `os.pathsep`-separated list of fixture directories for `ReplayHttp` and `ReplayProcs` (disables live network requests; passes `git` through `procs.git` hardening).
- `TECH_RADAR_NOTIFY_SINK`: File path where `notify.send` appends notification text instead of calling `terminal-notifier` or `osascript`. Honoured **only** when `TECH_RADAR_REPLAY_DIR` is also set.

**Safety refusal on the real installation:** `config.test_hooks_safe()` refuses both variables (exiting `4` before any command handler runs) unless `TECH_RADAR_STATE` resolves outside `~/.local/share/tech-radar` **and** `TECH_RADAR_RUN_ROOT` resolves outside `~/.local/share/tech-radar-run`. They cannot be used to divert or capture confirmation codes on the real installation.

## Residual Risks (Spec 06, Spec 12 A25 & A34)

- **ToS exposure for Reddit scraping:** Unauthenticated Reddit access via `wafer-py` (`reddit_pull.py`) and `fetchaller-mcp` carries Reddit Terms of Service and rate-limit/IP-block risk (accepted).
- **T3 scheduled-run permission enforcement (`ENFORCEMENT_HONOURED: no`):** Empirical probing in Task 1 (`spike-results.md ## T3 enforcement`) showed that T3 Code launches scheduled threads on `claudeAgent_bifrost` / `gemini-4-argon` with `--permission-mode auto`, which overrides `defaultMode: "dontAsk"` in `{run_root}/.claude/settings.json` for unlisted tools and paths not in `permissions.deny` (while explicit `permissions.deny` entries are still enforced by settings). Because T3 does not honour `dontAsk` allowlist confinement (`ENFORCEMENT_HONOURED = False`), `bootstrap lockdown` reports `enforcement: not honoured` and writes nothing; an injected run can use any tool the session allows, bounded only by `dev-guard` hooks and skill instructions (accepted by user). All decision, feedback, and taste-profile changes remain protected because they can only be staged by the agent and require a 6-character confirmation code delivered exclusively via macOS notification (`confirm CODE`, Spec 12 A41), and all direct state-mutating CLI commands require `--channel cli` plus an interactive TTY.
- **`dev-guard` `"ask"` behavior in unattended runs (`ASK_OUTCOME: auto-allow`):** Empirical probing in Task 1 (`spike-results.md ## T3 enforcement`, call `(d)`) recorded `ASK_OUTCOME: auto-allow` — when a `dev-guard` PreToolUse rule returns `permissionDecision: "ask"`, T3's `--permission-mode auto` auto-approves the prompt without user interaction. Consequently, `dev-guard` `ask` rules provide no protection for unattended scheduled runs (only `dev-guard` hard `block` rules fail closed). Additionally, until the local `dev-guard@personal-claude-marketplace` plugin is updated to `>=1.71.1` after merge, the cached `v1.71.0` comment-narration guard will block `Write` calls to `verdicts.json` or `shortlist.json` if `"https://..."` appears on the same line as words like `"Supersedes"` or `"Phase 2"`.
- **LLM provider & `WebFetch` exposure:** Gemini (`gemini-4-argon` via Bifrost) sees the run brief, which contains only `public` names and `private-project-N` aliases (private packages are counted only, never named — Spec 12 A8). Because `WebFetch` and `WebSearch` remain available for candidate evaluation, a prompt-injected agent could send brief content (public data and aliases) to arbitrary URLs.
- **Hourly app tampering and same-user malware:** Tampering inside `{state}/app` after sync is detected by the inline pinned verifier (`verify_app.py` embedded in the `launchd` plist), and `app sync` verifies plugin files against the installed git commit in the marketplace clone (falling back to clone `HEAD` when the clone is shallow and the plugin version matches, or skipping with `app sync skipped: provenance unavailable`). Same-user malware running under your macOS UID can still edit the `launchd` plist, the marketplace clone, or `uv`'s caches.
- **macOS Keychain access by same-user processes:** Same-user malware can read Keychain items under service `tech-radar` whose ACL trusts `/usr/bin/security`.
- **`gh` CLI write-capable login:** `gh` uses the user's normal write-capable GitHub login; write actions are mitigated only by `procs.gh` GET-only REST and read-only GraphQL enforcement in code.
