# Standup Plugin

## Overview

The `standup` plugin collects daily engineering activity across git repositories, GitHub pull requests, Jira issues, Slack channels, Google Workspace (Calendar, Gmail, Drive), project memory, and local AI coding transcripts into a structured digest and a Slack-ready standup draft. All collectors, repository paths, and templates are driven by a user-local configuration directory outside any git repository.

## Install

```bash
claude plugin install standup@personal-claude-marketplace
```

No manual Python dependency installation step is required: plugin commands invoke `uv run --directory "${CLAUDE_PLUGIN_ROOT}" standup-tool ...`, which automatically syncs the plugin's isolated environment from the committed `uv.lock` on first use (`uv` must be installed on `PATH`).

## Configuration

## Collectors

## Onboarding

## Commands

## CLI

## Security notes
