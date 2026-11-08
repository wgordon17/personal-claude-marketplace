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
| `collectors.jira.project` | `str \| null` | required when `jira.enabled` | Jira project key (`^[A-Z][A-Z0-9_]*$`) queried for sprint and issue updates. |
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

## Onboarding

## Commands

## CLI

## Security notes
