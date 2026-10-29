"""Run context (Ctx) and transport/DB builder for tech-radar."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import config, credentials, db, http, procs
from .errors import Refused
from .profile import Profile


class Http:
    """Bound HTTP client wrapper on Ctx."""

    def __init__(
        self,
        transport: http.Transport,
        cache_conn: sqlite3.Connection | None,
        now_fn: Any,
    ) -> None:
        self.transport = transport
        self.cache_conn = cache_conn
        self._now_fn = now_fn

    @property
    def calls(self) -> list[tuple[str, str, str | None]]:
        """Return recorded HTTP calls when backed by ReplayHttp."""
        raw_calls = getattr(self.transport, "calls", None)
        return raw_calls if isinstance(raw_calls, list) else []

    def fetch(
        self,
        spec_or_url: str | Any,
        *,
        method: str = "GET",
        body: bytes | str | None = None,
        headers: Mapping[str, str] | None = None,
        ttl_hours: int = 24,
        use_cache: bool = True,
        now: datetime | str | None = None,
    ) -> http.HttpResponse:
        """Fetch an HTTPS URL through the bound transport and HTTP cache."""
        active_now = now if now is not None else self._now_fn()
        return http.fetch(
            spec_or_url,
            method=method,
            body=body,
            headers=headers,
            transport=self.transport,
            cache_conn=self.cache_conn if use_cache else None,
            ttl_hours=ttl_hours,
            now=active_now,
        )

    def __call__(
        self,
        spec_or_url: str | Any,
        *,
        method: str = "GET",
        body: bytes | str | None = None,
        headers: Mapping[str, str] | None = None,
        ttl_hours: int = 24,
        use_cache: bool = True,
        now: datetime | str | None = None,
    ) -> http.HttpResponse:
        """Allow calling ctx.http(url, ...) directly."""
        return self.fetch(
            spec_or_url,
            method=method,
            body=body,
            headers=headers,
            ttl_hours=ttl_hours,
            use_cache=use_cache,
            now=now,
        )


class Gh:
    """Bound read-only GitHub CLI wrapper on Ctx."""

    def __init__(self, transport: procs.ProcsTransport) -> None:
        self.transport = transport

    @property
    def calls(self) -> list[tuple[list[str], frozenset[str]]]:
        """Return recorded gh subprocess calls when backed by ReplayProcs."""
        raw_calls = getattr(self.transport, "calls", None)
        return raw_calls if isinstance(raw_calls, list) else []

    def __call__(
        self,
        args: Sequence[str],
        *,
        timeout: float = 60.0,
    ) -> procs.ProcResult:
        """Execute `gh <args>` (must start with 'api') via the bound transport."""
        if isinstance(self.transport, procs.ReplayProcs):
            with self.transport.activate():
                return procs.gh(args, transport=self.transport, timeout=timeout)
        return procs.gh(args, transport=self.transport, timeout=timeout)

    def api(
        self,
        endpoint_or_args: str | Sequence[str],
        *,
        paginate: bool = False,
        timeout: float = 60.0,
    ) -> procs.ProcResult:
        """Convenience helper for `gh api ...`."""
        if isinstance(endpoint_or_args, str):
            cmd = ["api", endpoint_or_args]
            if paginate:
                cmd.append("--paginate")
            return self(cmd, timeout=timeout)
        cmd = list(endpoint_or_args)
        if not cmd or cmd[0] != "api":
            cmd = ["api", *cmd]
        if paginate and "--paginate" not in cmd:
            cmd.append("--paginate")
        return self(cmd, timeout=timeout)


class Procs:
    """Bound hardened subprocess and git wrapper on Ctx."""

    def __init__(self, transport: procs.ProcsTransport) -> None:
        self.transport = transport

    @property
    def calls(self) -> list[tuple[list[str], frozenset[str]]]:
        """Return recorded subprocess calls when backed by ReplayProcs."""
        raw_calls = getattr(self.transport, "calls", None)
        return raw_calls if isinstance(raw_calls, list) else []

    def resolve(self, name: str, *, force_live: bool = False) -> str | None:
        """Resolve an allowlisted binary name to its absolute path."""
        if isinstance(self.transport, procs.ReplayProcs) and not force_live:
            with self.transport.activate():
                return procs.resolve(name, force_live=force_live)
        return procs.resolve(name, force_live=force_live)

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = 60.0,
        free_text_indices: Collection[int] = (),
        allowed_flags: Collection[str] = (),
        allowed_prefixes: Collection[str] = (),
    ) -> procs.ProcResult:
        """Validate and run an argv command via the bound transport."""
        if isinstance(self.transport, procs.ReplayProcs):
            with self.transport.activate():
                return procs.run(
                    argv,
                    cwd=cwd,
                    env=env,
                    timeout=timeout,
                    transport=self.transport,
                    free_text_indices=free_text_indices,
                    allowed_flags=allowed_flags,
                    allowed_prefixes=allowed_prefixes,
                )
        return procs.run(
            argv,
            cwd=cwd,
            env=env,
            timeout=timeout,
            transport=self.transport,
            free_text_indices=free_text_indices,
            allowed_flags=allowed_flags,
            allowed_prefixes=allowed_prefixes,
        )

    def git(
        self,
        args: Sequence[str],
        repo: Path | str | None = None,
        *,
        timeout: float = 60.0,
    ) -> procs.ProcResult:
        """Execute a hardened read-only git command via the bound transport."""
        if isinstance(self.transport, procs.ReplayProcs):
            with self.transport.activate():
                return procs.git(args, repo, transport=self.transport, timeout=timeout)
        return procs.git(args, repo, transport=self.transport, timeout=timeout)

    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = 60.0,
        free_text_indices: Collection[int] = (),
        allowed_flags: Collection[str] = (),
        allowed_prefixes: Collection[str] = (),
    ) -> procs.ProcResult:
        """Allow calling ctx.procs(argv, ...) directly."""
        return self.run(
            argv,
            cwd=cwd,
            env=env,
            timeout=timeout,
            free_text_indices=free_text_indices,
            allowed_flags=allowed_flags,
            allowed_prefixes=allowed_prefixes,
        )


@dataclass
class Ctx:
    """Execution context passed to all collectors, sources, evaluators, and renderers."""

    paths: config.Paths
    db: sqlite3.Connection
    http: Http
    gh: Gh
    procs: Procs
    now: datetime
    run_id: str | None = None
    profile: Profile = field(default_factory=Profile)
    cache_db: sqlite3.Connection | None = None
    credentials: Any = None

    @property
    def now_iso(self) -> str:
        """Return ctx.now formatted as UTC ISO-8601 timestamp."""
        return self.now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def reload_profile(self) -> Profile:
        """Reload Profile from sync_dir and radar.db kv['private_scopes']."""
        self.profile = Profile.load(self.paths.sync, conn=self.db)
        return self.profile


def _parse_now(now: datetime | str | None) -> datetime:
    if now is None:
        return datetime.now(UTC)
    if isinstance(now, str):
        parsed = datetime.fromisoformat(now.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return now if now.tzinfo is not None else now.replace(tzinfo=UTC)


def _transports_from_replay_env() -> tuple[
    http.Transport,
    procs.ProcsTransport,
    procs.ProcsTransport,
]:
    raw_dirs = os.environ.get("TECH_RADAR_REPLAY_DIR", "")
    roots = [Path(p).expanduser() for p in raw_dirs.split(os.pathsep) if p]
    http_roots: list[Path] = []
    procs_roots: list[Path] = []
    gh_roots: list[Path] = []

    for r in roots:
        if (r / "http").is_dir():
            http_roots.append(r / "http")
        http_roots.append(r)

        if (r / "procs" / "gh").is_dir():
            gh_roots.append(r / "procs" / "gh")
        if (r / "gh").is_dir():
            gh_roots.append(r / "gh")
        if (r / "procs").is_dir():
            gh_roots.append(r / "procs")
            procs_roots.append(r / "procs")
        gh_roots.append(r)
        procs_roots.append(r)

    http_transport = http.ReplayHttp(http_roots)
    procs_transport = procs.ReplayProcs(procs_roots, passthrough=("git",))
    gh_transport = procs.ReplayProcs(gh_roots, passthrough=("git",))
    return http_transport, gh_transport, procs_transport


def build(
    transports: Mapping[str, Any] | tuple[Any, ...] | None = None,
    now: datetime | str | None = None,
    *,
    run_id: str | None = None,
    http_transport: http.Transport | None = None,
    gh_transport: procs.ProcsTransport | None = None,
    procs_transport: procs.ProcsTransport | None = None,
    db_conn: sqlite3.Connection | None = None,
    cache_conn: sqlite3.Connection | None = None,
) -> Ctx:
    """Create a live or replay Ctx (Spec 04 § Run context, Spec 11 §1, §4, Spec 12 A17)."""
    pre_paths = config.paths(ensure_dirs=False)
    if not config.test_hooks_safe(pre_paths):
        raise Refused(
            "test hooks (TECH_RADAR_REPLAY_DIR / TECH_RADAR_NOTIFY_SINK) "
            "refused on default installation paths"
        )
    p = config.paths(ensure_dirs=True)

    h_tr: http.Transport | None = http_transport
    g_tr: procs.ProcsTransport | None = gh_transport
    p_tr: procs.ProcsTransport | None = procs_transport

    if isinstance(transports, Mapping):
        h_tr = h_tr or transports.get("http")
        g_tr = g_tr or transports.get("gh")
        p_tr = p_tr or transports.get("procs")
    elif isinstance(transports, tuple):
        if len(transports) >= 1 and h_tr is None:
            h_tr = transports[0]
        if len(transports) >= 2 and g_tr is None:
            g_tr = transports[1]
        if len(transports) >= 3 and p_tr is None:
            p_tr = transports[2]
    elif transports is not None:
        h_tr = h_tr or getattr(transports, "http", None)
        g_tr = g_tr or getattr(transports, "gh", None)
        p_tr = p_tr or getattr(transports, "procs", None)

    if os.environ.get("TECH_RADAR_REPLAY_DIR") and (h_tr is None or g_tr is None or p_tr is None):
        env_h, env_g, env_p = _transports_from_replay_env()
        h_tr = h_tr or env_h
        g_tr = g_tr or env_g
        p_tr = p_tr or env_p

    if h_tr is None:
        h_tr = http.LiveTransport()
    if p_tr is None:
        p_tr = procs.LiveProcs()
    if g_tr is None:
        g_tr = p_tr if isinstance(p_tr, procs.ReplayProcs) else procs.LiveProcs()

    conn = db_conn if db_conn is not None else db.connect(p.db, auto_migrate=True)
    c_conn = cache_conn if cache_conn is not None else db.connect_cache(p.cache_db)
    resolved_now = _parse_now(now)
    prof = Profile.load(p.sync, conn=conn)

    ctx_holder: list[Ctx] = []

    def _get_now() -> datetime:
        return ctx_holder[0].now if ctx_holder else resolved_now

    procs_wrapper = Procs(p_tr)
    ctx = Ctx(
        paths=p,
        db=conn,
        cache_db=c_conn,
        http=Http(h_tr, c_conn, _get_now),
        gh=Gh(g_tr),
        procs=procs_wrapper,
        now=resolved_now,
        run_id=run_id,
        profile=prof,
        credentials=credentials.Credentials(procs_wrapper),
    )
    ctx_holder.append(ctx)
    return ctx
