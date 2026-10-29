"""Scaffold tests for tech-radar CLI entry, path config, and terminal-only gate."""

from __future__ import annotations

import json
import os
import pty
import select
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from tech_radar import cli, config, errors

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "tech-radar" / "skills" / "radar" / "scripts"
RADAR_SCRIPT = SCRIPTS_DIR / "radar.py"

SPEC_01_SUBCOMMANDS = (
    "doctor",
    "bootstrap init",
    "bootstrap apply",
    "bootstrap lockdown",
    "credentials status",
    "credentials guide",
    "plan-run",
    "collect",
    "enrich",
    "verdict apply",
    "render",
    "decide",
    "decide-pending",
    "feedback",
    "taste diff",
    "taste propose",
    "taste apply",
    "request-confirm",
    "pending list",
    "profile private-scopes",
    "confirm",
    "decisions apply",
    "alert",
    "exposure",
    "hourly",
    "launchd",
    "app sync",
    "notify-test",
    "sources list",
    "sources check",
    "search",
    "usage record",
)


def _run_pty_command(cmd: list[str], env: dict[str, str]) -> tuple[int, str]:
    """Run a command attached to a real pseudo-terminal on stdin and stdout."""
    master_fd, slave_fd = pty.openpty()
    try:
        proc = subprocess.Popen(
            cmd,
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

    output = b"".join(chunks).decode("utf-8", errors="replace")
    return proc.returncode, output


def test_help_lists_all_spec_01_subcommands_and_writes_no_pycache() -> None:
    """(a) --help exits 0, lists every Spec 01 subcommand, and creates no __pycache__ in S/."""
    env = {k: v for k, v in os.environ.items() if k != "UV_FROZEN"}
    res = subprocess.run(
        ["uv", "run", "--script", "--locked", "--isolated", str(RADAR_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    for subcmd in SPEC_01_SUBCOMMANDS:
        assert subcmd in res.stdout, f"Missing {subcmd!r} in --help output"

    pycache_dirs = list(SCRIPTS_DIR.rglob("__pycache__"))
    pyc_files = list(SCRIPTS_DIR.rglob("*.pyc"))
    assert not pycache_dirs, f"Unexpected __pycache__ directories under S/: {pycache_dirs}"
    assert not pyc_files, f"Unexpected .pyc files under S/: {pyc_files}"


def test_env_overrides_and_dir_permissions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """(b) & (c) TECH_RADAR_* overrides incl. RUN_ROOT honoured, dirs created 0700."""
    custom_home = tmp_path / "custom_home"
    custom_state = tmp_path / "custom_state"
    custom_run = tmp_path / "custom_run_root"
    custom_logs = tmp_path / "custom_logs"
    custom_sync = tmp_path / "custom_sync"
    root_a = tmp_path / "proj_a"
    root_b = tmp_path / "proj_b"

    (custom_home / "Library").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TECH_RADAR_HOME", str(custom_home))
    monkeypatch.setenv("TECH_RADAR_STATE", str(custom_state))
    monkeypatch.setenv("TECH_RADAR_RUN_ROOT", str(custom_run))
    monkeypatch.setenv("TECH_RADAR_LOGS", str(custom_logs))
    monkeypatch.setenv("TECH_RADAR_SYNC", str(custom_sync))
    monkeypatch.setenv("TECH_RADAR_PROJECT_ROOTS", f"{root_a}{os.pathsep}{root_b}")

    resolved = config.paths(ensure_dirs=True)
    assert resolved.home == custom_home
    assert resolved.state == custom_state
    assert resolved.db == custom_state / "radar.db"
    assert resolved.cache_db == custom_state / "cache.db"
    assert resolved.run_root == custom_run
    assert resolved.runs == custom_run / "runs"
    assert resolved.app == custom_state / "app"
    assert resolved.exposure == custom_state / "exposure"
    assert resolved.logs == custom_logs
    assert resolved.sync == custom_sync
    assert resolved.launch_agents == custom_home / "Library" / "LaunchAgents"
    assert resolved.project_roots == (root_a, root_b)

    for directory in (
        resolved.state,
        resolved.run_root,
        resolved.runs,
        resolved.exposure,
        resolved.logs,
        resolved.launch_agents,
    ):
        assert directory.is_dir(), f"Expected directory {directory} to be created"
        mode = stat.S_IMODE(directory.stat().st_mode)
        assert mode == 0o700, f"Expected 0700 for {directory}, got {oct(mode)}"
    assert not resolved.sync.exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["doctor"],
        ["credentials", "status"],
        ["credentials", "guide", "producthunt"],
        ["plan-run", "--requested", "daily"],
        ["collect", "--inventory-only"],
        ["collect", "--run", "20261028T070000Z-daily"],
        ["enrich", "--run", "20261028T070000Z-weekly"],
        ["verdict", "apply", "--run", "20261028T070000Z-daily"],
        ["render", "--run", "20261028T070000Z-daily"],
        ["decide-pending", "R-0001", "adopt"],
        ["feedback", "R-0001", "useful"],
        ["taste", "diff"],
        ["taste", "propose"],
        ["request-confirm"],
        ["confirm", "ABCDEF"],
        ["exposure", "pkg:pypi/litellm", "--versions", "1.107.0"],
        ["hourly"],
        ["app", "sync"],
        ["notify-test"],
        ["sources", "list"],
        ["search", "litellm"],
        ["usage", "record", "--run", "20261028T070000Z-daily", "--json", "u.json"],
    ],
)
def test_every_subcommand_emits_single_json_line(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """(d) Every subcommand emits exactly one valid JSON line to stdout."""
    code = cli.main(argv)
    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert len(lines) == 1, f"Expected 1 JSON line for {argv}, got: {captured.out!r}"
    payload = json.loads(lines[0])
    assert isinstance(payload.get("ok"), bool)
    assert code in (0, 1, 2, 3, 4)


def test_terminal_only_gate_non_tty_pty_and_env_override_immunity() -> None:
    """(e) decide R-0001 adopt --channel cli refuses non-TTY, allows PTY, ignores TTY env."""
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TECH_RADAR_TTY"] = "1"
    env["TECH_RADAR_FORCE_TTY"] = "1"
    env["TECH_RADAR_ALLOW_TTY"] = "true"

    cmd = [
        sys.executable,
        str(RADAR_SCRIPT),
        "decide",
        "R-0001",
        "adopt",
        "--channel",
        "cli",
    ]
    non_tty = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    assert non_tty.returncode == 4, non_tty.stdout
    non_tty_lines = non_tty.stdout.strip().splitlines()
    assert len(non_tty_lines) == 1
    non_tty_json = json.loads(non_tty_lines[0])
    assert non_tty_json["ok"] is False
    assert "decide R-0001 adopt --channel cli" in non_tty_json["error"]
    assert "decide R-0001 adopt --channel cli" in non_tty_json["command"]

    # Same command under a real PTY passes require_terminal (exits 2 validation error, not 4)
    pty_code, pty_out = _run_pty_command(cmd, env)
    assert pty_code != 4, f"Expected PTY invocation to pass require_terminal, got: {pty_out!r}"
    assert pty_code == 2
    pty_lines = [line.strip() for line in pty_out.splitlines() if line.strip()]
    assert len(pty_lines) == 1
    pty_json = json.loads(pty_lines[0])
    assert pty_json["ok"] is False
    assert any("unknown item: R-0001" in e.get("error", "") for e in pty_json.get("errors", []))

    # Under a real PTY but without --channel cli, still refused with exit 4
    cmd_no_channel = [sys.executable, str(RADAR_SCRIPT), "decide", "R-0001", "adopt"]
    no_chan_code, no_chan_out = _run_pty_command(cmd_no_channel, env)
    assert no_chan_code == 4
    no_chan_json = json.loads(no_chan_out.strip().splitlines()[-1])
    assert "decide R-0001 adopt --channel cli" in no_chan_json["command"]


@pytest.mark.parametrize(
    "argv",
    [
        ["bootstrap", "init", "--channel", "cli"],
        ["bootstrap", "apply", "--answers", "a.json", "--channel", "cli"],
        ["bootstrap", "lockdown", "--channel", "cli"],
        ["decide", "R-0001", "adopt", "--channel", "cli"],
        ["taste", "apply", "--channel", "cli"],
        ["pending", "list", "--channel", "cli"],
        ["profile", "private-scopes", "list", "--channel", "cli"],
        ["decisions", "apply", "--channel", "cli"],
        ["alert", "ack", "A-1", "--channel", "cli"],
        ["launchd", "status", "--channel", "cli"],
        ["app", "sync", "--from", "/some/dir", "--channel", "cli"],
    ],
)
def test_all_terminal_only_commands_refused_without_tty(
    argv: list[str], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every † command refuses execution with exit 4 when stdin/stdout is not a TTY."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    code = cli.main(argv)
    captured = capsys.readouterr()
    assert code == 4
    payload = json.loads(captured.out.strip())
    assert payload["ok"] is False
    assert "--channel cli" in payload["command"]


def test_test_hooks_refused_on_default_paths(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """TECH_RADAR_REPLAY_DIR and TECH_RADAR_NOTIFY_SINK are refused on default paths."""
    default_state, default_run_root = config.default_user_paths()
    monkeypatch.setenv("TECH_RADAR_STATE", str(default_state))
    monkeypatch.setenv("TECH_RADAR_REPLAY_DIR", "/some/replay/dir")
    code = cli.main(["doctor"])
    captured = capsys.readouterr()
    assert code == 4
    payload = json.loads(captured.out.strip())
    assert payload["ok"] is False

    # Also refused when state is isolated but run_root points inside default run_root
    monkeypatch.setenv("TECH_RADAR_STATE", str(default_state.parent / "safe-state"))
    monkeypatch.setenv("TECH_RADAR_RUN_ROOT", str(default_run_root / "sub"))
    monkeypatch.delenv("TECH_RADAR_REPLAY_DIR", raising=False)
    monkeypatch.setenv("TECH_RADAR_NOTIFY_SINK", "/some/sink.log")
    code2 = cli.main(["doctor"])
    captured2 = capsys.readouterr()
    assert code2 == 4
    payload2 = json.loads(captured2.out.strip())
    assert payload2["ok"] is False


def test_terminal_only_requires_both_stdin_and_stdout_tty(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """require_terminal refuses when only stdin or only stdout is a TTY, or channel != cli."""
    # Only stdin is TTY
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert cli.main(["decide", "R-0001", "adopt", "--channel", "cli"]) == 4
    capsys.readouterr()

    # Only stdout is TTY
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert cli.main(["decide", "R-0001", "adopt", "--channel", "cli"]) == 4
    capsys.readouterr()

    # Both TTY, but wrong channel value
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert cli.main(["decide", "R-0001", "adopt", "--channel", "thread"]) == 4
    out = json.loads(capsys.readouterr().out.strip())
    assert "--channel cli" in out["command"]

    # app sync without --from/--channel is allowed without TTY (used internally by plan-run),
    # whereas app sync --from /dir without TTY or --channel cli is refused with exit 4
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert cli.main(["app", "sync"]) == 1
    capsys.readouterr()
    assert cli.main(["app", "sync", "--from", "/some/dir"]) == 4
    capsys.readouterr()


def test_plugin_manifests_and_registries_synced() -> None:
    """Verify tech-radar manifests and marketplace entries match version and description."""
    root_manifest = json.loads((REPO_ROOT / "tech-radar" / "plugin.json").read_text())
    cc_manifest = json.loads(
        (REPO_ROOT / "tech-radar" / ".claude-plugin" / "plugin.json").read_text()
    )
    cc_market = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    ag_market = json.loads((REPO_ROOT / ".agents" / "plugins" / "marketplace.json").read_text())

    assert root_manifest["name"] == cc_manifest["name"] == "tech-radar"
    assert root_manifest["version"] == cc_manifest["version"] == "0.1.0"
    assert root_manifest["description"] == cc_manifest["description"]

    cc_entry = next(p for p in cc_market["plugins"] if p["name"] == "tech-radar")
    assert cc_entry["version"] == "0.1.0"
    assert cc_entry["description"] == root_manifest["description"]
    assert any(p["name"] == "tech-radar" for p in ag_market["plugins"])


def test_every_error_class_subclasses_radar_error() -> None:
    """Every error class in errors.py subclasses RadarError with expected attributes."""
    error_classes = (
        errors.EgressDenied,
        errors.FetchError,
        errors.UnsafeArg,
        errors.CommandDenied,
        errors.WriteDenied,
        errors.ValidationError,
        errors.Refused,
    )
    for cls in error_classes:
        assert issubclass(cls, errors.RadarError), f"{cls.__name__} must subclass RadarError"

    val_err = errors.ValidationError([{"field": "ring", "error": "invalid"}])
    assert isinstance(val_err, errors.RadarError)
    assert val_err.exit_code == 2
    assert val_err.errors == [{"field": "ring", "error": "invalid"}]

    refused_err = errors.Refused("denied")
    assert isinstance(refused_err, errors.RadarError)
    assert refused_err.exit_code == 4
