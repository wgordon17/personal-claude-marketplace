"""Tests for items.promote, A5 prescore relevance filter, gates, red flags, and scoring."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from tech_radar import context, evaluate, items
from tech_radar.sources import registries
from tech_radar.sources.base import RawSignal, persist_raw_signal

FIXED_NOW = "2026-10-29T07:00:00Z"


def _make_ctx(
    replay_http: Callable[..., Any],
    replay_gh: Callable[..., Any],
    replay_procs: Callable[..., Any],
) -> context.Ctx:
    ctx = context.build(
        transports=(replay_http(), replay_gh(), replay_procs()),
        now=FIXED_NOW,
    )
    # Seed profile with categories, anti-interests, and watchlist
    sync_dir = ctx.paths.sync
    (sync_dir / "TASTE_PROFILE.md").write_text(
        "# Taste Profile\n\n"
        "## Categories\n"
        "- python-tooling\n"
        "- cli-dev-tools\n"
        "- ai-agents-harness\n\n"
        "## Anti-interests\n"
        "- Heavy Electron wrappers\n"
        "- blockchain-crypto\n\n"
        "## Languages\n"
        "- python\n"
        "- rust\n",
        encoding="utf-8",
    )
    (sync_dir / "WATCHLIST.md").write_text(
        "# Watchlist\n\n- astral-sh/ruff\n- example-org/watched-tool\n",
        encoding="utf-8",
    )
    ctx.reload_profile()
    return ctx


def test_promote_relevance_filter_anti_interest_repeats_and_50_cap(
    replay_http: Callable[..., Any],
    replay_gh: Callable[..., Any],
    replay_procs: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """promote creates one candidate per new relevant hint, filters anti-interests/non-matching,
    and caps daily fetches at 50 with deferred=10 on 60 qualifying candidates.
    """
    ctx = _make_ctx(replay_http, replay_gh, replay_procs)

    # Pre-seed an in-use public entity so contender signals referencing it match
    ctx.db.execute(
        "INSERT INTO entities(entity_id, kind, ecosystem, name, repo, visibility, created_at) "
        "VALUES ('pkg:pypi/ruff', 'cli', 'pypi', 'ruff', 'astral-sh/ruff', 'public', ?)",
        (FIXED_NOW,),
    )
    ctx.db.execute(
        "INSERT INTO usages(entity_id, context, version, direct, first_seen, last_seen) "
        "VALUES ('pkg:pypi/ruff', 'system:uv', '0.8.0', 1, ?, ?)",
        (FIXED_NOW, FIXED_NOW),
    )
    ctx.db.commit()

    fetched_entities: list[str] = []

    def _fake_fetch_metrics(
        _ctx: context.Ctx, entity: str | dict[str, Any], **_kw: Any
    ) -> dict[str, Any]:
        eid = entity if isinstance(entity, str) else str(entity["entity_id"])
        fetched_entities.append(eid)
        return {
            "entity_id": eid,
            "osv_available": True,
            "has_mal_advisory": False,
            "has_unpatched_high_crit": False,
            "macos_arm64": "pass",
            "last_commit_at": "2026-10-20T00:00:00Z",
            "last_release_at": "2026-10-01T00:00:00Z",
            "first_release_at": "2025-01-01T00:00:00Z",
            "stars": 500,
        }

    monkeypatch.setattr(registries, "fetch_metrics", _fake_fetch_metrics)
    monkeypatch.setattr(items.classify, "visibility", lambda _c, _eid, **_k: "public")

    # 1. Anti-interest signal (filtered and counted)
    persist_raw_signal(
        ctx,
        RawSignal(
            source_id="hn_algolia",
            type="horizon",
            external_id="anti-1",
            title="New blockchain-crypto wallet CLI",
            url="https://github.com/example-org/crypto-cli",
            entity_hint="gh:example-org/crypto-cli",
            untrusted_excerpt="A blockchain-crypto tool.",
            dedupe_key="hn|horizon|anti-1",
        ),
    )

    # 2. Signal matching no category, watchlist/starred/taste term, or in-use entity
    persist_raw_signal(
        ctx,
        RawSignal(
            source_id="devto",
            type="horizon",
            external_id="irrel-1",
            title="Gardening spreadsheet templates for 2026",
            url="https://github.com/example-org/garden-sheets",
            entity_hint="svc:garden-sheets",
            untrusted_excerpt="Track tomato yields.",
            dedupe_key="devto|horizon|irrel-1",
        ),
    )

    # 3. 60 qualifying relevant candidates (plus a duplicate signal for cand-001)
    for i in range(1, 61):
        slug = f"example-org/py-fast-{i:03d}"
        persist_raw_signal(
            ctx,
            RawSignal(
                source_id="gh_search_rising",
                type="horizon",
                external_id=slug,
                title=f"{slug}: python-tooling helper {i}",
                url=f"https://github.com/{slug}",
                entity_hint=f"gh:{slug}",
                untrusted_excerpt="Fast python-tooling package.",
                metrics={"stars": 1000 - i, "star_velocity": float(100 - i)},
                dedupe_key=f"gh_search_rising|horizon|{slug}",
            ),
        )

    # Repeat signal for cand-001 -> must not create a second candidate item
    persist_raw_signal(
        ctx,
        RawSignal(
            source_id="hn_algolia",
            type="horizon",
            external_id="dup-001",
            title="Discussion of example-org/py-fast-001 python-tooling",
            url="https://github.com/example-org/py-fast-001",
            entity_hint="gh:example-org/py-fast-001",
            untrusted_excerpt="Great python-tooling.",
            dedupe_key="hn|horizon|dup-001",
        ),
    )

    stats = items.promote(ctx)
    assert stats["suppressed"]["anti_interest"] == 1
    assert stats["suppressed"]["not_relevant"] == 1
    assert stats["candidates_promoted"] == 50
    assert stats["candidates_fetched"] == 50
    assert stats["deferred"] == 10
    assert len(fetched_entities) == 50

    # Re-running promote creates 0 new candidate items for repeats
    stats2 = items.promote(ctx)
    assert stats2["candidates_promoted"] == 0

    cand_cnt = int(
        ctx.db.execute("SELECT COUNT(*) FROM items WHERE state = 'candidate'").fetchone()[0]
    )
    assert cand_cnt == 50


def test_star_velocity_from_two_metric_observations(
    replay_http: Callable[..., Any],
    replay_gh: Callable[..., Any],
    replay_procs: Callable[..., Any],
) -> None:
    """Star velocity = (stars_now - stars_prev)/days when >=2 metric observations exist."""
    ctx = _make_ctx(replay_http, replay_gh, replay_procs)
    eid = "gh:example-org/vel-repo"
    items.ensure_entity(ctx, eid, classify_visibility=False)
    ctx.db.execute(
        "INSERT INTO metrics(entity_id, metric, value, observed_at) VALUES (?, 'stars', ?, ?)",
        (eid, 100.0, "2026-10-19T07:00:00Z"),
    )
    ctx.db.execute(
        "INSERT INTO metrics(entity_id, metric, value, observed_at) VALUES (?, 'stars', ?, ?)",
        (eid, 250.0, "2026-10-29T07:00:00Z"),
    )
    ctx.db.commit()

    vel = evaluate.compute_star_velocity(ctx, eid)
    assert vel == pytest.approx(15.0)


def test_verified_candidate_reaches_trial_and_osv_unavailable_caps_assess(
    replay_http: Callable[..., Any],
    replay_gh: Callable[..., Any],
    replay_procs: Callable[..., Any],
) -> None:
    """Fully verified candidate reaches TRIAL; the same with OSV unavailable caps at ASSESS."""
    ctx = _make_ctx(replay_http, replay_gh, replay_procs)
    eid = "pkg:pypi/verified-pkg"
    items.ensure_entity(ctx, eid, classify_visibility=False)
    ctx.db.execute("UPDATE entities SET visibility = 'public' WHERE entity_id = ?", (eid,))
    item_id, _ = items.ensure_item(ctx, eid, in_use=False, state="candidate")

    base_inputs = {
        "osv_available": True,
        "has_mal_advisory": False,
        "has_unpatched_high_crit": False,
        "macos_arm64": "pass",
        "last_commit_at": "2026-10-25T00:00:00Z",
        "last_release_at": "2026-10-10T00:00:00Z",
        "first_release_at": "2025-01-01T00:00:00Z",
        "stars": 1200,
        "downloads_30d": 50000,
        "practitioner_signal_count": 3,
    }
    verdict_payload = {
        "ring": "trial",
        "action": "surface",
        "fit": 85,
        "quality_adj": 10,
        "confidence": "high",
        "why": "Fits Python tooling well.",
        "effort": "S",
        "llm_fields": {
            "cloud_code_upload": "no",
            "arbitrary_shell": "no",
            "broad_credentials": "no",
            "telemetry_no_optout": "no",
        },
    }

    fin_ok = evaluate.finalize(ctx, item_id, verdict_payload, gate_inputs=base_inputs)
    assert fin_ok["ring"] == "trial"
    assert fin_ok["cap"] is None

    # Same candidate with OSV unavailable -> GATE-05/06 unknown -> caps at ASSESS (Spec 12 A5)
    no_osv_inputs = {**base_inputs, "osv_available": False}
    fin_no_osv = evaluate.finalize(ctx, item_id, verdict_payload, gate_inputs=no_osv_inputs)
    assert fin_no_osv["gates"]["GATE-05"] == "unknown"
    assert fin_no_osv["gates"]["GATE-06"] == "unknown"
    assert fin_no_osv["cap"] == "assess"
    assert fin_no_osv["ring"] == "assess"


@pytest.mark.parametrize(
    ("case_name", "override_inputs", "override_llm", "expected_cap", "expected_ring"),
    [
        (
            "gate_03_stale_commit",
            {"last_commit_at": "2025-01-01T00:00:00Z"},
            {},
            "assess",
            "assess",
        ),
        (
            "gate_04_stale_release",
            {"last_release_at": "2024-01-01T00:00:00Z"},
            {},
            "assess",
            "assess",
        ),
        ("gate_05_unpatched_vuln", {"has_unpatched_high_crit": True}, {}, "hold", "hold"),
        ("gate_06_mal_history", {"has_mal_advisory": True}, {}, "hold", "hold"),
        (
            "gate_07_young_repo",
            {"first_release_at": "2026-09-15T00:00:00Z"},
            {},
            "assess",
            "assess",
        ),
        (
            "gate_08_cooldown_3d",
            {"last_release_at": "2026-10-26T00:00:00Z"},
            {},
            "assess",
            "assess",
        ),
        ("gate_10_arm64_unknown", {"macos_arm64": "unknown"}, {}, "assess", "assess"),
        ("gate_14_cloud_upload_yes", {}, {"cloud_code_upload": "yes"}, "hold", "hold"),
        ("gate_14_cloud_upload_unknown", {}, {"cloud_code_upload": "unknown"}, "assess", "assess"),
        (
            "redf_02_hype_spike",
            {"star_velocity_zscore": 4.5, "contributors_count": 1},
            {},
            "assess",
            "assess",
        ),
        ("redf_03_under_30d", {"first_release_at": "2026-10-15T00:00:00Z"}, {}, "assess", "assess"),
        ("redf_05_arbitrary_shell", {}, {"arbitrary_shell": "yes"}, "hold", "hold"),
        ("redf_06_broad_credentials", {}, {"broad_credentials": "yes"}, "assess", "assess"),
        ("redf_07_supply_chain_12m", {"supply_chain_incident_12m": True}, {}, "hold", "hold"),
        ("redf_08_telemetry_no_optout", {}, {"telemetry_no_optout": "yes"}, "assess", "assess"),
        (
            "redf_09_ai_slop_readme",
            {"readme_bytes": 40000, "has_tests": False},
            {},
            "assess",
            "assess",
        ),
        (
            "redf_10_closed_source_cli",
            {"source_available": False, "license": "proprietary"},
            {},
            "hold",
            "hold",
        ),
    ],
)
def test_each_gate_and_red_flag_yields_expected_cap(
    case_name: str,
    override_inputs: dict[str, Any],
    override_llm: dict[str, str],
    expected_cap: str,
    expected_ring: str,
    replay_http: Callable[..., Any],
    replay_gh: Callable[..., Any],
    replay_procs: Callable[..., Any],
) -> None:
    """Every Spec 05 §1 gate and red flag yields its exact cap."""
    ctx = _make_ctx(replay_http, replay_gh, replay_procs)
    eid = f"brew:formula/{case_name.replace('_', '-')}"
    items.ensure_entity(ctx, eid, classify_visibility=False)
    ctx.db.execute(
        "UPDATE entities SET visibility = 'public', kind = 'cli' WHERE entity_id = ?", (eid,)
    )
    item_id, _ = items.ensure_item(ctx, eid, in_use=False, state="candidate")

    clean_inputs: dict[str, Any] = {
        "osv_available": True,
        "has_mal_advisory": False,
        "has_unpatched_high_crit": False,
        "macos_arm64": "pass",
        "last_commit_at": "2026-10-25T00:00:00Z",
        "last_release_at": "2026-10-10T00:00:00Z",
        "first_release_at": "2025-01-01T00:00:00Z",
        "stars": 1000,
        "downloads_30d": 25000,
        "practitioner_signal_count": 3,
        **override_inputs,
    }
    verdict_payload = {
        "ring": "trial",
        "action": "surface",
        "fit": 90,
        "quality_adj": 5,
        "confidence": "high",
        "why": f"Testing {case_name}",
        "effort": "S",
        "llm_fields": {
            "cloud_code_upload": "no",
            "arbitrary_shell": "no",
            "broad_credentials": "no",
            "telemetry_no_optout": "no",
            **override_llm,
        },
    }

    res = evaluate.finalize(ctx, item_id, verdict_payload, gate_inputs=clean_inputs)
    assert res["cap"] == expected_cap, f"{case_name}: expected cap {expected_cap}, got {res['cap']}"
    assert res["ring"] == expected_ring, (
        f"{case_name}: expected ring {expected_ring}, got {res['ring']}"
    )


def test_reported_only_criteria_change_reported_json_but_never_ring_or_total(
    replay_http: Callable[..., Any],
    replay_gh: Callable[..., Any],
    replay_procs: Callable[..., Any],
) -> None:
    """Changing reported-only inputs (GPL license, bus factor 1, no SECURITY.md) changes
    reported_json but never affects ring or total score (Spec 05 §1).
    """
    ctx = _make_ctx(replay_http, replay_gh, replay_procs)
    eid = "pkg:pypi/reported-check"
    items.ensure_entity(ctx, eid, classify_visibility=False)
    ctx.db.execute("UPDATE entities SET visibility = 'public' WHERE entity_id = ?", (eid,))
    item_id, _ = items.ensure_item(ctx, eid, in_use=False, state="candidate")

    inputs_a = {
        "osv_available": True,
        "has_mal_advisory": False,
        "has_unpatched_high_crit": False,
        "macos_arm64": "pass",
        "last_commit_at": "2026-10-25T00:00:00Z",
        "last_release_at": "2026-10-10T00:00:00Z",
        "first_release_at": "2025-01-01T00:00:00Z",
        "stars": 1500,
        "downloads_30d": 60000,
        "practitioner_signal_count": 3,
        "license": "MIT",
        "bus_factor": 4,
        "top_contributor_share": 0.35,
        "has_security_md": True,
        "pipe_to_shell_only": False,
    }
    verdict_payload = {
        "ring": "trial",
        "action": "surface",
        "fit": 82,
        "quality_adj": 4,
        "confidence": "high",
        "why": "Solid utility.",
        "effort": "S",
        "llm_fields": {
            "cloud_code_upload": "no",
            "arbitrary_shell": "no",
            "broad_credentials": "no",
            "telemetry_no_optout": "no",
        },
    }

    res_a = evaluate.finalize(ctx, item_id, verdict_payload, gate_inputs=inputs_a)
    row_a = ctx.db.execute(
        "SELECT ring, total, reported_json FROM items WHERE item_id = ?",
        (item_id,),
    ).fetchone()

    inputs_b = {
        **inputs_a,
        "license": "GPL-3.0-only",
        "bus_factor": 1,
        "top_contributor_share": 0.98,
        "has_security_md": False,
        "pipe_to_shell_only": True,
    }
    res_b = evaluate.finalize(ctx, item_id, verdict_payload, gate_inputs=inputs_b)
    row_b = ctx.db.execute(
        "SELECT ring, total, reported_json FROM items WHERE item_id = ?",
        (item_id,),
    ).fetchone()

    assert row_a["ring"] == row_b["ring"] == "trial"
    assert float(row_a["total"]) == pytest.approx(float(row_b["total"]))
    assert res_a["total"] == pytest.approx(res_b["total"])
    assert row_a["reported_json"] != row_b["reported_json"]

    rep_b = json.loads(str(row_b["reported_json"]))
    assert rep_b["GATE-01"] == "GPL-3.0-only"
    assert rep_b["license_permissive"] is False
    assert rep_b["GATE-16"] == 1
    assert rep_b["SCORE-08"] is False
    assert rep_b["REDF-04"] is True

    # Prescore sets LLM gate GATE-14 to unknown and caps at assess
    pre = evaluate.prescore(ctx, item_id, gate_inputs=inputs_a)
    assert pre["gates"]["GATE-14"] == "unknown"
    assert pre["cap"] == "assess"

    # Under-30d repo (REDF-03) with low total (<70) demotes ring to 'watch'
    low_fit_verdict = {
        **verdict_payload,
        "fit": 10,
        "quality_adj": -15,
    }
    young_low_inputs = {
        **inputs_a,
        "first_release_at": "2026-10-20T00:00:00Z",
        "momentum_override": 20.0,
    }
    res_watch = evaluate.finalize(ctx, item_id, low_fit_verdict, gate_inputs=young_low_inputs)
    assert "REDF-03" in res_watch["red_flags"]
    assert isinstance(res_watch["total"], int | float) and res_watch["total"] < 70.0
    assert res_watch["ring"] == "watch"

    # DB-backed GATE-05/06/REDF-07 detection from confirmed compromise alert
    mal_eid = "pkg:pypi/db-mal-pkg"
    items.ensure_entity(ctx, mal_eid, classify_visibility=False)
    ctx.db.execute("UPDATE entities SET visibility = 'public' WHERE entity_id = ?", (mal_eid,))
    mal_item_id, _ = items.ensure_item(ctx, mal_eid, in_use=False, state="candidate")
    ctx.db.execute(
        "INSERT INTO alerts(tier, kind, confidence, entity_id, state, raised_at, dedupe_key) "
        "VALUES ('P0', 'compromise', 'confirmed', ?, 'open', ?, 'comp|db-mal-pkg')",
        (mal_eid, FIXED_NOW),
    )
    ctx.db.commit()
    res_db_mal = evaluate.finalize(ctx, mal_item_id, verdict_payload, gate_inputs=inputs_a)
    assert res_db_mal["gates"]["GATE-05"] == "fail"
    assert res_db_mal["gates"]["GATE-06"] == "fail"
    assert "REDF-07" in res_db_mal["red_flags"]
    assert res_db_mal["cap"] == "hold"
    assert res_db_mal["ring"] == "hold"
