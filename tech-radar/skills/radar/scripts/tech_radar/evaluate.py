"""Gates, red flags, reported-only criteria, scoring, and ring caps (Spec 05 §1–§3, Spec 12)."""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from . import db, items, sanitize

if TYPE_CHECKING:
    from .context import Ctx

PERMISSIVE_LICENSES: frozenset[str] = frozenset(
    {
        "MIT",
        "APACHE-2.0",
        "BSD-2-CLAUSE",
        "BSD-3-CLAUSE",
        "ISC",
        "MPL-2.0",
    }
)

PRACTITIONER_SOURCES: frozenset[str] = frozenset(
    {
        "hn_algolia",
        "lobsters",
        "reddit_top",
        "devto",
        "simonw",
        "latent_space",
    }
)

_RING_ORDER: dict[str, int] = {
    "hold": 0,
    "watch": 1,
    "assess": 2,
    "trial": 3,
    "adopt": 4,
}


_parse_iso = db.parse_iso


def _days_between(now: datetime, earlier_iso: str | None) -> float | None:
    dt = _parse_iso(earlier_iso)
    if dt is None:
        return None
    return max(0.0, (now - dt).total_seconds() / 86400.0)


def _emoji_density(text: str) -> float:
    if not text:
        return 0.0
    emoji_count = 0
    for ch in text:
        cp = ord(ch)
        if (
            0x1F300 <= cp <= 0x1FAFF
            or 0x2600 <= cp <= 0x27BF
            or 0xFE00 <= cp <= 0xFE0F
            or cp == 0x200D
        ):
            emoji_count += 1
    return emoji_count / max(1, len(text))


def compute_star_velocity(
    ctx: Ctx,
    entity_id: str,
    gate_inputs: Mapping[str, Any] | None = None,
) -> float | None:
    """Compute star velocity per Spec 11 §6 (`(stars_now - stars_prev)/days` or source velocity)."""
    rows = ctx.db.execute(
        "SELECT value, observed_at FROM metrics "
        "WHERE entity_id = ? AND metric = 'stars' AND value IS NOT NULL "
        "ORDER BY observed_at ASC",
        (entity_id,),
    ).fetchall()
    if len(rows) >= 2:
        first_dt = _parse_iso(str(rows[-2]["observed_at"]))
        last_dt = _parse_iso(str(rows[-1]["observed_at"]))
        if first_dt is not None and last_dt is not None:
            days = (last_dt - first_dt).total_seconds() / 86400.0
            if days > 0:
                return round((float(rows[-1]["value"]) - float(rows[-2]["value"])) / days, 4)

    if gate_inputs:
        for k in ("star_velocity_7d", "star_velocity"):
            if isinstance(gate_inputs.get(k), int | float):
                return float(gate_inputs[k])
        if isinstance(gate_inputs.get("stars_7d"), int | float):
            return round(float(gate_inputs["stars_7d"]) / 7.0, 4)

    vel_row = ctx.db.execute(
        "SELECT metric, value FROM metrics "
        "WHERE entity_id = ? AND metric IN ('star_velocity', 'stars_7d') "
        "AND value IS NOT NULL ORDER BY observed_at DESC LIMIT 1",
        (entity_id,),
    ).fetchone()
    if vel_row is not None:
        val = float(vel_row["value"])
        return round(val / 7.0, 4) if str(vel_row["metric"]) == "stars_7d" else val

    return None


def _star_velocity_zscore_30d(
    ctx: Ctx,
    entity_id: str,
    current_velocity: float | None,
    gate_inputs: Mapping[str, Any],
) -> float | None:
    if isinstance(gate_inputs.get("star_velocity_zscore"), int | float):
        return float(gate_inputs["star_velocity_zscore"])
    if current_velocity is None:
        return None
    cutoff_30 = (ctx.now - timedelta(days=30)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = ctx.db.execute(
        "SELECT value, observed_at FROM metrics "
        "WHERE entity_id = ? AND metric = 'stars' AND observed_at >= ? "
        "ORDER BY observed_at ASC",
        (entity_id, cutoff_30),
    ).fetchall()
    if len(rows) < 4:
        return None
    daily_vels: list[float] = []
    for idx in range(1, len(rows)):
        d0 = _parse_iso(str(rows[idx - 1]["observed_at"]))
        d1 = _parse_iso(str(rows[idx]["observed_at"]))
        if d0 and d1 and (d1 - d0).total_seconds() > 0:
            days = (d1 - d0).total_seconds() / 86400.0
            daily_vels.append((float(rows[idx]["value"]) - float(rows[idx - 1]["value"])) / days)
    if len(daily_vels) < 3:
        return None
    mean_v = statistics.mean(daily_vels)
    stdev_v = statistics.pstdev(daily_vels)
    if stdev_v <= 0:
        return 10.0 if current_velocity > mean_v else 0.0
    return (current_velocity - mean_v) / stdev_v


def _practitioner_signal_count(
    ctx: Ctx,
    entity_id: str,
    gate_inputs: Mapping[str, Any],
) -> int:
    if isinstance(gate_inputs.get("practitioner_signal_count"), int):
        return int(gate_inputs["practitioner_signal_count"])
    cutoff_90 = (ctx.now - timedelta(days=90)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = ctx.db.execute(
        "SELECT source_id, title, external_id FROM signals "
        "WHERE entity_id = ? AND withdrawn = 0 AND COALESCE(published_at, fetched_at) >= ?",
        (entity_id, cutoff_90),
    ).fetchall()
    distinct_items: set[str] = set()
    for r in rows:
        src = str(r["source_id"])
        if src not in PRACTITIONER_SOURCES:
            continue
        title = str(r["title"] or "").strip()
        if title.lower().startswith("show hn"):
            continue
        distinct_items.add(f"{src}:{r['external_id'] or title}")
    return len(distinct_items)


def _scale_practitioner_count(count: int) -> float:
    """Scale practitioner count per Spec 05 §2: 0→0, 1→40, 3→70, 6+→100."""
    if count <= 0:
        return 0.0
    if count == 1:
        return 40.0
    if count == 2:
        return 55.0
    if count == 3:
        return 70.0
    if count == 4:
        return 80.0
    if count == 5:
        return 90.0
    return 100.0


def _compute_velocity_percentile(ctx: Ctx, entity_id: str, vel: float | None) -> float | None:
    if vel is None:
        return None
    star_rows = ctx.db.execute(
        "SELECT entity_id, value, observed_at FROM metrics "
        "WHERE metric = 'stars' AND value IS NOT NULL "
        "AND entity_id IN (SELECT DISTINCT entity_id FROM items WHERE state = 'candidate') "
        "ORDER BY entity_id ASC, observed_at ASC"
    ).fetchall()
    stars_by_entity: dict[str, list[tuple[float, str]]] = {}
    for r in star_rows:
        eid = str(r["entity_id"])
        stars_by_entity.setdefault(eid, []).append((float(r["value"]), str(r["observed_at"])))

    computed_by_entity: dict[str, float] = {}
    for eid, obs_list in stars_by_entity.items():
        if len(obs_list) >= 2:
            first_dt = _parse_iso(obs_list[-2][1])
            last_dt = _parse_iso(obs_list[-1][1])
            if first_dt is not None and last_dt is not None:
                days = (last_dt - first_dt).total_seconds() / 86400.0
                if days > 0:
                    computed_by_entity[eid] = round((obs_list[-1][0] - obs_list[-2][0]) / days, 4)

    fallback_rows = ctx.db.execute(
        "SELECT entity_id, metric, value FROM metrics "
        "WHERE metric IN ('star_velocity', 'stars_7d') AND value IS NOT NULL "
        "AND entity_id IN (SELECT DISTINCT entity_id FROM items WHERE state = 'candidate') "
        "ORDER BY observed_at DESC"
    ).fetchall()
    for r in fallback_rows:
        eid = str(r["entity_id"])
        if eid in computed_by_entity:
            continue
        val = float(r["value"])
        computed_by_entity[eid] = round(val / 7.0, 4) if str(r["metric"]) == "stars_7d" else val

    all_vels = list(computed_by_entity.values())
    if len(all_vels) >= 3:
        below_or_eq = sum(1 for v in all_vels if v <= vel)
        return round(100.0 * below_or_eq / len(all_vels), 2)
    if vel <= 0:
        return 10.0
    if vel >= 20.0:
        return 95.0
    if vel >= 10.0:
        return 88.0
    if vel >= 5.0:
        return 78.0
    if vel >= 1.0:
        return 65.0
    return 45.0


def _compute_download_percentile(
    ctx: Ctx,
    entity_id: str,
    gate_inputs: Mapping[str, Any],
) -> float | None:
    if isinstance(gate_inputs.get("download_growth_percentile"), int | float):
        return float(gate_inputs["download_growth_percentile"])
    dl = gate_inputs.get("downloads_30d") or gate_inputs.get("downloads")
    if not isinstance(dl, int | float):
        m_row = ctx.db.execute(
            "SELECT value FROM metrics "
            "WHERE entity_id = ? AND metric IN ('downloads_30d', 'downloads', 'recent_downloads') "
            "ORDER BY observed_at DESC LIMIT 1",
            (entity_id,),
        ).fetchone()
        if m_row is not None and m_row["value"] is not None:
            dl = float(m_row["value"])
    if not isinstance(dl, int | float) or dl <= 0:
        return None
    # Smooth log-scaled percentile for downloads
    log_dl = math.log10(max(1.0, float(dl)))
    return round(min(100.0, max(10.0, (log_dl / 6.0) * 100.0)), 2)


def _load_gate_inputs(
    ctx: Ctx,
    entity_id: str,
    explicit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    raw_kv = db.kv_get(ctx.db, f"gate_inputs:{entity_id}")
    if raw_kv:
        try:
            loaded = json.loads(raw_kv)
            if isinstance(loaded, dict):
                merged.update(loaded)
        except json.JSONDecodeError:
            pass
    if explicit:
        merged.update(explicit)
    return merged


def _has_mal_in_db(ctx: Ctx, entity_id: str) -> bool:
    alert_row = ctx.db.execute(
        "SELECT 1 FROM alerts WHERE entity_id = ? AND kind IN ('compromise', 'incident') "
        "AND confidence = 'confirmed' AND state != 'withdrawn' LIMIT 1",
        (entity_id,),
    ).fetchone()
    if alert_row is not None:
        return True

    sig_rows = ctx.db.execute(
        "SELECT external_id, payload_json FROM signals "
        "WHERE entity_id = ? AND type = 'compromise' AND confidence = 'confirmed' "
        "AND withdrawn = 0",
        (entity_id,),
    ).fetchall()
    for r in sig_rows:
        ext = str(r["external_id"] or "").upper()
        if ext.startswith("MAL-"):
            return True
        if r["payload_json"]:
            try:
                p = json.loads(str(r["payload_json"]))
                if isinstance(p, Mapping) and p.get("is_mal") is True:
                    return True
            except json.JSONDecodeError:
                pass
    return False


def _has_supply_chain_incident_12m(ctx: Ctx, entity_id: str) -> bool:
    cutoff_365 = (ctx.now - timedelta(days=365)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    alert_row = ctx.db.execute(
        "SELECT 1 FROM alerts WHERE entity_id = ? AND kind IN ('compromise', 'incident') "
        "AND confidence = 'confirmed' AND state != 'withdrawn' AND raised_at >= ? LIMIT 1",
        (entity_id, cutoff_365),
    ).fetchone()
    if alert_row is not None:
        return True

    sig_row = ctx.db.execute(
        "SELECT 1 FROM signals WHERE entity_id = ? AND type = 'compromise' "
        "AND confidence = 'confirmed' AND withdrawn = 0 "
        "AND COALESCE(published_at, fetched_at) >= ? LIMIT 1",
        (entity_id, cutoff_365),
    ).fetchone()
    return sig_row is not None


def _has_unpatched_crit_high_in_db(ctx: Ctx, entity_id: str) -> bool:
    alert_row = ctx.db.execute(
        "SELECT 1 FROM alerts WHERE entity_id = ? AND kind IN ('compromise', 'kev') "
        "AND state = 'open' LIMIT 1",
        (entity_id,),
    ).fetchone()
    return alert_row is not None


def _independent_source_count(
    ctx: Ctx,
    entity_id: str,
    gate_inputs: Mapping[str, Any],
) -> int:
    if isinstance(gate_inputs.get("independent_source_count"), int):
        return int(gate_inputs["independent_source_count"])
    rows = ctx.db.execute(
        "SELECT DISTINCT source_id FROM signals WHERE entity_id = ? AND withdrawn = 0",
        (entity_id,),
    ).fetchall()
    sources = {str(r["source_id"]) for r in rows if r["source_id"]}
    if gate_inputs.get("stars") or gate_inputs.get("downloads_30d"):
        sources.add("registry_metrics")
    return max(1, len(sources))


def _resolve_item_row(ctx: Ctx, item: str | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(item, Mapping):
        item_id = str(item.get("item_id") or "")
        if item_id:
            row = ctx.db.execute(
                "SELECT i.*, e.kind, e.ecosystem, e.name, e.repo, e.visibility "
                "FROM items i JOIN entities e ON e.entity_id = i.entity_id "
                "WHERE i.item_id = ?",
                (item_id,),
            ).fetchone()
            if row is not None:
                return dict(row)
        return dict(item)

    key = str(item).strip()
    row = ctx.db.execute(
        "SELECT i.*, e.kind, e.ecosystem, e.name, e.repo, e.visibility "
        "FROM items i JOIN entities e ON e.entity_id = i.entity_id "
        "WHERE i.item_id = ? OR i.entity_id = ?",
        (key, key),
    ).fetchone()
    if row is None:
        raise ValueError(f"item not found: {key}")
    return dict(row)


def _evaluate_core(
    ctx: Ctx,
    item_row: Mapping[str, Any],
    *,
    gate_inputs: Mapping[str, Any],
    llm_fields: Mapping[str, str] | None = None,
    fit: float | None = None,
    quality_adj: float | None = None,
    requested_ring: str | None = None,
    effort: str | None = None,
) -> dict[str, Any]:
    entity_id = str(item_row["entity_id"])
    kind = str(item_row.get("kind") or "package")
    vis = str(item_row.get("visibility") or gate_inputs.get("visibility") or "public")
    repo = item_row.get("repo") or gate_inputs.get("repo")

    llm = dict(llm_fields or {})
    cloud_code_upload = str(llm.get("cloud_code_upload") or "unknown").lower()
    arbitrary_shell = str(llm.get("arbitrary_shell") or "unknown").lower()
    broad_credentials = str(llm.get("broad_credentials") or "unknown").lower()
    telemetry_no_optout = str(llm.get("telemetry_no_optout") or "unknown").lower()

    # Ages and dates
    commit_days = (
        float(gate_inputs["last_commit_days"])
        if isinstance(gate_inputs.get("last_commit_days"), int | float)
        else _days_between(
            ctx.now,
            gate_inputs.get("last_commit_at") or gate_inputs.get("pushed_at"),
        )
    )
    release_days = (
        float(gate_inputs["release_age_days"])
        if isinstance(gate_inputs.get("release_age_days"), int | float)
        else _days_between(ctx.now, gate_inputs.get("last_release_at"))
    )

    first_iso = gate_inputs.get("first_release_at") or gate_inputs.get("repo_created_at")
    age_days = (
        float(gate_inputs["age_days"])
        if isinstance(gate_inputs.get("age_days"), int | float)
        else _days_between(ctx.now, first_iso)
    )

    # Pure-source / technique / model defaults
    is_non_pkg_kind = kind in ("technique", "model")

    # GATE-03: last default-branch commit <= 6 months (180 days)
    if is_non_pkg_kind and commit_days is None:
        gate_03 = "pass"
    elif commit_days is None:
        gate_03 = "unknown"
    elif commit_days <= 180.0:
        gate_03 = "pass"
    else:
        gate_03 = "fail"

    # GATE-04: last release <= 12 months (365 days); explicit no_releases -> fail
    if gate_inputs.get("has_releases") is False and gate_inputs.get("has_tags") is False:
        gate_04 = "fail"
    elif is_non_pkg_kind and release_days is None:
        gate_04 = "pass"
    elif release_days is None:
        gate_04 = "unknown"
    elif release_days <= 365.0:
        gate_04 = "pass"
    else:
        gate_04 = "fail"

    # GATE-05 & GATE-06: OSV / GHSA advisories
    osv_avail = bool(gate_inputs.get("osv_available") is True)
    has_mal = bool(gate_inputs.get("has_mal_advisory") is True) or _has_mal_in_db(ctx, entity_id)
    has_unpatched = bool(
        gate_inputs.get("has_unpatched_high_crit") is True
    ) or _has_unpatched_crit_high_in_db(ctx, entity_id)

    if has_unpatched:
        gate_05 = "fail"
    elif osv_avail or is_non_pkg_kind:
        gate_05 = "pass"
    else:
        gate_05 = "unknown"

    if has_mal:
        gate_06 = "fail"
    elif osv_avail or is_non_pkg_kind:
        gate_06 = "pass"
    else:
        gate_06 = "unknown"

    # GATE-07: first release/repo creation >= 90 days
    if is_non_pkg_kind and age_days is None:
        gate_07 = "pass"
    elif age_days is None:
        gate_07 = "unknown"
    elif age_days >= 90.0:
        gate_07 = "pass"
    else:
        gate_07 = "fail"

    # GATE-08: recommended version published >= 8 days ago
    cooldown_days: int | None = None
    if is_non_pkg_kind and release_days is None:
        gate_08 = "pass"
    elif release_days is None:
        gate_08 = "unknown"
    elif release_days >= 8.0:
        gate_08 = "pass"
    else:
        gate_08 = "fail"
        cooldown_days = int(release_days)

    # GATE-09: entity exists live and is public
    if gate_inputs.get("not_found") is True or gate_inputs.get("private_repo") is True:
        gate_09 = "fail"
    elif vis == "public":
        gate_09 = "pass"
    elif vis == "private":
        gate_09 = "fail"
    else:
        gate_09 = "unknown"

    # GATE-10: macOS arm64 supported
    raw_arm64 = str(gate_inputs.get("macos_arm64") or "").lower()
    if raw_arm64 in ("pass", "fail", "unknown"):
        gate_10 = raw_arm64
    elif kind in ("skill", "technique", "model", "mcp", "plugin"):
        gate_10 = "pass"
    else:
        gate_10 = "unknown"

    # GATE-14: cloud code upload
    if cloud_code_upload == "no":
        gate_14 = "pass"
    elif cloud_code_upload == "yes":
        gate_14 = "fail"
    else:
        gate_14 = "unknown"

    gates: dict[str, str] = {
        "GATE-03": gate_03,
        "GATE-04": gate_04,
        "GATE-05": gate_05,
        "GATE-06": gate_06,
        "GATE-07": gate_07,
        "GATE-08": gate_08,
        "GATE-09": gate_09,
        "GATE-10": gate_10,
        "GATE-14": gate_14,
    }

    # RED FLAGS (Spec 05 §1)
    red_flags: list[str] = []
    extra_flags: list[str] = []

    vel = compute_star_velocity(ctx, entity_id, gate_inputs)
    zscore = _star_velocity_zscore_30d(ctx, entity_id, vel, gate_inputs)
    contributors = gate_inputs.get("contributors_count")
    issues_prs_30d = gate_inputs.get("issues_prs_30d")
    low_activity = (isinstance(contributors, int) and contributors <= 2) or (
        isinstance(issues_prs_30d, int) and issues_prs_30d < 5
    )
    if zscore is not None and zscore > 3.0 and low_activity:
        red_flags.append("REDF-02")
        extra_flags.append("hype-spike")

    if age_days is not None and age_days < 30.0:
        red_flags.append("REDF-03")

    if arbitrary_shell == "yes":
        red_flags.append("REDF-05")

    if broad_credentials == "yes":
        red_flags.append("REDF-06")

    if bool(gate_inputs.get("supply_chain_incident_12m") is True) or _has_supply_chain_incident_12m(
        ctx, entity_id
    ):
        red_flags.append("REDF-07")

    if telemetry_no_optout == "yes":
        red_flags.append("REDF-08")

    # REDF-09: AI-slop heuristics
    readme_text = str(gate_inputs.get("readme_text") or "")
    readme_bytes = int(gate_inputs.get("readme_bytes") or len(readme_text.encode("utf-8")))
    has_tests = gate_inputs.get("has_tests")
    emoji_dens = (
        float(gate_inputs["emoji_density"])
        if isinstance(gate_inputs.get("emoji_density"), int | float)
        else _emoji_density(readme_text)
    )
    unrepro_bench = bool(gate_inputs.get("unreproducible_benchmarks") is True)
    slop_hit = (
        (readme_bytes > 30 * 1024 and has_tests is False) or emoji_dens > 0.03 or unrepro_bench
    )
    if slop_hit:
        red_flags.append("REDF-09")

    # REDF-10: closed-source binary for a tool with code access
    lic_str = str(gate_inputs.get("license") or "").strip()
    is_proprietary = lic_str.lower() in ("proprietary", "closed", "commercial", "none", "other")
    source_avail = gate_inputs.get("source_available")
    if kind in ("cli", "mcp", "plugin", "skill") and (
        source_avail is False or (is_proprietary and not repo)
    ):
        red_flags.append("REDF-10")

    # REPORTED-ONLY criteria (Spec 05 §1 — never affect score or ring)
    lic_norm = lic_str.upper() if lic_str else "UNKNOWN"
    top_share = gate_inputs.get("top_contributor_share")
    bus_factor = gate_inputs.get("bus_factor")
    if bus_factor is None and isinstance(top_share, int | float):
        bus_factor = 1 if float(top_share) >= 0.8 else 2

    practitioner_cnt = _practitioner_signal_count(ctx, entity_id, gate_inputs)
    indep_sources = _independent_source_count(ctx, entity_id, gate_inputs)

    reported: dict[str, Any] = {
        "GATE-01": lic_str or "unknown",
        "license_permissive": lic_norm in PERMISSIVE_LICENSES,
        "GATE-02": bool(repo or source_avail is not False),
        "GATE-11": gate_inputs.get("install_channel") or item_row.get("ecosystem") or "unknown",
        "GATE-12": gate_inputs.get("provenance", "unknown"),
        "GATE-13": gate_inputs.get("telemetry_policy", "unknown"),
        "GATE-15": bool(gate_inputs.get("daemon_required", False)),
        "GATE-16": bus_factor if bus_factor is not None else "unknown",
        "SCORE-01": {
            "stars": gate_inputs.get("stars"),
            "downloads_30d": gate_inputs.get("downloads_30d"),
        },
        "SCORE-04": top_share,
        "SCORE-05": gate_inputs.get("median_issue_response_days"),
        "SCORE-06": gate_inputs.get("has_docs", "unknown"),
        "SCORE-07": has_tests if has_tests is not None else "unknown",
        "SCORE-08": gate_inputs.get("has_security_md", "unknown"),
        "SCORE-09": gate_inputs.get("scorecard"),
        "SCORE-10": gate_inputs.get("dependency_count"),
        "SCORE-13": effort or gate_inputs.get("migration_cost"),
        "SCORE-15": gate_inputs.get("backing", "unknown"),
        "REDF-01": top_share,
        "REDF-04": bool(gate_inputs.get("pipe_to_shell_only", False)),
        "EVID-01": indep_sources,
        "EVID-02": bool(practitioner_cnt > 0 or gate_inputs.get("practitioner_report") is True),
        "EVID-04": list(gate_inputs.get("date_gaps") or []),
        "EVID-05": bool(gate_inputs.get("vendor_benchmark_label", False)),
    }
    if gate_10 == "unknown":
        reported["macos_arm64"] = "unknown"
    if cooldown_days is not None:
        reported["cooldown_days"] = cooldown_days
    if extra_flags:
        reported["flags"] = extra_flags

    # SCORES (Spec 05 §2)
    if isinstance(gate_inputs.get("momentum_override"), int | float):
        momentum = round(min(100.0, max(0.0, float(gate_inputs["momentum_override"]))), 2)
    else:
        components: list[float] = []
        vel_pct = _compute_velocity_percentile(ctx, entity_id, vel)
        if vel_pct is not None:
            components.append(vel_pct)
        dl_pct = _compute_download_percentile(ctx, entity_id, gate_inputs)
        if dl_pct is not None:
            components.append(dl_pct)
        if practitioner_cnt > 0:
            components.append(_scale_practitioner_count(practitioner_cnt))
        momentum = round(statistics.mean(components), 2) if components else 60.0

    quality_base = 60.0
    if "REDF-09" in red_flags or gate_inputs.get("partial_slop") is True:
        quality_base -= 20.0
    if reported["EVID-02"]:
        quality_base += 10.0
    quality_base = min(100.0, max(0.0, quality_base))

    adj = float(quality_adj) if quality_adj is not None else 0.0
    adj = min(15.0, max(-15.0, adj))
    quality = round(min(100.0, max(0.0, quality_base + adj)), 2)

    fit_val = min(100.0, max(0.0, float(fit))) if fit is not None else None
    total = (
        round(0.35 * momentum + 0.35 * quality + 0.30 * fit_val, 2) if fit_val is not None else None
    )

    # Ring cap computation
    allows_arb_shell = any(
        "arbitrary_shell" in p.lower() or "unrestricted shell" in p.lower()
        for p in ctx.profile.preferences
    )
    hold_reasons: list[str] = []
    assess_reasons: list[str] = []

    if gate_05 == "fail":
        hold_reasons.append("GATE-05: unpatched critical/high advisory")
    if gate_06 == "fail":
        hold_reasons.append("GATE-06: MAL advisory history")
    if gate_14 == "fail":
        hold_reasons.append("GATE-14: requires cloud code upload")
    if "REDF-05" in red_flags and not allows_arb_shell:
        hold_reasons.append("REDF-05: unrestricted shell execution")
    if "REDF-07" in red_flags:
        hold_reasons.append("REDF-07: confirmed supply-chain incident within 12 months")
    if "REDF-10" in red_flags:
        hold_reasons.append("REDF-10: closed-source binary with code access")

    for g_id in ("GATE-03", "GATE-04", "GATE-07", "GATE-08", "GATE-10"):
        if gates[g_id] != "pass":
            assess_reasons.append(f"{g_id}: {gates[g_id]}")
    for g_id in ("GATE-05", "GATE-06", "GATE-14"):
        if gates[g_id] == "unknown":
            assess_reasons.append(f"{g_id}: unknown")
    if arbitrary_shell == "unknown":
        assess_reasons.append("REDF-05: arbitrary_shell unknown")
    for rf_id in ("REDF-02", "REDF-03", "REDF-06", "REDF-08", "REDF-09"):
        if rf_id in red_flags:
            assess_reasons.append(rf_id)

    cap: str | None = None
    if hold_reasons:
        cap = "hold"
    elif assess_reasons:
        cap = "assess"

    final_ring = requested_ring
    if final_ring is not None:
        if cap == "hold":
            final_ring = "hold"
        elif cap == "assess" and _RING_ORDER.get(final_ring, 2) > _RING_ORDER["assess"]:
            final_ring = "assess"
        if "REDF-03" in red_flags and total is not None and total < 70.0 and final_ring != "hold":
            final_ring = "watch"

    return {
        "item_id": item_row["item_id"],
        "entity_id": entity_id,
        "gates": gates,
        "red_flags": red_flags,
        "reported": reported,
        "momentum": momentum,
        "quality_base": quality_base,
        "quality_adj": adj if quality_adj is not None else None,
        "quality": quality,
        "fit": fit_val,
        "total": total,
        "cap": cap,
        "ring": final_ring,
        "hold_reasons": hold_reasons,
        "assess_reasons": assess_reasons,
        "metrics_summary": {
            "stars": gate_inputs.get("stars"),
            "star_velocity_7d": vel,
            "downloads_30d": gate_inputs.get("downloads_30d"),
            "age_days": round(age_days, 1) if age_days is not None else None,
            "release_age_days": round(release_days, 1) if release_days is not None else None,
        },
    }


def prescore(
    ctx: Ctx,
    item: str | Mapping[str, Any],
    *,
    gate_inputs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute script-observable gates, red flags, reported criteria, and base scores."""
    item_row = _resolve_item_row(ctx, item)
    entity_id = str(item_row["entity_id"])
    inputs = _load_gate_inputs(ctx, entity_id, gate_inputs)

    res = _evaluate_core(
        ctx,
        item_row,
        gate_inputs=inputs,
        llm_fields=None,
        fit=None,
        quality_adj=None,
        requested_ring=item_row.get("ring"),
    )

    ctx.db.execute(
        "UPDATE items SET "
        "momentum = ?, quality = ?, gates_json = ?, red_flags_json = ?, reported_json = ? "
        "WHERE item_id = ?",
        (
            res["momentum"],
            res["quality_base"],
            json.dumps(res["gates"], separators=(",", ":"), sort_keys=True),
            json.dumps(res["red_flags"], separators=(",", ":")),
            json.dumps(res["reported"], separators=(",", ":"), sort_keys=True),
            item_row["item_id"],
        ),
    )
    ctx.db.commit()
    return res


def finalize(
    ctx: Ctx,
    item: str | Mapping[str, Any],
    verdict: Mapping[str, Any],
    *,
    gate_inputs: Mapping[str, Any] | None = None,
    extra_cap: str | None = None,
    extra_reasons: Sequence[str] = (),
) -> dict[str, Any]:
    """Finalize item scores and ring caps incorporating LLM fields and T10 verification."""
    item_row = _resolve_item_row(ctx, item)
    entity_id = str(item_row["entity_id"])
    inputs = _load_gate_inputs(ctx, entity_id, gate_inputs)

    llm_fields = verdict.get("llm_fields")
    llm_map = llm_fields if isinstance(llm_fields, Mapping) else {}
    req_ring = verdict.get("ring")
    req_ring_str = str(req_ring) if req_ring else None
    fit_val = float(verdict["fit"]) if verdict.get("fit") is not None else 0.0
    q_adj = float(verdict["quality_adj"]) if verdict.get("quality_adj") is not None else 0.0
    effort = str(verdict["effort"]) if verdict.get("effort") else None

    res = _evaluate_core(
        ctx,
        item_row,
        gate_inputs=inputs,
        llm_fields=llm_map,
        fit=fit_val,
        quality_adj=q_adj,
        requested_ring=req_ring_str,
        effort=effort,
    )

    final_ring = res["ring"]
    cap = res["cap"]
    reasons = [*res["hold_reasons"], *res["assess_reasons"], *extra_reasons]

    if extra_cap == "hold":
        cap = "hold"
        if final_ring is not None:
            final_ring = "hold"
    elif extra_cap == "assess":
        if cap != "hold":
            cap = "assess"
        if final_ring is not None and _RING_ORDER.get(final_ring, 2) > _RING_ORDER["assess"]:
            final_ring = "assess"

    if extra_reasons:
        existing_flags = list(res["reported"].get("flags") or [])
        for r_txt in extra_reasons:
            if r_txt not in existing_flags:
                existing_flags.append(r_txt)
        res["reported"]["flags"] = existing_flags

    res["ring"] = final_ring
    res["cap"] = cap
    res["cap_reasons"] = reasons

    quad = str(verdict.get("quadrant") or item_row.get("quadrant") or "")
    if not quad or quad not in items.ALL_QUADRANTS:
        quad = items.default_quadrant(str(item_row.get("kind") or "package"), entity_id=entity_id)
    res["quadrant"] = quad

    why_raw = str(verdict.get("why") or item_row.get("rationale") or "")
    rationale = db.scrub_text(sanitize.clean(why_raw, limit=400))
    confidence = str(verdict.get("confidence") or item_row.get("confidence") or "medium")
    action = str(verdict.get("action") or "surface")
    new_state = (
        "active" if action in ("surface", "watch") else str(item_row.get("state") or "candidate")
    )
    if action == "watch" and final_ring is None:
        final_ring = "watch"
        res["ring"] = "watch"
    elif action == "drop":
        if not int(item_row.get("in_use") or 0):
            final_ring = str(item_row["ring"]) if item_row.get("ring") else None
        elif final_ring is None and item_row.get("ring"):
            final_ring = str(item_row["ring"])
        res["ring"] = final_ring

    ctx.db.execute(
        "UPDATE items SET "
        "ring = ?, quadrant = ?, state = ?, momentum = ?, quality = ?, quality_adj = ?, "
        "fit = ?, total = ?, confidence = ?, gates_json = ?, red_flags_json = ?, "
        "reported_json = ?, rationale = ? "
        "WHERE item_id = ?",
        (
            final_ring,
            quad,
            new_state,
            res["momentum"],
            res["quality"],
            res["quality_adj"],
            res["fit"],
            res["total"],
            confidence,
            json.dumps(res["gates"], separators=(",", ":"), sort_keys=True),
            json.dumps(res["red_flags"], separators=(",", ":")),
            json.dumps(res["reported"], separators=(",", ":"), sort_keys=True),
            rationale,
            item_row["item_id"],
        ),
    )
    ctx.db.commit()
    return res


def create_or_finalize_technique(
    ctx: Ctx,
    slug: str,
    verdict: Mapping[str, Any],
    *,
    evidence_count: int = 1,
) -> dict[str, Any]:
    """Create `technique:<slug>` entity + item (quadrant `techniques`) and score it (Spec 12 A9)."""
    clean_slug = slug.strip().lower()
    entity_id = f"technique:{clean_slug}"
    ensure_res = items.ensure_entity(
        ctx,
        entity_id,
        quadrant="techniques",
        classify_visibility=False,
    )
    eid = ensure_res[0]
    ctx.db.execute("UPDATE entities SET visibility = 'public' WHERE entity_id = ?", (eid,))
    item_id, _created = items.ensure_item(
        ctx,
        eid,
        in_use=False,
        state="active",
        quadrant="techniques",
    )
    ev_momentum = _scale_practitioner_count(max(1, evidence_count))
    gate_inputs = {
        "momentum_override": ev_momentum,
        "osv_available": True,
        "macos_arm64": "pass",
        "independent_source_count": max(2, evidence_count),
    }
    db.kv_set(
        ctx.db,
        f"gate_inputs:{eid}",
        json.dumps(gate_inputs, separators=(",", ":"), sort_keys=True),
    )
    verdict_copy = dict(verdict)
    verdict_copy["quadrant"] = "techniques"
    return finalize(ctx, item_id, verdict_copy, gate_inputs=gate_inputs)
