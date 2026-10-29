"""Persistent O_EXCL lockfiles for run.lock and hourly.lock (Spec 11 §3, Spec 12 A26)."""

from __future__ import annotations

import contextlib
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import config

logger = logging.getLogger(__name__)

STALE_SECONDS: dict[str, int] = {
    "run": 2 * 3600,
    "hourly": 15 * 60,
}


@dataclass(frozen=True)
class Lock:
    """Acquired persistent lockfile handle."""

    name: str
    path: Path
    owner: str
    mode: str
    pid: int
    acquired_at: str
    broke_stale: str | None = None
    warning: str | None = None

    @property
    def run_id(self) -> str:
        """Alias for owner (run_id stored in lockfile JSON)."""
        return self.owner


def _resolve_now(now: datetime | str | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if isinstance(now, str):
        parsed = datetime.fromisoformat(now.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return now if now.tzinfo is not None else now.replace(tzinfo=UTC)


def _format_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def lock_path(name: str, state_dir: Path | str | None = None) -> Path:
    """Return `{state}/{name}.lock`."""
    clean_name = name[:-5] if name.endswith(".lock") else name
    base = Path(state_dir) if state_dir is not None else config.paths(ensure_dirs=True).state
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    return base / f"{clean_name}.lock"


def read_holder(name: str, *, state_dir: Path | str | None = None) -> dict[str, Any] | None:
    """Read and parse the JSON content of an existing lockfile, if present."""
    lpath = lock_path(name, state_dir=state_dir)
    if not lpath.is_file():
        return None
    try:
        raw = lpath.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _is_stale(
    lpath: Path,
    holder: Mapping[str, Any] | None,
    threshold_seconds: int,
    now_dt: datetime,
) -> bool:
    if holder and isinstance(holder.get("acquired_at"), str):
        try:
            acq_dt = datetime.fromisoformat(str(holder["acquired_at"]).replace("Z", "+00:00"))
            if acq_dt.tzinfo is None:
                acq_dt = acq_dt.replace(tzinfo=UTC)
            if (now_dt - acq_dt).total_seconds() > threshold_seconds:
                return True
        except ValueError:
            pass
    try:
        st = lpath.stat()
        mtime_dt = datetime.fromtimestamp(st.st_mtime, tz=UTC)
        return (now_dt - mtime_dt).total_seconds() > threshold_seconds
    except OSError:
        return False


def _create_excl(
    clean_name: str,
    lpath: Path,
    owner: str,
    mode: str,
    now_iso: str,
    *,
    broke_stale: str | None = None,
    warning: str | None = None,
) -> Lock | None:
    os.umask(0o077)
    payload = {
        "run_id": owner,
        "owner": owner,
        "mode": mode,
        "pid": os.getpid(),
        "acquired_at": now_iso,
    }
    raw_bytes = (json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
    try:
        fd = os.open(str(lpath), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return None
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw_bytes)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(lpath, 0o600)
    except Exception:
        with contextlib.suppress(OSError):
            lpath.unlink()
        raise
    return Lock(
        name=clean_name,
        path=lpath,
        owner=owner,
        mode=mode,
        pid=int(payload["pid"]),
        acquired_at=now_iso,
        broke_stale=broke_stale,
        warning=warning,
    )


def try_lock(
    name: str = "run",
    owner: str | None = None,
    mode: str | None = None,
    *,
    state_dir: Path | str | None = None,
    now: datetime | str | None = None,
) -> Lock | None:
    """Attempt to create `{state}/{name}.lock` with O_EXCL; return None if already held."""
    clean_name = name[:-5] if name.endswith(".lock") else name
    resolved_owner = owner or f"{clean_name}-{os.getpid()}"
    resolved_mode = mode or clean_name
    now_dt = _resolve_now(now)
    now_iso = _format_iso(now_dt)
    lpath = lock_path(clean_name, state_dir=state_dir)
    return _create_excl(clean_name, lpath, resolved_owner, resolved_mode, now_iso)


def acquire(
    name: str = "run",
    owner: str | None = None,
    mode: str | None = None,
    *,
    state_dir: Path | str | None = None,
    now: datetime | str | None = None,
) -> Lock | None:
    """Acquire `{state}/{name}.lock`, breaking a stale lock once with a logged warning."""
    clean_name = name[:-5] if name.endswith(".lock") else name
    resolved_owner = owner or f"{clean_name}-{os.getpid()}"
    resolved_mode = mode or clean_name
    now_dt = _resolve_now(now)
    now_iso = _format_iso(now_dt)
    lpath = lock_path(clean_name, state_dir=state_dir)

    first = _create_excl(clean_name, lpath, resolved_owner, resolved_mode, now_iso)
    if first is not None:
        return first

    holder = read_holder(clean_name, state_dir=state_dir)
    threshold = STALE_SECONDS.get(clean_name, STALE_SECONDS["run"])
    if not _is_stale(lpath, holder, threshold, now_dt):
        return None

    stale_owner = (
        str(holder.get("run_id") or holder.get("owner") or "unknown") if holder else "unknown"
    )
    warn_msg = (
        f"Breaking stale {clean_name}.lock held by {stale_owner} (exceeded {threshold}s threshold)"
    )
    logger.warning(warn_msg)
    with contextlib.suppress(OSError):
        lpath.unlink()

    return _create_excl(
        clean_name,
        lpath,
        resolved_owner,
        resolved_mode,
        now_iso,
        broke_stale=stale_owner,
        warning=warn_msg,
    )


def release(
    name_or_owner: str | Lock,
    owner: str | None = None,
    *,
    state_dir: Path | str | None = None,
) -> bool:
    """Release a lockfile only if its recorded owner/run_id matches (else no-op + warning)."""
    if isinstance(name_or_owner, Lock):
        clean_name = name_or_owner.name
        expected_owner = owner or name_or_owner.owner
        lpath = name_or_owner.path
    elif owner is None:
        # 1-arg form: `release(run_id)` defaults to `run.lock` per Spec 11 §3
        clean_name = "run"
        expected_owner = str(name_or_owner)
        lpath = lock_path(clean_name, state_dir=state_dir)
    else:
        raw_name = str(name_or_owner)
        clean_name = raw_name[:-5] if raw_name.endswith(".lock") else raw_name
        expected_owner = str(owner)
        lpath = lock_path(clean_name, state_dir=state_dir)

    if not lpath.is_file():
        return False

    holder = read_holder(clean_name, state_dir=lpath.parent)
    actual_owner = str((holder or {}).get("run_id") or (holder or {}).get("owner") or "")
    if actual_owner != expected_owner:
        logger.warning(
            "Refusing to release %s.lock: held by %r, caller passed %r",
            clean_name,
            actual_owner,
            expected_owner,
        )
        return False

    try:
        lpath.unlink()
        return True
    except OSError:
        return False
