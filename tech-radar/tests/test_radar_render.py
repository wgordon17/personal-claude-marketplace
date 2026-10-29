"""Tests for radar_db_builder.py, render.py, and snapshot.py (Task 9 Steps 1–5)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
import radar_db_builder
import secret_samples
from tech_radar import cli, db, items, lock, pending, render, runs

TESTS_DIR = Path(__file__).resolve().parent
GOLDEN_DIR = TESTS_DIR / "fixtures" / "golden"


class _StrictRadarHTMLParser(HTMLParser):
    """HTML parser recording tags, attributes, links, and structural elements."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self.anchors: list[dict[str, str | None]] = []
        self.svg_count = 0
        self.circle_count = 0
        self.title_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        low = tag.lower()
        self.tags.append(low)
        attr_map = {k.lower(): v for k, v in attrs}
        if low == "a":
            self.anchors.append(attr_map)
        elif low == "svg":
            self.svg_count += 1
        elif low == "circle":
            self.circle_count += 1
        elif low == "title":
            self.title_count += 1


def _assert_fenced_only(markdown: str, needle: str) -> None:
    """Assert `needle` appears in `markdown` and ONLY inside fenced code blocks."""
    assert needle in markdown, f"Expected {needle!r} in markdown"
    in_fence = False
    fence_marker = ""
    for line in markdown.splitlines():
        stripped = line.strip()
        if not in_fence and stripped.startswith("```"):
            in_fence = True
            fence_marker = stripped[: len(stripped) - len(stripped.lstrip("`"))]
            continue
        if in_fence and stripped == fence_marker:
            in_fence = False
            fence_marker = ""
            continue
        if not in_fence:
            assert needle not in line, f"Found {needle!r} outside code fence on line: {line!r}"


def test_builder_deterministic_iterdump_and_state_completeness(tmp_path: Path) -> None:
    """Step 1: building twice yields byte-identical DB dumps (iterdump) across all states."""
    for state_name in ("daily", "weekly", "monthly", "quiet", "partial"):
        db_a = radar_db_builder.build(tmp_path / f"a_{state_name}", state=state_name)  # type: ignore[arg-type]
        db_b = radar_db_builder.build(tmp_path / f"b_{state_name}", state=state_name)  # type: ignore[arg-type]

        conn_a = sqlite3.connect(str(db_a))
        conn_b = sqlite3.connect(str(db_b))
        try:
            dump_a = "\n".join(conn_a.iterdump()).encode("utf-8")
            dump_b = "\n".join(conn_b.iterdump()).encode("utf-8")
            assert dump_a == dump_b, f"Non-deterministic iterdump for state={state_name}"
        finally:
            conn_a.close()
            conn_b.close()

    # Verify completeness of the seeded daily DB
    conn = sqlite3.connect(str(tmp_path / "a_daily" / "state" / "radar.db"))
    conn.row_factory = sqlite3.Row
    try:
        rings = {
            str(r["ring"])
            for r in conn.execute("SELECT DISTINCT ring FROM items WHERE ring IS NOT NULL")
        }
        assert rings == {"adopt", "trial", "assess", "hold", "watch"}

        quadrants = {
            str(r["quadrant"])
            for r in conn.execute("SELECT DISTINCT quadrant FROM items WHERE quadrant IS NOT NULL")
        }
        assert quadrants == set(items.ALL_QUADRANTS)

        alert_kinds = {str(r["kind"]) for r in conn.execute("SELECT DISTINCT kind FROM alerts")}
        assert alert_kinds == {
            "compromise",
            "incident",
            "kev",
            "integrity",
            "app_integrity",
            "chatter",
            "lifecycle",
            "feature",
        }
        alert_tiers = {str(r["tier"]) for r in conn.execute("SELECT DISTINCT tier FROM alerts")}
        assert alert_tiers == {"P0", "P1", "P2"}

        sig_types = {str(r["type"]) for r in conn.execute("SELECT DISTINCT type FROM signals")}
        assert sig_types == {
            "feature",
            "compromise",
            "kev",
            "lifecycle",
            "contender",
            "horizon",
            "technique",
            "pricing",
            "inventory_change",
        }

        priv_proj = conn.execute(
            "SELECT alias, visibility FROM projects WHERE project_key = ?",
            (radar_db_builder.PRIVATE_PROJECT_KEY,),
        ).fetchone()
        assert priv_proj is not None
        assert priv_proj["alias"] == "private-project-1"
        assert priv_proj["visibility"] == "private"

        priv_pkg = conn.execute(
            "SELECT kind, visibility FROM entities WHERE entity_id = ?",
            (radar_db_builder.PRIVATE_PACKAGE_ID,),
        ).fetchone()
        assert priv_pkg is not None
        assert priv_pkg["kind"] == "package"
        assert priv_pkg["visibility"] == "private"

        staged_cnt = int(
            conn.execute("SELECT COUNT(*) FROM pending WHERE status = 'staged'").fetchone()[0]
        )
        assert staged_cnt == 1

        fb_vals = {
            str(r["feedback"])
            for r in conn.execute(
                "SELECT DISTINCT feedback FROM digest_items WHERE feedback IS NOT NULL"
            )
        }
        assert fb_vals == {"acted", "useful", "not_useful"}

        prior_usage_runs = int(
            conn.execute(
                "SELECT COUNT(*) FROM runs WHERE status = 'ok' AND usage_json IS NOT NULL"
            ).fetchone()[0]
        )
        assert prior_usage_runs == 2
    finally:
        conn.close()

    # Partial state holds run.lock and has no applied verdict marker
    partial_lock = tmp_path / "a_partial" / "state" / "run.lock"
    assert partial_lock.is_file()
    with pytest.raises(ValueError, match="unsupported builder state"):
        radar_db_builder.build(tmp_path / "bad_state", state="invalid")  # type: ignore[arg-type]


def test_digest_goldens_changed_since_fenced_injection_and_privacy(tmp_path: Path) -> None:
    """Step 2: daily/weekly/monthly/quiet equal goldens; unchanged item omitted; injection
    text only inside fenced blocks; digest under run_root contains private-project-1 but
    not real private project/package names or paths; exposure reports referenced by alert id only.
    """
    for state_name in ("daily", "weekly", "monthly", "quiet"):
        ctx, run_id = radar_db_builder.build_ctx(
            tmp_path / f"run_{state_name}",
            state=state_name,  # type: ignore[arg-type]
        )
        res = render.render_run(ctx, run_id)
        assert res["ok"] is True
        assert res["status"] == "ok"

        digest_path = Path(res["digest_path"])
        assert digest_path.is_file()
        assert digest_path.resolve().is_relative_to(ctx.paths.run_root.resolve())
        assert not (ctx.paths.sync / "digest.md").exists()

        rendered_md = digest_path.read_text(encoding="utf-8")
        golden_md = (GOLDEN_DIR / f"digest_{state_name}.md").read_text(encoding="utf-8")
        assert rendered_md == golden_md, f"Digest mismatch for state={state_name}"

        # Stored in digests table and FTS
        db_row = ctx.db.execute(
            "SELECT markdown FROM digests WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        assert db_row is not None
        assert str(db_row["markdown"]) == rendered_md

        fts_hit = ctx.db.execute(
            "SELECT d.run_id FROM digests_fts JOIN digests d ON d.rowid = digests_fts.rowid "
            "WHERE digests_fts MATCH ? AND d.run_id = ?",
            ("Coverage", run_id),
        ).fetchone()
        assert fts_hit is not None

        if state_name != "quiet":
            # Verify Spec 09 § Digest section ordering [FMT-03]
            expected_headings = [
                f"# Tech Radar — 2026-11-02 ({state_name})",
                "**TL;DR**",
                "## Security",
                "## Lifecycle",
                "## Features you could use",
                "## Contenders",
                "## Horizon & techniques",
                "## Pricing",
                "## Decisions due",
                *(["## Precision", "## Usage"] if state_name == "monthly" else []),
                "## Coverage",
            ]
            positions = [rendered_md.index(h) for h in expected_headings]
            assert positions == sorted(positions), f"Out-of-order headings in {state_name}"

            # R-0002 was already in prior digest with no new ring_history -> omitted
            assert "### R-0002" not in rendered_md
            assert "| R-0002 |" not in rendered_md

            # Daily vs weekly/monthly horizon score threshold (daily >= 85, weekly/monthly >= 70)
            if state_name == "daily":
                assert "### R-0005" not in rendered_md
                assert "### R-0007" not in rendered_md
            else:
                assert "### R-0005" in rendered_md
                assert "### R-0007" in rendered_md
                assert "| R-0005 | fetchaller |" in rendered_md
                assert "### Watch ring" in rendered_md

            # Deep-research hint & unverified install command warning present
            assert '/code-quality:deep-research "uv-fast-pack' in rendered_md
            assert "Install (unverified third-party code — review before installing):" in (
                rendered_md
            )

            # Injection text only inside fenced code blocks
            _assert_fenced_only(rendered_md, "ignore previous instructions")
            _assert_fenced_only(rendered_md, "touch CANARY")
            _assert_fenced_only(rendered_md, "https://canary.invalid/")

            # Private aliasing (A8)
            assert "private-project-1" in rendered_md
            assert "secret-widget" not in rendered_md
            assert "~/Projects/secret-widget" not in rendered_md
            assert "/Users/example-user/Projects/secret-widget" not in rendered_md
            assert "secret-internal-pkg" not in rendered_md

            # Exposure reports referenced by alert id only, never by file path
            assert "radar.py exposure --show A-1" in rendered_md
            assert "radar.py exposure --show A-2" in rendered_md
            assert "exposure/1.md" not in rendered_md
            assert "exposure/2.md" not in rendered_md

    # Verify Coverage flags for non-default model and settings-prefix mismatch
    ctx_warn, run_warn = radar_db_builder.build_ctx(tmp_path / "run_warn", state="daily")
    ctx_warn.db.execute(
        "UPDATE runs SET model = 'claude-sonnet-4-5' WHERE run_id = ?",
        (run_warn,),
    )
    ctx_warn.db.commit()
    ctx_warn.paths.radar_cmd.write_text(
        "/opt/homebrew/bin/uv run --script --locked --isolated /app/radar.py\n",
        encoding="utf-8",
    )
    claude_dir = ctx_warn.paths.run_root / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / "settings.json").write_text(
        json.dumps(
            {
                "permissions": {
                    "allow": ["Bash(/wrong/prefix/radar.py plan-run *)"],
                }
            }
        ),
        encoding="utf-8",
    )
    warn_md, _, _ = render.render_digest(ctx_warn, run_warn)
    assert "model warning: expected gemini-4-argon, got claude-sonnet-4-5" in warn_md
    assert "settings-prefix mismatch" in warn_md


def test_projections_goldens_privacy_inventory_project_alerts_and_decisions_roundtrip(
    tmp_path: Path,
    replay_http: Callable[..., Any],
    replay_gh: Callable[..., Any],
    replay_procs: Callable[..., Any],
) -> None:
    """Step 3: TECH_RADAR/DECISIONS equal goldens; TECH_RADAR.md has no private name;
    INVENTORY.md has 4 context sections and maps private-project-1 to its display path;
    project-alerts.json uses normalized remote URLs and script-composed titles only (A24);
    DECISIONS.md round-trip stages on plan-run, applies on confirm, and re-renders.
    """
    ctx, run_id = radar_db_builder.build_ctx(tmp_path / "proj", state="weekly")
    ctx.http.transport = replay_http()
    ctx.gh.transport = replay_gh()
    ctx.procs.transport = replay_procs()

    # Seed TASTE_PROFILE.md and WATCHLIST.md sentinel content to verify render never overwrites them
    taste_sentinel = "# Sentinel Taste Profile\n"
    watch_sentinel = "# Sentinel Watchlist\n"
    (ctx.paths.sync / "TASTE_PROFILE.md").write_text(taste_sentinel, encoding="utf-8")
    (ctx.paths.sync / "WATCHLIST.md").write_text(watch_sentinel, encoding="utf-8")

    res = render.render_run(ctx, run_id)
    assert res["ok"] is True

    assert (ctx.paths.sync / "TASTE_PROFILE.md").read_text(encoding="utf-8") == taste_sentinel
    assert (ctx.paths.sync / "WATCHLIST.md").read_text(encoding="utf-8") == watch_sentinel

    tech_radar_md = (ctx.paths.sync / "TECH_RADAR.md").read_text(encoding="utf-8")
    decisions_md = (ctx.paths.sync / "DECISIONS.md").read_text(encoding="utf-8")
    inventory_md = (ctx.paths.sync / "INVENTORY.md").read_text(encoding="utf-8")

    assert tech_radar_md == (GOLDEN_DIR / "TECH_RADAR.md").read_text(encoding="utf-8")
    assert decisions_md == (GOLDEN_DIR / "DECISIONS.md").read_text(encoding="utf-8")
    assert (
        db.kv_get(ctx.db, "decisions_md_hash")
        == hashlib.sha256(decisions_md.encode("utf-8")).hexdigest()
    )

    # TECH_RADAR.md contains no private project or package names
    assert "secret-widget" not in tech_radar_md
    assert "secret-internal-pkg" not in tech_radar_md
    assert "_Private packages in this quadrant: 1_" in tech_radar_md

    # INVENTORY.md has one section per context type with expected counts and alias mapping
    assert "## Project Alias Map" in inventory_md
    assert "| `private-project-1` | private | `~/Projects/secret-widget` |" in inventory_md
    assert "## System (1)" in inventory_md
    assert "## Projects (5)" in inventory_md
    assert "## Harness (2)" in inventory_md
    assert "## Interests (1)" in inventory_md
    assert "secret-internal-pkg" in inventory_md

    # project-alerts.json written to both sync and state dirs with script-composed titles (A24)
    sync_alerts_file = ctx.paths.sync / "project-alerts.json"
    state_alerts_file = ctx.paths.state / "project-alerts.json"
    assert sync_alerts_file.is_file()
    assert state_alerts_file.is_file()
    assert sync_alerts_file.read_text(encoding="utf-8") == state_alerts_file.read_text(
        encoding="utf-8"
    )

    alerts_obj = json.loads(sync_alerts_file.read_text(encoding="utf-8"))
    projects_map = alerts_obj["projects"]
    assert set(projects_map.keys()) == {
        radar_db_builder.PUBLIC_PROJECT_KEY,
        radar_db_builder.PRIVATE_PROJECT_KEY,
    }
    raw_alerts_str = json.dumps(alerts_obj)
    # Injection-laden signal title must NEVER appear in project-alerts.json
    assert "IGNORE PREVIOUS INSTRUCTIONS" not in raw_alerts_str
    assert "touch CANARY" not in raw_alerts_str

    pub_alerts = projects_map[radar_db_builder.PUBLIC_PROJECT_KEY]
    assert len(pub_alerts) >= 4
    for pa in pub_alerts:
        assert set(pa.keys()) == {"tier", "kind", "title", "item_or_alert_id", "url"}
        assert pa["url"].startswith("https://")
    chatter_entry = next(a for a in pub_alerts if a["kind"] == "chatter")
    assert "unconfirmed — see radar" in chatter_entry["title"]
    assert "news.ycombinator.com" in chatter_entry["title"]
    incident_entry = next(a for a in pub_alerts if a["kind"] == "incident")
    assert "(installed version not affected)" in incident_entry["title"]

    # DECISIONS.md round-trip: edit R-0003 cell -> plan-run stages -> confirm -> re-render
    edited_decisions = decisions_md.replace(
        "| R-0003 | uv-fast-pack | trial | — | — |  |",
        "| R-0003 | uv-fast-pack | trial | — | — | snooze 30d |",
    )
    assert edited_decisions != decisions_md
    (ctx.paths.sync / "DECISIONS.md").write_text(edited_decisions, encoding="utf-8")

    # Seed fixture home installed_plugins so plan-run's app sync succeeds in test env
    import fixture_home as fixture_home_mod

    fixture_home_mod.build(tmp_path / "proj", scenario="baseline")
    ctx_next = radar_db_builder.context.build(
        transports=(replay_http(), replay_gh(), replay_procs()),
        now="2026-11-03T07:00:00Z",
    )
    plan_res = runs.plan_run(ctx_next, requested="daily")
    assert plan_res["ok"] is True
    assert plan_res["decisions_staged"] == 1

    sink_file = tmp_path / "proj" / "notify_sink.log"
    os.environ["TECH_RADAR_REPLAY_DIR"] = str(TESTS_DIR / "fixtures")
    os.environ["TECH_RADAR_NOTIFY_SINK"] = str(sink_file)
    pending.request_confirm(ctx_next)
    sink_text = sink_file.read_text(encoding="utf-8").strip().splitlines()[-1]
    code = sink_text.split("tech-radar code ", 1)[1].split(":", 1)[0].strip()
    confirm_res = pending.confirm(ctx_next, code)
    assert confirm_res["ok"] is True

    render.render_decisions_md(ctx_next)
    rerendered_dec = (ctx_next.paths.sync / "DECISIONS.md").read_text(encoding="utf-8")
    assert "| R-0003 | uv-fast-pack | trial | snooze | 2026-12-03 |  |" in rerendered_dec
    lock.release(plan_res["run_id"], state_dir=ctx_next.paths.state)


def test_exports_byor_json_and_self_contained_html(tmp_path: Path) -> None:
    """Step 4: BYOR objects have exact keys name, ring, quadrant, isNew, description;
    HTML parses with html.parser, contains no <script, <link, src=, or url(;
    every <a> has rel='noreferrer'; run_root copy contains no private name.
    """
    ctx, run_id = radar_db_builder.build_ctx(tmp_path / "exp", state="weekly")
    res = render.render_run(ctx, run_id)
    assert res["ok"] is True

    byor_path = Path(res["byor_path"])
    assert byor_path.is_file()
    byor_text = byor_path.read_text(encoding="utf-8")
    assert byor_text == (GOLDEN_DIR / "radar.byor.json").read_text(encoding="utf-8")

    byor_arr = json.loads(byor_text)
    assert isinstance(byor_arr, list) and len(byor_arr) == 10
    for obj in byor_arr:
        assert isinstance(obj, dict)
        assert set(obj.keys()) == {"name", "ring", "quadrant", "isNew", "description"}
        assert obj["ring"] in ("adopt", "trial", "assess", "hold")
        assert isinstance(obj["isNew"], bool)
        assert isinstance(obj["name"], str | int) and isinstance(obj["description"], str)

    watch_exported = next(x for x in byor_arr if x["name"] == "experimental-agent-tui")
    assert watch_exported["ring"] == "assess"
    assert watch_exported["description"].startswith("[watch] ")

    # HTML export in sync dir and run_root
    run_html_path = Path(res["html_path"])
    sync_html_path = ctx.paths.sync / "radar.html"
    assert run_html_path.is_file()
    assert sync_html_path.is_file()
    assert run_html_path.resolve().is_relative_to(ctx.paths.run_root.resolve())

    html_text = run_html_path.read_text(encoding="utf-8")
    low_html = html_text.lower()
    assert "<script" not in low_html
    assert "<link" not in low_html
    assert "src=" not in low_html
    assert "url(" not in low_html
    assert "http://" not in low_html
    assert "secret-widget" not in html_text
    assert "~/Projects/secret-widget" not in html_text
    assert "/Users/example-user/Projects/secret-widget" not in html_text
    assert "secret-internal-pkg" not in html_text

    parser = _StrictRadarHTMLParser()
    parser.feed(html_text)
    assert parser.svg_count == 1
    assert parser.circle_count >= 5  # 5 concentric rings + item blips
    assert parser.title_count >= 2  # page title + svg title + blip hover titles
    assert len(parser.anchors) >= 1
    for a_attrs in parser.anchors:
        assert a_attrs.get("rel") == "noreferrer"
        href = a_attrs.get("href") or ""
        assert href.startswith("https://")


def test_snapshot_partial_run_sync_unavailable_and_secret_bytescan(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Step 5:
    - snapshot opens as SQLite, mode 0600, no http_cache, sha in kv;
    - partial state renders (security/lifecycle sections present), run status degraded,
      run.lock removed;
    - sync dir removed before render -> digest stored in DB and written under run_root,
      run status degraded, Coverage says 'sync dir unavailable';
    - Python byte scan of sync dir, run_root, and digests finds none of the builder's
      seeded secrets.
    """
    # 1. Snapshot verification on daily build
    ctx, run_id = radar_db_builder.build_ctx(tmp_path / "snap", state="daily")
    assert ctx.cache_db is not None
    db.cache_put(
        ctx.cache_db,
        key="k1",
        url="https://osv.dev/v1/query",
        etag=None,
        last_modified=None,
        fetched_at=radar_db_builder.FIXED_NOW,
        status=200,
        body=b"{}",
    )

    res = render.render_run(ctx, run_id)
    snap_path = Path(res["snapshot_path"])
    assert snap_path.is_file()
    mode = stat.S_IMODE(snap_path.stat().st_mode)
    assert mode == 0o600
    expected_sha = hashlib.sha256(snap_path.read_bytes()).hexdigest()
    assert res["snapshot_sha"] == expected_sha
    assert db.kv_get(ctx.db, "last_snapshot_sha") == expected_sha

    snap_conn = sqlite3.connect(str(snap_path))
    try:
        tables = {
            str(r[0])
            for r in snap_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "items" in tables
        assert "digests" in tables
        assert "http_cache" not in tables
    finally:
        snap_conn.close()

    # Refuses symlinked sync dir
    from tech_radar import errors, snapshot

    sym_sync = tmp_path / "sym_sync"
    sym_sync.symlink_to(ctx.paths.sync)
    with pytest.raises(errors.WriteDenied):
        snapshot.create_snapshot(ctx, sync_dir=sym_sync)

    # Byte scan of sync dir, run_root, state dir, and digests table for seeded secret substrings
    canaries = secret_samples.raw_canary_substrings()
    scan_files: list[Path] = []
    for root_dir in (ctx.paths.sync, ctx.paths.run_root, ctx.paths.state):
        for p in root_dir.rglob("*"):
            if p.is_file():
                scan_files.append(p)
    assert len(scan_files) >= 5
    for fpath in scan_files:
        raw_bytes = fpath.read_bytes()
        for s_name, canary in canaries.items():
            assert canary.encode("utf-8") not in raw_bytes, (
                f"Secret canary {s_name!r} leaked in {fpath}"
            )

    for d_row in ctx.db.execute("SELECT markdown FROM digests").fetchall():
        md_bytes = str(d_row["markdown"]).encode("utf-8")
        for s_name, canary in canaries.items():
            assert canary.encode("utf-8") not in md_bytes, (
                f"Secret canary {s_name!r} leaked in digests table"
            )

    # 2. Partial run via CLI: renders security/lifecycle, finalizes degraded, removes run.lock
    ctx_part, run_part = radar_db_builder.build_ctx(tmp_path / "part", state="partial")
    lock_file = ctx_part.paths.state / "run.lock"
    assert lock_file.is_file()

    rc = cli.main(["render", "--run", run_part])
    out = json.loads(capsys.readouterr().out.strip())
    assert rc == 0
    assert out["ok"] is True
    assert out["status"] == "degraded"
    assert not lock_file.exists(), "Expected run.lock to be released by render"

    part_md = Path(out["digest_path"]).read_text(encoding="utf-8")
    assert "## Security" in part_md
    assert "MAL-2026-9999" in part_md
    assert "## Lifecycle" in part_md
    assert "LIFE-01" in part_md

    part_run_row = ctx_part.db.execute(
        "SELECT status, finished_at FROM runs WHERE run_id = ?",
        (run_part,),
    ).fetchone()
    assert part_run_row["status"] == "degraded"
    assert part_run_row["finished_at"] is not None

    # 3. Sync dir removed before render -> digest still stored & written under run_root,
    #    run status degraded, Coverage says 'sync dir unavailable'
    ctx_nosync, run_nosync = radar_db_builder.build_ctx(tmp_path / "nosync", state="daily")
    shutil.rmtree(ctx_nosync.paths.sync)
    assert not ctx_nosync.paths.sync.exists()

    rc_ns = cli.main(["render", "--run", run_nosync])
    out_ns = json.loads(capsys.readouterr().out.strip())
    assert rc_ns == 0
    assert out_ns["ok"] is True
    assert out_ns["status"] == "degraded"
    assert out_ns["sync_available"] is False
    assert not ctx_nosync.paths.sync.exists()

    ns_digest_path = Path(out_ns["digest_path"])
    assert ns_digest_path.is_file()
    ns_md = ns_digest_path.read_text(encoding="utf-8")
    assert "sync dir unavailable" in ns_md

    ns_db_row = ctx_nosync.db.execute(
        "SELECT markdown FROM digests WHERE run_id = ?",
        (run_nosync,),
    ).fetchone()
    assert ns_db_row is not None
    assert "sync dir unavailable" in str(ns_db_row["markdown"])
