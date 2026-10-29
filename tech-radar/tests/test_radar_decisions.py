"""Tests for decisions.py, DECISIONS.md staging/apply, wake triggers, and alerts.py."""

from __future__ import annotations

import json
import os
import pty
import select
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tech_radar import alerts, cli, context, decisions, items, pending, security

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


def _seed_item(
    ctx: context.Ctx,
    entity_id: str = "pkg:pypi/fasttool",
    *,
    vis: str = "public",
) -> str:
    items.ensure_entity(ctx, entity_id, classify_visibility=False)
    ctx.db.execute("UPDATE entities SET visibility = ? WHERE entity_id = ?", (vis, entity_id))
    item_id, _ = items.ensure_item(ctx, entity_id, in_use=False, state="candidate")
    return item_id


def test_decision_supersession_expiry_and_wake_triggers(
    replay_http: Callable[..., Any],
    replay_gh: Callable[..., Any],
    replay_procs: Callable[..., Any],
) -> None:
    """reject 90d via confirm sets expires_at; expiry and wake triggers mark item due."""
    ctx = context.build(
        transports=(replay_http(), replay_gh(), replay_procs()),
        now=FIXED_NOW,
    )
    item_id = _seed_item(ctx, "pkg:pypi/fasttool")

    # First decision: assess
    res1 = decisions.apply(ctx, item_id, "assess", reason="Initial check", channel="cli")
    assert res1["actor"] == "user"

    # Second decision: reject 90d via confirm -> obsoletes first decision and sets expires_at
    pending.stage_decision(ctx, item_id, "reject", ttl="90d", reason="Not needed now")
    sink_file = ctx.paths.state.parent / "notify_sink.log"
    os.environ["TECH_RADAR_REPLAY_DIR"] = str(REPO_ROOT / "tech-radar" / "tests" / "fixtures")
    os.environ["TECH_RADAR_NOTIFY_SINK"] = str(sink_file)
    pending.request_confirm(ctx)
    sink_line = sink_file.read_text(encoding="utf-8").strip().splitlines()[-1]
    code = sink_line.split("tech-radar code ", 1)[1].split(":", 1)[0].strip()
    pending.confirm(ctx, code)

    dec_rows = ctx.db.execute(
        "SELECT decision_id, verb, actor, channel, expires_at, superseded_by "
        "FROM decisions WHERE item_id = ? ORDER BY decision_id ASC",
        (item_id,),
    ).fetchall()
    assert len(dec_rows) == 2
    assert dec_rows[0]["superseded_by"] == dec_rows[1]["decision_id"]
    assert dec_rows[1]["verb"] == "reject"
    assert dec_rows[1]["actor"] == "user"
    assert dec_rows[1]["channel"] == "thread"
    assert dec_rows[1]["expires_at"] == "2027-01-27T07:00:00Z"

    # Not due yet at FIXED_NOW
    assert decisions.evaluate_due(ctx) == []

    # Advance clock past 90d -> item is due due to expiry
    ctx.now = datetime(2027, 2, 1, 7, 0, tzinfo=UTC)
    due = decisions.evaluate_due(ctx)
    assert len(due) == 1
    assert due[0]["item_id"] == item_id
    assert due[0]["due_reason"] == "expired"

    # Wake trigger test: stars_gte, major_release, and feature substring
    ctx.now = datetime(2026, 10, 29, 7, 0, tzinfo=UTC)
    item2 = _seed_item(ctx, "gh:example-org/waketool")
    decisions.apply(
        ctx,
        item2,
        "snooze",
        ttl="90d",
        wake={"stars_gte": 500, "major_release": True, "feature": "wasm support"},
        channel="cli",
    )
    assert decisions.evaluate_due(ctx) == []

    ctx.db.execute(
        "INSERT INTO metrics(entity_id, metric, value, observed_at) "
        "VALUES ('gh:example-org/waketool', 'stars', 600.0, ?)",
        (ctx.now_iso,),
    )
    ctx.db.commit()
    due_stars = decisions.evaluate_due(ctx)
    assert any(d["item_id"] == item2 and "stars_gte:500" in d["due_reason"] for d in due_stars)

    # Feature wake trigger and major_release wake trigger
    item3 = _seed_item(ctx, "gh:example-org/featwake")
    decisions.apply(
        ctx,
        item3,
        "snooze",
        ttl="90d",
        wake={"feature": "wasm support"},
        channel="cli",
    )
    ctx.db.execute(
        "INSERT INTO signals(entity_id, source_id, type, confidence, title, untrusted_excerpt, "
        "fetched_at, dedupe_key) VALUES ('gh:example-org/featwake', 'gh_releases', 'feature', "
        "'confirmed', 'v1.4.0', 'Added full WASM support for plugins', ?, 'gh_rel|featwake|1.4')",
        (ctx.now_iso,),
    )
    item4 = _seed_item(ctx, "gh:example-org/majorwake")
    decisions.apply(
        ctx,
        item4,
        "snooze",
        ttl="90d",
        wake={"major_release": True},
        channel="cli",
    )
    ctx.db.execute(
        "INSERT INTO signals(entity_id, source_id, type, confidence, title, payload_json, "
        "fetched_at, dedupe_key) VALUES ('gh:example-org/majorwake', 'gh_releases', 'feature', "
        "'confirmed', 'Release v2.0.0', '{\"tag_name\":\"v2.0.0\"}', ?, 'gh_rel|majorwake|2.0')",
        (ctx.now_iso,),
    )
    ctx.db.commit()
    due_all = decisions.evaluate_due(ctx)
    assert any(d["item_id"] == item3 and d["due_reason"] == "feature:wasm support" for d in due_all)
    assert any(d["item_id"] == item4 and d["due_reason"] == "major_release" for d in due_all)


def test_decisions_md_staging_confirm_malformed_and_pty_apply(
    isolated_radar_env: dict[str, Path],
    replay_http: Callable[..., Any],
    replay_gh: Callable[..., Any],
    replay_procs: Callable[..., Any],
) -> None:
    """DECISIONS.md `snooze 30d` edit is staged by ingest, applied after confirm,
    malformed cells are reported without guessing, and `decisions apply --channel cli`
    under a real PTY applies staged edits without a code.
    """
    ctx = context.build(
        transports=(replay_http(), replay_gh(), replay_procs()),
        now=FIXED_NOW,
    )
    item1 = _seed_item(ctx, "pkg:pypi/tool-one")
    item2 = _seed_item(ctx, "pkg:pypi/tool-two")

    dec_md = ctx.paths.sync / "DECISIONS.md"
    dec_md.write_text(
        "# Decisions\n\n"
        "| item | name | ring | decision | expires | your edit |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        f"| {item1} | tool-one | assess | assess | 2027-01-27 | snooze 30d |\n"
        f"| {item2} | tool-two | assess | assess | 2027-01-27 | maybe later?? |\n",
        encoding="utf-8",
    )

    stage_res = decisions.stage_decisions_md(ctx)
    assert stage_res["staged"] == 1
    assert len(stage_res["malformed"]) == 1
    assert stage_res["malformed"][0]["item"] == item2

    # Item 1 is STAGED in pending, NOT yet applied to items!
    st_row = ctx.db.execute("SELECT state FROM items WHERE item_id = ?", (item1,)).fetchone()
    assert st_row["state"] == "candidate"

    # Confirm applies the staged DECISIONS.md edit
    sink_file = isolated_radar_env["state"].parent / "sink_dec.log"
    os.environ["TECH_RADAR_REPLAY_DIR"] = str(REPO_ROOT / "tech-radar" / "tests" / "fixtures")
    os.environ["TECH_RADAR_NOTIFY_SINK"] = str(sink_file)
    pending.request_confirm(ctx)
    code = (
        sink_file.read_text(encoding="utf-8")
        .strip()
        .splitlines()[-1]
        .split("tech-radar code ", 1)[1]
        .split(":", 1)[0]
        .strip()
    )
    pending.confirm(ctx, code)

    st_after = ctx.db.execute("SELECT state FROM items WHERE item_id = ?", (item1,)).fetchone()
    assert st_after["state"] == "snoozed"
    dec_row = ctx.db.execute(
        "SELECT channel, actor, verb FROM decisions WHERE item_id = ? AND superseded_by IS NULL",
        (item1,),
    ).fetchone()
    assert dec_row["channel"] == "decisions_md"
    assert dec_row["actor"] == "user"
    assert dec_row["verb"] == "snooze"

    # Edit DECISIONS.md for item2 with valid `hold` and apply via `decisions apply` under a PTY
    dec_md.write_text(
        "# Decisions\n\n"
        "| item | name | ring | decision | expires | your edit |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        f"| {item1} | tool-one | assess | snooze | 2026-11-28 | |\n"
        f"| {item2} | tool-two | assess | assess | 2027-01-27 | hold |\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    rc, pty_out = _run_pty_subprocess(
        [sys.executable, str(RADAR_SCRIPT), "decisions", "apply", "--channel", "cli"],
        env,
    )
    assert rc == 0, pty_out
    pty_json = json.loads([ln.strip() for ln in pty_out.splitlines() if ln.strip()][-1])
    assert pty_json["ok"] is True
    assert pty_json["applied"] == 1

    item2_row = ctx.db.execute(
        "SELECT ring, state FROM items WHERE item_id = ?", (item2,)
    ).fetchone()
    assert item2_row["ring"] == "hold"
    assert item2_row["state"] == "active"


def test_alerts_auto_resolve_and_actor_check_constraint(
    replay_http: Callable[..., Any],
    replay_gh: Callable[..., Any],
    replay_procs: Callable[..., Any],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """auto-resolve marks version alerts resolved when bad version leaves usages and
    lifecycle alerts resolved when condition clears; non-TTY alert/decide exit 4;
    SQLite CHECK constraint rejects actor != 'user'.
    """
    ctx = context.build(
        transports=(replay_http(), replay_gh(), replay_procs()),
        now=FIXED_NOW,
    )
    eid1 = "pkg:pypi/litellm"
    eid2 = "gh:example-org/archived-lib"
    items.ensure_entity(ctx, eid1, classify_visibility=False)
    items.ensure_entity(ctx, eid2, classify_visibility=False)
    ctx.db.execute(
        "UPDATE entities SET visibility = 'public' WHERE entity_id IN (?, ?)", (eid1, eid2)
    )

    # Usage has safe version 1.106.2 (bad version 1.107.0 is no longer in usages)
    ctx.db.execute(
        "INSERT INTO usages(entity_id, context, version, direct, first_seen, last_seen) "
        "VALUES (?, 'project:https://github.com/example-org/public-app', '1.106.2', 1, ?, ?)",
        (eid1, FIXED_NOW, FIXED_NOW),
    )
    ctx.db.execute(
        "INSERT INTO usages(entity_id, context, version, direct, first_seen, last_seen) "
        "VALUES (?, 'project:https://github.com/example-org/public-app', '1.0.0', 1, ?, ?)",
        (eid2, FIXED_NOW, FIXED_NOW),
    )
    ctx.db.commit()

    # 1. Version-specific compromise alert on 1.107.0
    aid_ver, _ = security.upsert_alert(
        ctx,
        tier="P0",
        kind="compromise",
        confidence="confirmed",
        entity_id=eid1,
        dedupe_key="compromise|pkg:pypi/litellm|MAL-1|1.107.0",
        payload={"advisory_id": "MAL-1", "version": "1.107.0", "versions": ["1.107.0"]},
    )

    # 2. Lifecycle alert LIFE-01 whose repo is now unarchived
    aid_life, _ = security.upsert_alert(
        ctx,
        tier="P1",
        kind="lifecycle",
        confidence="confirmed",
        entity_id=eid2,
        dedupe_key="lifecycle|gh:example-org/archived-lib|LIFE-01",
        payload={"check": "LIFE-01", "entity_id": eid2},
    )
    ctx.db.execute(
        "INSERT INTO signals(entity_id, source_id, type, confidence, title, fetched_at, "
        "dedupe_key, payload_json) VALUES (?, 'gh_repo_meta', 'lifecycle', 'confirmed', "
        "'Repo meta', ?, 'gh_repo_meta|lifecycle|eid2', ?)",
        (eid2, FIXED_NOW, json.dumps({"archived": False})),
    )
    ctx.db.commit()

    resolved = alerts.auto_resolve(ctx)
    assert aid_ver in resolved
    assert aid_life in resolved

    for aid in (aid_ver, aid_life):
        st = ctx.db.execute("SELECT state FROM alerts WHERE alert_id = ?", (aid,)).fetchone()
        assert st["state"] == "resolved"

    # Non-TTY alert and decide commands exit 4
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert cli.main(["alert", "ack", f"A-{aid_ver}", "--channel", "cli"]) == 4
    capsys.readouterr()
    assert cli.main(["decide", "R-0001", "hold", "--channel", "cli"]) == 4
    capsys.readouterr()

    # Alert transitions (ack, resolve, false-positive) update state directly
    aid_open, _ = security.upsert_alert(
        ctx,
        tier="P1",
        kind="chatter",
        confidence="unconfirmed",
        entity_id=eid1,
        dedupe_key="chatter|pkg:pypi/litellm|hn-1",
        payload={"source": "hn_compromise"},
    )
    assert alerts.transition(ctx, "ack", f"A-{aid_open}")["state"] == "acked"
    assert alerts.transition(ctx, "false-positive", aid_open)["state"] == "false_positive"
    assert alerts.transition(ctx, "resolve", f"A-{aid_open}")["state"] == "resolved"

    # SQLite CHECK constraint forbids actor != 'user'
    item_id = _seed_item(ctx, eid1)
    with pytest.raises(sqlite3.IntegrityError):
        ctx.db.execute(
            "INSERT INTO decisions(item_id, verb, channel, actor, created_at) "
            "VALUES (?, 'hold', 'thread', 'agent', ?)",
            (item_id, FIXED_NOW),
        )
