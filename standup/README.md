# Standup Plugin

## Overview

The `standup` plugin collects daily engineering activity across git repositories, GitHub pull requests, Jira issues, Slack channels, Google Workspace (Calendar, Gmail, Drive), project memory, and local AI coding transcripts into a structured digest and a Slack-ready standup draft. All collectors, repository paths, and templates are driven by a user-local configuration directory outside any git repository.

## Install

```bash
claude plugin install standup@personal-claude-marketplace
```

No manual Python dependency installation step is required: plugin commands invoke `uv run --directory "${CLAUDE_PLUGIN_ROOT}" standup-tool ...`, which automatically syncs the plugin's isolated environment from the committed `uv.lock` on first use (`uv` must be installed on `PATH`).

## Configuration

Configuration and state files live in `~/.config/standup/` (or the directory set in `STANDUP_CONFIG_DIR`), which must reside outside any git worktree and is enforced at mode `0700`. Run `/standup-onboard` (or `standup-tool init`) to copy the packaged [`config.example.yaml`](config.example.yaml) and [`template.example.md`](template.example.md) into the configuration directory.

| Key | Type | Default | Purpose |
| --- | --- | --- | --- |
| `version` | `int` | required (`1`) | Schema version; must be integer `1`. |
| `identity.email` | `str \| null` | `null` | Author email for git commit filtering; when `null`, falls back to `git config user.email` in `repos[0]`. |
| `github.org` | `str` | required | GitHub organization slug (`^[A-Za-z0-9_-]+$`) used for PR search, team enrichment, and nested clone discovery. |
| `repos[].path` | `str` | required (non-empty list) | Path to a local git repository; direct non-dot child clones with an `upstream` remote matching `github.org` are auto-discovered. |
| `collectors.git.enabled` | `bool` | `true` | Enable local git commit and branch activity collection across discovered repositories. |
| `collectors.pr.enabled` | `bool` | `true` | Enable GitHub pull request and review activity collection via `gh`. |
| `collectors.jira.enabled` | `bool` | `true` | Enable Jira issue activity collection via the `jira` CLI. |
| `collectors.jira.project` | `str \| null` | required when `jira.enabled` | Jira project key (`^[A-Z][A-Z0-9_]*$`, supporting alphanumeric and underscore prefixes) queried for sprint/issue updates and matched in issue keys (`^[A-Z][A-Z0-9_]*-[0-9]+$`). |
| `collectors.jira.server` | `str \| null` | `null` | Optional `https://` Jira origin URL; when `null`, read from `~/.config/.jira/.config.yml`. |
| `collectors.calendar.enabled` | `bool` | `true` | Enable Google Calendar event collection. |
| `collectors.gmail.enabled` | `bool` | `true` | Enable Gmail message collection. |
| `collectors.drive.enabled` | `bool` | `true` | Enable Google Drive attachment/notes collection for calendar events (depends on `calendar`). |
| `collectors.slack.enabled` | `bool` | `true` | Enable Slack channel, thread, and DM activity collection. |
| `collectors.slack.workspace_url` | `str \| null` | required when `slack.enabled` | Validated `https://<workspace>.slack.com` origin URL used to bootstrap the session cookie. |
| `collectors.slack.channels` | `list[str]` | `[]` | Slack channel names scanned for recent messages and threads. |
| `collectors.slack.standup_channels` | `list[str]` | `[]` | Slack channel names searched by `standup-history` for prior posted standups. |
| `collectors.slack.self_dm_name` | `str \| null` | `null` | Display name of the user's self-DM channel; derived from identity when `null`. |
| `collectors.memory.enabled` | `bool` | `true` | Enable project-memory (`plans/` and `research/`) mtime activity collection. |
| `collectors.conversations.enabled` | `bool` | `false` | Enable local Claude/OMP coding transcript collection and Vertex AI summarization. |
| `collectors.conversations.project_match` | `list[str]` | `[]` | Explicit project-key substrings matched in addition to discovered canonical repository path keys. |
| `collectors.conversations.vertex_project` | `str \| null` | `null` | Google Cloud project ID for Vertex AI transcript compression; falls back to `ANTHROPIC_VERTEX_PROJECT_ID` / `GOOGLE_CLOUD_PROJECT`. |
| `collectors.conversations.region` | `str \| null` | `null` | Google Cloud region for Vertex AI; falls back to `CLOUD_ML_REGION`, then `us-east5`. |
| `standup.audience` | `str` | `"team"` | Free-text description of the standup audience passed to the draft agent. |
| `standup.template_file` | `str` | `"template.md"` | Bare filename inside the configuration directory containing the standup section template. |
| `ldap.enabled` | `bool` | `false` | Enable optional LDAP directory enrichment in `standup-tool enrich-team`. |
| `ldap.host` | `str \| null` | required when `ldap.enabled` | LDAP server URL; must start with `ldaps://`. |
| `ldap.base_dn` | `str \| null` | required when `ldap.enabled` | Base distinguished name for directory searches. |
| `ldap.email_domain` | `str \| null` | required when `ldap.enabled` | Email domain used to derive directory UIDs from member email addresses. |
| `ldap.auth_mech` | `str \| null` | `"GSSAPI"` | SASL mechanism passed to `ldapsearch -Y`, or `null` for simple/anonymous `-x` binds. |
| `ldap.uid_attr` | `str` | `"uid"` | Directory attribute name for user login/UID lookups. |
| `ldap.worker_id_attr` | `str \| null` | `null` | Optional directory attribute holding the employee/worker identifier for manager-chain resolution. |
| `ldap.manager_id_attr` | `str \| null` | `null` | Optional directory attribute referencing the manager's `worker_id_attr`. |
| `ldap.social_url_attr` | `str \| null` | `null` | Optional directory attribute holding GitHub profile URLs. |

The standup template file (`template.md` inside the configuration directory, initialized from [`template.example.md`](template.example.md)) uses a pinned format shared by the CLI and the `standup-draft` agent. Each section begins with a header line matching `^(:[a-z0-9_+-]+:) (.+)$` (at least two section headers are required, such as `:dart: Focus`, `:rotating_light: Blockers & Timelines`, and `:rocket: Progress`), and sections are separated by one blank line. An optional HTML comment `<!-- criteria: ... -->` immediately below a header specifies what belongs in that section and which status emojis apply (for example `:merged2:`, `:review:`, `:approved2:`, `:changes-requested:`, `:in-progress:`); the template parser skips `<!-- ... -->` comment lines when extracting headers for Slack Block Kit rendering and history detection while preserving the raw template text so the `<config>` prompt block passes the criteria comments directly to `standup-draft`. Items under each section are formatted as either plain lines or `• `-prefixed bullet lines, matching whichever style the template file uses.

## Collectors

| Collector | Config key | Output key | Dependencies / Requirements |
| --- | --- | --- | --- |
| `git` | `collectors.git.enabled` | `git_commits` | `git` CLI on `PATH`; discovered local repositories from `repos[].path`. |
| `pr` | `collectors.pr.enabled` | `prs` | Authenticated `gh` CLI (`gh auth status`) and repository remote slugs. |
| `jira` | `collectors.jira.enabled` | `jira_activity` | `jira` CLI (`ankitpokhrel/jira-cli`), `JIRA_API_TOKEN` with `/rest/api/3/search/jql` scope, and `collectors.jira.project`. (Optional Atlassian MCP server for interactive Phase 4 review.) |
| `calendar` | `collectors.calendar.enabled` | `calendar_events` | Google OAuth2 `credentials.json` and `token.json` in the config directory (`calendar.readonly` scope). |
| `gmail` | `collectors.gmail.enabled` | `emails` | Google OAuth2 `credentials.json` and `token.json` in the config directory (`gmail.readonly` scope). |
| `drive` | `collectors.drive.enabled` | `drive_docs` | Depends on `calendar` (`depends_on=("calendar",)`). Requires Google OAuth2 `credentials.json` and `token.json` (`drive.readonly` scope). Skips API calls when `calendar` returns no events; onboarding warns if `drive` is enabled while `calendar` is disabled. |
| `slack` | `collectors.slack.enabled` | `slack` | `slack-token` cookie file (or `STANDUP_SLACK_COOKIE`) and `collectors.slack.workspace_url`. |
| `memory` | `collectors.memory.enabled` | `memory_activity` | Local filesystem access to discovered repositories (no external credentials or network calls). |
| `conversations` | `collectors.conversations.enabled` | `conversations` | Local `~/.claude/projects` session transcripts (optional `~/.omp/agent/sessions`), `anthropic[vertex]`, and Google Cloud Vertex AI project (`collectors.conversations.vertex_project`, `ANTHROPIC_VERTEX_PROJECT_ID`, or `GOOGLE_CLOUD_PROJECT`). |

Every `standup-tool collect` run emits a top-level `"_status"` object as its first JSON key and a `COLLECTION SUMMARY:` line on stderr reporting each collector in one of three states:
- `ran` (`name=ran:<count>`): the collector executed and returned `<count>` items (list length, or sum of list lengths for dict outputs such as `jira_activity`). Note that `jira=ran:0` alone does not prove the `JIRA_API_TOKEN` has `/rest/api/3/search/jql` scope because `jira issue list` exits `0` with empty output when that scope is missing; `standup-tool verify` probes `/rest/api/3/search/jql` directly to catch scope errors.
- `disabled` (`name=disabled`): skipped with `count: 0` because `collectors.<name>.enabled` is `false` (`reason: "disabled in config"`), `--collectors` omitted the collector (`reason: "not selected"`), or a required dependency was disabled or not selected (`reason: "requires <dep>"`).
- `failed` (`name=failed`): an unhandled exception or upstream dependency failure (`reason: "dependency <dep> failed"`) occurred; the collector output falls back to its empty value, secret-redacted error details are recorded in `_status.<name>.reason` and stderr, and the `.last-run` marker is not advanced.

**Memory collector:** For each discovered repository, `memory` resolves the main worktree via `git -C <repo> worktree list --porcelain` (falling back to the repository path) and checks candidate directories in priority order: `hack/`, `.local/`, `scratch/`, and `.dev/` (following symlinks). The first candidate directory containing at least two of the five core project-memory files (`PROJECT.md`, `TODO.md`, `SESSIONS.md`, `NEXT.md`, `LESSONS.md`) is selected and scanned for markdown files under `plans/*.md`, `research/*.md`, and one nested component level `*/plans/*.md` and `*/research/*.md` (excluding any `done` or `archive` path segment) whose modification date falls within `[since, until]`.

## Onboarding

Run `/standup-onboard` (or `/standup:standup-onboard`) for guided interactive setup. The skill walks through seven steps without ever reading or prompting for raw secrets:

1. **Detect state:** Resolves `<config_dir>` via `standup-tool config path` and runs `standup-tool config validate`. If no config exists yet, runs `standup-tool init` to copy packaged `config.example.yaml` and `template.example.md` into `<config_dir>` with mode `0600` (never overwriting existing files). If validation already passes, jumps straight to step 6.
2. **Non-secret configuration:** Prompts one topic per `AskUserQuestion` call for `github.org`, `repos[].path` (noting auto-discovery of direct child clones matching `github.org`), `collectors.jira` (`project` and optional `server`), `collectors.slack` (`workspace_url`, `channels`, `standup_channels`), enabled collectors (`conversations` off by default with its Google Cloud Vertex AI egress disclosed; warns if `drive` is enabled without `calendar`), `standup.audience`, and optional `ldap` settings (`host`, `base_dn`, `email_domain`, attribute overrides), then writes `<config_dir>/config.yaml` and runs `standup-tool config validate`.
3. **Template customization:** Displays `<config_dir>/template.md` and offers to keep, edit, or replace it while preserving `:emoji: Header` section headers and `<!-- criteria: ... -->` comments.
4. **Permissions and credentials:** Offers to configure `~/.claude/settings.json` with `permissions.additionalDirectories` (`["<config_dir>"]`) and `permissions.deny` rules blocking `Read` on the three secret files:
   - `<config_dir>/slack-token` — single-line browser `d` session cookie captured via `umask 077; pbpaste > <config_dir>/slack-token; pbcopy < /dev/null` in the user's own terminal.
   - `<config_dir>/credentials.json` — Google OAuth2 Desktop client secrets (`0600`).
   - `<config_dir>/token.json` — Google OAuth2 refresh/access token created by running `uv run --directory "<plugin_root>" standup-tool google-auth` in the user's own terminal (required because `google-auth` prints an authorization URL to open in a browser).
   Also guides `jira init` + shell-profile `JIRA_API_TOKEN` setup and `gh auth login`.
5. **Team map:** Runs `standup-tool enrich-team` to populate `<config_dir>/team-members.yaml` from GitHub org members, optional LDAP, and Slack users.
6. **Verification (`standup-tool verify`):** Runs all 11 checks (`git`, `pr`, `jira`, `calendar`, `gmail`, `drive`, `slack`, `memory`, `conversations`, `ldap`, `permissions`, or a single check via `--only <name>`) and prints a `check | status | hint` table (`ok`, `missing`, `failed`, `disabled`) with secret-redacted hints. Missing `permissions` warns with a copy-paste settings snippet without failing the overall exit code unless `--only permissions` is passed.
7. **Dry run:** Runs `standup-tool collect --since YYYY-MM-DD --no-marker` for a 3-day window into `<config_dir>/.collect-output.json` (`0600`) and reads only `._status` to confirm collector readiness.

## Commands

- **`/standup` (`standup/commands/standup.md`):** Runs the full nine-phase daily standup workflow: (1) **Preflight** (`command -v uv gh jira`, `standup-tool config validate`, `standup-tool config show`, and Atlassian MCP bootstrap via `atlassianUserInfo` and `getAccessibleAtlassianResources`); (2) **Fetch check and Collect** (optional `git fetch --all --prune --quiet` across configured `repos` with a behind-upstream and worktree count summary, `standup-tool collect` from `<config_dir>/.last-run` through `max(<since>, yesterday)` — with `collect` and `digest` clamping `--until` to `max(since, until)` on same-day re-runs — `<collector-status>` table inspection, and data-quality checks from `references/data-quality.md`); (3) **Digest** (`standup-tool digest` into `<config_dir>/.digest.json`); (4) **Jira review** (cross-referenced Jira and PR hygiene analysis from `references/jira-review.md`, wrapping Jira MCP data in `<jira-data>` tags, validating keys against `^[A-Z][A-Z0-9_]*-[0-9]+$`, and requiring per-finding `AskUserQuestion` approval before any Jira mutation or `standup-tool backlog` call); (5) **Standup draft** (`standup-tool standup-history`, the edit-debrief loop from `references/debrief.md`, and spawning `standup:standup-draft` with the `<config>` block — `audience`, `github_org`, `jira_server`, `channels`, `standup_channels`, and verbatim `template` — placed before the data-boundary instruction); (6) **PR review** (spawning `standup:standup-pr-review` with `<config>` `audience`, `github_org`, `jira_server`, `channels`, and `standup_channels` when open PRs need review); (7) **Failure resolution** (revisiting any `failed` collectors, CI failures, or MCP errors); (8) **Priorities** (spawning `standup:standup-priorities`); and (9) **Post** (verifying `.standup-draft.txt` and `.pr-review.txt` contain no `<analysis>` or `<edit_learning>` tags, confirming with the user via `AskUserQuestion`, and running `standup-tool post-standup` to the user's Slack self-DM).
- **`/standup-preview` (`standup/commands/standup-preview.md`):** Runs the read-only-for-Slack guidance workflow to answer four questions (**What needs addressing**, **What needs following up on**, **What to focus on**, and **What to chase next**). It runs **Preflight**, **Collect + Digest** with `--no-marker`, **Signals** (computing file line counts and `git config user.name` in the command and spawning `standup:standup-signals` to reconstruct and classify uncorrelated Slack DMs, emails, calendar events, Drive comments, and transcripts), **Jira review**, **Priorities**, and a consolidated **Final Report**. It omits repository fetch, `standup-history`, the edit-debrief loop, `standup:standup-draft`, `standup:standup-pr-review`, and `post-standup`, and leaves `<config_dir>/.last-run` untouched.
- **Jira MCP soft dependency (`jira@personal-claude-marketplace`):** Both `/standup` (Phase 4) and `/standup-preview` (Phase D) treat the Atlassian MCP server from `jira@personal-claude-marketplace` as a soft dependency. Command `allowed-tools` frontmatter pre-approves only the 13 read-only Atlassian MCP tools and omits all 6 write tools (`editJiraIssue`, `createJiraIssue`, `addCommentToJiraIssue`, `transitionJiraIssue`, `createIssueLink`, `addWorklogToJiraIssue`) so every Jira write requires both the per-finding `AskUserQuestion` confirmation and the harness permission prompt. When the MCP server is unavailable or unauthenticated, the Jira review phase is skipped with a recorded reason (`Jira review skipped: <reason>`) and the rest of the pipeline continues normally.

## CLI

Invoke the CLI via `uv run --directory "${CLAUDE_PLUGIN_ROOT}" standup-tool <command>`.

| command | purpose | reads | writes |
| --- | --- | --- | --- |
| `collect` | Run enabled (or `--collectors`-selected) activity collectors across the `[--since, --until]` window and emit `_status` + collector JSON. | `<config_dir>/config.yaml`, `<config_dir>/slack-token`, `<config_dir>/credentials.json`, `<config_dir>/token.json`, `~/.config/.jira/.config.yml`, local git repos, `~/.claude/projects` | `<config_dir>/.collect-output.json` (or `--output PATH` / `-`), `<config_dir>/.last-run` (when all collectors run, `--no-marker` is omitted, and none fail) |
| `digest` | Correlate raw collector JSON with team-member identities into entity-linked work units and uncorrelated signals. | `--file` (collector JSON), `<config_dir>/config.yaml`, `<config_dir>/team-members.yaml` | `<config_dir>/.digest.json` (or `--output PATH`) |
| `post-standup` | Convert the approved standup markdown draft into Slack Block Kit rich text and post it to the user's Slack self-DM. | `--file` (standup draft), `<config_dir>/config.yaml`, `<config_dir>/<template_file>`, `<config_dir>/slack-token` | Slack self-DM message via `chat.postMessage` |
| `backlog` | Move one or more Jira issue keys out of the active sprint to the backlog via the Jira Agile REST API (supports `--dry-run`). | `<config_dir>/config.yaml`, `~/.config/.jira/.config.yml`, `JIRA_API_TOKEN` | Jira Agile REST API `POST /rest/agile/1.0/backlog/issue` (unless `--dry-run`) |
| `enrich-team` | Populate `team-members.yaml` by combining GitHub org members, optional LDAP directory lookups, and Slack workspace users (supports `--dry-run`). | `<config_dir>/config.yaml`, `<config_dir>/team-members.yaml`, `<config_dir>/slack-token`, GitHub API (`gh`), LDAP (`ldapsearch`) | `<config_dir>/team-members.yaml` (unless `--dry-run`, which writes to stdout) |
| `standup-history` | Search the user's Slack self-DM and configured `standup_channels` over `--days` to pair prior drafts with published standups for edit learning. | `<config_dir>/config.yaml`, `<config_dir>/<template_file>`, `<config_dir>/slack-token` | `<config_dir>/.standup-history.json` (or `--output PATH` / `-`) |
| `config` | Inspect or validate configuration: `config show` prints redacted JSON (`require_repos=False`), `config validate` verifies schema and repo `.git` paths, and `config path` prints `<config_dir>`. | `<config_dir>/config.yaml`, configured `repos[].path` (for `validate`) | stdout (`JSON`, `ok`, or `<config_dir>` path) |
| `verify` | Run non-destructive readiness checks across the 9 collectors, `ldap`, and Claude Code `permissions` (supports `--only <name>`). | `<config_dir>/config.yaml`, `<config_dir>/*`, `~/.config/.jira/.config.yml`, `~/.claude/settings.json`, `.claude/settings.local.json` | stdout (`check \| status \| hint` table) |
| `init` | Initialize `config.yaml` and `template.md` in `<config_dir>` from packaged examples (`0600`) without overwriting existing files. | Packaged `standup_tool.examples` (`config.example.yaml`, `template.example.md`) | `<config_dir>/config.yaml`, `<config_dir>/template.md` (when absent) |
| `google-auth` | Run the interactive Google OAuth2 Desktop flow in the user's terminal to authorize Calendar, Gmail, and Drive scopes. | `<config_dir>/credentials.json`, `<config_dir>/token.json` | `<config_dir>/token.json` (`0600`) |

## Security notes

- **Directory and file permissions:** The configuration directory (`~/.config/standup/` or `STANDUP_CONFIG_DIR`) must reside outside any git worktree and is enforced at mode `0700`. All secret files (`slack-token`, `credentials.json`, `token.json`), configuration files (`config.yaml`, `template.md`, `team-members.yaml`), and state outputs (`.collect-output.json`, `.digest.json`, `.standup-history.json`, `.last-run`) are tightened or written with `O_NOFOLLOW` and mode `0600`; symlinks are refused with `ConfigError`.
- **Slack cookie-only authentication:** Slack access uses the user's own browser session `d` cookie (`slack-token` or `STANDUP_SLACK_COOKIE`) and a browser `User-Agent` to bootstrap an in-memory client token from a workspace the user can already open in a browser. Automatic HTTP redirects are disabled: workspace bootstrap allows at most one redirect whose resolved origin matches `https://*.slack.com`, and all Slack API calls run through `safe_get` on `https://slack.com` / `https://*.slack.com` with `allow_redirects=False`. Using a browser session cookie for automated API access may be contrary to Slack's terms of service for some workspaces.
- **Data egress and AI model providers:** The `conversations` collector is disabled by default (`collectors.conversations.enabled: false`); when enabled, it sends secret-redacted local Claude/OMP coding session text to Google Cloud Vertex AI (`AsyncAnthropicVertex` in the configured `vertex_project` and `region`) for summarization. Furthermore, the `.digest.json` output assembled from all enabled collectors (including Gmail messages, Slack DMs and threads, Google Drive notes, and Jira issues) is read by the standup agents and therefore sent to the active Claude Code model provider.
- **Untrusted content and `Bash`:** `/standup` and `/standup-preview` ingest Slack, email, Jira, and Drive text written by other people, so their `allowed-tools` frontmatter pre-approves `Bash` only for the commands they run (`uv run --directory`, `chmod 600`, `jq`, `git -C`, `gh pr checks`, `gh run view`, `command -v`, `test -f`); any other shell command prompts for approval. Redaction (Slack, Atlassian, Google, GitHub, and AWS tokens, API keys, JWTs, PEM private keys, and `password=`-style assignments) is applied to collector output, session transcripts, and error text, but it is pattern-based and not exhaustive. All Slack requests use `https` on `slack.com`, never follow redirects, and send the `d` cookie with `Secure`; Google API and Slack requests have bounded timeouts.
- **Secret isolation:** Secrets are never requested via chat prompts, never passed on command lines, and redacted from collector failure reasons, HTTP error outputs, and logs. `standup-tool verify` inspects `~/.claude/settings.json` and `.claude/settings.local.json` for `permissions.deny` rules blocking `Read` on `slack-token`, `credentials.json`, and `token.json`.

