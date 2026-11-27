"""CLI entry point for review-rules (init, doctor, scratch, lint)."""

from __future__ import annotations

import argparse
import errno
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

from review_rules.db import SCHEMA_VERSION, connect
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
    verify_private_path,
)
from review_rules.rules import (
    RulesUnavailable,
    lint_directory,
    load_rules,
    run_git,
    verify_worktree_schema,
)

_SCRATCH_MAX_AGE_SECONDS = 86400

_DEFAULT_CONFIG_BODY = f"""# review-rules configuration
auto_activate = {"true" if CONFIG_DEFAULTS["auto_activate"] else "false"}
max_rules_per_reviewer = {CONFIG_DEFAULTS["max_rules_per_reviewer"]}
suppression_days = {CONFIG_DEFAULTS["suppression_days"]}
github_orgs = ["osac-project"]
archive_roots = []
max_candidates_per_run = {CONFIG_DEFAULTS["max_candidates_per_run"]}

# Set pricing & budget before running batch classification (required by classify)
# price_in_per_mtok = 1.0
# price_out_per_mtok = 5.0
# max_cost_usd = 5.0
"""


def _tighten_existing_empty_store_dir(store_dir: Path) -> None:
    """Safely tighten an existing empty owner-owned store directory to 0o700."""
    try:
        os.lstat(store_dir)
    except FileNotFoundError:
        return
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(store_dir, flags)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR, getattr(errno, "EMLINK", -1)}:
            raise StoreInsecure(f"Refusing symlink store directory: {store_dir}") from exc
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode):
            raise StoreInsecure(f"Expected directory, got non-directory: {store_dir}")
        uid = os.getuid()
        if st.st_uid != uid:
            raise StoreInsecure(f"Untrusted owner uid {st.st_uid} on {store_dir} (expected {uid})")
        if stat.S_IMODE(st.st_mode) & 0o077:
            dup_fd = os.dup(fd)
            try:
                if os.listdir(dup_fd):
                    raise StoreInsecure(
                        f"Refusing to chmod non-empty store directory {store_dir} "
                        f"with mode {oct(stat.S_IMODE(st.st_mode))}"
                    )
            finally:
                os.close(dup_fd)
            os.fchmod(fd, 0o700)
    finally:
        os.close(fd)


def _precheck_config_target(store_dir: Path, config_path: Path) -> None:
    """Validate config_path and custom parent directory before mutating store_dir/review.db."""
    if config_path.parent != store_dir:
        try:
            verify_private_path(config_path.parent, is_dir=True)
        except FileNotFoundError as exc:
            raise StoreInsecure(f"Config parent directory missing: {config_path.parent}") from exc
    try:
        os.lstat(config_path)
    except FileNotFoundError:
        return
    verify_private_path(config_path, is_dir=False)


def _write_default_config(config_path: Path) -> bool:
    """Create default config.toml atomically at 0600 if absent. Returns True if created."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(config_path, flags, 0o600)
    except FileExistsError:
        verify_private_path(config_path, is_dir=False)
        return False
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR, getattr(errno, "EMLINK", -1)}:
            raise StoreInsecure(f"Refusing symlink config path: {config_path}") from exc
        raise
    try:
        data = _DEFAULT_CONFIG_BODY.encode("utf-8")
        view = memoryview(data)
        written = 0
        while written < len(data):
            written += os.write(fd, view[written:])
    finally:
        os.close(fd)
    return True


def _cmd_init() -> int:
    old_umask = os.umask(0o077)
    try:
        store_dir = get_store_dir()
        config_path = get_config_path()
        _precheck_config_target(store_dir, config_path)
        _tighten_existing_empty_store_dir(store_dir)
        ensure_store_dir()
        conn = connect()
        conn.close()
        created_cfg = _write_default_config(config_path)
    finally:
        os.umask(old_umask)

    print(f"Store initialized: {store_dir}")
    print(f"Database ready:    {get_db_path()}")
    cfg_state = "created" if created_cfg else "existing"
    print(f"Config ({cfg_state}): {config_path}")
    print()
    print("Optional: add this allow rule to ~/.claude/settings.json permissions.allow")
    print("to avoid per-run prompts when review skills write scratch files:")
    print("  Edit(~/.local/share/review-rules/scratch/**)")
    print("Warning: do NOT use Write(~/.local/share/review-rules/scratch/**) —")
    print("Write(path) matches no tool calls and triggers a startup warning in Claude Code;")
    print("Edit(~/...) governs Edit, Write, NotebookEdit, and MultiEdit.")
    print("If permissions.blockWritesOutsideWorkingDirectories is true, also add")
    print("~/.local/share/review-rules/scratch to permissions.additionalDirectories.")
    return 0


def _cmd_scratch() -> int:
    ensure_store_dir()
    scratch_root = get_scratch_root()
    target = scratch_root / uuid.uuid4().hex
    os.mkdir(target, 0o700)
    verify_private_path(target, is_dir=True)
    print(str(target))
    return 0


def _prune_old_scratch(scratch_root: Path) -> int:
    """Remove scratch subdirectories older than 1 day without following symlinks."""
    try:
        st_root = os.lstat(scratch_root)
    except FileNotFoundError:
        return 0
    if stat.S_ISLNK(st_root.st_mode) or not stat.S_ISDIR(st_root.st_mode):
        return 0
    if st_root.st_uid != os.getuid() or (stat.S_IMODE(st_root.st_mode) & 0o077) != 0:
        return 0
    cutoff = time.time() - _SCRATCH_MAX_AGE_SECONDS
    pruned = 0
    with os.scandir(scratch_root) as entries:
        for entry in entries:
            if entry.is_symlink():
                continue
            if not entry.is_dir(follow_symlinks=False):
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if st.st_mtime < cutoff:
                try:
                    shutil.rmtree(entry.path)
                except (FileNotFoundError, OSError):
                    continue
                pruned += 1
    return pruned


def _check_required_path(label: str, path: Path, *, is_dir: bool) -> tuple[bool, str]:
    try:
        verify_private_path(path, is_dir=is_dir)
    except FileNotFoundError:
        return False, f"[fail] {label}: missing ({path})"
    except StoreInsecure as exc:
        return False, f"[fail] {label}: {exc}"
    mode = "0700" if is_dir else "0600"
    return True, f"[ok]   {label}: {path} (mode {mode})"


def _check_optional_path(label: str, path: Path, *, is_dir: bool) -> tuple[bool, str]:
    try:
        verify_private_path(path, is_dir=is_dir)
    except FileNotFoundError:
        return True, f"[ok]   {label}: absent ({path})"
    except StoreInsecure as exc:
        return False, f"[fail] {label}: {exc}"
    mode = "0700" if is_dir else "0600"
    return True, f"[ok]   {label}: {path} (mode {mode})"


def _cmd_doctor() -> int:
    store_dir = get_store_dir()
    scratch_root = get_scratch_root()
    db_path = get_db_path()
    wal_path = Path(f"{db_path}-wal")
    shm_path = Path(f"{db_path}-shm")
    spool_path = get_spool_path()
    config_path = get_config_path()

    _prune_old_scratch(scratch_root)

    checks: list[tuple[bool, str]] = []
    store_ok, store_msg = _check_required_path("store_dir", store_dir, is_dir=True)
    checks.append((store_ok, store_msg))
    checks.append(_check_optional_path("scratch", scratch_root, is_dir=True))
    db_ok, db_msg = _check_required_path("review.db", db_path, is_dir=False)
    checks.append((db_ok, db_msg))
    wal_ok, wal_msg = _check_optional_path("review.db-wal", wal_path, is_dir=False)
    checks.append((wal_ok, wal_msg))
    shm_ok, shm_msg = _check_optional_path("review.db-shm", shm_path, is_dir=False)
    checks.append((shm_ok, shm_msg))
    checks.append(_check_optional_path("spool.jsonl", spool_path, is_dir=False))
    checks.append(_check_optional_path("config.toml", config_path, is_dir=False))

    # Open DB only after os.lstat checks pass
    if store_ok and db_ok and wal_ok and shm_ok:
        try:
            conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
            try:
                conn.execute("PRAGMA busy_timeout = 2000")
                row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
                if row is None:
                    checks.append((False, "[fail] schema_version: missing in meta table"))
                else:
                    ver = int(row[0])
                    if ver != SCHEMA_VERSION:
                        checks.append(
                            (
                                False,
                                f"[fail] schema_version: {ver} (expected {SCHEMA_VERSION})",
                            )
                        )
                    else:
                        checks.append((True, f"[ok]   schema_version: {ver}"))
            finally:
                conn.close()
        except (sqlite3.Error, ValueError) as exc:
            checks.append((False, f"[fail] db_open: {exc}"))
    else:
        checks.append((False, "[fail] db_open: skipped due to store/DB check failure"))

    # Warnings (never fatal)
    warnings: list[str] = []
    repo_path = get_repo_path()
    if not repo_path.is_dir():
        warnings.append(f"[warn] rules_repo: missing ({repo_path})")
    else:
        try:
            top_proc = run_git(repo_path, ["rev-parse", "--show-toplevel"])
            branch_proc = run_git(repo_path, ["branch", "--show-current"])
            status_proc = run_git(repo_path, ["status", "--porcelain"])
        except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as exc:
            warnings.append(f"[warn] rules_repo: {exc}")
        else:
            top_ok = (
                top_proc.returncode == 0
                and bool(top_proc.stdout.strip())
                and Path(top_proc.stdout.strip()).resolve() == repo_path.resolve()
            )
            if not top_ok or branch_proc.returncode != 0:
                warnings.append(f"[warn] rules_repo: not a valid git checkout ({repo_path})")
            else:
                branch = branch_proc.stdout.strip()
                dirty = status_proc.stdout.strip()
                if branch != "main" or dirty:
                    warnings.append(
                        f"[warn] rules_repo: branch={branch or 'detached'}, "
                        f"clean={not bool(dirty)} ({repo_path})"
                    )
                else:
                    warnings.append(f"[ok]   rules_repo: clean main checkout ({repo_path})")

    key_path = get_anthropic_key_path()
    try:
        verify_private_path(key_path, is_dir=False)
    except FileNotFoundError:
        warnings.append(f"[warn] anthropic_key: missing ({key_path})")
    except StoreInsecure as exc:
        warnings.append(f"[warn] anthropic_key: {exc}")
    else:
        warnings.append(f"[ok]   anthropic_key: present, mode 0600 ({key_path})")

    try:
        gh_proc = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        warnings.append(f"[warn] gh_auth: {exc}")
    else:
        if gh_proc.returncode == 0:
            warnings.append("[ok]   gh_auth: authenticated")
        else:
            warnings.append("[warn] gh_auth: not authenticated")

    try:
        spool_st = os.lstat(spool_path)
    except FileNotFoundError:
        warnings.append("[ok]   spool_size: 0 bytes (absent)")
    except OSError as exc:
        warnings.append(f"[warn] spool_size: {exc}")
    else:
        warnings.append(f"[ok]   spool_size: {spool_st.st_size} bytes")

    for _ok, msg in checks:
        print(msg)
    for msg in warnings:
        print(msg)

    return 0 if all(ok for ok, _msg in checks) else 1


def _cmd_lint(*, worktree: bool, dir_path: Path | None, no_fetch: bool) -> int:
    try:
        if dir_path is not None:
            result = lint_directory(dir_path)
            print(f"dir:       {dir_path}")
        elif worktree:
            repo_path = get_repo_path()
            verify_worktree_schema(repo_path)
            target_dir = repo_path / "rules"
            result = lint_directory(target_dir)
            print(f"dir:       {target_dir}")
        else:
            repo_path = get_repo_path()
            result = load_rules(repo_path, fetch=not no_fetch)
            print(f"sha:       {result.sha}")
            print(f"fetched:   {result.fetched}")
            if result.fetch_error:
                print(f"fetch_err: {result.fetch_error}")
            try:
                ahead_proc = run_git(
                    repo_path,
                    ["rev-list", "--count", "refs/remotes/origin/main..refs/heads/main"],
                )
            except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as exc:
                print(f"warning: unable to check local main ahead-of-remote status: {exc}")
            else:
                if ahead_proc.returncode == 0:
                    try:
                        ahead_count = int(ahead_proc.stdout.strip())
                    except ValueError:
                        ahead_count = 0
                    if ahead_count > 0:
                        print(
                            f"warning: local main is {ahead_count} commit(s) ahead of origin/main "
                            "(approved but unpushed)"
                        )
    except RulesUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(
        f"rules:     active={len(result.active)} candidates={len(result.candidates)} "
        f"retired={len(result.retired)} rejected={len(result.rejected)}"
    )
    for rej in result.rejected:
        print(f"REJECTED {rej.path}: {rej.reason}")
    return 1 if result.rejected else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="review-rules",
        description="Manage learned code and plan review rules.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("init", help="Initialize the private store and default config.")
    subparsers.add_parser("doctor", help="Verify store permissions, DB health, and dependencies.")
    subparsers.add_parser("scratch", help="Create and print a 0700 scratch directory.")
    lint_parser = subparsers.add_parser(
        "lint",
        help="Validate learned rules from pinned origin/main, worktree, or directory.",
    )
    lint_mode = lint_parser.add_mutually_exclusive_group()
    lint_mode.add_argument(
        "--worktree",
        action="store_true",
        help="Validate rules/*.yaml in the rules repo working tree.",
    )
    lint_mode.add_argument(
        "--dir",
        type=Path,
        default=None,
        metavar="PATH",
        help="Validate *.yaml files in PATH.",
    )
    lint_parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="Skip git fetch origin main before inspecting refs/remotes/origin/main.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    old_umask = os.umask(0o077)
    try:
        if args.command == "init":
            return _cmd_init()
        if args.command == "scratch":
            return _cmd_scratch()
        if args.command == "doctor":
            return _cmd_doctor()
        if args.command == "lint":
            return _cmd_lint(
                worktree=bool(args.worktree),
                dir_path=args.dir,
                no_fetch=bool(args.no_fetch),
            )
    except StoreInsecure as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        os.umask(old_umask)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
