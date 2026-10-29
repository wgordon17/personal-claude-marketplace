"""Write confinement and cloud-placeholder detection for tech-radar."""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from collections.abc import Callable, Collection
from pathlib import Path

from . import config
from .errors import WriteDenied

SF_DATALESS = 0x40000000


def is_dataless(st: os.stat_result) -> bool:
    """Return True if a stat_result has the macOS SF_DATALESS flag (Linux-safe via getattr)."""
    flags = int(getattr(st, "st_flags", 0))
    return bool(flags & SF_DATALESS)


def _allowed_roots(explicit_root: Path | str | None = None) -> tuple[Path, ...]:
    if explicit_root is not None:
        return (Path(explicit_root).expanduser().resolve(),)
    p = config.paths(ensure_dirs=False)
    return tuple(r.resolve() for r in (p.state, p.run_root, p.sync, p.logs, p.launch_agents))


def _check_no_symlink_in_relpath(base_resolved: Path, target_parent_raw: Path) -> None:
    """Ensure no existing directory component under the matched root is a symlink."""
    cur = target_parent_raw
    visited: list[Path] = []
    while True:
        visited.append(cur)
        if cur.exists() and cur.resolve() == base_resolved:
            break
        if cur.parent == cur:
            break
        cur = cur.parent

    for item in reversed(visited):
        if item.exists() and item.resolve() != base_resolved and item.is_symlink():
            raise WriteDenied(f"symlinked directory component not permitted: {item}")


def safe_write(
    path: Path | str,
    data: bytes | str,
    *,
    mode: int = 0o600,
    root: Path | str | None = None,
) -> Path:
    """Atomically write data to path after verifying confinement inside allowed roots."""
    os.umask(0o077)
    raw_path = Path(path).expanduser()

    # Check lexical traversal before resolution if root is given or path is relative
    if ".." in raw_path.parts:
        # Resolve and check below, but also refuse any path escaping its intended root
        pass

    if raw_path.is_symlink():
        raise WriteDenied(f"refusing to write to symlink target: {path}")

    parent_raw = raw_path.parent
    parent_resolved = parent_raw.resolve()
    target_resolved = parent_resolved / raw_path.name

    if target_resolved.is_symlink():
        raise WriteDenied(f"refusing to write to symlink target: {target_resolved}")

    roots = _allowed_roots(root)
    matched_root: Path | None = None
    for allowed in roots:
        if target_resolved != allowed and target_resolved.is_relative_to(allowed):
            matched_root = allowed
            break

    if matched_root is None:
        raise WriteDenied(f"write target {target_resolved} is outside allowed roots {roots}")

    _check_no_symlink_in_relpath(matched_root, parent_raw)

    parent_resolved.mkdir(parents=True, exist_ok=True, mode=0o700)

    raw_bytes = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{raw_path.name}.",
        suffix=".tmp",
        dir=str(parent_resolved),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw_bytes)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_path, mode)
        if raw_path.is_symlink() or target_resolved.is_symlink():
            raise WriteDenied(f"symlink appeared at target before replace: {path}")
        os.replace(tmp_path, target_resolved)
    except Exception:
        if tmp_path.exists():
            with contextlib.suppress(OSError):
                tmp_path.unlink()
        raise

    return target_resolved


EXCLUDED_APP_NAMES: frozenset[str] = frozenset({"__pycache__", ".DS_Store", ".pytest_cache"})


def is_excluded_app_path(rel_path: Path) -> bool:
    """Return True if `rel_path` matches the Spec 11 §8 source exclusion filter."""
    return any(part in EXCLUDED_APP_NAMES or part.endswith(".pyc") for part in rel_path.parts)


def safe_replace_tree(
    src: Path | str,
    dst: Path | str,
    *,
    root: Path | str | None = None,
    exclude_names: Collection[str] = EXCLUDED_APP_NAMES,
    prepare_tmp: Callable[[Path], None] | None = None,
) -> Path:
    """Atomically replace directory `dst` with a clean copy of `src` (`dst` -> `dst.prev`).

    Rejects any symlink in `src` or `dst`, verifies `dst` is confined inside allowed roots,
    excludes `__pycache__/`, `*.pyc`, `.DS_Store`, `.pytest_cache/`, and invokes `prepare_tmp`
    on `dst.tmp` (e.g. to write `MANIFEST.sha256` and render the plist) before swapping.
    """
    os.umask(0o077)
    raw_src = Path(src).expanduser()
    if raw_src.is_symlink() or not raw_src.exists() or not raw_src.is_dir():
        raise WriteDenied(f"invalid or symlinked source tree: {src}")

    raw_dst = Path(dst).expanduser()
    if raw_dst.is_symlink():
        raise WriteDenied(f"refusing to replace symlink target: {dst}")

    parent_raw = raw_dst.parent
    parent_resolved = parent_raw.resolve()
    dst_resolved = parent_resolved / raw_dst.name

    if dst_resolved.is_symlink():
        raise WriteDenied(f"refusing to replace symlink target: {dst_resolved}")

    roots = _allowed_roots(root)
    matched_root: Path | None = None
    for allowed in roots:
        if dst_resolved != allowed and dst_resolved.is_relative_to(allowed):
            matched_root = allowed
            break

    if matched_root is None:
        raise WriteDenied(f"tree target {dst_resolved} is outside allowed roots {roots}")

    _check_no_symlink_in_relpath(matched_root, parent_raw)
    parent_resolved.mkdir(parents=True, exist_ok=True, mode=0o700)

    excl_set = frozenset(exclude_names)
    src_resolved = raw_src.resolve()

    # Pre-scan source tree for symlinks before staging
    for walk_root, dirs, files in os.walk(src_resolved, followlinks=False):
        wpath = Path(walk_root)
        rel_dir = wpath.relative_to(src_resolved)
        if any(part in excl_set or part.endswith(".pyc") for part in rel_dir.parts):
            dirs[:] = []
            continue
        kept_dirs: list[str] = []
        for dname in dirs:
            dpath = wpath / dname
            if dpath.is_symlink():
                raise WriteDenied(f"symlinked directory in source tree rejected: {dpath}")
            if dname not in excl_set and not dname.endswith(".pyc"):
                kept_dirs.append(dname)
        dirs[:] = kept_dirs
        for fname in files:
            fpath = wpath / fname
            if fpath.is_symlink():
                raise WriteDenied(f"symlinked file in source tree rejected: {fpath}")

    tmp_dir = parent_resolved / f"{raw_dst.name}.tmp"
    prev_dir = parent_resolved / f"{raw_dst.name}.prev"

    if tmp_dir.is_symlink() or tmp_dir.exists():
        if tmp_dir.is_symlink() or tmp_dir.is_file():
            tmp_dir.unlink()
        else:
            shutil.rmtree(tmp_dir)

    tmp_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(tmp_dir, 0o700)

    try:
        for walk_root, dirs, files in os.walk(src_resolved, followlinks=False):
            wpath = Path(walk_root)
            rel_dir = wpath.relative_to(src_resolved)
            dirs[:] = sorted(d for d in dirs if d not in excl_set and not d.endswith(".pyc"))
            target_dir = tmp_dir / rel_dir if rel_dir != Path(".") else tmp_dir
            target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(target_dir, 0o700)

            for fname in sorted(files):
                if fname in excl_set or fname.endswith(".pyc"):
                    continue
                src_file = wpath / fname
                if src_file.is_symlink():
                    raise WriteDenied(f"symlinked file in source tree rejected: {src_file}")
                dst_file = target_dir / fname
                shutil.copy2(src_file, dst_file, follow_symlinks=False)
                os.chmod(dst_file, 0o600)

        if prepare_tmp is not None:
            prepare_tmp(tmp_dir)

        if dst_resolved.is_symlink():
            raise WriteDenied(f"symlink appeared at destination before swap: {dst_resolved}")

        if dst_resolved.exists():
            if prev_dir.is_symlink() or prev_dir.is_file():
                prev_dir.unlink()
            elif prev_dir.exists():
                shutil.rmtree(prev_dir)
            os.replace(dst_resolved, prev_dir)

        os.replace(tmp_dir, dst_resolved)
    except Exception:
        if tmp_dir.exists():
            with contextlib.suppress(OSError):
                shutil.rmtree(tmp_dir)
        raise

    return dst_resolved
