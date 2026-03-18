#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# ///
"""Guard Stats — actionable metrics from the dev-guard audit database.

Queries ~/.claude/logs/dev-guard.db and formats insights that suggest
specific changes to rules, CLAUDE.md, or infrastructure.
"""

import contextlib
import datetime
import json
import os
import sqlite3
import sys
from pathlib import Path

_DB_PATH = Path(
    os.environ.get("GUARD_DB_PATH", str(Path.home() / ".claude" / "logs" / "dev-guard.db"))
)

# Default to 7 days; accept days as first CLI argument
try:
    _DAYS = max(1, int(sys.argv[1])) if len(sys.argv) > 1 else 7
except (ValueError, OverflowError):
    _DAYS = 7


def _connect() -> sqlite3.Connection | None:
    if not _DB_PATH.exists():
        print("No guard database found. Run some sessions first.")
        return None
    try:
        return sqlite3.connect(str(_DB_PATH), timeout=2)
    except sqlite3.Error:
        print(f"Cannot open {_DB_PATH}")
        return None


def _cutoff() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=_DAYS)
    ).isoformat()


def _section(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


def _guard_decisions(conn: sqlite3.Connection, since: str) -> None:
    rows = conn.execute(
        "SELECT category, action, COUNT(*) FROM events "
        "WHERE ts > ? GROUP BY category, action ORDER BY COUNT(*) DESC",
        (since,),
    ).fetchall()
    if not rows:
        return

    _section("Guard Decisions")

    total = sum(r[2] for r in rows)
    blocked = sum(r[2] for r in rows if r[1] == "blocked")
    asked = sum(r[2] for r in rows if r[1] in ("ask", "asked"))
    bypassed = sum(r[2] for r in rows if r[1] == "bypassed")
    block_pct = 100 * blocked // max(total, 1)
    print(f"  Total: {total:,}   Blocked: {blocked:,} ({block_pct}%)")
    print(f"  Asked: {asked:,}   Bypassed: {bypassed:,}")

    # Top blocked rules — these are the actionable ones
    top_blocks = conn.execute(
        "SELECT rule, COUNT(*) as n FROM events "
        "WHERE ts > ? AND action = 'blocked' AND rule IS NOT NULL "
        "GROUP BY rule ORDER BY n DESC LIMIT 5",
        (since,),
    ).fetchall()
    if top_blocks:
        print("  Top blocks (consider CLAUDE.md reinforcement):")
        for rule, count in top_blocks:
            print(f"    {rule:30s} {count:>5,}")


def _trust_insights(conn: sqlite3.Connection, since: str) -> None:
    # Most-asked rules that haven't been trusted
    top_asks = conn.execute(
        "SELECT rule, COUNT(*) as n FROM events "
        "WHERE ts > ? AND action = 'ask' AND rule IS NOT NULL "
        "GROUP BY rule ORDER BY n DESC LIMIT 5",
        (since,),
    ).fetchall()
    if not top_asks:
        return

    _section("Trust Opportunities")

    trusted_rules = {
        r[0] for r in conn.execute("SELECT DISTINCT rule_name FROM trusted_rules").fetchall()
    }
    for rule, count in top_asks:
        status = "trusted" if rule in trusted_rules else "not trusted"
        if status == "not trusted" and count >= 5:
            print(f"  {rule:30s} asked {count:>4,}x — consider: /trust add {rule}")


def _rtk_stats(conn: sqlite3.Connection, since: str) -> None:
    rows = conn.execute(
        "SELECT event_type, COUNT(*) FROM rtk_events WHERE ts > ? GROUP BY event_type",
        (since,),
    ).fetchall()
    if not rows:
        return

    _section("RTK Compression")

    stats = dict(rows)
    compressed = stats.get("compressed", 0)
    full_reads = stats.get("full_read", 0)
    if compressed == 0:
        return

    expansion_pct = 100 * full_reads // compressed
    print(f"  Compressed: {compressed:,}   Full reads: {full_reads:,} ({expansion_pct}% expansion)")
    if expansion_pct > 25:
        print("  WARNING: >25% expansion rate — agents are re-reading compressed output frequently")

    # Top compressed commands
    top = conn.execute(
        "SELECT command, COUNT(*) as n FROM rtk_events "
        "WHERE ts > ? AND event_type = 'compressed' "
        "GROUP BY command ORDER BY n DESC LIMIT 5",
        (since,),
    ).fetchall()
    if top:
        print("  Top compressed:")
        for cmd, count in top:
            print(f"    {(cmd or '')[:50]:50s} {count:>4,}")


def _stop_hook_stats(conn: sqlite3.Connection, since: str) -> None:
    rows = conn.execute(
        "SELECT outcome, COUNT(*) FROM stop_hook_events WHERE ts > ? GROUP BY outcome",
        (since,),
    ).fetchall()
    if not rows:
        return

    _section("Stop Hook")

    stats = dict(rows)
    total = sum(stats.values())
    passes = stats.get("llm_pass", 0)
    fails = stats.get("llm_fail", 0)
    errors = stats.get("llm_error", 0)
    print(f"  LLM evaluations: {total}   Pass: {passes}   Fail: {fails}   Error: {errors}")
    print("  (fast-exit invocations not counted — only events reaching LLM)")

    if errors > 0:
        error_pct = 100 * errors // total
        print(
            f"  ERROR RATE: {error_pct}% — LLM may be unreachable, "
            f"stop hook is silently failing open"
        )

    # Average LLM time (exclude errors)
    row = conn.execute(
        "SELECT AVG(llm_duration_ms) FROM stop_hook_events "
        "WHERE ts > ? AND outcome != 'llm_error' AND llm_duration_ms IS NOT NULL",
        (since,),
    ).fetchone()
    if row and row[0]:
        print(f"  Avg LLM time: {row[0]:.0f}ms")

    # Recent failures
    failures = conn.execute(
        "SELECT ts, detail FROM stop_hook_events "
        "WHERE ts > ? AND outcome = 'llm_fail' AND detail IS NOT NULL "
        "ORDER BY ts DESC LIMIT 3",
        (since,),
    ).fetchall()
    if failures:
        print("  Recent catches:")
        for ts, detail in failures:
            try:
                findings = json.loads(detail)
                first = findings[0] if isinstance(findings, list) and findings else str(detail)[:60]
            except (json.JSONDecodeError, IndexError):
                first = str(detail)[:60]
            print(f"    {ts[:10]}: {first}")


def _session_summaries(conn: sqlite3.Connection, since: str) -> None:
    rows = conn.execute(
        "SELECT key, value FROM session_state WHERE key LIKE 'summary:%' AND updated_ts > ?",
        (since,),
    ).fetchall()
    if not rows:
        return

    _section("Session Summaries")

    total_tools = 0
    total_blocked = 0
    total_asked = 0
    cwds: dict[str, int] = {}
    for _key, value in rows:
        try:
            s = json.loads(value)
            total_tools += s.get("tool_calls", 0)
            total_blocked += s.get("blocked", 0)
            total_asked += s.get("asked", 0)
            cwd = s.get("cwd")
            if cwd:
                # Shorten to last 2 path components
                short = "/".join(Path(cwd).parts[-2:])
                cwds[short] = cwds.get(short, 0) + 1
        except (json.JSONDecodeError, TypeError):
            continue

    print(f"  Sessions: {len(rows)}   Guarded tool calls: {total_tools:,}")
    print(f"  Blocked: {total_blocked:,}   Asked: {total_asked:,}")
    if total_tools > 0:
        friction = 100 * (total_blocked + total_asked) // total_tools
        print(f"  Friction rate: {friction}% of tool calls hit a guard rule")

    if cwds:
        print("  By project:")
        for cwd, count in sorted(cwds.items(), key=lambda x: -x[1])[:5]:
            print(f"    {cwd:40s} {count} session(s)")


def main() -> None:
    conn = _connect()
    if conn is None:
        sys.exit(0)

    since = _cutoff()
    print(f"Dev Guard Stats (last {_DAYS} days)")
    print("=" * 40)

    for section_fn in (
        _guard_decisions,
        _trust_insights,
        _rtk_stats,
        _stop_hook_stats,
        _session_summaries,
    ):
        with contextlib.suppress(sqlite3.OperationalError):
            section_fn(conn, since)

    print()
    conn.close()


if __name__ == "__main__":
    main()
