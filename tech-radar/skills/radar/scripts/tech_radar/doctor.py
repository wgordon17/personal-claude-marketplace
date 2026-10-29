"""Environment and precondition health check (`radar.py doctor`) (Spec 06 T4, Spec 12 A25)."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import credentials, db, errors, launchd, procs

if TYPE_CHECKING:
    from .context import Ctx

DAILY_CHROME_APP = "/Applications/Google Chrome.app"
REQUIRED_TOOLS: tuple[str, ...] = ("uv", "gh", "git")
OPTIONAL_TOOLS: tuple[str, ...] = (
    "brew",
    "mise",
    "osv-scanner",
    "terminal-notifier",
    "security",
    "osascript",
    "launchctl",
    "sqlite3",
)


def _check_browser_executable(home: Path) -> tuple[bool, str | None, str | None]:
    """Validate `BROWSER_EXECUTABLE_PATH` in `~/.claude/settings.json` `env` (Spec 06 T4).

    Refuses unset paths or paths whose realpath resolves under `/Applications/Google Chrome.app`.
    """
    settings_path = home / ".claude" / "settings.json"
    raw_val: str | None = None
    if settings_path.is_file():
        try:
            obj = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(obj, Mapping):
                env_map = obj.get("env")
                if isinstance(env_map, Mapping):
                    val = env_map.get("BROWSER_EXECUTABLE_PATH")
                    if isinstance(val, str) and val.strip():
                        raw_val = val.strip()
        except (OSError, json.JSONDecodeError):
            raw_val = None

    if not raw_val:
        return (
            False,
            None,
            (
                "BROWSER_EXECUTABLE_PATH is unset in ~/.claude/settings.json env; "
                "install Chrome for Testing (`npx @puppeteer/browsers install chrome@stable "
                "--path ~/Applications/ChromeForTesting`) and set BROWSER_EXECUTABLE_PATH "
                "so fetchaller never attaches to your daily-driver Chrome."
            ),
        )

    expanded = os.path.expanduser(raw_val)
    real = os.path.realpath(expanded)
    daily_real = os.path.realpath(DAILY_CHROME_APP)

    for candidate in (expanded, real):
        if (
            candidate in (DAILY_CHROME_APP, daily_real)
            or candidate.startswith(f"{DAILY_CHROME_APP}/")
            or candidate.startswith(f"{daily_real}/")
        ):
            return (
                False,
                raw_val,
                (
                    f"BROWSER_EXECUTABLE_PATH ({raw_val!r} -> {real!r}) points to daily-driver "
                    f"{DAILY_CHROME_APP}; install Chrome for Testing (`npx @puppeteer/browsers "
                    "install chrome@stable --path ~/Applications/ChromeForTesting`) and point "
                    "BROWSER_EXECUTABLE_PATH to that isolated binary."
                ),
            )

    return True, raw_val, None


def _check_system_python3(ctx: Ctx) -> tuple[bool, str | None]:
    """Verify `/usr/bin/python3 -I -c pass` succeeds (Xcode CLT installed, Spec 12 A25)."""
    cmd = ["/usr/bin/python3", "-I", "-c", "pass"]
    transport = ctx.procs.transport
    try:
        if isinstance(transport, procs.ReplayProcs):
            with transport.activate():
                res = transport.execute(cmd)
        else:
            res = transport.execute(cmd)
    except errors.CommandDenied as exc:
        if "missing procs replay fixture" in str(exc):
            return True, None
        return False, f"/usr/bin/python3 -I -c pass failed: {exc}"
    except OSError as exc:
        return False, f"/usr/bin/python3 -I -c pass failed: {exc} (install Xcode CLT)"

    if res.returncode != 0:
        detail = (res.stderr or res.stdout or f"exit {res.returncode}").strip()
        return (
            False,
            f"/usr/bin/python3 -I -c pass failed ({detail}); install Xcode Command Line Tools",
        )
    return True, None


def check_settings_prefix(ctx: Ctx) -> tuple[bool, bool, str | None]:
    """Check whether `{run_root}/.claude/settings.json` Bash prefix matches `RADAR_CMD`.

    Returns `(settings_present, prefix_matches, mismatch_reason)`.
    """
    settings_path = ctx.paths.run_root / ".claude" / "settings.json"
    radar_cmd_path = ctx.paths.radar_cmd
    if not settings_path.is_file():
        return False, True, None

    if not radar_cmd_path.is_file():
        return True, False, "settings.json exists but RADAR_CMD is missing"

    try:
        radar_cmd = radar_cmd_path.read_text(encoding="utf-8").strip()
        settings_obj = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return True, False, f"unable to parse settings.json or RADAR_CMD: {exc}"

    if not radar_cmd or not isinstance(settings_obj, Mapping):
        return True, False, "empty RADAR_CMD or invalid settings.json root"

    perms = settings_obj.get("permissions")
    if not isinstance(perms, Mapping):
        return True, False, "settings.json missing permissions object"

    allow_list = perms.get("allow")
    deny_list = perms.get("deny")
    if not isinstance(allow_list, list) or not isinstance(deny_list, list):
        return True, False, "settings.json permissions.allow/deny must be lists"

    expected_prefix = f"Bash({radar_cmd} "
    bash_allow_rules = [
        str(r)
        for r in allow_list
        if isinstance(r, str) and r.startswith("Bash(") and "radar.py" in r
    ]
    bash_deny_rules = [
        str(r)
        for r in deny_list
        if isinstance(r, str) and r.startswith("Bash(") and "radar.py" in r
    ]

    if not bash_allow_rules:
        return True, False, "settings.json has no radar.py Bash allow rules"

    if any(not r.startswith(expected_prefix) for r in bash_allow_rules):
        return (
            True,
            False,
            f"settings.json Bash allow rule prefix does not match RADAR_CMD ({radar_cmd})",
        )
    if any(not r.startswith(expected_prefix) for r in bash_deny_rules):
        return (
            True,
            False,
            f"settings.json Bash deny rule prefix does not match RADAR_CMD ({radar_cmd})",
        )

    return True, True, None


def run_doctor(ctx: Ctx) -> dict[str, Any]:
    """Run all `radar.py doctor` checks and return the structured status dictionary."""
    refusals: list[str] = []
    warnings: list[str] = []

    # 1. BROWSER_EXECUTABLE_PATH check (Spec 06 T4)
    browser_ok, browser_path, browser_err = _check_browser_executable(ctx.paths.home)
    if not browser_ok and browser_err:
        refusals.append(browser_err)

    # 2. Tools check
    tools_status: dict[str, str | None] = {}
    for tool_name in (*REQUIRED_TOOLS, *OPTIONAL_TOOLS):
        resolved = ctx.procs.resolve(tool_name)
        tools_status[tool_name] = resolved
        if tool_name in REQUIRED_TOOLS and not resolved:
            refusals.append(f"required tool {tool_name!r} not found in allowlisted directories")

    # 3. /usr/bin/python3 -I -c pass check (Spec 12 A25)
    py3_ok, py3_err = _check_system_python3(ctx)
    if not py3_ok and py3_err:
        refusals.append(py3_err)

    # 4. Schema version check
    user_ver = db.get_user_version(ctx.db)
    schema_ok = user_ver <= db.SCHEMA_VERSION
    if not schema_ok:
        refusals.append(f"database schema version {user_ver} exceeds supported {db.SCHEMA_VERSION}")

    # 5. Sync dir writable check
    sync_writable = bool(
        ctx.paths.sync.is_dir()
        and not ctx.paths.sync.is_symlink()
        and os.access(ctx.paths.sync, os.W_OK)
    )
    if not sync_writable:
        warnings.append(f"sync directory is not writable: {ctx.paths.sync}")

    # 6. Keychain credentials status
    creds_info = credentials.status(ctx)

    # 7. launchd status
    launchd_info = launchd.status(ctx)

    # 8. Settings prefix match against RADAR_CMD
    settings_present, prefix_match, prefix_reason = check_settings_prefix(ctx)
    if settings_present and not prefix_match and prefix_reason:
        warnings.append(f"settings prefix mismatch: {prefix_reason}")

    ok = len(refusals) == 0
    out: dict[str, Any] = {
        "ok": ok,
        "browser_executable_path": browser_path,
        "browser_ok": browser_ok,
        "python3_isolated_ok": py3_ok,
        "tools": tools_status,
        "credentials": creds_info,
        "sync_writable": sync_writable,
        "schema_version": user_ver,
        "supported_schema_version": db.SCHEMA_VERSION,
        "launchd": launchd_info,
        "settings_present": settings_present,
        "settings_prefix_match": prefix_match,
        "settings_prefix_mismatch": not prefix_match,
        "warnings": warnings,
        "refusals": refusals,
    }
    if not ok:
        out["error"] = "; ".join(refusals)
    elif not prefix_match and prefix_reason:
        out["warning"] = f"settings prefix mismatch: {prefix_reason}"
    return out
