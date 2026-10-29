"""Tests for SQLite schema, migrations, FTS5 triggers, cache, and SQL safety."""

from __future__ import annotations

import re
import sqlite3
import stat
import sys
from pathlib import Path

import pytest
from tech_radar import config, db

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import secret_samples  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG_DIR = REPO_ROOT / "tech-radar" / "skills" / "radar" / "scripts" / "tech_radar"


def test_fresh_migrate_and_simulated_v1_to_v2_backup(tmp_path: Path) -> None:
    """Fresh migrate sets user_version=1 without backup; v1->v2 writes radar.db.pre-v1."""
    db_file = tmp_path / "state" / "radar.db"
    conn = db.connect(db_file)
    try:
        assert db.get_user_version(conn) == 0
        new_ver = db.migrate(conn, db_path=db_file)
        assert new_ver == 1
        assert db.get_user_version(conn) == 1
        backup_v0 = db_file.parent / "radar.db.pre-v0"
        assert not backup_v0.exists()
        assert not (db_file.parent / "radar.db.pre-v1").exists()

        # Simulate a v2 migration on top of the existing v1 database
        fake_migrations = [
            *db.MIGRATIONS,
            (2, "CREATE TABLE test_v2_table (id INTEGER PRIMARY KEY, note TEXT);"),
        ]
        ver2 = db.migrate(conn, migrations=fake_migrations, db_path=db_file)
        assert ver2 == 2
        backup_v1 = db_file.parent / "radar.db.pre-v1"
        assert backup_v1.is_file()
        assert stat.S_IMODE(backup_v1.stat().st_mode) == 0o600

        # Verify backup_v1 is at user_version 1 and lacks test_v2_table
        bconn = sqlite3.connect(str(backup_v1))
        try:
            assert bconn.execute("PRAGMA user_version;").fetchone()[0] == 1
            tables = {
                r[0]
                for r in bconn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "test_v2_table" not in tables
        finally:
            bconn.close()
    finally:
        conn.close()


def test_db_and_cache_file_modes_0600() -> None:
    """Both radar.db and cache.db are created with 0600 permissions."""
    resolved = config.paths(ensure_dirs=True)
    conn = db.connect(resolved.db, auto_migrate=True)
    cache_conn = db.connect_cache(resolved.cache_db)
    try:
        assert resolved.db.is_file()
        assert resolved.cache_db.is_file()
        assert stat.S_IMODE(resolved.db.stat().st_mode) == 0o600
        assert stat.S_IMODE(resolved.cache_db.stat().st_mode) == 0o600
    finally:
        conn.close()
        cache_conn.close()


def test_signals_fts_and_digests_fts_sync_on_insert_update_delete() -> None:
    """FTS5 triggers keep signals_fts and digests_fts synchronized on insert, update, and delete."""
    conn = db.connect(auto_migrate=True)
    try:
        sig_id = db.insert_signal(
            conn,
            source_id="hn_algolia",
            signal_type="horizon",
            title="Astral uv workspace lockfile improvements",
            fetched_at="2026-10-28T07:00:00Z",
            dedupe_key="hn|1001",
            untrusted_excerpt="Fast dependency resolver for monorepos",
        )
        hits = conn.execute(
            "SELECT rowid FROM signals_fts WHERE signals_fts MATCH ?",
            ("monorepos",),
        ).fetchall()
        assert [r[0] for r in hits] == [sig_id]

        # Update signal excerpt
        conn.execute(
            "UPDATE signals SET untrusted_excerpt = ? WHERE signal_id = ?",
            ("Updated excerpt mentioning rust-analyzer", sig_id),
        )
        conn.commit()
        old_hits = conn.execute(
            "SELECT rowid FROM signals_fts WHERE signals_fts MATCH ?",
            ("monorepos",),
        ).fetchall()
        assert old_hits == []
        new_hits = conn.execute(
            "SELECT rowid FROM signals_fts WHERE signals_fts MATCH ?",
            ('"rust-analyzer"',),
        ).fetchall()
        assert [r[0] for r in new_hits] == [sig_id]

        # Delete signal
        conn.execute("DELETE FROM signals WHERE signal_id = ?", (sig_id,))
        conn.commit()
        after_del = conn.execute(
            "SELECT rowid FROM signals_fts WHERE signals_fts MATCH ?",
            ('"rust-analyzer"',),
        ).fetchall()
        assert after_del == []

        # Also check digests_fts triggers
        conn.execute(
            "INSERT INTO runs(run_id, mode, requested_mode, started_at, status) "
            "VALUES (?, ?, ?, ?, ?)",
            ("20261028T070000Z-daily", "daily", "daily", "2026-10-28T07:00:00Z", "ok"),
        )
        conn.execute(
            "INSERT INTO digests(run_id, path, markdown) VALUES (?, ?, ?)",
            (
                "20261028T070000Z-daily",
                "runs/20261028T070000Z-daily/digest.md",
                "UniqueDigestTokenAlpha",
            ),
        )
        conn.commit()
        d_hits = conn.execute(
            "SELECT rowid FROM digests_fts WHERE digests_fts MATCH ?",
            ("UniqueDigestTokenAlpha",),
        ).fetchall()
        assert len(d_hits) == 1
    finally:
        conn.close()


def test_check_constraints_and_confirm_batches_columns() -> None:
    """Verify CHECK constraints on decisions, alerts, pending, and confirm_batches columns."""
    conn = db.connect(auto_migrate=True)
    try:
        conn.execute(
            "INSERT INTO entities(entity_id, kind, name, visibility, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("pkg:pypi/litellm", "package", "litellm", "public", "2026-10-28T07:00:00Z"),
        )
        conn.execute(
            "INSERT INTO items(item_id, entity_id, state, created_at) VALUES (?, ?, ?, ?)",
            ("R-0001", "pkg:pypi/litellm", "active", "2026-10-28T07:00:00Z"),
        )
        conn.commit()

        # decisions.actor='agent' -> IntegrityError
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO decisions(item_id, verb, channel, actor, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("R-0001", "adopt", "cli", "agent", "2026-10-28T07:00:00Z"),
            )

        # alerts.kind='other' -> IntegrityError
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO alerts(tier, kind, confidence, state, dedupe_key, raised_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("P1", "other", "confirmed", "open", "a|1", "2026-10-28T07:00:00Z"),
            )

        # pending.channel='cli' -> IntegrityError
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO pending(kind, payload_json, channel, created_at, status) "
                "VALUES (?, ?, ?, ?, ?)",
                ("decision", "{}", "cli", "2026-10-28T07:00:00Z", "staged"),
            )

        # confirm_batches has exact Spec 02 columns and no plaintext code column
        cols = [row[1] for row in conn.execute("PRAGMA table_info(confirm_batches);").fetchall()]
        assert cols == [
            "batch_id",
            "code_hash",
            "salt",
            "created_at",
            "expires_at",
            "attempts",
            "status",
        ]
        assert "code" not in cols
    finally:
        conn.close()


def test_purge_cache_removes_old_and_keeps_recent() -> None:
    """purge_cache(days=14) deletes a 15-day-old row and retains a 13-day-old row."""
    cache_conn = db.connect_cache()
    try:
        db.cache_put(
            cache_conn,
            key="old-key",
            url="https://pypi.org/pypi/old/json",
            etag='"e1"',
            last_modified=None,
            fetched_at="2026-10-13T00:00:00Z",
            status=200,
            body=b'{"old":1}',
        )
        db.cache_put(
            cache_conn,
            key="recent-key",
            url="https://pypi.org/pypi/recent/json",
            etag='"e2"',
            last_modified=None,
            fetched_at="2026-10-15T12:00:00Z",
            status=200,
            body=b'{"recent":1}',
        )

        deleted = db.purge_cache(cache_conn, days=14, now="2026-10-28T07:00:00Z")
        assert deleted == 1
        assert db.cache_get(cache_conn, "old-key") is None
        recent = db.cache_get(cache_conn, "recent-key")
        assert recent is not None
        assert recent["body"] == b'{"recent":1}'
    finally:
        cache_conn.close()


def test_text_writers_route_through_scrub_and_clean() -> None:
    """insert_signal scrubs secrets and cleans untrusted_excerpt to <=600 chars."""
    conn = db.connect(auto_migrate=True)
    try:
        samples = secret_samples.build_samples()
        gh_tok = samples["github_token"]
        raw_excerpt = f"<script>evil()</script> token={gh_tok} " + ("x" * 700)
        sig_id = db.insert_signal(
            conn,
            source_id="gh_releases",
            signal_type="feature",
            title=f"Release with {gh_tok}",
            fetched_at="2026-10-28T07:00:00Z",
            dedupe_key="gh|rel|1",
            untrusted_excerpt=raw_excerpt,
        )
        row = conn.execute(
            "SELECT title, untrusted_excerpt FROM signals WHERE signal_id = ?",
            (sig_id,),
        ).fetchone()
        assert row is not None
        assert gh_tok not in row["title"]
        assert "[REDACTED:github_token]" in row["title"]
        assert gh_tok not in row["untrusted_excerpt"]
        assert "<script>" not in row["untrusted_excerpt"]
        assert len(row["untrusted_excerpt"]) <= 600
    finally:
        conn.close()


def test_no_interpolated_sql_in_package() -> None:
    """Grep test: no execute(f, executescript(f, or .format( inside execute( under P/."""
    py_files = sorted(PKG_DIR.rglob("*.py"))
    assert py_files, "Expected Python files under P/"
    bad_execute_f = re.compile(r"\bexecutescript?\s*\(\s*[fF][\"']")
    bad_execute_format = re.compile(r"\bexecutescript?\s*\([^)]*\.format\s*\(")

    violations: list[str] = []
    for path in py_files:
        text = path.read_text(encoding="utf-8")
        if bad_execute_f.search(text) or bad_execute_format.search(text):
            violations.append(str(path.relative_to(REPO_ROOT)))
    assert not violations, f"Found non-parameterized SQL in: {violations}"
