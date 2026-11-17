---
name: standup-onboard
description: |
  Interactive onboarding workflow to initialize, configure, and verify the standup plugin.
  Use when the user asks to "set up standup", "onboard standup", "configure standup",
  "initialize standup", or run `/standup-onboard` or `/standup:standup-onboard`.
allowed-tools: Read Write Edit Bash AskUserQuestion
---

# Standup Onboarding

> **Note:** `{skill_dir}` refers to the absolute "Base directory for this skill" path provided in your system instructions; `{plugin_root}` is two levels up (`{skill_dir}/../..`).

Interactive setup for the `standup` plugin (`/standup-onboard` or `/standup:standup-onboard`). Throughout this workflow, resolve `{plugin_root}` as the normalized absolute path two levels above `{skill_dir}` and substitute it literally as `<plugin_root>` in every command run via `Bash` or printed for the user's own terminal.

## Hard Security Rules

- Never ask for any secret, token, password, or cookie through `AskUserQuestion`.
- Never place a secret on a command line executed by `Bash` or any other tool.
- Never `Read` secret files (`<config_dir>/slack-token`, `<config_dir>/credentials.json`, `<config_dir>/token.json`) or full collector outputs containing mail/messages.

## Step 1: Detect State

1. Resolve `<plugin_root>` from `{skill_dir}/../..` and record `<config_dir>` by running:
   ```bash
   uv run --directory "<plugin_root>" standup-tool config path
   ```
2. Validate existing configuration:
   ```bash
   uv run --directory "<plugin_root>" standup-tool config validate
   ```
   - If `standup-tool config validate` exits `0` (`ok`), inform the user that `<config_dir>/config.yaml` is already valid and jump directly to **Step 6 (Verify)**.
   - If stderr begins with `No config at`, initialize the config directory from packaged examples:
     ```bash
     uv run --directory "<plugin_root>" standup-tool init
     ```
     Then continue to **Step 2**.
   - For any other validation error (including the freshly copied example config whose placeholder org and repository path fail validation), continue to **Step 2** and fix the reported keys.

## Step 2: Configure Non-Secret Settings (`AskUserQuestion`, One Topic Per Call)

Ask the user one topic per `AskUserQuestion` call:

1. **GitHub organization (`github.org`):** Organization slug matching `^[A-Za-z0-9_-]+$` used for PR search, team enrichment, and nested clone auto-discovery.
2. **Repository paths (`repos[].path`):** Free-text paths (one per line). Explain that each path must be a git clone (`~` is expanded) and that direct non-dot child git clones whose `upstream` remote matches `github.org` (such as `enhancement-proposals` inside a workspace root) are auto-discovered.
3. **Jira project and server (`collectors.jira`):** Project key matching `^[A-Z][A-Z0-9_]*$` (for example `ACME`) and optional `https://` Jira server origin URL (or leave `null` to read the server from `~/.config/.jira/.config.yml`).
4. **Slack workspace and channels (`collectors.slack`):**
   - `workspace_url`: Explain that this is the `https://<workspace>.slack.com` origin URL of the workspace the user's own browser session is logged into, because the collector reuses that session.
   - `channels`: Channel names scanned for daily activity.
   - `standup_channels`: Channel names searched by `standup-tool standup-history` for prior posted standups.
5. **Enabled collectors (`collectors.*.enabled`):** Multi-select from `git`, `pr`, `jira`, `calendar`, `gmail`, `drive`, `slack`, `memory`, and `conversations`:
   - State clearly that `conversations` is **off by default** because when enabled it sends secret-redacted local coding session transcripts to **Google Cloud Vertex AI** for summarization.
   - If `conversations` is enabled, also ask for `vertex_project`, `region` (default `us-east5`), and optional `project_match` substrings.
   - Warn if `drive` is enabled while `calendar` is disabled, because `drive` depends on `calendar` output to discover meeting attachments and notes.
6. **Standup audience (`standup.audience`):** Free-text audience description passed to the draft agent (for example `"engineering team"`).
7. **LDAP directory enrichment (`ldap`):** Ask whether to enable optional LDAP lookup during `standup-tool enrich-team`. When enabled, ask for `host` (`ldaps://...`), `base_dn`, `email_domain`, and any optional attribute overrides (`uid_attr`, `worker_id_attr`, `manager_id_attr`, `social_url_attr`, `auth_mech`).

Read `<config_dir>/config.yaml`, write the updated configuration using `Write`, then run:

```bash
uv run --directory "<plugin_root>" standup-tool config validate
```

If validation reports any error, fix the reported key and re-run `standup-tool config validate` until it prints `ok`.

## Step 3: Review Standup Template

1. `Read` `<config_dir>/template.md` and display its contents to the user.
2. Ask via `AskUserQuestion` whether to **Keep**, **Edit**, or **Replace** the template.
3. If editing or replacing, use `Edit` (or `Write`) on `<config_dir>/template.md` while preserving:
   - At least two section header lines matching `^(:[a-z0-9_+-]+:) (.+)$` (for example `:dart: Focus`, `:rotating_light: Blockers & Timelines`, `:rocket: Progress`).
   - Any `<!-- criteria: ... -->` HTML comments immediately below each header that guide item selection and PR status emojis.

## Step 4: Permissions and Credentials (One Collector at a Time)

1. **Claude Code permissions (`~/.claude/settings.json`):**
   First offer via `AskUserQuestion` to update `~/.claude/settings.json` so that:
   - `permissions.additionalDirectories` includes `<config_dir>` (allowing standup agents to write draft and state files without permission prompts).
   - `permissions.deny` includes `Read(<config_dir>/slack-token)`, `Read(<config_dir>/credentials.json)`, and `Read(<config_dir>/token.json)`.
   If the user approves, `Read` `~/.claude/settings.json` (if present) and merge those entries using `Edit` or `Write`.
2. **Per-collector credentials:**
   For each enabled collector that uses a credential file, check whether the file already exists by filename only (`test -f "<config_dir>/<name>"`, never reading its content). If it exists, ask via `AskUserQuestion` whether to **Keep existing** (default) or **Rotate**, and skip the setup command when keeping. Otherwise print the exact command for the user to run in their own terminal and wait for confirmation via `AskUserQuestion`:
   - **Slack (`<config_dir>/slack-token`):** Instruct the user to open their browser's developer tools (Application/Storage -> Cookies for their configured `https://<workspace>.slack.com` workspace), copy the value of the `d` session cookie, and run in their own terminal (with `<config_dir>` substituted literally):
     ```bash
     umask 077; pbpaste > <config_dir>/slack-token; pbcopy < /dev/null
     ```
     Explain that `pbcopy < /dev/null` immediately clears the cookie from the macOS clipboard (on Linux, use `xclip -o` / `wl-paste` and `wl-copy --clear`), and remind the user to remove the entry from any clipboard-history manager.
   - **Google Workspace (`calendar`, `gmail`, `drive`):** Instruct the user to place their OAuth Desktop client secrets file at `<config_dir>/credentials.json` (`chmod 600 <config_dir>/credentials.json`) and run in their own terminal:
     ```bash
     uv run --directory "<plugin_root>" standup-tool google-auth
     ```
     Explain that `standup-tool google-auth` must be run in the user's own terminal because it prints a browser authorization URL.
   - **Jira (`jira`):** Instruct the user to run `jira init` in their terminal and add `export JIRA_API_TOKEN=...` by opening their shell profile in an editor (never by running an `export` or `echo` command with the token on the command line, which records the token in shell history).
   - **GitHub (`pr`):** Instruct the user to run `gh auth login` in their terminal if not already authenticated.

## Step 5: Build Team Map

Run team enrichment:

```bash
uv run --directory "<plugin_root>" standup-tool enrich-team
```

Then `Read` `<config_dir>/team-members.yaml` and report how many team member entries were written.

## Step 6: Verify Setup

Run the verification suite:

```bash
uv run --directory "<plugin_root>" standup-tool verify
```

Display the `check | status | hint` table (including the `permissions` row). For any row whose status is `missing` or `failed`, present its hint and offer to re-check after the user resolves it (for example `uv run --directory "<plugin_root>" standup-tool verify --only slack` or `uv run --directory "<plugin_root>" standup-tool verify --only permissions`). Loop until all enabled checks pass or the user chooses via `AskUserQuestion` to proceed with partial data.

## Step 7: Dry-Run Collection

1. Compute the calendar date **three days ago** directly from today's date in `YYYY-MM-DD` format (do not use shell `date` flags) and run:
   ```bash
   uv run --directory "<plugin_root>" standup-tool collect --since YYYY-MM-DD --no-marker
   ```
   Output is written with mode `0600` to `<config_dir>/.collect-output.json` (never to stdout, keeping mail and messages out of the conversation).
2. Inspect **only** the `_status` summary key from `<config_dir>/.collect-output.json`:
   ```bash
   jq '._status' "<config_dir>/.collect-output.json"
   ```
3. Display the tri-state collector table (`ran`, `disabled`, `failed` with item counts) and inform the user that `/standup` and `/standup-preview` are ready to use.
