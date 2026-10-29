"""Tests for source framework, core Task 5 source parsers, osv_batch chunking, and collect union."""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tech_radar import context, http, procs, sources
from tech_radar.errors import FetchError
from tech_radar.sources.base import FetchMeta, RawSignal, SourceSpec, run_source
from tech_radar.sources.security_feeds import (
    build_osv_queries,
    chunk_osv_queries,
    filter_querybatch_hits,
)

FIXED_NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
FIXTURES_HTTP = Path(__file__).resolve().parent / "fixtures" / "http"


def _load_first_fixture_bytes(source_id: str) -> bytes:
    src_dir = FIXTURES_HTTP / source_id
    for fpath in sorted(src_dir.glob("*.json")):
        obj = json.loads(fpath.read_text(encoding="utf-8"))
        body = obj.get("body", "")
        if isinstance(body, str):
            return body.encode("utf-8")
        return json.dumps(body).encode("utf-8")
    raise AssertionError(f"no fixture found in {src_dir}")


def test_framework_failure_credential_and_hourly_cadence(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    ctx = context.build(
        transports={
            "http": replay_http(),
            "gh": replay_gh(),
            "procs": replay_procs(),
        },
        now=FIXED_NOW,
    )

    # 1. Failing source increments consecutive_failures
    def _boom(_spec: SourceSpec, _ctx: Any) -> list[RawSignal]:
        raise FetchError("simulated network failure")

    fail_spec = SourceSpec(
        id="test_failing",
        req=("TEST-01",),
        archetype="json_api",
        url="https://api.osv.dev/v1/querybatch",
        cadence="daily",
        hosts=("api.osv.dev",),
        signal_types=("compromise",),
        runner=_boom,
    )
    res1 = run_source(fail_spec, ctx)
    assert res1.ok is False
    row1 = ctx.db.execute(
        "SELECT consecutive_failures, last_error "
        "FROM source_health WHERE source_id = 'test_failing'"
    ).fetchone()
    assert row1 is not None
    assert int(row1["consecutive_failures"]) == 1
    assert "simulated network failure" in str(row1["last_error"])

    res2 = run_source(fail_spec, ctx)
    assert res2.ok is False
    row2 = ctx.db.execute(
        "SELECT consecutive_failures FROM source_health WHERE source_id = 'test_failing'"
    ).fetchone()
    assert int(row2["consecutive_failures"]) == 2

    # 2. Missing credential yields skipped_reason='no credential' and no exception
    cred_spec = SourceSpec(
        id="test_keyed",
        req=("TEST-02",),
        archetype="json_api",
        url="https://api.producthunt.com/v2/api/graphql",
        cadence="weekly",
        hosts=("api.producthunt.com",),
        signal_types=("horizon",),
        credential="producthunt",
    )
    res_cred = run_source(cred_spec, ctx)
    assert res_cred.ok is True
    assert res_cred.skipped_reason == "no credential"
    row_cred = ctx.db.execute(
        "SELECT skipped_reason, consecutive_failures "
        "FROM source_health WHERE source_id = 'test_keyed'"
    ).fetchone()
    assert row_cred is not None
    assert str(row_cred["skipped_reason"]) == "no credential"
    assert int(row_cred["consecutive_failures"]) == 0

    # 3. by_cadence('hourly') returns exactly the 7 ids in spec 07 §8a
    hourly_ids = tuple(s.id for s in sources.by_cadence("hourly"))
    assert hourly_ids == (
        "osv_batch",
        "ghsa_malware",
        "cisa_kev",
        "socket_blog",
        "stepsecurity_blog",
        "vendor_sec_blogs",
        "hn_compromise",
    )


def _assert_valid_raw_signals(sigs: list[RawSignal], expected_source_id: str) -> None:
    assert len(sigs) >= 1
    for sig in sigs:
        assert sig.source_id == expected_source_id
        assert sig.external_id
        assert sig.title
        assert len(sig.untrusted_excerpt) <= 600


def test_source_parse_osv_batch() -> None:
    spec = sources.get_source("osv_batch")
    assert spec.parser is not None
    raw = _load_first_fixture_bytes("osv_batch")
    meta = FetchMeta(url=spec.url or "", fetched_at="2026-10-28T12:00:00Z", source_id=spec.id)
    sigs = spec.parser(raw, meta)
    _assert_valid_raw_signals(sigs, "osv_batch")


def test_source_parse_ghsa_malware() -> None:
    spec = sources.get_source("ghsa_malware")
    assert spec.parser is not None
    raw = _load_first_fixture_bytes("ghsa_malware")
    meta = FetchMeta(url=spec.url or "", fetched_at="2026-10-28T12:00:00Z", source_id=spec.id)
    sigs = spec.parser(raw, meta)
    _assert_valid_raw_signals(sigs, "ghsa_malware")
    assert sigs[0].entity_hint == "pkg:pypi/litellm"


def test_source_parse_cisa_kev() -> None:
    spec = sources.get_source("cisa_kev")
    assert spec.parser is not None
    raw = _load_first_fixture_bytes("cisa_kev")
    meta = FetchMeta(url=spec.url or "", fetched_at="2026-10-28T12:00:00Z", source_id=spec.id)
    sigs = spec.parser(raw, meta)
    _assert_valid_raw_signals(sigs, "cisa_kev")
    assert sigs[0].external_id == "CVE-2026-4242"


def test_source_parse_socket_blog() -> None:
    spec = sources.get_source("socket_blog")
    assert spec.parser is not None
    raw = _load_first_fixture_bytes("socket_blog")
    meta = FetchMeta(url=spec.url or "", fetched_at="2026-10-28T12:00:00Z", source_id=spec.id)
    sigs = spec.parser(raw, meta)
    _assert_valid_raw_signals(sigs, "socket_blog")
    assert sigs[0].confidence == "unconfirmed"


def test_source_parse_stepsecurity_blog() -> None:
    spec = sources.get_source("stepsecurity_blog")
    assert spec.parser is not None
    raw = _load_first_fixture_bytes("stepsecurity_blog")
    meta = FetchMeta(url=spec.url or "", fetched_at="2026-10-28T12:00:00Z", source_id=spec.id)
    sigs = spec.parser(raw, meta)
    _assert_valid_raw_signals(sigs, "stepsecurity_blog")
    assert sigs[0].confidence == "unconfirmed"


def test_source_parse_vendor_sec_blogs() -> None:
    spec = sources.get_source("vendor_sec_blogs")
    assert spec.parser is not None
    raw = _load_first_fixture_bytes("vendor_sec_blogs")
    meta = FetchMeta(url=spec.url or "", fetched_at="2026-10-28T12:00:00Z", source_id=spec.id)
    sigs = spec.parser(raw, meta)
    _assert_valid_raw_signals(sigs, "vendor_sec_blogs")
    assert sigs[0].confidence == "unconfirmed"


def test_source_parse_registry_sec_news() -> None:
    spec = sources.get_source("registry_sec_news")
    assert spec.parser is not None
    raw = _load_first_fixture_bytes("registry_sec_news")
    meta = FetchMeta(url=spec.url or "", fetched_at="2026-10-28T12:00:00Z", source_id=spec.id)
    sigs = spec.parser(raw, meta)
    _assert_valid_raw_signals(sigs, "registry_sec_news")


def test_source_parse_hn_compromise(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    spec = sources.get_source("hn_compromise")
    assert spec.parser is not None
    raw = (
        FIXTURES_HTTP / "hn_compromise" / "9af12589017e360f8b7503160192aaecec4398c4.json"
    ).read_text(encoding="utf-8")
    body = json.dumps(json.loads(raw)["body"]).encode("utf-8")
    meta = FetchMeta(url=spec.url or "", fetched_at="2026-10-28T12:00:00Z", source_id=spec.id)
    sigs = spec.parser(body, meta)
    _assert_valid_raw_signals(sigs, "hn_compromise")

    # Verify hn_compromise runner issues exactly 4 requests with single keyword and no package name
    http_tr = replay_http("hn_compromise")
    ctx = context.build(
        transports={"http": http_tr, "gh": replay_gh(), "procs": replay_procs()},
        now=FIXED_NOW,
    )
    ctx.db.execute(
        "INSERT INTO entities(entity_id, kind, ecosystem, name, visibility, created_at) "
        "VALUES ("
        "'pkg:pypi/litellm', 'package', 'pypi', 'litellm', 'public', '2026-10-28T00:00:00Z'"
        ")"
    )
    ctx.db.commit()
    res = run_source(spec, ctx)
    assert res.ok is True
    assert len(http_tr.calls) == 4
    queried_keywords: list[str] = []
    for _method, url, _bsha in http_tr.calls:
        parsed_q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        q_vals = parsed_q.get("query", [])
        assert len(q_vals) == 1
        kw = q_vals[0]
        assert " " not in kw
        assert "litellm" not in url
        queried_keywords.append(kw)
    assert tuple(queried_keywords) == ("compromised", "malware", "backdoor", "malicious")


def test_source_parse_npm_registry_meta() -> None:
    spec = sources.get_source("npm_registry_meta")
    assert spec.parser is not None
    raw = _load_first_fixture_bytes("npm_registry_meta")
    meta = FetchMeta(url=spec.url or "", fetched_at="2026-10-28T12:00:00Z", source_id=spec.id)
    sigs = spec.parser(raw, meta)
    _assert_valid_raw_signals(sigs, "npm_registry_meta")
    assert sigs[0].entity_hint == "pkg:npm/react"


def test_source_parse_pypi_meta() -> None:
    spec = sources.get_source("pypi_meta")
    assert spec.parser is not None
    raw = _load_first_fixture_bytes("pypi_meta")
    meta = FetchMeta(url=spec.url or "", fetched_at="2026-10-28T12:00:00Z", source_id=spec.id)
    sigs = spec.parser(raw, meta)
    _assert_valid_raw_signals(sigs, "pypi_meta")
    assert sigs[0].entity_hint == "pkg:pypi/litellm"


def test_source_parse_endoflife() -> None:
    spec = sources.get_source("endoflife")
    assert spec.parser is not None
    raw = _load_first_fixture_bytes("endoflife")
    meta = FetchMeta(
        url="https://endoflife.date/api/v1/products/python",
        fetched_at="2026-10-28T12:00:00Z",
        params={"product": "python"},
        source_id=spec.id,
    )
    sigs = spec.parser(raw, meta)
    _assert_valid_raw_signals(sigs, "endoflife")
    assert sigs[0].entity_hint == "runtime:python"


def test_source_parse_gh_releases() -> None:
    spec = sources.get_source("gh_releases")
    assert spec.parser is not None
    raw = _load_first_fixture_bytes("gh_releases")
    meta = FetchMeta(
        url="repos/astral-sh/uv/releases?per_page=10",
        fetched_at="2026-10-28T12:00:00Z",
        source_id=spec.id,
    )
    sigs = spec.parser(raw, meta)
    _assert_valid_raw_signals(sigs, "gh_releases")
    assert sigs[0].entity_hint == "gh:astral-sh/uv"


def test_source_parse_gh_changelog_file() -> None:
    spec = sources.get_source("gh_changelog_file")
    assert spec.parser is not None
    raw = _load_first_fixture_bytes("gh_changelog_file")
    meta = FetchMeta(
        url="repos/astral-sh/uv/contents/CHANGELOG.md",
        fetched_at="2026-10-28T12:00:00Z",
        source_id=spec.id,
    )
    sigs = spec.parser(raw, meta)
    _assert_valid_raw_signals(sigs, "gh_changelog_file")
    assert sigs[0].entity_hint == "gh:astral-sh/uv"


def test_source_parse_gh_repo_meta() -> None:
    spec = sources.get_source("gh_repo_meta")
    assert spec.parser is not None
    raw = _load_first_fixture_bytes("gh_repo_meta")
    meta = FetchMeta(
        url="repos/astral-sh/uv",
        fetched_at="2026-10-28T12:00:00Z",
        source_id=spec.id,
    )
    sigs = spec.parser(raw, meta)
    _assert_valid_raw_signals(sigs, "gh_repo_meta")
    assert sigs[0].entity_hint == "gh:astral-sh/uv"


def test_source_parse_claude_code_changelog() -> None:
    spec = sources.get_source("claude_code_changelog")
    assert spec.parser is not None
    raw = _load_first_fixture_bytes("claude_code_changelog")
    meta = FetchMeta(
        url="repos/anthropics/claude-code/contents/CHANGELOG.md",
        fetched_at="2026-10-28T12:00:00Z",
        source_id=spec.id,
    )
    sigs = spec.parser(raw, meta)
    _assert_valid_raw_signals(sigs, "claude_code_changelog")
    assert sigs[0].entity_hint == "gh:anthropics/claude-code"


def test_source_parse_anthropic_news() -> None:
    spec = sources.get_source("anthropic_news")
    assert spec.parser is not None
    raw = _load_first_fixture_bytes("anthropic_news")
    meta = FetchMeta(
        url="https://www.anthropic.com/news",
        fetched_at="2026-10-28T12:00:00Z",
        source_id=spec.id,
    )
    sigs = spec.parser(raw, meta)
    _assert_valid_raw_signals(sigs, "anthropic_news")


def test_source_parse_osv_batch_input_builder_and_chunking() -> None:
    # Transitive usage is included in queries; transitive non-MAL result is discarded;
    # 2,500 usages -> 3 sorted chunks of <= 1,000
    raw_usages = [
        {
            "entity_id": f"pkg:pypi/pkg-{i:04d}",
            "ecosystem": "pypi",
            "name": f"pkg-{i:04d}",
            "version": "1.0.0",
            "direct": 1 if i % 2 == 0 else 0,
            "visibility": "public",
        }
        for i in range(2500, 0, -1)
    ]
    queries = build_osv_queries(raw_usages)
    assert len(queries) == 2500
    # Verify sorted by (ecosystem, name, version)
    names = [q["name"] for q in queries]
    assert names == sorted(names)
    # Transitive usages included
    assert any(q["direct"] == 0 for q in queries)

    chunks = chunk_osv_queries(queries, chunk_size=1000)
    assert len(chunks) == 3
    assert len(chunks[0]) == 1000
    assert len(chunks[1]) == 1000
    assert len(chunks[2]) == 500

    # Transitive non-MAL result discarded, transitive MAL result kept
    sample_q = [
        {
            "ecosystem": "PyPI",
            "name": "trans-pkg",
            "version": "1.0.0",
            "entity_id": "pkg:pypi/trans-pkg",
            "direct": 0,
        },
        {
            "ecosystem": "PyPI",
            "name": "trans-mal",
            "version": "1.0.0",
            "entity_id": "pkg:pypi/trans-mal",
            "direct": 0,
        },
    ]
    sample_res = [
        {"vulns": [{"id": "CVE-2026-1111", "modified": "2026-10-28T00:00:00Z"}]},
        {"vulns": [{"id": "MAL-2026-2222", "modified": "2026-10-28T00:00:00Z"}]},
    ]
    kept = filter_querybatch_hits(sample_q, sample_res)
    assert len(kept) == 1
    assert kept[0][0]["entity_id"] == "pkg:pypi/trans-mal"
    assert kept[0][1]["id"] == "MAL-2026-2222"


def test_collect_union_monday_runs_hourly_daily_and_weekly_once(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
    monkeypatch: Any,
) -> None:
    # Monday 2026-11-02 -> weekly + hourly + daily
    monday_now = datetime(2026, 11, 2, 7, 0, 0, tzinfo=UTC)
    ctx = context.build(
        transports={
            "http": replay_http(),
            "gh": replay_gh(),
            "procs": replay_procs(),
        },
        now=monday_now,
        run_id="20261102T070000Z-weekly",
    )

    executed_ids: list[str] = []

    def _spy_run_source(spec: SourceSpec, _ctx: Any) -> sources.SourceResult:
        executed_ids.append(spec.id)
        return sources.SourceResult(source_id=spec.id, signals=[], ok=True)

    monkeypatch.setattr(sources.registry, "run_source", _spy_run_source)
    sources.run_collect_sources(ctx)

    expected_set = {
        *(s.id for s in sources.by_cadence("hourly")),
        *(s.id for s in sources.by_cadence("daily")),
        *(s.id for s in sources.by_cadence("weekly")),
    }
    assert len(executed_ids) == len(set(executed_ids))
    assert set(executed_ids) == expected_set
