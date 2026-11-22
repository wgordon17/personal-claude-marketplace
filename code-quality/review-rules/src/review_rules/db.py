"""SQLite schema v1, transactional migrations, and store security enforcement."""

from __future__ import annotations

import contextlib
import errno
import os
import sqlite3
import stat
from pathlib import Path
from typing import Any

from review_rules.paths import (
    StoreInsecure,
    ensure_store_dir,
    get_db_path,
    verify_private_path,
)

__all__ = [
    "MIGRATIONS",
    "SCHEMA_VERSION",
    "SCHEMA_V1_STATEMENTS",
    "StoreInsecure",
    "connect",
    "counters",
]

SCHEMA_VERSION = 1

SCHEMA_V1_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        event_id TEXT PRIMARY KEY,
        source TEXT NOT NULL CHECK (
            source IN (
                'claude_code', 't3', 'omp', 'claude_mem',
                'cursor', 'hook', 'github', 'dev_guard'
            )
        ),
        native_id TEXT NOT NULL,
        session_id TEXT,
        ts TEXT,
        cwd TEXT,
        repo TEXT,
        branch TEXT,
        pr_url TEXT,
        author TEXT,
        author_class TEXT NOT NULL CHECK (
            author_class IN ('owner', 'member', 'coderabbit', 'bot', 'unknown')
        ),
        kind TEXT NOT NULL CHECK (
            kind IN ('prompt', 'slash', 'queued', 'bash', 'answer', 'review_comment', 'decision')
        ),
        text TEXT NOT NULL,
        text_sha256 TEXT NOT NULL,
        thread_state TEXT,
        ingested_at TEXT NOT NULL,
        UNIQUE (source, native_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS event_labels (
        event_id TEXT NOT NULL REFERENCES events(event_id),
        label TEXT NOT NULL CHECK (label IN ('correction', 'preference', 'noise', 'skipped')),
        lesson TEXT,
        languages TEXT,
        paths TEXT,
        confidence REAL,
        model TEXT,
        prompt_version TEXT NOT NULL,
        labeled_at TEXT NOT NULL,
        PRIMARY KEY (event_id, prompt_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS classify_batches (
        batch_id TEXT PRIMARY KEY,
        stage TEXT NOT NULL CHECK (stage IN ('label', 'synthesize')),
        model TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        submitted_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('submitted', 'ended', 'collected', 'failed')),
        request_count INTEGER NOT NULL DEFAULT 0,
        input_tokens INTEGER,
        output_tokens INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS classify_requests (
        batch_id TEXT NOT NULL REFERENCES classify_batches(batch_id),
        custom_id TEXT NOT NULL,
        item_ids TEXT NOT NULL,
        PRIMARY KEY (batch_id, custom_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ingest_files (
        path TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        size INTEGER,
        mtime REAL,
        byte_offset INTEGER,
        watermark TEXT,
        ingested_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS event_aliases (
        source TEXT NOT NULL,
        native_id TEXT NOT NULL,
        event_id TEXT NOT NULL REFERENCES events(event_id),
        PRIMARY KEY (source, native_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS t3_thread_sessions (
        thread_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        PRIMARY KEY (thread_id, session_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        skill TEXT NOT NULL,
        repo TEXT,
        repo_path TEXT,
        pr INTEGER,
        branch TEXT,
        base_sha TEXT,
        head_sha TEXT,
        rules_sha TEXT,
        scratch_dir TEXT,
        worktree_anchors INTEGER NOT NULL DEFAULT 0,
        started_at TEXT NOT NULL,
        finalized_at TEXT,
        status TEXT NOT NULL CHECK (status IN ('open', 'finalized')),
        truncated TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS merge_checks (
        run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
        pr INTEGER,
        state TEXT NOT NULL CHECK (state IN ('open', 'merged', 'closed', 'unresolvable')),
        attempts INTEGER NOT NULL DEFAULT 0,
        checked_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS loaded (
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        rule_id TEXT NOT NULL,
        reviewer TEXT NOT NULL,
        PRIMARY KEY (run_id, rule_id, reviewer)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS findings (
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        finding_key TEXT NOT NULL,
        reviewer TEXT NOT NULL,
        category TEXT NOT NULL,
        rule_id TEXT,
        cited_rule TEXT,
        path TEXT,
        line INTEGER,
        anchor TEXT,
        anchor_verified INTEGER NOT NULL DEFAULT 0,
        verdict TEXT,
        classification TEXT,
        description_sha256 TEXT,
        PRIMARY KEY (run_id, finding_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS outcomes (
        finding_key TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('accepted', 'dismissed', 'fixed')),
        strength TEXT NOT NULL CHECK (strength IN ('strong', 'weak')),
        source TEXT NOT NULL,
        ref TEXT NOT NULL,
        run_id TEXT REFERENCES runs(run_id),
        repo TEXT,
        branch TEXT,
        ts TEXT NOT NULL,
        UNIQUE (finding_key, kind, source, ref)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS suppressions (
        finding_key TEXT PRIMARY KEY,
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_loaded_rule_id ON loaded(rule_id, run_id, reviewer)",
    "CREATE INDEX IF NOT EXISTS idx_findings_rule_id ON findings(rule_id, run_id, finding_key)",
    "CREATE INDEX IF NOT EXISTS idx_findings_finding_key ON findings(finding_key, run_id)",
    (
        "CREATE INDEX IF NOT EXISTS idx_outcomes_key_ts ON outcomes(finding_key, ts DESC) "
        "WHERE strength = 'strong' AND kind IN ('accepted', 'dismissed')"
    ),
    """
    CREATE VIEW IF NOT EXISTS rule_counters AS
    WITH all_rules AS (
        SELECT rule_id FROM loaded
        UNION
        SELECT rule_id FROM findings WHERE rule_id IS NOT NULL
    ),
    loaded_counts AS (
        SELECT l.rule_id, COUNT(DISTINCT l.run_id) AS loaded
        FROM loaded l
        JOIN runs r ON r.run_id = l.run_id
        WHERE l.reviewer != 'verifier' AND r.skill != 'fix'
        GROUP BY l.rule_id
    ),
    finding_counts AS (
        SELECT
            f.rule_id,
            COUNT(*) AS fired,
            SUM(CASE WHEN f.verdict = 'verified' THEN 1 ELSE 0 END) AS verified,
            SUM(CASE WHEN f.verdict = 'false_positive' THEN 1 ELSE 0 END) AS false_positive,
            MAX(r.started_at) AS last_fired
        FROM findings f
        JOIN runs r ON r.run_id = f.run_id
        WHERE f.rule_id IS NOT NULL
        GROUP BY f.rule_id
    ),
    latest_strong_outcome AS (
        SELECT
            o.finding_key,
            o.kind,
            ROW_NUMBER() OVER (
                PARTITION BY o.finding_key
                ORDER BY o.ts DESC, o.rowid DESC
            ) AS rn
        FROM outcomes o
        WHERE o.strength = 'strong' AND o.kind IN ('accepted', 'dismissed')
    ),
    latest_finding_rule AS (
        SELECT
            f.finding_key,
            f.rule_id,
            ROW_NUMBER() OVER (
                PARTITION BY f.finding_key
                ORDER BY r.started_at DESC, f.run_id DESC
            ) AS rn
        FROM findings f
        JOIN runs r ON r.run_id = f.run_id
        WHERE f.rule_id IS NOT NULL
    ),
    outcome_counts AS (
        SELECT
            lfr.rule_id,
            SUM(CASE WHEN lso.kind = 'accepted' THEN 1 ELSE 0 END) AS accepted,
            SUM(CASE WHEN lso.kind = 'dismissed' THEN 1 ELSE 0 END) AS dismissed
        FROM latest_strong_outcome lso
        JOIN latest_finding_rule lfr ON lfr.finding_key = lso.finding_key AND lfr.rn = 1
        WHERE lso.rn = 1
        GROUP BY lfr.rule_id
    )
    SELECT
        ar.rule_id AS rule_id,
        COALESCE(lc.loaded, 0) AS loaded,
        COALESCE(fc.fired, 0) AS fired,
        COALESCE(fc.verified, 0) AS verified,
        COALESCE(fc.false_positive, 0) AS false_positive,
        COALESCE(oc.accepted, 0) AS accepted,
        COALESCE(oc.dismissed, 0) AS dismissed,
        fc.last_fired AS last_fired
    FROM all_rules ar
    LEFT JOIN loaded_counts lc ON lc.rule_id = ar.rule_id
    LEFT JOIN finding_counts fc ON fc.rule_id = ar.rule_id
    LEFT JOIN outcome_counts oc ON oc.rule_id = ar.rule_id
    """,
)

MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = ((1, SCHEMA_V1_STATEMENTS),)


def _enforce_sidecar_0600(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR, getattr(errno, "EMLINK", -1)}:
            raise StoreInsecure(f"Refusing symlink SQLite sidecar: {path}") from exc
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise StoreInsecure(f"Expected regular SQLite sidecar, got non-regular: {path}")
        uid = os.getuid()
        if st.st_uid != uid:
            raise StoreInsecure(f"Untrusted owner uid {st.st_uid} on {path} (expected {uid})")
        if stat.S_IMODE(st.st_mode) & 0o077:
            os.fchmod(fd, 0o600)
    finally:
        os.close(fd)


def _current_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
    ).fetchone()
    if row is None:
        return 0
    ver_row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if ver_row is None:
        return 0
    raw_val = ver_row[0]
    try:
        ver = int(raw_val)
    except (TypeError, ValueError) as exc:
        raise StoreInsecure(f"Corrupt database schema_version: {raw_val!r}") from exc
    if str(ver) != str(raw_val).strip() or ver < 0:
        raise StoreInsecure(f"Corrupt database schema_version: {raw_val!r}")
    return ver


def connect(
    db_path: Path | None = None,
    *,
    migrations: tuple[tuple[int, tuple[str, ...]], ...] = MIGRATIONS,
) -> sqlite3.Connection:
    """Open the SQLite store with 0700/0600 checks, WAL, foreign keys, and migrations."""
    target = (
        Path(os.path.abspath(os.path.expanduser(str(db_path))))
        if db_path is not None
        else get_db_path()
    )
    ensure_store_dir(target.parent)

    # Check review.db and sidecars via os.lstat BEFORE sqlite3.connect()
    try:
        os.lstat(target)
    except FileNotFoundError:
        flags = (
            os.O_CREAT
            | os.O_EXCL
            | os.O_RDWR
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            fd = os.open(target, flags, 0o600)
        except FileExistsError:
            pass
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR, getattr(errno, "EMLINK", -1)}:
                raise StoreInsecure(f"Refusing symlink or non-file DB path: {target}") from exc
            raise
        else:
            os.close(fd)
    verify_private_path(target, is_dir=False)

    wal_path = Path(f"{target}-wal")
    shm_path = Path(f"{target}-shm")
    for sidecar in (wal_path, shm_path):
        with contextlib.suppress(FileNotFoundError):
            verify_private_path(sidecar, is_dir=False)

    old_umask = os.umask(0o077)
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(target, isolation_level=None)
        verify_private_path(target, is_dir=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 2000")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
    except Exception:
        if conn is not None:
            conn.close()
        raise
    finally:
        os.umask(old_umask)
    try:
        max_supported = migrations[-1][0] if migrations else SCHEMA_VERSION
        current = _current_schema_version(conn)
        if current > max_supported:
            raise StoreInsecure(
                f"Unsupported database schema_version {current} (max supported {max_supported})"
            )
        if current < max_supported:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = _current_schema_version(conn)
                if current > max_supported:
                    raise StoreInsecure(
                        f"Unsupported database schema_version {current} "
                        f"(max supported {max_supported})"
                    )
                for version, statements in migrations:
                    if version <= current:
                        continue
                    for stmt in statements:
                        conn.execute(stmt)
                    conn.execute(
                        "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                        (str(version),),
                    )
                    current = version
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

        _enforce_sidecar_0600(wal_path)
        _enforce_sidecar_0600(shm_path)
    except Exception:
        conn.close()
        raise
    return conn


def counters(conn: sqlite3.Connection, rule_id: str) -> dict[str, Any]:
    """Return rule_counters row for rule_id, or an all-zero dict if absent."""
    row = conn.execute(
        """
        WITH
        loaded_count AS (
            SELECT COUNT(DISTINCT l.run_id) AS loaded
            FROM loaded l
            JOIN runs r ON r.run_id = l.run_id
            WHERE l.rule_id = :rule_id
              AND l.reviewer != 'verifier'
              AND r.skill != 'fix'
        ),
        finding_stats AS (
            SELECT
                COUNT(*) AS fired,
                COALESCE(SUM(CASE WHEN f.verdict = 'verified' THEN 1 ELSE 0 END), 0) AS verified,
                COALESCE(
                    SUM(CASE WHEN f.verdict = 'false_positive' THEN 1 ELSE 0 END), 0
                ) AS false_positive,
                MAX(r.started_at) AS last_fired
            FROM findings f
            JOIN runs r ON r.run_id = f.run_id
            WHERE f.rule_id = :rule_id
        ),
        candidate_keys AS (
            SELECT DISTINCT finding_key
            FROM findings
            WHERE rule_id = :rule_id
        ),
        latest_finding_rule AS (
            SELECT
                f.finding_key,
                f.rule_id,
                ROW_NUMBER() OVER (
                    PARTITION BY f.finding_key
                    ORDER BY r.started_at DESC, f.run_id DESC
                ) AS rn
            FROM findings f
            JOIN runs r ON r.run_id = f.run_id
            WHERE f.finding_key IN (SELECT finding_key FROM candidate_keys)
              AND f.rule_id IS NOT NULL
        ),
        owned_keys AS (
            SELECT finding_key
            FROM latest_finding_rule
            WHERE rn = 1 AND rule_id = :rule_id
        ),
        latest_strong_outcome AS (
            SELECT
                o.finding_key,
                o.kind,
                ROW_NUMBER() OVER (
                    PARTITION BY o.finding_key
                    ORDER BY o.ts DESC, o.rowid DESC
                ) AS rn
            FROM outcomes o
            WHERE o.finding_key IN (SELECT finding_key FROM owned_keys)
              AND o.strength = 'strong'
              AND o.kind IN ('accepted', 'dismissed')
        ),
        outcome_stats AS (
            SELECT
                COALESCE(SUM(CASE WHEN kind = 'accepted' THEN 1 ELSE 0 END), 0) AS accepted,
                COALESCE(SUM(CASE WHEN kind = 'dismissed' THEN 1 ELSE 0 END), 0) AS dismissed
            FROM latest_strong_outcome
            WHERE rn = 1
        )
        SELECT
            lc.loaded,
            fs.fired,
            fs.verified,
            fs.false_positive,
            os.accepted,
            os.dismissed,
            fs.last_fired
        FROM loaded_count lc
        CROSS JOIN finding_stats fs
        CROSS JOIN outcome_stats os
        """,
        {"rule_id": rule_id},
    ).fetchone()
    assert row is not None
    return {
        "rule_id": rule_id,
        "loaded": int(row["loaded"]),
        "fired": int(row["fired"]),
        "verified": int(row["verified"]),
        "false_positive": int(row["false_positive"]),
        "accepted": int(row["accepted"]),
        "dismissed": int(row["dismissed"]),
        "last_fired": row["last_fired"],
    }
