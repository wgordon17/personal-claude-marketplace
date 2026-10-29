"""Run planning (`plan-run`), `--run` validation, T3 model check, and FTS5 search (Spec 05 §7)."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from . import appsync, brief, db, decisions, errors, lock, profile, runlog, sanitize, usage

if TYPE_CHECKING:
    from .context import Ctx

RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z-(daily|weekly|monthly)$")
EXPECTED_MODEL = "gemini-4-argon"


_parse_iso = db.parse_iso


def validate_run_id(ctx: Ctx, run_id: str | None) -> str:
    """Validate `--run ID` against `RUN_ID_RE` and an existing `runs` row (Spec 12 A25)."""
    if not run_id or not isinstance(run_id, str):
        raise errors.ValidationError([{"field": "run", "error": "run_id is required"}])
    clean = run_id.strip()
    if not RUN_ID_RE.match(clean):
        raise errors.ValidationError([{"field": "run", "error": f"invalid run_id: {clean}"}])

    row = ctx.db.execute(
        "SELECT 1 FROM runs WHERE run_id = ?",
        (clean,),
    ).fetchone()
    if row is None:
        raise errors.ValidationError([{"field": "run", "error": f"run_id not found: {clean}"}])
    return clean


def resolve_t3_model(
    ctx: Ctx,
    thread_id: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Read `(resolved_thread_id, model, model_warning)` from `statev2.sqlite` (Spec 12 A18)."""
    t3_db = ctx.paths.home / ".t3" / "userdata" / "statev2.sqlite"
    if not t3_db.is_file():
        return thread_id, None, None

    resolved_thread = thread_id.strip() if thread_id and thread_id.strip() else None
    model: str | None = None

    try:
        conn = sqlite3.connect(f"file:{t3_db}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            if resolved_thread:
                row = conn.execute(
                    "SELECT thread_id, "
                    "json_extract(payload_json, '$.modelSelection.model') AS model "
                    "FROM orchestration_v2_projection_threads "
                    "WHERE thread_id = ? AND deleted_at IS NULL LIMIT 1",
                    (resolved_thread,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT t.thread_id, "
                    "json_extract(t.payload_json, '$.modelSelection.model') AS model "
                    "FROM orchestration_v2_projection_threads t "
                    "JOIN projection_projects p ON p.project_id = t.project_id "
                    "WHERE p.workspace_root = ? "
                    "AND p.deleted_at IS NULL "
                    "AND t.deleted_at IS NULL "
                    "AND t.thread_id NOT LIKE 'thread:provider:%' "
                    "ORDER BY t.updated_at DESC, t.created_at DESC "
                    "LIMIT 1",
                    (str(ctx.paths.run_root),),
                ).fetchone()

            if row is not None:
                resolved_thread = str(row["thread_id"])
                if row["model"]:
                    model = str(row["model"]).strip()

            if resolved_thread and not model:
                s_row = conn.execute(
                    "SELECT model FROM orchestration_v2_projection_provider_sessions "
                    "WHERE thread_id = ? AND model IS NOT NULL "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (resolved_thread,),
                ).fetchone()
                if s_row is not None and s_row["model"]:
                    model = str(s_row["model"]).strip()
        finally:
            conn.close()
    except sqlite3.Error:
        return resolved_thread, None, None

    model_warning: str | None = None
    if model and model != EXPECTED_MODEL:
        model_warning = f"model warning: expected {EXPECTED_MODEL}, got {model}"
    return resolved_thread, model, model_warning


def select_effective_mode(
    ctx: Ctx,
    requested: str = "daily",
) -> tuple[str, bool]:
    """Select effective run mode (`daily|weekly|monthly`) and `catch_up` flag (Spec 05 §7, A37)."""
    now = ctx.now
    req = requested.strip().lower() if requested else "daily"

    # Calendar mode rules
    if req == "monthly" or (now.weekday() == 0 and 1 <= now.day <= 7):
        effective = "monthly"
    elif req == "weekly" or now.weekday() == 0:
        effective = "weekly"
    else:
        effective = "daily"

    prior_rows = ctx.db.execute(
        "SELECT run_id, mode, started_at, status FROM runs "
        "WHERE mode IN ('daily', 'weekly', 'monthly') AND status IN ('ok', 'degraded') "
        "ORDER BY started_at DESC"
    ).fetchall()

    # First-ever plan-run: overdue and catch-up rules apply only once prior runs exist (A37)
    if not prior_rows:
        return effective, False

    last_monthly_dt: datetime | None = None
    last_weekly_dt: datetime | None = None
    last_any_dt: datetime | None = None

    for r in prior_rows:
        dt = _parse_iso(str(r["started_at"]))
        if dt is None:
            continue
        if last_any_dt is None:
            last_any_dt = dt
        m = str(r["mode"])
        if m == "monthly" and last_monthly_dt is None:
            last_monthly_dt = dt
        if m in ("weekly", "monthly") and last_weekly_dt is None:
            last_weekly_dt = dt

    if last_monthly_dt is not None and (now - last_monthly_dt) > timedelta(days=35):
        effective = "monthly"
    elif (
        effective == "daily"
        and last_weekly_dt is not None
        and (now - last_weekly_dt) > timedelta(days=8)
    ):
        effective = "weekly"

    catch_up = bool(last_any_dt is not None and (now - last_any_dt) > timedelta(hours=30))
    return effective, catch_up


def _detect_stalled_runs(ctx: Ctx) -> list[str]:
    """Find `running` daily/weekly/monthly runs older than 2 hours (Spec 12 A26)."""
    cutoff_2h = (ctx.now - timedelta(hours=2)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = ctx.db.execute(
        "SELECT run_id FROM runs "
        "WHERE mode IN ('daily', 'weekly', 'monthly') "
        "AND status = 'running' AND started_at < ? "
        "ORDER BY started_at ASC",
        (cutoff_2h,),
    ).fetchall()
    stalled: list[str] = []
    for r in rows:
        rid = str(r["run_id"])
        stalled.append(rid)
        ctx.db.execute(
            "UPDATE runs SET status = 'failed', finished_at = ?, error = 'stalled' "
            "WHERE run_id = ?",
            (ctx.now_iso, rid),
        )
    if stalled:
        ctx.db.commit()
    return stalled


def plan_run(
    ctx: Ctx,
    *,
    requested: str = "daily",
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Execute `radar.py plan-run` per Spec 05 §7 and Spec 12 A18, A25, A26, A31."""
    coverage_warnings: list[str] = []

    # 1. App sync runs first; refused or skipped sync never aborts plan-run (A25, A31)
    sync_res = appsync.sync(ctx)
    if sync_res.coverage_warning:
        coverage_warnings.append(sync_res.coverage_warning)

    # 2. Detect prior stalled running rows (>2h old, A26)
    stalled_runs = _detect_stalled_runs(ctx)

    # 3. Effective mode and catch_up
    mode, catch_up = select_effective_mode(ctx, requested=requested)
    candidate_run_id = runlog.format_run_id(ctx, mode)

    # 4. Acquire persistent run.lock (breaks stale >2h lock once; NOT released here)
    acquired = lock.acquire(
        "run",
        owner=candidate_run_id,
        mode=mode,
        state_dir=ctx.paths.state,
        now=ctx.now,
    )
    if acquired is None:
        holder = lock.read_holder("run", state_dir=ctx.paths.state) or {}
        holder_id = str(holder.get("run_id") or holder.get("owner") or "unknown")
        return {
            "ok": False,
            "error": "run in progress",
            "run_id": holder_id,
            "_exit_code": 4,
        }

    if acquired.broke_stale and acquired.broke_stale not in stalled_runs:
        stalled_runs.append(acquired.broke_stale)
        ctx.db.execute(
            "UPDATE runs SET status = 'failed', finished_at = ?, error = 'stalled' "
            "WHERE run_id = ? AND status = 'running'",
            (ctx.now_iso, acquired.broke_stale),
        )
        ctx.db.commit()

    if stalled_runs:
        coverage_warnings.append(f"stalled run(s): {', '.join(stalled_runs)}")

    # 5. Stage DECISIONS.md edits (Spec 05 §6, A41)
    dec_stage = decisions.stage_decisions_md(ctx)
    pending_total = int(
        ctx.db.execute(
            "SELECT COUNT(*) FROM pending WHERE status IN ('staged', 'batched')"
        ).fetchone()[0]
    )

    # 6. Resolve T3 thread_id and model (A18)
    resolved_thread, model, model_warning = resolve_t3_model(ctx, thread_id=thread_id)
    if model_warning:
        coverage_warnings.append(model_warning)

    # 7. Compute previous run usage (Spec 11 §7) & purge HTTP cache > 14d (MEM-09)
    prev_usage = usage.compute_previous_run_usage(ctx, current_run_id=candidate_run_id)
    if ctx.cache_db is not None:
        db.purge_cache(ctx.cache_db, days=14, now=ctx.now)

    # 8. Start run row in `runs`
    counts_payload: dict[str, Any] = {
        "cwd": os.getcwd(),
        "t3_thread_id": resolved_thread,
        "catch_up": catch_up,
        "pending": pending_total,
        "decisions_staged": dec_stage["staged"],
        "decisions_malformed": dec_stage["malformed"],
        "stalled_runs": stalled_runs,
        "app_sync_status": sync_res.status,
        "coverage_warnings": coverage_warnings,
    }
    run_id = runlog.start_run(
        ctx,
        mode=mode,
        requested_mode=requested,
        run_id=candidate_run_id,
        t3_thread_id=resolved_thread,
        model=model,
        counts=counts_payload,
    )

    # Ensure lockfile owner matches final run_id if bumped by 1s
    if run_id != candidate_run_id:
        lock.release(acquired)
        reacquired = lock.acquire(
            "run",
            owner=run_id,
            mode=mode,
            state_dir=ctx.paths.state,
            now=ctx.now,
        )
        if reacquired is not None:
            acquired = reacquired

    if acquired.path.is_file():
        ts = ctx.now.timestamp()
        os.utime(acquired.path, (ts, ts))

    return {
        "ok": True,
        "run_id": run_id,
        "mode": mode,
        "requested_mode": requested,
        "catch_up": catch_up,
        "pending": pending_total,
        "decisions_staged": dec_stage["staged"],
        "decisions_malformed": dec_stage["malformed"],
        "t3_thread_id": resolved_thread,
        "model": model,
        "model_warning": model_warning,
        "stalled_runs": stalled_runs,
        "app_sync": sync_res.status,
        "coverage_warnings": coverage_warnings,
        "usage_last_run": prev_usage,
    }


def search(ctx: Ctx, query: str) -> dict[str, Any]:
    """Run FTS5 search over `signals_fts` and `digests_fts` with Spec 12 A8 privacy aliasing."""
    clean_q = str(query or "").strip()
    if not clean_q:
        return {"ok": True, "query": "", "count": 0, "results": []}

    priv_map = brief._private_scrub_map(ctx)
    priv_scopes = profile.load_private_scopes(ctx.db)
    if brief._is_priv_str(clean_q, priv_map, priv_scopes):
        return {
            "ok": True,
            "query": brief.scrub_private_text(clean_q, priv_map),
            "count": 0,
            "results": [],
        }

    proj_lookup = brief._project_alias_lookup(ctx)

    sig_rows: list[Any] = []
    try:
        sig_rows = ctx.db.execute(
            "SELECT s.signal_id, s.entity_id, s.source_id, s.type, s.title, s.url, "
            "s.published_at, s.untrusted_excerpt, s.payload_json, "
            "e.name AS entity_name, e.kind AS entity_kind, e.visibility AS entity_vis "
            "FROM signals_fts f "
            "JOIN signals s ON s.signal_id = f.rowid "
            "LEFT JOIN entities e ON e.entity_id = s.entity_id "
            "WHERE signals_fts MATCH ? AND s.withdrawn = 0 "
            "ORDER BY s.signal_id DESC LIMIT 25",
            (clean_q,),
        ).fetchall()
    except sqlite3.OperationalError:
        like_pat = f"%{clean_q}%"
        sig_rows = ctx.db.execute(
            "SELECT s.signal_id, s.entity_id, s.source_id, s.type, s.title, s.url, "
            "s.published_at, s.untrusted_excerpt, s.payload_json, "
            "e.name AS entity_name, e.kind AS entity_kind, e.visibility AS entity_vis "
            "FROM signals s LEFT JOIN entities e ON e.entity_id = s.entity_id "
            "WHERE (s.title LIKE ? OR s.untrusted_excerpt LIKE ?) AND s.withdrawn = 0 "
            "ORDER BY s.signal_id DESC LIMIT 25",
            (like_pat, like_pat),
        ).fetchall()

    results: list[dict[str, Any]] = []
    for r in sig_rows:
        sig_type = str(r["type"])
        vis = str(r["entity_vis"] or "public")
        eid = str(r["entity_id"] or "")

        # Spec 12 A8: inventory_change rows for private packages or non-public projects excluded
        if sig_type == "inventory_change":
            if vis != "public":
                continue
            raw_title = str(r["title"] or "")
            raw_payload = str(r["payload_json"] or "")
            is_priv_proj_change = False
            if raw_payload:
                try:
                    p_obj = json.loads(raw_payload)
                    if isinstance(p_obj, dict):
                        p_ctx = str(p_obj.get("context") or "")
                        if p_ctx.startswith("project:"):
                            pkey = p_ctx[len("project:") :]
                            if pkey in proj_lookup and proj_lookup[pkey][1] != "public":
                                is_priv_proj_change = True
                except json.JSONDecodeError:
                    pass
            if not is_priv_proj_change:
                for pkey, (_alias, p_vis) in proj_lookup.items():
                    if p_vis != "public" and pkey in raw_title:
                        is_priv_proj_change = True
                        break
            if is_priv_proj_change:
                continue

        display_entity = eid
        if vis != "public" and eid:
            # Resolve project alias or item alias for private entity
            u_rows = ctx.db.execute(
                "SELECT context FROM usages WHERE entity_id = ?",
                (eid,),
            ).fetchall()
            alias_found: str | None = None
            for ur in u_rows:
                uctx = str(ur["context"])
                if uctx.startswith("project:"):
                    pkey = uctx[len("project:") :]
                    if pkey in proj_lookup:
                        alias_found = proj_lookup[pkey][0]
                        break
            if not alias_found:
                item_r = ctx.db.execute(
                    "SELECT item_id FROM items WHERE entity_id = ?",
                    (eid,),
                ).fetchone()
                if item_r is not None:
                    alias_found, _v, _ip = decisions.alias_for_item(ctx, str(item_r["item_id"]))
            display_entity = alias_found or "private-project-1"
            if eid:
                priv_map[eid] = display_entity
                priv_map[eid.lower()] = display_entity
            if r["entity_name"]:
                ename = str(r["entity_name"])
                priv_map[ename] = display_entity
                priv_map[ename.lower()] = display_entity

        safe_title = brief.scrub_private_text(
            sanitize.clean(str(r["title"] or ""), limit=200),
            priv_map,
        )
        safe_exc = brief.scrub_private_text(
            sanitize.clean(str(r["untrusted_excerpt"] or ""), limit=300),
            priv_map,
        )
        results.append(
            {
                "kind": "signal",
                "ref": f"S-{r['signal_id']}",
                "type": sig_type,
                "entity": brief.scrub_private_text(display_entity, priv_map),
                "visibility": "private" if vis != "public" else "public",
                "title": safe_title,
                "excerpt": safe_exc,
                "published_at": r["published_at"],
            }
        )

    # Search digests_fts as well
    dig_rows: list[Any] = []
    try:
        dig_rows = ctx.db.execute(
            "SELECT d.run_id, d.markdown FROM digests_fts f "
            "JOIN digests d ON d.rowid = f.rowid "
            "WHERE digests_fts MATCH ? LIMIT 10",
            (clean_q,),
        ).fetchall()
    except sqlite3.OperationalError:
        dig_rows = ctx.db.execute(
            "SELECT run_id, markdown FROM digests WHERE markdown LIKE ? LIMIT 10",
            (f"%{clean_q}%",),
        ).fetchall()

    for dr in dig_rows:
        results.append(
            {
                "kind": "digest",
                "run_id": str(dr["run_id"]),
                "excerpt": brief.scrub_private_text(
                    sanitize.clean(str(dr["markdown"] or ""), limit=300),
                    priv_map,
                ),
            }
        )

    return {
        "ok": True,
        "query": brief.scrub_private_text(clean_q, priv_map),
        "count": len(results),
        "results": results,
    }
