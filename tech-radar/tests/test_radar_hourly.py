"""Tests for lock.py, runlog.py, notify.py, and hourly compromise check."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tech_radar import (
    cli,
    config,
    context,
    db,
    http,
    inventory,
    lock,
    notify,
    procs,
    runlog,
    security,
)
from tech_radar.sources.base import RawSignal, SourceResult

FIXED_NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
REPO_ROOT = TESTS_DIR.parents[1]
SCRIPTS_DIR = REPO_ROOT / "tech-radar" / "skills" / "radar" / "scripts"
RADAR_SCRIPT = SCRIPTS_DIR / "radar.py"


def _make_ctx(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
    *,
    now: datetime = FIXED_NOW,
    extra_http_roots: list[Path] | None = None,
    extra_proc_roots: list[Path] | None = None,
    db_conn: Any = None,
    cache_conn: Any = None,
) -> context.Ctx:
    return context.build(
        transports={
            "http": replay_http(extra_roots=extra_http_roots),
            "gh": replay_gh(extra_roots=extra_proc_roots),
            "procs": replay_procs(extra_roots=extra_proc_roots),
        },
        now=now,
        db_conn=db_conn,
        cache_conn=cache_conn,
    )


def test_lock_cross_process_persistence_stale_breaking_and_hourly_independence(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """run.lock survives subprocess exit, checks owner, breaks stale, hourly separate."""
    p = config.paths(ensure_dirs=True)

    # 1. Acquire run.lock in a separate subprocess that exits immediately
    sub_code = (
        "import sys; sys.dont_write_bytecode = True; "
        f"sys.path.insert(0, {str(SCRIPTS_DIR)!r}); "
        "from tech_radar import lock; "
        "lk = lock.acquire('run', '20261028T070000Z-daily', 'daily'); "
        "assert lk is not None"
    )
    res = subprocess.run(
        [sys.executable, "-c", sub_code],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert (p.state / "run.lock").is_file()

    # Blocks try_lock('run') in this process
    assert lock.try_lock("run", "20261028T080000Z-daily", "daily", now=FIXED_NOW) is None

    # Holding run.lock does NOT block try_lock('hourly')
    h_lock = lock.try_lock("hourly", "hourly-1", "hourly", now=FIXED_NOW)
    assert h_lock is not None
    assert (p.state / "hourly.lock").is_file()

    # Release with a different owner is a no-op
    assert lock.release("run", "wrong-owner") is False
    assert (p.state / "run.lock").is_file()

    # Release with the right owner removes the file
    assert lock.release("run", "20261028T070000Z-daily") is True
    assert not (p.state / "run.lock").exists()
    assert lock.release(h_lock) is True
    assert not (p.state / "hourly.lock").exists()

    # 2. A run.lock 3 hours old is broken by acquire with a logged warning
    old_run_time = FIXED_NOW - timedelta(hours=3)
    stale_run = lock.try_lock("run", "stale-run-id", "daily", now=old_run_time)
    assert stale_run is not None
    old_ts = old_run_time.timestamp()
    os.utime(stale_run.path, (old_ts, old_ts))

    with caplog.at_level(logging.WARNING):
        fresh_run = lock.acquire("run", "new-run-id", "daily", now=FIXED_NOW)
    assert fresh_run is not None
    assert fresh_run.owner == "new-run-id"
    assert fresh_run.broke_stale == "stale-run-id"
    assert any("stale" in rec.message.lower() for rec in caplog.records)
    lock.release(fresh_run)

    # 3. An hourly.lock 20 minutes old is broken by acquire
    old_hourly_time = FIXED_NOW - timedelta(minutes=20)
    stale_hourly = lock.try_lock("hourly", "stale-hourly-id", "hourly", now=old_hourly_time)
    assert stale_hourly is not None
    old_h_ts = old_hourly_time.timestamp()
    os.utime(stale_hourly.path, (old_h_ts, old_h_ts))

    fresh_hourly = lock.acquire("hourly", "new-hourly-id", "hourly", now=FIXED_NOW)
    assert fresh_hourly is not None
    assert fresh_hourly.broke_stale == "stale-hourly-id"
    lock.release(fresh_hourly)


def test_runlog_start_and_finish_run_hourly(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    """start_run and finish_run write and finalize a runs row with mode 'hourly'."""
    ctx = _make_ctx(replay_http, replay_gh, replay_procs)
    rid = runlog.start_run(ctx, mode="hourly", requested_mode="hourly")
    assert rid == "20261028T120000Z-hourly"

    row_running = ctx.db.execute(
        "SELECT mode, requested_mode, status, started_at, finished_at FROM runs WHERE run_id = ?",
        (rid,),
    ).fetchone()
    assert row_running is not None
    assert str(row_running["mode"]) == "hourly"
    assert str(row_running["status"]) == "running"
    assert row_running["finished_at"] is None

    runlog.finish_run(ctx, rid, "ok", counts={"notified": 1})
    row_done = ctx.db.execute(
        "SELECT status, finished_at, counts_json FROM runs WHERE run_id = ?",
        (rid,),
    ).fetchone()
    assert row_done is not None
    assert str(row_done["status"]) == "ok"
    assert row_done["finished_at"] == ctx.now_iso
    assert json.loads(str(row_done["counts_json"])) == {"notified": 1}


def test_notify_injection_policy_retries_and_sink_gating(
    fixture_home: Any,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Injection summary stripped, non-notifiable kinds skipped, retry cap, sink gated."""
    _ = fixture_home
    ctx = _make_ctx(replay_http, replay_gh, replay_procs)
    inventory.collect(ctx)
    db.kv_set(ctx.db, "baseline_done", ctx.now_iso)

    canary_target = tmp_path / "TOUCHED_X"
    inj_summary = f'"; do shell script "touch {canary_target}"'
    inj_sig = RawSignal(
        source_id="osv_batch",
        type="compromise",
        external_id="MAL-2026-7777",
        title=inj_summary,
        url="https://osv.dev/vulnerability/MAL-2026-7777",
        published_at="2026-10-28T10:00:00Z",
        untrusted_excerpt=inj_summary,
        entity_hint="pkg:pypi/litellm",
        confidence="confirmed",
        payload={
            "advisory_id": "MAL-2026-7777",
            "is_mal": True,
            "affected_versions": ["1.106.2"],
            "published": "2026-10-28T10:00:00Z",
            "modified": "2026-10-28T10:00:00Z",
        },
    )
    security.evaluate(ctx, [SourceResult(source_id="osv_batch", signals=[inj_sig])], mode="hourly")

    # Seed chatter, lifecycle, and >30-day-old incident alerts (none should notify)
    security.upsert_alert(
        ctx,
        tier="P1",
        kind="chatter",
        confidence="unconfirmed",
        entity_id="pkg:pypi/litellm",
        dedupe_key="chatter|pkg:pypi/litellm|2026-10-28",
        notified_at=None,
    )
    security.upsert_alert(
        ctx,
        tier="P1",
        kind="lifecycle",
        confidence="confirmed",
        entity_id="pkg:pypi/litellm",
        dedupe_key="lifecycle|pkg:pypi/litellm|archived",
        notified_at=None,
    )
    security.upsert_alert(
        ctx,
        tier="P1",
        kind="incident",
        confidence="confirmed",
        entity_id="pkg:pypi/litellm",
        dedupe_key="incident|pkg:pypi/litellm|MAL-2026-0001|1.106.2",
        notified_at=None,
        payload={
            "advisory_id": "MAL-2026-0001",
            "entity_id": "pkg:pypi/litellm",
            "published": "2026-06-01T00:00:00Z",
            "modified": "2026-06-01T00:00:00Z",
        },
    )

    # Set TECH_RADAR_NOTIFY_SINK WITHOUT TECH_RADAR_REPLAY_DIR -> must be ignored
    sink_file = tmp_path / "notify_sink.txt"
    monkeypatch.setenv("TECH_RADAR_NOTIFY_SINK", str(sink_file))
    monkeypatch.delenv("TECH_RADAR_REPLAY_DIR", raising=False)

    calls_before = len(ctx.procs.calls)
    sent = notify.deliver_pending(ctx)
    assert sent == 1
    assert not sink_file.exists()
    assert not canary_target.exists()

    notify_calls = [
        c[0]
        for c in ctx.procs.calls[calls_before:]
        if c[0][0] in ("terminal-notifier", "osascript")
    ]
    assert len(notify_calls) == 1
    first_call = notify_calls[0]
    msg_arg = first_call[4] if first_call[0] == "terminal-notifier" else first_call[2]
    assert "MAL-2026-7777" in msg_arg
    assert "litellm" in msg_arg
    assert "1.106.2" in msg_arg
    assert "do shell script" not in msg_arg
    assert "touch" not in msg_arg

    # Verify chatter, lifecycle, and old incident were NOT notified
    unnotified_kinds = {
        str(r["kind"])
        for r in ctx.db.execute("SELECT kind FROM alerts WHERE notified_at IS NULL").fetchall()
    }
    assert unnotified_kinds == {"chatter", "lifecycle", "incident"}

    # Test 3-attempt failure backoff and cap on a failing notification
    fail_aid, _ = security.upsert_alert(
        ctx,
        tier="P1",
        kind="kev",
        confidence="confirmed",
        entity_id="pkg:pypi/litellm",
        dedupe_key="kev|pkg:pypi/litellm|CVE-2026-1111|1.106.2",
        notified_at=None,
        payload={
            "advisory_id": "CVE-2026-1111",
            "cve_id": "CVE-2026-1111",
            "entity_id": "pkg:pypi/litellm",
            "version": "1.106.2",
        },
    )

    fail_proc_dir = tmp_path / "fail_notify_procs"
    fail_proc_dir.mkdir(parents=True, exist_ok=True)
    grp_hash = notify.group_id("kev|pkg:pypi/litellm|CVE-2026-1111|1.106.2")
    (fail_proc_dir / "tn_fail.json").write_text(
        json.dumps(
            {
                "argv": [
                    "terminal-notifier",
                    "-title",
                    "tech-radar",
                    "-message",
                    "tech-radar: exploited vuln CVE-2026-1111 in litellm",
                    "-group",
                    grp_hash,
                ],
                "returncode": 1,
                "stdout": "",
                "stderr": "simulated notifier error",
            }
        ),
        encoding="utf-8",
    )

    ctx_fail = _make_ctx(
        replay_http,
        replay_gh,
        replay_procs,
        extra_proc_roots=[fail_proc_dir],
        db_conn=ctx.db,
        cache_conn=ctx.cache_db,
    )

    # Attempt 1 & 2 fail -> notify_attempts=1, 2 and notified_at remains NULL
    assert notify.deliver_pending(ctx_fail) == 0
    r1 = ctx.db.execute(
        "SELECT notify_attempts, notified_at, last_error FROM alerts WHERE alert_id = ?",
        (fail_aid,),
    ).fetchone()
    assert int(r1["notify_attempts"]) == 1
    assert r1["notified_at"] is None

    assert notify.deliver_pending(ctx_fail) == 0
    r2 = ctx.db.execute(
        "SELECT notify_attempts, notified_at, last_error FROM alerts WHERE alert_id = ?",
        (fail_aid,),
    ).fetchone()
    assert int(r2["notify_attempts"]) == 2
    assert r2["notified_at"] is None

    # Attempt 3 fails -> notify_attempts=3 and notified_at is set with last_error
    assert notify.deliver_pending(ctx_fail) == 0
    r3 = ctx.db.execute(
        "SELECT notify_attempts, notified_at, last_error FROM alerts WHERE alert_id = ?",
        (fail_aid,),
    ).fetchone()
    assert int(r3["notify_attempts"]) == 3
    assert r3["notified_at"] == ctx.now_iso
    assert "simulated notifier error" in str(r3["last_error"])

    # Test notify.esc, osascript fallback when terminal-notifier is unavailable, and notify-test CLI
    assert notify.esc('hello "world" \\ \n\x00') == 'hello \\"world\\" \\\\  '
    monkeypatch.setattr(
        ctx.procs,
        "resolve",
        lambda name: None if name == "terminal-notifier" else "/usr/bin/osascript",
    )
    osa_before = len(ctx.procs.calls)
    ok_osa, err_osa = notify.send('test "quote"', "grp1", ctx=ctx)
    assert ok_osa is True
    assert err_osa is None
    osa_calls = [c[0] for c in ctx.procs.calls[osa_before:] if "osascript" in c[0][0]]
    assert len(osa_calls) == 1
    assert osa_calls[0][1] == "-e"
    assert 'display notification "test \\"quote\\"" with title "tech-radar"' in osa_calls[0][2]

    monkeypatch.setenv("TECH_RADAR_REPLAY_DIR", str(FIXTURES_DIR))
    monkeypatch.setenv("TECH_RADAR_NOTIFY_SINK", str(sink_file))
    assert cli.main(["notify-test"]) == 0
    assert "tech-radar: test notification" in sink_file.read_text(encoding="utf-8")


def test_hourly_skip_rules_compromised_replay_a27_refresh_and_schema_guard(
    fixture_home: Any,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Skip rules, litellm replay, A27 version bump & new dep, schema guard."""
    # 1. Empty usages -> skipped: no inventory yet
    monkeypatch.setenv("TECH_RADAR_REPLAY_DIR", str(FIXTURES_DIR))
    assert cli.main(["hourly"]) == 0
    empty_out = json.loads(capsys.readouterr().out.strip())
    assert empty_out == {"ok": True, "skipped": "no inventory yet"}

    # 2. hourly.lock held by a live hourly -> exit 0 {"ok": True, "skipped": "hourly in progress"}
    held_h = lock.acquire("hourly", "live-hourly", "hourly")
    assert held_h is not None
    assert cli.main(["hourly"]) == 0
    locked_out = json.loads(capsys.readouterr().out.strip())
    assert locked_out == {"ok": True, "skipped": "hourly in progress"}
    lock.release(held_h)

    # 3. Baseline collect, then A27: rewrite launchd project's uv.lock to litellm==1.107.0
    home = fixture_home(scenario="baseline")
    ctx_base = _make_ctx(replay_http, replay_gh, replay_procs)
    inventory.collect(ctx_base)

    # Hold run.lock to verify hourly still runs when run.lock is held
    held_run = lock.acquire("run", "20261028T070000Z-daily", "daily")
    assert held_run is not None

    gw_lock = home / ".config" / "llm-gateway" / "uv.lock"
    gw_lock.write_text(
        gw_lock.read_text(encoding="utf-8").replace("1.106.2", "1.107.0"),
        encoding="utf-8",
    )

    sink = tmp_path / "hourly_sink.txt"
    monkeypatch.setenv("TECH_RADAR_NOTIFY_SINK", str(sink))

    assert cli.main(["hourly"]) == 0
    h1_out = json.loads(capsys.readouterr().out.strip())
    assert h1_out["ok"] is True
    assert h1_out["refreshed"] >= 1
    assert h1_out["new_alerts"] == 1
    assert h1_out["notified"] == 1
    sink_lines = sink.read_text(encoding="utf-8").splitlines()
    assert len(sink_lines) == 1
    assert "MALWARE litellm 1.107.0 (MAL-2026-9999)" in sink_lines[0]

    # Second hourly rerun -> no new notification
    assert cli.main(["hourly"]) == 0
    h2_out = json.loads(capsys.readouterr().out.strip())
    assert h2_out["notified"] == 0
    assert len(sink.read_text(encoding="utf-8").splitlines()) == 1

    # 4. A27: Add a NEW dependency evilpkg==0.0.1 (MAL fixture) to repo A's uv.lock
    extra_http = tmp_path / "extra_http"
    extra_http.mkdir(parents=True, exist_ok=True)

    # Classify fixture for https://pypi.org/simple/evilpkg/
    evil_cls_key = http.replay_key("GET", "https://pypi.org/simple/evilpkg/")
    (extra_http / f"{evil_cls_key}.json").write_text(
        json.dumps({"status": 200, "headers": {}, "body": "<html>evilpkg</html>"}),
        encoding="utf-8",
    )

    # Vuln detail fixture for https://api.osv.dev/v1/vulns/MAL-2026-8888
    evil_vuln_key = http.replay_key("GET", "https://api.osv.dev/v1/vulns/MAL-2026-8888")
    (extra_http / f"{evil_vuln_key}.json").write_text(
        json.dumps(
            {
                "status": 200,
                "headers": {"content-type": "application/json"},
                "body": {
                    "id": "MAL-2026-8888",
                    "summary": "Malicious Package in evilpkg",
                    "details": "Credential stealer in evilpkg 0.0.1",
                    "published": "2026-10-28T09:00:00Z",
                    "modified": "2026-10-28T09:00:00Z",
                    "database_specific": {"cwe_ids": ["CWE-506"]},
                    "affected": [
                        {
                            "package": {"ecosystem": "PyPI", "name": "evilpkg"},
                            "versions": ["0.0.1"],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    # Extended querybatch fixture including ("PyPI", "evilpkg", "0.0.1") in sorted order
    base_qb = json.loads(
        (FIXTURES_DIR / "http" / "osv_batch" / "querybatch_default.json").read_text(
            encoding="utf-8"
        )
    )
    paired = list(zip(base_qb["queries"], base_qb["body"]["results"], strict=True))
    paired.append(
        (
            ["PyPI", "evilpkg", "0.0.1"],
            {"vulns": [{"id": "MAL-2026-8888", "modified": "2026-10-28T09:00:00Z"}]},
        )
    )
    paired.sort(key=lambda item: tuple(item[0]))
    base_qb["queries"] = [q for q, _r in paired]
    base_qb["body"]["results"] = [r for _q, r in paired]
    (extra_http / "querybatch_with_evilpkg.json").write_text(
        json.dumps(base_qb),
        encoding="utf-8",
    )

    monkeypatch.setenv("TECH_RADAR_REPLAY_DIR", f"{extra_http}{os.pathsep}{FIXTURES_DIR}")
    # Clear cached querybatch entry so the new dependency set queries OSV
    ctx_base.cache_db.execute("DELETE FROM http_cache")
    ctx_base.cache_db.commit()

    repo_a_lock = home / "SynologyDrive" / "ActiveProjects" / "public-app" / "uv.lock"
    lock_txt = repo_a_lock.read_text(encoding="utf-8")
    lock_txt = lock_txt.replace(
        'dependencies = [\n    { name = "anthropic" },',
        'dependencies = [\n    { name = "anthropic" },\n    { name = "evilpkg" },',
    )
    lock_txt += (
        "\n[[package]]\n"
        'name = "evilpkg"\n'
        'version = "0.0.1"\n'
        'source = { registry = "https://pypi.org/simple" }\n'
    )
    repo_a_lock.write_text(lock_txt, encoding="utf-8")

    assert cli.main(["hourly"]) == 0
    h3_out = json.loads(capsys.readouterr().out.strip())
    assert h3_out["ok"] is True
    assert h3_out["new_alerts"] == 1
    assert h3_out["notified"] == 1

    evil_ent = ctx_base.db.execute(
        "SELECT visibility FROM entities WHERE entity_id = 'pkg:pypi/evilpkg'"
    ).fetchone()
    assert evil_ent is not None
    assert str(evil_ent["visibility"]) == "public"

    sink_lines_after = sink.read_text(encoding="utf-8").splitlines()
    assert len(sink_lines_after) == 2
    assert "MALWARE evilpkg 0.0.1 (MAL-2026-8888)" in sink_lines_after[1]

    lock.release(held_run)

    # 5. Schema guard (A31): user_version above supported -> exit 4 + one notification
    ctx_base.db.execute("PRAGMA user_version = 99;")
    ctx_base.db.commit()

    assert cli.main(["hourly"]) == 4
    sg1_out = json.loads(capsys.readouterr().out.strip())
    assert sg1_out["ok"] is False
    assert len(sink.read_text(encoding="utf-8").splitlines()) == 3
    assert sink.read_text(encoding="utf-8").splitlines()[-1] == (
        "tech-radar: app integrity check failed"
    )

    # Second run with unsupported user_version -> exit 4, no second notification
    assert cli.main(["hourly"]) == 4
    capsys.readouterr()
    assert len(sink.read_text(encoding="utf-8").splitlines()) == 3


def test_hourly_subprocess_offline_with_replay_and_sink(
    fixture_home: Any,
    warm_uv_script_cache: bool,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
    tmp_path: Path,
) -> None:
    """Subprocess `radar.py hourly` with UV_OFFLINE=1 and replay/sink env exits 0."""
    if not warm_uv_script_cache:
        pytest.skip("uv script cache warm-up unavailable on cold machine")

    fixture_home(scenario="compromised")
    ctx = _make_ctx(replay_http, replay_gh, replay_procs)
    inventory.collect(ctx)

    sink = tmp_path / "subproc_sink.txt"
    env = os.environ.copy()
    env["UV_MALWARE_CHECK"] = "0"
    env["UV_OFFLINE"] = "1"
    env["TECH_RADAR_REPLAY_DIR"] = str(FIXTURES_DIR)
    env["TECH_RADAR_NOTIFY_SINK"] = str(sink)

    uv_bin = procs.resolve("uv", force_live=True) or "uv"
    res = subprocess.run(
        [uv_bin, "run", "--script", "--locked", "--isolated", str(RADAR_SCRIPT), "hourly"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    out_json = json.loads(res.stdout.strip())
    assert out_json["ok"] is True
    assert out_json["notified"] == 1
    assert sink.is_file()
    assert "MALWARE litellm 1.107.0 (MAL-2026-9999)" in sink.read_text(encoding="utf-8")
