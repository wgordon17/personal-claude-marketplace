"""Compromise, incident, KEV, integrity, chatter, and scanner evaluation (Spec 07 §1, Spec 12)."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from . import db, exposure, fsguard
from .errors import RadarError
from .sources.base import RawSignal, SourceResult, persist_raw_signal, record_source_skipped
from .sources.security_feeds import (
    entity_id_from_osv_package,
    is_mal_advisory,
    osv_ecosystem,
)

if TYPE_CHECKING:
    from .context import Ctx

# Set from spike-results.md ## uv audit (changed_files: [], venv_created: no)
UV_AUDIT_ENABLED: bool = True

CHATTER_STOPLIST: frozenset[str] = frozenset(
    {
        "react",
        "next",
        "express",
        "click",
        "rich",
        "debug",
        "python",
        "node",
        "docker",
        "curl",
        "request",
        "requests",
        "test",
        "core",
        "utils",
        "common",
        "http",
        "json",
        "yaml",
        "ui",
        "cli",
        "app",
        "api",
        "sdk",
    }
)

CHATTER_SECURITY_KEYWORDS_RE = re.compile(
    r"(?:compromis|malware|backdoor|malicious|hijack|stealer|supply[\s._-]?chain|typosquat|takeover|exfiltrat)",
    re.IGNORECASE,
)

_VERSION_TOKEN_RE = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?[A-Za-z0-9._-]*\b")
_SEMVER_PARTS_RE = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def _parse_semver_tuple(ver: str) -> tuple[int, int, int, str] | None:
    clean = ver.strip()
    m = _SEMVER_PARTS_RE.match(clean)
    if not m:
        return None
    major = int(m.group(1) or 0)
    minor = int(m.group(2) or 0)
    patch = int(m.group(3) or 0)
    rest = clean[m.end() :]
    return (major, minor, patch, rest)


def _compare_versions(v1: str, v2: str) -> int | None:
    t1 = _parse_semver_tuple(v1)
    t2 = _parse_semver_tuple(v2)
    if t1 is None or t2 is None:
        return 0 if v1.strip() == v2.strip() else None
    if t1[:3] != t2[:3]:
        return -1 if t1[:3] < t2[:3] else 1
    if t1[3] == t2[3]:
        return 0
    return -1 if t1[3] < t2[3] else 1


def _matches_ghsa_range(installed: str, expr: str) -> bool:
    for clause in expr.split(","):
        part = clause.strip()
        if not part:
            continue
        for op in (">=", "<=", "==", "!=", "=", ">", "<"):
            if part.startswith(op):
                target = part[len(op) :].strip()
                cmp_val = _compare_versions(installed, target)
                if cmp_val is None:
                    if op in ("=", "==") and installed != target:
                        return False
                    continue
                if op in ("=", "==") and cmp_val != 0:
                    return False
                if op == "!=" and cmp_val == 0:
                    return False
                if op == ">=" and cmp_val < 0:
                    return False
                if op == "<=" and cmp_val > 0:
                    return False
                if op == ">" and cmp_val <= 0:
                    return False
                if op == "<" and cmp_val >= 0:
                    return False
                break
        else:
            if installed != part:
                return False
    return True


def _matches_osv_ranges(installed: str, ranges: Sequence[Mapping[str, Any]]) -> bool:
    for rng in ranges:
        events = rng.get("events")
        if not isinstance(events, list):
            continue
        introduced: str | None = None
        fixed: str | None = None
        last_affected: str | None = None
        for ev in events:
            if not isinstance(ev, Mapping):
                continue
            if ev.get("introduced") is not None:
                introduced = str(ev["introduced"])
            if ev.get("fixed") is not None:
                fixed = str(ev["fixed"])
            if ev.get("last_affected") is not None:
                last_affected = str(ev["last_affected"])

        ok = True
        if introduced and introduced != "0":
            c_intro = _compare_versions(installed, introduced)
            if c_intro is None or c_intro < 0:
                ok = False
        if ok and fixed:
            c_fix = _compare_versions(installed, fixed)
            if c_fix is None or c_fix >= 0:
                ok = False
        if ok and last_affected:
            c_last = _compare_versions(installed, last_affected)
            if c_last is None or c_last > 0:
                ok = False
        if ok and (introduced is not None or fixed is not None or last_affected is not None):
            return True
    return False


def version_is_affected(installed_version: str | None, payload: Mapping[str, Any]) -> bool:
    """Return True if `installed_version` falls within the advisory's affected versions/ranges."""
    if not installed_version:
        return False
    inst = installed_version.strip()
    if not inst:
        return False

    aff_vers = payload.get("affected_versions")
    if isinstance(aff_vers, list) and aff_vers:
        str_vers = [str(v).strip() for v in aff_vers if v]
        if inst in str_vers:
            return True
        aff_ranges = payload.get("affected_ranges")
        if isinstance(aff_ranges, list) and aff_ranges:
            return _matches_osv_ranges(inst, aff_ranges)
        return False

    aff_ranges = payload.get("affected_ranges")
    if isinstance(aff_ranges, list) and aff_ranges:
        return _matches_osv_ranges(inst, aff_ranges)

    ghsa_ranges = payload.get("vulnerable_version_ranges")
    if isinstance(ghsa_ranges, list) and ghsa_ranges:
        return any(_matches_ghsa_range(inst, str(r)) for r in ghsa_ranges if r)

    vulns = payload.get("vulnerabilities")
    if isinstance(vulns, list) and vulns:
        matched_any = False
        has_range = False
        for v in vulns:
            if isinstance(v, Mapping) and v.get("vulnerable_version_range"):
                has_range = True
                if _matches_ghsa_range(inst, str(v["vulnerable_version_range"])):
                    matched_any = True
        if has_range:
            return matched_any

    queried_ver = str(payload.get("queried_version") or "").strip()
    if queried_ver:
        return queried_ver == inst

    return True


def is_within_days(iso_ts: str | None, now: datetime, days: int = 30) -> bool:
    """Return True if `iso_ts` is within `days` of `now` (Spec 12 A1 30-day window)."""
    if not iso_ts:
        return True
    try:
        dt = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return (now - dt) <= timedelta(days=days)
    except ValueError:
        return True


def is_baseline_collect(ctx: Ctx) -> bool:
    """Return True if this is the first source-running collect (Spec 12 A3 baseline rule)."""
    cached = getattr(ctx, "_is_baseline_run", None)
    if isinstance(cached, bool):
        return cached
    is_base = db.kv_get(ctx.db, "baseline_done") is None
    ctx._is_baseline_run = is_base  # type: ignore[attr-defined]
    return is_base


def upsert_alert(
    ctx: Ctx,
    *,
    tier: str,
    kind: str,
    confidence: str,
    dedupe_key: str,
    entity_id: str | None = None,
    signal_id: int | None = None,
    project_key: str | None = None,
    state: str = "open",
    notified_at: str | None = None,
    payload: Mapping[str, Any] | None = None,
) -> tuple[int, bool]:
    """Insert or update an alert row, applying the Spec 12 A3 baseline rule on creation.

    Returns `(alert_id, is_new)`.
    """
    existing = ctx.db.execute(
        "SELECT alert_id, state, notified_at, payload_json FROM alerts WHERE dedupe_key = ?",
        (dedupe_key,),
    ).fetchone()

    resolved_project_key: str | None = project_key
    if resolved_project_key:
        pk_row = ctx.db.execute(
            "SELECT 1 FROM projects WHERE project_key = ?",
            (resolved_project_key,),
        ).fetchone()
        if pk_row is None:
            resolved_project_key = None

    resolved_entity_id: str | None = entity_id
    if resolved_entity_id:
        ent_row = ctx.db.execute(
            "SELECT 1 FROM entities WHERE entity_id = ?",
            (resolved_entity_id,),
        ).fetchone()
        if ent_row is None:
            resolved_entity_id = None

    resolved_signal_id: int | None = signal_id
    if resolved_signal_id is not None:
        sig_row = ctx.db.execute(
            "SELECT 1 FROM signals WHERE signal_id = ?",
            (resolved_signal_id,),
        ).fetchone()
        if sig_row is None:
            resolved_signal_id = None

    effective_notified = notified_at
    if (
        effective_notified is None
        and kind in ("lifecycle", "feature", "chatter")
        and is_baseline_collect(ctx)
    ):
        effective_notified = ctx.now_iso

    payload_str = (
        db.scrub_text(json.dumps(dict(payload), separators=(",", ":"), sort_keys=True))
        if payload
        else None
    )

    if existing is not None:
        alert_id = int(existing["alert_id"])
        if state == "withdrawn":
            ctx.db.execute(
                "UPDATE alerts SET state = 'withdrawn', payload_json = COALESCE(?, payload_json) "
                "WHERE alert_id = ?",
                (payload_str, alert_id),
            )
            ctx.db.commit()
        elif payload_str is not None:
            ctx.db.execute(
                "UPDATE alerts SET payload_json = ? WHERE alert_id = ?",
                (payload_str, alert_id),
            )
            ctx.db.commit()
        return alert_id, False

    cur = ctx.db.execute(
        "INSERT INTO alerts("
        "tier, kind, entity_id, signal_id, project_key, confidence, state, "
        "dedupe_key, raised_at, notified_at, notify_attempts, last_error, payload_json"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?)",
        (
            tier,
            kind,
            resolved_entity_id,
            resolved_signal_id,
            resolved_project_key,
            confidence,
            state,
            dedupe_key,
            ctx.now_iso,
            effective_notified,
            payload_str,
        ),
    )
    ctx.db.commit()
    row_id = ctx.db.execute(
        "SELECT alert_id FROM alerts WHERE dedupe_key = ?",
        (dedupe_key,),
    ).fetchone()
    new_id = int(row_id[0]) if row_id is not None else (int(cur.lastrowid) if cur.lastrowid else 0)
    return new_id, True


def matches_package_context_a28(
    *,
    title: str,
    excerpt: str,
    url: str | None,
    ecosystem: str,
    name: str,
    repo: str | None,
) -> bool:
    """Match a public direct dependency name in package context per Spec 12 A28."""
    clean_name = name.strip()
    if not clean_name:
        return False
    low_name = clean_name.lower()
    in_stoplist = low_name in CHATTER_STOPLIST

    combined_text = f"{title}\n{excerpt}\n{url or ''}"
    if not CHATTER_SECURITY_KEYWORDS_RE.search(combined_text):
        return False

    esc_name = re.escape(clean_name)
    eco_low = (ecosystem or "").strip().lower()
    eco_aliases = {eco_low}
    if eco_low == "pypi":
        eco_aliases.update({"pypi", "pip", "python"})
    elif eco_low == "npm":
        eco_aliases.update({"npm", "node"})
    elif eco_low in ("cargo", "crates.io"):
        eco_aliases.update({"cargo", "crates", "rust"})

    # 1. Registry URL form (`pypi.org/project/<name>`, `npmjs.com/package/<name>`, etc.)
    reg_patterns = [
        rf"pypi\.org/project/{esc_name}(?:[/\s'\")\]]|$)",
        rf"npmjs\.com/package/{esc_name}(?:[/\s'\")\]]|$)",
        rf"crates\.io/crates/{esc_name}(?:[/\s'\")\]]|$)",
    ]
    if repo and "/" in repo:
        reg_patterns.append(rf"github\.com/{re.escape(repo)}(?:[/\s'\")\]]|$)")
    else:
        reg_patterns.append(rf"github\.com/[A-Za-z0-9_.-]+/{esc_name}(?:[/\s'\")\]]|$)")

    for pat in reg_patterns:
        if re.search(pat, combined_text, re.IGNORECASE):
            return True

    # 2. `<ecosystem>:<name>` or `pkg:<ecosystem>/<name>` form
    for ea in eco_aliases:
        if not ea:
            continue
        if re.search(
            rf"\b(?:pkg:)?{re.escape(ea)}[:/]{esc_name}\b",
            combined_text,
            re.IGNORECASE,
        ):
            return True

    if in_stoplist:
        return False

    # 3. Backticked `` `name` `` form
    if re.search(rf"`{esc_name}`", combined_text, re.IGNORECASE):
        return True

    # 4. Name in TITLE next to a version token
    return bool(
        re.search(rf"\b{esc_name}\b", title, re.IGNORECASE) and _VERSION_TOKEN_RE.search(title)
    )


def damerau_levenshtein(a: str, b: str) -> int:
    """Compute optimal string alignment / Damerau-Levenshtein distance between `a` and `b`."""
    s1 = a.lower()
    s2 = b.lower()
    if s1 == s2:
        return 0
    len1, len2 = len(s1), len(s2)
    if abs(len1 - len2) > 2:
        return abs(len1 - len2)

    d: dict[tuple[int, int], int] = {}
    for i in range(-1, len1 + 1):
        d[(i, -1)] = i + 1
    for j in range(-1, len2 + 1):
        d[(-1, j)] = j + 1

    for i in range(len1):
        for j in range(len2):
            cost = 0 if s1[i] == s2[j] else 1
            d[(i, j)] = min(
                d[(i - 1, j)] + 1,
                d[(i, j - 1)] + 1,
                d[(i - 1, j - 1)] + cost,
            )
            if i > 0 and j > 0 and s1[i] == s2[j - 1] and s1[i - 1] == s2[j]:
                d[(i, j)] = min(d[(i, j)], d[(i - 2, j - 2)] + cost)
    return d[(len1 - 1, len2 - 1)]


def _query_package_level_osv_incidents(
    ctx: Ctx,
    direct_public_deps: Sequence[Mapping[str, Any]],
) -> list[RawSignal]:
    """Query OSV `POST /v1/query` by package (no version) for direct public deps (Spec 12 A1).

    In replay mode, silently skips packages without a `/v1/query` fixture.
    """
    out: list[RawSignal] = []
    seen_eids: set[str] = set()
    for dep in direct_public_deps:
        eid = str(dep["entity_id"])
        if eid in seen_eids:
            continue
        seen_eids.add(eid)
        eco = osv_ecosystem(dep.get("ecosystem"), eid)
        name = str(dep.get("name") or "").strip()
        if not eco or not name:
            continue

        body_bytes = json.dumps(
            {"package": {"ecosystem": eco, "name": name}},
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            resp = ctx.http.fetch(
                "https://api.osv.dev/v1/query",
                method="POST",
                body=body_bytes,
                ttl_hours=24,
            )
        except RadarError:
            continue

        try:
            obj = json.loads(resp.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(obj, Mapping):
            continue
        for vuln in obj.get("vulns") or []:
            if not isinstance(vuln, Mapping) or not is_mal_advisory(vuln):
                continue
            vid = str(vuln.get("id") or "").strip()
            if not vid:
                continue
            aff_vers: list[str] = []
            aff_ranges: list[dict[str, Any]] = []
            for aff in vuln.get("affected") or []:
                if isinstance(aff, Mapping):
                    for v in aff.get("versions") or []:
                        if v and str(v) not in aff_vers:
                            aff_vers.append(str(v))
                    for r in aff.get("ranges") or []:
                        if isinstance(r, Mapping):
                            aff_ranges.append(dict(r))

            pub = str(vuln.get("published") or vuln.get("modified") or ctx.now_iso)
            mod = str(vuln.get("modified") or pub)
            out.append(
                RawSignal(
                    source_id="osv_batch",
                    type="compromise",
                    external_id=vid,
                    title=str(vuln.get("summary") or f"OSV MAL {vid}"),
                    url=f"https://osv.dev/vulnerability/{vid}",
                    published_at=pub,
                    untrusted_excerpt=str(vuln.get("details") or vuln.get("summary") or vid),
                    entity_hint=eid,
                    confidence="confirmed",
                    payload={
                        "advisory_id": vid,
                        "is_mal": True,
                        "published": pub,
                        "modified": mod,
                        "withdrawn": vuln.get("withdrawn"),
                        "affected_versions": aff_vers,
                        "affected_ranges": aff_ranges,
                    },
                    dedupe_key=f"osv_query|compromise|{eid}|{vid}",
                    withdrawn=1 if vuln.get("withdrawn") else 0,
                )
            )
    return out


def _attach_auto_exposure_report(
    ctx: Ctx,
    *,
    alert_id: int,
    entity_id: str,
    versions: Sequence[str],
    advisory_id: str,
    published_at: str | None,
    payload: dict[str, Any],
    excerpt: str,
) -> dict[str, Any]:
    """Run automatic read-only exposure check for new compromise/incident alerts (Spec 12 A2)."""
    exp_res = exposure.check(
        ctx,
        entity_id,
        versions=versions,
        alert_id=alert_id,
        advisory_id=advisory_id,
        published_at=published_at,
        payload_text=excerpt,
    )
    updated_payload = dict(payload)
    updated_payload["exposure_report"] = str(exp_res.report_path)
    updated_payload["rotation_checklist"] = exp_res.rotation_checklist
    payload_str = db.scrub_text(json.dumps(updated_payload, separators=(",", ":"), sort_keys=True))
    ctx.db.execute(
        "UPDATE alerts SET payload_json = ? WHERE alert_id = ?",
        (payload_str, alert_id),
    )
    ctx.db.commit()
    return updated_payload


def _run_daily_scanners(
    ctx: Ctx,
) -> tuple[list[RawSignal], list[str]]:
    """Run `uv audit` (on isolated copies, A20) and `osv-scanner` for all-public projects (A32)."""
    scanner_signals: list[RawSignal] = []
    coverage_notes: list[str] = []

    proj_rows = ctx.db.execute(
        "SELECT project_key, main_worktree, display_path, visibility FROM projects WHERE active = 1"
    ).fetchall()

    private_skipped = False
    for prow in proj_rows:
        proj_key = str(prow["project_key"])
        proj_vis = str(prow["visibility"] or "unknown")
        raw_wt = str(prow["main_worktree"] or prow["display_path"])
        proj_dir = Path(raw_wt)
        if not proj_dir.is_dir():
            sf_row = ctx.db.execute(
                "SELECT attrs_json FROM usages "
                "WHERE context = ? AND attrs_json IS NOT NULL LIMIT 1",
                (f"project:{proj_key}",),
            ).fetchone()
            if sf_row is not None and sf_row["attrs_json"]:
                try:
                    sf_attrs = json.loads(str(sf_row["attrs_json"]))
                    if isinstance(sf_attrs, Mapping) and sf_attrs.get("source_file"):
                        cand_parent = Path(str(sf_attrs["source_file"])).parent
                        if cand_parent.is_dir():
                            proj_dir = cand_parent
                except json.JSONDecodeError:
                    pass
            if not proj_dir.is_dir() and "/" in proj_key and not proj_key.startswith("local:"):
                repo_tail = proj_key.rstrip("/").rsplit("/", 1)[-1]
                for pr_root in ctx.paths.project_roots:
                    cand_dir = pr_root / repo_tail
                    if cand_dir.is_dir():
                        proj_dir = cand_dir
                        break
        if not proj_dir.is_dir():
            continue

        dep_rows = ctx.db.execute(
            "SELECT e.entity_id, e.visibility "
            "FROM usages u JOIN entities e ON e.entity_id = u.entity_id "
            "WHERE u.context = ? AND e.entity_id LIKE 'pkg:%'",
            (f"project:{proj_key}",),
        ).fetchall()
        if proj_vis != "public" or any(str(r["visibility"]) != "public" for r in dep_rows):
            private_skipped = True
            continue

        pyproject_path = proj_dir / "pyproject.toml"
        uv_lock_path = proj_dir / "uv.lock"
        if pyproject_path.is_file() and uv_lock_path.is_file():
            if not UV_AUDIT_ENABLED:
                record_source_skipped(ctx, "uv_audit", "disabled: mutates repos")
                if "disabled: mutates repos" not in coverage_notes:
                    coverage_notes.append("disabled: mutates repos")
            else:
                proj_hash = hashlib.sha256(proj_key.encode("utf-8")).hexdigest()[:16]
                audit_dir = ctx.paths.state / "audit" / proj_hash
                fsguard.safe_write(
                    audit_dir / "pyproject.toml",
                    pyproject_path.read_bytes(),
                    root=ctx.paths.state,
                )
                fsguard.safe_write(
                    audit_dir / "uv.lock",
                    uv_lock_path.read_bytes(),
                    root=ctx.paths.state,
                )
                audit_env = {**os.environ, "UV_MALWARE_CHECK": "1"}
                try:
                    res = ctx.procs.run(
                        ["uv", "audit", "--frozen", "--output-format", "json"],
                        cwd=audit_dir,
                        env=audit_env,
                    )
                    if res.stdout.strip():
                        scanner_signals.extend(
                            _parse_uv_audit_output(res.stdout, proj_key, ctx.now_iso)
                        )
                except RadarError:
                    pass

        for non_py_lock in ("package-lock.json", "Cargo.lock", "go.mod"):
            lf_path = proj_dir / non_py_lock
            if not lf_path.is_file():
                continue
            osv_bin = ctx.procs.resolve("osv-scanner")
            if osv_bin is None:
                record_source_skipped(ctx, "osv_scanner", "osv-scanner not installed")
                if "osv-scanner not installed" not in coverage_notes:
                    coverage_notes.append("osv-scanner not installed")
                break
            try:
                res = ctx.procs.run(
                    [
                        "osv-scanner",
                        "scan",
                        f"--lockfile={lf_path}",
                        "--format=json",
                    ],
                    cwd=proj_dir,
                )
                if res.stdout.strip():
                    scanner_signals.extend(
                        _parse_osv_scanner_output(res.stdout, proj_key, ctx.now_iso)
                    )
            except RadarError:
                pass

    if private_skipped:
        record_source_skipped(ctx, "scanners_private", "scanner skipped: private dependencies")
        coverage_notes.append("scanner skipped: private dependencies")

    if (
        ctx.procs.resolve("osv-scanner") is None
        and "osv-scanner not installed" not in coverage_notes
    ):
        record_source_skipped(ctx, "osv_scanner", "osv-scanner not installed")
        coverage_notes.append("osv-scanner not installed")

    return scanner_signals, coverage_notes


def _parse_uv_audit_output(
    stdout: str,
    project_key: str,
    fetched_at: str,
) -> list[RawSignal]:
    try:
        obj = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(obj, Mapping):
        return []

    out: list[RawSignal] = []
    for vuln in obj.get("vulnerabilities") or []:
        if not isinstance(vuln, Mapping):
            continue
        if not is_mal_advisory(vuln):
            continue
        dep = vuln.get("dependency")
        if not isinstance(dep, Mapping):
            continue
        pkg_name = str(dep.get("name") or "").strip().lower()
        ver = str(dep.get("version") or "").strip()
        vid = str(vuln.get("id") or vuln.get("display_id") or "").strip()
        if not pkg_name or not vid:
            continue
        eid = f"pkg:pypi/{pkg_name}"
        pub = str(vuln.get("published") or vuln.get("modified") or fetched_at)
        out.append(
            RawSignal(
                source_id="uv_audit",
                type="compromise",
                external_id=vid,
                title=str(vuln.get("summary") or f"uv audit malware {vid} in {pkg_name}"),
                url=str(vuln.get("link") or f"https://osv.dev/vulnerability/{vid}"),
                published_at=pub,
                untrusted_excerpt=str(vuln.get("description") or vuln.get("summary") or vid),
                entity_hint=eid,
                confidence="confirmed",
                payload={
                    "advisory_id": vid,
                    "aliases": list(vuln.get("aliases") or []),
                    "is_mal": True,
                    "project_key": project_key,
                    "affected_versions": [ver] if ver else [],
                    "queried_version": ver or None,
                    "published": pub,
                    "modified": str(vuln.get("modified") or pub),
                },
                dedupe_key=f"uv_audit|compromise|{eid}|{vid}|{ver}",
            )
        )

    for adv in obj.get("adverse_statuses") or []:
        if not isinstance(adv, Mapping):
            continue
        raw_dep = adv.get("dependency")
        adv_dep: Mapping[str, Any] = raw_dep if isinstance(raw_dep, Mapping) else adv
        pkg_name = str(adv_dep.get("name") or "").strip().lower()
        ver = str(adv_dep.get("version") or "").strip()
        status_str = str(adv.get("status") or adv.get("reason") or "quarantined").strip().lower()
        if not pkg_name:
            continue
        eid = f"pkg:pypi/{pkg_name}"
        adv_id = f"PEP792-{pkg_name}-{status_str}"
        out.append(
            RawSignal(
                source_id="uv_audit",
                type="compromise",
                external_id=adv_id,
                title=f"Adverse package status ({status_str}) for {pkg_name}",
                url=f"https://pypi.org/project/{pkg_name}/",
                published_at=fetched_at,
                untrusted_excerpt=(
                    f"uv audit reported adverse status {status_str} for {pkg_name} {ver}"
                ),
                entity_hint=eid,
                confidence="confirmed",
                payload={
                    "advisory_id": adv_id,
                    "is_mal": True,
                    "status": status_str,
                    "project_key": project_key,
                    "affected_versions": [ver] if ver else [],
                    "queried_version": ver or None,
                    "published": fetched_at,
                    "modified": fetched_at,
                },
                dedupe_key=f"uv_audit|compromise|{eid}|{adv_id}|{ver}",
            )
        )
    return out


def _parse_osv_scanner_output(
    stdout: str,
    project_key: str,
    fetched_at: str,
) -> list[RawSignal]:
    try:
        obj = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(obj, Mapping):
        return []

    out: list[RawSignal] = []
    for res_entry in obj.get("results") or []:
        if not isinstance(res_entry, Mapping):
            continue
        for pkg_entry in res_entry.get("packages") or []:
            if not isinstance(pkg_entry, Mapping):
                continue
            pkg_info = pkg_entry.get("package")
            if not isinstance(pkg_info, Mapping):
                continue
            eco = str(pkg_info.get("ecosystem") or "")
            name = str(pkg_info.get("name") or "")
            ver = str(pkg_info.get("version") or "")
            eid = entity_id_from_osv_package(eco, name)
            if not eid:
                continue
            for vuln in pkg_entry.get("vulnerabilities") or []:
                if not isinstance(vuln, Mapping) or not is_mal_advisory(vuln):
                    continue
                vid = str(vuln.get("id") or "").strip()
                if not vid:
                    continue
                pub = str(vuln.get("published") or vuln.get("modified") or fetched_at)
                out.append(
                    RawSignal(
                        source_id="osv_scanner",
                        type="compromise",
                        external_id=vid,
                        title=str(vuln.get("summary") or f"osv-scanner MAL {vid}"),
                        url=f"https://osv.dev/vulnerability/{vid}",
                        published_at=pub,
                        untrusted_excerpt=str(vuln.get("details") or vid),
                        entity_hint=eid,
                        confidence="confirmed",
                        payload={
                            "advisory_id": vid,
                            "is_mal": True,
                            "project_key": project_key,
                            "affected_versions": [ver] if ver else [],
                            "queried_version": ver or None,
                            "published": pub,
                            "modified": str(vuln.get("modified") or pub),
                        },
                        dedupe_key=f"osv_scanner|compromise|{eid}|{vid}|{ver}",
                    )
                )
    return out


def _evaluate_integrity_regressions(
    ctx: Ctx,
    signals: Sequence[RawSignal],
    usage_by_entity: Mapping[str, list[dict[str, Any]]],
) -> list[int]:
    """Check SEC-10, SEC-11, SEC-12 integrity regressions on direct dependencies."""
    new_alert_ids: list[int] = []

    for sig in signals:
        if sig.source_id not in ("pypi_meta", "npm_registry_meta"):
            continue
        eid = sig.entity_hint
        if not eid or eid not in usage_by_entity:
            continue
        usages = usage_by_entity[eid]
        if not any(int(u.get("direct") or 0) > 0 for u in usages):
            continue

        payload = sig.payload
        reasons: list[str] = []

        # SEC-10: provenance present in previous release, absent in latest
        prov_map = payload.get("provenance_by_version") or payload.get("attestations_by_version")
        latest_ver = str(payload.get("latest_version") or "").strip()
        if isinstance(prov_map, Mapping) and latest_ver and latest_ver in prov_map:
            latest_has_prov = bool(prov_map[latest_ver])
            prior_had_prov = any(
                bool(val) for ver_k, val in prov_map.items() if str(ver_k) != latest_ver
            )
            if prior_had_prov and not latest_has_prov:
                reasons.append("SEC-10: provenance/trusted publishing dropped in latest release")

        # SEC-10: repository URL changed between releases
        prev_repo = payload.get("previous_repository")
        cur_repo = payload.get("repository")
        if prev_repo and cur_repo and str(prev_repo).lower() != str(cur_repo).lower():
            reasons.append(f"SEC-10: repository changed from {prev_repo} to {cur_repo}")

        # SEC-11: maintainer set changed
        prev_maint = payload.get("previous_maintainers")
        cur_maint = payload.get("maintainers")
        if (
            isinstance(prev_maint, list)
            and prev_maint
            and isinstance(cur_maint, list)
            and cur_maint
            and set(map(str, prev_maint)) != set(map(str, cur_maint))
        ):
            reasons.append("SEC-11: publisher/maintainer set changed")

        # SEC-12: new install-time execution (preinstall/install/postinstall, .pth, sdist-only)
        scripts_map = payload.get("install_script_versions")
        if isinstance(scripts_map, Mapping) and latest_ver and latest_ver in scripts_map:
            prior_had_scripts = any(
                bool(val) for ver_k, val in scripts_map.items() if str(ver_k) != latest_ver
            )
            if not prior_had_scripts:
                reasons.append(
                    f"SEC-12: install scripts added in {latest_ver} ({scripts_map[latest_ver]})"
                )

        pth_vers = payload.get("pth_or_sitecustomize_versions")
        if isinstance(pth_vers, list) and pth_vers:
            reasons.append(f"SEC-12: wheel contains startup .pth/sitecustomize ({pth_vers[0]})")

        sdist_only = payload.get("sdist_only_versions")
        wheel_vers = payload.get("wheel_versions")
        if (
            isinstance(sdist_only, list)
            and isinstance(wheel_vers, list)
            and latest_ver
            and latest_ver in sdist_only
            and len(wheel_vers) > 0
        ):
            reasons.append(f"SEC-12: {latest_ver} is sdist-only where wheels previously existed")

        for reason in reasons:
            rule_code = reason.split(":", 1)[0].strip()
            sig_id = persist_raw_signal(
                ctx,
                RawSignal(
                    source_id=sig.source_id,
                    type="compromise",
                    external_id=f"integrity:{eid}:{rule_code}:{latest_ver or 'latest'}",
                    title=f"Integrity regression on {eid}: {reason}",
                    url=sig.url,
                    published_at=ctx.now_iso,
                    untrusted_excerpt=reason,
                    entity_hint=eid,
                    confidence="confirmed",
                    payload={"reason": reason, "version": latest_ver},
                    dedupe_key=f"integrity|{eid}|{rule_code}|{latest_ver or 'latest'}",
                ),
            )
            aid, is_new = upsert_alert(
                ctx,
                tier="P1",
                kind="integrity",
                confidence="confirmed",
                entity_id=eid,
                signal_id=sig_id,
                dedupe_key=f"integrity|{eid}|{rule_code}|{latest_ver or 'latest'}",
                payload={
                    "advisory_id": rule_code,
                    "entity_id": eid,
                    "version": latest_ver or None,
                    "reason": reason,
                },
            )
            if is_new:
                new_alert_ids.append(aid)

    return new_alert_ids


def evaluate(
    ctx: Ctx,
    results: Sequence[SourceResult],
    *,
    mode: Literal["hourly", "daily"] = "daily",
) -> list[int]:
    """Evaluate security signals from `results`, upsert `alerts`, and return new alert IDs."""
    # Ensure baseline flag is snapshotted at start of evaluation
    was_baseline = is_baseline_collect(ctx)

    all_signals: list[RawSignal] = []
    for res in results:
        for sig in res.signals:
            persist_raw_signal(ctx, sig, fetched_at=ctx.now_iso)
            all_signals.append(sig)

    usage_rows = ctx.db.execute(
        "SELECT u.entity_id, u.context, u.version, u.direct, u.attrs_json, "
        "e.kind, e.ecosystem, e.name, e.repo, e.visibility "
        "FROM usages u JOIN entities e ON e.entity_id = u.entity_id "
        "WHERE u.context NOT LIKE 'interest:%'"
    ).fetchall()

    usage_by_entity: dict[str, list[dict[str, Any]]] = {}
    entity_by_repo: dict[str, list[str]] = {}
    direct_public_deps: list[dict[str, Any]] = []

    for row in usage_rows:
        d = dict(row)
        eid = str(d["entity_id"])
        usage_by_entity.setdefault(eid, []).append(d)
        repo_val = str(d.get("repo") or "").strip().lower()
        if repo_val:
            entity_by_repo.setdefault(repo_val, []).append(eid)
        if int(d.get("direct") or 0) > 0 and str(d.get("visibility")) == "public":
            direct_public_deps.append(d)

    if mode == "daily":
        pkg_inc_signals = _query_package_level_osv_incidents(ctx, direct_public_deps)
        for sig in pkg_inc_signals:
            persist_raw_signal(ctx, sig, fetched_at=ctx.now_iso)
            all_signals.append(sig)

        scanner_signals, coverage_notes = _run_daily_scanners(ctx)
        for sig in scanner_signals:
            persist_raw_signal(ctx, sig, fetched_at=ctx.now_iso)
            all_signals.append(sig)
        if coverage_notes:
            db.kv_set(ctx.db, "last_scanner_coverage_json", json.dumps(coverage_notes))

    kev_cves: set[str] = set()
    for sig in all_signals:
        if sig.source_id == "cisa_kev" and sig.external_id:
            kev_cves.add(sig.external_id.strip().upper())
    db_kev_rows = ctx.db.execute(
        "SELECT external_id FROM signals WHERE source_id = 'cisa_kev' AND external_id IS NOT NULL"
    ).fetchall()
    for r in db_kev_rows:
        kev_cves.add(str(r["external_id"]).strip().upper())

    new_alert_ids: list[int] = []
    confirmed_compromised_entities: set[str] = set()

    # 1. Process structured OSV / GHSA / scanner compromise and KEV signals
    for sig in all_signals:
        if sig.source_id not in ("osv_batch", "ghsa_malware", "uv_audit", "osv_scanner"):
            continue
        payload = dict(sig.payload)
        adv_id = str(payload.get("advisory_id") or sig.external_id).strip()
        if not adv_id:
            continue

        is_withdrawn = bool(sig.withdrawn or payload.get("withdrawn"))
        if is_withdrawn:
            ctx.db.execute(
                "UPDATE alerts SET state = 'withdrawn' WHERE dedupe_key LIKE ?",
                (f"%|{adv_id}|%",),
            )
            ctx.db.execute(
                "UPDATE signals SET withdrawn = 1 WHERE external_id = ?",
                (adv_id,),
            )
            ctx.db.commit()
            continue

        target_eids: list[str] = []
        if sig.entity_hint and sig.entity_hint in usage_by_entity:
            target_eids.append(sig.entity_hint)
        elif sig.entity_hint and sig.entity_hint.startswith("gh:"):
            r_slug = sig.entity_hint[len("gh:") :].lower()
            target_eids.extend(entity_by_repo.get(r_slug, []))

        for v_pkg in payload.get("vulnerabilities") or []:
            if isinstance(v_pkg, Mapping) and v_pkg.get("entity_id"):
                cand_eid = str(v_pkg["entity_id"])
                if cand_eid in usage_by_entity and cand_eid not in target_eids:
                    target_eids.append(cand_eid)

        is_mal = bool(payload.get("is_mal") or is_mal_advisory(payload))

        # SEC-13: Lookalike check against direct public deps for MAL advisories
        if is_mal and sig.entity_hint and sig.entity_hint not in usage_by_entity:
            mal_parts = sig.entity_hint.split("/", 1)
            if len(mal_parts) == 2:
                mal_prefix, mal_name = mal_parts[0], mal_parts[1]
                for dep in direct_public_deps:
                    dep_eid = str(dep["entity_id"])
                    if not dep_eid.startswith(f"{mal_prefix}/"):
                        continue
                    dep_name = str(dep.get("name") or dep_eid.split("/", 1)[-1])
                    if 0 < damerau_levenshtein(mal_name, dep_name) <= 1:
                        sig_id = persist_raw_signal(
                            ctx,
                            sig,
                            fetched_at=ctx.now_iso,
                        )
                        date_str = ctx.now_iso[:10]
                        aid, is_new = upsert_alert(
                            ctx,
                            tier="P1",
                            kind="chatter",
                            confidence="unconfirmed",
                            entity_id=dep_eid,
                            signal_id=sig_id,
                            dedupe_key=f"chatter|{dep_eid}|{date_str}",
                            payload={
                                "advisory_id": adv_id,
                                "lookalike": mal_name,
                                "entity_id": dep_eid,
                            },
                        )
                        if is_new:
                            new_alert_ids.append(aid)

        for eid in target_eids:
            usages = usage_by_entity.get(eid, [])
            if not usages:
                continue
            is_any_direct = any(int(u.get("direct") or 0) > 0 for u in usages)
            is_public = any(str(u.get("visibility")) == "public" for u in usages)
            if not is_public:
                continue

            affected_usages = [u for u in usages if version_is_affected(u.get("version"), payload)]

            aliases = [str(a).strip().upper() for a in (payload.get("aliases") or []) if a]
            all_adv_ids = {adv_id.upper(), *aliases}
            matched_kevs = sorted(all_adv_ids & kev_cves)

            if is_mal:
                if affected_usages:
                    confirmed_compromised_entities.add(eid)
                    matched_vers = sorted(
                        {str(u["version"]) for u in affected_usages if u.get("version")}
                    )
                    ver_key = matched_vers[0] if matched_vers else "any"
                    proj_keys = sorted(
                        {
                            str(u["context"])[len("project:") :]
                            for u in affected_usages
                            if str(u.get("context") or "").startswith("project:")
                        }
                    )
                    first_proj = proj_keys[0] if proj_keys else None
                    checklist = exposure.build_rotation_checklist(sig.untrusted_excerpt)
                    alert_payload: dict[str, Any] = {
                        "advisory_id": adv_id,
                        "entity_id": eid,
                        "version": ver_key,
                        "versions": matched_vers,
                        "project_count": max(1, len(proj_keys) or len(affected_usages)),
                        "projects": proj_keys,
                        "published": payload.get("published") or sig.published_at,
                        "modified": payload.get("modified") or sig.published_at,
                        "rotation_checklist": checklist,
                    }
                    sig.payload.update(alert_payload)
                    sig_id = persist_raw_signal(ctx, sig, fetched_at=ctx.now_iso)
                    dedupe = f"compromise|{eid}|{adv_id}|{ver_key}"
                    aid, is_new = upsert_alert(
                        ctx,
                        tier="P0",
                        kind="compromise",
                        confidence="confirmed",
                        entity_id=eid,
                        signal_id=sig_id,
                        project_key=first_proj,
                        dedupe_key=dedupe,
                        notified_at=None,
                        payload=alert_payload,
                    )
                    if is_new:
                        new_alert_ids.append(aid)
                        _attach_auto_exposure_report(
                            ctx,
                            alert_id=aid,
                            entity_id=eid,
                            versions=matched_vers,
                            advisory_id=adv_id,
                            published_at=str(
                                payload.get("published") or sig.published_at or ctx.now_iso
                            ),
                            payload=alert_payload,
                            excerpt=sig.untrusted_excerpt,
                        )
                elif is_any_direct:
                    # Spec 12 A1: Package-level incident (installed version not affected)
                    confirmed_compromised_entities.add(eid)
                    installed_vers = sorted({str(u["version"]) for u in usages if u.get("version")})
                    ver_key = installed_vers[0] if installed_vers else "any"
                    pub_or_mod = str(
                        payload.get("modified")
                        or payload.get("published")
                        or sig.published_at
                        or ctx.now_iso
                    )
                    within_30d = is_within_days(pub_or_mod, ctx.now, days=30)
                    notified_val = None if within_30d else ctx.now_iso
                    bad_vers = [str(v) for v in (payload.get("affected_versions") or []) if v]
                    proj_keys = sorted(
                        {
                            str(u["context"])[len("project:") :]
                            for u in usages
                            if str(u.get("context") or "").startswith("project:")
                        }
                    )
                    first_proj = proj_keys[0] if proj_keys else None
                    alert_payload = {
                        "advisory_id": adv_id,
                        "entity_id": eid,
                        "installed_version": ver_key,
                        "affected_versions": bad_vers,
                        "title": "incident in a package you use (installed version not affected)",
                        "published": payload.get("published") or sig.published_at,
                        "modified": pub_or_mod,
                    }
                    sig_id = persist_raw_signal(ctx, sig, fetched_at=ctx.now_iso)
                    dedupe = f"incident|{eid}|{adv_id}|{ver_key}"
                    aid, is_new = upsert_alert(
                        ctx,
                        tier="P1",
                        kind="incident",
                        confidence="confirmed",
                        entity_id=eid,
                        signal_id=sig_id,
                        project_key=first_proj,
                        dedupe_key=dedupe,
                        notified_at=notified_val,
                        payload=alert_payload,
                    )
                    if is_new:
                        new_alert_ids.append(aid)
                        _attach_auto_exposure_report(
                            ctx,
                            alert_id=aid,
                            entity_id=eid,
                            versions=bad_vers or installed_vers,
                            advisory_id=adv_id,
                            published_at=pub_or_mod,
                            payload=alert_payload,
                            excerpt=sig.untrusted_excerpt,
                        )

                    # SEC-06: Tag-pinned GitHub Action appearing in an incident signal
                    if eid.startswith("gha:"):
                        for u in usages:
                            try:
                                u_attrs = json.loads(str(u.get("attrs_json") or "{}"))
                            except json.JSONDecodeError:
                                u_attrs = {}
                            if u_attrs.get("sha_pinned") is False:
                                tag_dedupe = (
                                    f"incident|{eid}|tag-pinned|{u.get('version') or 'tag'}"
                                )
                                t_aid, t_new = upsert_alert(
                                    ctx,
                                    tier="P1",
                                    kind="incident",
                                    confidence="confirmed",
                                    entity_id=eid,
                                    signal_id=sig_id,
                                    dedupe_key=tag_dedupe,
                                    notified_at=notified_val,
                                    payload={
                                        "advisory_id": adv_id,
                                        "entity_id": eid,
                                        "reason": "tag-pinned GitHub Action in incident",
                                    },
                                )
                                if t_new:
                                    new_alert_ids.append(t_aid)

            elif matched_kevs and is_any_direct and affected_usages:
                # Spec 07 §1 [SEC-04]: CISA KEV exact CVE match
                cve_id = matched_kevs[0]
                matched_vers = sorted(
                    {str(u["version"]) for u in affected_usages if u.get("version")}
                )
                ver_key = matched_vers[0] if matched_vers else "any"
                sig_id = persist_raw_signal(ctx, sig, fetched_at=ctx.now_iso)
                dedupe = f"kev|{eid}|{cve_id}|{ver_key}"
                aid, is_new = upsert_alert(
                    ctx,
                    tier="P1",
                    kind="kev",
                    confidence="confirmed",
                    entity_id=eid,
                    signal_id=sig_id,
                    dedupe_key=dedupe,
                    notified_at=None,
                    payload={
                        "advisory_id": cve_id,
                        "cve_id": cve_id,
                        "entity_id": eid,
                        "version": ver_key,
                        "epss": payload.get("epss"),
                    },
                )
                if is_new:
                    new_alert_ids.append(aid)

    # 2. Daily integrity regressions (SEC-10, SEC-11, SEC-12)
    if mode == "daily":
        new_alert_ids.extend(_evaluate_integrity_regressions(ctx, all_signals, usage_by_entity))

    # 3. Community / blog / HN / Reddit chatter matching (Spec 07 §1, Spec 12 A28)
    date_str = ctx.now_iso[:10]
    for sig in all_signals:
        if sig.confidence != "unconfirmed" or sig.type != "compromise":
            continue
        for dep in direct_public_deps:
            eid = str(dep["entity_id"])
            if eid in confirmed_compromised_entities:
                continue
            eco = str(dep.get("ecosystem") or "")
            name = str(dep.get("name") or "")
            repo = str(dep.get("repo") or "") or None
            if matches_package_context_a28(
                title=sig.title,
                excerpt=sig.untrusted_excerpt,
                url=sig.url,
                ecosystem=eco,
                name=name,
                repo=repo,
            ):
                sig_id = persist_raw_signal(ctx, sig, fetched_at=ctx.now_iso)
                dedupe = f"chatter|{eid}|{date_str}"
                aid, is_new = upsert_alert(
                    ctx,
                    tier="P1",
                    kind="chatter",
                    confidence="unconfirmed",
                    entity_id=eid,
                    signal_id=sig_id,
                    dedupe_key=dedupe,
                    payload={
                        "entity_id": eid,
                        "source_id": sig.source_id,
                        "url": sig.url,
                    },
                )
                if is_new:
                    new_alert_ids.append(aid)

    # 4. SEC-14: Repeat-incident check (>= 2 confirmed compromises/incidents within 365 days)
    cutoff_365 = (ctx.now - timedelta(days=365)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    repeat_rows = ctx.db.execute(
        "SELECT entity_id, COUNT(*) AS cnt FROM alerts "
        "WHERE kind IN ('compromise', 'incident') AND confidence = 'confirmed' "
        "AND state != 'withdrawn' AND raised_at >= ? AND entity_id IS NOT NULL "
        "GROUP BY entity_id HAVING COUNT(*) >= 2",
        (cutoff_365,),
    ).fetchall()
    for r_row in repeat_rows:
        r_eid = str(r_row["entity_id"])
        db.insert_signal(
            ctx.db,
            source_id="security",
            signal_type="lifecycle",
            entity_id=r_eid,
            title=f"Repeat security incidents on {r_eid} (recommend HOLD review)",
            fetched_at=ctx.now_iso,
            dedupe_key=f"security|lifecycle|repeat-incident|{r_eid}",
            payload_json=json.dumps(
                {
                    "reason": "repeat-incident",
                    "recommendation": "HOLD",
                    "incident_count": int(r_row["cnt"]),
                },
                separators=(",", ":"),
            ),
        )

    # Mark baseline_done after the first daily source-running evaluation (Spec 12 A3)
    if mode == "daily" and was_baseline and results:
        db.kv_set(ctx.db, "baseline_done", ctx.now_iso)

    from . import alerts

    alerts.auto_resolve(ctx)
    return new_alert_ids
