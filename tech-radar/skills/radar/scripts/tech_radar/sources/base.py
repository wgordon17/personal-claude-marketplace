"""Source archetype definitions, default RSS/Atom feed parser, and source runner (Spec 03)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any, Literal
from xml.etree.ElementTree import Element

from .. import db, redact, sanitize
from ..errors import FetchError, RadarError

if TYPE_CHECKING:
    from ..context import Ctx

ArchetypeLiteral = Literal[
    "feed",
    "json_api",
    "gh_api",
    "gh_search",
    "html",
    "git_log",
    "subprocess",
]
CadenceLiteral = Literal[
    "hourly",
    "daily",
    "weekly",
    "monthly",
    "on-demand",
]

_GITHUB_URL_SLUG_RE = re.compile(
    r"https?://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FetchMeta:
    """Metadata passed into pure source parsers alongside raw response bytes."""

    url: str
    fetched_at: str
    status: int = 200
    headers: Mapping[str, str] = field(default_factory=dict)
    params: Mapping[str, Any] = field(default_factory=dict)
    source_id: str = ""
    entity_id: str | None = None


@dataclass
class RawSignal:
    """Parsed signal emitted by a source parser or runner before SQLite persistence."""

    source_id: str
    type: str
    external_id: str
    title: str
    url: str | None = None
    published_at: str | None = None
    untrusted_excerpt: str = ""
    entity_hint: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    confidence: str = "confirmed"
    payload: dict[str, Any] = field(default_factory=dict)
    dedupe_key: str | None = None
    withdrawn: int = 0

    def __post_init__(self) -> None:
        self.title = sanitize.clean(self.title, limit=300) or self.external_id or self.source_id
        self.untrusted_excerpt = sanitize.clean(self.untrusted_excerpt, limit=600)


@dataclass
class SourceResult:
    """Outcome of running a single SourceSpec."""

    source_id: str
    signals: list[RawSignal] = field(default_factory=list)
    ok: bool = True
    skipped_reason: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class SourceSpec:
    """Declarative source specification (Spec 03 § Archetypes)."""

    id: str
    req: tuple[str, ...]
    archetype: ArchetypeLiteral
    url: str | None
    cadence: CadenceLiteral
    hosts: tuple[str, ...]
    signal_types: tuple[str, ...]
    credential: str | None = None
    ttl_hours: int = 24
    params: Mapping[str, Any] = field(default_factory=dict)
    parser: Callable[[bytes, FetchMeta], list[RawSignal]] | None = None
    runner: Callable[[SourceSpec, Ctx], list[RawSignal]] | None = None
    enabled: bool = True
    skipped_reason: str | None = None


def normalize_timestamp(raw: str | int | float | None) -> str | None:
    """Convert ISO-8601, RFC-2822, or epoch timestamps to UTC ISO-8601 string."""
    if raw is None:
        return None
    if isinstance(raw, int | float):
        try:
            return datetime.fromtimestamp(float(raw), tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (OSError, OverflowError, ValueError):
            return None
    text = str(raw).strip()
    if not text:
        return None
    if text.isdigit():
        try:
            return datetime.fromtimestamp(float(text), tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (OSError, OverflowError, ValueError):
            return text
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        pass
    try:
        dt2 = parsedate_to_datetime(text)
        if dt2.tzinfo is None:
            dt2 = dt2.replace(tzinfo=UTC)
        return dt2.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return text


def infer_github_entity_hint(*texts: str | None) -> str | None:
    """Infer a `gh:owner/repo` hint from URLs or text when present."""
    for text in texts:
        if not text:
            continue
        match = _GITHUB_URL_SLUG_RE.search(text)
        if match:
            slug = match.group(1).rstrip(".").lower()
            if slug.endswith(".git"):
                slug = slug[:-4]
            parts = slug.split("/")
            if len(parts) == 2 and parts[0] not in ("orgs", "topics", "trending", "settings"):
                return f"gh:{slug}"
    return None


def _local_tag(elem: Element) -> str:
    tag = elem.tag
    if "}" in tag:
        return tag.split("}", 1)[1].lower()
    return tag.lower()


def _child_text(elem: Element, names: tuple[str, ...]) -> str:
    for child in elem:
        if _local_tag(child) in names:
            txt = "".join(child.itertext()).strip()
            if txt:
                return txt
    return ""


def _extract_entry_link(elem: Element) -> str | None:
    fallback_href: str | None = None
    for child in elem:
        if _local_tag(child) != "link":
            continue
        href = (child.attrib.get("href") or "").strip()
        rel = (child.attrib.get("rel") or "alternate").strip().lower()
        if href:
            if rel == "alternate":
                return href
            if fallback_href is None:
                fallback_href = href
        txt = "".join(child.itertext()).strip()
        if txt:
            return txt
    return fallback_href


def parse_feed_bytes(
    data: bytes,
    meta: FetchMeta,
    *,
    source_id: str | None = None,
    signal_type: str = "horizon",
    confidence: str = "confirmed",
) -> list[RawSignal]:
    """Parse RSS 2.0 or Atom XML feed bytes using sanitize.parse_xml (defusedxml only)."""
    root = sanitize.parse_xml(data)
    active_source = source_id or meta.source_id or "feed"
    out: list[RawSignal] = []

    root_name = _local_tag(root)
    entries: list[Element] = []
    if root_name == "feed":
        entries = [ch for ch in root if _local_tag(ch) == "entry"]
    else:
        for ch in root.iter():
            if _local_tag(ch) in ("item", "entry"):
                entries.append(ch)

    for idx, item in enumerate(entries):
        title = _child_text(item, ("title",))
        link = _extract_entry_link(item)
        ext_id = _child_text(item, ("id", "guid")) or link or f"{active_source}-{idx}"
        pub_raw = _child_text(item, ("published", "pubdate", "updated", "date"))
        published_at = normalize_timestamp(pub_raw) or meta.fetched_at
        excerpt_raw = _child_text(
            item,
            ("summary", "description", "content", "encoded"),
        )
        if not title and not excerpt_raw:
            continue
        hint = infer_github_entity_hint(link, excerpt_raw)
        out.append(
            RawSignal(
                source_id=active_source,
                type=signal_type,
                external_id=ext_id,
                title=title or ext_id,
                url=link,
                published_at=published_at,
                untrusted_excerpt=excerpt_raw or title,
                entity_hint=hint,
                confidence=confidence,
            )
        )
    return out


def make_feed_parser(
    source_id: str,
    *,
    signal_type: str = "horizon",
    confidence: str = "confirmed",
) -> Callable[[bytes, FetchMeta], list[RawSignal]]:
    """Build a pure `(bytes, FetchMeta) -> list[RawSignal]` parser for an RSS/Atom source."""

    def _parser(data: bytes, meta: FetchMeta) -> list[RawSignal]:
        return parse_feed_bytes(
            data,
            meta,
            source_id=source_id,
            signal_type=signal_type,
            confidence=confidence,
        )

    return _parser


def persist_raw_signal(ctx: Ctx, sig: RawSignal, *, fetched_at: str | None = None) -> int:
    """Insert or update a RawSignal in the SQLite `signals` table and return `signal_id`."""
    ts = fetched_at or ctx.now_iso
    dedupe = sig.dedupe_key or f"{sig.source_id}|{sig.type}|{sig.external_id}"
    payload_dict: dict[str, Any] = dict(sig.payload) if sig.payload else {}
    if sig.metrics:
        payload_dict.setdefault("metrics", sig.metrics)
    payload_str = (
        json.dumps(payload_dict, separators=(",", ":"), sort_keys=True) if payload_dict else None
    )

    entity_id: str | None = None
    if sig.entity_hint:
        row = ctx.db.execute(
            "SELECT entity_id FROM entities WHERE entity_id = ?",
            (sig.entity_hint,),
        ).fetchone()
        if row is not None:
            entity_id = str(row["entity_id"])
        else:
            alias_row = ctx.db.execute(
                "SELECT entity_id FROM entity_aliases WHERE alias = ?",
                (sig.entity_hint,),
            ).fetchone()
            if alias_row is not None:
                entity_id = str(alias_row["entity_id"])

    row_id = db.insert_signal(
        ctx.db,
        source_id=sig.source_id,
        signal_type=sig.type,
        title=sig.title,
        fetched_at=ts,
        dedupe_key=dedupe,
        entity_id=entity_id,
        confidence=sig.confidence,
        external_id=sig.external_id,
        url=sig.url,
        published_at=sig.published_at,
        untrusted_excerpt=sig.untrusted_excerpt,
        payload_json=payload_str,
        withdrawn=sig.withdrawn,
    )
    safe_dedupe = redact.scrub(dedupe)
    sig_row = ctx.db.execute(
        "SELECT signal_id FROM signals WHERE dedupe_key = ?",
        (safe_dedupe,),
    ).fetchone()
    return int(sig_row[0]) if sig_row is not None else row_id


def record_source_ok(ctx: Ctx, source_id: str, item_count: int) -> None:
    """Update `source_health` after a successful source execution."""
    ctx.db.execute(
        "INSERT INTO source_health("
        "source_id, last_ok_at, last_error, consecutive_failures, last_item_count, skipped_reason"
        ") VALUES (?, ?, NULL, 0, ?, NULL) "
        "ON CONFLICT(source_id) DO UPDATE SET "
        "last_ok_at = excluded.last_ok_at, "
        "last_error = NULL, "
        "consecutive_failures = 0, "
        "last_item_count = excluded.last_item_count, "
        "skipped_reason = NULL",
        (source_id, ctx.now_iso, item_count),
    )
    ctx.db.commit()


def record_source_skipped(ctx: Ctx, source_id: str, reason: str) -> None:
    """Record a skipped source in `source_health` without incrementing failures."""
    safe_reason = redact.scrub(reason)
    ctx.db.execute(
        "INSERT INTO source_health("
        "source_id, last_ok_at, last_error, consecutive_failures, last_item_count, skipped_reason"
        ") VALUES (?, NULL, NULL, 0, 0, ?) "
        "ON CONFLICT(source_id) DO UPDATE SET "
        "skipped_reason = excluded.skipped_reason",
        (source_id, safe_reason),
    )
    ctx.db.commit()


def record_source_error(ctx: Ctx, source_id: str, error_msg: str) -> int:
    """Increment `consecutive_failures` in `source_health` and return the new failure count."""
    safe_err = redact.scrub(error_msg)
    row = ctx.db.execute(
        "SELECT consecutive_failures FROM source_health WHERE source_id = ?",
        (source_id,),
    ).fetchone()
    prev = int(row["consecutive_failures"]) if row is not None else 0
    new_count = prev + 1
    ctx.db.execute(
        "INSERT INTO source_health("
        "source_id, last_ok_at, last_error, consecutive_failures, last_item_count, skipped_reason"
        ") VALUES (?, NULL, ?, ?, 0, NULL) "
        "ON CONFLICT(source_id) DO UPDATE SET "
        "last_error = excluded.last_error, "
        "consecutive_failures = excluded.consecutive_failures, "
        "skipped_reason = NULL",
        (source_id, safe_err, new_count),
    )
    ctx.db.commit()
    return new_count


def run_source(spec: SourceSpec, ctx: Ctx) -> SourceResult:
    """Execute a SourceSpec via ctx.http/ctx.gh/ctx.procs, persist signals, and update health."""
    if not spec.enabled:
        reason = spec.skipped_reason or "disabled"
        record_source_skipped(ctx, spec.id, reason)
        return SourceResult(source_id=spec.id, signals=[], ok=True, skipped_reason=reason)

    if spec.credential:
        has_cred = bool(ctx.credentials is not None and ctx.credentials.has(spec.credential))
        if not has_cred:
            record_source_skipped(ctx, spec.id, "no credential")
            return SourceResult(
                source_id=spec.id,
                signals=[],
                ok=True,
                skipped_reason="no credential",
            )

    try:
        if spec.runner is not None:
            signals = spec.runner(spec, ctx)
        elif spec.archetype in ("feed", "json_api", "html"):
            if not spec.url:
                raise FetchError(f"source {spec.id} missing URL")
            resp = ctx.http.fetch(spec.url, ttl_hours=spec.ttl_hours)
            meta = FetchMeta(
                url=resp.url,
                fetched_at=ctx.now_iso,
                status=resp.status,
                headers=resp.headers,
                params=spec.params,
                source_id=spec.id,
            )
            parser_fn = spec.parser
            if parser_fn is None and spec.archetype == "feed":
                default_type = spec.signal_types[0] if spec.signal_types else "horizon"
                parser_fn = make_feed_parser(spec.id, signal_type=default_type)
            if parser_fn is None:
                raise FetchError(f"source {spec.id} missing parser")
            signals = parser_fn(resp.body, meta)
        elif spec.archetype in ("gh_api", "gh_search"):
            if not spec.url:
                raise FetchError(f"source {spec.id} missing gh endpoint")
            res = ctx.gh.api(spec.url)
            if res.returncode != 0:
                raise FetchError(f"gh api failed for {spec.id}: {res.stderr}")
            if spec.parser is None:
                raise FetchError(f"source {spec.id} missing parser")
            meta = FetchMeta(
                url=spec.url,
                fetched_at=ctx.now_iso,
                status=200,
                params=spec.params,
                source_id=spec.id,
            )
            signals = spec.parser(res.stdout.encode("utf-8"), meta)
        else:
            raise FetchError(f"unsupported archetype {spec.archetype} without runner for {spec.id}")

        for sig in signals:
            persist_raw_signal(ctx, sig, fetched_at=ctx.now_iso)
        record_source_ok(ctx, spec.id, len(signals))
        return SourceResult(source_id=spec.id, signals=signals, ok=True)
    except (RadarError, Exception) as exc:
        record_source_error(ctx, spec.id, str(exc))
        return SourceResult(
            source_id=spec.id,
            signals=[],
            ok=False,
            error=str(exc),
        )
