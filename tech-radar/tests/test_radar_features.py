"""Tests for features.py and pricing signals (Spec 07 §4, §5, §9)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

from tech_radar import context, features, http, procs
from tech_radar.sources.base import RawSignal, SourceResult

FIXED_NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)


def test_features_consolidation_feat09_life15_and_a12_flag_and_pricing(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    ctx = context.build(
        transports={"http": replay_http(), "gh": replay_gh(), "procs": replay_procs()},
        now=FIXED_NOW,
    )

    # Seed entities, usages, and fingerprints:
    # - user uses `uv` (subcommand `uv sync`, flag `--frozen`) and `pip-audit`, NOT `uv audit`
    # - user uses `gh` with subcommand `gh pr` and flag `--state` (A12 fingerprint)
    ctx.db.execute(
        "INSERT INTO entities(entity_id, kind, ecosystem, name, repo, visibility, created_at) "
        "VALUES "
        "('gh:astral-sh/uv', 'cli', 'homebrew', 'uv', 'astral-sh/uv', 'public', ?), "
        "('pkg:pypi/pip-audit', 'package', 'pypi', 'pip-audit', NULL, 'public', ?), "
        "('gh:cli/cli', 'cli', 'homebrew', 'gh', 'cli/cli', 'public', ?), "
        "('gh:anthropics/claude-code', 'cli', 'claude-code', "
        "'claude-code', 'anthropics/claude-code', 'public', ?)",
        (ctx.now_iso, ctx.now_iso, ctx.now_iso, ctx.now_iso),
    )
    ctx.db.execute(
        "INSERT INTO usages(entity_id, context, version, direct, first_seen, last_seen) "
        "VALUES "
        "('gh:astral-sh/uv', "
        "'project:https://github.com/example-org/public-app', '0.14.0', 1, ?, ?), "
        "('pkg:pypi/pip-audit', "
        "'project:https://github.com/example-org/public-app', '2.7.0', 1, ?, ?), "
        "('gh:cli/cli', "
        "'project:https://github.com/example-org/public-app', '2.60.0', 1, ?, ?)",
        (ctx.now_iso, ctx.now_iso, ctx.now_iso, ctx.now_iso, ctx.now_iso, ctx.now_iso),
    )
    ctx.db.execute(
        "INSERT INTO fingerprints(context, kind, token, count, last_seen) "
        "VALUES "
        "('project:https://github.com/example-org/public-app', 'cmd', 'uv', 5, ?), "
        "('project:https://github.com/example-org/public-app', 'subcommand', 'uv sync', 5, ?), "
        "('project:https://github.com/example-org/public-app', 'flag', '--frozen', 5, ?), "
        "('project:https://github.com/example-org/public-app', 'cmd', 'pip-audit', 3, ?), "
        "('project:https://github.com/example-org/public-app', 'cmd', 'gh', 4, ?), "
        "('project:https://github.com/example-org/public-app', 'subcommand', 'gh pr', 4, ?), "
        "('project:https://github.com/example-org/public-app', 'flag', '--state', 4, ?)",
        (
            ctx.now_iso,
            ctx.now_iso,
            ctx.now_iso,
            ctx.now_iso,
            ctx.now_iso,
            ctx.now_iso,
            ctx.now_iso,
        ),
    )
    ctx.db.commit()

    # 1. Minor release 0.14.0 of uv adding `uv audit` + unrelated bullet
    uv_minor_rel = RawSignal(
        source_id="gh_releases",
        type="feature",
        external_id="astral-sh/uv:0.14.0",
        title="astral-sh/uv 0.14.0",
        published_at="2026-10-22T12:00:00Z",
        entity_hint="gh:astral-sh/uv",
        payload={
            "repo": "astral-sh/uv",
            "tag_name": "0.14.0",
            "body": (
                "- Add `uv audit` subcommand for dependency vulnerability scanning\n"
                "- Internal refactor of Windows installer registry paths\n"
            ),
        },
    )

    # 2. Patch-only release 0.14.1 of uv (must be ignored per FEAT-04)
    uv_patch_rel = RawSignal(
        source_id="gh_releases",
        type="feature",
        external_id="astral-sh/uv:0.14.1",
        title="astral-sh/uv 0.14.1",
        published_at="2026-10-25T12:00:00Z",
        entity_hint="gh:astral-sh/uv",
        payload={
            "repo": "astral-sh/uv",
            "tag_name": "0.14.1",
            "body": "- Add `uv lock --new-patch-flag` option\n",
        },
    )

    # 3. Minor release 2.60.0 of gh adding `--template` to `gh pr` (A12 fingerprint)
    gh_minor_rel = RawSignal(
        source_id="gh_releases",
        type="feature",
        external_id="cli/cli:v2.60.0",
        title="cli/cli v2.60.0",
        published_at="2026-10-24T12:00:00Z",
        entity_hint="gh:cli/cli",
        payload={
            "repo": "cli/cli",
            "tag_name": "v2.60.0",
            "body": "- Support `--template` flag in `gh pr` creation workflows\n",
        },
    )

    # 4. Claude Code changelog item mentioning "weekly limit" -> pricing signal
    cc_changelog = RawSignal(
        source_id="claude_code_changelog",
        type="pricing",
        external_id="claude-code:2.1.178",
        title="Claude Code 2.1.178",
        published_at="2026-10-26T12:00:00Z",
        untrusted_excerpt="Updated Claude Max plan weekly limit and quota display.",
        entity_hint="gh:anthropics/claude-code",
        payload={
            "repo": "anthropics/claude-code",
            "version": "2.1.178",
            "body": "- Updated Claude Max plan weekly limit and quota display\n",
        },
    )

    out = features.evaluate(
        ctx,
        [
            SourceResult(
                source_id="gh_releases",
                signals=[uv_minor_rel, uv_patch_rel, gh_minor_rel],
            ),
            SourceResult(
                source_id="claude_code_changelog",
                signals=[cc_changelog],
            ),
        ],
    )

    # Verify `uv audit` surfaced with FEAT-09 tag and emitted LIFE-15 lifecycle signal for pip-audit
    feat_rows = ctx.db.execute(
        "SELECT entity_id, untrusted_excerpt, payload_json FROM signals WHERE type = 'feature'"
    ).fetchall()
    uv_feat = [r for r in feat_rows if "uv audit" in str(r["untrusted_excerpt"])]
    assert len(uv_feat) == 1
    uv_payload = json.loads(str(uv_feat[0]["payload_json"]))
    assert "FEAT-09" in uv_payload["tags"]
    assert uv_payload["replaces_tool"] == "pip-audit"

    life15_rows = ctx.db.execute(
        "SELECT entity_id, title, payload_json FROM signals "
        "WHERE type = 'lifecycle' AND dedupe_key LIKE '%LIFE-15'"
    ).fetchall()
    assert len(life15_rows) == 1
    assert str(life15_rows[0]["entity_id"]) == "pkg:pypi/pip-audit"

    # Verify `gh pr` flag `--template` surfaced via A12 fingerprint
    gh_feat = [r for r in feat_rows if "--template" in str(r["untrusted_excerpt"])]
    assert len(gh_feat) == 1
    gh_payload = json.loads(str(gh_feat[0]["payload_json"]))
    assert "FEAT-05" in gh_payload["tags"]
    assert "gh pr" in gh_payload["matched_tokens"]

    # Verify patch-only release (`--new-patch-flag`) and unrelated Windows bullet were dropped
    assert not any("--new-patch-flag" in str(r["untrusted_excerpt"]) for r in feat_rows)
    assert not any("Windows installer" in str(r["untrusted_excerpt"]) for r in feat_rows)

    # Verify "weekly limit" Claude Code changelog item produced a pricing signal
    assert len(out["pricing"]) >= 1
    pricing_rows = ctx.db.execute(
        "SELECT source_id, untrusted_excerpt FROM signals WHERE type = 'pricing'"
    ).fetchall()
    assert len(pricing_rows) >= 1
    assert any("weekly limit" in str(r["untrusted_excerpt"]).lower() for r in pricing_rows)

    # Verify hold-ring item is excluded from FEAT-13 feature extraction while
    # gh_discussions_announce on a covered entity extracts matching features
    ctx.db.execute(
        "INSERT INTO items(item_id, entity_id, ring, state, in_use, created_at) "
        "VALUES ('R-0099', 'gh:cli/cli', 'hold', 'active', 1, ?)",
        (ctx.now_iso,),
    )
    ctx.db.commit()
    gh_hold_rel = RawSignal(
        source_id="gh_releases",
        type="feature",
        external_id="cli/cli:v2.61.0",
        title="cli/cli v2.61.0",
        published_at="2026-10-26T12:00:00Z",
        entity_hint="gh:cli/cli",
        payload={
            "repo": "cli/cli",
            "tag_name": "v2.61.0",
            "body": "- Add `--hold-ignored` flag to `gh pr` workflows\n",
        },
    )
    uv_disc = RawSignal(
        source_id="gh_discussions_announce",
        type="feature",
        external_id="disc-uv-1",
        title="uv 0.15 announcement",
        published_at="2026-10-27T12:00:00Z",
        entity_hint="gh:astral-sh/uv",
        payload={
            "repo": "astral-sh/uv",
            "body": "- Support `--locked-hashes` flag in `uv sync` workflows\n",
        },
    )
    features.evaluate(
        ctx,
        [
            SourceResult(source_id="gh_releases", signals=[gh_hold_rel]),
            SourceResult(source_id="gh_discussions_announce", signals=[uv_disc]),
        ],
    )
    feat_rows_after = ctx.db.execute(
        "SELECT untrusted_excerpt FROM signals WHERE type = 'feature'"
    ).fetchall()
    assert not any("--hold-ignored" in str(r["untrusted_excerpt"]) for r in feat_rows_after)
    assert any("--locked-hashes" in str(r["untrusted_excerpt"]) for r in feat_rows_after)

    # Verify FEAT-13 top-40 fingerprint ranking cap and config_key / module scoring
    for idx in range(45):
        eid = f"pkg:pypi/extra-tool-{idx:02d}"
        ctx.db.execute(
            "INSERT INTO entities(entity_id, kind, ecosystem, name, visibility, created_at) "
            "VALUES (?, 'package', 'pypi', ?, 'public', ?)",
            (eid, f"extra-tool-{idx:02d}", ctx.now_iso),
        )
        ctx.db.execute(
            "INSERT INTO usages(entity_id, context, version, direct, first_seen, last_seen) "
            "VALUES (?, 'project:https://github.com/example-org/public-app', '1.0.0', 1, ?, ?)",
            (eid, ctx.now_iso, ctx.now_iso),
        )
    ctx.db.execute(
        "INSERT INTO fingerprints(context, kind, token, count, last_seen) "
        "VALUES "
        "('project:https://github.com/example-org/public-app', 'config_key', "
        "'tool.extra-tool-44.strict', 10, ?), "
        "('project:https://github.com/example-org/public-app', 'import', 'extra_tool_43', 9, ?)",
        (ctx.now_iso, ctx.now_iso),
    )
    ctx.db.commit()
    dom = features._load_fingerprint_domain(ctx)
    assert "pkg:pypi/extra-tool-44" in dom["covered_entities"]
    assert "pkg:pypi/extra-tool-43" in dom["covered_entities"]
    assert "pkg:pypi/extra-tool-42" not in dom["covered_entities"]
