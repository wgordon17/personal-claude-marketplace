# github-mcp

GitHub MCP server plugin for Claude Code. Provides full GitHub API access via the Model Context Protocol, enabling Claude to manage PRs, issues, actions, code security, and more without relying on the `gh` CLI.

## Requirements

Set the `GITHUB_PERSONAL_ACCESS_TOKEN` environment variable to a GitHub Personal Access Token with appropriate scopes before starting Claude Code:

```bash
export GITHUB_PERSONAL_ACCESS_TOKEN=ghp_...
```

The token needs scopes matching the toolsets you want to use (e.g., `repo`, `workflow`, `read:org`).

## MCP Server

Connects to `https://api.githubcopilot.com/mcp/` using HTTP transport.

## Enabled Toolsets

All toolsets are enabled via the `X-MCP-Toolsets` header:

| Toolset | Description |
|---------|-------------|
| `default` | Core GitHub operations: repos, PRs, issues, code search |
| `actions` | GitHub Actions: workflow runs, logs, artifacts |
| `orgs` | Organization management |
| `labels` | Issue and PR labels |
| `notifications` | GitHub notifications |
| `discussions` | Repository discussions |
| `gists` | GitHub Gists |
| `projects` | GitHub Projects (v2) |
| `code_security` | Code scanning alerts, secret scanning |
| `secret_protection` | Secret scanning and push protection |
| `dependabot` | Dependabot alerts and security updates |
| `security_advisories` | Repository security advisories |
| `github_support_docs_search` | Search GitHub documentation |

## Usage

Claude Code will automatically connect to the MCP server at SessionStart. MCP tools are available as `mcp__github__<tool_name>`.

Prefer MCP tools over `gh` CLI for all GitHub operations. Use `gh` CLI only for operations the MCP server does not support (e.g., `gh pr checks --watch`, `gh pr merge`).

For file contents from `raw.githubusercontent.com`, use `mcp__github__get_file_contents` instead of WebFetch.
