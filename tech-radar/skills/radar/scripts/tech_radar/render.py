"""Digest rendering, Markdown projections, BYOR/HTML exports, and run finalization (Spec 09)."""

from __future__ import annotations

import contextlib
import hashlib
import html
import json
import math
import os
import urllib.parse
from collections.abc import Mapping, Sequence
from datetime import UTC, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import (
    brief,
    db,
    decisions,
    doctor,
    errors,
    fsguard,
    items,
    lock,
    procs,
    redact,
    runlog,
    sanitize,
    snapshot,
    verdict,
)

if TYPE_CHECKING:
    from .context import Ctx

_PKG_DIR = Path(__file__).resolve().parent
_RADAR_HTML_TMPL_PATH = _PKG_DIR / "templates" / "radar.html.tmpl"

RING_ORDER: tuple[str, ...] = ("adopt", "trial", "assess", "watch", "hold")
SECURITY_ALERT_KINDS: tuple[str, ...] = (
    "compromise",
    "incident",
    "kev",
    "integrity",
    "app_integrity",
    "chatter",
)
TIER_SORT_KEY: dict[str, int] = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}

QUADRANT_LABELS: dict[str, str] = {
    "languages-libraries": "Languages & Libraries",
    "cli-dev-tools": "CLI & Dev Tools",
    "ai-agents-harness": "AI Agents & Harness",
    "skills-mcp-plugins": "Skills, MCP & Plugins",
    "platforms-services": "Platforms & Services",
    "techniques": "Techniques",
}


def _safe_agent_str(text: str | None, priv_map: Mapping[str, str], *, limit: int = 400) -> str:
    """Scrub secrets, replace private identifiers with aliases, and strip HTML/controls."""
    if not text:
        return ""
    cleaned = sanitize.clean(str(text), limit=limit)
    return brief.scrub_private_text(cleaned, priv_map)


def _safe_sync_str(text: str | None, *, limit: int = 400) -> str:
    """Scrub secrets and strip HTML/controls for non-agent-readable sync projections."""
    if not text:
        return ""
    return redact.scrub(sanitize.clean(str(text), limit=limit))


def _safe_fence(text: str | None, priv_map: Mapping[str, str] | None = None) -> str:
    """Scrub secrets (and private identifiers when `priv_map` is provided) then fence."""
    raw = redact.scrub(str(text or ""))
    if priv_map is not None:
        raw = brief.scrub_private_text(raw, priv_map)
    return sanitize.fence(raw)


def _safe_https_url(url: str | None, fallback: str = "https://github.com") -> str:
    if not url:
        return fallback
    clean = redact.scrub(str(url).strip())
    if not clean.startswith("https://"):
        return fallback
    if any(ch in clean for ch in (" ", "<", ">", '"', "'", ")", "(")):
        return fallback
    return clean


def _url_host(url: str | None) -> str:
    if not url:
        return "unknown"
    try:
        parsed = urllib.parse.urlsplit(str(url).strip())
        host = (parsed.hostname or "").strip().lower()
        return host if host and procs.is_safe_arg(host) else "unknown"
    except ValueError:
        return "unknown"


def _safe_token(val: Any, fallback: str = "unknown") -> str:
    s = str(val or "").strip()
    if not s:
        return fallback
    return s if procs.is_safe_arg(s) else fallback


def _previous_digest_info(ctx: Ctx, current_run_id: str) -> tuple[str | None, str | None]:
    """Return `(prev_run_id, prev_started_at)` for the most recent prior digest."""
    row = ctx.db.execute(
        "SELECT d.run_id, r.started_at "
        "FROM digests d JOIN runs r ON r.run_id = d.run_id "
        "WHERE d.run_id != ? "
        "ORDER BY r.started_at DESC, d.run_id DESC LIMIT 1",
        (current_run_id,),
    ).fetchone()
    if row is None:
        return None, None
    return str(row["run_id"]), str(row["started_at"])


def _affected_project_aliases_for_alert(
    ctx: Ctx,
    alert_row: Mapping[str, Any],
    proj_lookup: Mapping[str, tuple[str, str]],
) -> list[str]:
    aliases: list[str] = []
    pkey = str(alert_row.get("project_key") or "").strip()
    if pkey and pkey in proj_lookup:
        aliases.append(proj_lookup[pkey][0])
    eid = str(alert_row.get("entity_id") or "").strip()
    if eid:
        u_rows = ctx.db.execute(
            "SELECT context FROM usages WHERE entity_id = ? AND context LIKE 'project:%'",
            (eid,),
        ).fetchall()
        for ur in u_rows:
            ukey = str(ur["context"])[len("project:") :]
            alias = proj_lookup.get(ukey, ("private-project-1", "private"))[0]
            if alias not in aliases:
                aliases.append(alias)
    return sorted(aliases)


def _entity_display_for_agent(
    ctx: Ctx,
    entity_id: str | None,
    priv_map: Mapping[str, str],
    proj_lookup: Mapping[str, tuple[str, str]],
) -> str:
    if not entity_id:
        return "app"
    row = ctx.db.execute(
        "SELECT name, visibility FROM entities WHERE entity_id = ?",
        (entity_id,),
    ).fetchone()
    if row is None:
        return _safe_agent_str(entity_id, priv_map, limit=120)
    if str(row["visibility"] or "public") == "public":
        return _safe_agent_str(str(row["name"] or entity_id), priv_map, limit=120)
    u_rows = ctx.db.execute(
        "SELECT context FROM usages WHERE entity_id = ? AND context LIKE 'project:%'",
        (entity_id,),
    ).fetchall()
    for ur in u_rows:
        pkey = str(ur["context"])[len("project:") :]
        if pkey in proj_lookup:
            return proj_lookup[pkey][0]
    return "private-project-1"


def _check_settings_prefix_mismatch(ctx: Ctx) -> bool:
    """Return True if `{run_root}/.claude/settings.json` prefix disagrees with `RADAR_CMD` (A25)."""
    _settings_present, prefix_matches, _reason = doctor.check_settings_prefix(ctx)
    return not prefix_matches


def _select_changed_alerts(
    ctx: Ctx,
    run_id: str,
    prev_started_at: str | None,
    kinds: Sequence[str],
) -> list[dict[str, Any]]:
    placeholders = ", ".join("?" for _ in kinds)
    rows = ctx.db.execute(
        f"SELECT a.*, s.url AS signal_url, s.source_id AS signal_source, "
        f"s.untrusted_excerpt AS signal_excerpt "
        f"FROM alerts a LEFT JOIN signals s ON s.signal_id = a.signal_id "
        f"WHERE a.state = 'open' AND a.kind IN ({placeholders}) "
        f"ORDER BY a.alert_id ASC",
        tuple(kinds),
    ).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        aid = int(r["alert_id"])
        ref = f"A-{aid}"
        raised = str(r["raised_at"] or "")
        if prev_started_at is not None:
            prior_surfaced = ctx.db.execute(
                "SELECT 1 FROM digest_items WHERE ref = ? AND run_id != ? LIMIT 1",
                (ref, run_id),
            ).fetchone()
            if prior_surfaced is not None and raised <= prev_started_at:
                continue
        out.append(dict(r))

    out.sort(key=lambda x: (TIER_SORT_KEY.get(str(x["tier"]), 9), int(x["alert_id"])))
    return out


def _item_changed_since_prior_digest(
    ctx: Ctx,
    item_row: Mapping[str, Any],
    run_id: str,
    prev_started_at: str | None,
) -> bool:
    """Evaluate Spec 09 § Changed-since rule for an item."""
    iid = str(item_row["item_id"])
    if prev_started_at is None:
        return True

    # 1. Has a ring_history row with `at` after the previous digest's run start (or this run)
    rh_row = ctx.db.execute(
        "SELECT 1 FROM ring_history WHERE item_id = ? AND (at > ? OR run_id = ?) LIMIT 1",
        (iid, prev_started_at, run_id),
    ).fetchone()
    if rh_row is not None:
        return True

    # 2. Check if it already has a digest_items row from an earlier run
    prior_di = ctx.db.execute(
        "SELECT 1 FROM digest_items WHERE ref = ? AND run_id != ? LIMIT 1",
        (iid, run_id),
    ).fetchone()
    if prior_di is not None:
        return False

    # 3. Newly promoted or verdict applied this run with no earlier digest_items row
    created_at = str(item_row.get("created_at") or "")
    if created_at > prev_started_at:
        return True

    cur_di = ctx.db.execute(
        "SELECT 1 FROM digest_items WHERE ref = ? AND run_id = ? LIMIT 1",
        (iid, run_id),
    ).fetchone()
    return cur_di is not None


def _load_surfaced_section_entries(
    ctx: Ctx,
    run_id: str,
    section: str,
    prev_started_at: str | None,
) -> list[dict[str, Any]]:
    """Load items/signals surfaced for `run_id` in `section` that satisfy the changed-since rule."""
    di_rows = ctx.db.execute(
        "SELECT ref, rank FROM digest_items WHERE run_id = ? AND section = ? ORDER BY rank ASC",
        (run_id, section),
    ).fetchall()

    entries: list[dict[str, Any]] = []
    for dr in di_rows:
        ref = str(dr["ref"])
        if ref.startswith("R-"):
            i_row = ctx.db.execute(
                "SELECT i.*, e.kind, e.ecosystem, e.name, e.repo, e.homepage, e.visibility "
                "FROM items i JOIN entities e ON e.entity_id = i.entity_id "
                "WHERE i.item_id = ?",
                (ref,),
            ).fetchone()
            if i_row is None or str(i_row["visibility"] or "public") != "public":
                ctx.db.execute(
                    "DELETE FROM digest_items WHERE run_id = ? AND ref = ?",
                    (run_id, ref),
                )
                continue
            if not _item_changed_since_prior_digest(ctx, dict(i_row), run_id, prev_started_at):
                ctx.db.execute(
                    "DELETE FROM digest_items WHERE run_id = ? AND ref = ?",
                    (run_id, ref),
                )
                continue
            entries.append({"kind": "item", "ref": ref, "row": dict(i_row)})
        elif ref.startswith("S-"):
            try:
                sig_id = int(ref[2:])
            except ValueError:
                continue
            s_row = ctx.db.execute(
                "SELECT s.*, e.name AS entity_name, e.visibility AS entity_vis "
                "FROM signals s LEFT JOIN entities e ON e.entity_id = s.entity_id "
                "WHERE s.signal_id = ? AND s.withdrawn = 0",
                (sig_id,),
            ).fetchone()
            if s_row is None:
                continue
            if s_row["entity_vis"] and str(s_row["entity_vis"]) != "public":
                continue
            prior_s = ctx.db.execute(
                "SELECT 1 FROM digest_items WHERE ref = ? AND run_id != ? LIMIT 1",
                (ref, run_id),
            ).fetchone()
            if (
                prev_started_at is not None
                and prior_s is not None
                and str(s_row["fetched_at"] or "") <= prev_started_at
            ):
                ctx.db.execute(
                    "DELETE FROM digest_items WHERE run_id = ? AND ref = ?",
                    (run_id, ref),
                )
                continue
            entries.append({"kind": "signal", "ref": ref, "row": dict(s_row)})
    ctx.db.commit()
    return entries


def _primary_evidence_for_entity(
    ctx: Ctx,
    entity_id: str,
    repo: str | None,
) -> tuple[str, str | None]:
    """Return `(evidence_url, untrusted_excerpt)` for `entity_id`."""
    s_row = ctx.db.execute(
        "SELECT url, untrusted_excerpt FROM signals "
        "WHERE entity_id = ? AND withdrawn = 0 ORDER BY signal_id DESC LIMIT 1",
        (entity_id,),
    ).fetchone()
    fallback_url = f"https://github.com/{repo}" if repo else "https://github.com"
    if s_row is None:
        it_row = ctx.db.execute(
            "SELECT reported_json FROM items WHERE entity_id = ?",
            (entity_id,),
        ).fetchone()
        if it_row is not None and it_row["reported_json"]:
            try:
                rep = json.loads(str(it_row["reported_json"]))
                cits = rep.get("citations") if isinstance(rep, dict) else None
                if isinstance(cits, list) and cits:
                    return _safe_https_url(str(cits[0]), fallback=fallback_url), None
            except json.JSONDecodeError:
                pass
        return _safe_https_url(fallback_url), None
    url = _safe_https_url(s_row["url"] or fallback_url, fallback=fallback_url)
    exc = str(s_row["untrusted_excerpt"]) if s_row["untrusted_excerpt"] else None
    return url, exc


def _record_surfaced(
    ctx: Ctx,
    run_id: str,
    ref: str,
    section: str,
    rank: int,
    *,
    signal_id: int | None = None,
    entity_id: str | None = None,
) -> None:
    ctx.db.execute(
        "INSERT INTO digest_items(run_id, ref, section, rank, feedback) "
        "VALUES (?, ?, ?, ?, NULL) "
        "ON CONFLICT(run_id, ref) DO UPDATE SET section = excluded.section, rank = excluded.rank",
        (run_id, ref, section, rank),
    )
    if signal_id is not None:
        ctx.db.execute(
            "UPDATE signals SET last_surfaced_at = ? WHERE signal_id = ?",
            (ctx.now_iso, signal_id),
        )
    if entity_id:
        ctx.db.execute(
            "UPDATE signals SET last_surfaced_at = ? WHERE entity_id = ?",
            (ctx.now_iso, entity_id),
        )


def _render_monthly_precision_and_usage(ctx: Ctx) -> list[str]:
    """Compute monthly `## Precision` and `## Usage` sections from SQLite (Spec 09 §9)."""
    lines: list[str] = ["## Precision", ""]
    cutoff_30 = (ctx.now - timedelta(days=35)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    sec_rows = ctx.db.execute(
        "SELECT di.section, COUNT(*) AS surfaced, "
        "SUM(CASE WHEN di.feedback = 'acted' THEN 1 ELSE 0 END) AS acted, "
        "SUM(CASE WHEN di.feedback = 'useful' THEN 1 ELSE 0 END) AS useful, "
        "SUM(CASE WHEN di.feedback = 'not_useful' THEN 1 ELSE 0 END) AS not_useful "
        "FROM digest_items di JOIN runs r ON r.run_id = di.run_id "
        "WHERE r.started_at >= ? "
        "GROUP BY di.section ORDER BY di.section ASC",
        (cutoff_30,),
    ).fetchall()

    lines.append("### By section")
    lines.append(
        "| Section | Surfaced | Acted | Useful | Not useful | Precision (acted/surfaced) |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for sr in sec_rows:
        surf = int(sr["surfaced"] or 0)
        act = int(sr["acted"] or 0)
        use = int(sr["useful"] or 0)
        not_use = int(sr["not_useful"] or 0)
        pct = f"{(100.0 * act / surf):.0f}%" if surf > 0 else "0%"
        lines.append(f"| {sr['section']} | {surf} | {act} | {use} | {not_use} | {pct} |")
    lines.append("")

    # By source
    di_all = ctx.db.execute(
        "SELECT di.ref, di.feedback FROM digest_items di "
        "JOIN runs r ON r.run_id = di.run_id WHERE r.started_at >= ?",
        (cutoff_30,),
    ).fetchall()
    source_stats: dict[str, list[int]] = {}  # source_id -> [surfaced, acted, useful]
    for dr in di_all:
        ref = str(dr["ref"])
        fb = str(dr["feedback"] or "")
        src_id = "inventory"
        if ref.startswith("S-"):
            with_s = ctx.db.execute(
                "SELECT source_id FROM signals WHERE signal_id = ?",
                (int(ref[2:]),),
            ).fetchone()
            if with_s is not None and with_s["source_id"]:
                src_id = str(with_s["source_id"])
        elif ref.startswith("R-"):
            with_i = ctx.db.execute(
                "SELECT s.source_id FROM items i "
                "JOIN signals s ON s.entity_id = i.entity_id "
                "WHERE i.item_id = ? ORDER BY s.signal_id ASC LIMIT 1",
                (ref,),
            ).fetchone()
            if with_i is not None and with_i["source_id"]:
                src_id = str(with_i["source_id"])
        elif ref.startswith("A-"):
            with_a = ctx.db.execute(
                "SELECT s.source_id FROM alerts a "
                "LEFT JOIN signals s ON s.signal_id = a.signal_id "
                "WHERE a.alert_id = ?",
                (int(ref[2:]),),
            ).fetchone()
            if with_a is not None and with_a["source_id"]:
                src_id = str(with_a["source_id"])

        counts = source_stats.setdefault(src_id, [0, 0, 0])
        counts[0] += 1
        if fb == "acted":
            counts[1] += 1
        elif fb == "useful":
            counts[2] += 1

    lines.append("### By source")
    lines.append("| Source | Surfaced | Acted | Useful | Precision (acted/surfaced) |")
    lines.append("| --- | --- | --- | --- | --- |")
    for src_id, (surf, act, use) in sorted(source_stats.items()):
        pct = f"{(100.0 * act / surf):.0f}%" if surf > 0 else "0%"
        lines.append(f"| {src_id} | {surf} | {act} | {use} | {pct} |")
    lines.append("")

    # Monthly Usage
    lines.append("## Usage")
    lines.append("")
    run_rows = ctx.db.execute(
        "SELECT run_id, mode, started_at, usage_json FROM runs "
        "WHERE started_at >= ? AND usage_json IS NOT NULL "
        "ORDER BY started_at ASC",
        (cutoff_30,),
    ).fetchall()
    lines.append("| Run ID | Mode | Input tokens | Output tokens | Turns |")
    lines.append("| --- | --- | --- | --- | --- |")
    total_in = 0
    total_out = 0
    total_turns = 0
    for rr in run_rows:
        try:
            u = json.loads(str(rr["usage_json"]))
        except json.JSONDecodeError:
            u = {}
        inp = int(u.get("input_tokens") or 0)
        outp = int(u.get("output_tokens") or 0)
        turns = int(u.get("turns") or 0)
        total_in += inp
        total_out += outp
        total_turns += turns
        lines.append(f"| {rr['run_id']} | {rr['mode']} | {inp} | {outp} | {turns} |")
    lines.append(f"| **Total** | — | **{total_in}** | **{total_out}** | **{total_turns}** |")
    lines.append("")
    return lines


def _render_coverage_section(
    ctx: Ctx,
    run_id: str,
    priv_map: Mapping[str, str],
    *,
    sync_available: bool = True,
) -> list[str]:
    """Render `## Coverage` per Spec 09 §10 (FMT-08, A5, A8, A18, A25, A26)."""
    sh_rows = ctx.db.execute(
        "SELECT source_id, last_ok_at, last_error, consecutive_failures, skipped_reason "
        "FROM source_health ORDER BY source_id ASC"
    ).fetchall()
    ok_ids: list[str] = []
    degraded_strs: list[str] = []
    skipped_strs: list[str] = []
    for r in sh_rows:
        sid = _safe_agent_str(str(r["source_id"]), priv_map, limit=80)
        skip_r = str(r["skipped_reason"] or "").strip()
        consec = int(r["consecutive_failures"] or 0)
        last_err = str(r["last_error"] or "").strip()
        if skip_r:
            skipped_strs.append(f"{sid} ({_safe_agent_str(skip_r, priv_map, limit=120)})")
        elif consec > 0 or last_err:
            err_label = _safe_agent_str(last_err or f"failures={consec}", priv_map, limit=120)
            degraded_strs.append(f"{sid} ({err_label})")
        else:
            ok_ids.append(sid)

    promote_raw = db.kv_get(ctx.db, "last_promote_stats_json")
    suppressed = {
        "renovate": 0,
        "seen": 0,
        "anti_interest": 0,
        "gate_failed": 0,
        "private": 0,
        "deferred": 0,
    }
    if promote_raw:
        try:
            p_obj = json.loads(promote_raw)
            if isinstance(p_obj, Mapping):
                sup_map = p_obj.get("suppressed")
                if isinstance(sup_map, Mapping):
                    for k in suppressed:
                        suppressed[k] = int(sup_map.get(k) or 0)
                if "deferred" in p_obj:
                    suppressed["deferred"] = int(p_obj.get("deferred") or suppressed["deferred"])
        except json.JSONDecodeError:
            pass

    pub_ent_cnt = int(
        ctx.db.execute("SELECT COUNT(*) FROM entities WHERE visibility = 'public'").fetchone()[0]
    )
    priv_pkg_cnt = int(
        ctx.db.execute(
            "SELECT COUNT(*) FROM entities WHERE kind = 'package' AND visibility != 'public'"
        ).fetchone()[0]
    )
    pub_proj_cnt = int(
        ctx.db.execute("SELECT COUNT(*) FROM projects WHERE visibility = 'public'").fetchone()[0]
    )
    priv_proj_cnt = int(
        ctx.db.execute("SELECT COUNT(*) FROM projects WHERE visibility != 'public'").fetchone()[0]
    )

    # Previous run usage
    prev_usage_row = ctx.db.execute(
        "SELECT run_id, usage_json FROM runs "
        "WHERE run_id != ? AND usage_json IS NOT NULL "
        "ORDER BY started_at DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    prev_usage_str = "none"
    if prev_usage_row is not None and prev_usage_row["usage_json"]:
        try:
            u = json.loads(str(prev_usage_row["usage_json"]))
            prev_usage_str = (
                f"{int(u.get('input_tokens') or 0)} in / "
                f"{int(u.get('output_tokens') or 0)} out tokens, "
                f"{int(u.get('turns') or 0)} turns ({prev_usage_row['run_id']})"
            )
        except json.JSONDecodeError:
            prev_usage_str = "unavailable"

    # Run warnings (model, stalled run, settings-prefix mismatch, pending changes, sync dir)
    run_row = ctx.db.execute(
        "SELECT model, counts_json FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    warnings: list[str] = []
    if run_row is not None:
        model_val = str(run_row["model"] or "").strip()
        if model_val and model_val != "gemini-4-argon":
            warnings.append(f"model warning: expected gemini-4-argon, got {model_val}")
        if run_row["counts_json"]:
            try:
                c_obj = json.loads(str(run_row["counts_json"]))
                if isinstance(c_obj, Mapping):
                    for w in c_obj.get("coverage_warnings") or []:
                        w_str = _safe_agent_str(str(w), priv_map, limit=160)
                        if w_str and w_str not in warnings:
                            warnings.append(w_str)
                    stalled_list = c_obj.get("stalled_runs") or []
                    if stalled_list:
                        st_msg = f"stalled run(s): {', '.join(str(x) for x in stalled_list)}"
                        if st_msg not in warnings:
                            warnings.append(st_msg)
            except json.JSONDecodeError:
                pass

    if _check_settings_prefix_mismatch(ctx) and "settings-prefix mismatch" not in warnings:
        warnings.append("settings-prefix mismatch")

    if not sync_available and "sync dir unavailable" not in warnings:
        warnings.append("sync dir unavailable")

    pending_cnt = int(
        ctx.db.execute(
            "SELECT COUNT(*) FROM pending WHERE status IN ('staged', 'batched')"
        ).fetchone()[0]
    )

    lines = [
        "## Coverage",
        "",
        f"- **Sources OK ({len(ok_ids)}):** {', '.join(ok_ids) if ok_ids else 'none'}",
        (
            f"- **Sources degraded ({len(degraded_strs)}):** "
            f"{', '.join(degraded_strs) if degraded_strs else 'none'}"
        ),
        (
            f"- **Sources skipped ({len(skipped_strs)}):** "
            f"{', '.join(skipped_strs) if skipped_strs else 'none'}"
        ),
        (
            f"- **Suppressed:** renovate={suppressed['renovate']}, seen={suppressed['seen']}, "
            f"anti-interest={suppressed['anti_interest']}, "
            f"gate-failed={suppressed['gate_failed']}, "
            f"private={suppressed['private']}"
        ),
        f"- **Candidates deferred:** {suppressed['deferred']}",
        (
            f"- **Inventory:** {pub_ent_cnt} public entities, {pub_proj_cnt} public project(s), "
            f"{priv_proj_cnt} private project(s), {priv_pkg_cnt} private package(s)"
        ),
        f"- **Previous run usage:** {prev_usage_str}",
        f"- **Pending changes awaiting confirmation:** {pending_cnt}",
    ]
    if warnings:
        lines.append(f"- **Flags & warnings:** {'; '.join(warnings)}")
    lines.append("")
    return lines


def render_digest(
    ctx: Ctx,
    run_id: str,
    *,
    sync_available: bool = True,
) -> tuple[str, Path, bool]:
    """Render run digest Markdown, persist in `digests`, and write `{run_root}/runs/{id}/digest.md`.

    Returns `(markdown, digest_path, is_quiet)`.
    """
    run_row = ctx.db.execute(
        "SELECT mode, started_at, status, counts_json FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    mode = (
        str(run_row["mode"])
        if run_row is not None
        else (run_id.rsplit("-", 1)[-1] if "-" in run_id else "daily")
    )
    started_iso = str(run_row["started_at"]) if run_row is not None else ctx.now_iso
    date_str = started_iso[:10] if len(started_iso) >= 10 else ctx.now_iso[:10]

    priv_map = brief._private_scrub_map(ctx)
    proj_lookup = brief._project_alias_lookup(ctx)
    _prev_run_id, prev_started_at = _previous_digest_info(ctx, run_id)

    sec_alerts = _select_changed_alerts(ctx, run_id, prev_started_at, SECURITY_ALERT_KINDS)
    life_alerts = _select_changed_alerts(ctx, run_id, prev_started_at, ("lifecycle",))
    feat_entries = _load_surfaced_section_entries(ctx, run_id, "features", prev_started_at)
    cont_entries = _load_surfaced_section_entries(ctx, run_id, "contenders", prev_started_at)
    horiz_entries = _load_surfaced_section_entries(ctx, run_id, "horizon", prev_started_at)
    tech_entries = _load_surfaced_section_entries(ctx, run_id, "technique", prev_started_at)
    price_entries = _load_surfaced_section_entries(ctx, run_id, "pricing", prev_started_at)
    due_decisions = decisions.evaluate_due(ctx)

    # Filter contenders to total >= 70 (Spec 09 §5)
    filtered_contenders: list[dict[str, Any]] = []
    for entry in cont_entries:
        if entry["kind"] == "item":
            tot = float(entry["row"].get("total") or 0.0)
            if tot >= 70.0:
                filtered_contenders.append(entry)

    # Filter horizon & techniques: daily shows only total >= 85; weekly/monthly shows >= 70
    min_horizon_score = 85.0 if mode == "daily" else 70.0
    filtered_horizon: list[dict[str, Any]] = []
    for entry in [*horiz_entries, *tech_entries]:
        if entry["kind"] == "item":
            tot = float(entry["row"].get("total") or 0.0)
            if tot >= min_horizon_score:
                filtered_horizon.append(entry)

    # Ring movements since prior digest
    if prev_started_at is not None:
        mov_rows = ctx.db.execute(
            "SELECT h.item_id, h.from_ring, h.to_ring, h.at, h.reason, "
            "e.name, e.visibility, e.kind "
            "FROM ring_history h "
            "JOIN items i ON i.item_id = h.item_id "
            "JOIN entities e ON e.entity_id = i.entity_id "
            "WHERE (h.at > ? OR h.run_id = ?) AND e.visibility = 'public' "
            "ORDER BY h.at ASC, h.item_id ASC",
            (prev_started_at, run_id),
        ).fetchall()
    else:
        mov_rows = ctx.db.execute(
            "SELECT h.item_id, h.from_ring, h.to_ring, h.at, h.reason, "
            "e.name, e.visibility, e.kind "
            "FROM ring_history h "
            "JOIN items i ON i.item_id = h.item_id "
            "JOIN entities e ON e.entity_id = i.entity_id "
            "WHERE h.run_id = ? AND e.visibility = 'public' "
            "ORDER BY h.at ASC, h.item_id ASC",
            (run_id,),
        ).fetchall()

    # Watch-ring items for weekly/monthly
    watch_rows: list[Any] = []
    if mode in ("weekly", "monthly"):
        watch_rows = ctx.db.execute(
            "SELECT i.item_id, i.total, i.rationale, e.name, e.visibility "
            "FROM items i JOIN entities e ON e.entity_id = i.entity_id "
            "WHERE i.ring = 'watch' AND e.visibility = 'public' "
            "ORDER BY i.item_id ASC"
        ).fetchall()

    notable_count = (
        len(sec_alerts)
        + len(life_alerts)
        + len(feat_entries)
        + len(filtered_contenders)
        + len(filtered_horizon)
        + len(price_entries)
        + len(due_decisions)
        + len(mov_rows)
    )

    if notable_count == 0 and mode != "monthly" and sync_available:
        sh_rows = ctx.db.execute(
            "SELECT consecutive_failures, last_error, skipped_reason FROM source_health"
        ).fetchall()
        s_ok = sum(
            1
            for r in sh_rows
            if not r["skipped_reason"]
            and int(r["consecutive_failures"] or 0) == 0
            and not r["last_error"]
        )
        s_deg = sum(
            1
            for r in sh_rows
            if not r["skipped_reason"]
            and (int(r["consecutive_failures"] or 0) > 0 or bool(r["last_error"]))
        )
        s_skip = sum(1 for r in sh_rows if bool(r["skipped_reason"]))
        in_use_cnt = int(
            ctx.db.execute(
                "SELECT COUNT(*) FROM items i JOIN entities e ON e.entity_id = i.entity_id "
                "WHERE i.in_use = 1 AND e.visibility = 'public'"
            ).fetchone()[0]
        )
        quiet_md = (
            f"# Tech Radar — {date_str} ({mode})\n"
            "Quiet run: no new security alerts, lifecycle warnings, "
            "or radar movements since the last digest.\n"
            f"Coverage: {s_ok} sources ok, {s_deg} degraded, {s_skip} skipped "
            f"· {in_use_cnt} public tools in use.\n"
        )
        quiet_md = brief.scrub_private_text(redact.scrub(quiet_md), priv_map)
        digest_path = ctx.paths.run_dir(run_id) / "digest.md"
        fsguard.safe_write(digest_path, quiet_md, mode=0o600, root=ctx.paths.runs)
        rel_path = f"runs/{run_id}/digest.md"
        ctx.db.execute(
            "INSERT INTO digests(run_id, path, markdown) VALUES (?, ?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET path = excluded.path, markdown = excluded.markdown",
            (run_id, rel_path, quiet_md),
        )
        ctx.db.commit()
        return quiet_md, digest_path, True

    # Build TL;DR bullets (<= 5, highest tier first)
    tldr: list[str] = []
    for a in sec_alerts:
        if len(tldr) >= 5:
            break
        aid = int(a["alert_id"])
        kind = str(a["kind"])
        tier = str(a["tier"])
        ent_disp = _entity_display_for_agent(ctx, a.get("entity_id"), priv_map, proj_lookup)
        conf_lbl = " (unconfirmed)" if str(a["confidence"]) == "unconfirmed" else ""
        tldr.append(f"- **[{tier}] {kind}{conf_lbl}** on `{ent_disp}` (`A-{aid}`).")
    for a in life_alerts:
        if len(tldr) >= 5:
            break
        aid = int(a["alert_id"])
        tier = str(a["tier"])
        ent_disp = _entity_display_for_agent(ctx, a.get("entity_id"), priv_map, proj_lookup)
        tldr.append(f"- **[{tier}] lifecycle** alert on `{ent_disp}` (`A-{aid}`).")
    for c_ent in filtered_contenders:
        if len(tldr) >= 5:
            break
        row = c_ent["row"]
        name = _safe_agent_str(str(row.get("name") or row["entity_id"]), priv_map, limit=80)
        tldr.append(
            f"- **Contender `{name}`** (`{row['item_id']}`, ring `{row.get('ring')}`, "
            f"score {float(row.get('total') or 0):.1f})."
        )
    for h_ent in filtered_horizon:
        if len(tldr) >= 5:
            break
        row = h_ent["row"]
        name = _safe_agent_str(str(row.get("name") or row["entity_id"]), priv_map, limit=80)
        tldr.append(
            f"- **Horizon `{name}`** (`{row['item_id']}`, ring `{row.get('ring')}`, "
            f"score {float(row.get('total') or 0):.1f})."
        )
    if not tldr:
        tldr.append("- Scheduled radar summary and coverage report.")

    lines: list[str] = [
        f"# Tech Radar — {date_str} ({mode})",
        "",
        "**TL;DR**",
        *tldr[:5],
        "",
    ]

    # 2. Security section
    lines.append("## Security")
    lines.append("")
    if not sec_alerts:
        lines.append("No new security alerts.")
        lines.append("")
    else:
        for idx, a in enumerate(sec_alerts, start=1):
            aid = int(a["alert_id"])
            ref = f"A-{aid}"
            tier = str(a["tier"])
            kind = str(a["kind"])
            conf = str(a["confidence"])
            payload: dict[str, Any] = {}
            if a.get("payload_json"):
                try:
                    p = json.loads(str(a["payload_json"]))
                    if isinstance(p, dict):
                        payload = p
                except json.JSONDecodeError:
                    payload = {}
            adv_id = _safe_agent_str(
                str(
                    payload.get("advisory_id")
                    or payload.get("cve_id")
                    or payload.get("check")
                    or "n/a"
                ),
                priv_map,
                limit=80,
            )
            ent_disp = _entity_display_for_agent(ctx, a.get("entity_id"), priv_map, proj_lookup)
            proj_aliases = _affected_project_aliases_for_alert(ctx, a, proj_lookup)
            proj_str = (
                ", ".join(f"`{p}`" for p in proj_aliases) if proj_aliases else "system / global"
            )
            conf_tag = " (unconfirmed)" if kind == "chatter" or conf == "unconfirmed" else ""
            ev_url = _safe_https_url(
                str(payload.get("url") or a.get("signal_url") or "https://osv.dev")
            )

            lines.append(f"### [{tier}] {ref} — `{kind}`{conf_tag} · `{ent_disp}` ({adv_id})")
            lines.append(f"- **Affected projects:** {proj_str}")
            lines.append(f"- **Evidence:** [advisory]({ev_url})")
            if kind in ("compromise", "incident") or payload.get("exposure_report"):
                lines.append(
                    f"- **Exposure report:** see INVENTORY folder / "
                    f"`radar.py exposure --show {ref}` in your terminal"
                )
            checklist = payload.get("rotation_checklist")
            if isinstance(checklist, list) and checklist:
                lines.append("- **Rotation checklist:**")
                for step in checklist:
                    lines.append(f"  - {_safe_agent_str(str(step), priv_map, limit=200)}")
            if a.get("signal_excerpt"):
                lines.append("- **Excerpt:**")
                lines.append(_safe_fence(str(a["signal_excerpt"]), priv_map))
            lines.append("")
            _record_surfaced(
                ctx,
                run_id,
                ref,
                "security",
                idx,
                signal_id=int(a["signal_id"]) if a.get("signal_id") is not None else None,
                entity_id=str(a["entity_id"]) if a.get("entity_id") else None,
            )

    # 3. Lifecycle section
    lines.append("## Lifecycle")
    lines.append("")
    if not life_alerts:
        lines.append("No new lifecycle alerts.")
        lines.append("")
    else:
        for idx, a in enumerate(life_alerts, start=1):
            aid = int(a["alert_id"])
            ref = f"A-{aid}"
            tier = str(a["tier"])
            payload = {}
            if a.get("payload_json"):
                try:
                    p = json.loads(str(a["payload_json"]))
                    if isinstance(p, dict):
                        payload = p
                except json.JSONDecodeError:
                    payload = {}
            check_id = _safe_agent_str(str(payload.get("check") or "LIFE"), priv_map, limit=40)
            ent_disp = _entity_display_for_agent(ctx, a.get("entity_id"), priv_map, proj_lookup)
            proj_aliases = _affected_project_aliases_for_alert(ctx, a, proj_lookup)
            proj_str = ", ".join(f"`{p}`" for p in proj_aliases) if proj_aliases else "inventory"
            ev_url = _safe_https_url(
                str(payload.get("url") or a.get("signal_url") or "https://github.com")
            )
            lines.append(
                f"- **[{tier}] {ref} ({check_id})** — `{ent_disp}` in {proj_str} · "
                f"[evidence]({ev_url})"
            )
            if a.get("signal_excerpt"):
                lines.append(_safe_fence(str(a["signal_excerpt"]), priv_map))
            _record_surfaced(
                ctx,
                run_id,
                ref,
                "lifecycle",
                idx,
                signal_id=int(a["signal_id"]) if a.get("signal_id") is not None else None,
                entity_id=str(a["entity_id"]) if a.get("entity_id") else None,
            )
        lines.append("")

    # 4. Features you could use
    lines.append("## Features you could use")
    lines.append("")
    if not feat_entries:
        lines.append("No new feature recommendations.")
        lines.append("")
    else:
        for idx, entry in enumerate(feat_entries, start=1):
            ref = entry["ref"]
            row = entry["row"]
            if entry["kind"] == "signal":
                title = _safe_agent_str(str(row.get("title") or ref), priv_map, limit=200)
                ev_url = _safe_https_url(row.get("url"))
                payload = {}
                if row.get("payload_json"):
                    try:
                        p = json.loads(str(row["payload_json"]))
                        if isinstance(p, dict):
                            payload = p
                    except json.JSONDecodeError:
                        payload = {}
                raw_repos = payload.get("repos") or []
                repo_aliases: list[str] = []
                for rp in raw_repos:
                    rp_s = str(rp)
                    if rp_s in proj_lookup:
                        alias = proj_lookup[rp_s][0]
                    else:
                        alias = _safe_agent_str(rp_s, priv_map, limit=80)
                    if alias not in repo_aliases:
                        repo_aliases.append(alias)
                applies_to = (
                    ", ".join(f"`{a}`" for a in repo_aliases) if repo_aliases else "`workflow`"
                )
                snippet = str(payload.get("adoption_snippet") or row.get("untrusted_excerpt") or "")
                lines.append(f"### {ref} — {title}")
                lines.append(f"- **Applies to:** {applies_to}")
                lines.append(f"- **Evidence:** [release notes]({ev_url})")
                if snippet:
                    lines.append("- **Adoption snippet:**")
                    lines.append(_safe_fence(snippet, priv_map))
                lines.append(f"- `reply: useful {ref}`")
                lines.append("")
                _record_surfaced(
                    ctx,
                    run_id,
                    ref,
                    "features",
                    idx,
                    signal_id=int(row["signal_id"]),
                    entity_id=str(row["entity_id"]) if row.get("entity_id") else None,
                )
            else:
                iid = str(row["item_id"])
                eid = str(row["entity_id"])
                name = _safe_agent_str(str(row.get("name") or eid), priv_map, limit=120)
                why = _safe_agent_str(str(row.get("rationale") or ""), priv_map, limit=300)
                ev_url, exc = _primary_evidence_for_entity(ctx, eid, row.get("repo"))
                used_in = brief._used_in_for_entity(ctx, eid, proj_lookup)
                applies_to = ", ".join(f"`{u}`" for u in used_in) if used_in else "`workflow`"
                reported = json.loads(str(row["reported_json"])) if row.get("reported_json") else {}
                effort = str(reported.get("effort") or "S")
                conf = str(row.get("confidence") or "high")
                snippet = str(reported.get("adoption_snippet") or exc or "")
                lines.append(f"### {iid} — `{name}`")
                lines.append(f"- **What & why it matters to you:** {why} (applies to {applies_to})")
                lines.append(
                    f"- **Evidence:** [source]({ev_url}) · **Effort:** {effort} · "
                    f"**Confidence:** {conf}"
                )
                if snippet:
                    lines.append("- **Adoption snippet:**")
                    lines.append(_safe_fence(snippet, priv_map))
                lines.append(f"- `reply: trial {iid} · reject {iid} 90d · useful {iid}`")
                lines.append("")
                _record_surfaced(ctx, run_id, iid, "features", idx, entity_id=eid)

    # 5. Contenders
    lines.append("## Contenders")
    lines.append("")
    if not filtered_contenders:
        lines.append("No contenders met the score threshold this run.")
        lines.append("")
    else:
        if len(filtered_contenders) >= 3:
            lines.append("| Item | Name | Ring | Score | Replaces | Effort | Confidence |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- |")
            for entry in filtered_contenders:
                r = entry["row"]
                rep = json.loads(str(r["reported_json"])) if r.get("reported_json") else {}
                c_name = _safe_agent_str(str(r.get("name") or r["entity_id"]), priv_map, limit=80)
                c_repl = _safe_agent_str(str(rep.get("replaces") or "n/a"), priv_map, limit=80)
                lines.append(
                    f"| {r['item_id']} | {c_name} "
                    f"| {r.get('ring') or 'assess'} | {float(r.get('total') or 0):.1f} "
                    f"| `{c_repl}` "
                    f"| {rep.get('effort') or 'M'} | {r.get('confidence') or 'high'} |"
                )
            lines.append("")

        for idx, entry in enumerate(filtered_contenders, start=1):
            row = entry["row"]
            iid = str(row["item_id"])
            eid = str(row["entity_id"])
            name = _safe_agent_str(str(row.get("name") or eid), priv_map, limit=120)
            ring = str(row.get("ring") or "assess")
            total = float(row.get("total") or 0.0)
            conf = str(row.get("confidence") or "high")
            why = _safe_agent_str(str(row.get("rationale") or ""), priv_map, limit=350)
            gates = json.loads(str(row["gates_json"])) if row.get("gates_json") else {}
            red_flags = json.loads(str(row["red_flags_json"])) if row.get("red_flags_json") else []
            reported = json.loads(str(row["reported_json"])) if row.get("reported_json") else {}

            replaces = _safe_agent_str(str(reported.get("replaces") or "n/a"), priv_map, limit=120)
            superiority = _safe_agent_str(
                str(reported.get("superiority") or why), priv_map, limit=350
            )
            benefit = _safe_agent_str(
                str(reported.get("benefit") or "Improves workflow efficiency"),
                priv_map,
                limit=200,
            )
            effort = str(reported.get("effort") or "M")
            cooldown = reported.get("cooldown_days")
            cooldown_tag = (
                f" `[COOLDOWN: {int(cooldown)} days]`" if isinstance(cooldown, int | float) else ""
            )
            ev_url, exc = _primary_evidence_for_entity(ctx, eid, row.get("repo"))
            install_cmd = reported.get("install_command") or verdict.compose_install_command(
                row, reported
            )
            gates_summary = ", ".join(f"{k}={v}" for k, v in sorted(gates.items())) or "all pass"
            rf_summary = ", ".join(str(x) for x in red_flags) if red_flags else "none"

            lines.append(f"### {iid} — `{name}` ({ring}, score {total:.1f}){cooldown_tag}")
            lines.append(f"- **What & why it matters to you:** {why}")
            lines.append(f"- **Replaces:** `{replaces}`")
            lines.append(f"- **Superiority:** {superiority} ([citation]({ev_url}))")
            lines.append(f"- **Benefit:** {benefit}")
            lines.append(f"- **Gates:** {gates_summary} · **Red flags:** {rf_summary}")
            lines.append(
                f"- **Suggested action:** `{ring}` · **Effort:** {effort} · "
                f"**Confidence:** {conf} · **Evidence:** [source]({ev_url})"
            )
            if install_cmd:
                safe_cmd = _safe_agent_str(str(install_cmd), priv_map, limit=160)
                lines.append(
                    f"- **Install (unverified third-party code — review before installing):** "
                    f"`{safe_cmd}`"
                )
            if exc:
                lines.append(_safe_fence(exc, priv_map))
            lines.append(f"- `reply: trial {iid} · reject {iid} 90d · useful {iid}`")
            lines.append(
                f'- Deep dive: `/code-quality:deep-research "{name} — '
                f'migration & tradeoffs vs {replaces}"`'
            )
            lines.append("")
            _record_surfaced(ctx, run_id, iid, "contenders", idx, entity_id=eid)

    # 6. Horizon & techniques
    lines.append("## Horizon & techniques")
    lines.append("")
    if not filtered_horizon and not mov_rows and not watch_rows:
        lines.append("No horizon or technique updates met the threshold this run.")
        lines.append("")
    else:
        if len(filtered_horizon) >= 3:
            lines.append("| Item | Name | Quadrant | Ring | Score | Effort | Confidence |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- |")
            for entry in filtered_horizon:
                r = entry["row"]
                rep = json.loads(str(r["reported_json"])) if r.get("reported_json") else {}
                h_name = _safe_agent_str(str(r.get("name") or r["entity_id"]), priv_map, limit=80)
                lines.append(
                    f"| {r['item_id']} | {h_name} "
                    f"| {r.get('quadrant') or 'languages-libraries'} | {r.get('ring') or 'assess'} "
                    f"| {float(r.get('total') or 0):.1f} | {rep.get('effort') or 'M'} "
                    f"| {r.get('confidence') or 'high'} |"
                )
            lines.append("")

        for idx, entry in enumerate(filtered_horizon, start=1):
            row = entry["row"]
            iid = str(row["item_id"])
            eid = str(row["entity_id"])
            name = _safe_agent_str(str(row.get("name") or eid), priv_map, limit=120)
            quad = str(row.get("quadrant") or "languages-libraries")
            ring = str(row.get("ring") or "assess")
            total = float(row.get("total") or 0.0)
            conf = str(row.get("confidence") or "high")
            why = _safe_agent_str(str(row.get("rationale") or ""), priv_map, limit=350)
            reported = json.loads(str(row["reported_json"])) if row.get("reported_json") else {}
            rel_to = [
                _safe_agent_str(str(x), priv_map, limit=100)
                for x in (reported.get("relevant_to") or [])
            ]
            rel_str = ", ".join(f"`{x}`" for x in rel_to) if rel_to else "`stack`"
            benefit = _safe_agent_str(
                str(reported.get("benefit") or "Relevant to current workflow"),
                priv_map,
                limit=200,
            )
            effort = str(reported.get("effort") or "M")
            ev_url, exc = _primary_evidence_for_entity(ctx, eid, row.get("repo"))
            install_cmd = reported.get("install_command") or verdict.compose_install_command(
                row, reported
            )

            lines.append(f"### {iid} — `{name}` ({quad}, ring `{ring}`, score {total:.1f})")
            lines.append(f"- **What & why it matters to you:** {why} (relevant to {rel_str})")
            lines.append(f"- **Benefit:** {benefit}")
            lines.append(
                f"- **Suggested action:** `{ring}` · **Effort:** {effort} · "
                f"**Confidence:** {conf} · **Evidence:** [source]({ev_url})"
            )
            if install_cmd and str(row.get("kind")) != "technique":
                safe_cmd = _safe_agent_str(str(install_cmd), priv_map, limit=160)
                lines.append(
                    f"- **Install (unverified third-party code — review before installing):** "
                    f"`{safe_cmd}`"
                )
            if exc:
                lines.append(_safe_fence(exc, priv_map))
            lines.append(f"- `reply: trial {iid} · reject {iid} 90d · useful {iid}`")
            lines.append(
                f'- Deep dive: `/code-quality:deep-research "{name} — architecture & adoption fit"`'
            )
            lines.append("")
            sec_label = "technique" if quad == "techniques" else "horizon"
            _record_surfaced(ctx, run_id, iid, sec_label, idx, entity_id=eid)

        if mov_rows:
            lines.append("### Ring movements")
            for mr in mov_rows:
                m_name = _safe_agent_str(str(mr["name"] or mr["item_id"]), priv_map, limit=100)
                from_r = str(mr["from_ring"] or "new")
                to_r = str(mr["to_ring"] or "assess")
                m_reason = _safe_agent_str(str(mr["reason"] or ""), priv_map, limit=200)
                lines.append(
                    f"- `{mr['item_id']}` (`{m_name}`): `{from_r}` → `{to_r}` — {m_reason}"
                )
            lines.append("")

        # AI ecosystem roundup (Spec 12 A21)
        ai_roundup: list[str] = []
        for entry in [*filtered_contenders, *filtered_horizon]:
            r = entry["row"]
            q = str(r.get("quadrant") or "")
            if q in ("ai-agents-harness", "skills-mcp-plugins"):
                r_name = _safe_agent_str(str(r.get("name") or r["entity_id"]), priv_map, limit=80)
                ai_roundup.append(f"- `{r['item_id']}` `{r_name}` ({q}, ring `{r.get('ring')}`)")
        for p_entry in price_entries:
            pr = p_entry["row"]
            p_title = _safe_agent_str(str(pr.get("title") or p_entry["ref"]), priv_map, limit=120)
            ai_roundup.append(f"- `{p_entry['ref']}` Pricing: {p_title}")
        if ai_roundup:
            lines.append("### AI ecosystem roundup")
            lines.extend(ai_roundup)
            lines.append("")

        if watch_rows:
            lines.append("### Watch ring")
            for wr in watch_rows:
                w_name = _safe_agent_str(str(wr["name"] or wr["item_id"]), priv_map, limit=80)
                w_why = _safe_agent_str(str(wr["rationale"] or ""), priv_map, limit=160)
                lines.append(
                    f"- `{wr['item_id']}` `{w_name}` (score {float(wr['total'] or 0):.1f}): {w_why}"
                )
            lines.append("")

    # 7. Pricing
    lines.append("## Pricing")
    lines.append("")
    if not price_entries:
        lines.append("No notable pricing changes.")
        lines.append("")
    else:
        for idx, entry in enumerate(price_entries, start=1):
            ref = entry["ref"]
            row = entry["row"]
            if entry["kind"] == "signal":
                title = _safe_agent_str(str(row.get("title") or ref), priv_map, limit=200)
                ev_url = _safe_https_url(row.get("url"))
                exc = str(row.get("untrusted_excerpt") or "")
                lines.append(f"- **{ref} — {title}** · [source]({ev_url})")
                if exc:
                    lines.append(_safe_fence(exc, priv_map))
                lines.append(f"  - `reply: useful {ref}`")
                _record_surfaced(
                    ctx,
                    run_id,
                    ref,
                    "pricing",
                    idx,
                    signal_id=int(row["signal_id"]),
                    entity_id=str(row["entity_id"]) if row.get("entity_id") else None,
                )
            else:
                iid = str(row["item_id"])
                eid = str(row["entity_id"])
                name = _safe_agent_str(str(row.get("name") or eid), priv_map, limit=120)
                why = _safe_agent_str(str(row.get("rationale") or ""), priv_map, limit=250)
                ev_url, _ = _primary_evidence_for_entity(ctx, eid, row.get("repo"))
                lines.append(f"- **{iid} — `{name}`**: {why} · [source]({ev_url})")
                lines.append(f"  - `reply: useful {iid}`")
                _record_surfaced(ctx, run_id, iid, "pricing", idx, entity_id=eid)
        lines.append("")

    # 8. Decisions due
    lines.append("## Decisions due")
    lines.append("")
    if not due_decisions:
        lines.append("No decisions currently due.")
        lines.append("")
    else:
        for d in due_decisions:
            iid = str(d["item_id"])
            d_name = _safe_agent_str(str(d["name"]), priv_map, limit=100)
            verb = str(d["verb"])
            due_reason = _safe_agent_str(str(d["due_reason"]), priv_map, limit=100)
            lines.append(
                f"- **{iid} (`{d_name}`)** — prior `{verb}` is due (`{due_reason}`) · "
                f"`reply: trial {iid} · reject {iid} 90d · useful {iid}`"
            )
        lines.append("")

    # 9. Monthly only: Precision & Usage
    if mode == "monthly":
        lines.extend(_render_monthly_precision_and_usage(ctx))

    # 10. Coverage
    lines.extend(_render_coverage_section(ctx, run_id, priv_map, sync_available=sync_available))

    ctx.db.commit()
    markdown = "\n".join(lines).rstrip() + "\n"
    markdown = brief.scrub_private_text(redact.scrub(markdown), priv_map)

    digest_path = ctx.paths.run_dir(run_id) / "digest.md"
    fsguard.safe_write(digest_path, markdown, mode=0o600, root=ctx.paths.runs)
    rel_path = f"runs/{run_id}/digest.md"
    ctx.db.execute(
        "INSERT INTO digests(run_id, path, markdown) VALUES (?, ?, ?) "
        "ON CONFLICT(run_id) DO UPDATE SET path = excluded.path, markdown = excluded.markdown",
        (run_id, rel_path, markdown),
    )
    ctx.db.commit()
    return markdown, digest_path, False


def render_tech_radar_md(ctx: Ctx) -> Path:
    """Render `{sync}/TECH_RADAR.md` with Spec 12 A8 privacy aliasing."""
    priv_map = brief._private_scrub_map(ctx)
    proj_lookup = brief._project_alias_lookup(ctx)

    lines: list[str] = [
        "# Personal Tech Radar",
        "",
        f"Generated at {ctx.now_iso}. Private entities are shown by alias or count only.",
        "",
    ]

    for quad in items.ALL_QUADRANTS:
        q_label = QUADRANT_LABELS.get(quad, quad)
        lines.append(f"## {q_label} (`{quad}`)")
        lines.append("")

        q_rows = ctx.db.execute(
            "SELECT i.item_id, i.entity_id, i.ring, i.total, i.in_use, i.created_at, "
            "e.name, e.kind, e.visibility, "
            "(SELECT MAX(h.at) FROM ring_history h WHERE h.item_id = i.item_id) AS last_moved "
            "FROM items i JOIN entities e ON e.entity_id = i.entity_id "
            "WHERE i.quadrant = ? AND i.ring IS NOT NULL "
            "ORDER BY CASE i.ring "
            "  WHEN 'adopt' THEN 0 WHEN 'trial' THEN 1 WHEN 'assess' THEN 2 "
            "  WHEN 'watch' THEN 3 WHEN 'hold' THEN 4 ELSE 5 END ASC, "
            "i.item_id ASC",
            (quad,),
        ).fetchall()

        pub_rows: list[Any] = []
        priv_pkg_count = 0
        for r in q_rows:
            vis = str(r["visibility"] or "public")
            if vis != "public" and str(r["kind"]) == "package":
                priv_pkg_count += 1
                continue
            pub_rows.append(r)

        lines.append("| Item | Name | Ring | Score | In Use | Last Movement |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for r in pub_rows:
            iid = str(r["item_id"])
            eid = str(r["entity_id"])
            vis = str(r["visibility"] or "public")
            if vis == "public":
                disp_name = _safe_agent_str(str(r["name"] or eid), priv_map, limit=100)
            else:
                disp_name = _entity_display_for_agent(ctx, eid, priv_map, proj_lookup)
            ring = str(r["ring"] or "assess")
            score = f"{float(r['total'] or 0.0):.1f}"
            in_use = "yes" if int(r["in_use"] or 0) == 1 else "no"
            moved = str(r["last_moved"] or r["created_at"] or "")[:10]
            lines.append(f"| {iid} | `{disp_name}` | {ring} | {score} | {in_use} | {moved} |")
        if priv_pkg_count > 0:
            lines.append("")
            lines.append(f"_Private packages in this quadrant: {priv_pkg_count}_")
        lines.append("")

    # Recent movements (last 30 days)
    cutoff_30 = (ctx.now - timedelta(days=30)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    mov_rows = ctx.db.execute(
        "SELECT h.item_id, h.from_ring, h.to_ring, h.at, h.reason, "
        "e.name, e.kind, e.visibility "
        "FROM ring_history h "
        "JOIN items i ON i.item_id = h.item_id "
        "JOIN entities e ON e.entity_id = i.entity_id "
        "WHERE h.at >= ? "
        "ORDER BY h.at DESC, h.item_id ASC",
        (cutoff_30,),
    ).fetchall()

    lines.append("## Recent movements")
    lines.append("")
    if not mov_rows:
        lines.append("No ring movements in the last 30 days.")
    else:
        for mr in mov_rows:
            if str(mr["visibility"] or "public") != "public" and str(mr["kind"]) == "package":
                continue
            m_name = _safe_agent_str(str(mr["name"] or mr["item_id"]), priv_map, limit=100)
            from_r = str(mr["from_ring"] or "new")
            to_r = str(mr["to_ring"] or "assess")
            at_date = str(mr["at"] or "")[:10]
            reason = _safe_agent_str(str(mr["reason"] or ""), priv_map, limit=200)
            lines.append(
                f"- **{at_date}** · `{mr['item_id']}` (`{m_name}`): "
                f"`{from_r}` → `{to_r}` — {reason}"
            )
    lines.append("")

    content = brief.scrub_private_text(redact.scrub("\n".join(lines).rstrip() + "\n"), priv_map)
    target = ctx.paths.sync / "TECH_RADAR.md"
    return fsguard.safe_write(target, content, mode=0o600, root=ctx.paths.sync)


def render_decisions_md(ctx: Ctx) -> Path:
    """Render `{sync}/DECISIONS.md` and update `kv['decisions_md_hash']` (Spec 09, A41)."""
    target = ctx.paths.sync / "DECISIONS.md"
    if target.is_file():
        with contextlib.suppress(OSError, errors.RadarError):
            decisions.stage_decisions_md(ctx)

    lines: list[str] = [
        "# Tech Radar Decisions",
        "",
        "Edit the `your edit` column using `^(adopt|trial|assess|hold|reject|snooze)( \\d+d)?$` "
        "(for example `snooze 30d` or `hold`).",
        "Edits are staged on the next `plan-run` and applied only after you confirm with "
        "`confirm <CODE>` from the macOS notification "
        "(or run `radar.py decisions apply --channel cli` "
        "in an interactive terminal).",
        "",
        "| item | name | ring | decision | expires | your edit |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    item_rows = ctx.db.execute(
        "SELECT i.item_id, i.ring, i.state, e.name, e.entity_id "
        "FROM items i JOIN entities e ON e.entity_id = i.entity_id "
        "ORDER BY i.item_id ASC"
    ).fetchall()

    active_decisions: dict[str, tuple[str, str]] = {}
    for dr in ctx.db.execute(
        "SELECT item_id, verb, expires_at FROM decisions "
        "WHERE superseded_by IS NULL ORDER BY decision_id ASC"
    ).fetchall():
        active_decisions[str(dr["item_id"])] = (
            str(dr["verb"]),
            str(dr["expires_at"])[:10] if dr["expires_at"] else "—",
        )

    for ir in item_rows:
        iid = str(ir["item_id"])
        name = _safe_sync_str(str(ir["name"] or ir["entity_id"]), limit=100)
        ring = str(ir["ring"] or "assess")
        dec_verb, exp_str = active_decisions.get(iid, ("—", "—"))
        lines.append(f"| {iid} | {name} | {ring} | {dec_verb} | {exp_str} |  |")

    lines.append("")
    content = redact.scrub("\n".join(lines))
    written = fsguard.safe_write(target, content, mode=0o600, root=ctx.paths.sync)
    file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    db.kv_set(ctx.db, "decisions_md_hash", file_hash)
    return written


def render_inventory_md(ctx: Ctx) -> Path:
    """Render `{sync}/INVENTORY.md` with context sections and project alias mapping (Spec 09)."""
    u_rows = ctx.db.execute(
        "SELECT u.entity_id, u.context, u.version, u.direct, e.name, e.kind, e.visibility "
        "FROM usages u JOIN entities e ON e.entity_id = u.entity_id "
        "ORDER BY u.context ASC, u.entity_id ASC"
    ).fetchall()

    system_rows: list[Any] = []
    project_rows: list[Any] = []
    harness_rows: list[Any] = []
    interest_rows: list[Any] = []

    for r in u_rows:
        uctx = str(r["context"])
        if uctx.startswith(("system:", "launchd:", "bin:")):
            system_rows.append(r)
        elif uctx.startswith("project:"):
            project_rows.append(r)
        elif uctx.startswith("harness:"):
            harness_rows.append(r)
        elif uctx.startswith("interest:"):
            interest_rows.append(r)
        else:
            system_rows.append(r)

    proj_rows = ctx.db.execute(
        "SELECT project_key, display_path, visibility, alias, active "
        "FROM projects ORDER BY COALESCE(alias, project_key) ASC"
    ).fetchall()

    lines: list[str] = [
        "# Tech Radar Inventory",
        "",
        f"Generated at {ctx.now_iso}. (Sync-only file; not readable by the agent.)",
        "",
        "## Project Alias Map",
        "",
        "| Alias | Visibility | Display Path | Project Key |",
        "| --- | --- | --- | --- |",
    ]
    for pr in proj_rows:
        alias = _safe_sync_str(str(pr["alias"] or pr["project_key"]), limit=100)
        vis = _safe_sync_str(str(pr["visibility"] or "private"), limit=20)
        dpath = _safe_sync_str(str(pr["display_path"]), limit=200)
        pkey = _safe_sync_str(str(pr["project_key"]), limit=200)
        lines.append(f"| `{alias}` | {vis} | `{dpath}` | `{pkey}` |")
    lines.append("")

    def _append_group(heading: str, group_rows: Sequence[Any]) -> None:
        lines.append(f"## {heading} ({len(group_rows)})")
        lines.append("")
        if not group_rows:
            lines.append("None.")
            lines.append("")
            return
        lines.append("| Entity | Name | Version | Context | Visibility |")
        lines.append("| --- | --- | --- | --- | --- |")
        for gr in group_rows:
            eid = _safe_sync_str(str(gr["entity_id"]), limit=120)
            name = _safe_sync_str(str(gr["name"] or gr["entity_id"]), limit=100)
            ver = _safe_sync_str(str(gr["version"] or "—"), limit=60)
            uctx = _safe_sync_str(str(gr["context"]), limit=160)
            vis = _safe_sync_str(str(gr["visibility"] or "public"), limit=20)
            lines.append(f"| `{eid}` | {name} | `{ver}` | `{uctx}` | {vis} |")
        lines.append("")

    _append_group("System", system_rows)
    _append_group("Projects", project_rows)
    _append_group("Harness", harness_rows)
    _append_group("Interests", interest_rows)

    content = redact.scrub("\n".join(lines).rstrip() + "\n")
    target = ctx.paths.sync / "INVENTORY.md"
    return fsguard.safe_write(target, content, mode=0o600, root=ctx.paths.sync)


def compose_project_alert_title(
    *,
    tier: str,
    kind: str,
    confidence: str,
    advisory_id: str | None,
    package_name: str | None,
    version: str | None,
    source_id: str | None,
    url: str | None,
    check: str | None = None,
) -> str:
    """Compose a session-start alert title strictly from validated structured fields (Spec 12 A24).

    Never reads or includes fetched signal titles.
    """
    s_tier = _safe_token(tier, "P2")
    s_kind = _safe_token(kind, "alert")
    s_pkg = _safe_token(package_name, "package")
    s_ver = _safe_token(version, "")
    s_adv = _safe_token(advisory_id, "")
    s_src = _safe_token(source_id, "radar")
    s_host = _url_host(url)
    s_check = _safe_token(check, "")

    ver_part = f" {s_ver}" if s_ver and s_ver != "unknown" else ""
    adv_part = f" ({s_adv})" if s_adv and s_adv != "unknown" else ""
    check_part = f" [{s_check}]" if s_check and s_check != "unknown" else ""
    unconf_part = (
        " — unconfirmed — see radar"
        if s_kind == "chatter" or str(confidence).lower() == "unconfirmed"
        else ""
    )

    if s_kind == "incident":
        return (
            f"{s_tier} {s_kind}: {s_pkg} (installed version not affected){adv_part} "
            f"via {s_src} ({s_host}){unconf_part}"
        )
    return (
        f"{s_tier} {s_kind}{check_part}: {s_pkg}{ver_part}{adv_part} "
        f"via {s_src} ({s_host}){unconf_part}"
    )


def render_project_alerts_json(
    ctx: Ctx,
    *,
    sync_available: bool = True,
) -> tuple[Path, Path | None]:
    """Render `project-alerts.json` to `{state}` and `{sync}` (Spec 09, Spec 12 A24)."""
    proj_rows = ctx.db.execute(
        "SELECT project_key FROM projects ORDER BY project_key ASC"
    ).fetchall()
    remote_keys = [
        str(r["project_key"]) for r in proj_rows if str(r["project_key"]).startswith("https://")
    ]

    alert_rows = ctx.db.execute(
        "SELECT a.alert_id, a.tier, a.kind, a.entity_id, a.project_key, a.confidence, "
        "a.payload_json, e.name AS pkg_name, s.source_id AS sig_source, s.url AS sig_url "
        "FROM alerts a "
        "LEFT JOIN entities e ON e.entity_id = a.entity_id "
        "LEFT JOIN signals s ON s.signal_id = a.signal_id "
        "WHERE a.state = 'open' AND a.tier IN ('P0', 'P1', 'P2') "
        "ORDER BY a.alert_id ASC"
    ).fetchall()

    projects_map: dict[str, list[dict[str, str]]] = {}
    for pkey in remote_keys:
        projects_map[pkey] = []

    for ar in alert_rows:
        aid = int(ar["alert_id"])
        tier = str(ar["tier"])
        kind = str(ar["kind"])
        conf = str(ar["confidence"])
        eid = str(ar["entity_id"] or "")
        pkg_name = str(ar["pkg_name"] or eid or "app")
        payload: dict[str, Any] = {}
        if ar["payload_json"]:
            try:
                p = json.loads(str(ar["payload_json"]))
                if isinstance(p, dict):
                    payload = p
            except json.JSONDecodeError:
                payload = {}

        adv_id = str(payload.get("advisory_id") or payload.get("cve_id") or "")
        ver = str(payload.get("version") or "")
        src_id = str(payload.get("source_id") or ar["sig_source"] or "radar")
        url = _safe_https_url(str(payload.get("url") or ar["sig_url"] or "https://github.com"))
        check = str(payload.get("check") or "")

        affected_pkeys: list[str] = []
        if ar["project_key"] and str(ar["project_key"]).startswith("https://"):
            affected_pkeys.append(str(ar["project_key"]))
        if eid:
            u_rows = ctx.db.execute(
                "SELECT context, version FROM usages "
                "WHERE entity_id = ? AND context LIKE 'project:https://%'",
                (eid,),
            ).fetchall()
            for ur in u_rows:
                ukey = str(ur["context"])[len("project:") :]
                if ukey not in affected_pkeys:
                    affected_pkeys.append(ukey)
                if not ver and ur["version"]:
                    ver = str(ur["version"])

        title = compose_project_alert_title(
            tier=tier,
            kind=kind,
            confidence=conf,
            advisory_id=adv_id,
            package_name=pkg_name,
            version=ver,
            source_id=src_id,
            url=url,
            check=check,
        )

        entry = {
            "tier": tier,
            "kind": kind,
            "title": redact.scrub(title),
            "item_or_alert_id": f"A-{aid}",
            "url": url,
        }
        for pkey in affected_pkeys:
            projects_map.setdefault(pkey, []).append(entry)

    # Sort each project's alerts by tier then alert_id
    for pkey in list(projects_map.keys()):
        projects_map[pkey].sort(
            key=lambda x: (TIER_SORT_KEY.get(x["tier"], 9), x["item_or_alert_id"])
        )

    doc = {
        "generated_at": ctx.now_iso,
        "projects": projects_map,
    }
    raw_json = redact.scrub(json.dumps(doc, indent=2, sort_keys=True) + "\n")

    state_target = fsguard.safe_write(
        ctx.paths.state / "project-alerts.json",
        raw_json,
        mode=0o600,
        root=ctx.paths.state,
    )
    sync_target: Path | None = None
    if sync_available:
        sync_target = fsguard.safe_write(
            ctx.paths.sync / "project-alerts.json",
            raw_json,
            mode=0o600,
            root=ctx.paths.sync,
        )
    return state_target, sync_target


def render_byor_json(ctx: Ctx) -> Path:
    """Render `{sync}/radar.byor.json` in Thoughtworks BYOR schema (Spec 09 [RADAR-05])."""
    cutoff_30 = (ctx.now - timedelta(days=30)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = ctx.db.execute(
        "SELECT i.item_id, i.entity_id, i.ring, i.quadrant, i.rationale, i.created_at, "
        "e.name, e.kind, e.visibility "
        "FROM items i JOIN entities e ON e.entity_id = i.entity_id "
        "WHERE i.ring IS NOT NULL "
        "ORDER BY i.item_id ASC"
    ).fetchall()

    byor_entries: list[dict[str, Any]] = []
    for r in rows:
        raw_ring = str(r["ring"])
        quad = str(r["quadrant"] or items.default_quadrant(str(r["kind"])))
        name = _safe_sync_str(str(r["name"] or r["entity_id"]), limit=120)
        desc = _safe_sync_str(str(r["rationale"] or name), limit=300)
        if raw_ring == "watch":
            export_ring = "assess"
            export_desc = f"[watch] {desc}" if not desc.startswith("[watch]") else desc
        else:
            export_ring = raw_ring
            export_desc = desc
        created_at = str(r["created_at"] or "")
        is_new = bool(created_at >= cutoff_30)
        byor_entries.append(
            {
                "name": name,
                "ring": export_ring,
                "quadrant": quad,
                "isNew": is_new,
                "description": export_desc,
            }
        )

    raw_json = redact.scrub(json.dumps(byor_entries, indent=2) + "\n")
    target = ctx.paths.sync / "radar.byor.json"
    return fsguard.safe_write(target, raw_json, mode=0o600, root=ctx.paths.sync)


def render_radar_html(
    ctx: Ctx,
    run_id: str,
    *,
    sync_available: bool = True,
) -> tuple[Path | None, Path]:
    """Render self-contained `radar.html` to `{sync}` and `{run_root}/runs/{id}/radar.html`."""
    priv_map = brief._private_scrub_map(ctx)
    proj_lookup = brief._project_alias_lookup(ctx)
    tmpl = _RADAR_HTML_TMPL_PATH.read_text(encoding="utf-8")

    # 4 concentric rings + outer watch band (center 400, 400)
    ring_radii: list[tuple[str, int, str]] = [
        ("watch", 360, "#1e1b4b"),
        ("hold", 300, "#31102f"),
        ("assess", 240, "#1e293b"),
        ("trial", 170, "#172554"),
        ("adopt", 100, "#064e3b"),
    ]
    svg_rings_lines: list[str] = []
    for r_name, radius, fill in ring_radii:
        svg_rings_lines.append(
            f'<circle cx="400" cy="400" r="{radius}" fill="{fill}" '
            f'stroke="#475569" stroke-width="1.5" />'
        )
        svg_rings_lines.append(
            f'<text x="404" y="{400 - radius + 16}" fill="#94a3b8" '
            f'font-size="11">{html.escape(r_name.upper())}</text>'
        )

    # 6 sectors (60 degrees each)
    svg_sectors_lines: list[str] = []
    for idx, quad in enumerate(items.ALL_QUADRANTS):
        angle_deg = idx * 60.0
        angle_rad = math.radians(angle_deg)
        x2 = 400.0 + 360.0 * math.cos(angle_rad)
        y2 = 400.0 + 360.0 * math.sin(angle_rad)
        svg_sectors_lines.append(
            f'<line x1="400" y1="400" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="#475569" stroke-width="1" />'
        )
        mid_rad = math.radians(angle_deg + 30.0)
        lx = 400.0 + 380.0 * math.cos(mid_rad)
        ly = 400.0 + 380.0 * math.sin(mid_rad)
        q_label = QUADRANT_LABELS.get(quad, quad)
        svg_sectors_lines.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" fill="#cbd5e1" font-size="11" '
            f'text-anchor="middle">{html.escape(q_label)}</text>'
        )

    ring_band_radius: dict[str, tuple[float, float]] = {
        "adopt": (25.0, 90.0),
        "trial": (110.0, 160.0),
        "assess": (180.0, 230.0),
        "hold": (250.0, 290.0),
        "watch": (310.0, 350.0),
    }

    rows = ctx.db.execute(
        "SELECT i.item_id, i.entity_id, i.ring, i.quadrant, i.total, i.in_use, i.rationale, "
        "e.name, e.kind, e.repo, e.visibility "
        "FROM items i JOIN entities e ON e.entity_id = i.entity_id "
        "WHERE i.ring IS NOT NULL "
        "ORDER BY i.item_id ASC"
    ).fetchall()

    svg_blips: list[str] = []
    legend_items: list[str] = []
    table_rows: list[str] = []

    for idx, r in enumerate(rows, start=1):
        vis = str(r["visibility"] or "public")
        if vis != "public" and str(r["kind"]) == "package":
            continue
        iid = str(r["item_id"])
        eid = str(r["entity_id"])
        if vis == "public":
            disp_name = _safe_agent_str(str(r["name"] or eid), priv_map, limit=100)
        else:
            disp_name = _entity_display_for_agent(ctx, eid, priv_map, proj_lookup)

        ring = str(r["ring"] or "assess")
        quad = str(r["quadrant"] or items.default_quadrant(str(r["kind"])))
        score = float(r["total"] or 0.0)
        in_use_str = "yes" if int(r["in_use"] or 0) == 1 else "no"
        ev_url, _ = _primary_evidence_for_entity(ctx, eid, r["repo"])

        q_idx = items.ALL_QUADRANTS.index(quad) if quad in items.ALL_QUADRANTS else 0
        r_min, r_max = ring_band_radius.get(ring, (180.0, 230.0))
        frac = ((idx * 37) % 100) / 100.0
        rad_dist = r_min + (r_max - r_min) * (0.2 + 0.6 * frac)
        deg = q_idx * 60.0 + 12.0 + ((idx * 19) % 36)
        bx = 400.0 + rad_dist * math.cos(math.radians(deg))
        by = 400.0 + rad_dist * math.sin(math.radians(deg))

        hover = html.escape(f"{iid}: {disp_name} ({ring}, {score:.1f})", quote=True)
        svg_blips.append(
            f'<g class="blip">'
            f"<title>{hover}</title>"
            f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="9" '
            f'fill="#38bdf8" stroke="#0f172a" stroke-width="1.5" />'
            f'<text x="{bx:.1f}" y="{by + 3.5:.1f}" fill="#0f172a" font-size="9" '
            f'font-weight="bold" text-anchor="middle">{idx}</text>'
            f"</g>"
        )
        legend_items.append(
            f"<li><strong>{idx}. {html.escape(iid)}</strong> &mdash; "
            f"{html.escape(disp_name)} "
            f'<span class="ring-badge ring-{html.escape(ring)}">{html.escape(ring)}</span></li>'
        )
        table_rows.append(
            "<tr>"
            f"<td>{html.escape(iid)}</td>"
            f"<td>{html.escape(disp_name)}</td>"
            f"<td>{html.escape(quad)}</td>"
            f'<td><span class="ring-badge ring-{html.escape(ring)}">{html.escape(ring)}</span></td>'
            f"<td>{score:.1f}</td>"
            f"<td>{html.escape(in_use_str)}</td>"
            f'<td><a href="{html.escape(ev_url, quote=True)}" rel="noreferrer">evidence</a></td>'
            "</tr>"
        )

    title_str = f"Personal Tech Radar — {run_id}"
    rendered = (
        tmpl.replace("{{TITLE}}", html.escape(title_str))
        .replace("{{GENERATED_AT}}", html.escape(ctx.now_iso))
        .replace("{{SVG_RINGS}}", "\n      ".join(svg_rings_lines))
        .replace("{{SVG_SECTORS}}", "\n      ".join(svg_sectors_lines))
        .replace("{{SVG_BLIPS}}", "\n      ".join(svg_blips))
        .replace("{{LEGEND_ITEMS}}", "\n      ".join(legend_items))
        .replace("{{TABLE_ROWS}}", "\n      ".join(table_rows))
    )
    rendered = brief.scrub_private_text(redact.scrub(rendered), priv_map)

    run_html_path = fsguard.safe_write(
        ctx.paths.run_dir(run_id) / "radar.html",
        rendered,
        mode=0o600,
        root=ctx.paths.runs,
    )
    sync_html_path: Path | None = None
    if sync_available:
        sync_html_path = fsguard.safe_write(
            ctx.paths.sync / "radar.html",
            rendered,
            mode=0o600,
            root=ctx.paths.sync,
        )
    return sync_html_path, run_html_path


def _is_sync_dir_available(sync_dir: Path) -> bool:
    """Return True if `sync_dir` exists, is a non-symlink directory, and is writable."""
    if sync_dir.is_symlink() or not sync_dir.exists() or not sync_dir.is_dir():
        return False
    return os.access(sync_dir, os.W_OK | os.X_OK)


def _verdict_was_applied(ctx: Ctx, run_id: str) -> bool:
    """Return True if verdicts were applied (or run had no pending LLM items)."""
    run_row = ctx.db.execute(
        "SELECT status, counts_json FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if run_row is not None and run_row["counts_json"]:
        try:
            c_obj = json.loads(str(run_row["counts_json"]))
            if isinstance(c_obj, Mapping):
                if c_obj.get("partial") is True or c_obj.get("verdict_applied") is False:
                    return False
                if c_obj.get("verdict_applied") is True:
                    return True
        except json.JSONDecodeError:
            pass

    if db.kv_get(ctx.db, f"verdict_applied:{run_id}") is not None:
        return True

    di_cnt = int(
        ctx.db.execute(
            "SELECT COUNT(*) FROM digest_items WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0]
    )
    return di_cnt > 0


def render_run(
    ctx: Ctx,
    run_id: str,
) -> dict[str, Any]:
    """Execute `radar.py render --run ID`: render digest, projections, exports, and snapshot.

    Always finalizes the run (`degraded` if partial or sync dir unavailable) and releases
    `run.lock`.
    """
    try:
        run_row = ctx.db.execute(
            "SELECT mode, status FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        mode = (
            str(run_row["mode"])
            if run_row is not None
            else (run_id.rsplit("-", 1)[-1] if "-" in run_id else "daily")
        )

        sync_ok = _is_sync_dir_available(ctx.paths.sync)
        verdict_ok = _verdict_was_applied(ctx, run_id)

        digest_md, digest_path, is_quiet = render_digest(
            ctx,
            run_id,
            sync_available=sync_ok,
        )

        # Always write `{state}/project-alerts.json` (and `{sync}/project-alerts.json` when sync_ok)
        state_alerts_path, sync_alerts_path = render_project_alerts_json(
            ctx,
            sync_available=sync_ok,
        )

        tech_radar_path: Path | None = None
        decisions_path: Path | None = None
        inventory_path: Path | None = None
        byor_path: Path | None = None
        sync_html_path: Path | None = None
        run_html_path: Path | None = None
        snapshot_path: Path | None = None
        snapshot_sha: str | None = None

        if mode in ("weekly", "monthly"):
            sync_html_path, run_html_path = render_radar_html(
                ctx,
                run_id,
                sync_available=sync_ok,
            )

        if sync_ok:
            try:
                tech_radar_path = render_tech_radar_md(ctx)
                decisions_path = render_decisions_md(ctx)
                inventory_path = render_inventory_md(ctx)
                byor_path = render_byor_json(ctx)
                snapshot_path, snapshot_sha = snapshot.create_snapshot(ctx)
            except (OSError, errors.RadarError):
                sync_ok = False
                digest_md, digest_path, is_quiet = render_digest(
                    ctx,
                    run_id,
                    sync_available=False,
                )

        final_status = "ok" if (sync_ok and verdict_ok) else "degraded"
        runlog.finish_run(
            ctx,
            run_id,
            final_status,
            counts={
                "quiet": is_quiet,
                "sync_available": sync_ok,
                "verdict_applied": verdict_ok,
            },
        )
        return {
            "ok": True,
            "run_id": run_id,
            "mode": mode,
            "status": final_status,
            "quiet": is_quiet,
            "sync_available": sync_ok,
            "digest_path": str(digest_path),
            "tech_radar_path": str(tech_radar_path) if tech_radar_path else None,
            "decisions_path": str(decisions_path) if decisions_path else None,
            "inventory_path": str(inventory_path) if inventory_path else None,
            "project_alerts_path": str(sync_alerts_path or state_alerts_path),
            "byor_path": str(byor_path) if byor_path else None,
            "html_path": str(run_html_path) if run_html_path else None,
            "snapshot_path": str(snapshot_path) if snapshot_path else None,
            "snapshot_sha": snapshot_sha,
        }
    finally:
        lock.release(run_id, state_dir=ctx.paths.state)
