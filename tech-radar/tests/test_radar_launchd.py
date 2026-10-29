"""Tests for appsync.py and launchd.py (Task 6 Step 5, Spec 07 §7, §9, Spec 12 A25)."""

from __future__ import annotations

import json
import os
import plistlib
import pty
import select
import stat
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import verify_app
from tech_radar import appsync, cli, context, db, http, launchd, procs
from tech_radar.errors import RadarError

FIXED_NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[1]
SCRIPTS_DIR = REPO_ROOT / "tech-radar" / "skills" / "radar" / "scripts"
RADAR_SCRIPT = SCRIPTS_DIR / "radar.py"
GOLDEN_PLIST = TESTS_DIR / "fixtures" / "launchd" / "hourly.plist.golden"


def _make_ctx(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
    *,
    extra_proc_roots: list[Path] | None = None,
) -> context.Ctx:
    return context.build(
        transports={
            "http": replay_http(),
            "gh": replay_gh(),
            "procs": replay_procs(extra_roots=extra_proc_roots),
        },
        now=FIXED_NOW,
    )


def _run_pty_cli(args: list[str], env: dict[str, str]) -> tuple[int, dict[str, Any]]:
    master_fd, slave_fd = pty.openpty()
    try:
        proc = subprocess.Popen(
            [sys.executable, str(RADAR_SCRIPT), *args],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=subprocess.PIPE,
            env=env,
            close_fds=True,
        )
    finally:
        os.close(slave_fd)

    chunks: list[bytes] = []
    try:
        while True:
            rlist, _, _ = select.select([master_fd], [], [], 0.1)
            if rlist:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                chunks.append(data)
            elif proc.poll() is not None:
                break
        proc.wait(timeout=15)
    finally:
        os.close(master_fd)
        if proc.stderr is not None:
            proc.stderr.close()

    out_text = b"".join(chunks).decode("utf-8", errors="replace")
    lines = [line.strip() for line in out_text.splitlines() if line.strip()]
    return proc.returncode, json.loads(lines[-1])


def test_plist_golden_and_app_copy_structure(
    fixture_home: Any,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    """Rendered plist matches golden and Spec 07 §9 invariants; app copy verifies cleanly."""
    _ = fixture_home
    ctx = _make_ctx(replay_http, replay_gh, replay_procs)
    res = appsync.sync(ctx)
    assert res.ok is True
    assert res.status == "synced"
    assert res.manifest_sha is not None

    # App copy structure
    app_dir = ctx.paths.app
    assert (app_dir / "radar.py").is_file()
    assert (app_dir / "radar.py.lock").is_file()
    assert (app_dir / "verify_app.py").is_file()
    assert (app_dir / "tech_radar" / "__init__.py").is_file()
    assert (app_dir / "MANIFEST.sha256").is_file()
    ok, reason = verify_app.verify_tree(app_dir, expected_manifest_sha=res.manifest_sha)
    assert ok is True, reason

    # Plist mode 0600 and golden comparison
    pfile = launchd.plist_path(ctx.paths)
    assert pfile.is_file()
    assert stat.S_IMODE(pfile.stat().st_mode) == 0o600

    rendered_text = pfile.read_text(encoding="utf-8")
    assert ".claude/plugins/cache" not in rendered_text

    normalized_rendered = (
        rendered_text.replace(str(ctx.paths.state), "{STATE}")
        .replace(str(ctx.paths.logs), "{LOGS}")
        .replace(res.manifest_sha, "{MANIFEST_SHA256}")
    )
    golden_text = GOLDEN_PLIST.read_text(encoding="utf-8")
    assert normalized_rendered == golden_text

    parsed = plistlib.loads(pfile.read_bytes())
    prog_args = parsed["ProgramArguments"]
    assert prog_args[0:3] == ["/usr/bin/python3", "-I", "-c"]
    assert prog_args[3] == (SCRIPTS_DIR / "verify_app.py").read_text(encoding="utf-8")
    assert prog_args[4] == res.manifest_sha
    assert prog_args[5] == str(ctx.paths.app)
    assert prog_args[6] == "/opt/homebrew/bin/uv"
    assert parsed["EnvironmentVariables"]["PATH"] == "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin"
    assert parsed["Umask"] == 63


def test_app_sync_noop_when_unchanged_and_rebuilds_when_extra_file_planted(
    fixture_home: Any,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    """Unchanged tree with verifying app/ is a no-op; planted extra file triggers repair."""
    _ = fixture_home
    ctx1 = _make_ctx(replay_http, replay_gh, replay_procs)
    res1 = appsync.sync(ctx1)
    assert res1.status == "synced"
    assert any(c[0][0] == "launchctl" for c in ctx1.procs.calls)

    # Second sync with unchanged source and intact app/ -> no-op, zero launchctl calls
    ctx2 = _make_ctx(replay_http, replay_gh, replay_procs)
    res2 = appsync.sync(ctx2)
    assert res2.ok is True
    assert res2.status == "noop"
    assert not any(c[0][0] == "launchctl" for c in ctx2.procs.calls)

    # Drop an unlisted file into {state}/app under an unchanged source tree -> repaired
    planted = ctx2.paths.app / "planted_unlisted.txt"
    planted.write_text("unlisted\n", encoding="utf-8")
    ctx3 = _make_ctx(replay_http, replay_gh, replay_procs)
    res3 = appsync.sync(ctx3)
    assert res3.ok is True
    assert res3.status == "synced"
    assert not planted.exists()
    ok, _ = verify_app.verify_tree(ctx3.paths.app, expected_manifest_sha=res3.manifest_sha)
    assert ok is True


def test_app_sync_provenance_mismatch_refuses_and_creates_alert(
    fixture_home: Any,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    """Tampering cached plugin script when commit is present refuses sync and raises alert."""
    home = fixture_home(scenario="baseline", marketplace="present")
    cached_radar = (
        home
        / ".claude"
        / "plugins"
        / "cache"
        / "personal-claude-marketplace"
        / "tech-radar"
        / "0.1.0"
        / "skills"
        / "radar"
        / "scripts"
        / "radar.py"
    )
    cached_radar.write_text(
        cached_radar.read_text(encoding="utf-8") + "\n# tampered in cache\n",
        encoding="utf-8",
    )

    ctx = _make_ctx(replay_http, replay_gh, replay_procs)
    res = appsync.sync(ctx)
    assert res.ok is False
    assert res.status == "refused"
    assert res.alert_id is not None

    alert_row = ctx.db.execute(
        "SELECT tier, kind, confidence, state FROM alerts WHERE alert_id = ?",
        (res.alert_id,),
    ).fetchone()
    assert alert_row is not None
    assert str(alert_row["tier"]) == "P0"
    assert str(alert_row["kind"]) == "app_integrity"
    assert str(alert_row["confidence"]) == "confirmed"


def test_app_sync_shallow_missing_commit_head_match_and_version_mismatch(
    fixture_home: Any,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    """shallow-missing with matching HEAD version syncs; differing version skips without alert."""
    home = fixture_home(scenario="baseline", marketplace="shallow-missing")

    # 1. HEAD version == installed version (0.1.0) and files match HEAD -> syncs
    ctx1 = _make_ctx(replay_http, replay_gh, replay_procs)
    res1 = appsync.sync(ctx1)
    assert res1.ok is True
    assert res1.status == "synced"
    sentinel = ctx1.paths.app / "radar.py"
    assert sentinel.is_file()

    # 2. Change marketplace clone HEAD plugin.json version to 0.2.0 while installed is 0.1.0
    mkt_plugin_json = (
        home
        / ".claude"
        / "plugins"
        / "marketplaces"
        / "personal-claude-marketplace"
        / "tech-radar"
        / ".claude-plugin"
        / "plugin.json"
    )
    data = json.loads(mkt_plugin_json.read_text(encoding="utf-8"))
    data["version"] = "0.2.0"
    mkt_plugin_json.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    ctx2 = _make_ctx(replay_http, replay_gh, replay_procs)
    res2 = appsync.sync(ctx2)
    assert res2.ok is True
    assert res2.status == "skipped"
    assert res2.coverage_warning == "app sync skipped: provenance unavailable"
    assert db.kv_get(ctx2.db, "last_app_sync_warning") == "app sync skipped: provenance unavailable"
    # Current app/ kept and zero app_integrity alerts created
    assert sentinel.is_file()
    alerts_cnt = int(
        ctx2.db.execute("SELECT COUNT(*) FROM alerts WHERE kind = 'app_integrity'").fetchone()[0]
    )
    assert alerts_cnt == 0


def test_app_sync_rollback_on_launchctl_bootstrap_failure(
    fixture_home: Any,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
    tmp_path: Path,
) -> None:
    """When launchctl bootstrap fails, app and plist are restored from app.prev and previous."""
    _ = fixture_home
    ctx1 = _make_ctx(replay_http, replay_gh, replay_procs)
    res1 = appsync.sync(ctx1)
    assert res1.status == "synced"
    orig_manifest_sha = res1.manifest_sha
    pfile = launchd.plist_path(ctx1.paths)
    orig_plist_bytes = pfile.read_bytes()

    # Stage a modified dev source tree and configure launchctl bootstrap to return exit code 1
    dev_src = tmp_path / "dev_scripts"
    appsync.fsguard.safe_replace_tree(SCRIPTS_DIR, ctx1.paths.state / "dev_src_copy")
    dev_src = ctx1.paths.state / "dev_src_copy"
    (dev_src / "radar.py").write_text(
        (dev_src / "radar.py").read_text(encoding="utf-8") + "\n# v2 dev change\n",
        encoding="utf-8",
    )

    fail_fix = tmp_path / "fail_bootstrap"
    fail_fix.mkdir(parents=True, exist_ok=True)
    (fail_fix / "bootstrap_fail.json").write_text(
        json.dumps(
            {
                "argv": [
                    "launchctl",
                    "bootstrap",
                    "gui/{UID}",
                    "{HOME}/Library/LaunchAgents/com.user.tech-radar.hourly.plist",
                ],
                "returncode": 1,
                "stdout": "",
                "stderr": "Bootstrap failed: 5: Input/output error",
            }
        ),
        encoding="utf-8",
    )

    ctx2 = _make_ctx(
        replay_http,
        replay_gh,
        replay_procs,
        extra_proc_roots=[fail_fix],
    )
    with pytest.raises(RadarError, match="launchctl bootstrap failed"):
        appsync.sync(ctx2, from_path=dev_src)

    # Verify rollback: app/ and plist match original v1 state
    assert pfile.read_bytes() == orig_plist_bytes
    assert "# v2 dev change" not in (ctx2.paths.app / "radar.py").read_text(encoding="utf-8")
    ok, _ = verify_app.verify_tree(ctx2.paths.app, expected_manifest_sha=orig_manifest_sha)
    assert ok is True


def test_launchd_install_uninstall_status_and_tty_gate(
    fixture_home: Any,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """install, uninstall, status record expected ReplayProcs.calls; install without TTY exits 4."""
    _ = fixture_home
    ctx = _make_ctx(replay_http, replay_gh, replay_procs)
    db.kv_set(ctx.db, "last_hourly_at", "2026-10-28T11:00:00Z")

    inst_res = launchd.install(ctx)
    assert inst_res["ok"] is True
    pfile = launchd.plist_path(ctx.paths)
    assert pfile.is_file()
    assert [c[0] for c in ctx.procs.calls if c[0][0] == "launchctl"] == [
        ["launchctl", "bootout", "gui/{UID}/com.user.tech-radar.hourly"],
        [
            "launchctl",
            "bootstrap",
            "gui/{UID}",
            "{HOME}/Library/LaunchAgents/com.user.tech-radar.hourly.plist",
        ],
    ]

    st_res = launchd.status(ctx)
    assert st_res["ok"] is True
    assert st_res["loaded"] is True
    assert st_res["last_exit_status"] == 0
    assert st_res["last_hourly_at"] == "2026-10-28T11:00:00Z"
    assert st_res["plist_exists"] is True

    uninst_res = launchd.uninstall(ctx)
    assert uninst_res["ok"] is True
    assert uninst_res["plist_removed"] is True
    assert not pfile.exists()
    # App and state data remain intact after uninstall
    assert (ctx.paths.app / "radar.py").is_file()

    # CLI launchd install/uninstall and app sync --from without TTY -> exit 4
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert cli.main(["launchd", "install", "--channel", "cli"]) == 4
    assert cli.main(["launchd", "uninstall", "--channel", "cli"]) == 4
    assert cli.main(["app", "sync", "--from", str(SCRIPTS_DIR), "--channel", "cli"]) == 4
    capsys.readouterr()

    # CLI app sync --from with TTY succeeds
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert cli.main(["app", "sync", "--from", str(SCRIPTS_DIR), "--channel", "cli"]) == 0
    sync_cli_out = json.loads(capsys.readouterr().out.strip())
    assert sync_cli_out["ok"] is True
