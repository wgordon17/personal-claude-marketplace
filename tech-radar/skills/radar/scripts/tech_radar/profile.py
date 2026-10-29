"""TASTE_PROFILE.md, WATCHLIST.md, and kv['private_scopes'] loader for tech-radar."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import config, db, redact, sanitize

MAX_PROFILE_BYTES = 16 * 1024
BULLET_LIMIT = 200

_HEADING_RE = re.compile(r"^#{1,3}\s+(.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.+?)\s*$")

_SECTION_MAP: dict[str, str] = {
    "watchlist": "watchlist",
    "preferences": "preferences",
    "anti-interests": "anti_interests",
    "anti interests": "anti_interests",
    "anti_interests": "anti_interests",
    "taste-makers": "taste_makers",
    "taste makers": "taste_makers",
    "tastemakers": "taste_makers",
    "taste_makers": "taste_makers",
    "categories": "categories",
    "languages": "languages",
    "subscriptions": "subscriptions",
}

_IGNORED_SECTIONS: frozenset[str] = frozenset(
    {
        "private scopes",
        "private-scopes",
        "private_scopes",
    }
)


def _clean_bullet(raw: str) -> str:
    cleaned = sanitize.clean(raw, limit=BULLET_LIMIT)
    scrubbed = redact.scrub(cleaned).strip()
    if len(scrubbed) > BULLET_LIMIT:
        scrubbed = scrubbed[: BULLET_LIMIT - 1].rstrip() + "…"
    return scrubbed


def _read_bounded_markdown(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        raw_bytes = path.read_bytes()[:MAX_PROFILE_BYTES]
    except OSError:
        return ""
    return raw_bytes.decode("utf-8", errors="ignore")


def parse_private_scopes_value(raw: str | None) -> list[str]:
    """Parse kv['private_scopes'] JSON array or newline/comma-separated string."""
    if not raw or not raw.strip():
        return []
    text = raw.strip()
    items: list[str] = []
    if text.startswith("["):
        try:
            loaded = json.loads(text)
            if isinstance(loaded, list):
                items = [str(x) for x in loaded if x is not None]
        except json.JSONDecodeError:
            items = []
    if not items:
        for line in text.replace(",", "\n").splitlines():
            part = line.strip()
            if part:
                items.append(part)

    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = _clean_bullet(item)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return out


def load_private_scopes(conn: sqlite3.Connection | None = None) -> list[str]:
    """Load private_scopes exclusively from radar.db kv['private_scopes'] (Spec 12 A8)."""
    if conn is not None:
        raw = db.kv_get(conn, "private_scopes")
        return parse_private_scopes_value(raw)

    p = config.paths(ensure_dirs=False)
    if not p.db.is_file():
        return []
    try:
        temp_conn = db.connect(p.db, auto_migrate=True)
        try:
            raw = db.kv_get(temp_conn, "private_scopes")
        finally:
            temp_conn.close()
        return parse_private_scopes_value(raw)
    except sqlite3.Error:
        return []


def save_private_scopes(conn: sqlite3.Connection, scopes: list[str]) -> list[str]:
    """Save deduplicated private_scopes list into radar.db kv['private_scopes']."""
    cleaned_list: list[str] = []
    seen: set[str] = set()
    for scope in scopes:
        cleaned = _clean_bullet(scope)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            cleaned_list.append(cleaned)
    db.kv_set(conn, "private_scopes", json.dumps(cleaned_list))
    return cleaned_list


@dataclass
class Profile:
    """Parsed user taste profile, watchlist, and DB-backed private scopes."""

    preferences: list[str] = field(default_factory=list)
    anti_interests: list[str] = field(default_factory=list)
    taste_makers: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    subscriptions: list[str] = field(default_factory=list)
    watchlist: list[str] = field(default_factory=list)
    private_scopes: list[str] = field(default_factory=list)

    @classmethod
    def load(
        cls,
        sync_dir: Path | str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> Profile:
        """Load TASTE_PROFILE.md and WATCHLIST.md from sync_dir and private_scopes from DB kv."""
        resolved_sync = (
            Path(sync_dir).expanduser()
            if sync_dir is not None
            else config.paths(ensure_dirs=False).sync
        )
        profile = cls()

        taste_text = _read_bounded_markdown(resolved_sync / "TASTE_PROFILE.md")
        if taste_text:
            cls._parse_into(taste_text, profile, default_section=None)

        watchlist_text = _read_bounded_markdown(resolved_sync / "WATCHLIST.md")
        if watchlist_text:
            cls._parse_into(watchlist_text, profile, default_section="watchlist")

        profile.private_scopes = load_private_scopes(conn)
        return profile

    @classmethod
    def _parse_into(
        cls,
        text: str,
        profile: Profile,
        *,
        default_section: str | None,
    ) -> None:
        current_attr: str | None = default_section
        for raw_line in text.splitlines():
            heading_match = _HEADING_RE.match(raw_line)
            if heading_match:
                heading_norm = heading_match.group(1).strip().lower()
                if heading_norm in _IGNORED_SECTIONS:
                    current_attr = None
                else:
                    current_attr = _SECTION_MAP.get(heading_norm, default_section)
                continue

            if current_attr is None:
                continue

            bullet_match = _BULLET_RE.match(raw_line)
            if not bullet_match:
                continue

            cleaned = _clean_bullet(bullet_match.group(1))
            if not cleaned:
                continue

            target_list: list[str] = getattr(profile, current_attr)
            if cleaned not in target_list:
                target_list.append(cleaned)
