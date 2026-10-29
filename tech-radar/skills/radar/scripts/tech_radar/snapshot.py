"""SQLite snapshot backup to the sync directory (Spec 02, Spec 09)."""

from __future__ import annotations

import contextlib
import hashlib
import os
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from . import db, errors, fsguard

if TYPE_CHECKING:
    from .context import Ctx


def create_snapshot(
    ctx: Ctx,
    *,
    sync_dir: Path | str | None = None,
) -> tuple[Path, str]:
    """Create `{sync}/radar.snapshot.db` via `sqlite3.Connection.backup()`.

    Writes to `{sync}/.radar.snapshot.db.tmp`, ensures `http_cache` is absent, sets mode 0600,
    atomically replaces `{sync}/radar.snapshot.db`, and records the SHA-256 hex digest in
    `kv['last_snapshot_sha']`.
    """
    os.umask(0o077)
    raw_sync = Path(sync_dir).expanduser() if sync_dir is not None else ctx.paths.sync
    if raw_sync.is_symlink() or not raw_sync.exists() or not raw_sync.is_dir():
        raise errors.WriteDenied(f"sync directory unavailable or symlinked: {raw_sync}")

    sync_resolved = raw_sync.resolve()
    allowed_roots = fsguard._allowed_roots(raw_sync)
    if sync_resolved not in allowed_roots and not any(
        sync_resolved.is_relative_to(r) for r in allowed_roots
    ):
        raise errors.WriteDenied(f"sync directory {sync_resolved} outside allowed roots")

    target_raw = raw_sync / "radar.snapshot.db"
    tmp_raw = raw_sync / ".radar.snapshot.db.tmp"
    if target_raw.is_symlink() or tmp_raw.is_symlink():
        raise errors.WriteDenied("refusing to write snapshot to symlink target")

    target_path = sync_resolved / "radar.snapshot.db"
    tmp_path = sync_resolved / ".radar.snapshot.db.tmp"
    if target_path.is_symlink() or tmp_path.is_symlink():
        raise errors.WriteDenied("refusing to write snapshot to symlink target")

    if tmp_path.exists():
        with contextlib.suppress(OSError):
            tmp_path.unlink()

    ctx.db.commit()
    try:
        dest_conn = sqlite3.connect(str(tmp_path), timeout=30.0)
        try:
            ctx.db.backup(dest_conn)
            dest_conn.execute("DROP TABLE IF EXISTS http_cache")
            dest_conn.execute("PRAGMA journal_mode=DELETE;")
            dest_conn.commit()
        finally:
            dest_conn.close()

        for sidecar_ext in ("-wal", "-shm"):
            sidecar = Path(str(tmp_path) + sidecar_ext)
            if sidecar.exists():
                with contextlib.suppress(OSError):
                    sidecar.unlink()

        os.chmod(tmp_path, 0o600)
        if target_raw.is_symlink() or target_path.is_symlink():
            raise errors.WriteDenied("symlink appeared at radar.snapshot.db before replace")
        os.replace(tmp_path, target_path)
        os.chmod(target_path, 0o600)
    except Exception:
        if tmp_path.exists():
            with contextlib.suppress(OSError):
                tmp_path.unlink()
        raise

    sha_hex = hashlib.sha256(target_path.read_bytes()).hexdigest()
    db.kv_set(ctx.db, "last_snapshot_sha", sha_hex)
    return target_path, sha_hex
