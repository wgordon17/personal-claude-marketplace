"""Tests for security.evaluate (Spec 07 §1, §9, Spec 12 A1, A2, A3, A20, A28, A32)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tech_radar import context, db, http, inventory, lifecycle, procs, security, sources
from tech_radar.sources.base import RawSignal, SourceResult

FIXED_NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)


def _sha256_tree(directory: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(directory.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(directory))
        h.update(rel.encode("utf-8"))
        h.update(p.read_bytes())
    return h.hexdigest()


def test_compromised_scenario_litellm_p0_and_auto_exposure(
    fixture_home: Any,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
    monkeypatch: Any,
) -> None:
    fixture_home(scenario="compromised")
    monkeypatch.setattr(security, "UV_AUDIT_ENABLED", False)

    ctx = context.build(
        transports={
            "http": replay_http("osv_batch"),
            "gh": replay_gh(),
            "procs": replay_procs(),
        },
        now=FIXED_NOW,
    )
    inventory.collect(ctx)

    osv_res = sources.run_source(sources.get_source("osv_batch"), ctx)
    assert osv_res.ok is True

    new_ids = security.evaluate(ctx, [osv_res], mode="daily")
    assert len(new_ids) == 1

    rows = ctx.db.execute(
        "SELECT alert_id, tier, kind, confidence, state, notified_at, payload_json "
        "FROM alerts WHERE kind = 'compromise'"
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert str(row["tier"]) == "P0"
    assert str(row["confidence"]) == "confirmed"
    assert row["notified_at"] is None

    payload = json.loads(str(row["payload_json"]))
    exp_path = Path(str(payload["exposure_report"]))
    assert exp_path == ctx.paths.state / "exposure" / f"{row['alert_id']}.md"
    assert exp_path.is_file()
    assert payload.get("rotation_checklist")


def test_package_level_incident_30d_window_and_withdrawn(
    fixture_home: Any,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
    monkeypatch: Any,
) -> None:
    _ = fixture_home
    monkeypatch.setattr(security, "UV_AUDIT_ENABLED", False)

    ctx = context.build(
        transports={
            "http": replay_http("classify"),
            "gh": replay_gh(),
            "procs": replay_procs(),
        },
        now=FIXED_NOW,
    )
    inventory.collect(ctx)

    # 1. Baseline (litellm 1.106.2) + fresh litellm 1.107.0 MAL -> P1 incident, notified_at NULL
    fresh_mal = RawSignal(
        source_id="osv_batch",
        type="compromise",
        external_id="MAL-2026-9999",
        title="Malicious Package in litellm",
        url="https://osv.dev/vulnerability/MAL-2026-9999",
        published_at="2026-10-27T10:00:00Z",
        untrusted_excerpt="Malicious release 1.107.0",
        entity_hint="pkg:pypi/litellm",
        confidence="confirmed",
        payload={
            "advisory_id": "MAL-2026-9999",
            "is_mal": True,
            "affected_versions": ["1.107.0"],
            "published": "2026-10-27T10:00:00Z",
            "modified": "2026-10-27T12:00:00Z",
        },
    )
    security.evaluate(ctx, [SourceResult(source_id="osv_batch", signals=[fresh_mal])], mode="daily")

    p0_count = int(ctx.db.execute("SELECT COUNT(*) FROM alerts WHERE tier = 'P0'").fetchone()[0])
    assert p0_count == 0

    inc_rows = ctx.db.execute(
        "SELECT alert_id, tier, kind, notified_at, state, payload_json "
        "FROM alerts WHERE kind = 'incident'"
    ).fetchall()
    assert len(inc_rows) == 1
    assert str(inc_rows[0]["tier"]) == "P1"
    assert inc_rows[0]["notified_at"] is None
    inc_payload = json.loads(str(inc_rows[0]["payload_json"]))
    assert Path(str(inc_payload["exposure_report"])).is_file()

    # 2. Record marked withdrawn -> alert state becomes 'withdrawn'
    withdrawn_mal = RawSignal(
        source_id="osv_batch",
        type="compromise",
        external_id="MAL-2026-9999",
        title="Malicious Package in litellm",
        url="https://osv.dev/vulnerability/MAL-2026-9999",
        published_at="2026-10-27T10:00:00Z",
        untrusted_excerpt="Withdrawn advisory",
        entity_hint="pkg:pypi/litellm",
        confidence="confirmed",
        withdrawn=1,
        payload={
            "advisory_id": "MAL-2026-9999",
            "is_mal": True,
            "withdrawn": "2026-10-28T11:00:00Z",
            "affected_versions": ["1.107.0"],
        },
    )
    security.evaluate(
        ctx,
        [SourceResult(source_id="osv_batch", signals=[withdrawn_mal])],
        mode="daily",
    )
    st_row = ctx.db.execute(
        "SELECT state FROM alerts WHERE alert_id = ?",
        (inc_rows[0]["alert_id"],),
    ).fetchone()
    assert str(st_row["state"]) == "withdrawn"

    # 3. Same record dated 90 days ago -> incident created with notified_at set
    old_mal = RawSignal(
        source_id="osv_batch",
        type="compromise",
        external_id="MAL-2026-1111",
        title="Malicious Package in litellm (historical)",
        url="https://osv.dev/vulnerability/MAL-2026-1111",
        published_at="2026-07-01T10:00:00Z",
        untrusted_excerpt="Old incident in 1.0.0",
        entity_hint="pkg:pypi/litellm",
        confidence="confirmed",
        payload={
            "advisory_id": "MAL-2026-1111",
            "is_mal": True,
            "affected_versions": ["1.0.0"],
            "published": "2026-07-01T10:00:00Z",
            "modified": "2026-07-01T10:00:00Z",
        },
    )
    security.evaluate(ctx, [SourceResult(source_id="osv_batch", signals=[old_mal])], mode="daily")
    old_row = ctx.db.execute(
        "SELECT notified_at FROM alerts WHERE dedupe_key LIKE '%MAL-2026-1111%'"
    ).fetchone()
    assert old_row is not None
    assert old_row["notified_at"] == ctx.now_iso


def test_transitive_non_mal_vs_mal_and_chatter_precision_a28(
    fixture_home: Any,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
    monkeypatch: Any,
) -> None:
    _ = fixture_home
    monkeypatch.setattr(security, "UV_AUDIT_ENABLED", False)

    ctx = context.build(
        transports={
            "http": replay_http("classify"),
            "gh": replay_gh(),
            "procs": replay_procs(),
        },
        now=FIXED_NOW,
    )
    inventory.collect(ctx)

    # starlette==0.41.0 is transitive (direct=0) in public-app
    trans_cve = RawSignal(
        source_id="osv_batch",
        type="kev",
        external_id="CVE-2026-0001",
        title="Routine CVE in starlette",
        entity_hint="pkg:pypi/starlette",
        confidence="confirmed",
        payload={
            "advisory_id": "CVE-2026-0001",
            "is_mal": False,
            "affected_versions": ["0.41.0"],
            "direct": False,
        },
    )
    security.evaluate(
        ctx, [SourceResult(source_id="osv_batch", signals=[trans_cve])], mode="hourly"
    )
    assert (
        int(
            ctx.db.execute(
                "SELECT COUNT(*) FROM alerts WHERE entity_id = 'pkg:pypi/starlette'"
            ).fetchone()[0]
        )
        == 0
    )

    # Transitive with MAL -> P0 confirmed compromise
    trans_mal = RawSignal(
        source_id="osv_batch",
        type="compromise",
        external_id="MAL-2026-7777",
        title="Malicious Package in starlette",
        entity_hint="pkg:pypi/starlette",
        confidence="confirmed",
        payload={
            "advisory_id": "MAL-2026-7777",
            "is_mal": True,
            "affected_versions": ["0.41.0"],
            "direct": False,
        },
    )
    security.evaluate(
        ctx, [SourceResult(source_id="osv_batch", signals=[trans_mal])], mode="hourly"
    )
    star_alert = ctx.db.execute(
        "SELECT tier, kind, confidence FROM alerts WHERE entity_id = 'pkg:pypi/starlette'"
    ).fetchone()
    assert star_alert is not None
    assert str(star_alert["tier"]) == "P0"
    assert str(star_alert["kind"]) == "compromise"

    # Chatter matching (A28):
    # - blog item with pypi.org/project/litellm + "compromised" -> one P1 chatter alert
    # - generic post mentioning "react" -> none
    # - post mentioning private package `secret-widget-core` -> none
    ctx.db.execute(
        "INSERT INTO entities(entity_id, kind, ecosystem, name, visibility, created_at) "
        "VALUES ('pkg:npm/react', 'package', 'npm', 'react', 'public', '2026-10-28T00:00:00Z')"
    )
    ctx.db.execute(
        "INSERT INTO usages(entity_id, context, version, direct, first_seen, last_seen) "
        "VALUES ('pkg:npm/react', 'project:https://github.com/example-org/public-app', "
        "'19.0.0', 1, '2026-10-28T00:00:00Z', '2026-10-28T00:00:00Z')"
    )
    ctx.db.commit()

    chatter_signals = [
        RawSignal(
            source_id="socket_blog",
            type="compromise",
            external_id="blog-litellm",
            title="Researchers analyze compromised release",
            url="https://socket.dev/blog/post-1",
            untrusted_excerpt=(
                "See https://pypi.org/project/litellm for details on the compromised build."
            ),
            confidence="unconfirmed",
        ),
        RawSignal(
            source_id="hn_compromise",
            type="compromise",
            external_id="hn-react",
            title="How do you react when a server is compromised?",
            url="https://news.ycombinator.com/item?id=1",
            untrusted_excerpt="Developers react quickly to compromised servers.",
            confidence="unconfirmed",
        ),
        RawSignal(
            source_id="socket_blog",
            type="compromise",
            external_id="blog-private",
            title="Compromised package https://pypi.org/project/secret-widget-core",
            url="https://socket.dev/blog/post-2",
            untrusted_excerpt="pypi:secret-widget-core was compromised.",
            confidence="unconfirmed",
        ),
    ]
    security.evaluate(
        ctx,
        [SourceResult(source_id="socket_blog", signals=chatter_signals)],
        mode="hourly",
    )
    chatter_rows = ctx.db.execute(
        "SELECT entity_id, tier, confidence FROM alerts WHERE kind = 'chatter'"
    ).fetchall()
    assert len(chatter_rows) == 1
    assert str(chatter_rows[0]["entity_id"]) == "pkg:pypi/litellm"
    assert str(chatter_rows[0]["tier"]) == "P1"
    assert str(chatter_rows[0]["confidence"]) == "unconfirmed"


def test_baseline_notification_rule_a3(
    fixture_home: Any,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
    monkeypatch: Any,
) -> None:
    fixture_home(scenario="compromised")
    monkeypatch.setattr(security, "UV_AUDIT_ENABLED", False)

    ctx = context.build(
        transports={
            "http": replay_http("osv_batch"),
            "gh": replay_gh(),
            "procs": replay_procs(),
        },
        now=FIXED_NOW,
    )
    inventory.collect(ctx)
    assert db.kv_get(ctx.db, "baseline_done") is None

    # Baseline collect with held malware AND an archived-repo lifecycle alert
    mal_sig = RawSignal(
        source_id="osv_batch",
        type="compromise",
        external_id="MAL-2026-9999",
        title="Malicious Package in litellm",
        entity_hint="pkg:pypi/litellm",
        confidence="confirmed",
        payload={
            "advisory_id": "MAL-2026-9999",
            "is_mal": True,
            "affected_versions": ["1.107.0"],
        },
    )
    arch_sig = RawSignal(
        source_id="gh_repo_meta",
        type="lifecycle",
        external_id="gh_repo_meta:astral-sh/uv",
        title="GitHub repository astral-sh/uv is archived",
        entity_hint="gh:astral-sh/uv",
        confidence="confirmed",
        payload={"repo": "astral-sh/uv", "archived": True},
    )
    res_list = [
        SourceResult(source_id="osv_batch", signals=[mal_sig]),
        SourceResult(source_id="gh_repo_meta", signals=[arch_sig]),
    ]
    security.evaluate(ctx, res_list, mode="daily")
    lifecycle.evaluate(ctx, res_list)

    # kv['baseline_done'] set after the first source-running collect
    assert db.kv_get(ctx.db, "baseline_done") == ctx.now_iso

    # Archived-repo lifecycle alert has notified_at set at creation
    life_row = ctx.db.execute("SELECT notified_at FROM alerts WHERE kind = 'lifecycle'").fetchone()
    assert life_row is not None
    assert life_row["notified_at"] == ctx.now_iso

    # Held malware on the same baseline collect has notified_at NULL
    comp_row = ctx.db.execute("SELECT notified_at FROM alerts WHERE kind = 'compromise'").fetchone()
    assert comp_row is not None
    assert comp_row["notified_at"] is None


def test_uv_audit_toggle_isolated_copy_private_skip_and_hourly_skip(
    fixture_home: Any,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
    monkeypatch: Any,
) -> None:
    home = fixture_home(scenario="compromised")
    repo_a = home / "SynologyDrive" / "ActiveProjects" / "public-app"
    before_tree = _sha256_tree(repo_a)

    p_tr = replay_procs()
    ctx = context.build(
        transports={
            "http": replay_http("classify"),
            "gh": replay_gh(),
            "procs": p_tr,
        },
        now=FIXED_NOW,
    )
    inventory.collect(ctx)

    # 1. mode="hourly" never invokes uv audit or osv-scanner
    p_tr.calls.clear()
    security.evaluate(ctx, [], mode="hourly")
    assert not any(argv[:2] == ["uv", "audit"] for argv, _ in p_tr.calls)
    assert not any(argv and argv[0] == "osv-scanner" for argv, _ in p_tr.calls)

    # 2. UV_AUDIT_ENABLED=False -> source_health skipped reason and gap in counts
    monkeypatch.setattr(security, "UV_AUDIT_ENABLED", False)
    p_tr.calls.clear()
    security.evaluate(ctx, [], mode="daily")
    assert not any(argv[:2] == ["uv", "audit"] for argv, _ in p_tr.calls)
    sh_row = ctx.db.execute(
        "SELECT skipped_reason FROM source_health WHERE source_id = 'uv_audit'"
    ).fetchone()
    assert sh_row is not None
    assert str(sh_row["skipped_reason"]) == "disabled: mutates repos"
    cov_notes = json.loads(db.kv_get(ctx.db, "last_scanner_coverage_json") or "[]")
    assert "disabled: mutates repos" in cov_notes
    assert "scanner skipped: private dependencies" in cov_notes

    # 3. UV_AUDIT_ENABLED=True -> runs under {state}/audit/, env has UV_MALWARE_CHECK,
    # repo A dir untouched, repo C (private deps) skipped, and JSON fixture maps to confirmed signal
    monkeypatch.setattr(security, "UV_AUDIT_ENABLED", True)
    p_tr.calls.clear()
    security.evaluate(ctx, [], mode="daily")

    audit_calls = [(argv, env_keys) for argv, env_keys in p_tr.calls if argv[:2] == ["uv", "audit"]]
    # Only public-app (1 public Python project) is audited; secret-widget (repo C) is skipped!
    assert len(audit_calls) == 1
    assert "UV_MALWARE_CHECK" in audit_calls[0][1]

    audit_root = ctx.paths.state / "audit"
    audit_subdirs = [d for d in audit_root.iterdir() if d.is_dir()]
    assert len(audit_subdirs) == 1
    assert (audit_subdirs[0] / "pyproject.toml").is_file()
    assert (audit_subdirs[0] / "uv.lock").is_file()
    assert _sha256_tree(repo_a) == before_tree

    uv_sig = ctx.db.execute(
        "SELECT confidence, external_id FROM signals WHERE source_id = 'uv_audit'"
    ).fetchone()
    assert uv_sig is not None
    assert str(uv_sig["confidence"]) == "confirmed"
    assert str(uv_sig["external_id"]) == "MAL-2026-9999"
