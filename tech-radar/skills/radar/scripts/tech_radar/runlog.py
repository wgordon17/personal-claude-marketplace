"""Run lifecycle logging in the SQLite runs table (Spec 02, Spec 07 §8a)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, timedelta
from typing import TYPE_CHECKING, Any

from . import db

if TYPE_CHECKING:
    from .context import Ctx


def format_run_id(ctx: Ctx, mode: str) -> str:
    """Build a canonical `<YYYYMMDDTHHMMSSZ>-<mode>` run identifier from `ctx.now`."""
    stamp = ctx.now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{mode}"


def start_run(
    ctx: Ctx,
    mode: str,
    requested_mode: str | None = None,
    *,
    run_id: str | None = None,
    t3_thread_id: str | None = None,
    model: str | None = None,
    counts: Mapping[str, Any] | None = None,
) -> str:
    """Insert a `running` row into `runs` and return the `run_id`."""
    req_mode = requested_mode or mode
    if run_id is not None:
        resolved_id = run_id
    else:
        dt = ctx.now.astimezone(UTC)
        resolved_id = f"{dt.strftime('%Y%m%dT%H%M%SZ')}-{mode}"
        offset_sec = 1
        while (
            ctx.db.execute("SELECT 1 FROM runs WHERE run_id = ?", (resolved_id,)).fetchone()
            is not None
        ):
            bumped = dt + timedelta(seconds=offset_sec)
            resolved_id = f"{bumped.strftime('%Y%m%dT%H%M%SZ')}-{mode}"
            offset_sec += 1

    counts_str = (
        db.scrub_text(json.dumps(dict(counts), separators=(",", ":"), sort_keys=True))
        if counts is not None
        else None
    )
    safe_thread = db.scrub_text(t3_thread_id)
    safe_model = db.scrub_text(model)

    ctx.db.execute(
        "INSERT INTO runs("
        "run_id, mode, requested_mode, started_at, finished_at, status, "
        "t3_thread_id, model, usage_json, counts_json, error"
        ") VALUES (?, ?, ?, ?, NULL, 'running', ?, ?, NULL, ?, NULL) "
        "ON CONFLICT(run_id) DO UPDATE SET "
        "mode = excluded.mode, "
        "requested_mode = excluded.requested_mode, "
        "started_at = excluded.started_at, "
        "finished_at = NULL, "
        "status = 'running', "
        "t3_thread_id = COALESCE(excluded.t3_thread_id, runs.t3_thread_id), "
        "model = COALESCE(excluded.model, runs.model), "
        "counts_json = COALESCE(excluded.counts_json, runs.counts_json), "
        "error = NULL",
        (
            resolved_id,
            mode,
            req_mode,
            ctx.now_iso,
            safe_thread,
            safe_model,
            counts_str,
        ),
    )
    ctx.db.commit()
    ctx.run_id = resolved_id
    return resolved_id


def finish_run(
    ctx: Ctx,
    run_id: str,
    status: str,
    counts: Mapping[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Finalize a `runs` row with `finished_at`, `status`, merged `counts_json`, and `error`."""
    row = ctx.db.execute(
        "SELECT counts_json FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()

    merged_counts: dict[str, Any] = {}
    if row is not None and row["counts_json"]:
        try:
            existing = json.loads(str(row["counts_json"]))
            if isinstance(existing, dict):
                merged_counts.update(existing)
        except json.JSONDecodeError:
            pass
    if counts is not None:
        merged_counts.update(dict(counts))

    counts_str = (
        db.scrub_text(json.dumps(merged_counts, separators=(",", ":"), sort_keys=True))
        if merged_counts
        else None
    )
    safe_error = db.scrub_text(error)

    ctx.db.execute(
        "UPDATE runs SET finished_at = ?, status = ?, counts_json = ?, error = ? WHERE run_id = ?",
        (
            ctx.now_iso,
            status,
            counts_str,
            safe_error,
            run_id,
        ),
    )
    ctx.db.commit()
