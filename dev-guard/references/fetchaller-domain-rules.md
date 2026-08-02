# fetchaller domain rules

Rationale for every domain `tool-selection-guard.py`'s `BLOCKED_URL_RULES`
redirects toward the `fetchaller-mcp` plugin, so future additions follow the
same review bar: live-verify before adding when possible, and categorize by
evidence tier and by how the domain is handled.

**Evidence tiers:**
- **confirmed** — backed by a specific Claude Code GitHub issue report of
  `WebFetch` failing against the domain, found during planning research.
- **inferred** — backed only by `fetchaller-mcp` shipping a dedicated
  bypass/cleanup module or tool for that site, not an independently confirmed
  Claude Code failure report. A false positive here just costs an extra
  prompt (or, for `redirect / block` entries, redirects a request that plain
  `WebFetch` might have handled fine) — not a correctness bug.

**Categories:**
- **redirect / block** — hard block (`action="block"`, the `URLRule` default);
  `WebFetch`/curl/wget calls exit 2 and must go through fetchaller instead.
- **redirect / ask** — interactive confirmation (`action="ask"`); the user can
  approve the original call anyway, and `_check_trust` silences repeat
  prompts once trusted.
- **guidance-only with Wayback fallback** — no fetch tool, including
  fetchaller, can serve these pages at all for most URLs (login-gated). The
  guidance directs a bounded fetch → Wayback Availability API → stop-and-ask
  sequence instead of a flat refusal.

| Domain | Rule name | Evidence tier | Category |
|---|---|---|---|
| Reddit | `reddit-blocked` | confirmed | redirect / block |
| Wikipedia | `wikipedia-blocked` | confirmed | redirect / block |
| npmjs.com | `npm-blocked` | confirmed | redirect / block |
| Amazon | `amazon-blocked` | inferred | redirect / ask |
| eBay | `ebay-blocked` | inferred | redirect / ask |
| Stack Overflow / Stack Exchange | `stackoverflow-blocked` | inferred | redirect / ask |
| Hacker News | `hackernews-blocked` | inferred | redirect / ask |
| Medium | `medium-blocked` | inferred | redirect / ask |
| AliExpress | `aliexpress-blocked` | inferred | redirect / ask |
| Alibaba.com | `alibaba-blocked` | inferred | redirect / ask |
| Facebook Marketplace | `facebook-marketplace-blocked` | inferred | redirect / ask |
| Realtor.com | `realtor-blocked` | inferred | redirect / ask |
| LinkedIn (job postings, `/jobs/`) | `linkedin-jobs-blocked` | inferred | redirect / block |
| LinkedIn (general — profiles, feed, posts) | `linkedin-login-gated` | inferred | guidance-only with Wayback fallback |
| Quora | `quora-login-gated` | inferred | guidance-only with Wayback fallback |
| Twitter/X | `twitter-x-login-gated` | inferred | guidance-only with Wayback fallback |

**Note on `linkedin-jobs-blocked`'s placement:** it's evidence-tier
*inferred* (fetchaller ships `search_linkedin_jobs`/`get_linkedin_job`, but
this wasn't independently confirmed against a live LinkedIn job posting the
way Reddit and `web.archive.org` were during planning), yet it's placed in
*redirect / block* rather than *redirect / ask* alongside the other
inferred-tier domains. This is a deliberate inconsistency worth being aware
of — not something to fix retroactively.

New domains can be added later either by extending `BLOCKED_URL_RULES`
directly (following this same evidence-tier/category review), or, for a
personal addition that doesn't require a plugin release, via
`~/.claude/dev-guard.json`'s `url_rules` extension point.

`tool-selection-guard.py`'s `_WEBSEARCH_TOOL_HINTS` dict must be kept in
sync with `BLOCKED_URL_RULES` rule names: a domain added there without a
matching `_WEBSEARCH_TOOL_HINTS` entry silently falls back to the generic
`_DEFAULT_WEBSEARCH_HINT` for WebSearch guidance.
