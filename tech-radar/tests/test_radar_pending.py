"""Tests for pending.py: confirmation codes, batching, truncation, aliasing, and taste locking."""

from __future__ import annotations

import json
import os
import pty
import select
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tech_radar import cli, context, errors, items, pending, runlog

REPO_ROOT = Path(__file__).resolve().parents[2]
RADAR_SCRIPT = REPO_ROOT / "tech-radar" / "skills" / "radar" / "scripts" / "radar.py"
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
        proc.wait(timeout=15)
    finally:
        os.close(master_fd)
        if proc.stderr is not None:
            proc.stderr.close()

    return proc.returncode, b"".join(chunks).decode("utf-8", errors="replace")


def _extract_code_from_sink(sink_file: Path) -> str:
    line = [ln for ln in sink_file.read_text(encoding="utf-8").splitlines() if ln.strip()][-1]
    return line.split("tech-radar code ", 1)[1].split(":", 1)[0].strip()


def _byte_scan_dirs(needle: bytes, dirs: list[Path]) -> list[Path]:
    hits: list[Path] = []
    for d in dirs:
        if not d.exists():
            continue
        for fpath in d.rglob("*"):
            if fpath.is_file():
                data = fpath.read_bytes()
                if needle in data:
                    hits.append(fpath)
    return hits


def test_decide_pending_request_confirm_truncation_aliasing_and_pty_pending_list(
    isolated_radar_env: dict[str, Path],
    replay_http: Callable[..., Any],
    replay_gh: Callable[..., Any],
    replay_procs: Callable[..., Any],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """decide-pending + request-confirm (with and without --run), 12-entry <=120-char truncation,
    private item alias in notification and stdout, pending list TTY gate, and zero code leaks.
    """
    sink_file = isolated_radar_env["state"].parent / "notify_sink.txt"
    monkeypatch.setenv(
        "TECH_RADAR_REPLAY_DIR",
        str(REPO_ROOT / "tech-radar" / "tests" / "fixtures"),
    )
    monkeypatch.setenv("TECH_RADAR_NOTIFY_SINK", str(sink_file))

    ctx = context.build(
        transports=(replay_http(), replay_gh(), replay_procs()),
        now=FIXED_NOW,
    )
    run_id = runlog.start_run(ctx, mode="daily", requested_mode="daily")

    # Seed 2 public items + 1 private item linked to private-project-1
    item_ids: list[str] = []
    for idx in range(1, 3):
        eid = f"pkg:pypi/pub-pkg-{idx}"
        items.ensure_entity(ctx, eid, classify_visibility=False)
        ctx.db.execute("UPDATE entities SET visibility = 'public' WHERE entity_id = ?", (eid,))
        iid, _ = items.ensure_item(ctx, eid)
        item_ids.append(iid)

    priv_eid = "pkg:pypi/secret-widget-core"
    items.ensure_entity(ctx, priv_eid, classify_visibility=False)
    ctx.db.execute(
        "UPDATE entities SET visibility = 'private', name = 'secret-widget-core' "
        "WHERE entity_id = ?",
        (priv_eid,),
    )
    ctx.db.execute(
        "INSERT INTO projects(project_key, display_path, main_worktree, active, excluded, "
        "root_source, visibility, alias, manifest_files_json) "
        "VALUES ('https://github.com/example-org/secret-widget', '/p/secret-widget', "
        "'/p/secret-widget', 1, 0, 'active_projects', 'private', 'private-project-1', '[]')"
    )
    ctx.db.execute(
        "INSERT INTO usages(entity_id, context, version, direct, first_seen, last_seen) "
        "VALUES (?, 'project:https://github.com/example-org/secret-widget', '0.2.0', 1, ?, ?)",
        (priv_eid, FIXED_NOW, FIXED_NOW),
    )
    ctx.db.commit()
    priv_item_id, _ = items.ensure_item(ctx, priv_eid)

    # Stage 2 public decisions + 1 private decision via CLI
    assert cli.main(["decide-pending", item_ids[0], "trial", "--reason", "Try it"]) == 0
    capsys.readouterr()
    assert cli.main(["decide-pending", item_ids[1], "hold", "--reason", "Extra agent entry"]) == 0
    capsys.readouterr()
    assert cli.main(["decide-pending", priv_item_id, "reject", "--reason", "Private pkg"]) == 0
    out_priv_stage = capsys.readouterr().out.strip()
    assert "secret-widget" not in out_priv_stage
    assert "private-project-1" in out_priv_stage

    # request-confirm without --run
    assert cli.main(["request-confirm"]) == 0
    req_out_1 = capsys.readouterr().out.strip()
    req_json_1 = json.loads(req_out_1)
    assert req_json_1["ok"] is True
    assert req_json_1["pending"] == 3
    assert "secret-widget" not in req_out_1
    assert "private-project-1" in req_out_1

    sink_msg_1 = sink_file.read_text(encoding="utf-8").strip().splitlines()[-1]
    code_1 = _extract_code_from_sink(sink_file)
    assert len(code_1) == 6
    assert code_1 not in req_out_1
    assert f"{item_ids[0]} trial" in sink_msg_1
    assert f"{item_ids[1]} hold" in sink_msg_1
    assert "private-project-1" in sink_msg_1
    assert "secret-widget" not in sink_msg_1

    # Byte scan of state, run_root, and logs finds zero occurrences of code_1
    hits = _byte_scan_dirs(
        code_1.encode("utf-8"),
        [
            isolated_radar_env["state"],
            isolated_radar_env["run_root"],
            isolated_radar_env["logs"],
        ],
    )
    assert hits == []

    # request-confirm WITH --run also works
    assert cli.main(["request-confirm", "--run", run_id]) == 0
    req_out_2 = capsys.readouterr().out.strip()
    code_2 = _extract_code_from_sink(sink_file)
    assert code_2 not in req_out_2

    # pending list without TTY -> exit 4
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert cli.main(["pending", "list", "--channel", "cli"]) == 4
    capsys.readouterr()

    # pending list under a real PTY prints every staged entry with verb and reason
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    rc_pty, pty_out = _run_pty_subprocess(
        [sys.executable, str(RADAR_SCRIPT), "pending", "list", "--channel", "cli"],
        env,
    )
    assert rc_pty == 0, pty_out
    pty_json = json.loads([ln.strip() for ln in pty_out.splitlines() if ln.strip()][-1])
    assert pty_json["count"] == 3
    verbs = [e.get("verb") for e in pty_json["items"]]
    reasons = [e.get("reason") for e in pty_json["items"]]
    assert verbs == ["trial", "hold", "reject"]
    assert "Try it" in reasons

    # Stage 9 more items (total 12 staged entries) -> notification <=120 chars ending with +N more
    for idx in range(3, 12):
        eid = f"pkg:pypi/pub-pkg-{idx}"
        items.ensure_entity(ctx, eid, classify_visibility=False)
        ctx.db.execute("UPDATE entities SET visibility = 'public' WHERE entity_id = ?", (eid,))
        iid, _ = items.ensure_item(ctx, eid)
        pending.stage_decision(ctx, iid, "assess", reason=f"Reason {idx}")

    pending.request_confirm(ctx)
    sink_msg_12 = sink_file.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert len(sink_msg_12) <= 120
    assert sink_msg_12.endswith("more — run radar.py pending list in your terminal")


def test_confirm_wrong_code_5_attempts_expiry_feedback_and_taste_sha256_lock(
    isolated_radar_env: dict[str, Path],
    replay_http: Callable[..., Any],
    replay_gh: Callable[..., Any],
    replay_procs: Callable[..., Any],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrong code -> exit 4 + attempts=1; 5 wrong -> batch cancelled; expired -> exit 4;
    feedback staged+confirmed -> digest_items.feedback; taste propose freezes original content
    and refuses tampered payload sha256; taste apply without proposed file -> exit 4.
    """
    sink_file = isolated_radar_env["state"].parent / "notify_sink2.txt"
    monkeypatch.setenv(
        "TECH_RADAR_REPLAY_DIR",
        str(REPO_ROOT / "tech-radar" / "tests" / "fixtures"),
    )
    monkeypatch.setenv("TECH_RADAR_NOTIFY_SINK", str(sink_file))

    ctx = context.build(
        transports=(replay_http(), replay_gh(), replay_procs()),
        now=FIXED_NOW,
    )
    run_id = runlog.start_run(ctx, mode="daily", requested_mode="daily")
    eid = "pkg:pypi/confirm-pkg"
    items.ensure_entity(ctx, eid, classify_visibility=False)
    ctx.db.execute("UPDATE entities SET visibility = 'public' WHERE entity_id = ?", (eid,))
    item_id, _ = items.ensure_item(ctx, eid)

    # 1. Wrong code -> exit 4 and attempts=1, then 4 more wrong codes -> cancelled (5 total)
    pending.stage_decision(ctx, item_id, "trial")
    pending.request_confirm(ctx)

    assert cli.main(["confirm", "ZZZZZZ"]) == 4
    capsys.readouterr()
    b_row = ctx.db.execute(
        "SELECT attempts, status FROM confirm_batches ORDER BY batch_id DESC LIMIT 1"
    ).fetchone()
    assert int(b_row["attempts"]) == 1
    assert b_row["status"] == "open"

    for _ in range(4):
        assert cli.main(["confirm", "ZZZZZZ"]) == 4
        capsys.readouterr()

    b_cancelled = ctx.db.execute(
        "SELECT attempts, status FROM confirm_batches ORDER BY batch_id DESC LIMIT 1"
    ).fetchone()
    assert int(b_cancelled["attempts"]) == 5
    assert b_cancelled["status"] == "cancelled"

    # 2. Expired batch -> exit 4
    pending.stage_decision(ctx, item_id, "trial")
    pending.request_confirm(ctx)
    exp_code = _extract_code_from_sink(sink_file)
    ctx.now = datetime(2026, 10, 31, 8, 0, tzinfo=UTC)  # >24h later
    with pytest.raises(errors.Refused):
        pending.confirm(ctx, exp_code)

    # 3. Right code + feedback + taste propose (original content written even if file edited later)
    ctx.now = datetime(2026, 10, 29, 7, 0, tzinfo=UTC)
    ctx.db.execute(
        "INSERT INTO digest_items(run_id, ref, section, rank, feedback) "
        "VALUES (?, ?, 'horizon', 1, NULL)",
        (run_id, item_id),
    )
    ctx.db.commit()

    proposed_path = ctx.paths.sync / "TASTE_PROFILE.proposed.md"
    original_proposed = "# Taste Profile\n\n## Categories\n- original-staged-category\n"
    proposed_path.write_text(original_proposed, encoding="utf-8")

    # Verify taste_diff returns unified diff and sha256
    tdiff = pending.taste_diff(ctx)
    assert tdiff["ok"] is True
    assert tdiff["has_changes"] is True
    assert "+- original-staged-category" in tdiff["diff"]

    pending.stage_decision(ctx, item_id, "adopt", reason="Approved")
    pending.stage_feedback(ctx, item_id, "acted")
    pending.stage_taste(ctx)

    # Verify list_pending_cli includes decision, feedback, and taste diff
    listed = pending.list_pending_cli(ctx)
    assert listed["count"] == 3
    taste_entries = [e for e in listed["items"] if e["kind"] == "taste"]
    assert len(taste_entries) == 1
    assert "+- original-staged-category" in taste_entries[0]["diff"]

    # Edit TASTE_PROFILE.proposed.md AFTER `taste propose` -> must be ignored on confirm!
    proposed_path.write_text(
        "# Taste Profile\n\n## Categories\n- tampered-after-propose\n",
        encoding="utf-8",
    )

    pending.request_confirm(ctx)
    good_code = _extract_code_from_sink(sink_file)
    res_ok = pending.confirm(ctx, good_code)
    assert res_ok["ok"] is True
    assert res_ok["confirmed"] == 3

    # Verify decision applied with actor='user', channel='thread'
    d_row = ctx.db.execute(
        "SELECT verb, actor, channel FROM decisions WHERE item_id = ? AND superseded_by IS NULL",
        (item_id,),
    ).fetchone()
    assert d_row["verb"] == "adopt"
    assert d_row["actor"] == "user"
    assert d_row["channel"] == "thread"

    # Verify feedback written to digest_items.feedback
    fb_row = ctx.db.execute(
        "SELECT feedback FROM digest_items WHERE run_id = ? AND ref = ?",
        (run_id, item_id),
    ).fetchone()
    assert fb_row["feedback"] == "acted"

    # Verify ORIGINAL staged taste content was written to TASTE_PROFILE.md
    written_taste = (ctx.paths.sync / "TASTE_PROFILE.md").read_text(encoding="utf-8")
    assert "original-staged-category" in written_taste
    assert "tampered-after-propose" not in written_taste

    # 4. Tampered taste sha256 in DB -> confirm exits 4 and leaves batch open/batched
    proposed_path.write_text("# Taste Profile\n\n## Categories\n- legit\n", encoding="utf-8")
    pending.stage_taste(ctx)
    pending.request_confirm(ctx)
    tamper_code = _extract_code_from_sink(sink_file)

    ctx.db.execute(
        "UPDATE pending SET payload_json = ? WHERE kind = 'taste' AND status = 'batched'",
        (json.dumps({"content": "# Tampered in DB\n", "sha256": "0" * 64}),),
    )
    ctx.db.commit()

    assert cli.main(["confirm", tamper_code]) == 4
    capsys.readouterr()
    b_still_open = ctx.db.execute(
        "SELECT status FROM confirm_batches ORDER BY batch_id DESC LIMIT 1"
    ).fetchone()
    assert b_still_open["status"] == "open"

    # 5. taste apply without TTY -> exit 4; under PTY without proposed file -> exit 4
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert cli.main(["taste", "apply", "--channel", "cli"]) == 4
    capsys.readouterr()

    if proposed_path.exists():
        proposed_path.unlink()
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    rc_no_prop, _out = _run_pty_subprocess(
        [sys.executable, str(RADAR_SCRIPT), "taste", "apply", "--channel", "cli"],
        env,
    )
    assert rc_no_prop == 4

    # Under PTY with proposed file -> applies and deletes proposed file
    proposed_path.write_text(
        "# Taste Profile\n\n## Categories\n- applied-via-pty\n",
        encoding="utf-8",
    )
    rc_apply_prop, apply_out = _run_pty_subprocess(
        [sys.executable, str(RADAR_SCRIPT), "taste", "apply", "--channel", "cli"],
        env,
    )
    assert rc_apply_prop == 0, apply_out
    assert not proposed_path.exists()
    assert "applied-via-pty" in (ctx.paths.sync / "TASTE_PROFILE.md").read_text(encoding="utf-8")
