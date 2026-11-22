"""Path resolution, config loading, and 0700/0600 store security checks."""

from __future__ import annotations

import contextlib
import copy
import errno
import os
import stat
import tomllib
from pathlib import Path
from typing import Any

CONFIG_DEFAULTS: dict[str, Any] = {
    "auto_activate": False,
    "max_rules_per_reviewer": 15,
    "suppression_days": 90,
    "github_orgs": ["osac-project"],
    "archive_roots": [],
    "max_candidates_per_run": 50,
    "price_in_per_mtok": None,
    "price_out_per_mtok": None,
    "max_cost_usd": None,
}


class StoreInsecure(Exception):
    """Raised when store directory or file ownership/permissions/symlink checks fail."""


def get_db_path() -> Path:
    raw = os.environ.get("REVIEW_RULES_DB")
    if raw:
        return Path(os.path.abspath(os.path.expanduser(raw)))
    return Path.home() / ".local" / "share" / "review-rules" / "review.db"


def get_store_dir() -> Path:
    return get_db_path().parent


def get_scratch_root() -> Path:
    return get_store_dir() / "scratch"


def get_spool_path() -> Path:
    raw = os.environ.get("REVIEW_RULES_SPOOL")
    if raw:
        return Path(os.path.abspath(os.path.expanduser(raw)))
    return get_store_dir() / "spool.jsonl"


def get_repo_path() -> Path:
    raw = os.environ.get("REVIEW_RULES_REPO")
    if raw:
        return Path(os.path.abspath(os.path.expanduser(raw)))
    return Path.home() / "SynologyDrive" / "ActiveProjects" / "personal" / "review-rules"


def get_venv_path() -> Path:
    raw = os.environ.get("REVIEW_RULES_VENV")
    if raw:
        return Path(os.path.abspath(os.path.expanduser(raw)))
    return Path.home() / ".cache" / "review-rules" / "venv"


def get_config_path() -> Path:
    raw = os.environ.get("REVIEW_RULES_CONFIG")
    if raw:
        return Path(os.path.abspath(os.path.expanduser(raw)))
    return get_store_dir() / "config.toml"


def get_anthropic_key_path() -> Path:
    raw = os.environ.get("REVIEW_RULES_ANTHROPIC_KEY_FILE")
    if raw:
        return Path(os.path.abspath(os.path.expanduser(raw)))
    return get_store_dir() / "anthropic.key"


def verify_private_path(path: Path, *, is_dir: bool = False) -> os.stat_result:
    """Verify path via os.lstat: expected type, non-symlink, owner uid, and 0700/0600 mode."""
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        raise StoreInsecure(f"Refusing symlink path: {path}")
    if is_dir:
        if not stat.S_ISDIR(st.st_mode):
            raise StoreInsecure(f"Expected directory, got non-directory: {path}")
    elif not stat.S_ISREG(st.st_mode):
        raise StoreInsecure(f"Expected regular file, got non-regular file: {path}")
    uid = os.getuid()
    if st.st_uid != uid:
        raise StoreInsecure(f"Untrusted owner uid {st.st_uid} on {path} (expected {uid})")
    mode = stat.S_IMODE(st.st_mode)
    if mode & 0o077:
        expected = "0700" if is_dir else "0600"
        raise StoreInsecure(
            f"Insecure permissions {oct(mode)} on {path}; expected {expected} (no group/other bits)"
        )
    return st


def read_regular_nofollow(
    path: Path, *, max_bytes: int | None = None, require_private: bool = False
) -> bytes:
    """Read a regular file without following symlinks, enforcing size and optional privacy."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR, getattr(errno, "EMLINK", -1)}:
            raise StoreInsecure(f"Refusing symlink or non-file path: {path}") from exc
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise StoreInsecure(f"Expected regular file, got non-regular file: {path}")
        if require_private:
            uid = os.getuid()
            if st.st_uid != uid:
                raise StoreInsecure(f"Untrusted owner uid {st.st_uid} on {path} (expected {uid})")
            mode = stat.S_IMODE(st.st_mode)
            if mode & 0o077:
                raise StoreInsecure(f"Insecure permissions {oct(mode)} on {path}; expected 0600")
        if max_bytes is not None and st.st_size > max_bytes:
            raise ValueError(f"{path} exceeds max size {max_bytes} bytes")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise ValueError(f"{path} exceeds max size {max_bytes} bytes")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load TOML config with defaults fallback on missing/malformed; reject insecure files."""
    cfg = copy.deepcopy(CONFIG_DEFAULTS)
    target = config_path if config_path is not None else get_config_path()
    try:
        raw = read_regular_nofollow(target, max_bytes=1_048_576, require_private=True)
    except (OSError, ValueError):
        return cfg
    try:
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return cfg
    if not isinstance(parsed, dict):
        return cfg
    for key, default_val in CONFIG_DEFAULTS.items():
        if key not in parsed:
            continue
        val = parsed[key]
        if isinstance(default_val, bool):
            if isinstance(val, bool):
                cfg[key] = val
        elif isinstance(default_val, int):
            if isinstance(val, int) and not isinstance(val, bool):
                cfg[key] = val
        elif isinstance(default_val, list):
            if isinstance(val, list) and all(isinstance(x, str) for x in val):
                cfg[key] = list(val)
        elif default_val is None and isinstance(val, int | float) and not isinstance(val, bool):
            cfg[key] = float(val)
    return cfg


def ensure_store_dir(store_dir: Path | None = None) -> Path:
    """Create and verify 0700 store_dir and scratch/ using os.lstat."""
    old_umask = os.umask(0o077)
    try:
        target = (
            Path(os.path.abspath(os.path.expanduser(str(store_dir))))
            if store_dir is not None
            else get_store_dir()
        )
        _ensure_private_dir(target)
        _ensure_private_dir(target / "scratch")
        return target
    finally:
        os.umask(old_umask)


def _ensure_private_dir(directory: Path) -> None:
    try:
        os.lstat(directory)
    except FileNotFoundError:
        directory.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileExistsError):
            os.mkdir(directory, 0o700)
    verify_private_path(directory, is_dir=True)
