"""Unit tests for review_rules.db."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

import pytest

import review_rules.db as db_mod
from review_rules.db import (
    MIGRATIONS,
    SCHEMA_VERSION,
    StoreInsecure,
    _enforce_sidecar_0600,
    connect,
    counters,
)

_EXPECTED_TABLES = {
    "meta",
    "events",
    "event_labels",
    "classify_batches",
    "classify_requests",
    "ingest_files",
    "event_aliases",
    "t3_thread_sessions",
    "merge_checks",
    "runs",
    "loaded",
    "findings",
    "outcomes",
    "suppressions",
}


def _insert_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    skill: str = "pr-review",
    started_at: str = "2026-11-20T00:00:00Z",
) -> None:
    conn.execute(
        """
        INSERT INTO runs (run_id, skill, repo, started_at, status)
        VALUES (?, ?, 'org/repo', ?, 'finalized')
        """,
        (run_id, skill, started_at),
    )


def _insert_finding(
    conn: sqlite3.Connection,
    run_id: str,
    finding_key: str,
    *,
    reviewer: str = "security",
    category: str = "security",
    rule_id: str | None = "r-test",
    verdict: str | None = "verified",
) -> None:
    conn.execute(
        """
        INSERT INTO findings (run_id, finding_key, reviewer, category, rule_id, verdict)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (run_id, finding_key, reviewer, category, rule_id, verdict),
    )


def _insert_outcome(
    conn: sqlite3.Connection,
    finding_key: str,
    kind: str,
    *,
    strength: str = "strong",
    source: str = "github",
    ref: str,
    ts: str,
    run_id: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO outcomes (finding_key, kind, strength, source, ref, run_id, ts)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (finding_key, kind, strength, source, ref, run_id, ts),
    )


class TestDbConnectAndSchema:
    def test_fresh_create_and_idempotent_rerun(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "store" / "review.db"
        monkeypatch.setenv("REVIEW_RULES_DB", str(db_path))

        conn1 = connect()
        try:
            tables = {
                row[0]
                for row in conn1.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            assert _EXPECTED_TABLES.issubset(tables)

            views = {
                row[0]
                for row in conn1.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'view'"
                ).fetchall()
            }
            assert "rule_counters" in views

            ver = conn1.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
            assert int(ver) == SCHEMA_VERSION

            journal_mode = conn1.execute("PRAGMA journal_mode").fetchone()[0]
            assert str(journal_mode).lower() == "wal"

            foreign_keys = conn1.execute("PRAGMA foreign_keys").fetchone()[0]
            assert int(foreign_keys) == 1

            # Perform a write to exercise WAL/SHM creation and verify 0600 modes.
            conn1.execute("BEGIN IMMEDIATE")
            _insert_run(conn1, "run-1")
            conn1.execute("COMMIT")
        finally:
            conn1.close()

        assert stat.S_IMODE(os.lstat(db_path).st_mode) == 0o600
        for sidecar in (Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
            if sidecar.exists():
                assert (stat.S_IMODE(os.lstat(sidecar).st_mode) & 0o077) == 0

        # Idempotent second connect preserves existing rows and schema_version.
        conn2 = connect()
        try:
            assert conn2.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
            ver2 = conn2.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[
                0
            ]
            assert int(ver2) == SCHEMA_VERSION
        finally:
            conn2.close()

    def test_failing_migration_rolls_back(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "store" / "review.db"
        monkeypatch.setenv("REVIEW_RULES_DB", str(db_path))

        bad_migrations = (
            *MIGRATIONS,
            (
                2,
                (
                    "CREATE TABLE temp_v2 (id INTEGER PRIMARY KEY)",
                    "INSERT INTO nonexistent_table VALUES (1)",
                ),
            ),
        )
        with pytest.raises(sqlite3.Error):
            connect(db_path, migrations=bad_migrations)

        conn = connect(db_path)
        try:
            ver = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
            assert int(ver) == SCHEMA_VERSION
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'temp_v2'"
            ).fetchone()
            assert row is None
        finally:
            conn.close()

    def test_refuses_wide_mode_or_symlink_db_and_sidecars(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "store" / "review.db"
        monkeypatch.setenv("REVIEW_RULES_DB", str(db_path))

        conn = connect()
        conn.close()

        # 0644 review.db raises StoreInsecure and is NOT auto-repaired.
        os.chmod(db_path, 0o644)
        with pytest.raises(StoreInsecure, match="Insecure permissions"):
            connect()
        assert stat.S_IMODE(os.lstat(db_path).st_mode) == 0o644

        # Symlinked review.db raises StoreInsecure.
        os.chmod(db_path, 0o600)
        real_db = tmp_path / "store" / "real.db"
        db_path.rename(real_db)
        os.symlink(real_db, db_path)
        with pytest.raises(StoreInsecure, match="symlink"):
            connect()

        # Restore regular review.db, plant symlinked -wal sidecar.
        db_path.unlink()
        real_db.rename(db_path)
        fake_wal_target = tmp_path / "store" / "other.wal"
        fake_wal_target.write_bytes(b"")
        os.chmod(fake_wal_target, 0o600)
        wal_path = Path(f"{db_path}-wal")
        wal_path.unlink(missing_ok=True)
        os.symlink(fake_wal_target, wal_path)
        with pytest.raises(StoreInsecure, match="symlink"):
            connect()
        wal_path.unlink()

        # Non-regular -shm sidecar (directory) raises StoreInsecure.
        shm_path = Path(f"{db_path}-shm")
        shm_path.unlink(missing_ok=True)
        shm_path.mkdir(mode=0o700)
        with pytest.raises(StoreInsecure, match="non-regular"):
            connect()
        shm_path.rmdir()

        # Pre-existing wide-mode (0644) -wal sidecar raises StoreInsecure BEFORE open.
        wal_path.write_bytes(b"preexisting-wal")
        os.chmod(wal_path, 0o644)
        with pytest.raises(StoreInsecure, match="Insecure permissions"):
            connect()
        assert stat.S_IMODE(os.lstat(wal_path).st_mode) == 0o644
        wal_path.unlink()

    def test_explicit_db_path_verifies_parent_without_touching_default_store(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        default_db = tmp_path / "default-store" / "review.db"
        monkeypatch.setenv("REVIEW_RULES_DB", str(default_db))

        custom_db = tmp_path / "custom-store" / "review.db"
        conn = connect(custom_db)
        conn.close()
        assert not (tmp_path / "default-store").exists()
        assert stat.S_IMODE(os.lstat(custom_db.parent).st_mode) == 0o700

        wide_parent = tmp_path / "wide-store"
        wide_parent.mkdir()
        os.chmod(wide_parent, 0o755)
        with pytest.raises(StoreInsecure, match="Insecure permissions"):
            connect(wide_parent / "review.db")

    def test_rejects_future_or_corrupt_schema_version(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "store" / "review.db"
        monkeypatch.setenv("REVIEW_RULES_DB", str(db_path))

        conn = connect()
        conn.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
        conn.close()
        with pytest.raises(StoreInsecure, match="Unsupported database schema_version"):
            connect()

        raw_conn = sqlite3.connect(db_path)
        raw_conn.execute("UPDATE meta SET value = 'corrupt' WHERE key = 'schema_version'")
        raw_conn.commit()
        raw_conn.close()
        with pytest.raises(StoreInsecure, match="Corrupt database schema_version"):
            connect()

    def test_secondary_indexes_created(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("REVIEW_RULES_DB", str(tmp_path / "store" / "review.db"))
        conn = connect()
        try:
            indexes = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                ).fetchall()
            }
            assert {
                "idx_loaded_rule_id",
                "idx_findings_rule_id",
                "idx_findings_finding_key",
                "idx_outcomes_key_ts",
            }.issubset(indexes)
        finally:
            conn.close()

    def test_refuses_preopen_wide_mode_wal_and_shm_without_chmod(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "store" / "review.db"
        monkeypatch.setenv("REVIEW_RULES_DB", str(db_path))

        conn = connect()
        conn.close()

        wal_path = Path(f"{db_path}-wal")
        shm_path = Path(f"{db_path}-shm")
        wal_path.unlink(missing_ok=True)
        shm_path.unlink(missing_ok=True)

        def _forbid_sqlite_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            raise AssertionError("sqlite3.connect must not be called with wide-mode sidecar")

        monkeypatch.setattr(sqlite3, "connect", _forbid_sqlite_connect)

        # Wide-mode (0644) -wal is refused before sqlite3.connect and not auto-chmodded.
        wal_path.write_bytes(b"preexisting-wal")
        os.chmod(wal_path, 0o644)
        with pytest.raises(StoreInsecure, match="Insecure permissions"):
            connect()
        assert stat.S_IMODE(os.lstat(wal_path).st_mode) == 0o644
        wal_path.unlink()

        # Wide-mode (0644) -shm is also refused before sqlite3.connect and not auto-chmodded.
        shm_path.write_bytes(b"preexisting-shm")
        os.chmod(shm_path, 0o644)
        with pytest.raises(StoreInsecure, match="Insecure permissions"):
            connect()
        assert stat.S_IMODE(os.lstat(shm_path).st_mode) == 0o644

        # Post-open helper _enforce_sidecar_0600 tightens newly created wide-mode sidecar.
        _enforce_sidecar_0600(shm_path)
        assert stat.S_IMODE(os.lstat(shm_path).st_mode) == 0o600
        shm_path.unlink()

    def test_refuses_wrong_owner_sidecar_in_connect_and_enforce(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "store" / "review.db"
        monkeypatch.setenv("REVIEW_RULES_DB", str(db_path))

        conn = connect()
        conn.close()

        wal_path = Path(f"{db_path}-wal")
        shm_path = Path(f"{db_path}-shm")
        wal_path.write_bytes(b"")
        shm_path.write_bytes(b"")
        os.chmod(wal_path, 0o600)
        os.chmod(shm_path, 0o600)

        real_lstat = os.lstat
        real_uid = os.getuid()

        def _spoof_owner_lstat(target_path: Path, path: object, *args: object) -> os.stat_result:
            st = real_lstat(path, *args)  # type: ignore[arg-type]
            if Path(os.fspath(path)) == target_path:  # type: ignore[arg-type]
                fields = list(st)
                fields[4] = real_uid + 1
                return os.stat_result(fields)
            return st

        # Wrong-owner -wal raises StoreInsecure on connect().
        monkeypatch.setattr(os, "lstat", lambda p, *a: _spoof_owner_lstat(wal_path, p, *a))
        with pytest.raises(StoreInsecure, match="Untrusted owner uid"):
            connect()

        # Wrong-owner -shm raises StoreInsecure on connect().
        monkeypatch.setattr(os, "lstat", lambda p, *a: _spoof_owner_lstat(shm_path, p, *a))
        with pytest.raises(StoreInsecure, match="Untrusted owner uid"):
            connect()

        # Direct _enforce_sidecar_0600 check also rejects wrong owner via fstat.
        monkeypatch.undo()
        monkeypatch.setattr(os, "getuid", lambda: real_uid + 1)
        with pytest.raises(StoreInsecure, match="Untrusted owner uid"):
            _enforce_sidecar_0600(wal_path)

    def test_foreign_keys_enforced_on_insert(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("REVIEW_RULES_DB", str(tmp_path / "store" / "review.db"))
        conn = connect()
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO loaded (run_id, rule_id, reviewer) VALUES (?, ?, ?)",
                    ("missing-run", "r-one", "security"),
                )
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO findings (run_id, finding_key, reviewer, category, rule_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    ("missing-run", "fk-missing", "security", "security", "r-one"),
                )
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO outcomes (finding_key, kind, strength, source, ref, run_id, ts)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "fk-missing",
                        "accepted",
                        "strong",
                        "github",
                        "ref-1",
                        "missing-run",
                        "2026-11-20T00:00:00Z",
                    ),
                )
        finally:
            conn.close()

    def test_concurrent_migration_rereads_schema_version_inside_begin_immediate(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "store" / "review.db"
        monkeypatch.setenv("REVIEW_RULES_DB", str(db_path))

        connect(db_path).close()

        v2_migrations = (
            *MIGRATIONS,
            (2, ("ALTER TABLE runs ADD COLUMN v2_note TEXT",)),
        )
        real_version_fn = db_mod._current_schema_version
        calls = 0

        def _racing_schema_version(conn: sqlite3.Connection) -> int:
            nonlocal calls
            calls += 1
            ver = real_version_fn(conn)
            if calls == 1:
                # Simulate another process completing the non-idempotent v2 migration
                # after our pre-lock read and before our BEGIN IMMEDIATE.
                racer = sqlite3.connect(db_path)
                try:
                    racer.execute("ALTER TABLE runs ADD COLUMN v2_note TEXT")
                    racer.execute(
                        "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', '2')"
                    )
                    racer.commit()
                finally:
                    racer.close()
            return ver

        monkeypatch.setattr(db_mod, "_current_schema_version", _racing_schema_version)
        conn = connect(db_path, migrations=v2_migrations)
        try:
            assert calls == 2
            cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
            assert "v2_note" in cols
            ver = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
            assert int(ver) == 2
        finally:
            conn.close()

        # Also verify TOCTOU future-version jump inside BEGIN IMMEDIATE raises and rolls back.
        calls_future = 0
        v3_migrations = (*v2_migrations, (3, ("ALTER TABLE runs ADD COLUMN v3_note TEXT",)))

        def _racing_future_version(conn: sqlite3.Connection) -> int:
            nonlocal calls_future
            calls_future += 1
            ver = real_version_fn(conn)
            if calls_future == 1:
                racer = sqlite3.connect(db_path)
                try:
                    racer.execute(
                        "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', '99')"
                    )
                    racer.commit()
                finally:
                    racer.close()
            return ver

        monkeypatch.setattr(db_mod, "_current_schema_version", _racing_future_version)
        with pytest.raises(StoreInsecure, match="Unsupported database schema_version"):
            connect(db_path, migrations=v3_migrations)
        assert calls_future == 2

    def test_rejects_negative_or_noncanonical_schema_version(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "store" / "review.db"
        monkeypatch.setenv("REVIEW_RULES_DB", str(db_path))

        connect().close()

        for bad_val in ("-1", "01"):
            raw_conn = sqlite3.connect(db_path)
            raw_conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (bad_val,))
            raw_conn.commit()
            raw_conn.close()
            with pytest.raises(StoreInsecure, match="Corrupt database schema_version"):
                connect()


class TestRuleCounters:
    def test_unseen_rule_returns_zeros(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("REVIEW_RULES_DB", str(tmp_path / "store" / "review.db"))
        conn = connect()
        try:
            assert counters(conn, "r-does-not-exist") == {
                "rule_id": "r-does-not-exist",
                "loaded": 0,
                "fired": 0,
                "verified": 0,
                "false_positive": 0,
                "accepted": 0,
                "dismissed": 0,
                "last_fired": None,
            }
        finally:
            conn.close()

    def test_excludes_verifier_and_fix_from_loaded(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("REVIEW_RULES_DB", str(tmp_path / "store" / "review.db"))
        conn = connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _insert_run(conn, "run-pr-1", skill="pr-review", started_at="2026-11-20T01:00:00Z")
            _insert_run(conn, "run-fix-1", skill="fix", started_at="2026-11-20T02:00:00Z")
            # Loaded by two regular reviewers + verifier in run-pr-1 -> counts as 1 loaded.
            conn.executemany(
                "INSERT INTO loaded (run_id, rule_id, reviewer) VALUES (?, ?, ?)",
                [
                    ("run-pr-1", "r-one", "security"),
                    ("run-pr-1", "r-one", "qa"),
                    ("run-pr-1", "r-one", "verifier"),
                    # Loaded in a fix run -> excluded completely.
                    ("run-fix-1", "r-one", "security"),
                    # Loaded only by verifier -> loaded stays 0.
                    ("run-pr-1", "r-verifier-only", "verifier"),
                ],
            )
            conn.execute("COMMIT")

            assert counters(conn, "r-one")["loaded"] == 1
            assert counters(conn, "r-verifier-only")["loaded"] == 0
        finally:
            conn.close()

    def test_dedupes_finding_key_across_runs_and_uses_latest_strong_disposition(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("REVIEW_RULES_DB", str(tmp_path / "store" / "review.db"))
        conn = connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _insert_run(conn, "run-1", started_at="2026-11-20T01:00:00Z")
            _insert_run(conn, "run-2", started_at="2026-11-20T02:00:00Z")

            _insert_finding(conn, "run-1", "fk-1", rule_id="r-helm", verdict="verified")
            _insert_finding(conn, "run-2", "fk-1", rule_id="r-helm", verdict="false_positive")
            _insert_outcome(
                conn,
                "fk-1",
                "dismissed",
                strength="strong",
                ref="comment-1",
                ts="2026-11-20T03:00:00Z",
                run_id="run-1",
            )
            # Weak outcome must not affect accepted/dismissed counters.
            _insert_outcome(
                conn,
                "fk-1",
                "accepted",
                strength="weak",
                ref="comment-weak",
                ts="2026-11-20T04:00:00Z",
                run_id="run-2",
            )
            conn.execute("COMMIT")

            c1 = counters(conn, "r-helm")
            assert c1["fired"] == 2
            assert c1["verified"] == 1
            assert c1["false_positive"] == 1
            assert c1["dismissed"] == 1
            assert c1["accepted"] == 0
            assert c1["last_fired"] == "2026-11-20T02:00:00Z"

            # Later strong acceptance flips fk-1 from dismissed=1 to accepted=1, dismissed=0.
            conn.execute("BEGIN IMMEDIATE")
            _insert_outcome(
                conn,
                "fk-1",
                "accepted",
                strength="strong",
                ref="commit-fix",
                ts="2026-11-21T01:00:00Z",
                run_id="run-2",
            )
            conn.execute("COMMIT")

            c2 = counters(conn, "r-helm")
            assert c2["accepted"] == 1
            assert c2["dismissed"] == 0
        finally:
            conn.close()

    def test_rule_counters_attributes_changed_rule_id_to_latest_and_ignores_null(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("REVIEW_RULES_DB", str(tmp_path / "store" / "review.db"))
        conn = connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _insert_run(conn, "run-1", started_at="2026-11-20T01:00:00Z")
            _insert_run(conn, "run-2", started_at="2026-11-20T02:00:00Z")
            _insert_run(conn, "run-3", started_at="2026-11-20T03:00:00Z")

            # fk-switch changes rule_id from r-old -> r-new, then later fires with rule_id=NULL.
            _insert_finding(conn, "run-1", "fk-switch", rule_id="r-old", verdict="verified")
            _insert_finding(conn, "run-2", "fk-switch", rule_id="r-new", verdict="false_positive")
            _insert_finding(conn, "run-3", "fk-switch", rule_id=None, verdict="verified")
            _insert_outcome(
                conn,
                "fk-switch",
                "accepted",
                strength="strong",
                ref="commit-switch",
                ts="2026-11-20T04:00:00Z",
                run_id="run-2",
            )

            # fk-null-only has only rule_id=NULL + strong outcome -> never counted under any rule.
            _insert_finding(conn, "run-1", "fk-null-only", rule_id=None, verdict="verified")
            _insert_outcome(
                conn,
                "fk-null-only",
                "dismissed",
                strength="strong",
                ref="comment-null",
                ts="2026-11-20T04:00:00Z",
                run_id="run-1",
            )
            conn.execute("COMMIT")

            c_old = counters(conn, "r-old")
            assert c_old == {
                "rule_id": "r-old",
                "loaded": 0,
                "fired": 1,
                "verified": 1,
                "false_positive": 0,
                "accepted": 0,
                "dismissed": 0,
                "last_fired": "2026-11-20T01:00:00Z",
            }

            c_new = counters(conn, "r-new")
            assert c_new == {
                "rule_id": "r-new",
                "loaded": 0,
                "fired": 1,
                "verified": 0,
                "false_positive": 1,
                "accepted": 1,
                "dismissed": 0,
                "last_fired": "2026-11-20T02:00:00Z",
            }

            # rule_counters view agrees and never produces a NULL rule_id row.
            view_rows = {
                row["rule_id"]: dict(row)
                for row in conn.execute("SELECT * FROM rule_counters").fetchall()
            }
            assert None not in view_rows
            assert set(view_rows) == {"r-old", "r-new"}
            assert view_rows["r-old"] == c_old
            assert view_rows["r-new"] == c_new
        finally:
            conn.close()
