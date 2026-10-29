"""Tests for doctor.py, bootstrap.py, and profile private-scopes."""

from __future__ import annotations

import json
import os
import pty
import select
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from tech_radar import bootstrap, cli, context, db, doctor, fsguard, procs

REPO_ROOT = Path(__file__).resolve().parents[2]
RADAR_SCRIPT = REPO_ROOT / "tech-radar" / "skills" / "radar" / "scripts" / "radar.py"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
FIXED_NOW = "2026-10-29T07:00:00Z"


def _run_pty_subprocess(cmd: list[str], env: dict[str, str]) -> tuple[int, str]:
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
        proc.wait(timeout=20)
    finally:
        os.close(master_fd)
        if proc.stderr is not None:
            proc.stderr.close()

    return proc.returncode, b"".join(chunks).decode("utf-8", errors="replace")


def _write_claude_settings(home: Path, browser_path: str | None) -> None:
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    env_dict: dict[str, str] = {}
    if browser_path is not None:
        env_dict["BROWSER_EXECUTABLE_PATH"] = browser_path
    (claude_dir / "settings.json").write_text(
        json.dumps({"env": env_dict}, indent=2) + "\n",
        encoding="utf-8",
    )


def _byte_scan_dirs(needle: bytes, dirs: list[Path]) -> list[Path]:
    hits: list[Path] = []
    for d in dirs:
        if not d.exists():
            continue
        for fpath in d.rglob("*"):
            if fpath.is_file() and needle in fpath.read_bytes():
                hits.append(fpath)
    return hits


def test_doctor_browser_path_python3_and_settings_prefix(
    isolated_radar_env: dict[str, Path],
    replay_http: Callable[..., Any],
    replay_gh: Callable[..., Any],
    replay_procs: Callable[..., Any],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """doctor: unset BROWSER_EXECUTABLE_PATH -> 4; daily Chrome (direct and symlink) -> 4;
    Chrome for Testing -> passes; failing /usr/bin/python3 (ReplayProcs non-zero) -> 4;
    settings prefix != RADAR_CMD -> reported mismatch.
    """
    home = isolated_radar_env["home"]
    monkeypatch.setenv("TECH_RADAR_REPLAY_DIR", str(FIXTURES_DIR))

    # 1. Unset BROWSER_EXECUTABLE_PATH -> exit 4
    _write_claude_settings(home, None)
    assert cli.main(["doctor"]) == 4
    out_unset = json.loads(capsys.readouterr().out.strip())
    assert out_unset["ok"] is False
    assert out_unset["browser_ok"] is False
    assert "BROWSER_EXECUTABLE_PATH" in out_unset["error"]

    # 2. Daily Chrome direct path -> exit 4
    _write_claude_settings(
        home,
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    )
    assert cli.main(["doctor"]) == 4
    out_daily = json.loads(capsys.readouterr().out.strip())
    assert out_daily["ok"] is False
    assert out_daily["browser_ok"] is False

    # 3. Daily Chrome via symlink -> exit 4
    symlink_path = home / "chrome-symlink"
    symlink_path.symlink_to("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    _write_claude_settings(home, str(symlink_path))
    assert cli.main(["doctor"]) == 4
    out_sym = json.loads(capsys.readouterr().out.strip())
    assert out_sym["ok"] is False
    assert out_sym["browser_ok"] is False

    # 4. Chrome for Testing path -> passes (exit 0)
    cft_path = str(home / "Applications" / "ChromeForTesting" / "Google Chrome for Testing")
    _write_claude_settings(home, cft_path)
    assert cli.main(["doctor"]) == 0
    out_cft = json.loads(capsys.readouterr().out.strip())
    assert out_cft["ok"] is True
    assert out_cft["browser_ok"] is True
    assert out_cft["python3_isolated_ok"] is True

    # 5. Failing /usr/bin/python3 (ReplayProcs non-zero) -> exit 4
    fail_py_dir = home / "fail_py_procs"
    fail_py_dir.mkdir(parents=True, exist_ok=True)
    py_key = procs.procs_key(None, ["/usr/bin/python3", "-I", "-c", "pass"])
    (fail_py_dir / f"{py_key}.json").write_text(
        json.dumps(
            {
                "argv": ["python3", "-I", "-c", "pass"],
                "returncode": 1,
                "stdout": "",
                "stderr": "xcrun: error: invalid active developer path",
            }
        ),
        encoding="utf-8",
    )
    ctx_fail_py = context.build(
        transports=(
            replay_http(),
            replay_gh(),
            replay_procs(extra_roots=[fail_py_dir]),
        ),
        now=FIXED_NOW,
    )
    doc_fail_py = doctor.run_doctor(ctx_fail_py)
    assert doc_fail_py["ok"] is False
    assert doc_fail_py["python3_isolated_ok"] is False
    assert "/usr/bin/python3" in doc_fail_py["error"]

    # 6. Settings prefix != RADAR_CMD -> reported mismatch
    ctx_ok = context.build(
        transports=(replay_http(), replay_gh(), replay_procs()),
        now=FIXED_NOW,
    )
    fsguard.safe_write(
        ctx_ok.paths.radar_cmd,
        "/opt/homebrew/bin/uv run --script --locked --isolated /expected/app/radar.py\n",
        mode=0o600,
        root=ctx_ok.paths.run_root,
    )
    bad_settings = {
        "permissions": {
            "defaultMode": "dontAsk",
            "allow": [
                "Bash(/wrong/uv run --script --locked --isolated /wrong/app/radar.py plan-run *)"
            ],
            "deny": [],
        }
    }
    fsguard.safe_write(
        ctx_ok.paths.run_root / ".claude" / "settings.json",
        json.dumps(bad_settings, indent=2) + "\n",
        mode=0o600,
        root=ctx_ok.paths.run_root,
    )
    doc_mismatch = doctor.run_doctor(ctx_ok)
    assert doc_mismatch["settings_present"] is True
    assert doc_mismatch["settings_prefix_match"] is False
    assert doc_mismatch["settings_prefix_mismatch"] is True
    assert "mismatch" in str(doc_mismatch.get("warning") or "").lower()
    assert isinstance(doc_mismatch["tools"], dict)
    assert isinstance(doc_mismatch["credentials"], dict)
    assert isinstance(doc_mismatch["launchd"], dict)
    assert isinstance(doc_mismatch["sync_writable"], bool)
    assert isinstance(doc_mismatch["schema_version"], int | float)

    # 7. Settings deny rule prefix mismatch and missing RADAR_CMD with settings.json present
    expected_allow = (
        "Bash(/opt/homebrew/bin/uv run --script --locked --isolated "
        "/expected/app/radar.py plan-run *)"
    )
    bad_deny_settings = {
        "permissions": {
            "defaultMode": "dontAsk",
            "allow": [expected_allow],
            "deny": [
                "Bash(/wrong/uv run --script --locked --isolated /wrong/app/radar.py alert *)"
            ],
        }
    }
    fsguard.safe_write(
        ctx_ok.paths.run_root / ".claude" / "settings.json",
        json.dumps(bad_deny_settings, indent=2) + "\n",
        mode=0o600,
        root=ctx_ok.paths.run_root,
    )
    present_d, match_d, reason_d = doctor.check_settings_prefix(ctx_ok)
    assert present_d is True and match_d is False
    assert "deny rule prefix" in str(reason_d)

    ctx_ok.paths.radar_cmd.unlink()
    present_no_cmd, match_no_cmd, reason_no_cmd = doctor.check_settings_prefix(ctx_ok)
    assert present_no_cmd is True and match_no_cmd is False
    assert "RADAR_CMD is missing" in str(reason_no_cmd)

    # 8. Schema version exceeding supported SCHEMA_VERSION -> refusal (ok: False)
    ctx_ok.db.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 5}")
    doc_schema_bad = doctor.run_doctor(ctx_ok)
    assert doc_schema_bad["ok"] is False
    assert any("schema version" in r for r in doc_schema_bad["refusals"])
    ctx_ok.db.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION}")


def test_bootstrap_init_apply_private_scopes_and_lockdown(
    fixture_home: Any,
    isolated_radar_env: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """bootstrap:
    - every bootstrap subcommand without TTY -> exit 4;
    - under a real PTY: init writes templates with expected headings & pre-filled categories/
      anti-interests, 1-line absolute RADAR_CMD with --isolated and {state}/app/radar.py,
      and interview-context.json with no private names;
    - apply rejects malformed JSON, private_scopes key, or unknown initial_rings entity (exit 2),
      and applies valid answers (sections populated, rings set with channel='bootstrap', answers
      file deleted);
    - profile private-scopes add/list/remove works under PTY (exit 4 without TTY) and leaves zero
      occurrences in sync dir or run_root;
    - lockdown writes settings iff ENFORCEMENT_HONOURED (tested both False and True) and expanded
      settings match Spec 12 A25 exactly.
    """
    _ = fixture_home
    state_dir = isolated_radar_env["state"]
    run_root = isolated_radar_env["run_root"]
    sync_dir = isolated_radar_env["sync"]
    monkeypatch.setenv("TECH_RADAR_REPLAY_DIR", str(FIXTURES_DIR))

    # 1. Every bootstrap subcommand and profile private-scopes without TTY -> exit 4
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    for argv in (
        ["bootstrap", "init", "--channel", "cli"],
        ["bootstrap", "apply", "--answers", str(run_root / "a.json"), "--channel", "cli"],
        ["bootstrap", "lockdown", "--channel", "cli"],
        ["profile", "private-scopes", "add", "example-org", "--channel", "cli"],
        ["profile", "private-scopes", "list", "--channel", "cli"],
        ["profile", "private-scopes", "remove", "example-org", "--channel", "cli"],
    ):
        assert cli.main(argv) == 4
        capsys.readouterr()

    # Unit-level edge cases for bootstrap.validate_answers
    assert bootstrap.validate_answers(["not", "a", "mapping"])[0]["field"] == "$"
    errs_extra = bootstrap.validate_answers(
        {
            "languages": ["python"],
            "categories": ["Python tooling"],
            "anti_interests": ["local inference"],
            "preferences": ["MIT"],
            "taste_makers": ["simonw"],
            "watchlist": ["astral-sh/ruff"],
            "initial_rings": [
                {"entity_id": "brew:formula/jq", "ring": "bogus_ring", "reason": 123}
            ],
            "subscriptions": ["claude-max"],
            "private-scopes": ["corp"],
            "unknown_extra": True,
        }
    )
    err_fields = {e["field"] for e in errs_extra}
    assert "private-scopes" in err_fields
    assert "unknown_extra" in err_fields
    assert "initial_rings[0].ring" in err_fields
    assert "initial_rings[0].reason" in err_fields

    # Build subprocess environment for real PTY runs
    sub_env = os.environ.copy()
    sub_env["PYTHONDONTWRITEBYTECODE"] = "1"
    sub_env["TECH_RADAR_REPLAY_DIR"] = str(FIXTURES_DIR)

    # 2. bootstrap init under a real PTY
    rc_init, out_init = _run_pty_subprocess(
        [sys.executable, str(RADAR_SCRIPT), "bootstrap", "init", "--channel", "cli"],
        sub_env,
    )
    assert rc_init == 0, out_init
    init_json = json.loads([ln.strip() for ln in out_init.splitlines() if ln.strip()][-1])
    assert init_json["ok"] is True
    assert init_json["action"] == "init"

    taste_text = (sync_dir / "TASTE_PROFILE.md").read_text(encoding="utf-8")
    watchlist_text = (sync_dir / "WATCHLIST.md").read_text(encoding="utf-8")
    for heading in (
        "## Categories",
        "## Anti-interests",
        "## Preferences",
        "## Taste-makers",
        "## Languages",
        "## Subscriptions",
        "## Watchlist",
    ):
        assert heading in taste_text, f"Missing {heading} in TASTE_PROFILE.md"
    assert "## Watchlist" in watchlist_text
    for cat in bootstrap.DEFAULT_CATEGORIES:
        assert f"- {cat}" in taste_text
    for anti in bootstrap.DEFAULT_ANTI_INTERESTS:
        assert f"- {anti}" in taste_text

    radar_cmd_lines = (run_root / "RADAR_CMD").read_text(encoding="utf-8").splitlines()
    assert len(radar_cmd_lines) == 1
    radar_cmd_val = radar_cmd_lines[0]
    assert radar_cmd_val.startswith("/")
    assert "--isolated" in radar_cmd_val
    assert f"{os.path.abspath(state_dir)}/app/radar.py" in radar_cmd_val

    interview_ctx_path = run_root / "runs" / "bootstrap" / "interview-context.json"
    assert interview_ctx_path.is_file()
    interview_raw = interview_ctx_path.read_text(encoding="utf-8")
    assert "secret-widget" not in interview_raw
    assert "example-org/secret-widget" not in interview_raw
    assert "private-project-1" in interview_raw
    interview_obj = json.loads(interview_raw)
    assert isinstance(interview_obj["detected_languages"], list)
    assert isinstance(interview_obj["top_in_use_public"], list)
    assert isinstance(interview_obj["in_use"]["private_counts"], dict)

    # Spec 11 §9: bootstrap init's inventory-only collect must NOT set kv['baseline_done']
    conn_init = db.connect(state_dir / "radar.db")
    try:
        assert db.kv_get(conn_init, "baseline_done") is None
    finally:
        conn_init.close()

    # 3. bootstrap apply validation failures (malformed, private_scopes, unknown entity) -> exit 2
    bad_answers_path = run_root / "runs" / "bootstrap" / "bad_answers.json"
    bad_answers_path.write_text("{not-valid-json", encoding="utf-8")
    rc_mal, out_mal = _run_pty_subprocess(
        [
            sys.executable,
            str(RADAR_SCRIPT),
            "bootstrap",
            "apply",
            "--answers",
            str(bad_answers_path),
            "--channel",
            "cli",
        ],
        sub_env,
    )
    assert rc_mal == 2, out_mal

    priv_scope_answers = {
        "languages": ["python"],
        "categories": ["Python tooling"],
        "anti_interests": ["local inference"],
        "preferences": ["MIT license"],
        "taste_makers": ["simonw"],
        "watchlist": ["astral-sh/ruff"],
        "initial_rings": [],
        "subscriptions": ["claude-max"],
        "private_scopes": ["secret-corp"],
    }
    bad_answers_path.write_text(json.dumps(priv_scope_answers), encoding="utf-8")
    rc_priv, out_priv = _run_pty_subprocess(
        [
            sys.executable,
            str(RADAR_SCRIPT),
            "bootstrap",
            "apply",
            "--answers",
            str(bad_answers_path),
            "--channel",
            "cli",
        ],
        sub_env,
    )
    assert rc_priv == 2, out_priv
    priv_err_json = json.loads([ln.strip() for ln in out_priv.splitlines() if ln.strip()][-1])
    assert any(e["field"] == "private_scopes" for e in priv_err_json["errors"])

    unknown_ring_answers = {
        "languages": ["python"],
        "categories": ["Python tooling"],
        "anti_interests": ["local inference"],
        "preferences": ["MIT license"],
        "taste_makers": ["simonw"],
        "watchlist": ["astral-sh/ruff"],
        "initial_rings": [
            {"entity_id": "brew:formula/does-not-exist", "ring": "hold", "reason": "No item"}
        ],
        "subscriptions": ["claude-max"],
    }
    bad_answers_path.write_text(json.dumps(unknown_ring_answers), encoding="utf-8")
    rc_unk, out_unk = _run_pty_subprocess(
        [
            sys.executable,
            str(RADAR_SCRIPT),
            "bootstrap",
            "apply",
            "--answers",
            str(bad_answers_path),
            "--channel",
            "cli",
        ],
        sub_env,
    )
    assert rc_unk == 2, out_unk
    bad_answers_path.unlink()

    # 4. Valid bootstrap apply under PTY -> populates profile, records decision, deletes file
    valid_answers_path = run_root / "runs" / "bootstrap" / "answers.json"
    valid_answers = {
        "languages": ["python", "typescript"],
        "categories": ["Python tooling", "MCP servers"],
        "anti_interests": ["local inference", "Rust tooling"],
        "preferences": ["MIT or Apache-2.0", "single-binary CLI"],
        "taste_makers": ["simonw"],
        "watchlist": ["rtk-ai/rtk"],
        "initial_rings": [
            {"entity_id": "brew:formula/jq", "ring": "hold", "reason": "Moving to typed CLI"}
        ],
        "subscriptions": ["claude-max"],
    }
    valid_answers_path.write_text(json.dumps(valid_answers, indent=2), encoding="utf-8")
    rc_app, out_app = _run_pty_subprocess(
        [
            sys.executable,
            str(RADAR_SCRIPT),
            "bootstrap",
            "apply",
            "--answers",
            str(valid_answers_path),
            "--channel",
            "cli",
        ],
        sub_env,
    )
    assert rc_app == 0, out_app
    assert not valid_answers_path.exists(), "answers.json must be deleted after bootstrap apply"

    conn = db.connect(state_dir / "radar.db")
    try:
        dec_row = conn.execute(
            "SELECT d.verb, d.channel, d.actor, i.ring "
            "FROM decisions d JOIN items i ON i.item_id = d.item_id "
            "WHERE i.entity_id = 'brew:formula/jq' AND d.superseded_by IS NULL"
        ).fetchone()
        assert dec_row is not None
        assert dec_row["verb"] == "hold"
        assert dec_row["channel"] == "bootstrap"
        assert dec_row["actor"] == "user"
        assert dec_row["ring"] == "hold"
    finally:
        conn.close()

    # 5. profile private-scopes add/list/remove under PTY -> stored only in DB kv
    rc_ps_add, out_ps_add = _run_pty_subprocess(
        [
            sys.executable,
            str(RADAR_SCRIPT),
            "profile",
            "private-scopes",
            "add",
            "private-acme-org",
            "--channel",
            "cli",
        ],
        sub_env,
    )
    assert rc_ps_add == 0, out_ps_add
    assert "private-acme-org" in json.loads(out_ps_add.strip().splitlines()[-1])["scopes"]

    rc_ps_list, out_ps_list = _run_pty_subprocess(
        [
            sys.executable,
            str(RADAR_SCRIPT),
            "profile",
            "private-scopes",
            "list",
            "--channel",
            "cli",
        ],
        sub_env,
    )
    assert rc_ps_list == 0, out_ps_list
    assert "private-acme-org" in json.loads(out_ps_list.strip().splitlines()[-1])["scopes"]

    # Byte scan of sync_dir and run_root finds no private-acme-org
    assert _byte_scan_dirs(b"private-acme-org", [sync_dir, run_root]) == []

    rc_ps_rem, out_ps_rem = _run_pty_subprocess(
        [
            sys.executable,
            str(RADAR_SCRIPT),
            "profile",
            "private-scopes",
            "remove",
            "private-acme-org",
            "--channel",
            "cli",
        ],
        sub_env,
    )
    assert rc_ps_rem == 0, out_ps_rem
    assert "private-acme-org" not in json.loads(out_ps_rem.strip().splitlines()[-1])["scopes"]

    # 6. bootstrap lockdown: default ENFORCEMENT_HONOURED == False -> prints not honoured, no file
    assert bootstrap.ENFORCEMENT_HONOURED is False
    rc_lock, out_lock = _run_pty_subprocess(
        [sys.executable, str(RADAR_SCRIPT), "bootstrap", "lockdown", "--channel", "cli"],
        sub_env,
    )
    assert rc_lock == 0, out_lock
    assert "enforcement: not honoured" in out_lock
    settings_file = run_root / ".claude" / "settings.json"
    assert not settings_file.exists()

    # 7. bootstrap lockdown with ENFORCEMENT_HONOURED = True (monkeypatched) -> writes settings.json
    monkeypatch.setattr(bootstrap, "ENFORCEMENT_HONOURED", True)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert cli.main(["bootstrap", "lockdown", "--channel", "cli"]) == 0
    lock_json = json.loads(capsys.readouterr().out.strip())
    assert lock_json["settings_written"] is True
    assert settings_file.is_file()

    written_settings = json.loads(settings_file.read_text(encoding="utf-8"))
    perms = written_settings["permissions"]
    assert perms["defaultMode"] == "dontAsk"
    allow_rules: list[str] = perms["allow"]
    deny_rules: list[str] = perms["deny"]

    assert "Edit" not in allow_rules
    assert "Skill(tech-radar:radar)" in allow_rules
    assert "ToolSearch" in allow_rules

    # The 7 bootstrap-only T3 MCP tools must NOT be in run-project-settings.json allow
    for t3_boot_tool in (
        "mcp__t3-code__t3_project_create",
        "mcp__t3-code__t3_project_update",
        "mcp__t3-code__t3_thread_launch",
        "mcp__t3-code__t3_thread_wait",
        "mcp__t3-code__t3_thread_read",
        "mcp__t3-code__schedule_task",
        "mcp__t3-code__run_scheduled_task_now",
    ):
        assert t3_boot_tool not in allow_rules

    # Required deny entries per Spec 12 A25
    for req_deny in (
        "Edit",
        "Grep",
        "Glob",
        "Agent",
        "Task",
        "Bash(grep *)",
        "Bash(rg *)",
        f"Read({state_dir}/**)",
        f"Write({state_dir}/**)",
    ):
        assert req_deny in deny_rules

    for sync_protected in (
        "INVENTORY.md",
        "DECISIONS.md",
        "radar.snapshot.db",
        "project-alerts.json",
        "radar.byor.json",
        "radar.html",
    ):
        assert f"Read({sync_dir}/{sync_protected})" in deny_rules
        assert f"Write({sync_dir}/{sync_protected})" in deny_rules

    # Every Bash allow rule and RADAR_CMD-scoped deny rule is prefixed by radar_cmd_val
    bash_allows = [r for r in allow_rules if r.startswith("Bash(")]
    assert len(bash_allows) == 14
    for r in bash_allows:
        assert r.startswith(f"Bash({radar_cmd_val} ")

    radar_denies = [r for r in deny_rules if r.startswith("Bash(") and "radar.py" in r]
    assert len(radar_denies) == 9
    for r in radar_denies:
        assert r.startswith(f"Bash({radar_cmd_val} ")
