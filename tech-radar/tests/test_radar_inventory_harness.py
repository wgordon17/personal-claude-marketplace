"""Tests for AI harness inventory and CLI/code fingerprints (Task 4 Step 5, Spec 04, A12, A13)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
import secret_samples
from tech_radar import context, http, inventory, procs

FIXED_NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)


def _make_ctx(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> context.Ctx:
    return context.build(
        transports={
            "http": replay_http("classify"),
            "gh": replay_gh(),
            "procs": replay_procs(),
        },
        now=FIXED_NOW,
    )


def test_harness_mcp_transcript_metrics_and_cli_fingerprints(
    fixture_home: Path,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    _ = fixture_home
    ctx = _make_ctx(replay_http, replay_gh, replay_procs)
    inventory.collect(ctx)

    # 1. MCP names recorded without header/env values
    mcp_rows = ctx.db.execute(
        "SELECT e.entity_id, e.name, u.version, u.attrs_json "
        "FROM entities e JOIN usages u ON e.entity_id = u.entity_id "
        "WHERE e.kind = 'mcp'"
    ).fetchall()
    mcp_ids = {str(r["entity_id"]) for r in mcp_rows}
    assert "mcp:github" in mcp_ids
    assert "mcp:custom-uvx" in mcp_ids

    db_dump = "\n".join(ctx.db.iterdump())
    assert "TOOL_RESULT_SECRET_CANARY" not in db_dump
    for secret_name, canary in secret_samples.raw_canary_substrings().items():
        assert canary not in db_dump, f"secret {secret_name} leaked into DB"

    # 2. Metrics has harness:tool/Bash token and byte totals (A13)
    bash_metrics = {
        str(r["metric"]): float(r["value"])
        for r in ctx.db.execute(
            "SELECT metric, value FROM metrics WHERE entity_id = 'harness:tool/Bash'"
        ).fetchall()
    }
    assert "tokens" in bash_metrics and bash_metrics["tokens"] > 0
    assert "bytes" in bash_metrics and bash_metrics["bytes"] > 0

    # 3. CLI subcommand/flag fingerprints (A12) with no flag values
    fp_rows = ctx.db.execute("SELECT context, kind, token FROM fingerprints").fetchall()
    subcommands = {str(r["token"]) for r in fp_rows if r["kind"] == "subcommand"}
    flags = {str(r["token"]) for r in fp_rows if r["kind"] == "flag"}

    assert "uv sync" in subcommands
    assert "--frozen" in flags
    assert "gh pr" in subcommands
    assert "--state" in flags
    assert "open" not in flags
    assert not any("=" in f for f in flags)

    # 4. Nothing derived from shell history beyond command names
    hist_rows = [r for r in fp_rows if r["context"] == "system:shell-history"]
    assert hist_rows
    assert all(r["kind"] == "cmd" for r in hist_rows)


def test_t3_db_handle_is_read_only_and_unknown_schema_records_skipped_reason(
    fixture_home: Path,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    t3_db = fixture_home / ".t3" / "userdata" / "statev2.sqlite"
    ro_conn = inventory.harness.open_t3_db(t3_db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro_conn.execute("CREATE TABLE should_fail(id INTEGER)")
    finally:
        ro_conn.close()

    # Replace statev2.sqlite with an unrecognized schema
    t3_db.unlink()
    bad_conn = sqlite3.connect(str(t3_db))
    bad_conn.execute("CREATE TABLE unrelated_table(x TEXT)")
    bad_conn.commit()
    bad_conn.close()

    ctx = _make_ctx(replay_http, replay_gh, replay_procs)
    inventory.harness.collect(ctx)

    row = ctx.db.execute(
        "SELECT skipped_reason FROM source_health WHERE source_id = 't3_state_db'"
    ).fetchone()
    assert row is not None
    assert row["skipped_reason"] == "t3 schema not recognized"
