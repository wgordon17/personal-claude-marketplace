"""AI vendor sources for Task 5: claude_code_changelog and anthropic_news (Spec 03, Spec 07)."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from collections.abc import Mapping
from html.parser import HTMLParser
from typing import TYPE_CHECKING

from .. import sanitize
from ..errors import FetchError, RadarError
from .base import FetchMeta, RawSignal, SourceSpec
from .github import decode_github_contents

if TYPE_CHECKING:
    from ..context import Ctx

PRICING_KEYWORDS_RE = re.compile(
    r"\b(?:usage limit|rate limit|weekly limit|quota|plan|pricing"
    r"|max plan|pro plan|promo|free|discount|credits)\b",
    re.IGNORECASE,
)

_CHANGELOG_SECTION_RE = re.compile(r"^#{1,3}\s+(.+?)\s*$", re.MULTILINE)


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


ANTHROPIC_NEWS_URLS: tuple[str, ...] = (
    "https://www.anthropic.com/news",
    "https://docs.anthropic.com/en/release-notes/overview",
)


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

AI_SPECS: tuple[SourceSpec, ...] = (
    CLAUDE_CODE_CHANGELOG_SPEC,
    ANTHROPIC_NEWS_SPEC,
)
