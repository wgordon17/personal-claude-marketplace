"""Community discovery sources: HN Algolia, HN pricing, dev.to, Product Hunt, DevHunt, Reddit."""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
from collections.abc import Mapping
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import credentials, redact, sanitize
from ..errors import FetchError, RadarError
from ..inventory import classify
from .base import (
    FetchMeta,
    RawSignal,
    SourceSpec,
    infer_github_entity_hint,
    normalize_timestamp,
)

if TYPE_CHECKING:
    from ..context import Ctx

_SCRIPTS_DIR = Path(__file__).resolve().parents[2]

REDDIT_SUBREDDITS: tuple[str, ...] = (
    "ClaudeAI",
    "ClaudeCode",
    "LocalLLaMA",
    "mcp",
    "ChatGPTCoding",
    "AI_Agents",
    "cursor",
    "Python",
    "rust",
    "golang",
    "typescript",
    "commandline",
    "fishshell",
    "neovim",
    "macapps",
    "devops",
    "selfhosted",
    "netsec",
    "programming",
    "ExperiencedDevs",
    "django",
    "kubernetes",
    "LocalLLM",
    "Nix",
    "web_design",
)

HN_PRICING_PHRASES: tuple[str, ...] = (
    "free tier",
    "promo",
    "discount",
    "free for",
)

_AI_PRICING_CONTEXT_RE = re.compile(
    r"\b(?:ai|llm|llms|claude|anthropic|openai|chatgpt|gpt|gemini|deepseek|qwen|mistral"
    r"|llama|cursor|copilot|codex|windsurf|opencode|ampcode|openrouter|agent|agents"
    r"|mcp|coding|code|developer|ide|inference|token|tokens|model|models|api)\b",
    re.IGNORECASE,
)

_CONTENDER_HINT_RE = re.compile(
    r"\b(?:vs\.?|versus|alternative to|faster than|drop-in|migration from)\b",
    re.IGNORECASE,
)

_TECHNIQUE_HINT_RE = re.compile(
    r"\b(?:how we|how i|workflow|pattern|architecture|guide|lessons learned|best practices)\b",
    re.IGNORECASE,
)

_PLAIN_TERM_RE = re.compile(r"^[A-Za-z0-9._+-]{2,40}$")
_DEVTO_TAG_RE = re.compile(r"^[a-z0-9]{2,30}$")
_SUBREDDIT_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,50}$")

PRODUCTHUNT_GRAPHQL_QUERY = (
    '{ posts(topic: "developer-tools", order: VOTES, first: 20) '
    "{ edges { node { id name tagline url votesCount createdAt } } } }"
)


def _classify_community_signal_type(title: str, excerpt: str) -> str:
    combined = f"{title} {excerpt}"
    if _CONTENDER_HINT_RE.search(combined):
        return "contender"
    if _TECHNIQUE_HINT_RE.search(combined):
        return "technique"
    return "horizon"


def _plain_algolia_terms(ctx: Ctx, limit: int = 12) -> list[str]:
    raw_terms = classify.public_terms(ctx)
    out: list[str] = []
    seen: set[str] = set()
    boolean_ops = {"and", "or", "not"}

    for raw in raw_terms:
        candidate = raw.strip()
        if "/" in candidate:
            candidate = candidate.rsplit("/", 1)[-1].strip()
        if " " in candidate:
            parts = [p for p in candidate.split() if len(p) >= 3 and p.lower() not in boolean_ops]
            candidate = parts[0] if parts else ""
        if not candidate or candidate.lower() in boolean_ops:
            continue
        if not _PLAIN_TERM_RE.match(candidate):
            continue
        low = candidate.lower()
        if low not in seen:
            seen.add(low)
            out.append(candidate)
        if len(out) >= limit:
            break

    if not out:
        out = ["python", "claude", "mcp"]
    return out


def parse_hn_algolia(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse HN Algolia `search_by_date` story/show_hn hits into `RawSignal`s."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid hn_algolia JSON: {exc}") from exc

    if not isinstance(obj, Mapping):
        raise FetchError("hn_algolia payload must be a JSON object")

    hits = obj.get("hits")
    if not isinstance(hits, list):
        return []

    out: list[RawSignal] = []
    for hit in hits:
        if not isinstance(hit, Mapping):
            continue
        obj_id = str(hit.get("objectID") or hit.get("story_id") or "").strip()
        if not obj_id:
            continue
        title = str(hit.get("title") or hit.get("story_title") or obj_id).strip()
        excerpt = str(hit.get("story_text") or hit.get("comment_text") or title).strip()
        url = str(
            hit.get("url")
            or hit.get("story_url")
            or f"https://news.ycombinator.com/item?id={obj_id}"
        ).strip()
        pub = normalize_timestamp(hit.get("created_at") or hit.get("created_at_i"))
        points = int(hit.get("points") or 0)
        comments = int(hit.get("num_comments") or 0)
        sig_type = _classify_community_signal_type(title, excerpt)
        hint = infer_github_entity_hint(url, excerpt, title)
        out.append(
            RawSignal(
                source_id="hn_algolia",
                type=sig_type,
                external_id=obj_id,
                title=title,
                url=url,
                published_at=pub or meta.fetched_at,
                untrusted_excerpt=excerpt,
                entity_hint=hint,
                metrics={"points": points, "num_comments": comments},
                confidence="confirmed",
                dedupe_key=f"hn_algolia|{sig_type}|{obj_id}",
            )
        )
    return out


def _run_hn_algolia(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    """Issue one plain-word query per public term (Spec 12 A42)."""
    since_epoch = int((ctx.now - timedelta(days=7)).timestamp())
    terms = _plain_algolia_terms(ctx)
    seen_ids: set[str] = set()
    signals: list[RawSignal] = []
    errors: list[str] = []

    for term in terms:
        q_enc = urllib.parse.quote(term, safe="")
        url = (
            "https://hn.algolia.com/api/v1/search_by_date"
            f"?tags=(story,show_hn)&numericFilters=points>=50,created_at_i>{since_epoch}"
            f"&query={q_enc}"
        )
        try:
            resp = ctx.http.fetch(url, ttl_hours=spec.ttl_hours)
            meta = FetchMeta(
                url=resp.url,
                fetched_at=ctx.now_iso,
                status=resp.status,
                headers=resp.headers,
                params={"query": term},
                source_id=spec.id,
            )
            for sig in parse_hn_algolia(resp.body, meta):
                if sig.external_id not in seen_ids:
                    seen_ids.add(sig.external_id)
                    signals.append(sig)
        except RadarError as exc:
            errors.append(str(exc))

    if not signals and errors:
        raise FetchError("; ".join(errors))
    return signals


def is_ai_pricing_story(title: str, excerpt: str, url: str | None = None) -> bool:
    """Return True if an HN pricing hit mentions AI/LLM/coding context (Spec 12 A42)."""
    combined = f"{title} {excerpt} {url or ''}"
    return bool(_AI_PRICING_CONTEXT_RE.search(combined))


def parse_hn_pricing(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse HN Algolia pricing hits and keep only AI/LLM/coding stories locally (A42)."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid hn_pricing JSON: {exc}") from exc

    if not isinstance(obj, Mapping):
        raise FetchError("hn_pricing payload must be a JSON object")

    hits = obj.get("hits")
    if not isinstance(hits, list):
        return []

    out: list[RawSignal] = []
    for hit in hits:
        if not isinstance(hit, Mapping):
            continue
        obj_id = str(hit.get("objectID") or hit.get("story_id") or "").strip()
        if not obj_id:
            continue
        title = str(hit.get("title") or hit.get("story_title") or obj_id).strip()
        excerpt = str(hit.get("story_text") or hit.get("comment_text") or title).strip()
        url = str(
            hit.get("url")
            or hit.get("story_url")
            or f"https://news.ycombinator.com/item?id={obj_id}"
        ).strip()
        if not is_ai_pricing_story(title, excerpt, url):
            continue
        pub = normalize_timestamp(hit.get("created_at") or hit.get("created_at_i"))
        points = int(hit.get("points") or 0)
        comments = int(hit.get("num_comments") or 0)
        out.append(
            RawSignal(
                source_id="hn_pricing",
                type="pricing",
                external_id=obj_id,
                title=title,
                url=url,
                published_at=pub or meta.fetched_at,
                untrusted_excerpt=excerpt,
                entity_hint=infer_github_entity_hint(url, excerpt),
                metrics={"points": points, "num_comments": comments},
                confidence="confirmed",
                payload={"category": "subscription_or_promo"},
                dedupe_key=f"hn_pricing|pricing|{obj_id}",
            )
        )
    return out


def _run_hn_pricing(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    """Issue one HN Algolia request per pricing phrase and filter locally (Spec 12 A15, A42)."""
    since_epoch = int((ctx.now - timedelta(days=1)).timestamp())
    seen_ids: set[str] = set()
    signals: list[RawSignal] = []
    errors: list[str] = []

    for phrase in HN_PRICING_PHRASES:
        q_enc = urllib.parse.quote(phrase, safe="")
        url = (
            "https://hn.algolia.com/api/v1/search_by_date"
            f"?query={q_enc}&tags=story&numericFilters=points>=50,created_at_i>{since_epoch}"
        )
        try:
            resp = ctx.http.fetch(url, ttl_hours=spec.ttl_hours)
            meta = FetchMeta(
                url=resp.url,
                fetched_at=ctx.now_iso,
                status=resp.status,
                headers=resp.headers,
                params={"query": phrase},
                source_id=spec.id,
            )
            for sig in parse_hn_pricing(resp.body, meta):
                if sig.external_id not in seen_ids:
                    seen_ids.add(sig.external_id)
                    signals.append(sig)
        except RadarError as exc:
            errors.append(str(exc))

    if not signals and errors:
        raise FetchError("; ".join(errors))
    return signals


def parse_devto(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse dev.to `/api/articles` JSON list into horizon `RawSignal`s."""
    try:
        arr = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid devto JSON: {exc}") from exc

    if not isinstance(arr, list):
        raise FetchError("devto payload must be a JSON list")

    out: list[RawSignal] = []
    for item in arr:
        if not isinstance(item, Mapping):
            continue
        art_id = str(item.get("id") or item.get("slug") or "").strip()
        if not art_id:
            continue
        title = str(item.get("title") or art_id).strip()
        desc = str(item.get("description") or title).strip()
        url = str(item.get("url") or item.get("canonical_url") or "").strip() or None
        pub = normalize_timestamp(item.get("published_timestamp") or item.get("published_at"))
        reactions = int(
            item.get("public_reactions_count") or item.get("positive_reactions_count") or 0
        )
        comments = int(item.get("comments_count") or 0)
        out.append(
            RawSignal(
                source_id="devto",
                type="horizon",
                external_id=art_id,
                title=title,
                url=url,
                published_at=pub or meta.fetched_at,
                untrusted_excerpt=desc,
                entity_hint=infer_github_entity_hint(url, desc),
                metrics={"reactions": reactions, "comments": comments},
                confidence="confirmed",
                dedupe_key=f"devto|horizon|{art_id}",
            )
        )
    return out


def _devto_tags(ctx: Ctx) -> list[str]:
    candidates: list[str] = []
    for lang in ctx.profile.languages:
        clean = re.sub(r"[^a-z0-9]", "", lang.strip().lower())
        if _DEVTO_TAG_RE.match(clean):
            candidates.append(clean)
    for term in classify.public_terms(ctx):
        clean = re.sub(r"[^a-z0-9]", "", term.strip().lower())
        if _DEVTO_TAG_RE.match(clean):
            candidates.append(clean)
    if not candidates:
        candidates = ["python", "rust", "typescript", "ai", "cli"]
    deduped: list[str] = []
    for t in candidates:
        if t not in deduped:
            deduped.append(t)
        if len(deduped) >= 5:
            break
    return deduped


def _run_devto(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    tags = _devto_tags(ctx)
    seen_ids: set[str] = set()
    signals: list[RawSignal] = []
    errors: list[str] = []

    for tag in tags:
        url = f"https://dev.to/api/articles?tag={urllib.parse.quote(tag, safe='')}&top=7"
        try:
            resp = ctx.http.fetch(url, ttl_hours=spec.ttl_hours)
            meta = FetchMeta(
                url=resp.url,
                fetched_at=ctx.now_iso,
                status=resp.status,
                headers=resp.headers,
                params={"tag": tag},
                source_id=spec.id,
            )
            for sig in parse_devto(resp.body, meta):
                if sig.external_id not in seen_ids:
                    seen_ids.add(sig.external_id)
                    signals.append(sig)
        except RadarError as exc:
            errors.append(str(exc))

    if not signals and errors:
        raise FetchError("; ".join(errors))
    return signals


class _CardHTMLParser(HTMLParser):
    """Stdlib HTMLParser extracting anchor/heading items from directory HTML pages."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.items: list[tuple[str, str, str]] = []
        self._current_href: str | None = None
        self._in_heading = False
        self._in_link = False
        self._heading_buf: list[str] = []
        self._link_buf: list[str] = []
        self._para_buf: list[str] = []
        self._in_para = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        t_low = tag.lower()
        if t_low == "a":
            href = attr_map.get("href", "").strip()
            if href and not href.startswith(("#", "javascript:", "mailto:")):
                self._current_href = urllib.parse.urljoin(self.base_url, href)
                self._in_link = True
                self._link_buf = []
        elif t_low in ("h1", "h2", "h3", "h4"):
            self._in_heading = True
            self._heading_buf = []
        elif t_low == "p":
            self._in_para = True
            self._para_buf = []

    def handle_endtag(self, tag: str) -> None:
        t_low = tag.lower()
        if t_low in ("h1", "h2", "h3", "h4") and self._in_heading:
            self._in_heading = False
            txt = " ".join("".join(self._heading_buf).split()).strip()
            if txt and len(txt) >= 3:
                url = self._current_href or self.base_url
                self.items.append((txt, url, txt))
        elif t_low == "a" and self._in_link:
            self._in_link = False
            txt = " ".join("".join(self._link_buf).split()).strip()
            if txt and len(txt) >= 4 and self._current_href:
                self.items.append((txt, self._current_href, txt))
        elif t_low == "p" and self._in_para:
            self._in_para = False
            p_txt = " ".join("".join(self._para_buf).split()).strip()
            if p_txt and self.items:
                last_t, last_u, last_e = self.items[-1]
                if last_e == last_t:
                    self.items[-1] = (last_t, last_u, p_txt)

    def handle_data(self, data: str) -> None:
        if self._in_heading:
            self._heading_buf.append(data)
        if self._in_link:
            self._link_buf.append(data)
        if self._in_para:
            self._para_buf.append(data)


def parse_html_directory(
    data: bytes,
    meta: FetchMeta,
    *,
    source_id: str,
    base_url: str,
    ignore_titles: frozenset[str] = frozenset(),
) -> list[RawSignal]:
    """Parse an HTML directory page using stdlib `html.parser` into horizon `RawSignal`s."""
    text = data.decode("utf-8", errors="ignore")
    if not text.strip():
        return []
    parser = _CardHTMLParser(meta.url or base_url)
    parser.feed(text)

    default_ignore = {
        "sign in",
        "log in",
        "login",
        "sign up",
        "register",
        "home",
        "about",
        "pricing",
        "privacy",
        "terms",
        "navigation",
        "menu",
        *ignore_titles,
    }
    out: list[RawSignal] = []
    seen: set[str] = set()
    for title, url, excerpt in parser.items:
        clean_t = sanitize.clean(title, limit=200)
        if not clean_t or clean_t.lower() in default_ignore:
            continue
        key = f"{clean_t}|{url}"
        if key in seen:
            continue
        seen.add(key)
        ext_id = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        out.append(
            RawSignal(
                source_id=source_id,
                type="horizon",
                external_id=ext_id,
                title=clean_t,
                url=url,
                published_at=meta.fetched_at,
                untrusted_excerpt=excerpt or clean_t,
                entity_hint=infer_github_entity_hint(url, excerpt),
                confidence="confirmed",
                dedupe_key=f"{source_id}|horizon|{ext_id}",
            )
        )
    return out


def parse_dailydev(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse daily.dev HTML page (source is disabled/BROKEN in production per spike-results)."""
    return parse_html_directory(
        data,
        meta,
        source_id="dailydev",
        base_url="https://app.daily.dev/",
    )


def parse_devhunt(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse DevHunt (`https://devhunt.org/`) HTML page into horizon `RawSignal`s."""
    return parse_html_directory(
        data,
        meta,
        source_id="devhunt",
        base_url="https://devhunt.org/",
        ignore_titles=frozenset({"dev hunt", "devhunt", "submit a tool"}),
    )


def parse_producthunt(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse Product Hunt GraphQL `data.posts.edges[].node` JSON into horizon `RawSignal`s."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid producthunt JSON: {exc}") from exc

    if not isinstance(obj, Mapping):
        raise FetchError("producthunt payload must be a JSON object")

    data_obj = obj.get("data")
    posts_obj = data_obj.get("posts") if isinstance(data_obj, Mapping) else None
    edges = posts_obj.get("edges") if isinstance(posts_obj, Mapping) else None
    if not isinstance(edges, list):
        return []

    out: list[RawSignal] = []
    for edge in edges:
        if not isinstance(edge, Mapping):
            continue
        node = edge.get("node") if isinstance(edge.get("node"), Mapping) else edge
        if not isinstance(node, Mapping):
            continue
        post_id = str(node.get("id") or node.get("slug") or "").strip()
        name = str(node.get("name") or post_id).strip()
        if not post_id or not name:
            continue
        tagline = str(node.get("tagline") or node.get("description") or name).strip()
        url = str(node.get("url") or node.get("website") or "").strip() or None
        votes = int(node.get("votesCount") or 0)
        pub = normalize_timestamp(node.get("createdAt")) or meta.fetched_at
        title = f"{name} — {tagline}" if tagline and tagline != name else name
        out.append(
            RawSignal(
                source_id="producthunt",
                type="horizon",
                external_id=post_id,
                title=title,
                url=url,
                published_at=pub,
                untrusted_excerpt=tagline,
                entity_hint=infer_github_entity_hint(url, tagline),
                metrics={"votes": votes},
                confidence="confirmed",
                dedupe_key=f"producthunt|horizon|{post_id}",
            )
        )
    return out


def _run_producthunt(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    """Run Product Hunt GraphQL query using Keychain token in memory only (Spec 06 T7)."""
    token = credentials.get(ctx, "producthunt")
    if not token:
        return []

    body_bytes = json.dumps({"query": PRODUCTHUNT_GRAPHQL_QUERY}, separators=(",", ":")).encode(
        "utf-8"
    )
    req_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        resp = ctx.http.fetch(
            spec.url or "https://api.producthunt.com/v2/api/graphql",
            method="POST",
            body=body_bytes,
            headers=req_headers,
            use_cache=False,
        )
        meta = FetchMeta(
            url=resp.url,
            fetched_at=ctx.now_iso,
            status=resp.status,
            headers=resp.headers,
            source_id=spec.id,
        )
        return parse_producthunt(resp.body, meta)
    except Exception as exc:
        safe_msg = redact.scrub(str(exc)).replace(token, "[REDACTED:token]")
        raise FetchError(f"producthunt fetch failed: {safe_msg}") from None
    finally:
        del token


def _post_dict_to_signal(item: Mapping[str, Any], fetched_at: str) -> RawSignal | None:
    post_id = str(item.get("id") or item.get("name") or "").strip()
    if not post_id:
        return None
    sub = str(item.get("subreddit") or "reddit").strip()
    title_raw = str(item.get("title") or post_id).strip()
    selftext_raw = str(item.get("selftext") or "").strip()[:1000]
    permalink = str(item.get("permalink") or f"/r/{sub}/comments/{post_id}/").strip()
    permalink_url = f"https://www.reddit.com{permalink}" if permalink.startswith("/") else permalink
    url = str(item.get("url") or permalink_url).strip()
    pub = normalize_timestamp(item.get("created_utc")) or fetched_at
    score = int(item.get("score") or item.get("ups") or 0)
    num_comments = int(item.get("num_comments") or 0)

    clean_title = sanitize.clean(title_raw, limit=300) or post_id
    clean_excerpt = sanitize.clean(selftext_raw or title_raw, limit=600)
    hint = infer_github_entity_hint(url, selftext_raw, title_raw)
    return RawSignal(
        source_id="reddit_top",
        type="horizon",
        external_id=f"{sub}:{post_id}",
        title=clean_title,
        url=url,
        published_at=pub,
        untrusted_excerpt=clean_excerpt,
        entity_hint=hint,
        metrics={"score": score, "num_comments": num_comments},
        confidence="confirmed",
        payload={"subreddit": sub},
        dedupe_key=f"reddit_top|horizon|{sub}:{post_id}",
    )


def parse_reddit_top(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse `reddit_pull.py` JSONL stdout (or raw Reddit API Listing JSON) into `RawSignal`s."""
    text = data.decode("utf-8", errors="ignore").strip()
    if not text:
        return []

    out: list[RawSignal] = []
    seen: set[str] = set()

    # Try full JSON first (in case raw Reddit Listing JSON is passed directly)
    try:
        whole = json.loads(text)
    except json.JSONDecodeError:
        whole = None

    if isinstance(whole, Mapping) and isinstance(whole.get("data"), Mapping):
        children = whole["data"].get("children")
        if isinstance(children, list):
            for child in children:
                post = child.get("data") if isinstance(child, Mapping) else None
                if isinstance(post, Mapping):
                    sig = _post_dict_to_signal(post, meta.fetched_at)
                    if sig is not None and sig.external_id not in seen:
                        seen.add(sig.external_id)
                        out.append(sig)
            return out

    if isinstance(whole, Mapping):
        sig = _post_dict_to_signal(whole, meta.fetched_at)
        return [sig] if sig is not None else []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FetchError(f"invalid reddit_pull JSONL line: {exc}") from exc
        if not isinstance(item, Mapping):
            continue
        sig = _post_dict_to_signal(item, meta.fetched_at)
        if sig is not None and sig.external_id not in seen:
            seen.add(sig.external_id)
            out.append(sig)

    return out


def _run_reddit_top(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    """Invoke `S/reddit_pull.py` in an isolated subprocess with minimal env and timeout=600."""
    raw_subs = spec.params.get("subreddits") or REDDIT_SUBREDDITS
    valid_subs = [str(s).strip() for s in raw_subs if _SUBREDDIT_NAME_RE.match(str(s).strip())]
    if not valid_subs:
        valid_subs = list(REDDIT_SUBREDDITS)

    t_filter = str(spec.params.get("t") or "day").strip()
    limit = int(spec.params.get("limit") or 25)
    script_path = _SCRIPTS_DIR / "reddit_pull.py"

    minimal_env = {
        "PATH": os.environ.get("PATH", "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin"),
        "HOME": str(ctx.paths.home),
        "TECH_RADAR_STATE": str(ctx.paths.state),
    }
    argv = [
        "uv",
        "run",
        "--script",
        "--locked",
        "--isolated",
        str(script_path),
        "--subs",
        ",".join(valid_subs),
        "--t",
        t_filter,
        "--limit",
        str(limit),
    ]
    res = ctx.procs.run(
        argv,
        env=minimal_env,
        timeout=600.0,
        free_text_indices=(7,),
    )
    if res.returncode != 0:
        raise FetchError(f"reddit_pull.py exited with {res.returncode}: {res.stderr}")
    if not res.stdout.strip():
        return []
    meta = FetchMeta(
        url="reddit_pull.py",
        fetched_at=ctx.now_iso,
        status=200,
        params=spec.params,
        source_id=spec.id,
    )
    return parse_reddit_top(res.stdout.encode("utf-8"), meta)


HN_ALGOLIA_SPEC = SourceSpec(
    id="hn_algolia",
    req=("SRC-COMM-01",),
    archetype="json_api",
    url="https://hn.algolia.com/api/v1/search_by_date",
    cadence="daily",
    hosts=("hn.algolia.com",),
    signal_types=("horizon", "contender", "technique"),
    ttl_hours=12,
    parser=parse_hn_algolia,
    runner=_run_hn_algolia,
)

HN_PRICING_SPEC = SourceSpec(
    id="hn_pricing",
    req=("GOAL-09",),
    archetype="json_api",
    url="https://hn.algolia.com/api/v1/search_by_date",
    cadence="daily",
    hosts=("hn.algolia.com",),
    signal_types=("pricing",),
    ttl_hours=12,
    params={"phrases": HN_PRICING_PHRASES},
    parser=parse_hn_pricing,
    runner=_run_hn_pricing,
)

DEVTO_SPEC = SourceSpec(
    id="devto",
    req=("SRC-COMM-11",),
    archetype="json_api",
    url="https://dev.to/api/articles?tag=python&top=7",
    cadence="weekly",
    hosts=("dev.to",),
    signal_types=("horizon",),
    ttl_hours=24,
    parser=parse_devto,
    runner=_run_devto,
)

DAILYDEV_SPEC = SourceSpec(
    id="dailydev",
    req=("SRC-COMM-11",),
    archetype="html",
    url="https://app.daily.dev/",
    cadence="weekly",
    hosts=("app.daily.dev",),
    signal_types=("horizon",),
    ttl_hours=24,
    parser=parse_dailydev,
    enabled=False,
    skipped_reason="BROKEN: no public unauthenticated API/feed (marketing/SPA shell only)",
)

PRODUCTHUNT_SPEC = SourceSpec(
    id="producthunt",
    req=("SRC-COMM-12",),
    archetype="json_api",
    url="https://api.producthunt.com/v2/api/graphql",
    cadence="weekly",
    hosts=("api.producthunt.com",),
    signal_types=("horizon",),
    credential="producthunt",
    ttl_hours=24,
    parser=parse_producthunt,
    runner=_run_producthunt,
)

DEVHUNT_SPEC = SourceSpec(
    id="devhunt",
    req=("SRC-COMM-12",),
    archetype="html",
    url="https://devhunt.org/",
    cadence="weekly",
    hosts=("devhunt.org",),
    signal_types=("horizon",),
    ttl_hours=24,
    parser=parse_devhunt,
)

REDDIT_TOP_SPEC = SourceSpec(
    id="reddit_top",
    req=("SRC-COMM-17", *(f"RED-{i:02d}" for i in range(1, 22))),
    archetype="subprocess",
    url=None,
    cadence="daily",
    hosts=(),
    signal_types=("horizon", "contender", "technique", "compromise"),
    ttl_hours=24,
    params={"subreddits": REDDIT_SUBREDDITS, "t": "day", "limit": 25},
    parser=parse_reddit_top,
    runner=_run_reddit_top,
)

COMMUNITY_SPECS: tuple[SourceSpec, ...] = (
    HN_ALGOLIA_SPEC,
    HN_PRICING_SPEC,
    DEVTO_SPEC,
    DAILYDEV_SPEC,
    PRODUCTHUNT_SPEC,
    DEVHUNT_SPEC,
    REDDIT_TOP_SPEC,
)
