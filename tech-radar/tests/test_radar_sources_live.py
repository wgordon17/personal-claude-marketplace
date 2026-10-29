"""Opt-in live smoke tests parametrized over SOURCES (Spec 01, Spec 12 A23)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tech_radar import context, sources
from tech_radar.sources.base import SourceSpec, run_source

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not os.environ.get("RUN_LIVE_TESTS"),
        reason="RUN_LIVE_TESTS not set",
    ),
]


@pytest.mark.parametrize("spec", sources.SOURCES, ids=lambda s: s.id)
def test_live_source_smoke(
    spec: SourceSpec,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run each SourceSpec once against live endpoints (or verify skip/on-demand handling)."""
    monkeypatch.delenv("TECH_RADAR_REPLAY_DIR", raising=False)
    monkeypatch.delenv("TECH_RADAR_NOTIFY_SINK", raising=False)
    ctx = context.build()

    # Seed one public PyPI, npm, and GitHub usage so inventory-driven sources have a target
    ts = "2026-10-28T00:00:00Z"
    ctx.db.execute(
        "INSERT OR IGNORE INTO entities("
        "entity_id, kind, ecosystem, name, repo, visibility, created_at"
        ") VALUES "
        f"('pkg:pypi/litellm', 'package', 'pypi', 'litellm', 'berriai/litellm', 'public', '{ts}'),"
        f"('pkg:npm/react', 'package', 'npm', 'react', 'facebook/react', 'public', '{ts}'),"
        f"('gh:astral-sh/uv', 'cli', 'github', 'uv', 'astral-sh/uv', 'public', '{ts}'),"
        f"('runtime:python', 'runtime', 'mise', 'python', NULL, 'public', '{ts}')"
    )
    ctx.db.execute(
        "INSERT OR IGNORE INTO usages("
        "entity_id, context, version, direct, first_seen, last_seen"
        ") VALUES "
        f"('pkg:pypi/litellm', 'project:example-org/demo', '1.106.2', 1, '{ts}', '{ts}'),"
        f"('pkg:npm/react', 'project:example-org/demo', '19.0.0', 1, '{ts}', '{ts}'),"
        f"('gh:astral-sh/uv', 'system:brew', '0.13.5', 1, '{ts}', '{ts}'),"
        f"('runtime:python', 'system:mise', '3.13.0', 1, '{ts}', '{ts}')"
    )
    ctx.db.commit()

    if not spec.enabled:
        res = run_source(spec, ctx)
        assert res.ok is True
        assert res.skipped_reason is not None
        return

    if spec.cadence == "on-demand":
        if spec.url:
            resp = ctx.http.fetch(spec.url, ttl_hours=1)
            assert resp.status == 200
            assert spec.parser is not None
            sigs = spec.parser(
                resp.body,
                sources.FetchMeta(url=resp.url, fetched_at=ctx.now_iso, source_id=spec.id),
            )
            assert isinstance(sigs, list)
        return

    # For reddit_top in live smoke, limit to 1 subreddit so smoke completes quickly
    active_spec = spec
    if spec.id == "reddit_top":
        from dataclasses import replace

        active_spec = replace(
            spec,
            params={"subreddits": ("programming",), "t": "day", "limit": 2},
        )
    elif spec.id == "topnames":
        # Fetch single page of pypi.org in live smoke
        resp = ctx.http.fetch(spec.url or "", ttl_hours=1)
        assert resp.status == 200
        assert spec.parser is not None
        sigs = spec.parser(
            resp.body,
            sources.FetchMeta(
                url=resp.url,
                fetched_at=ctx.now_iso,
                params={"registry": "pypi.org"},
                source_id=spec.id,
            ),
        )
        assert len(sigs) >= 1
        return

    res = run_source(active_spec, ctx)
    assert res.ok is True, f"live smoke failed for {spec.id}: {res.error}"
