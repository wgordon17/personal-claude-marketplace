"""Tests for runs.py, plan-run, usage.py, --run validation, and FTS5 search."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tech_radar import brief, cli, context, db, items, lock, runlog, runs

REPO_ROOT = Path(__file__).resolve().parents[2]
RADAR_SCRIPT = REPO_ROOT / "tech-radar" / "skills" / "radar" / "scripts" / "radar.py"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
FIXED_NOW = "2026-10-29T07:00:00Z"


def test_mode_selection_fixed_clock_overdue_and_catch_up(
    fixture_home: Any,
    replay_http: Callable[..., Any],
    replay_gh: Callable[..., Any],
    replay_procs: Callable[..., Any],
) -> None:
    """Fixed-clock mode selection:
    - Mon 2026-11-02 (first Monday of month) -> monthly
    - Mon 2026-11-09 -> weekly
    - Tue 2026-11-10 -> daily
    - last weekly 9 days ago on a Wednesday -> weekly
    - last daily 40 h ago -> catch_up
    """
    _ = fixture_home
    ctx = context.build(
        transports=(replay_http(), replay_gh(), replay_procs()),
        now="2026-11-02T07:00:00Z",
    )

    # First Monday of month -> monthly
    m_mode, m_catch = runs.select_effective_mode(ctx, requested="daily")
    assert m_mode == "monthly"
    assert m_catch is False

    # Second Monday of month -> weekly
    ctx.now = datetime(2026, 11, 9, 7, 0, tzinfo=UTC)
    w_mode, w_catch = runs.select_effective_mode(ctx, requested="daily")
    assert w_mode == "weekly"
    assert w_catch is False

    # Tuesday -> daily
    ctx.now = datetime(2026, 11, 10, 7, 0, tzinfo=UTC)
    d_mode, d_catch = runs.select_effective_mode(ctx, requested="daily")
    assert d_mode == "daily"
    assert d_catch is False

    # Seed a completed weekly run 9 days before Wednesday 2026-11-11 -> overdue (>8d) -> weekly
    ctx.db.execute(
        "INSERT INTO runs(run_id, mode, requested_mode, started_at, finished_at, status) "
        "VALUES ('20261102T070000Z-weekly', 'weekly', 'weekly', "
        "'2026-11-02T06:00:00Z', '2026-11-02T06:05:00Z', 'ok')"
    )
    ctx.db.commit()
    ctx.now = datetime(2026, 11, 11, 7, 0, tzinfo=UTC)  # Wednesday, >9 days after 2026-11-02T06:00
    over_mode, over_catch = runs.select_effective_mode(ctx, requested="daily")
    assert over_mode == "weekly"
    assert over_catch is True

    # Seed a completed daily run 40 hours before Thursday 2026-11-12 -> catch_up=True
    ctx.db.execute("DELETE FROM runs")
    ctx.db.execute(
        "INSERT INTO runs(run_id, mode, requested_mode, started_at, finished_at, status) "
        "VALUES ('20261110T150000Z-daily', 'daily', 'daily', "
        "'2026-11-10T15:00:00Z', '2026-11-10T15:05:00Z', 'ok')"
    )
    ctx.db.commit()
    ctx.now = datetime(2026, 11, 12, 7, 0, tzinfo=UTC)  # 40h after 2026-11-10T15:00:00Z
    c_mode, c_catch = runs.select_effective_mode(ctx, requested="daily")
    assert c_mode == "daily"
    assert c_catch is True


def test_plan_run_locking_stalled_hourly_isolation_and_subprocess_block(
    fixture_home: Any,
    isolated_radar_env: dict[str, Path],
    replay_http: Callable[..., Any],
    replay_gh: Callable[..., Any],
    replay_procs: Callable[..., Any],
) -> None:
    """Second plan-run in a separate subprocess exits 4; 3-hour-old lockfile is broken with
    warning and that run is reported stalled; held hourly.lock does not block plan-run;
    app sync invoked once per plan-run with no launchctl calls when unchanged.
    """
    _ = fixture_home
    p_tr = replay_procs()
    ctx = context.build(
        transports=(replay_http(), replay_gh(), p_tr),
        now="2026-11-10T07:00:00Z",
    )

    # Hold hourly.lock -> must NOT block plan-run
    h_lock = lock.acquire(
        "hourly",
        owner="hourly-1",
        mode="hourly",
        state_dir=ctx.paths.state,
        now=ctx.now,
    )
    assert h_lock is not None

    # 1. First plan-run syncs app and acquires run.lock
    res1 = runs.plan_run(ctx, requested="daily")
    assert res1["ok"] is True
    assert res1["app_sync"] == "synced"
    first_run_id = res1["run_id"]

    # 2. Second plan-run in a separate subprocess while run.lock is held -> exit 4
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TECH_RADAR_REPLAY_DIR"] = str(FIXTURES_DIR)
    sub = subprocess.run(
        [sys.executable, str(RADAR_SCRIPT), "plan-run", "--now", "2026-11-10T07:05:00Z"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert sub.returncode == 4, sub.stdout
    sub_json = json.loads(sub.stdout.strip())
    assert sub_json["ok"] is False
    assert sub_json["error"] == "run in progress"
    assert sub_json["run_id"] == first_run_id

    # 3. Advance clock by 3 hours -> lock is stale (>2h), broken with warning, prior run stalled,
    # and unchanged app sync makes zero launchctl calls (`status == 'noop'`).
    p_tr.calls.clear()
    ctx.now = datetime(2026, 11, 10, 10, 30, tzinfo=UTC)
    res2 = runs.plan_run(ctx, requested="daily")
    assert res2["ok"] is True
    assert res2["app_sync"] == "noop"
    assert first_run_id in res2["stalled_runs"]
    assert any("stalled" in w for w in res2["coverage_warnings"])
    launchctl_calls = [c for c, _e in p_tr.calls if c and c[0] == "launchctl"]
    assert launchctl_calls == []

    stalled_row = ctx.db.execute(
        "SELECT status, error FROM runs WHERE run_id = ?",
        (first_run_id,),
    ).fetchone()
    assert stalled_row["status"] == "failed"
    assert stalled_row["error"] == "stalled"
    lock.release(h_lock)


def test_plan_run_continues_past_provenance_mismatch_and_shallow_missing(
    fixture_home: Any,
    replay_http: Callable[..., Any],
    replay_gh: Callable[..., Any],
    replay_procs: Callable[..., Any],
) -> None:
    """Provenance-mismatch fixture -> plan-run exits 0 with a run row, an app_integrity alert,
    and a Coverage warning flag; shallow-missing with differing version -> exit 0, Coverage
    warning, no alert.
    """
    # 1. Tamper cached plugin file to trigger provenance mismatch
    home = Path(fixture_home)
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
        cached_radar.read_text(encoding="utf-8") + "\n# tampered\n",
        encoding="utf-8",
    )

    ctx = context.build(
        transports=(replay_http(), replay_gh(), replay_procs()),
        now="2026-11-10T07:00:00Z",
    )
    res_mismatch = runs.plan_run(ctx, requested="daily")
    assert res_mismatch["ok"] is True
    assert res_mismatch["app_sync"] == "refused"
    assert any("provenance mismatch" in w for w in res_mismatch["coverage_warnings"])

    run_row = ctx.db.execute(
        "SELECT run_id FROM runs WHERE run_id = ?",
        (res_mismatch["run_id"],),
    ).fetchone()
    assert run_row is not None
    alert_row = ctx.db.execute(
        "SELECT alert_id FROM alerts WHERE kind = 'app_integrity'"
    ).fetchone()
    assert alert_row is not None
    lock.release(res_mismatch["run_id"], state_dir=ctx.paths.state)

    # 2. Shallow-missing with differing version -> exit 0, Coverage warning, NO alert
    ctx.db.execute("DELETE FROM alerts")
    ctx.db.commit()
    home_shallow = fixture_home("baseline", marketplace="shallow-missing")
    mkt_plugin_json = (
        Path(home_shallow)
        / ".claude"
        / "plugins"
        / "marketplaces"
        / "personal-claude-marketplace"
        / "tech-radar"
        / ".claude-plugin"
        / "plugin.json"
    )
    p_obj = json.loads(mkt_plugin_json.read_text(encoding="utf-8"))
    p_obj["version"] = "0.9.9"
    mkt_plugin_json.write_text(json.dumps(p_obj, indent=2) + "\n", encoding="utf-8")

    ctx2 = context.build(
        transports=(replay_http(), replay_gh(), replay_procs()),
        now="2026-11-10T08:00:00Z",
    )
    res_shallow = runs.plan_run(ctx2, requested="daily")
    assert res_shallow["ok"] is True
    assert res_shallow["app_sync"] == "skipped"
    assert any("provenance unavailable" in w for w in res_shallow["coverage_warnings"])
    alert_cnt = int(
        ctx2.db.execute("SELECT COUNT(*) FROM alerts WHERE kind = 'app_integrity'").fetchone()[0]
    )
    assert alert_cnt == 0
    lock.release(res_shallow["run_id"], state_dir=ctx2.paths.state)


@pytest.mark.parametrize(
    "bad_run_id",
    [
        "../x",
        "20261102T070000Z-daily/../../state",
        "20261102T070000Z-daily",  # well-formed id with no runs row
    ],
)
@pytest.mark.parametrize(
    "subcmd_prefix",
    [
        ["collect"],
        ["enrich"],
        ["verdict", "apply"],
        ["render"],
    ],
)
def test_invalid_or_missing_run_id_exits_2_across_all_run_subcommands(
    bad_run_id: str,
    subcmd_prefix: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--run ../x`, `--run 20261102T070000Z-daily/../../state`, and a well-formed id with no
    runs row all exit 2 for collect, enrich, verdict apply, and render.
    """
    rc = cli.main([*subcmd_prefix, "--run", bad_run_id])
    out = json.loads(capsys.readouterr().out.strip())
    assert rc == 2, f"Expected exit 2 for {subcmd_prefix} --run {bad_run_id}, got {rc}: {out}"
    assert out["ok"] is False


def test_t3_model_resolution_decisions_staging_usage_and_fts_search_aliasing(
    fixture_home: Any,
    replay_http: Callable[..., Any],
    replay_gh: Callable[..., Any],
    replay_procs: Callable[..., Any],
) -> None:
    """- runs.model read from fixture T3 DB both by explicit --thread-id and by workspace_root
        fallback when --thread-id is omitted, and non-gemini-4-argon produces a Coverage warning;
    - edited DECISIONS.md staged once (pending:1) and not re-staged on next run;
    - usage fixture JSONL overlapping previous run window (same cwd) summed into usage_json
      and surfaced in next brief's usage_last_run; JSONL with different cwd ignored;
    - search for a private package's signal returns only its alias.
    """
    home = Path(fixture_home)
    ctx = context.build(
        transports=(replay_http(), replay_gh(), replay_procs()),
        now="2026-10-28T07:00:00Z",
    )

    # Seed an item and DECISIONS.md edit
    eid_pub = "pkg:pypi/public-tool"
    items.ensure_entity(ctx, eid_pub, classify_visibility=False)
    ctx.db.execute("UPDATE entities SET visibility = 'public' WHERE entity_id = ?", (eid_pub,))
    item_id, _ = items.ensure_item(ctx, eid_pub)

    (ctx.paths.sync / "DECISIONS.md").write_text(
        "# Decisions\n\n"
        "| item | name | ring | decision | expires | your edit |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        f"| {item_id} | public-tool | assess | assess | 2027-01-27 | snooze 30d |\n",
        encoding="utf-8",
    )

    # 1. Run 1: workspace_root fallback resolves gemini-4-argon and stages DECISIONS.md once
    res1 = runs.plan_run(ctx, requested="daily")
    assert res1["ok"] is True
    assert res1["model"] == "gemini-4-argon"
    assert res1["model_warning"] is None
    assert res1["pending"] == 1
    assert res1["decisions_staged"] == 1
    run1_id = res1["run_id"]
    runlog.finish_run(ctx, run1_id, "ok")
    lock.release(run1_id, state_dir=ctx.paths.state)

    # Write usage JSONL overlapping Run 1 window with matching cwd, plus one with different cwd
    proj_transcript_dir = home / ".claude" / "projects" / "-Users-example-user-radar"
    proj_transcript_dir.mkdir(parents=True, exist_ok=True)
    template_jsonl = (FIXTURES_DIR / "usage" / "run_session.jsonl").read_text(encoding="utf-8")
    (proj_transcript_dir / "matching.jsonl").write_text(
        template_jsonl.replace("{CWD}", os.getcwd()),
        encoding="utf-8",
    )
    (proj_transcript_dir / "other_cwd.jsonl").write_text(
        template_jsonl.replace("{CWD}", "/some/unrelated/cwd"),
        encoding="utf-8",
    )

    # Add a thread with a non-gemini-4-argon model to test explicit --thread-id and model warning
    t3_db = home / ".t3" / "userdata" / "statev2.sqlite"
    t3_conn = sqlite3.connect(str(t3_db))
    try:
        t3_conn.execute(
            "INSERT INTO orchestration_v2_projection_threads("
            "thread_id, project_id, title, default_provider, runtime_mode, interaction_mode, "
            "created_at, updated_at, deleted_at, payload_json"
            ") VALUES (?, 'proj-radar-1', 'Custom Thread', 'claudeAgent_bifrost', "
            "'auto', 'default', '2026-10-29T07:00:00Z', '2026-10-29T07:01:00Z', NULL, ?)",
            (
                "thread:project:proj-radar-1:custom-sonnet",
                json.dumps({"modelSelection": {"model": "claude-sonnet-4-5"}}),
            ),
        )
        t3_conn.commit()
    finally:
        t3_conn.close()

    # 2. Run 2 on next day with explicit --thread-id:
    # - DECISIONS.md unchanged -> not re-staged (decisions_staged == 0, pending still 1)
    # - non-gemini-4-argon model -> coverage warning
    # - previous run's usage_json computed from matching.jsonl (ignoring other_cwd.jsonl)
    ctx.now = datetime(2026, 10, 29, 7, 0, tzinfo=UTC)
    res2 = runs.plan_run(
        ctx,
        requested="daily",
        thread_id="thread:project:proj-radar-1:custom-sonnet",
    )
    assert res2["ok"] is True
    assert res2["decisions_staged"] == 0
    assert res2["pending"] == 1
    assert res2["model"] == "claude-sonnet-4-5"
    assert res2["model_warning"] is not None
    assert any("model warning" in w for w in res2["coverage_warnings"])

    # Verify Run 1 usage_json was populated from matching.jsonl only (2000 in, 800 out, 2 turns)
    run1_row = ctx.db.execute(
        "SELECT usage_json FROM runs WHERE run_id = ?",
        (run1_id,),
    ).fetchone()
    assert run1_row["usage_json"] is not None
    u_obj = json.loads(str(run1_row["usage_json"]))
    assert u_obj["input_tokens"] == 2000
    assert u_obj["output_tokens"] == 800
    assert u_obj["turns"] == 2

    # Generate brief for Run 2 and verify usage_last_run is surfaced in index.json
    idx_obj = brief.generate(ctx, res2["run_id"])
    assert idx_obj["usage_last_run"]["input_tokens"] == 2000
    assert idx_obj["usage_last_run"]["turns"] == 2
    lock.release(res2["run_id"], state_dir=ctx.paths.state)

    # 3. FTS5 search for a private package's signal returns only its alias (Spec 12 A8)
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
        "'/p/secret-widget', 1, 0, 'active_projects', 'private', 'private-project-1', '[]') "
        "ON CONFLICT(project_key) DO NOTHING"
    )
    ctx.db.execute(
        "INSERT INTO usages(entity_id, context, version, direct, first_seen, last_seen) "
        "VALUES (?, 'project:https://github.com/example-org/secret-widget', '0.2.0', 1, ?, ?) "
        "ON CONFLICT(entity_id, context) DO NOTHING",
        (priv_eid, FIXED_NOW, FIXED_NOW),
    )
    db.insert_signal(
        ctx.db,
        source_id="pypi_meta",
        signal_type="lifecycle",
        entity_id=priv_eid,
        title="Deprecation notice on secret-widget-core",
        untrusted_excerpt="Package secret-widget-core in example-org/secret-widget is deprecated",
        fetched_at=FIXED_NOW,
        dedupe_key="pypi_meta|lifecycle|secret-widget-core",
    )
    # Also insert an inventory_change signal for the private package -> must be excluded per A8
    db.insert_signal(
        ctx.db,
        source_id="inventory",
        signal_type="inventory_change",
        entity_id=priv_eid,
        title="Added secret-widget-core in project",
        untrusted_excerpt="Deprecation keyword in inventory change for secret-widget-core",
        fetched_at=FIXED_NOW,
        dedupe_key="inventory_change|added|secret-widget-core",
    )

    search_res = runs.search(ctx, "Deprecation")
    dumped = json.dumps(search_res)
    assert search_res["count"] >= 1
    assert "secret-widget" not in dumped
    assert "private-project-1" in dumped
    assert all(r.get("type") != "inventory_change" for r in search_res["results"])

    # 4. Explicit `usage record --run <id> --json <file>` records usage_json on Run 2
    explicit_usage_file = home / "explicit_usage.json"
    explicit_usage_file.write_text(
        json.dumps({"input_tokens": 500, "output_tokens": 250, "turns": 3}),
        encoding="utf-8",
    )
    assert (
        cli.main(
            [
                "usage",
                "record",
                "--run",
                res2["run_id"],
                "--json",
                str(explicit_usage_file),
            ]
        )
        == 0
    )
    run2_u = json.loads(
        str(
            ctx.db.execute(
                "SELECT usage_json FROM runs WHERE run_id = ?",
                (res2["run_id"],),
            ).fetchone()["usage_json"]
        )
    )
    assert run2_u["input_tokens"] == 500
    assert run2_u["output_tokens"] == 250
    assert run2_u["turns"] == 3

    # 5. format_run_id bumps by +1s when a run row with the same timestamp+mode exists
    ctx.now = datetime(2026, 10, 29, 7, 0, tzinfo=UTC)
    bumped_id = runlog.format_run_id(ctx, "daily")
    assert bumped_id == "20261029T070001Z-daily"

    # 6. SCHED-10: lookback_since widens query window when catch_up=True is active on a run
    from datetime import timedelta

    from tech_radar.sources.base import lookback_since

    runlog.finish_run(ctx, res2["run_id"], "ok")
    ctx.now = datetime(2026, 10, 30, 23, 0, tzinfo=UTC)  # 40h after 2026-10-29T07:00:00Z
    res_catchup = runs.plan_run(ctx, requested="daily")
    assert res_catchup["catch_up"] is True
    ctx.run_id = res_catchup["run_id"]
    widened = lookback_since(ctx, timedelta(days=1))
    assert widened == datetime(2026, 10, 29, 7, 0, tzinfo=UTC)
    lock.release(res_catchup["run_id"], state_dir=ctx.paths.state)


def test_collect_degraded_source_returns_exit_code_3(
    fixture_home: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_handle_collect returns exit code 3 when a source fails (not skipped)."""
    _ = fixture_home
    from tech_radar import sources
    from tech_radar.sources.base import SourceResult

    monkeypatch.setenv("TECH_RADAR_REPLAY_DIR", str(FIXTURES_DIR))
    assert cli.main(["plan-run", "--now", "2026-10-29T07:00:00Z"]) == 0
    plan_out = json.loads(capsys.readouterr().out.strip())
    run_id = plan_out["run_id"]

    monkeypatch.setattr(
        sources,
        "run_collect_sources",
        lambda _ctx: [
            SourceResult(source_id="cisa_kev", signals=[], ok=False, error="simulated HTTP 500")
        ],
    )
    rc = cli.main(["collect", "--run", run_id])
    out = json.loads(capsys.readouterr().out.strip())
    assert rc == 3
    assert out["ok"] is True
    assert out["run_id"] == run_id
