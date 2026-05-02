# Jira Formatting Reference

Markdown style guide for writing Jira issue descriptions and comments via the MCP server.

## How Formatting Works

The Atlassian Rovo MCP server converts between formats automatically:

- **Write operations** (`createJiraIssue`, `editJiraIssue`, `addCommentToJiraIssue`): Pass `contentFormat: "markdown"` — the server converts markdown to Atlassian Document Format (ADF)
- **Read operations** (`getJiraIssue`, `searchJiraIssuesUsingJql`): Pass `responseContentFormat: "markdown"` — the server converts ADF back to markdown

Always use these parameters. Without `responseContentFormat: "markdown"`, read responses return verbose nested ADF JSON that wastes tokens.

## Write Standard Markdown

Write standard CommonMark markdown. Do NOT write Jira wiki markup (`h1.`, `*bold*`, `{{monospace}}`, `{code}`). Jira wiki markup is the wrong format — the MCP server expects standard CommonMark markdown, not Atlassian wiki syntax.

**Wrong (wiki markup — do not use):**
```
h1. Problem Statement

The {{observatorium-api}} route is misconfigured.

{code}
oc delete route observatorium-api
{code}
```

**Correct (markdown):**
```markdown
## Problem Statement

The `observatorium-api` route is misconfigured.

```bash
oc delete route observatorium-api
```
```

## What Renders Correctly

These standard markdown features convert cleanly to ADF:

| Feature | Markdown Syntax |
|---------|----------------|
| Headings | `## H2`, `### H3` (avoid `# H1` — Jira renders it too large) |
| Bold | `**bold**` |
| Italic | `*italic*` |
| Inline code | `` `code` `` |
| Fenced code blocks | ```` ```bash ... ``` ```` |
| Unordered lists | `- item` or `* item` |
| Ordered lists | `1. item` |
| Tables | Standard GFM table syntax |
| Links | `[text](url)` |
| Checkboxes | `- [ ] item` (renders as task list in ADF) |
| Blockquotes | `> text` |
| Horizontal rules | `---` |

## Issue Key Auto-Linking

Write bare issue keys (`MGMT-12345`) in descriptions and comments — Jira auto-links them in the UI. No special markdown syntax needed.

```markdown
This task depends on MGMT-12345 and blocks MGMT-67890.
```

## Code Blocks

Use fenced markdown code blocks with a language tag:

```markdown
```bash
oc get pods -n osac-namespace
```

```python
def check_status():
    return True
```
```

Avoid the old `{code}...{code}` wiki syntax — it is not valid markdown.

## Known Conversion Quirks

- **Heading levels:** Prefer `##` and `###` over `#` for section headers. Jira renders `# H1` very large (title-level), which looks oversized for body content.
- **Nested lists:** Deep nesting (3+ levels) may render inconsistently. Keep list nesting to 2 levels.
- **Tables:** Simple GFM tables convert well. Complex tables with merged cells are not supported in markdown — use lists instead.
- **Images:** Markdown image syntax (`![alt](url)`) may not render in all Jira contexts. Attach images via the Jira UI for reliability.
- **HTML tags:** Raw HTML in markdown is stripped in ADF conversion. Use only markdown syntax.
- **Line breaks:** Use blank lines between paragraphs. Single newlines may be collapsed in the ADF output.

## Description Templates

Follow the templates in `jira/reference/osac-conventions.md` for OSAC issue types. Write them in standard markdown — the MCP server handles the conversion.

## Always Pass Content Format Parameters

Both `contentFormat` and `responseContentFormat` are top-level parameters on MCP tool calls
(not nested under `fields` or `body`). Pass them on every call that reads or writes content:

| Tool | Write parameter | `contentFormat` | `responseContentFormat` |
|------|----------------|-----------------|------------------------|
| `createJiraIssue` | `description` (string) | `"markdown"` | `"markdown"` |
| `editJiraIssue` | `fields` (object with Jira field names) | `"markdown"` | `"markdown"` |
| `addCommentToJiraIssue` | `commentBody` (string) | `"markdown"` | `"markdown"` |
| `addWorklogToJiraIssue` | `commentBody` (string, optional) | `"markdown"` | — |
| `getJiraIssue` | — | — | `"markdown"` |
| `searchJiraIssuesUsingJql` | — | — | `"markdown"` |

Missing `responseContentFormat: "markdown"` returns ADF JSON — verbose, deeply nested, and token-expensive.
