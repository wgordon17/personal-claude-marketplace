# fetchaller-mcp

Pinned MCP fetch/search server for domains that block Claude Code's built-in
`WebFetch`/`WebSearch` — Reddit and 15 other sites. `dev-guard`'s
`BLOCKED_URL_RULES` redirects `WebFetch`/`WebSearch` calls to these domains
toward this server's tools instead. Live-verified during planning against
real Reddit content and a `web.archive.org` snapshot (both cases built-in
`WebFetch` cannot reach at all).

Backed by [`Averyy/fetchaller-mcp`](https://github.com/Averyy/fetchaller-mcp)
(MIT), which uses `wafer-py[browser]`'s TLS-fingerprint impersonation and
Patchright (patched Playwright) browser automation to clear bot challenges.

## Setup

Requires:
- `uv` (already required by this marketplace's other Python tooling)
- A local Chrome install (`wafer-py[browser]`'s `BrowserSolver` drives real
  Chrome via Patchright — it validates the configured executable is branded
  Google Chrome, not Chromium)

Add `BROWSER_EXECUTABLE_PATH` to `~/.claude/settings.json`'s `env` block,
pointing at your Chrome binary:

```json
{
  "env": {
    "BROWSER_EXECUTABLE_PATH": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  }
}
```

Linux and Windows paths differ (e.g. `/usr/bin/google-chrome` on most Linux
distributions, `C:\Program Files\Google\Chrome\Application\chrome.exe` on
Windows) — adjust accordingly.

**WARNING — use a dedicated, logged-out Chrome install or profile, not your
daily-driver browser.** fetchaller-mcp/`wafer-py[browser]` runs real
browser-automation against `BROWSER_EXECUTABLE_PATH` with no profile
isolation option. We investigated this directly (checked fetchaller-mcp's
README at the pinned commit, its documented environment variables, and
`wafer-py`'s `BrowserSolver` API on PyPI): fetchaller-mcp exposes no
`CHROME_ARGS`-style environment variable or launch-flag passthrough, and
`wafer-py`'s `BrowserSolver` only accepts `executable_path`, `headless`,
`idle_timeout`, and `solve_timeout` — no `user_data_dir` or equivalent
profile-isolation parameter, even though the underlying Patchright library
supports one internally. There is currently no way to force an isolated
profile through this integration. If `BROWSER_EXECUTABLE_PATH` points at your
normal Chrome, this MCP server's automation runs against a profile carrying
your live cookies, active sessions, and saved passwords for every site
you're logged into — a session-hijacking-adjacent exposure if fetchaller-mcp
or its dependencies were ever compromised or misbehaved. Install a second,
separate Chrome (or Chrome for Testing) that you never log in with, and point
`BROWSER_EXECUTABLE_PATH` at that instead.

## Pinned version

Pinned to commit
[`a74501c7e`](https://github.com/Averyy/fetchaller-mcp/commit/a74501c7eac721a0604782f73ccfef5cab1975df)
(`a74501c7eac721a0604782f73ccfef5cab1975df`) rather than tracking `main`,
because:
- Solo maintainer, project is ~7 months old
- No PyPI release for the MCP server itself — only installable via
  `uvx --from git+<url>@<sha>`
- Its `[browser]` extra depends on `wafer-py`, a fast-churning,
  trust-critical dependency (TLS-fingerprint impersonation + bot-challenge
  solving) with no upper version bound in fetchaller-mcp's own
  `pyproject.toml`

`.mcp.json` additionally pins `--with "wafer-py[browser]==0.4.4"` — the exact
version fetchaller-mcp's own `uv.lock` resolves to at this commit. This
second pin is required, not redundant with the commit SHA: `uvx --from git+`
does not consult the target repository's own `uv.lock` when resolving
dependencies (confirmed by a `uv` maintainer,
[astral-sh/uv#13414](https://github.com/astral-sh/uv/issues/13414)), so
without the explicit `--with` pin, `wafer-py` would re-resolve to whatever
the latest PyPI release compatible with `>=0.4.4` is at install time —
silently drifting on every fresh `uv` cache miss despite the commit SHA
pin holding fetchaller-mcp's own code fixed.

## Manual update process

Never auto-track `main`. To bump the pin:

1. Diff `git log <old-sha>..<new-sha>` on
   [`Averyy/fetchaller-mcp`](https://github.com/Averyy/fetchaller-mcp) and
   review every commit.
2. Diff `uv.lock` between the two commits on GitHub, specifically for
   `wafer-py`/`wreq` version changes.
3. Update `.mcp.json`'s `--with "wafer-py[browser]==<version>"` to match the
   new commit's resolved `wafer-py` version.
4. Re-verify against a real Reddit URL and a `web.archive.org` URL (this
   plugin's live-test pattern from planning) against the candidate SHA
   before updating `.mcp.json`.

## Security posture

- **stdio-only.** `.mcp.json` never passes `--http` (hosted mode). A
  structural test
  (`dev-guard/tests/test_plugin_integrity.py::TestFetchallerMcpConfigSecurity`)
  asserts `--http` never appears in the checked-in args and that the git ref
  is always a full 40-character commit SHA.
- **No vendored source, no auto-sync.** The server runs directly from the
  pinned upstream commit via `uvx --from git+`; nothing is copied into this
  marketplace, and there is no scheduled job re-pinning to a newer commit.
- **Runs arbitrary third-party Python with local browser-automation
  privileges.** Mitigated by the commit-SHA pin, the `wafer-py` version pin,
  and the manual (never automatic) update process above.
- **Optional further hardening (not implemented by this plugin):** an
  OS-level network-egress restriction (a `sandbox-exec` profile on macOS, or
  a firewall rule) limiting this MCP server's outbound connections, as
  defense-in-depth on top of the mitigations above. Left undone for this
  personal, single-user setup — revisit if this ever runs in a
  shared/multi-tenant environment.

Tool capability audit: see the note added by the mcp_constants.py
allow-listing task (Task 2) — will be filled in by Task 6.
