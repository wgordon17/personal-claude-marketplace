"""Curated RSS/Atom feed sources (Spec 03, Task 7 Step 1)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from ..errors import FetchError, RadarError
from .base import (
    FetchMeta,
    RawSignal,
    SourceSpec,
    infer_github_entity_hint,
    make_feed_parser,
    normalize_timestamp,
    parse_feed_bytes,
)

if TYPE_CHECKING:
    from ..context import Ctx

TERMINAL_TROVE_URLS: tuple[str, ...] = (
    "https://terminaltrove.com/new.xml",
    "https://terminaltrove.com/totw.xml",
)

JS_NODE_WEEKLY_URLS: tuple[str, ...] = (
    "https://javascriptweekly.com/rss",
    "https://nodeweekly.com/rss",
)

TLDR_URLS: tuple[str, ...] = (
    "https://tldr.tech/api/rss/dev",
    "https://tldr.tech/api/rss/ai",
)

LOBSTERS_TAGS: tuple[str, ...] = ("python", "rust", "devops", "ai", "cli")


def _run_multi_feed_urls(
    spec: SourceSpec,
    ctx: Ctx,
    urls: Sequence[str],
    *,
    signal_type: str = "horizon",
) -> list[RawSignal]:
    out: list[RawSignal] = []
    seen_ids: set[str] = set()
    errors: list[str] = []
    parser_fn = spec.parser or make_feed_parser(spec.id, signal_type=signal_type)
    for url in urls:
        try:
            resp = ctx.http.fetch(url, ttl_hours=spec.ttl_hours)
            meta = FetchMeta(
                url=resp.url,
                fetched_at=ctx.now_iso,
                status=resp.status,
                headers=resp.headers,
                params=spec.params,
                source_id=spec.id,
            )
            for sig in parser_fn(resp.body, meta):
                if sig.external_id not in seen_ids:
                    seen_ids.add(sig.external_id)
                    out.append(sig)
        except RadarError as exc:
            errors.append(str(exc))
    if not out and errors:
        raise FetchError("; ".join(errors))
    return out


def parse_console_dev(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse Console.dev RSS XML (or fallback JSON tools list) into horizon `RawSignal`s."""
    stripped = data.lstrip()
    if stripped.startswith((b"{", b"[")):
        try:
            obj = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FetchError(f"invalid console_dev JSON: {exc}") from exc
        items = obj.get("tools") if isinstance(obj, Mapping) else obj
        if not isinstance(items, list):
            return []
        out: list[RawSignal] = []
        for idx, item in enumerate(items):
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or item.get("title") or "").strip()
            url = str(item.get("url") or item.get("link") or "").strip() or None
            desc = str(item.get("description") or item.get("summary") or name).strip()
            ext_id = str(item.get("id") or url or name or f"console-{idx}")
            pub = normalize_timestamp(item.get("published_at") or item.get("date"))
            if not name and not desc:
                continue
            out.append(
                RawSignal(
                    source_id="console_dev",
                    type="horizon",
                    external_id=ext_id,
                    title=name or ext_id,
                    url=url,
                    published_at=pub or meta.fetched_at,
                    untrusted_excerpt=desc or name,
                    entity_hint=infer_github_entity_hint(url, desc),
                )
            )
        return out
    return parse_feed_bytes(data, meta, source_id="console_dev", signal_type="horizon")


parse_simonw = make_feed_parser("simonw", signal_type="horizon")
parse_latent_space = make_feed_parser("latent_space", signal_type="horizon")
parse_terminal_trove = make_feed_parser("terminal_trove", signal_type="horizon")
parse_pycoders = make_feed_parser("pycoders", signal_type="horizon")
parse_twir = make_feed_parser("twir", signal_type="horizon")
parse_js_node_weekly = make_feed_parser("js_node_weekly", signal_type="horizon")
parse_changelog_news = make_feed_parser("changelog_news", signal_type="horizon")
parse_tldr = make_feed_parser("tldr", signal_type="horizon")
parse_lobsters = make_feed_parser("lobsters", signal_type="horizon")


def _run_lobsters(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    tags = spec.params.get("tags") or LOBSTERS_TAGS
    urls = [f"https://lobste.rs/t/{tag}.rss" for tag in tags]
    return _run_multi_feed_urls(spec, ctx, urls, signal_type="horizon")


SIMONW_SPEC = SourceSpec(
    id="simonw",
    req=("SRC-CUR-01",),
    archetype="feed",
    url="https://simonwillison.net/atom/everything/",
    cadence="daily",
    hosts=("simonwillison.net",),
    signal_types=("horizon", "technique"),
    ttl_hours=12,
    parser=parse_simonw,
)

LATENT_SPACE_SPEC = SourceSpec(
    id="latent_space",
    req=("SRC-CUR-02",),
    archetype="feed",
    url="https://www.latent.space/feed",
    cadence="weekly",
    hosts=("www.latent.space", "latent.space"),
    signal_types=("horizon", "technique"),
    ttl_hours=24,
    parser=parse_latent_space,
)

CONSOLE_DEV_SPEC = SourceSpec(
    id="console_dev",
    req=("SRC-CUR-03",),
    archetype="feed",
    url="https://console.dev/rss.xml",
    cadence="weekly",
    hosts=("console.dev",),
    signal_types=("horizon",),
    ttl_hours=24,
    parser=parse_console_dev,
)

TERMINAL_TROVE_SPEC = SourceSpec(
    id="terminal_trove",
    req=("SRC-CUR-04",),
    archetype="feed",
    url=TERMINAL_TROVE_URLS[0],
    cadence="weekly",
    hosts=("terminaltrove.com",),
    signal_types=("horizon",),
    ttl_hours=24,
    params={"urls": TERMINAL_TROVE_URLS},
    parser=parse_terminal_trove,
    runner=lambda spec, ctx: _run_multi_feed_urls(spec, ctx, TERMINAL_TROVE_URLS),
)

PYCODERS_SPEC = SourceSpec(
    id="pycoders",
    req=("SRC-CUR-05",),
    archetype="feed",
    url="https://pycoders.com/feed",
    cadence="weekly",
    hosts=("pycoders.com",),
    signal_types=("horizon", "technique"),
    ttl_hours=24,
    parser=parse_pycoders,
)

TWIR_SPEC = SourceSpec(
    id="twir",
    req=("SRC-CUR-06",),
    archetype="feed",
    url="https://this-week-in-rust.org/rss.xml",
    cadence="weekly",
    hosts=("this-week-in-rust.org",),
    signal_types=("horizon",),
    ttl_hours=24,
    parser=parse_twir,
)

JS_NODE_WEEKLY_SPEC = SourceSpec(
    id="js_node_weekly",
    req=("SRC-CUR-07",),
    archetype="feed",
    url=JS_NODE_WEEKLY_URLS[0],
    cadence="weekly",
    hosts=("javascriptweekly.com", "nodeweekly.com"),
    signal_types=("horizon",),
    ttl_hours=24,
    params={"urls": JS_NODE_WEEKLY_URLS},
    parser=parse_js_node_weekly,
    runner=lambda spec, ctx: _run_multi_feed_urls(spec, ctx, JS_NODE_WEEKLY_URLS),
)

CHANGELOG_NEWS_SPEC = SourceSpec(
    id="changelog_news",
    req=("SRC-CUR-08",),
    archetype="feed",
    url="https://changelog.com/news/feed",
    cadence="weekly",
    hosts=("changelog.com",),
    signal_types=("horizon", "technique"),
    ttl_hours=24,
    parser=parse_changelog_news,
)

TLDR_SPEC = SourceSpec(
    id="tldr",
    req=("SRC-CUR-09",),
    archetype="feed",
    url=TLDR_URLS[0],
    cadence="weekly",
    hosts=("tldr.tech",),
    signal_types=("horizon",),
    ttl_hours=24,
    params={"urls": TLDR_URLS},
    parser=parse_tldr,
    runner=lambda spec, ctx: _run_multi_feed_urls(spec, ctx, TLDR_URLS),
)

LOBSTERS_SPEC = SourceSpec(
    id="lobsters",
    req=("SRC-COMM-02",),
    archetype="feed",
    url="https://lobste.rs/t/python.rss",
    cadence="daily",
    hosts=("lobste.rs",),
    signal_types=("horizon", "technique"),
    ttl_hours=12,
    params={"tags": LOBSTERS_TAGS},
    parser=parse_lobsters,
    runner=_run_lobsters,
)

FEED_SPECS: tuple[SourceSpec, ...] = (
    SIMONW_SPEC,
    LATENT_SPACE_SPEC,
    CONSOLE_DEV_SPEC,
    TERMINAL_TROVE_SPEC,
    PYCODERS_SPEC,
    TWIR_SPEC,
    JS_NODE_WEEKLY_SPEC,
    CHANGELOG_NEWS_SPEC,
    TLDR_SPEC,
    LOBSTERS_SPEC,
)
