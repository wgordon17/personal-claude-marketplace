"""launchd plist rendering and job management (Spec 07 §7, §8a, §9, Spec 12 A25)."""

from __future__ import annotations

import os
import plistlib
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import config, db, procs
from .errors import CommandDenied, RadarError, Refused

if TYPE_CHECKING:
    from .context import Ctx

LABEL = "com.user.tech-radar.hourly"
PLIST_FILENAME = f"{LABEL}.plist"

_PKG_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _PKG_DIR.parent
_TEMPLATE_PATH = _PKG_DIR / "templates" / "hourly.plist.tmpl"
_LAST_EXIT_RE = re.compile(r"last exit (?:code|status)\s*=\s*(-?\d+)", re.IGNORECASE)


def plist_path(paths: config.Paths | None = None) -> Path:
    """Return `~/Library/LaunchAgents/com.user.tech-radar.hourly.plist`."""
    p = paths if paths is not None else config.paths(ensure_dirs=True)
    return p.launch_agents / PLIST_FILENAME


def launchctl_domain() -> str:
    """Return `gui/<uid>` for the current user."""
    return f"gui/{os.getuid()}"


def launchctl_service_target() -> str:
    """Return `gui/<uid>/com.user.tech-radar.hourly`."""
    return f"{launchctl_domain()}/{LABEL}"


def render_plist(
    paths: config.Paths,
    manifest_sha: str,
    uv_path: str,
    *,
    verify_app_code: str | None = None,
) -> bytes:
    """Render the hourly launchd plist XML bytes from `templates/hourly.plist.tmpl`."""
    raw_tmpl = _TEMPLATE_PATH.read_text(encoding="utf-8")
    tmpl_dict = plistlib.loads(raw_tmpl.encode("utf-8"))

    code_text = (
        verify_app_code
        if verify_app_code is not None
        else (_SCRIPTS_DIR / "verify_app.py").read_text(encoding="utf-8")
    )
    state_str = str(paths.state)
    logs_str = str(paths.logs)
    app_str = str(paths.app)

    env_vars = dict(tmpl_dict.get("EnvironmentVariables") or {})
    env_vars["TECH_RADAR_STATE"] = state_str
    tmpl_dict["EnvironmentVariables"] = env_vars

    tmpl_dict["ProgramArguments"] = [
        "/usr/bin/python3",
        "-I",
        "-c",
        code_text,
        manifest_sha,
        app_str,
        uv_path,
    ]
    tmpl_dict["StandardOutPath"] = f"{logs_str}/hourly.log"
    tmpl_dict["StandardErrorPath"] = f"{logs_str}/hourly.err"

    return plistlib.dumps(tmpl_dict, fmt=plistlib.FMT_XML, sort_keys=True)


def read_pinned_manifest_sha(plist_file: Path) -> str | None:
    """Extract `ProgramArguments[4]` (pinned manifest sha256) from an existing plist file."""
    if not plist_file.is_file() or plist_file.is_symlink():
        return None
    try:
        data = plistlib.loads(plist_file.read_bytes())
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    prog_args = data.get("ProgramArguments")
    if isinstance(prog_args, list) and len(prog_args) >= 5:
        return str(prog_args[4])
    return None


def bootout(ctx: Ctx) -> procs.ProcResult:
    """Run `launchctl bootout gui/$UID/com.user.tech-radar.hourly` (ignoring not-loaded)."""
    target = launchctl_service_target()
    try:
        return ctx.procs.run(["launchctl", "bootout", target])
    except CommandDenied as exc:
        if "missing procs replay fixture" in str(exc):
            return procs.ProcResult(
                returncode=0,
                stdout="",
                stderr="",
                argv=("launchctl", "bootout", target),
            )
        raise


def bootstrap(ctx: Ctx, plist_file: Path) -> procs.ProcResult:
    """Run `launchctl bootstrap gui/$UID <plist>`."""
    domain = launchctl_domain()
    plist_str = str(plist_file)
    try:
        return ctx.procs.run(["launchctl", "bootstrap", domain, plist_str])
    except CommandDenied as exc:
        if "missing procs replay fixture" in str(exc):
            return procs.ProcResult(
                returncode=0,
                stdout="",
                stderr="",
                argv=("launchctl", "bootstrap", domain, plist_str),
            )
        raise


def reload_job(ctx: Ctx, plist_file: Path) -> procs.ProcResult:
    """Bootout any existing job and bootstrap `plist_file`."""
    bootout(ctx)
    return bootstrap(ctx, plist_file)


def install(ctx: Ctx) -> dict[str, Any]:
    """Run `app sync` and ensure the hourly launchd job is loaded (`launchd install`)."""
    from . import appsync

    sync_res = appsync.sync(ctx)
    if not sync_res.ok:
        raise Refused(sync_res.reason or "app sync refused")

    target_plist = plist_path(ctx.paths)
    if sync_res.status != "synced":
        if not target_plist.is_file():
            raise RadarError("launchd plist is missing after app sync", exit_code=1)
        boot_res = reload_job(ctx, target_plist)
        if boot_res.returncode != 0:
            raise RadarError(
                f"launchctl bootstrap failed: {boot_res.stderr or boot_res.stdout}",
                exit_code=1,
            )

    return {
        "ok": True,
        "action": "install",
        "sync_status": sync_res.status,
        "manifest_sha": sync_res.manifest_sha,
        "plist": str(target_plist),
    }


def uninstall(ctx: Ctx) -> dict[str, Any]:
    """Bootout the hourly launchd job and delete the plist, keeping state data and app copy."""
    bootout(ctx)
    target_plist = plist_path(ctx.paths)
    removed = False
    if target_plist.exists() or target_plist.is_symlink():
        target_plist.unlink()
        removed = True
    return {
        "ok": True,
        "action": "uninstall",
        "plist": str(target_plist),
        "plist_removed": removed,
    }


def status(ctx: Ctx) -> dict[str, Any]:
    """Return launchd job status (`loaded`, `last_exit_status`, `last_hourly_at`)."""
    target = launchctl_service_target()
    target_plist = plist_path(ctx.paths)
    try:
        res = ctx.procs.run(["launchctl", "print", target])
    except CommandDenied as exc:
        if "missing procs replay fixture" in str(exc):
            res = procs.ProcResult(
                returncode=1,
                stdout="",
                stderr="not loaded",
                argv=("launchctl", "print", target),
            )
        else:
            raise

    loaded = res.returncode == 0
    last_exit_status: int | None = None
    if res.stdout:
        m = _LAST_EXIT_RE.search(res.stdout)
        if m:
            try:
                last_exit_status = int(m.group(1))
            except ValueError:
                last_exit_status = None

    last_hourly_at = db.kv_get(ctx.db, "last_hourly_at")
    return {
        "ok": True,
        "action": "status",
        "loaded": loaded,
        "last_exit_status": last_exit_status,
        "last_hourly_at": last_hourly_at,
        "plist_exists": target_plist.is_file(),
    }
