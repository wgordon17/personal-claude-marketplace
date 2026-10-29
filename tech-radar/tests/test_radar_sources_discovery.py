"""Tests for discovery sources, enrich-only fetch_metrics, HN filters, and broken specs."""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tech_radar import cli, context, http, procs, sources
from tech_radar.errors import FetchError
from tech_radar.sources import ai, feeds, registries
from tech_radar.sources.base import FetchMeta, RawSignal, run_source

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


def _assert_valid_raw_signals(sigs: list[RawSignal], expected_source_id: str) -> None:
    assert len(sigs) >= 1
    for sig in sigs:
        assert sig.source_id == expected_source_id
        assert sig.external_id
        assert sig.title
        assert len(sig.untrusted_excerpt) <= 600


def _parse_source(source_id: str, **meta_kwargs: Any) -> list[RawSignal]:
    spec = sources.get_source(source_id)
    assert spec.parser is not None
    raw = _load_first_fixture_bytes(source_id)
    meta = FetchMeta(
        url=meta_kwargs.get("url") or spec.url or "",
        fetched_at="2026-10-28T12:00:00Z",
        params=meta_kwargs.get("params") or spec.params,
        source_id=spec.id,
        entity_id=meta_kwargs.get("entity_id"),
    )
    sigs = spec.parser(raw, meta)
    _assert_valid_raw_signals(sigs, source_id)
    return sigs


# --- feeds.py (10 sources) ---


def test_source_parse_simonw() -> None:
    sigs = _parse_source("simonw")
    assert sigs[0].entity_hint == "gh:simonw/llm"


def test_source_parse_latent_space() -> None:
    sigs = _parse_source("latent_space")
    assert sigs[0].entity_hint == "gh:example-org/agent-kit"


def test_source_parse_console_dev() -> None:
    sigs = _parse_source("console_dev")
    assert sigs[0].entity_hint == "gh:julien-cpsn/atac"


def test_source_parse_terminal_trove() -> None:
    sigs = _parse_source("terminal_trove")
    assert sigs[0].entity_hint == "gh:sxyazi/yazi"


def test_source_parse_pycoders() -> None:
    sigs = _parse_source("pycoders")
    assert sigs[0].entity_hint == "gh:astral-sh/ruff"


def test_source_parse_twir() -> None:
    sigs = _parse_source("twir")
    assert sigs[0].entity_hint == "gh:burntsushi/ripgrep"


def test_source_parse_js_node_weekly() -> None:
    sigs = _parse_source("js_node_weekly")
    assert sigs[0].entity_hint == "gh:nodejs/node"


def test_source_parse_changelog_news() -> None:
    sigs = _parse_source("changelog_news")
    assert sigs[0].entity_hint == "gh:casey/just"


def test_source_parse_tldr() -> None:
    sigs = _parse_source("tldr")
    assert sigs[0].entity_hint == "gh:example-org/codetool"


def test_source_parse_lobsters() -> None:
    sigs = _parse_source("lobsters")
    assert sigs[0].entity_hint is not None


# --- community.py (7 sources) ---


def test_source_parse_hn_algolia() -> None:
    sigs = _parse_source("hn_algolia")
    assert sigs[0].type == "contender"
    assert sigs[0].entity_hint == "gh:example-org/uv-fast"


def test_source_parse_hn_pricing() -> None:
    sigs = _parse_source("hn_pricing")
    assert len(sigs) == 1
    assert sigs[0].external_id == "42000201"
    assert sigs[0].type == "pricing"


def test_source_parse_devto() -> None:
    sigs = _parse_source("devto")
    assert sigs[0].entity_hint == "gh:astral-sh/uv"


def test_source_parse_dailydev() -> None:
    sigs = _parse_source("dailydev")
    assert sigs[0].entity_hint == "gh:example-org/daily-tool"


def test_source_parse_producthunt() -> None:
    sigs = _parse_source("producthunt")
    assert sigs[0].entity_hint == "gh:example-org/termflow"
    assert sigs[0].metrics.get("votes") == 340


def test_source_parse_devhunt() -> None:
    sigs = _parse_source("devhunt")
    assert sigs[0].entity_hint == "gh:example-org/devhunt-cli"


def test_source_parse_reddit_top() -> None:
    sigs = _parse_source("reddit_top")
    assert sigs[0].entity_hint == "gh:example-org/fast-mcp"


# --- github.py discovery (7 sources) ---


def test_source_parse_gh_search_rising() -> None:
    sigs = _parse_source("gh_search_rising")
    assert sigs[0].entity_hint == "gh:example-org/rising-agent"


def test_source_parse_gh_trending() -> None:
    sigs = _parse_source("gh_trending")
    assert sigs[0].entity_hint is not None
    assert sigs[0].entity_hint.startswith("gh:example-org/trending-")
    assert sigs[0].metrics.get("stars_7d") == 1420


def test_source_parse_gh_topics() -> None:
    sigs = _parse_source("gh_topics")
    assert sigs[0].entity_hint == "gh:example-org/mcp-inspector-plus"


def test_source_parse_gh_tastemaker_stars() -> None:
    sigs = _parse_source("gh_tastemaker_stars")
    assert sigs[0].entity_hint == "gh:example-org/sqlite-vec"


def test_source_parse_gh_discussions_announce() -> None:
    sigs = _parse_source("gh_discussions_announce", params={"repo": "astral-sh/uv"})
    assert sigs[0].entity_hint == "gh:astral-sh/uv"


def test_source_parse_awesome_diffs() -> None:
    sigs = _parse_source("awesome_diffs", params={"repo": "hesreallyhim/awesome-claude-code"})
    assert sigs[0].entity_hint == "gh:example-org/claude-skill-lint"


def test_source_parse_anthropic_skills() -> None:
    sigs = _parse_source("anthropic_skills")
    assert sigs[0].entity_hint == "gh:anthropics/skills"


# --- registries.py discovery & enrich-only (8 sources) ---


def test_source_parse_ossinsight_trending() -> None:
    sigs = _parse_source("ossinsight_trending")
    assert sigs[0].entity_hint == "gh:example-org/oss-trend-pkg"


def test_source_parse_ecosystems_pkg() -> None:
    sigs = _parse_source("ecosystems_pkg")
    assert sigs[0].entity_hint == "pkg:pypi/litellm"
    assert sigs[0].metrics.get("downloads") == 4500000


def test_source_parse_depsdev() -> None:
    sigs = _parse_source("depsdev")
    assert len(sigs) >= 1


def test_source_parse_pypistats() -> None:
    sigs = _parse_source("pypistats")
    assert sigs[0].metrics.get("downloads_30d") == 4500000


def test_source_parse_npm_downloads() -> None:
    sigs = _parse_source("npm_downloads")
    assert sigs[0].metrics.get("downloads_30d") == 95000000


def test_source_parse_crates_new() -> None:
    sigs = _parse_source("crates_new")
    assert sigs[0].entity_hint == "gh:example-org/fast-walker"


def test_source_parse_brew_new_formulae() -> None:
    sigs = _parse_source("brew_new_formulae")
    assert sigs[0].entity_hint == "brew:formula/hyperfine-plus"


def test_source_parse_topnames() -> None:
    sigs = _parse_source("topnames", params={"registry": "pypi.org"})
    assert "requests" in sigs[0].payload.get("names", [])


# --- ai.py discovery & pricing (8 sources) ---


def test_source_parse_harness_changelogs() -> None:
    sigs = _parse_source("harness_changelogs")
    assert sigs[0].type == "technique"


def test_source_parse_skills_sh() -> None:
    sigs = _parse_source("skills_sh")
    assert sigs[0].entity_hint == "gh:example-org/skill-pack"


def test_source_parse_plugin_directories() -> None:
    sigs = _parse_source("plugin_directories")
    assert sigs[0].entity_hint == "gh:example-org/claude-plugin-demo"


def test_source_parse_mcp_registry() -> None:
    sigs = _parse_source("mcp_registry")
    assert sigs[0].entity_hint == "gh:example-org/sqlite-mcp"


def test_source_parse_models_dev() -> None:
    sigs = _parse_source("models_dev")
    assert sigs[0].entity_hint == "model:claude-sonnet-4-5"


def test_source_parse_openrouter_models() -> None:
    sigs = _parse_source("openrouter_models")
    assert sigs[0].entity_hint == "model:claude-sonnet-4-5"


def test_source_parse_openai_news() -> None:
    sigs = _parse_source("openai_news")
    assert sigs[0].type == "pricing"


def test_source_parse_google_ai_blog() -> None:
    sigs = _parse_source("google_ai_blog")
    assert sigs[0].type == "pricing"


# --- Behavioral tests for HN filters, broken sources, and fetch_metrics ---


def test_hn_pricing_keeps_ai_free_tier_and_drops_non_ai_discount(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    http_tr = replay_http("hn_pricing")
    ctx = context.build(
        transports={"http": http_tr, "gh": replay_gh(), "procs": replay_procs()},
        now=FIXED_NOW,
    )
    spec = sources.get_source("hn_pricing")
    res = run_source(spec, ctx)
    assert res.ok is True
    assert len(http_tr.calls) == 4
    ext_ids = [s.external_id for s in res.signals]
    assert "42000201" in ext_ids
    assert "42000202" not in ext_ids


def test_hn_algolia_requests_carry_single_plain_term_only(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    http_tr = replay_http("hn_algolia")
    ctx = context.build(
        transports={"http": http_tr, "gh": replay_gh(), "procs": replay_procs()},
        now=FIXED_NOW,
    )
    ctx.db.execute(
        "INSERT INTO entities(entity_id, kind, ecosystem, name, visibility, created_at) "
        "VALUES "
        "('pkg:pypi/litellm', 'package', 'pypi', 'litellm', 'public', '2026-10-28T00:00:00Z'),"
        "('pkg:pypi/sec-int', 'package', 'pypi', 'sec-int', 'private', '2026-10-28T00:00:00Z')"
    )
    ctx.db.commit()
    ctx.profile.categories = ["mcp AND python", "claude"]

    spec = sources.get_source("hn_algolia")
    res = run_source(spec, ctx)
    assert res.ok is True
    assert len(http_tr.calls) >= 2

    for _method, url, _bsha in http_tr.calls:
        assert "sec-int" not in url
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        q_list = qs.get("query", [])
        assert len(q_list) == 1
        term = q_list[0]
        assert " " not in term
        assert term.upper() not in ("AND", "OR", "NOT")


def test_broken_sources_report_disabled_with_reason(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    ctx = context.build(
        transports={"http": replay_http(), "gh": replay_gh(), "procs": replay_procs()},
        now=FIXED_NOW,
    )
    for broken_id in ("ossinsight_trending", "dailydev"):
        spec = sources.get_source(broken_id)
        assert spec.enabled is False
        assert spec.skipped_reason is not None
        assert "BROKEN" in spec.skipped_reason

        res = run_source(spec, ctx)
        assert res.ok is True
        assert res.signals == []
        assert res.skipped_reason == spec.skipped_reason
        row = ctx.db.execute(
            "SELECT skipped_reason, consecutive_failures FROM source_health WHERE source_id = ?",
            (broken_id,),
        ).fetchone()
        assert row is not None
        assert str(row["skipped_reason"]) == spec.skipped_reason
        assert int(row["consecutive_failures"]) == 0

    # Verify adopt_trial_public_repos includes adopt/trial + non-held watchlist and excludes hold
    from tech_radar.sources import github as gh_mod

    ctx.db.execute(
        "INSERT INTO entities(entity_id, kind, ecosystem, name, repo, visibility, created_at) "
        "VALUES "
        "('gh:example-org/adopt-repo', 'repo', 'github', 'adopt-repo', "
        "'example-org/adopt-repo', 'public', '2026-10-28T00:00:00Z'), "
        "('gh:example-org/hold-repo', 'repo', 'github', 'hold-repo', "
        "'example-org/hold-repo', 'public', '2026-10-28T00:00:00Z')"
    )
    ctx.db.execute(
        "INSERT INTO items(item_id, entity_id, ring, state, in_use, created_at) "
        "VALUES "
        "('R-0101', 'gh:example-org/adopt-repo', 'adopt', 'active', 1, '2026-10-28T00:00:00Z'), "
        "('R-0102', 'gh:example-org/hold-repo', 'hold', 'active', 1, '2026-10-28T00:00:00Z')"
    )
    ctx.db.commit()
    at_repos = dict(gh_mod.adopt_trial_public_repos(ctx))
    assert "example-org/adopt-repo" in at_repos
    assert "example-org/hold-repo" not in at_repos


def test_fetch_metrics_for_public_and_private_entities(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    http_tr = replay_http()
    gh_tr = replay_gh()
    procs_tr = replay_procs()
    ctx = context.build(
        transports={"http": http_tr, "gh": gh_tr, "procs": procs_tr},
        now=FIXED_NOW,
    )
    ctx.db.execute(
        "INSERT INTO entities("
        "entity_id, kind, ecosystem, name, repo, visibility, created_at"
        ") VALUES "
        "("
        "'pkg:pypi/litellm', 'package', 'pypi', 'litellm', "
        "'berriai/litellm', 'public', '2026-10-28T00:00:00Z'"
        "),"
        "("
        "'pkg:pypi/secret-pkg', 'package', 'pypi', 'secret-pkg', "
        "NULL, 'private', '2026-10-28T00:00:00Z'"
        ")"
    )
    ctx.db.commit()

    # Private entity makes zero outbound calls
    priv_metrics = sources.fetch_metrics(ctx, "pkg:pypi/secret-pkg")
    assert priv_metrics.get("skipped") == "private"
    assert len(http_tr.calls) == 0
    assert len(gh_tr.calls) == 0

    # Public entity fetches registry, pypistats, ecosyste.ms, deps.dev, OSV, and GitHub metrics
    pub_metrics = sources.fetch_metrics(ctx, "pkg:pypi/litellm")
    assert pub_metrics["entity_id"] == "pkg:pypi/litellm"
    assert pub_metrics["downloads_30d"] == 4500000
    assert pub_metrics["stars"] == 14000
    assert pub_metrics["contributors_count"] == 3
    assert pub_metrics["scorecard"] == 6.8
    assert pub_metrics["first_release_at"] is not None
    assert pub_metrics["last_commit_at"] == "2026-10-27T10:00:00Z"


def test_cli_sources_list_and_check(capsys: Any) -> None:
    code_list = cli.main(["sources", "list"])
    assert code_list == 0
    out_list = json.loads(capsys.readouterr().out.strip())
    assert out_list["ok"] is True
    assert out_list["count"] == len(sources.SOURCES)

    code_check = cli.main(["sources", "check"])
    assert code_check == 0
    out_check = json.loads(capsys.readouterr().out.strip())
    assert out_check["ok"] is True
    assert out_check["failed"] == 0


def test_fetch_metrics_private_scope_suppression_and_macos_arm64_helpers(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    http_tr = replay_http()
    gh_tr = replay_gh()
    ctx = context.build(
        transports={"http": http_tr, "gh": gh_tr, "procs": replay_procs()},
        now=FIXED_NOW,
    )
    ctx.profile.private_scopes = ["@acme", "acme-internal"]

    # Matching private_scopes on entity_id/name/repo makes zero network or gh calls
    scoped_npm = sources.fetch_metrics(ctx, "pkg:npm/@acme/secret-ui")
    assert scoped_npm == {"entity_id": "pkg:npm/@acme/secret-ui", "skipped": "private"}
    scoped_repo = sources.fetch_metrics(
        ctx,
        {
            "entity_id": "pkg:pypi/public-looking",
            "name": "public-looking",
            "ecosystem": "pypi",
            "repo": "acme-internal/core",
            "visibility": "public",
        },
    )
    assert scoped_repo == {"entity_id": "pkg:pypi/public-looking", "skipped": "private"}
    assert len(http_tr.calls) == 0
    assert len(gh_tr.calls) == 0

    # PyPI macos_arm64 wheel classification edge cases
    assert registries._check_pypi_macos_arm64([]) == "unknown"
    assert registries._check_pypi_macos_arm64([{"filename": "pkg-1.0-py3-none-any.whl"}]) == "pass"
    assert (
        registries._check_pypi_macos_arm64([{"filename": "pkg-1.0-cp313-macosx_11_0_arm64.whl"}])
        == "pass"
    )
    assert (
        registries._check_pypi_macos_arm64(
            [{"filename": "pkg-1.0-cp313-manylinux_2_17_x86_64.whl"}]
        )
        == "fail"
    )
    assert registries._check_pypi_macos_arm64([{"filename": "pkg-1.0.tar.gz"}]) == "pass"

    # npm macos_arm64 os/cpu classification edge cases
    assert registries._check_npm_macos_arm64({}) == "pass"
    assert registries._check_npm_macos_arm64({"os": ["darwin"], "cpu": ["arm64"]}) == "pass"
    assert registries._check_npm_macos_arm64({"os": ["!darwin"]}) == "fail"
    assert registries._check_npm_macos_arm64({"os": ["linux"]}) == "fail"
    assert registries._check_npm_macos_arm64({"cpu": ["!arm64"]}) == "fail"
    assert registries._check_npm_macos_arm64({"cpu": ["x64"]}) == "fail"


def test_console_dev_json_fallback_and_malformed_json_errors() -> None:
    meta = FetchMeta(
        url="https://console.dev/rss.xml",
        fetched_at="2026-10-28T12:00:00Z",
        source_id="console_dev",
    )
    json_payload = json.dumps(
        {
            "tools": [
                {
                    "id": "tool-1",
                    "name": "atac",
                    "url": "https://github.com/julien-cpsn/atac",
                    "description": "Terminal API client in Rust",
                    "published_at": "2026-10-25T00:00:00Z",
                }
            ]
        }
    ).encode("utf-8")
    sigs = feeds.parse_console_dev(json_payload, meta)
    assert len(sigs) == 1
    assert sigs[0].entity_hint == "gh:julien-cpsn/atac"
    assert sigs[0].external_id == "tool-1"

    with pytest.raises(FetchError, match="invalid console_dev JSON"):
        feeds.parse_console_dev(b"{bad json", meta)


def test_harness_changelogs_includes_watchlist_repos(
    tmp_path: Path,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    gh_fix_dir = tmp_path / "wl_gh"
    gh_fix_dir.mkdir(parents=True)
    (gh_fix_dir / "wl_releases.json").write_text(
        json.dumps(
            {
                "argv": [
                    "gh",
                    "api",
                    "repos/example-org/custom-harness/releases?per_page=5",
                ],
                "returncode": 0,
                "stderr": "",
                "stdout": json.dumps(
                    [
                        {
                            "id": 9001,
                            "tag_name": "v0.9.0",
                            "name": "v0.9.0 — New subagent handoff",
                            "html_url": "https://github.com/example-org/custom-harness/releases/tag/v0.9.0",
                            "published_at": "2026-10-27T00:00:00Z",
                            "body": "Added structured handoff protocol.",
                        }
                    ]
                ),
            }
        ),
        encoding="utf-8",
    )
    ctx = context.build(
        transports={
            "http": replay_http("harness_changelogs"),
            "gh": replay_gh(extra_roots=[gh_fix_dir]),
            "procs": replay_procs(),
        },
        now=FIXED_NOW,
    )
    ctx.db.execute(
        "INSERT INTO entities(entity_id, kind, ecosystem, name, repo, visibility, created_at) "
        "VALUES ('gh:example-org/custom-harness', 'cli', 'github', 'custom-harness', "
        "'example-org/custom-harness', 'public', '2026-10-28T00:00:00Z')"
    )
    ctx.db.execute(
        "INSERT INTO usages(entity_id, context, version, direct, first_seen, last_seen) "
        "VALUES ('gh:example-org/custom-harness', 'interest:watchlist', '*', 1, "
        "'2026-10-28T00:00:00Z', '2026-10-28T00:00:00Z')"
    )
    ctx.db.commit()

    spec = sources.get_source("harness_changelogs")
    sigs = ai._run_harness_changelogs(spec, ctx)
    hints = {s.entity_hint for s in sigs}
    assert "gh:example-org/custom-harness" in hints
