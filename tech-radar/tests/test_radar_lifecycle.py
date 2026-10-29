"""Tests for Spec 07 §3 lifecycle rows (test_life_01..14), Renovate suppression, and A22."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

from tech_radar import context, http, lifecycle, procs
from tech_radar.sources.base import RawSignal, SourceResult

FIXED_NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)


def _seed_used_entity(
    ctx: context.Ctx,
    *,
    entity_id: str,
    kind: str = "package",
    ecosystem: str = "pypi",
    name: str = "demo",
    repo: str | None = None,
    version: str | None = "1.0.0",
    usage_context: str = "project:https://github.com/example-org/public-app",
    direct: int = 1,
) -> None:
    ctx.db.execute(
        "INSERT OR REPLACE INTO entities("
        "entity_id, kind, ecosystem, name, repo, visibility, created_at"
        ") VALUES (?, ?, ?, ?, ?, 'public', ?)",
        (entity_id, kind, ecosystem, name, repo, ctx.now_iso),
    )
    ctx.db.execute(
        "INSERT OR REPLACE INTO usages("
        "entity_id, context, version, direct, first_seen, last_seen"
        ") VALUES (?, ?, ?, ?, ?, ?)",
        (entity_id, usage_context, version, direct, ctx.now_iso, ctx.now_iso),
    )
    ctx.db.commit()


def test_life_01_repo_archived_and_watchlist_a22(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    ctx = context.build(
        transports={"http": replay_http(), "gh": replay_gh(), "procs": replay_procs()},
        now=FIXED_NOW,
    )
    # Seed a WATCHLIST-only entity (Spec 12 A22)
    _seed_used_entity(
        ctx,
        entity_id="gh:example-org/watched-archived",
        kind="repo",
        ecosystem="github",
        name="example-org/watched-archived",
        repo="example-org/watched-archived",
        usage_context="interest:watchlist",
        direct=0,
    )
    sig = RawSignal(
        source_id="gh_repo_meta",
        type="lifecycle",
        external_id="gh_repo_meta:example-org/watched-archived",
        title="Repository archived",
        entity_hint="gh:example-org/watched-archived",
        payload={"repo": "example-org/watched-archived", "archived": True},
    )
    new_ids = lifecycle.evaluate(ctx, [SourceResult(source_id="gh_repo_meta", signals=[sig])])
    assert len(new_ids) == 1
    row = ctx.db.execute(
        "SELECT tier, kind, entity_id FROM alerts WHERE alert_id = ?",
        (new_ids[0],),
    ).fetchone()
    assert str(row["tier"]) == "P1"
    assert str(row["kind"]) == "lifecycle"
    assert str(row["entity_id"]) == "gh:example-org/watched-archived"


def test_life_02_npm_deprecated_and_renovate_suppression(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    ctx = context.build(
        transports={"http": replay_http(), "gh": replay_gh(), "procs": replay_procs()},
        now=FIXED_NOW,
    )
    proj_key = "https://github.com/example-org/public-app"
    ctx.db.execute(
        "INSERT INTO projects("
        "project_key, display_path, main_worktree, active, renovate_json, "
        "excluded, root_source, visibility, alias"
        ") VALUES ("
        "?, '/tmp/app', '/tmp/app', 1, ?, 0, 'active_projects', 'public', 'example-org/public-app'"
        ")",
        (proj_key, json.dumps({"extends": ["config:recommended"]})),
    )
    _seed_used_entity(
        ctx,
        entity_id="pkg:npm/old-pkg",
        ecosystem="npm",
        name="old-pkg",
        version="1.0.0",
        usage_context=f"project:{proj_key}",
    )
    sig = RawSignal(
        source_id="npm_registry_meta",
        type="lifecycle",
        external_id="npm:old-pkg:1.0.0",
        title="npm old-pkg deprecated",
        entity_hint="pkg:npm/old-pkg",
        payload={
            "name": "old-pkg",
            "deprecated": "Use new-pkg instead",
            "deprecated_versions": {"1.0.0": "Use new-pkg instead"},
        },
    )
    new_ids = lifecycle.evaluate(ctx, [SourceResult(source_id="npm_registry_meta", signals=[sig])])
    assert len(new_ids) == 1

    # Now enable npm deprecation handling in renovate.json -> suppressed!
    ctx.db.execute("DELETE FROM alerts")
    ctx.db.execute(
        "UPDATE projects SET renovate_json = ? WHERE project_key = ?",
        (json.dumps({"extends": ["config:recommended"], "npmDeprecations": True}), proj_key),
    )
    ctx.db.commit()
    suppressed_ids = lifecycle.evaluate(
        ctx, [SourceResult(source_id="npm_registry_meta", signals=[sig])]
    )
    assert suppressed_ids == []


def test_life_03_pypi_status_archived_deprecated_quarantined(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    ctx = context.build(
        transports={"http": replay_http(), "gh": replay_gh(), "procs": replay_procs()},
        now=FIXED_NOW,
    )
    _seed_used_entity(ctx, entity_id="pkg:pypi/quar-pkg", name="quar-pkg")
    _seed_used_entity(ctx, entity_id="pkg:pypi/arch-pkg", name="arch-pkg")

    sigs = [
        RawSignal(
            source_id="pypi_meta",
            type="lifecycle",
            external_id="pypi:quar-pkg",
            title="quarantined",
            entity_hint="pkg:pypi/quar-pkg",
            payload={"name": "quar-pkg", "project_status": "quarantined"},
        ),
        RawSignal(
            source_id="pypi_meta",
            type="lifecycle",
            external_id="pypi:arch-pkg",
            title="archived",
            entity_hint="pkg:pypi/arch-pkg",
            payload={"name": "arch-pkg", "project_status": "archived"},
        ),
    ]
    lifecycle.evaluate(ctx, [SourceResult(source_id="pypi_meta", signals=sigs)])
    q_row = ctx.db.execute(
        "SELECT tier, confidence FROM alerts WHERE entity_id = 'pkg:pypi/quar-pkg'"
    ).fetchone()
    a_row = ctx.db.execute(
        "SELECT tier, confidence FROM alerts WHERE entity_id = 'pkg:pypi/arch-pkg'"
    ).fetchone()
    assert str(q_row["tier"]) == "P0"
    assert str(q_row["confidence"]) == "confirmed"
    assert str(a_row["tier"]) == "P1"


def test_life_04_yanked_pinned_release(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    ctx = context.build(
        transports={"http": replay_http(), "gh": replay_gh(), "procs": replay_procs()},
        now=FIXED_NOW,
    )
    _seed_used_entity(ctx, entity_id="pkg:pypi/yanked-lib", name="yanked-lib", version="2.1.0")
    sig = RawSignal(
        source_id="pypi_meta",
        type="lifecycle",
        external_id="pypi:yanked-lib:2.1.0",
        title="yanked",
        entity_hint="pkg:pypi/yanked-lib",
        payload={
            "name": "yanked-lib",
            "project_status": "active",
            "yanked_versions": {"2.1.0": "broken wheel"},
        },
    )
    ids = lifecycle.evaluate(ctx, [SourceResult(source_id="pypi_meta", signals=[sig])])
    assert len(ids) == 1
    row = ctx.db.execute("SELECT tier FROM alerts WHERE alert_id = ?", (ids[0],)).fetchone()
    assert str(row["tier"]) == "P1"


def test_life_05_endoflife_cycle(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    ctx = context.build(
        transports={"http": replay_http(), "gh": replay_gh(), "procs": replay_procs()},
        now=FIXED_NOW,
    )
    _seed_used_entity(
        ctx,
        entity_id="runtime:python",
        kind="runtime",
        ecosystem="os",
        name="python",
        version="3.10.16",
    )
    sig = RawSignal(
        source_id="endoflife",
        type="lifecycle",
        external_id="eol:python",
        title="Python EOL",
        entity_hint="runtime:python",
        payload={
            "product": "python",
            "cycles": [
                {"cycle": "3.10", "eol": "2026-10-31", "is_eol": False},
            ],
        },
    )
    ids = lifecycle.evaluate(ctx, [SourceResult(source_id="endoflife", signals=[sig])])
    assert len(ids) == 1
    row = ctx.db.execute("SELECT tier FROM alerts WHERE alert_id = ?", (ids[0],)).fetchone()
    assert str(row["tier"]) == "P1"


def test_life_06_no_commits_6_months(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    ctx = context.build(
        transports={"http": replay_http(), "gh": replay_gh(), "procs": replay_procs()},
        now=FIXED_NOW,
    )
    _seed_used_entity(
        ctx,
        entity_id="gh:example-org/stale-tool",
        kind="repo",
        ecosystem="github",
        name="stale-tool",
        repo="example-org/stale-tool",
    )
    sig = RawSignal(
        source_id="gh_repo_meta",
        type="lifecycle",
        external_id="gh_repo_meta:example-org/stale-tool",
        title="stale-tool",
        entity_hint="gh:example-org/stale-tool",
        payload={"repo": "example-org/stale-tool", "archived": False},
    )
    ids = lifecycle.evaluate(ctx, [SourceResult(source_id="gh_repo_meta", signals=[sig])])
    assert len(ids) == 1
    row = ctx.db.execute(
        "SELECT tier, dedupe_key FROM alerts WHERE alert_id = ?", (ids[0],)
    ).fetchone()
    assert str(row["tier"]) == "P2"
    assert "LIFE-06" in str(row["dedupe_key"])


def test_life_07_no_release_12_months(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    ctx = context.build(
        transports={"http": replay_http(), "gh": replay_gh(), "procs": replay_procs()},
        now=FIXED_NOW,
    )
    _seed_used_entity(
        ctx,
        entity_id="gh:example-org/old-releases",
        kind="repo",
        ecosystem="github",
        name="old-releases",
        repo="example-org/old-releases",
    )
    sig = RawSignal(
        source_id="gh_releases",
        type="feature",
        external_id="example-org/old-releases:v1.0.0",
        title="v1.0.0",
        published_at="2025-01-15T00:00:00Z",
        entity_hint="gh:example-org/old-releases",
        payload={"repo": "example-org/old-releases", "tag_name": "v1.0.0"},
    )
    ids = lifecycle.evaluate(ctx, [SourceResult(source_id="gh_releases", signals=[sig])])
    assert len(ids) == 1
    row = ctx.db.execute(
        "SELECT tier, dedupe_key FROM alerts WHERE alert_id = ?", (ids[0],)
    ).fetchone()
    assert str(row["tier"]) == "P2"
    assert "LIFE-07" in str(row["dedupe_key"])


def test_life_08_issue_response_collapse(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    ctx = context.build(
        transports={"http": replay_http(), "gh": replay_gh(), "procs": replay_procs()},
        now=FIXED_NOW,
    )
    _seed_used_entity(
        ctx,
        entity_id="gh:example-org/slow-issues",
        kind="repo",
        ecosystem="github",
        name="slow-issues",
        repo="example-org/slow-issues",
    )
    sig = RawSignal(
        source_id="gh_repo_meta",
        type="lifecycle",
        external_id="gh_repo_meta:example-org/slow-issues",
        title="slow-issues",
        entity_hint="gh:example-org/slow-issues",
        payload={
            "repo": "example-org/slow-issues",
            "archived": False,
            "pushed_at": "2026-10-20T00:00:00Z",
        },
    )
    ids = lifecycle.evaluate(ctx, [SourceResult(source_id="gh_repo_meta", signals=[sig])])
    assert any(
        "LIFE-08"
        in str(
            ctx.db.execute("SELECT dedupe_key FROM alerts WHERE alert_id = ?", (i,)).fetchone()[0]
        )
        for i in ids
    )

    # Also verify LIFE-09 fires on gh_discussions_announce maintainer-leaving announcement
    disc_sig = RawSignal(
        source_id="gh_discussions_announce",
        type="lifecycle",
        external_id="disc-leaving-1",
        title="Project status update",
        untrusted_excerpt="This project is looking for new maintainers.",
        entity_hint="gh:example-org/slow-issues",
        payload={"repo": "example-org/slow-issues"},
    )
    disc_ids = lifecycle.evaluate(
        ctx,
        [SourceResult(source_id="gh_discussions_announce", signals=[disc_sig])],
    )
    assert any(
        "LIFE-09"
        in str(
            ctx.db.execute("SELECT dedupe_key FROM alerts WHERE alert_id = ?", (i,)).fetchone()[0]
        )
        for i in disc_ids
    )

    # Verify 7-day KV cache hit on second evaluate with same pushed_at makes zero gh calls,
    # and changing pushed_at invalidates the cache
    ctx.gh.transport.calls.clear()  # type: ignore[attr-defined]
    lifecycle.evaluate(ctx, [SourceResult(source_id="gh_repo_meta", signals=[sig])])
    assert ctx.gh.transport.calls == []  # type: ignore[attr-defined]

    sig_updated_push = RawSignal(
        source_id="gh_repo_meta",
        type="lifecycle",
        external_id="gh_repo_meta:example-org/slow-issues",
        title="slow-issues",
        entity_hint="gh:example-org/slow-issues",
        payload={
            "repo": "example-org/slow-issues",
            "archived": False,
            "pushed_at": "2026-10-25T00:00:00Z",
            "open_issues_count": 0,
            "forks_count": 0,
        },
    )
    lifecycle.evaluate(ctx, [SourceResult(source_id="gh_repo_meta", signals=[sig_updated_push])])
    assert len(ctx.gh.transport.calls) >= 1  # type: ignore[attr-defined]
    assert not any("search/issues" in " ".join(argv) for argv, _ in ctx.gh.transport.calls)  # type: ignore[attr-defined]


def test_life_09_maintainer_leaving_notice(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    ctx = context.build(
        transports={"http": replay_http(), "gh": replay_gh(), "procs": replay_procs()},
        now=FIXED_NOW,
    )
    _seed_used_entity(
        ctx,
        entity_id="gh:example-org/unmaintained-lib",
        kind="repo",
        ecosystem="github",
        name="unmaintained-lib",
        repo="example-org/unmaintained-lib",
    )
    sig = RawSignal(
        source_id="gh_repo_meta",
        type="lifecycle",
        external_id="gh_repo_meta:example-org/unmaintained-lib",
        title="unmaintained-lib",
        entity_hint="gh:example-org/unmaintained-lib",
        payload={
            "repo": "example-org/unmaintained-lib",
            "archived": False,
            "pushed_at": "2026-10-20T00:00:00Z",
        },
    )
    ids = lifecycle.evaluate(ctx, [SourceResult(source_id="gh_repo_meta", signals=[sig])])
    row = ctx.db.execute("SELECT tier FROM alerts WHERE dedupe_key LIKE '%LIFE-09%'").fetchone()
    assert row is not None
    assert str(row["tier"]) == "P1"
    assert len(ids) >= 1


def test_life_11_acquisition_sunset_notice(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    ctx = context.build(
        transports={"http": replay_http(), "gh": replay_gh(), "procs": replay_procs()},
        now=FIXED_NOW,
    )
    _seed_used_entity(ctx, entity_id="pkg:pypi/sunset-sdk", name="sunset-sdk")
    sig = RawSignal(
        source_id="registry_sec_news",
        type="lifecycle",
        external_id="news-sunset-1",
        title="sunset-sdk is shutting down next quarter",
        untrusted_excerpt="The sunset-sdk service was acquired and is shutting down.",
        entity_hint="pkg:pypi/sunset-sdk",
        confidence="unconfirmed",
    )
    ids = lifecycle.evaluate(ctx, [SourceResult(source_id="registry_sec_news", signals=[sig])])
    assert len(ids) == 1
    row = ctx.db.execute(
        "SELECT tier, confidence FROM alerts WHERE alert_id = ?", (ids[0],)
    ).fetchone()
    assert str(row["tier"]) == "P2"
    assert str(row["confidence"]) == "unconfirmed"


def test_life_12_community_fork_overtaking(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    ctx = context.build(
        transports={"http": replay_http(), "gh": replay_gh(), "procs": replay_procs()},
        now=FIXED_NOW,
    )
    _seed_used_entity(
        ctx,
        entity_id="gh:example-org/forked-lib",
        kind="repo",
        ecosystem="github",
        name="forked-lib",
        repo="example-org/forked-lib",
    )
    sig = RawSignal(
        source_id="gh_repo_meta",
        type="lifecycle",
        external_id="gh_repo_meta:example-org/forked-lib",
        title="forked-lib",
        entity_hint="gh:example-org/forked-lib",
        payload={
            "repo": "example-org/forked-lib",
            "archived": False,
            "stargazers_count": 1000,
            "commits_90d": 2,
            "pushed_at": "2026-09-01T00:00:00Z",
        },
    )
    lifecycle.evaluate(ctx, [SourceResult(source_id="gh_repo_meta", signals=[sig])])
    row = ctx.db.execute("SELECT tier FROM alerts WHERE dedupe_key LIKE '%LIFE-12%'").fetchone()
    assert row is not None
    assert str(row["tier"]) == "P2"


def test_life_13_homebrew_formula_deprecated(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    ctx = context.build(
        transports={
            "http": replay_http(),
            "gh": replay_gh(),
            "procs": replay_procs(scenario="brew_deprecated"),
        },
        now=FIXED_NOW,
    )
    _seed_used_entity(
        ctx,
        entity_id="brew:formula/jq",
        kind="cli",
        ecosystem="homebrew",
        name="jq",
        usage_context="system:brew",
    )
    lifecycle.evaluate(ctx, [])
    row = ctx.db.execute(
        "SELECT tier FROM alerts WHERE dedupe_key = 'lifecycle|brew:formula/jq|LIFE-13'"
    ).fetchone()
    assert row is not None
    assert str(row["tier"]) == "P1"


def test_life_14_top_maintainer_inactive_90d(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    ctx = context.build(
        transports={"http": replay_http(), "gh": replay_gh(), "procs": replay_procs()},
        now=FIXED_NOW,
    )
    _seed_used_entity(
        ctx,
        entity_id="gh:example-org/inactive-maint",
        kind="repo",
        ecosystem="github",
        name="inactive-maint",
        repo="example-org/inactive-maint",
    )
    sig = RawSignal(
        source_id="gh_repo_meta",
        type="lifecycle",
        external_id="gh_repo_meta:example-org/inactive-maint",
        title="inactive-maint",
        entity_hint="gh:example-org/inactive-maint",
        payload={
            "repo": "example-org/inactive-maint",
            "archived": False,
            "pushed_at": "2026-10-20T00:00:00Z",
        },
    )
    lifecycle.evaluate(ctx, [SourceResult(source_id="gh_repo_meta", signals=[sig])])
    row = ctx.db.execute("SELECT tier FROM alerts WHERE dedupe_key LIKE '%LIFE-14%'").fetchone()
    assert row is not None
    assert str(row["tier"]) == "P3"
