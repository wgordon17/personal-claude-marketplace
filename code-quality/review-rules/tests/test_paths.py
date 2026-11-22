"""Unit tests for review_rules.paths."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from review_rules.paths import (
    CONFIG_DEFAULTS,
    StoreInsecure,
    ensure_store_dir,
    get_anthropic_key_path,
    get_config_path,
    get_db_path,
    get_repo_path,
    get_scratch_root,
    get_spool_path,
    get_store_dir,
    get_venv_path,
    load_config,
    read_regular_nofollow,
    verify_private_path,
)

_ENV_VARS = (
    "REVIEW_RULES_DB",
    "REVIEW_RULES_SPOOL",
    "REVIEW_RULES_REPO",
    "REVIEW_RULES_VENV",
    "REVIEW_RULES_CONFIG",
    "REVIEW_RULES_ANTHROPIC_KEY_FILE",
)


def _clear_review_rules_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


class TestPathResolution:
    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_review_rules_env(monkeypatch)
        home = Path.home()
        assert get_db_path() == home / ".local" / "share" / "review-rules" / "review.db"
        assert get_store_dir() == home / ".local" / "share" / "review-rules"
        assert get_scratch_root() == home / ".local" / "share" / "review-rules" / "scratch"
        assert get_spool_path() == home / ".local" / "share" / "review-rules" / "spool.jsonl"
        assert (
            get_repo_path()
            == home / "SynologyDrive" / "ActiveProjects" / "personal" / "review-rules"
        )
        assert get_venv_path() == home / ".cache" / "review-rules" / "venv"
        assert get_config_path() == home / ".local" / "share" / "review-rules" / "config.toml"
        assert (
            get_anthropic_key_path() == home / ".local" / "share" / "review-rules" / "anthropic.key"
        )

    def test_relative_overrides_resolve_to_abspath(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _clear_review_rules_env(monkeypatch)
        monkeypatch.chdir(tmp_path)

        monkeypatch.setenv("REVIEW_RULES_DB", "rel-store/review.db")
        expected_store = (tmp_path / "rel-store").resolve()
        assert get_db_path() == expected_store / "review.db"
        assert get_db_path().is_absolute()
        assert get_store_dir() == expected_store
        assert get_scratch_root() == expected_store / "scratch"
        assert get_spool_path() == expected_store / "spool.jsonl"
        assert get_config_path() == expected_store / "config.toml"
        assert get_anthropic_key_path() == expected_store / "anthropic.key"

        monkeypatch.setenv("REVIEW_RULES_SPOOL", "custom/spool.jsonl")
        monkeypatch.setenv("REVIEW_RULES_REPO", "custom/repo")
        monkeypatch.setenv("REVIEW_RULES_VENV", "custom/venv")
        monkeypatch.setenv("REVIEW_RULES_CONFIG", "custom/config.toml")
        monkeypatch.setenv("REVIEW_RULES_ANTHROPIC_KEY_FILE", "custom/anthropic.key")

        assert get_spool_path() == (tmp_path / "custom" / "spool.jsonl").resolve()
        assert get_repo_path() == (tmp_path / "custom" / "repo").resolve()
        assert get_venv_path() == (tmp_path / "custom" / "venv").resolve()
        assert get_config_path() == (tmp_path / "custom" / "config.toml").resolve()
        assert get_anthropic_key_path() == (tmp_path / "custom" / "anthropic.key").resolve()

    def test_tilde_expansion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_review_rules_env(monkeypatch)
        monkeypatch.setenv("REVIEW_RULES_DB", "~/custom-rr/review.db")
        assert get_db_path() == Path.home() / "custom-rr" / "review.db"


class TestLoadConfig:
    def test_missing_file_returns_defaults_and_copies_lists(self, tmp_path: Path) -> None:
        cfg = load_config(tmp_path / "missing.toml")
        assert cfg == CONFIG_DEFAULTS
        cfg["github_orgs"].append("mutated-org")
        cfg["archive_roots"].append("/tmp/mutated")
        assert CONFIG_DEFAULTS["github_orgs"] == ["osac-project"]
        assert CONFIG_DEFAULTS["archive_roots"] == []
        assert load_config(tmp_path / "missing.toml")["github_orgs"] == ["osac-project"]

    def test_malformed_toml_falls_back_to_defaults(self, tmp_path: Path) -> None:
        bad = tmp_path / "config.toml"
        bad.write_text("auto_activate = [unclosed\n", encoding="utf-8")
        os.chmod(bad, 0o600)
        assert load_config(bad) == CONFIG_DEFAULTS

    def test_valid_and_invalid_fields(self, tmp_path: Path) -> None:
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text(
            """
auto_activate = true
max_rules_per_reviewer = 25
suppression_days = true
github_orgs = ["org-a", "org-b"]
archive_roots = [123]
price_in_per_mtok = 1
price_out_per_mtok = 5.5
max_cost_usd = "not-a-number"
unknown_extra = "ignored"
""",
            encoding="utf-8",
        )
        os.chmod(cfg_file, 0o600)
        cfg = load_config(cfg_file)
        assert cfg["auto_activate"] is True
        assert cfg["max_rules_per_reviewer"] == 25
        # bool must not be accepted as int
        assert cfg["suppression_days"] == CONFIG_DEFAULTS["suppression_days"]
        assert cfg["github_orgs"] == ["org-a", "org-b"]
        # non-str list rejected
        assert cfg["archive_roots"] == []
        assert cfg["price_in_per_mtok"] == 1.0
        assert cfg["price_out_per_mtok"] == 5.5
        assert cfg["max_cost_usd"] is None
        assert "unknown_extra" not in cfg

    def test_insecure_or_symlink_config_raises_store_insecure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        real_cfg = tmp_path / "real.toml"
        real_cfg.write_text("auto_activate = true\n", encoding="utf-8")
        os.chmod(real_cfg, 0o600)
        link_cfg = tmp_path / "link.toml"
        os.symlink(real_cfg, link_cfg)
        with pytest.raises(StoreInsecure):
            load_config(link_cfg)

        wide_cfg = tmp_path / "wide.toml"
        wide_cfg.write_text("auto_activate = true\n", encoding="utf-8")
        os.chmod(wide_cfg, 0o644)
        with pytest.raises(StoreInsecure, match="Insecure permissions"):
            load_config(wide_cfg)

        owner_uid = os.lstat(real_cfg).st_uid
        monkeypatch.setattr(os, "getuid", lambda: owner_uid + 1)
        with pytest.raises(StoreInsecure, match="Untrusted owner"):
            load_config(real_cfg)


class TestStoreSecurity:
    def test_ensure_store_dir_creates_0700_dirs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "store" / "review.db"
        monkeypatch.setenv("REVIEW_RULES_DB", str(db_path))

        store = ensure_store_dir()
        assert store == tmp_path / "store"
        scratch = store / "scratch"
        assert stat.S_IMODE(os.lstat(store).st_mode) == 0o700
        assert stat.S_IMODE(os.lstat(scratch).st_mode) == 0o700

    def test_verify_private_path_accepts_0600_and_0700(self, tmp_path: Path) -> None:
        d = tmp_path / "privdir"
        d.mkdir(mode=0o700)
        os.chmod(d, 0o700)
        f = d / "secret.key"
        f.write_text("x", encoding="utf-8")
        os.chmod(f, 0o600)

        verify_private_path(d, is_dir=True)
        verify_private_path(f, is_dir=False)

    def test_symlink_refusal_via_lstat(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        real_dir = tmp_path / "real"
        real_dir.mkdir(mode=0o700)
        os.chmod(real_dir, 0o700)
        (real_dir / "scratch").mkdir(mode=0o700)
        os.chmod(real_dir / "scratch", 0o700)

        link_dir = tmp_path / "link-store"
        os.symlink(real_dir, link_dir)
        monkeypatch.setenv("REVIEW_RULES_DB", str(link_dir / "review.db"))
        with pytest.raises(StoreInsecure, match="symlink"):
            ensure_store_dir()

        real_file = real_dir / "target.key"
        real_file.write_text("abc", encoding="utf-8")
        os.chmod(real_file, 0o600)
        link_file = real_dir / "link.key"
        os.symlink(real_file, link_file)
        with pytest.raises(StoreInsecure, match="symlink"):
            verify_private_path(link_file, is_dir=False)

    def test_symlink_scratch_refused(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        store = tmp_path / "store"
        store.mkdir(mode=0o700)
        os.chmod(store, 0o700)
        real_scratch = tmp_path / "other"
        real_scratch.mkdir(mode=0o700)
        os.chmod(real_scratch, 0o700)
        os.symlink(real_scratch, store / "scratch")

        monkeypatch.setenv("REVIEW_RULES_DB", str(store / "review.db"))
        with pytest.raises(StoreInsecure, match="symlink"):
            ensure_store_dir()

    def test_wrong_owner_refusal(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        store = tmp_path / "store"
        store.mkdir(mode=0o700)
        os.chmod(store, 0o700)
        owner_uid = os.lstat(store).st_uid
        monkeypatch.setattr(os, "getuid", lambda: owner_uid + 1)

        with pytest.raises(StoreInsecure, match="Untrusted owner"):
            verify_private_path(store, is_dir=True)

    @pytest.mark.parametrize("bad_mode", [0o755, 0o750, 0o705, 0o777])
    def test_wide_dir_mode_refusal(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bad_mode: int
    ) -> None:
        store = tmp_path / "store"
        store.mkdir()
        os.chmod(store, bad_mode)
        monkeypatch.setenv("REVIEW_RULES_DB", str(store / "review.db"))

        with pytest.raises(StoreInsecure, match="Insecure permissions"):
            ensure_store_dir()

    @pytest.mark.parametrize("bad_mode", [0o644, 0o640, 0o604, 0o666])
    def test_wide_file_mode_refusal(self, tmp_path: Path, bad_mode: int) -> None:
        f = tmp_path / "review.db"
        f.write_text("", encoding="utf-8")
        os.chmod(f, bad_mode)

        with pytest.raises(StoreInsecure, match="Insecure permissions"):
            verify_private_path(f, is_dir=False)

    def test_read_regular_nofollow_enforces_privacy_and_size(self, tmp_path: Path) -> None:
        f = tmp_path / "data.txt"
        f.write_bytes(b"hello world")
        os.chmod(f, 0o644)

        with pytest.raises(StoreInsecure, match="Insecure permissions"):
            read_regular_nofollow(f, require_private=True)

        os.chmod(f, 0o600)
        assert read_regular_nofollow(f, require_private=True) == b"hello world"
        with pytest.raises(ValueError, match="exceeds max size"):
            read_regular_nofollow(f, max_bytes=5, require_private=True)

    def test_read_regular_nofollow_refuses_symlink_and_fifo_without_blocking(
        self, tmp_path: Path
    ) -> None:
        real_file = tmp_path / "real.txt"
        real_file.write_bytes(b"ok")
        os.chmod(real_file, 0o600)
        link_file = tmp_path / "link.txt"
        os.symlink(real_file, link_file)
        with pytest.raises(StoreInsecure, match="symlink"):
            read_regular_nofollow(link_file)

        fifo_path = tmp_path / "pipe.fifo"
        os.mkfifo(fifo_path, 0o600)
        with pytest.raises(StoreInsecure, match="non-regular"):
            read_regular_nofollow(fifo_path)

    def test_ensure_store_dir_explicit_dir_and_restores_umask(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("REVIEW_RULES_DB", raising=False)
        explicit = tmp_path / "explicit-store"
        before = os.umask(0o022)
        os.umask(before)
        res = ensure_store_dir(explicit)
        after = os.umask(before)
        os.umask(after)
        assert res == explicit
        assert after == before
        assert stat.S_IMODE(os.lstat(explicit).st_mode) == 0o700
        assert stat.S_IMODE(os.lstat(explicit / "scratch").st_mode) == 0o700
