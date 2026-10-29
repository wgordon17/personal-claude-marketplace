"""SQLite connection setup, migrations, HTTP cache helpers, and scrubbed text writers."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import config, redact, sanitize

_PKG_DIR = Path(__file__).resolve().parent
SCHEMA_SQL = (_PKG_DIR / "schema.sql").read_text(encoding="utf-8")
CACHE_SCHEMA_SQL = (_PKG_DIR / "cache_schema.sql").read_text(encoding="utf-8")

SCHEMA_VERSION = 1
MIGRATIONS: tuple[tuple[int, str], ...] = ((1, SCHEMA_SQL),)


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=30000;")


def connect(
    path: Path | str | None = None,
    *,
    is_cache: bool = False,
    auto_migrate: bool = False,
) -> sqlite3.Connection:
    """Open a SQLite connection with WAL, foreign keys, busy_timeout, and 0600 permissions."""
    os.umask(0o077)
    if path is None:
        resolved_paths = config.paths(ensure_dirs=True)
        target = resolved_paths.cache_db if is_cache else resolved_paths.db
    else:
        target = Path(path)

    is_memory = str(target) == ":memory:" or str(target).startswith("file::memory:")
    if not is_memory:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(target.parent, 0o700)

    conn = sqlite3.connect(str(target), timeout=30.0)
    conn.row_factory = sqlite3.Row
    if not is_memory and target.exists():
        os.chmod(target, 0o600)

    _apply_pragmas(conn)
    if is_cache:
        conn.executescript(CACHE_SCHEMA_SQL)
        conn.commit()
    elif auto_migrate:
        migrate(conn, db_path=None if is_memory else target)
    return conn


def connect_cache(path: Path | str | None = None) -> sqlite3.Connection:
    """Open and initialize the HTTP cache database."""
    return connect(path, is_cache=True)


def get_user_version(conn: sqlite3.Connection) -> int:
    """Return the current PRAGMA user_version of the database."""
    row = conn.execute("PRAGMA user_version;").fetchone()
    return int(row[0]) if row else 0


def _resolve_main_db_file(conn: sqlite3.Connection, db_path: Path | None) -> Path | None:
    if db_path is not None:
        return db_path
    rows = conn.execute("PRAGMA database_list;").fetchall()
    for row in rows:
        name = row[1]
        file_col = row[2]
        if name == "main" and file_col:
            return Path(str(file_col))
    return None


def migrate(
    conn: sqlite3.Connection,
    migrations: Sequence[tuple[int, str]] = MIGRATIONS,
    *,
    db_path: Path | None = None,
) -> int:
    """Apply pending schema migrations, backing up existing databases before upgrading."""
    _apply_pragmas(conn)
    current = get_user_version(conn)
    ordered = sorted(migrations, key=lambda item: item[0])
    pending = [(ver, sql) for ver, sql in ordered if ver > current]
    if not pending:
        return current

    if current > 0:
        main_file = _resolve_main_db_file(conn, db_path)
        if main_file is not None and main_file.exists():
            backup_path = main_file.parent / (main_file.name + ".pre-v" + str(current))
            backup_conn = sqlite3.connect(str(backup_path))
            try:
                conn.backup(backup_conn)
            finally:
                backup_conn.close()
            os.chmod(backup_path, 0o600)

    for ver, sql in pending:
        ver_int = int(ver)
        script = sql + "\nPRAGMA user_version = " + str(ver_int) + ";\n"
        conn.executescript(script)
        _apply_pragmas(conn)

    conn.commit()
    return get_user_version(conn)


def scrub_text(value: str | None) -> str | None:
    """Scrub secret patterns from a nullable string before writing to SQLite."""
    if value is None:
        return None
    return redact.scrub(value)


def clean_excerpt(value: str | None, limit: int = 600) -> str | None:
    """Sanitize and scrub an untrusted excerpt, enforcing the character limit."""
    if value is None:
        return None
    cleaned = sanitize.clean(value, limit=limit)
    scrubbed = redact.scrub(cleaned)
    if limit and limit > 0 and len(scrubbed) > limit:
        scrubbed = scrubbed[: limit - 1].rstrip() + "…"
    return scrubbed


def insert_signal(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    signal_type: str,
    title: str,
    fetched_at: str,
    dedupe_key: str,
    entity_id: str | None = None,
    confidence: str = "confirmed",
    external_id: str | None = None,
    url: str | None = None,
    published_at: str | None = None,
    untrusted_excerpt: str | None = None,
    payload_json: str | None = None,
    last_surfaced_at: str | None = None,
    withdrawn: int = 0,
) -> int:
    """Insert or update a signal row with scrubbed text and sanitized untrusted_excerpt."""
    safe_title = redact.scrub(sanitize.clean(title, limit=300))
    safe_url = scrub_text(url)
    safe_excerpt = clean_excerpt(untrusted_excerpt, limit=600)
    safe_payload = scrub_text(payload_json)
    safe_dedupe = redact.scrub(dedupe_key)

    cur = conn.execute(
        "INSERT INTO signals ("
        "entity_id, source_id, type, confidence, external_id, title, url, "
        "published_at, fetched_at, untrusted_excerpt, payload_json, "
        "dedupe_key, last_surfaced_at, withdrawn"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(dedupe_key) DO UPDATE SET "
        "title = excluded.title, "
        "url = excluded.url, "
        "fetched_at = excluded.fetched_at, "
        "untrusted_excerpt = excluded.untrusted_excerpt, "
        "payload_json = excluded.payload_json, "
        "withdrawn = excluded.withdrawn",
        (
            entity_id,
            source_id,
            signal_type,
            confidence,
            external_id,
            safe_title,
            safe_url,
            published_at,
            fetched_at,
            safe_excerpt,
            safe_payload,
            safe_dedupe,
            last_surfaced_at,
            withdrawn,
        ),
    )
    conn.commit()
    if cur.lastrowid:
        return int(cur.lastrowid)
    row = conn.execute(
        "SELECT signal_id FROM signals WHERE dedupe_key = ?",
        (safe_dedupe,),
    ).fetchone()
    return int(row[0]) if row else 0


def kv_get(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    """Fetch a value from the kv table."""
    row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row is not None else default


def kv_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Upsert a scrubbed value into the kv table."""
    safe_val = redact.scrub(value)
    conn.execute(
        "INSERT INTO kv(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, safe_val),
    )
    conn.commit()


def cache_get(cache_conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    """Retrieve a cached HTTP response row by key."""
    row = cache_conn.execute(
        "SELECT key, url, etag, last_modified, fetched_at, status, body "
        "FROM http_cache WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    return {
        "key": str(row["key"]),
        "url": row["url"],
        "etag": row["etag"],
        "last_modified": row["last_modified"],
        "fetched_at": str(row["fetched_at"]),
        "status": int(row["status"]) if row["status"] is not None else 200,
        "body": bytes(row["body"]) if row["body"] is not None else b"",
    }


def cache_put(
    cache_conn: sqlite3.Connection,
    *,
    key: str,
    url: str,
    etag: str | None,
    last_modified: str | None,
    fetched_at: str,
    status: int,
    body: bytes,
) -> None:
    """Store or update an HTTP response in http_cache."""
    cache_conn.execute(
        "INSERT INTO http_cache(key, url, etag, last_modified, fetched_at, status, body) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET "
        "url = excluded.url, "
        "etag = excluded.etag, "
        "last_modified = excluded.last_modified, "
        "fetched_at = excluded.fetched_at, "
        "status = excluded.status, "
        "body = excluded.body",
        (key, url, etag, last_modified, fetched_at, status, sqlite3.Binary(body)),
    )
    cache_conn.commit()


def purge_cache(
    cache_conn: sqlite3.Connection,
    days: int = 14,
    *,
    now: datetime | str | None = None,
) -> int:
    """Delete http_cache entries older than the given number of days."""
    if now is None:
        ref_dt = datetime.now(UTC)
    elif isinstance(now, str):
        parsed = datetime.fromisoformat(now.replace("Z", "+00:00"))
        ref_dt = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    else:
        ref_dt = now if now.tzinfo is not None else now.replace(tzinfo=UTC)

    cutoff = (ref_dt - timedelta(days=days)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = cache_conn.execute("DELETE FROM http_cache WHERE fetched_at < ?", (cutoff,))
    cache_conn.commit()
    return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
