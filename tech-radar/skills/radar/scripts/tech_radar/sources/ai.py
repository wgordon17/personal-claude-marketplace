"""AI vendor and ecosystem sources: Claude Code, Anthropic, harnesses, skills, MCP, models, news."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from collections.abc import Mapping
from datetime import timedelta
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any

from .. import sanitize
from ..errors import FetchError, RadarError
from .base import (
    FetchMeta,
    RawSignal,
    SourceSpec,
    infer_github_entity_hint,
    normalize_timestamp,
    parse_feed_bytes,
)
from .community import parse_html_directory
from .github import decode_github_contents, parse_gh_releases

if TYPE_CHECKING:
    from ..context import Ctx

PRICING_KEYWORDS_RE = re.compile(
    r"\b(?:usage limit|rate limit|weekly limit|quota|plan|pricing"
    r"|max plan|pro plan|promo|free|discount|credits)\b",
    re.IGNORECASE,
)

_CHANGELOG_SECTION_RE = re.compile(r"^#{1,3}\s+(.+?)\s*$", re.MULTILINE)

ANTHROPIC_NEWS_URLS: tuple[str, ...] = (
    "https://www.anthropic.com/news",
    "https://docs.anthropic.com/en/release-notes/overview",
)

HARNESS_GH_REPOS: tuple[str, ...] = (
    "openai/codex",
    "google-gemini/gemini-cli",
    "sst/opencode",
)

HARNESS_HTML_URLS: tuple[str, ...] = (
    "https://cursor.com/changelog",
    "https://ampcode.com/news",
)

PLUGIN_DIRECTORY_URLS: tuple[str, ...] = (
    "https://www.claudepluginhub.com/",
    "https://awesomeclaudeplugins.com/",
)


def is_pricing_text(text: str) -> bool:
    """Return True if text matches Spec 07 §5 subscription/pricing keywords."""
    return bool(PRICING_KEYWORDS_RE.search(text))


def parse_claude_code_changelog(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse `repos/anthropics/claude-code/contents/CHANGELOG.md` JSON into `RawSignal`s."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid claude_code_changelog JSON: {exc}") from exc

    if not isinstance(obj, Mapping):
        raise FetchError("claude_code_changelog payload must be a JSON object")

    sha = str(obj.get("sha") or "head").strip()
    html_url = str(
        obj.get("html_url") or "https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md"
    ).strip()
    decoded = decode_github_contents(obj)
    if not decoded.strip():
        return []

    out: list[RawSignal] = []
    matches = list(_CHANGELOG_SECTION_RE.finditer(decoded))
    sections: list[tuple[str, str]] = []
    if matches:
        for idx, m in enumerate(matches[:10]):
            heading = m.group(1).strip()
            start = m.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(decoded)
            body = decoded[start:end].strip()
            if body:
                sections.append((heading, body))
    else:
        sections.append(("latest", decoded.strip()))

    for heading, body in sections:
        combined = f"{heading}\n{body}"
        sig_type = "pricing" if is_pricing_text(combined) else "feature"
        cleaned_body = sanitize.clean(body, limit=4000)
        ext_id = f"claude-code:{heading}:{sha[:8]}"
        out.append(
            RawSignal(
                source_id="claude_code_changelog",
                type=sig_type,
                external_id=ext_id,
                title=f"Claude Code {heading}",
                url=html_url,
                published_at=meta.fetched_at,
                untrusted_excerpt=body,
                entity_hint="gh:anthropics/claude-code",
                confidence="confirmed",
                payload={
                    "repo": "anthropics/claude-code",
                    "version": heading,
                    "sha": sha,
                    "body": cleaned_body,
                },
                dedupe_key=f"claude_code_changelog|{sig_type}|{ext_id}",
            )
        )
    return out


class _AnthropicNewsHTMLParser(HTMLParser):
    """Stdlib HTMLParser extracting news/release-note items from Anthropic HTML pages."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.items: list[tuple[str, str, str]] = []
        self._current_href: str | None = None
        self._in_heading = False
        self._in_article = False
        self._heading_buf: list[str] = []
        self._text_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        t_low = tag.lower()
        if t_low == "article":
            self._in_article = True
            self._heading_buf = []
            self._text_buf = []
        elif t_low == "a":
            href = attr_map.get("href", "").strip()
            if href and not href.startswith(("#", "javascript:", "mailto:")):
                self._current_href = urllib.parse.urljoin(self.base_url, href)
        elif t_low in ("h1", "h2", "h3", "h4"):
            self._in_heading = True
            self._heading_buf = []

    def handle_endtag(self, tag: str) -> None:
        t_low = tag.lower()
        if t_low in ("h1", "h2", "h3", "h4") and self._in_heading:
            self._in_heading = False
            heading_txt = " ".join("".join(self._heading_buf).split()).strip()
            if heading_txt and len(heading_txt) >= 4:
                url = self._current_href or self.base_url
                self.items.append((heading_txt, url, heading_txt))
        elif t_low == "article" and self._in_article:
            self._in_article = False
            summary_txt = " ".join("".join(self._text_buf).split()).strip()
            if summary_txt and self.items:
                last_title, last_url, _ = self.items[-1]
                self.items[-1] = (last_title, last_url, summary_txt)

    def handle_data(self, data: str) -> None:
        if self._in_heading:
            self._heading_buf.append(data)
        if self._in_article:
            self._text_buf.append(data)


def parse_anthropic_news(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse Anthropic news / release-notes HTML via stdlib `html.parser` into `RawSignal`s."""
    text = data.decode("utf-8", errors="ignore")
    if not text.strip():
        return []

    parser = _AnthropicNewsHTMLParser(meta.url or "https://www.anthropic.com/news")
    parser.feed(text)

    out: list[RawSignal] = []
    seen: set[str] = set()
    for title, url, excerpt in parser.items:
        clean_t = sanitize.clean(title, limit=200)
        if not clean_t or clean_t.lower() in ("newsroom", "anthropic", "navigation", "menu"):
            continue
        key = f"{clean_t}|{url}"
        if key in seen:
            continue
        seen.add(key)
        combined = f"{clean_t} {excerpt}"
        sig_type = "pricing" if is_pricing_text(combined) else "feature"
        ext_id = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        out.append(
            RawSignal(
                source_id="anthropic_news",
                type=sig_type,
                external_id=ext_id,
                title=clean_t,
                url=url,
                published_at=meta.fetched_at,
                untrusted_excerpt=excerpt or clean_t,
                entity_hint="gh:anthropics/claude-code",
                confidence="confirmed",
                payload={"body": sanitize.clean(excerpt or clean_t, limit=2000)},
                dedupe_key=f"anthropic_news|{sig_type}|{ext_id}",
            )
        )
    return out


def _run_anthropic_news(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    all_signals: list[RawSignal] = []
    errors: list[str] = []
    for url in ANTHROPIC_NEWS_URLS:
        try:
            resp = ctx.http.fetch(url, ttl_hours=spec.ttl_hours)
            meta = FetchMeta(
                url=resp.url,
                fetched_at=ctx.now_iso,
                status=resp.status,
                headers=resp.headers,
                source_id=spec.id,
            )
            all_signals.extend(parse_anthropic_news(resp.body, meta))
        except RadarError as exc:
            errors.append(str(exc))
    if not all_signals and errors:
        raise FetchError("; ".join(errors))
    return all_signals


def parse_harness_changelogs(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse AI harness changelogs from GitHub releases JSON or HTML changelog pages."""
    stripped = data.lstrip()
    if stripped.startswith(b"["):
        rels = parse_gh_releases(data, meta)
        out: list[RawSignal] = []
        for sig in rels:
            sig.source_id = "harness_changelogs"
            sig.type = "technique"
            sig.dedupe_key = f"harness_changelogs|technique|{sig.external_id}"
            out.append(sig)
        return out

    html_sigs = parse_html_directory(
        data,
        meta,
        source_id="harness_changelogs",
        base_url=meta.url or "https://cursor.com/changelog",
        ignore_titles=frozenset({"changelog", "cursor", "amp", "news", "download"}),
    )
    for sig in html_sigs:
        sig.type = "technique"
        sig.dedupe_key = f"harness_changelogs|technique|{sig.external_id}"
    return html_sigs


def _run_harness_changelogs(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    out: list[RawSignal] = []
    seen: set[str] = set()
    errors: list[str] = []

    repos = list(spec.params.get("repos") or HARNESS_GH_REPOS)
    # Include watchlist repos that look like AI harnesses (Spec 12 A22)
    wl_rows = ctx.db.execute(
        "SELECT DISTINCT e.repo FROM entities e JOIN usages u ON u.entity_id = e.entity_id "
        "WHERE u.context = 'interest:watchlist' AND e.visibility = 'public' AND e.repo IS NOT NULL"
    ).fetchall()
    for r in wl_rows:
        slug = str(r["repo"] or "").strip().lower()
        if "/" in slug and slug not in repos:
            repos.append(slug)

    for repo_slug in repos:
        endpoint = f"repos/{repo_slug}/releases?per_page=5"
        try:
            res = ctx.gh.api(endpoint)
            if res.returncode == 0 and res.stdout.strip():
                meta = FetchMeta(
                    url=endpoint,
                    fetched_at=ctx.now_iso,
                    status=200,
                    params={"repo": repo_slug},
                    source_id=spec.id,
                    entity_id=f"gh:{repo_slug}",
                )
                for sig in parse_harness_changelogs(res.stdout.encode("utf-8"), meta):
                    if sig.external_id not in seen:
                        seen.add(sig.external_id)
                        out.append(sig)
        except RadarError as exc:
            errors.append(str(exc))

    for url in spec.params.get("urls") or HARNESS_HTML_URLS:
        try:
            resp = ctx.http.fetch(url, ttl_hours=spec.ttl_hours)
            meta = FetchMeta(
                url=resp.url,
                fetched_at=ctx.now_iso,
                status=resp.status,
                headers=resp.headers,
                source_id=spec.id,
            )
            for sig in parse_harness_changelogs(resp.body, meta):
                if sig.external_id not in seen:
                    seen.add(sig.external_id)
                    out.append(sig)
        except RadarError as exc:
            errors.append(str(exc))

    if not out and errors:
        raise FetchError("; ".join(errors))
    return out


def parse_skills_sh(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse `https://skills.sh/` leaderboard HTML into horizon `RawSignal`s."""
    return parse_html_directory(
        data,
        meta,
        source_id="skills_sh",
        base_url="https://skills.sh/",
        ignore_titles=frozenset({"the agent skills directory", "skills.sh", "browse"}),
    )


def parse_plugin_directories(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse Claude plugin directory HTML pages into horizon `RawSignal`s."""
    return parse_html_directory(
        data,
        meta,
        source_id="plugin_directories",
        base_url=meta.url or "https://www.claudepluginhub.com/",
        ignore_titles=frozenset(
            {
                "claudepluginhub",
                "awesome claude plugins",
                "submit plugin",
                "browse plugins",
            }
        ),
    )


def _run_plugin_directories(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    out: list[RawSignal] = []
    seen: set[str] = set()
    errors: list[str] = []
    for url in spec.params.get("urls") or PLUGIN_DIRECTORY_URLS:
        try:
            resp = ctx.http.fetch(url, ttl_hours=spec.ttl_hours)
            meta = FetchMeta(
                url=resp.url,
                fetched_at=ctx.now_iso,
                status=resp.status,
                headers=resp.headers,
                source_id=spec.id,
            )
            for sig in parse_plugin_directories(resp.body, meta):
                if sig.external_id not in seen:
                    seen.add(sig.external_id)
                    out.append(sig)
        except RadarError as exc:
            errors.append(str(exc))
    if not out and errors:
        raise FetchError("; ".join(errors))
    return out


def parse_mcp_registry(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse `https://registry.modelcontextprotocol.io/v0.1/servers` JSON into `RawSignal`s."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid mcp_registry JSON: {exc}") from exc

    if not isinstance(obj, Mapping):
        raise FetchError("mcp_registry payload must be a JSON object")

    servers = obj.get("servers")
    if not isinstance(servers, list):
        return []

    out: list[RawSignal] = []
    for entry in servers:
        if not isinstance(entry, Mapping):
            continue
        raw_srv = entry.get("server")
        srv: Mapping[str, Any] = raw_srv if isinstance(raw_srv, Mapping) else entry
        raw_meta = entry.get("_meta")
        meta_obj: Mapping[str, Any] = raw_meta if isinstance(raw_meta, Mapping) else {}
        name = str(srv.get("name") or srv.get("id") or "").strip()
        if not name:
            continue
        desc = str(srv.get("description") or name).strip()
        ver = str(srv.get("version") or "").strip()
        repo_obj = srv.get("repository")
        repo_url = (
            str(repo_obj.get("url") or "") if isinstance(repo_obj, Mapping) else str(repo_obj or "")
        ).strip()
        official_meta = meta_obj.get("io.modelcontextprotocol.registry/official")
        official_updated = (
            official_meta.get("updatedAt") if isinstance(official_meta, Mapping) else None
        )
        pub = normalize_timestamp(
            srv.get("updated_at") or srv.get("published_at") or official_updated
        )
        hint = infer_github_entity_hint(repo_url) or f"mcp:{name}"
        title = f"MCP server {name} ({ver}): {desc}" if ver else f"MCP server {name}: {desc}"
        out.append(
            RawSignal(
                source_id="mcp_registry",
                type="horizon",
                external_id=f"{name}:{ver}" if ver else name,
                title=title,
                url=repo_url or f"https://registry.modelcontextprotocol.io/v0.1/servers/{name}",
                published_at=pub or meta.fetched_at,
                untrusted_excerpt=desc,
                entity_hint=hint,
                confidence="confirmed",
                payload={"name": name, "version": ver or None, "repository_url": repo_url or None},
                dedupe_key=f"mcp_registry|horizon|{name}",
            )
        )
    return out


def _run_mcp_registry(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    since_iso = (ctx.now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (
        "https://registry.modelcontextprotocol.io/v0.1/servers"
        f"?updated_since={urllib.parse.quote(since_iso, safe='')}&version=latest"
    )
    resp = ctx.http.fetch(url, ttl_hours=spec.ttl_hours)
    meta = FetchMeta(
        url=resp.url,
        fetched_at=ctx.now_iso,
        status=resp.status,
        headers=resp.headers,
        source_id=spec.id,
    )
    return parse_mcp_registry(resp.body, meta)


def parse_models_dev(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse `https://models.dev/api.json` provider/model catalog into `RawSignal`s."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid models_dev JSON: {exc}") from exc

    if not isinstance(obj, Mapping):
        raise FetchError("models_dev payload must be a JSON object")

    out: list[RawSignal] = []
    for prov_id, prov_val in obj.items():
        if not isinstance(prov_val, Mapping):
            continue
        prov_name = str(prov_val.get("name") or prov_id).strip()
        models_tbl = prov_val.get("models")
        if not isinstance(models_tbl, Mapping):
            continue
        for model_id, m_val in models_tbl.items():
            if not isinstance(m_val, Mapping):
                continue
            m_name = str(m_val.get("name") or model_id).strip()
            raw_cost = m_val.get("cost")
            cost_obj: dict[str, Any] = dict(raw_cost) if isinstance(raw_cost, Mapping) else {}
            raw_limit = m_val.get("limit")
            limit_obj: dict[str, Any] = dict(raw_limit) if isinstance(raw_limit, Mapping) else {}
            rel_date = normalize_timestamp(m_val.get("release_date")) or meta.fetched_at
            ext_id = f"{prov_id}/{model_id}"
            in_cost = cost_obj.get("input")
            out_cost = cost_obj.get("output")
            ctx_len = limit_obj.get("context")
            desc = (
                f"{prov_name} {m_name} (context={ctx_len}, cost input={in_cost} output={out_cost})"
            )
            sig_type = "pricing" if (in_cost is not None or out_cost is not None) else "horizon"
            out.append(
                RawSignal(
                    source_id="models_dev",
                    type=sig_type,
                    external_id=ext_id,
                    title=f"{prov_name}: {m_name}",
                    url=str(prov_val.get("doc") or "https://models.dev/").strip(),
                    published_at=rel_date,
                    untrusted_excerpt=desc,
                    entity_hint=f"model:{model_id}",
                    confidence="confirmed",
                    payload={
                        "provider": prov_id,
                        "model_id": model_id,
                        "cost": cost_obj,
                        "limit": limit_obj,
                    },
                    dedupe_key=f"models_dev|{sig_type}|{ext_id}",
                )
            )
            if len(out) >= 50:
                return out
    return out


def parse_openrouter_models(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse `https://openrouter.ai/api/v1/models` JSON into `RawSignal`s."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid openrouter_models JSON: {exc}") from exc

    if not isinstance(obj, Mapping):
        raise FetchError("openrouter_models payload must be a JSON object")

    arr = obj.get("data")
    if not isinstance(arr, list):
        return []

    out: list[RawSignal] = []
    for item in arr[:50]:
        if not isinstance(item, Mapping):
            continue
        mod_id = str(item.get("id") or "").strip()
        if not mod_id:
            continue
        name = str(item.get("name") or mod_id).strip()
        desc = str(item.get("description") or name).strip()
        created = normalize_timestamp(item.get("created")) or meta.fetched_at
        raw_pricing = item.get("pricing")
        pricing: dict[str, Any] = dict(raw_pricing) if isinstance(raw_pricing, Mapping) else {}
        ctx_len = int(item.get("context_length") or 0)
        sig_type = "pricing" if is_pricing_text(f"{name} {desc}") else "horizon"
        out.append(
            RawSignal(
                source_id="openrouter_models",
                type=sig_type,
                external_id=mod_id,
                title=f"OpenRouter model: {name}",
                url=f"https://openrouter.ai/{mod_id}",
                published_at=created,
                untrusted_excerpt=desc,
                entity_hint=f"model:{mod_id.split('/')[-1]}",
                metrics={"context_length": ctx_len},
                confidence="confirmed",
                payload={"id": mod_id, "pricing": pricing, "context_length": ctx_len},
                dedupe_key=f"openrouter_models|{sig_type}|{mod_id}",
            )
        )
    return out


def _parse_ai_vendor_feed(data: bytes, meta: FetchMeta, *, source_id: str) -> list[RawSignal]:
    sigs = parse_feed_bytes(data, meta, source_id=source_id, signal_type="horizon")
    for sig in sigs:
        if is_pricing_text(f"{sig.title} {sig.untrusted_excerpt}"):
            sig.type = "pricing"
            sig.payload["category"] = "subscription_or_promo"
        sig.dedupe_key = f"{source_id}|{sig.type}|{sig.external_id}"
    return sigs


def parse_openai_news(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse `https://openai.com/news/rss.xml` into pricing or horizon `RawSignal`s (A15)."""
    return _parse_ai_vendor_feed(data, meta, source_id="openai_news")


def parse_google_ai_blog(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse `https://blog.google/technology/ai/rss/` into pricing or horizon `RawSignal`s (A15)."""
    return _parse_ai_vendor_feed(data, meta, source_id="google_ai_blog")


CLAUDE_CODE_CHANGELOG_SPEC = SourceSpec(
    id="claude_code_changelog",
    req=("SRC-AI-01",),
    archetype="gh_api",
    url="repos/anthropics/claude-code/contents/CHANGELOG.md",
    cadence="daily",
    hosts=("api.github.com",),
    signal_types=("feature", "pricing"),
    ttl_hours=24,
    parser=parse_claude_code_changelog,
)

ANTHROPIC_NEWS_SPEC = SourceSpec(
    id="anthropic_news",
    req=("SRC-AI-02", "GOAL-09"),
    archetype="html",
    url=ANTHROPIC_NEWS_URLS[0],
    cadence="daily",
    hosts=("www.anthropic.com", "docs.anthropic.com"),
    signal_types=("feature", "pricing"),
    ttl_hours=24,
    params={"urls": ANTHROPIC_NEWS_URLS},
    parser=parse_anthropic_news,
    runner=_run_anthropic_news,
)

HARNESS_CHANGELOGS_SPEC = SourceSpec(
    id="harness_changelogs",
    req=("SRC-AI-03",),
    archetype="html",
    url=HARNESS_HTML_URLS[0],
    cadence="weekly",
    hosts=("cursor.com", "ampcode.com"),
    signal_types=("technique", "contender"),
    ttl_hours=24 * 7,
    params={"repos": HARNESS_GH_REPOS, "urls": HARNESS_HTML_URLS},
    parser=parse_harness_changelogs,
    runner=_run_harness_changelogs,
)

SKILLS_SH_SPEC = SourceSpec(
    id="skills_sh",
    req=("SRC-AI-04",),
    archetype="html",
    url="https://skills.sh/",
    cadence="weekly",
    hosts=("skills.sh", "www.skills.sh"),
    signal_types=("horizon",),
    ttl_hours=24 * 7,
    parser=parse_skills_sh,
)

PLUGIN_DIRECTORIES_SPEC = SourceSpec(
    id="plugin_directories",
    req=("SRC-AI-09",),
    archetype="html",
    url=PLUGIN_DIRECTORY_URLS[0],
    cadence="weekly",
    hosts=("www.claudepluginhub.com", "awesomeclaudeplugins.com"),
    signal_types=("horizon",),
    ttl_hours=24 * 7,
    params={"urls": PLUGIN_DIRECTORY_URLS},
    parser=parse_plugin_directories,
    runner=_run_plugin_directories,
)

MCP_REGISTRY_SPEC = SourceSpec(
    id="mcp_registry",
    req=("SRC-AI-10",),
    archetype="json_api",
    url="https://registry.modelcontextprotocol.io/v0.1/servers?version=latest",
    cadence="weekly",
    hosts=("registry.modelcontextprotocol.io",),
    signal_types=("horizon",),
    ttl_hours=24 * 7,
    parser=parse_mcp_registry,
    runner=_run_mcp_registry,
)

MODELS_DEV_SPEC = SourceSpec(
    id="models_dev",
    req=("SRC-AI-12",),
    archetype="json_api",
    url="https://models.dev/api.json",
    cadence="daily",
    hosts=("models.dev",),
    signal_types=("horizon", "pricing"),
    ttl_hours=24,
    parser=parse_models_dev,
)

OPENROUTER_MODELS_SPEC = SourceSpec(
    id="openrouter_models",
    req=("SRC-AI-13",),
    archetype="json_api",
    url="https://openrouter.ai/api/v1/models",
    cadence="daily",
    hosts=("openrouter.ai",),
    signal_types=("horizon", "pricing"),
    ttl_hours=24,
    parser=parse_openrouter_models,
)

OPENAI_NEWS_SPEC = SourceSpec(
    id="openai_news",
    req=("GOAL-09",),
    archetype="feed",
    url="https://openai.com/news/rss.xml",
    cadence="daily",
    hosts=("openai.com",),
    signal_types=("pricing", "horizon"),
    ttl_hours=12,
    parser=parse_openai_news,
)

GOOGLE_AI_BLOG_SPEC = SourceSpec(
    id="google_ai_blog",
    req=("GOAL-09",),
    archetype="feed",
    url="https://blog.google/technology/ai/rss/",
    cadence="daily",
    hosts=("blog.google",),
    signal_types=("pricing", "horizon"),
    ttl_hours=12,
    parser=parse_google_ai_blog,
)

AI_SPECS: tuple[SourceSpec, ...] = (
    CLAUDE_CODE_CHANGELOG_SPEC,
    ANTHROPIC_NEWS_SPEC,
    HARNESS_CHANGELOGS_SPEC,
    SKILLS_SH_SPEC,
    PLUGIN_DIRECTORIES_SPEC,
    MCP_REGISTRY_SPEC,
    MODELS_DEV_SPEC,
    OPENROUTER_MODELS_SPEC,
    OPENAI_NEWS_SPEC,
    GOOGLE_AI_BLOG_SPEC,
)
