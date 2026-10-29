"""Tests for Profile loader, visibility classification, and seeds (Task 4 Steps 1 & 6)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tech_radar import context, db, http, inventory, procs
from tech_radar.profile import MAX_PROFILE_BYTES, Profile, save_private_scopes

FIXED_NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)


def test_profile_parses_all_sections_ignores_markdown_private_scopes_and_truncates(
    tmp_path: Path,
) -> None:
    sync_dir = tmp_path / "sync_profile"
    sync_dir.mkdir(parents=True, exist_ok=True)
    conn = db.connect(":memory:", auto_migrate=True)
    save_private_scopes(conn, ["@corp-internal", "secret-org/*"])

    taste_md = (
        "# Taste Profile\n\n"
        "## Watchlist\n"
        "- astral-sh/uv\n\n"
        "## Preferences\n"
        "- Fast CLI tools <script>alert(1)</script>‮\n\n"
        "## Anti-interests\n"
        "- Bloated SDKs\n\n"
        "## Taste-makers\n"
        "- @simonw\n\n"
        "## Categories\n"
        "- python-tooling\n\n"
        "## Languages\n"
        "- python\n\n"
        "## Subscriptions\n"
        "- Claude Max\n\n"
        "## Private scopes\n"
        "- markdown-scope-must-be-ignored\n"
    )
    (sync_dir / "TASTE_PROFILE.md").write_text(taste_md, encoding="utf-8")
    (sync_dir / "WATCHLIST.md").write_text("- pkg:pypi/pydantic\n", encoding="utf-8")

    prof = Profile.load(sync_dir, conn=conn)
    assert "astral-sh/uv" in prof.watchlist
    assert "pkg:pypi/pydantic" in prof.watchlist
    assert prof.preferences == ["Fast CLI tools alert(1)"]
    assert "<" not in prof.preferences[0]
    assert "‮" not in prof.preferences[0]
    assert prof.anti_interests == ["Bloated SDKs"]
    assert prof.taste_makers == ["@simonw"]
    assert prof.categories == ["python-tooling"]
    assert prof.languages == ["python"]
    assert prof.subscriptions == ["Claude Max"]
    # Markdown Private scopes section is ignored; comes strictly from kv['private_scopes']
    assert "markdown-scope-must-be-ignored" not in prof.private_scopes
    assert prof.private_scopes == ["@corp-internal", "secret-org/*"]

    # 20 KB file is truncated to 16 KB (MAX_PROFILE_BYTES)
    padding = "\n".join(f"- pref-{i:04d}-" + ("x" * 60) for i in range(350))
    big_md = "## Preferences\n" + padding + "\n- tail-beyond-16kb\n"
    assert len(big_md.encode("utf-8")) > 20 * 1024
    assert len(big_md.encode("utf-8")) > MAX_PROFILE_BYTES
    (sync_dir / "TASTE_PROFILE.md").write_text(big_md, encoding="utf-8")

    prof_big = Profile.load(sync_dir, conn=conn)
    assert "tail-beyond-16kb" not in prof_big.preferences


def test_classify_visibility_private_scopes_retry_on_5xx_and_public_terms(
    fixture_home: Path,
    tmp_path: Path,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    _ = fixture_home
    ctx = context.build(
        transports={
            "http": replay_http("classify"),
            "gh": replay_gh(),
            "procs": replay_procs(),
        },
        now=FIXED_NOW,
    )
    save_private_scopes(ctx.db, ["@corp-scope", "internal-team"])
    inventory.collect(ctx)

    # 1. Unknown-registry name (secret-widget-core -> 404) is classified private
    row_swc = ctx.db.execute(
        "SELECT visibility FROM entities WHERE entity_id = 'pkg:pypi/secret-widget-core'"
    ).fetchone()
    assert row_swc is not None
    assert row_swc["visibility"] == "private"

    # 2. Name matching kv['private_scopes'] is classified private without network calls
    ctx.db.execute(
        "INSERT INTO entities(entity_id, kind, ecosystem, name, visibility, created_at) "
        "VALUES ('pkg:npm/@corp-scope/auth', 'package', 'npm', '@corp-scope/auth', 'unknown', ?)",
        (ctx.now_iso,),
    )
    ctx.db.commit()
    calls_before = len(ctx.http.calls)
    assert inventory.classify.visibility(ctx, "pkg:npm/@corp-scope/auth") == "private"
    assert len(ctx.http.calls) == calls_before

    # 3. Registry 5xx/timeout replay -> private (fail closed) and re-checked next run
    flaky_url = "https://pypi.org/simple/flaky-pkg/"
    flaky_key = http.replay_key("GET", flaky_url)
    err_root = tmp_path / "http_503"
    err_root.mkdir(parents=True, exist_ok=True)
    (err_root / f"{flaky_key}.json").write_text('{"status": 503}\n', encoding="utf-8")

    ctx.db.execute(
        "INSERT INTO entities(entity_id, kind, ecosystem, name, visibility, created_at) "
        "VALUES ('pkg:pypi/flaky-pkg', 'package', 'pypi', 'flaky-pkg', 'unknown', ?)",
        (ctx.now_iso,),
    )
    ctx.db.commit()

    ctx_503 = context.build(
        transports={
            "http": replay_http("classify", extra_roots=[err_root]),
            "gh": replay_gh(),
            "procs": replay_procs(),
        },
        now=FIXED_NOW,
        db_conn=ctx.db,
        cache_conn=ctx.cache_db,
    )
    assert inventory.classify.visibility(ctx_503, "pkg:pypi/flaky-pkg") == "private"
    cached_check = ctx.db.execute(
        "SELECT 1 FROM metrics "
        "WHERE entity_id = 'pkg:pypi/flaky-pkg' AND metric = 'visibility_checked'"
    ).fetchone()
    assert cached_check is None

    # Next run with 200 fixture re-checks and promotes to public
    ok_root = tmp_path / "http_200"
    ok_root.mkdir(parents=True, exist_ok=True)
    (ok_root / f"{flaky_key}.json").write_text('{"status": 200, "body": "ok"}\n', encoding="utf-8")
    ctx_200 = context.build(
        transports={
            "http": replay_http("classify", extra_roots=[ok_root]),
            "gh": replay_gh(),
            "procs": replay_procs(),
        },
        now=FIXED_NOW + timedelta(hours=1),
        db_conn=ctx.db,
        cache_conn=ctx.cache_db,
    )
    assert inventory.classify.visibility(ctx_200, "pkg:pypi/flaky-pkg") == "public"

    # 4. public_terms() excludes project names/paths/local: keys and secret-widget*
    terms = inventory.classify.public_terms(ctx_200)
    assert "litellm" in terms
    assert "python-tooling" in terms
    for t in terms:
        assert not t.startswith("local:")
        assert "/" not in t or not t.startswith("/Users")
        assert "secret-widget" not in t.lower()
        assert "public-app" not in t.lower()
        assert "evil-repo" not in t.lower()

    # 5. Starred repos appear under interest:starred, watched under interest:watched,
    # watchlist under interest:watchlist, taste-makers, pain bigrams, and bin:mytool repo link
    starred = {
        str(r["entity_id"])
        for r in ctx.db.execute(
            "SELECT entity_id FROM usages WHERE context = 'interest:starred'"
        ).fetchall()
    }
    assert "gh:astral-sh/uv" in starred
    assert "gh:example-org/mytool" in starred

    watched = {
        str(r["entity_id"])
        for r in ctx.db.execute(
            "SELECT entity_id FROM usages WHERE context = 'interest:watched'"
        ).fetchall()
    }
    assert watched

    watchlist = {
        str(r["entity_id"])
        for r in ctx.db.execute(
            "SELECT entity_id FROM usages WHERE context = 'interest:watchlist'"
        ).fetchall()
    }
    assert watchlist

    mytool_repo = ctx.db.execute(
        "SELECT repo FROM entities WHERE entity_id = 'bin:mytool'"
    ).fetchone()
    assert mytool_repo is not None
    assert mytool_repo["repo"] == "example-org/mytool"

    all_tokens = {
        str(r["token"]) for r in ctx.db.execute("SELECT token FROM fingerprints").fetchall()
    }
    assert any(t.startswith("tastemaker:") for t in all_tokens)
    assert any(t.startswith("bigram:") for t in all_tokens)
