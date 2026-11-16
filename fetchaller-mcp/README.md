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
pointing at a **dedicated, separate** Chrome install (e.g. a Chrome for
Testing binary) — never your daily-driver browser (see the WARNING below):

```json
{
  "env": {
    "BROWSER_EXECUTABLE_PATH": "/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
  }
}
```

Linux and Windows paths differ (e.g. `/usr/bin/google-chrome` on most Linux
distributions, `C:\Program Files\Google\Chrome\Application\chrome.exe` on
Windows) — adjust accordingly.

`BROWSER_EXECUTABLE_PATH` is **required**, not optional. If it's left unset,
fetchaller-mcp omits the `executable_path` argument when constructing
`wafer-py`'s `BrowserSolver` (confirmed directly in fetchaller-mcp's
`server.py` at the pinned commit), and `BrowserSolver` itself then falls back
to its own OS-standard Chrome auto-detection — confirmed directly in
`wafer-py`'s own `browser/_solver.py` at the pinned `0.7.14` version
(`_browser_executable()` calls `_system_chrome_executable()` when no
executable path was configured). On macOS that fallback checks exactly
`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`. If a Chrome
install exists there — true for most users — it is used **silently**, with
no warning or error: the same daily-driver-Chrome exposure the WARNING below
describes, triggered by leaving this unset rather than by misconfiguring it.
Always set this variable explicitly.

**WARNING — use a dedicated, logged-out Chrome install or profile, not your
daily-driver browser.** fetchaller-mcp/`wafer-py[browser]` runs real
browser-automation against `BROWSER_EXECUTABLE_PATH` with no user-configurable
profile-directory option. We investigated this directly (checked
fetchaller-mcp's README at the pinned commit, its documented environment
variables, and `wafer-py`'s `BrowserSolver` API): fetchaller-mcp exposes no
`CHROME_ARGS`-style environment variable or launch-flag passthrough, and
`wafer-py`'s `BrowserSolver` only accepts `executable_path`, `headless`,
`idle_timeout`, `solve_timeout`, `egress_guard_proxy`, `proxy_server`, and
`timezone` — no `user_data_dir` parameter (though Patchright's
`chromium.launch()` creates a fresh temporary profile directory internally).
As defense-in-depth against any upstream regression that attaches to a
persistent user profile, install a second, separate Chrome (or Chrome for
Testing) that you never log in with, and point `BROWSER_EXECUTABLE_PATH` at
that instead.

## Pinned version

Pinned to
[`v3.8.12`](https://github.com/Averyy/fetchaller-mcp/commit/500078f7f349d080de1fb7f231cd5d11f3fc42d2)
rather than tracking `main`,
because:
- Solo maintainer, fast-moving scraper/WAF surface
- No PyPI release for the MCP server itself — only installable via
  `uvx --from git+<url>@<sha>`
- Depends on `wafer-py[browser]`, a fast-churning, trust-critical dependency
  (TLS-fingerprint impersonation + bot-challenge solving)

`.mcp.json` additionally pins `--with "wafer-py[browser]==0.7.14"` — the exact
version fetchaller-mcp's own `uv.lock` (and `pyproject.toml`) resolves to at
this commit. This explicit `--with` pin is kept as defense-in-depth:
`uvx --from git+` does not consult the target repository's own `uv.lock` when
resolving dependencies (confirmed by a `uv` maintainer,
[astral-sh/uv#13414](https://github.com/astral-sh/uv/issues/13414)), so if an
upstream commit ever uses a `>=` floor in `pyproject.toml` without `--with`,
`wafer-py` would silently drift on fresh `uv` cache misses despite the commit
SHA pin holding fetchaller-mcp's own code fixed.

The combined `uvx --from git+...@500078f7f --with "wafer-py[browser]==0.7.14"
fetchaller-mcp` invocation was independently live-verified (via `--help`,
Reddit reads, and `web.archive.org`) to resolve and start cleanly (2026-11-15).
This verification is also a permanent regression test —
`dev-guard/tests/test_plugin_integrity.py::TestFetchallerMcpConfigSecurity::test_live_uvx_invocation_resolves_and_starts`,
opt-in via `make test-live`.

## Manual update process

Never auto-track `main`. Renovate (`renovate.json`) checks tagged
`Averyy/fetchaller-mcp` releases (in `README.md`) and `wafer-py` releases (in
`.mcp.json` / `mcp.json`) on the first Monday of each month (`automerge: false`,
14-day release cooldown) and opens a **fail-closed audit-trigger PR** whose CI
intentionally fails (`TestFetchallerMcpConfigSecurity`) until the manual audit
below is completed. To bump the pin:

1. Diff `git log <old-sha>..<new-sha>` on
   [`Averyy/fetchaller-mcp`](https://github.com/Averyy/fetchaller-mcp) and
   review every commit.
2. Diff `uv.lock` between the two commits on GitHub, specifically for
   `wafer-py`/`wreq` version changes.
3. Align `README.md`, `.mcp.json`, and `mcp.json` to the candidate commit SHA
   and `--with "wafer-py[browser]==<version>"` matching the commit's resolved
   `wafer-py` version, and update `test_wafer_py_version_pinned` in
   `dev-guard/tests/test_plugin_integrity.py`.
4. Re-verify against a real Reddit URL and a `web.archive.org` URL (this
   plugin's live-test pattern from planning) against the candidate SHA
   before merging.
5. Redo the **Tool capability audit** below against the new commit (new or
   removed tools, changed schemas, the `fetch` parameter-name check). Update
   the audit section and `dev-guard/hooks/mcp_constants.py`'s `MCP_READ_ONLY`
   list accordingly if anything changed.

## Security posture

- **stdio-only.** `.mcp.json` never passes `--http` (hosted mode). A
  structural test
  (`dev-guard/tests/test_plugin_integrity.py::TestFetchallerMcpConfigSecurity`)
  asserts `--http` never appears in the checked-in args and that the git ref
  is always a full 40-character commit SHA.
- **No vendored source, no unattended updates.** The server runs directly from
  the pinned upstream commit via `uvx --from git+`; nothing is copied into this
  marketplace, and monthly Renovate PRs (`automerge: false`) are blocked by
  fail-closed `TestFetchallerMcpConfigSecurity` assertions until manually
  audited.
- **Runs arbitrary third-party Python with local browser-automation
  privileges.** Mitigated by the commit-SHA pin, the `wafer-py` version pin,
  and the manual (never automatic) update process above.
- **Optional further hardening (not implemented by this plugin):** an
  OS-level network-egress restriction (a `sandbox-exec` profile on macOS, or
  a firewall rule) limiting this MCP server's outbound connections, as
  defense-in-depth on top of the mitigations above. Left undone for this
  personal, single-user setup — revisit if this ever runs in a
  shared/multi-tenant environment.

**Tool capability audit** (performed at the pinned commit,
`500078f7f349d080de1fb7f231cd5d11f3fc42d2`, covering all 28 registered MCP
tools, and re-required on every future SHA bump per the manual update process
above):

- **`fetch`** is a generic authenticated HTTP client — `tools/fetch.py`
  allows both GET and POST (`ALLOWED_METHODS = {"GET", "POST"}`), up to 32
  custom headers (including `authorization`), request bodies up to 1MB, plus
  enum-validated read options (`redditComments`, `redditSort`). Because of
  `method`/`headers`/`body`, it is deliberately *not* included in
  `dev-guard`'s blanket `MCP_READ_ONLY` allow-list. Instead,
  `tool-selection-guard.py` gates each call: a plain GET with no custom headers
  or body is auto-approved (the normal blocked-domain-redirect case this plugin
  exists for), while a POST or any call carrying custom headers/body asks for
  confirmation first. **This gate depends on fetchaller's `fetch` tool using
  exactly the parameter names `method`/`headers`/`body`** — if a future commit
  renames these, the gate silently fails open (auto-approves) instead of
  failing closed. Re-verify these parameter names against `tools/fetch.py`'s
  actual signature on every SHA bump.
- **`get_unifi_manual`** (`ubiquiti/manual.py`) is restricted to
  `ui.com`/`dl.ui.com` `/qig/<slug>` installation guides, but writes
  reconstructed `.pdf`/`.png`/`.svg` files to local disk under
  `~/.local/share/fetchaller/manuals/<slug>/`. Because `MCP_READ_ONLY` covers
  side-effect-free reads with no local disk writes, it is deliberately excluded
  from `MCP_READ_ONLY`.
- **`search_oracle_jobs`** (`oracle_recruiting/employers.py`,
  `oracle_recruiting/api.py`) accepts arbitrary `http://`/`https://` careers
  URLs in its `employer` parameter and calls `session.get(careers_url)` in
  `discover_host()` without passing through `security.ssrf.check_host()`.
  Although response bodies are not returned directly unless an
  `*.fa.*.oraclecloud.com` host is matched, it is deliberately excluded from
  `MCP_READ_ONLY` so arbitrary-URL discovery cannot issue unprompted requests
  to internal/loopback hosts. (`search_uber_jobs` hardcodes `EMPLOYER = "uber"`
  and does not accept a caller URL.)
- **`search`** (`search/google.py`, `search/ddg.py`, `search/brave.py`) issues
  anonymous web search requests (`provider` in `google`/`ddg`/`brave`/`all`)
  via public Google Docs research-search JSON, DuckDuckGo HTML, and Brave
  Search (with browser-solver PoW challenge fallback). Included in
  `MCP_READ_ONLY`.
- **The remaining 24 tools** (`browse_reddit`, `search_reddit`,
  `search_marketplace`, `search_realtor`, `search_linkedin_jobs`,
  `get_linkedin_job`, `get_aliexpress_product`, `search_aliexpress`,
  `get_alibaba_product`, `search_alibaba`, `search_eightfold_jobs`,
  `search_workday_jobs`, `search_amazon_jobs`, `search_google_jobs`,
  `search_apple_jobs`, `search_meta_jobs`, `search_uber_jobs`,
  `search_gojobs`, `get_gojobs_job`, `search_indeed`, `get_indeed_job`,
  `search_jobbank`, `search_gcjobs`, `get_gcjobs_job`) are read-only at both
  the MCP schema and transport levels — `additionalProperties: False`, only
  query/filter/ID/timeout parameters, no mutation-shaped fields, and outbound
  requests restricted to hardcoded public origins or strict ATS hostname
  regexes (`*.eightfold.ai`, `*.wd<N>.myworkdayjobs.com`). GraphQL clients
  (`facebook_marketplace/graphql.py`, `meta_careers/api.py`) use hardcoded,
  non-mutating query operations rather than caller-controlled queries. All 24
  are included in `MCP_READ_ONLY`.
