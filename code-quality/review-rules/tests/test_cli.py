"""Unit tests for review_rules.cli (init, doctor, scratch)."""

from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import subprocess
import time
from pathlib import Path

import pytest

import review_rules.cli as cli_mod
from review_rules.cli import main


def _set_cli_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    key_path: Path | None = None,
    repo_path: Path | None = None,
) -> Path:
    store_dir = tmp_path / "store"
    db_path = store_dir / "review.db"
    monkeypatch.setenv("REVIEW_RULES_DB", str(db_path))
    monkeypatch.setenv("REVIEW_RULES_CONFIG", str(store_dir / "config.toml"))
    monkeypatch.setenv("REVIEW_RULES_SPOOL", str(store_dir / "spool.jsonl"))
    monkeypatch.setenv(
        "REVIEW_RULES_REPO",
        str(repo_path if repo_path is not None else tmp_path / "missing-rules-repo"),
    )
    monkeypatch.setenv(
        "REVIEW_RULES_ANTHROPIC_KEY_FILE",
        str(key_path if key_path is not None else tmp_path / "missing-anthropic.key"),
    )
    return store_dir


def _install_gh_stub(monkeypatch: pytest.MonkeyPatch, bin_dir: Path, *, exit_code: int) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    gh_script = bin_dir / "gh"
    gh_script.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
    os.chmod(gh_script, 0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")


class TestCliHelpAndScratch:
    def test_no_args_prints_help_and_returns_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main([]) == 0
        out = capsys.readouterr().out
        assert "review-rules" in out
        assert "init" in out
        assert "doctor" in out
        assert "scratch" in out

    def test_scratch_creates_0700_absolute_non_symlink_dir(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        store_dir = _set_cli_env(monkeypatch, tmp_path)
        assert main(["scratch"]) == 0
        out = capsys.readouterr().out.strip()
        created = Path(out)
        assert created.is_absolute()
        assert created.parent == store_dir / "scratch"
        st = os.lstat(created)
        assert stat.S_ISDIR(st.st_mode)
        assert not stat.S_ISLNK(st.st_mode)
        assert stat.S_IMODE(st.st_mode) == 0o700

        assert main(["scratch"]) == 0
        second = Path(capsys.readouterr().out.strip())
        assert second != created
        assert stat.S_IMODE(os.lstat(second).st_mode) == 0o700


class TestCliInit:
    def test_init_creates_store_config_and_prints_edit_guidance(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        store_dir = _set_cli_env(monkeypatch, tmp_path)
        assert main(["init"]) == 0
        out = capsys.readouterr().out

        assert "Edit(~/.local/share/review-rules/scratch/**)" in out
        assert "Write(~/.local/share/review-rules/scratch/**)" in out
        assert "permissions.additionalDirectories" in out

        assert stat.S_IMODE(os.lstat(store_dir).st_mode) == 0o700
        assert stat.S_IMODE(os.lstat(store_dir / "scratch").st_mode) == 0o700
        assert stat.S_IMODE(os.lstat(store_dir / "review.db").st_mode) == 0o600
        cfg_path = store_dir / "config.toml"
        assert stat.S_IMODE(os.lstat(cfg_path).st_mode) == 0o600
        cfg_text = cfg_path.read_text(encoding="utf-8")
        assert "# price_in_per_mtok = 1.0" in cfg_text
        assert "# price_out_per_mtok = 5.0" in cfg_text
        assert "# max_cost_usd" in cfg_text

        # Re-running init preserves existing config.
        cfg_path.write_text("auto_activate = true\n", encoding="utf-8")
        assert main(["init"]) == 0
        assert cfg_path.read_text(encoding="utf-8") == "auto_activate = true\n"

    def test_init_tightens_existing_empty_owner_owned_store_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        store_dir = _set_cli_env(monkeypatch, tmp_path)
        store_dir.mkdir()
        os.chmod(store_dir, 0o755)

        assert main(["init"]) == 0
        assert stat.S_IMODE(os.lstat(store_dir).st_mode) == 0o700

    def test_init_refuses_non_empty_wide_store_dir_with_exit_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        store_dir = _set_cli_env(monkeypatch, tmp_path)
        store_dir.mkdir()
        (store_dir / "preexisting.txt").write_text("keep", encoding="utf-8")
        os.chmod(store_dir, 0o755)

        assert main(["init"]) == 2
        err = capsys.readouterr().err
        assert "non-empty" in err
        assert stat.S_IMODE(os.lstat(store_dir).st_mode) == 0o755

    def test_store_insecure_on_scratch_returns_exit_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        store_dir = _set_cli_env(monkeypatch, tmp_path)
        real_dir = tmp_path / "real-store"
        real_dir.mkdir(mode=0o700)
        os.chmod(real_dir, 0o700)
        os.symlink(real_dir, store_dir)

        assert main(["scratch"]) == 2
        assert "symlink" in capsys.readouterr().err

    def test_init_prechecks_config_target_before_creating_store_or_db(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        store_dir = _set_cli_env(monkeypatch, tmp_path)
        missing_parent_cfg = tmp_path / "missing-parent" / "config.toml"
        monkeypatch.setenv("REVIEW_RULES_CONFIG", str(missing_parent_cfg))

        assert main(["init"]) == 2
        assert "missing" in capsys.readouterr().err
        assert not store_dir.exists()
        assert not (store_dir / "review.db").exists()

        # Pre-existing 0644 config.toml in custom 0700 dir also fails before store creation.
        cfg_dir = tmp_path / "custom-cfg-dir"
        cfg_dir.mkdir(mode=0o700)
        os.chmod(cfg_dir, 0o700)
        bad_cfg = cfg_dir / "config.toml"
        bad_cfg.write_text("auto_activate = true\n", encoding="utf-8")
        os.chmod(bad_cfg, 0o644)
        monkeypatch.setenv("REVIEW_RULES_CONFIG", str(bad_cfg))

        assert main(["init"]) == 2
        assert "Insecure permissions" in capsys.readouterr().err
        assert not store_dir.exists()
        assert not (store_dir / "review.db").exists()


class TestCliDoctor:
    def test_doctor_warns_and_exits_zero_when_repo_key_and_gh_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _set_cli_env(monkeypatch, tmp_path)
        _install_gh_stub(monkeypatch, tmp_path / "bin", exit_code=1)

        assert main(["init"]) == 0
        capsys.readouterr()

        assert main(["doctor"]) == 0
        out = capsys.readouterr().out
        assert "[warn] rules_repo:" in out
        assert "[warn] anthropic_key:" in out
        assert "[warn] gh_auth:" in out

    def test_doctor_reports_ok_with_clean_repo_key_and_gh(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        repo = tmp_path / "rules-repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Test User"],
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# rules\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True
        )

        key_path = tmp_path / "anthropic.key"
        key_path.write_text("secret-value", encoding="utf-8")
        os.chmod(key_path, 0o600)

        _set_cli_env(monkeypatch, tmp_path, key_path=key_path, repo_path=repo)
        _install_gh_stub(monkeypatch, tmp_path / "bin", exit_code=0)

        assert main(["init"]) == 0
        capsys.readouterr()

        assert main(["doctor"]) == 0
        out = capsys.readouterr().out
        assert "[ok]   rules_repo: clean main checkout" in out
        assert "[ok]   anthropic_key: present, mode 0600" in out
        assert "[ok]   gh_auth: authenticated" in out

    def test_doctor_prunes_old_scratch_without_following_symlinks(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        store_dir = _set_cli_env(monkeypatch, tmp_path)
        _install_gh_stub(monkeypatch, tmp_path / "bin", exit_code=1)
        assert main(["init"]) == 0
        capsys.readouterr()

        scratch_root = store_dir / "scratch"
        old_dir = scratch_root / "old-run"
        old_dir.mkdir(mode=0o700)
        (old_dir / "artifact.txt").write_text("old", encoding="utf-8")
        old_mtime = time.time() - 2 * 86400
        os.utime(old_dir, (old_mtime, old_mtime))

        fresh_dir = scratch_root / "fresh-run"
        fresh_dir.mkdir(mode=0o700)

        external_dir = tmp_path / "external-do-not-delete"
        external_dir.mkdir(mode=0o700)
        sentinel = external_dir / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        link_dir = scratch_root / "symlink-run"
        os.symlink(external_dir, link_dir)

        assert main(["doctor"]) == 0
        assert not old_dir.exists()
        assert fresh_dir.exists()
        assert link_dir.is_symlink()
        assert sentinel.exists()

    def test_doctor_fails_on_insecure_db_or_sidecar_without_opening_db(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        store_dir = _set_cli_env(monkeypatch, tmp_path)
        _install_gh_stub(monkeypatch, tmp_path / "bin", exit_code=1)
        assert main(["init"]) == 0
        capsys.readouterr()

        db_path = store_dir / "review.db"
        os.chmod(db_path, 0o644)

        def _forbid_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
            raise AssertionError("sqlite3.connect must not be called when lstat checks fail")

        monkeypatch.setattr(sqlite3, "connect", _forbid_connect)
        assert main(["doctor"]) == 1
        out = capsys.readouterr().out
        assert "[fail] review.db:" in out
        assert "[fail] db_open: skipped" in out

        # Restore 0600 on review.db and make -wal insecure (0644).
        os.chmod(db_path, 0o600)
        wal_path = store_dir / "review.db-wal"
        wal_path.write_bytes(b"")
        os.chmod(wal_path, 0o644)

        assert main(["doctor"]) == 1
        out_wal = capsys.readouterr().out
        assert "[fail] review.db-wal:" in out_wal
        assert "[fail] db_open: skipped" in out_wal

    def test_doctor_fails_on_insecure_scratch_and_warns_on_nested_or_timed_out_repo(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        store_dir = _set_cli_env(monkeypatch, tmp_path)
        _install_gh_stub(monkeypatch, tmp_path / "bin", exit_code=1)
        assert main(["init"]) == 0
        capsys.readouterr()

        # 0755 scratch/ directory causes doctor to fail.
        os.chmod(store_dir / "scratch", 0o755)
        assert main(["doctor"]) == 1
        assert "[fail] scratch:" in capsys.readouterr().out
        os.chmod(store_dir / "scratch", 0o700)

        # Non-git subdir nested inside a parent git checkout is NOT treated as valid repo.
        parent_repo = tmp_path / "parent-git"
        parent_repo.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main", str(parent_repo)], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(parent_repo), "config", "user.email", "t@example.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(parent_repo), "config", "user.name", "Test"],
            check=True,
            capture_output=True,
        )
        (parent_repo / "README.md").write_text("hi\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(parent_repo), "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(parent_repo), "commit", "-m", "init"],
            check=True,
            capture_output=True,
        )
        nested_dir = parent_repo / "not-a-repo"
        nested_dir.mkdir()
        monkeypatch.setenv("REVIEW_RULES_REPO", str(nested_dir))
        assert main(["doctor"]) == 0
        assert "[warn] rules_repo: not a valid git checkout" in capsys.readouterr().out

        # TimeoutExpired in run_git degrades cleanly to [warn].
        def _timeout_git(*args: object, **kwargs: object) -> object:
            raise subprocess.TimeoutExpired(cmd=["git"], timeout=10.0)

        monkeypatch.setattr(cli_mod, "run_git", _timeout_git)
        assert main(["doctor"]) == 0
        assert "[warn] rules_repo:" in capsys.readouterr().out

    def test_doctor_prune_scratch_ignores_concurrent_rmtree_oserror(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        store_dir = _set_cli_env(monkeypatch, tmp_path)
        _install_gh_stub(monkeypatch, tmp_path / "bin", exit_code=1)
        assert main(["init"]) == 0
        capsys.readouterr()

        old_dir = store_dir / "scratch" / "vanished-run"
        old_dir.mkdir(mode=0o700)
        old_mtime = time.time() - 2 * 86400
        os.utime(old_dir, (old_mtime, old_mtime))

        def _vanished_rmtree(path: object, *args: object, **kwargs: object) -> None:
            raise FileNotFoundError(path)

        monkeypatch.setattr(shutil, "rmtree", _vanished_rmtree)
        assert main(["doctor"]) == 0
        capsys.readouterr()

    def test_doctor_sets_busy_timeout_on_readonly_connection(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _set_cli_env(monkeypatch, tmp_path)
        _install_gh_stub(monkeypatch, tmp_path / "bin", exit_code=1)
        assert main(["init"]) == 0
        capsys.readouterr()

        executed_sql: list[str] = []
        real_connect = sqlite3.connect

        class _RecordingConnection(sqlite3.Connection):
            def execute(self, sql: str, parameters: object = (), /) -> sqlite3.Cursor:
                executed_sql.append(sql)
                return super().execute(sql, parameters)  # type: ignore[arg-type]

        def _recording_connect(
            database: object, *args: object, **kwargs: object
        ) -> sqlite3.Connection:
            kwargs.setdefault("factory", _RecordingConnection)
            return real_connect(database, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(sqlite3, "connect", _recording_connect)
        assert main(["doctor"]) == 0
        capsys.readouterr()

        assert "PRAGMA busy_timeout = 2000" in executed_sql
        assert executed_sql.index("PRAGMA busy_timeout = 2000") < next(
            i for i, sql in enumerate(executed_sql) if "schema_version" in sql
        )
