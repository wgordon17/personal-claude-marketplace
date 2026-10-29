"""Brief generation, context.json, shortlist validation, and enrich verification (Spec 05 §4)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, timedelta
from typing import TYPE_CHECKING, Any

from . import db, decisions, errors, evaluate, fsguard, items, profile, redact, sanitize, topnames
from .inventory import classify
from .sources import registries

if TYPE_CHECKING:
    from .context import Ctx

SECTION_NAMES: tuple[str, ...] = (
    "security",
    "lifecycle",
    "features",
    "pricing",
    "contenders",
    "horizon",
    "technique",
    "rotation",
    "decisions_due",
    "replies_pending",
    "inventory_changes",
)

PROPOSAL_ECOSYSTEMS: frozenset[str] = frozenset(
    {
        "pypi",
        "npm",
        "cargo",
        "brew",
        "github",
        "mcp",
        "skill",
    }
)

_REPO_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def validate_shortlist(obj: Any) -> list[dict[str, Any]]:
    """Validate `shortlist.json` per Spec 05 §4 and Spec 11 §10.

    Returns a list of field error dicts (empty when valid).
    """
    errs: list[dict[str, Any]] = []
    if not isinstance(obj, Mapping):
        return [{"field": "$", "error": "shortlist must be a JSON object"}]

    item_ids = obj.get("item_ids")
    if not isinstance(item_ids, list):
        errs.append({"field": "item_ids", "error": "must be a list of item_id strings"})
    else:
        for idx, iid in enumerate(item_ids):
            if not isinstance(iid, str) or not iid.strip():
                errs.append({"field": f"item_ids[{idx}]", "error": "must be a non-empty string"})

    proposals = obj.get("proposals")
    if not isinstance(proposals, list):
        errs.append({"field": "proposals", "error": "must be a list of proposal objects"})
        return errs

    for idx, prop in enumerate(proposals):
        pfx = f"proposals[{idx}]"
        if not isinstance(prop, Mapping):
            errs.append({"field": pfx, "error": "proposal must be an object"})
            continue

        name = prop.get("name")
        if not isinstance(name, str) or not name.strip():
            errs.append({"field": f"{pfx}.name", "error": "name is required"})

        eco = prop.get("ecosystem")
        if not isinstance(eco, str) or eco.strip().lower() not in PROPOSAL_ECOSYSTEMS:
            errs.append(
                {
                    "field": f"{pfx}.ecosystem",
                    "error": f"ecosystem must be one of {sorted(PROPOSAL_ECOSYSTEMS)}",
                }
            )

        repo = prop.get("repo")
        if repo is not None and (
            not isinstance(repo, str) or not _REPO_SLUG_RE.match(repo.strip())
        ):
            errs.append({"field": f"{pfx}.repo", "error": "repo must be 'owner/repo' or null"})

        why = prop.get("why")
        if not isinstance(why, str) or not why.strip():
            errs.append({"field": f"{pfx}.why", "error": "why is required"})

        source_url = prop.get("source_url")
        if not isinstance(source_url, str) or not source_url.strip().startswith("https://"):
            errs.append({"field": f"{pfx}.source_url", "error": "source_url must be https://"})

        replaces = prop.get("replaces")
        if replaces is not None and (not isinstance(replaces, str) or not replaces.strip()):
            errs.append({"field": f"{pfx}.replaces", "error": "replaces must be string or null"})

    return errs


def _private_scrub_map(ctx: Ctx) -> dict[str, str]:
    """Build a mapping `{private_token_lower: replacement_alias}` for Spec 12 A8 scrubbing."""
    mapping: dict[str, str] = {}
    scopes = profile.load_private_scopes(ctx.db)

    proj_rows = ctx.db.execute(
        "SELECT project_key, display_path, main_worktree, visibility, alias FROM projects"
    ).fetchall()
    for r in proj_rows:
        vis = str(r["visibility"] or "unknown")
        if vis == "public":
            continue
        alias = str(r["alias"] or "private-project-1")
        pkey = str(r["project_key"] or "")
        dpath = str(r["display_path"] or "")
        mwt = str(r["main_worktree"] or "")
        for raw_tok in (pkey, dpath, mwt):
            if raw_tok:
                mapping[raw_tok] = alias
                mapping[raw_tok.lower()] = alias
                if "/" in raw_tok and not raw_tok.startswith("local:"):
                    tail = raw_tok.rstrip("/").rsplit("/", 1)[-1]
                    if len(tail) >= 3:
                        mapping[tail] = alias
                        mapping[tail.lower()] = alias
                    parts = raw_tok.rstrip("/").split("/")
                    if len(parts) >= 2:
                        slug = f"{parts[-2]}/{parts[-1]}"
                        mapping[slug] = alias
                        mapping[slug.lower()] = alias

    priv_ents = ctx.db.execute(
        "SELECT entity_id, name, repo FROM entities WHERE visibility != 'public'"
    ).fetchall()
    for er in priv_ents:
        eid = str(er["entity_id"] or "")
        name = str(er["name"] or "")
        repo = str(er["repo"] or "")
        for tok in (eid, name, repo):
            if tok and len(tok) >= 3:
                mapping.setdefault(tok, "[PRIVATE]")
                mapping.setdefault(tok.lower(), "[PRIVATE]")

    for sc in scopes:
        clean_sc = sc.strip().rstrip("/*")
        if len(clean_sc) >= 3:
            mapping.setdefault(clean_sc, "[PRIVATE]")
            mapping.setdefault(clean_sc.lower(), "[PRIVATE]")

    return mapping


def scrub_private_text(text: str, priv_map: Mapping[str, str]) -> str:
    """Replace any private project/package/scope tokens in `text` with aliases."""
    out = redact.scrub(text)
    if not priv_map:
        return out
    out_low = out.lower()
    for needle in sorted(priv_map.keys(), key=len, reverse=True):
        if len(needle) < 3:
            continue
        if needle.lower() not in out_low:
            continue
        replacement = priv_map[needle]
        if "/" in needle or ":" in needle:
            pattern = re.escape(needle)
        else:
            pattern = (
                r"(?<![A-Za-z0-9_/@:-])(?<![A-Za-z0-9_]\.)"
                + re.escape(needle)
                + r"(?![A-Za-z0-9_/@:-])(?!\.[A-Za-z0-9_])(?!\"\s*:)"
            )
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
        out_low = out.lower()
    return out


def _is_priv_str(
    s: str,
    priv_map: Mapping[str, str],
    scopes: Sequence[str] = (),
) -> bool:
    """Return True if string `s` matches a private scope or any private token in `priv_map`."""
    low = s.strip().lower()
    if not low:
        return True
    if classify.matches_private_scope(low, scopes):
        return True
    for tok in re.split(r"[\s,;\"'()\[\]{}]+", low):
        if tok and classify.matches_private_scope(tok, scopes):
            return True
    for k in priv_map:
        if len(k) < 3:
            continue
        k_low = k.lower()
        if k_low not in low:
            continue
        if "/" in k or ":" in k:
            return True
        pattern = (
            r"(?<![A-Za-z0-9_/@:-])(?<![A-Za-z0-9_]\.)"
            + re.escape(k_low)
            + r"(?![A-Za-z0-9_/@:-])(?!\.[A-Za-z0-9_])"
        )
        if re.search(pattern, low, flags=re.IGNORECASE):
            return True
    return False


def _project_alias_lookup(ctx: Ctx) -> dict[str, tuple[str, str]]:
    """Return `{project_key: (alias_or_slug, visibility)}`."""
    rows = ctx.db.execute("SELECT project_key, visibility, alias FROM projects").fetchall()
    out: dict[str, tuple[str, str]] = {}
    for r in rows:
        pkey = str(r["project_key"])
        vis = str(r["visibility"] or "private")
        alias = str(r["alias"] or "")
        if vis == "public":
            slug = alias or ("/".join(pkey.rstrip("/").split("/")[-2:]) if "/" in pkey else pkey)
            out[pkey] = (slug, "public")
        else:
            out[pkey] = (alias or "private-project-1", "private")
    return out


def _used_in_for_entity(
    ctx: Ctx,
    entity_id: str,
    proj_lookup: Mapping[str, tuple[str, str]],
) -> list[str]:
    rows = ctx.db.execute(
        "SELECT context FROM usages WHERE entity_id = ? AND context NOT LIKE 'interest:%'",
        (entity_id,),
    ).fetchall()
    used_in: list[str] = []
    for r in rows:
        uctx = str(r["context"])
        if uctx.startswith("project:"):
            pkey = uctx[len("project:") :]
            alias, _ = proj_lookup.get(pkey, ("private-project-1", "private"))
            formatted = f"project:{alias}"
        else:
            formatted = uctx
        if formatted not in used_in:
            used_in.append(formatted)
    return sorted(used_in)


def _evidence_kind(source_id: str, sig_type: str) -> str:
    if sig_type in ("compromise", "kev") or source_id in ("osv_batch", "ghsa_malware", "cisa_kev"):
        return "advisory"
    if source_id in (
        "gh_releases",
        "gh_changelog_file",
        "claude_code_changelog",
        "harness_changelogs",
    ):
        return "release"
    if source_id == "hn_algolia" or source_id.startswith("hn_"):
        return "hn"
    if source_id == "reddit_top":
        return "reddit"
    if source_id == "gh_repo_meta":
        return "readme"
    return "blog"


def _build_common_item_object(
    *,
    ref: str,
    item_id: str | None,
    entity_id: str,
    kind: str,
    visibility: str,
    title: str,
    url: str | None,
    source_id: str,
    published_at: str,
    confidence: str,
    used_in: list[str],
    matched_tokens: list[str],
    metrics: Mapping[str, Any],
    gates: Mapping[str, str],
    red_flags: list[str],
    reported: Mapping[str, Any],
    momentum: float,
    quality_base: float,
    cap: str | None,
    evidence: list[dict[str, str]],
    priv_map: Mapping[str, str],
) -> dict[str, Any]:
    safe_title = scrub_private_text(sanitize.clean(title, limit=200), priv_map)
    safe_url = url if (url and url.startswith("https://")) else "https://github.com"
    cleaned_evidence: list[dict[str, str]] = []
    for ev in evidence:
        ev_url = str(ev.get("url") or safe_url)
        if not ev_url.startswith("https://"):
            ev_url = safe_url
        ev_kind = str(ev.get("kind") or "blog")
        raw_exc = str(ev.get("untrusted_excerpt") or "")
        clean_exc = scrub_private_text(
            db.clean_excerpt(raw_exc, limit=600) or "",
            priv_map,
        )
        cleaned_evidence.append(
            {
                "url": ev_url,
                "kind": ev_kind,
                "untrusted_excerpt": clean_exc,
            }
        )

    return {
        "ref": ref,
        "item_id": item_id,
        "entity_id": scrub_private_text(entity_id, priv_map),
        "kind": kind,
        "visibility": visibility,
        "title": safe_title,
        "url": safe_url,
        "source_id": source_id,
        "published_at": published_at,
        "confidence": confidence if confidence in ("confirmed", "unconfirmed") else "confirmed",
        "used_in": used_in,
        "matched_tokens": matched_tokens,
        "metrics": {
            "stars": metrics.get("stars", 0) or 0,
            "star_velocity_7d": metrics.get("star_velocity_7d", 0.0) or 0.0,
            "downloads_30d": metrics.get("downloads_30d", 0) or 0,
            "age_days": metrics.get("age_days", 0) or 0,
            "release_age_days": metrics.get("release_age_days", 0) or 0,
        },
        "gates": dict(gates),
        "red_flags": list(red_flags),
        "reported": dict(reported),
        "momentum": round(float(momentum or 0.0), 2),
        "quality_base": round(float(quality_base or 60.0), 2),
        "cap": cap,
        "evidence": cleaned_evidence,
    }


def build_context_json(
    ctx: Ctx,
    priv_map: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build `brief/context.json` per Spec 05 §4 and Spec 12 A7/A8."""
    ctx.reload_profile()
    active_priv_map = priv_map if priv_map is not None else _private_scrub_map(ctx)
    scopes = ctx.profile.private_scopes

    clean_watchlist = [
        scrub_private_text(w, active_priv_map)
        for w in ctx.profile.watchlist
        if not _is_priv_str(w, active_priv_map, scopes)
    ]

    # Top 40 in-use public entities by quadrant + private counts by kind
    in_use_by_quad: dict[str, list[dict[str, str]]] = {}
    pub_in_use_rows = ctx.db.execute(
        "SELECT i.entity_id, i.quadrant, e.name, e.kind, e.visibility "
        "FROM items i JOIN entities e ON e.entity_id = i.entity_id "
        "WHERE i.in_use = 1 AND e.visibility = 'public' "
        "ORDER BY i.entity_id ASC LIMIT 40"
    ).fetchall()
    for r in pub_in_use_rows:
        quad = str(
            r["quadrant"] or items.default_quadrant(str(r["kind"]), entity_id=str(r["entity_id"]))
        )
        in_use_by_quad.setdefault(quad, []).append(
            {
                "entity_id": str(r["entity_id"]),
                "name": str(r["name"] or r["entity_id"]),
                "visibility": "public",
            }
        )

    priv_cnt_rows = ctx.db.execute(
        "SELECT e.kind, COUNT(DISTINCT e.entity_id) AS cnt "
        "FROM entities e JOIN usages u ON u.entity_id = e.entity_id "
        "WHERE u.context NOT LIKE 'interest:%' AND e.visibility != 'public' "
        "GROUP BY e.kind"
    ).fetchall()
    private_counts = {str(r["kind"]): int(r["cnt"]) for r in priv_cnt_rows}
    in_use_obj: dict[str, Any] = {
        **in_use_by_quad,
        "private_counts": private_counts,
    }

    # Projects with aliases and public stack/languages only
    proj_lookup = _project_alias_lookup(ctx)
    proj_list: list[dict[str, Any]] = []
    for pkey, (alias, _) in sorted(proj_lookup.items(), key=lambda x: x[1][0]):
        dep_rows = ctx.db.execute(
            "SELECT e.name, e.ecosystem FROM usages u "
            "JOIN entities e ON e.entity_id = u.entity_id "
            "WHERE u.context = ? AND u.direct = 1 AND e.visibility = 'public' "
            "ORDER BY e.name ASC LIMIT 15",
            (f"project:{pkey}",),
        ).fetchall()
        stack: list[str] = []
        langs: list[str] = []
        eco_to_lang = {"pypi": "python", "npm": "typescript", "cargo": "rust", "golang": "go"}
        for dr in dep_rows:
            dname = str(dr["name"] or "").strip()
            if dname and not _is_priv_str(dname, active_priv_map, scopes) and dname not in stack:
                stack.append(dname)
            lang = eco_to_lang.get(str(dr["ecosystem"] or "").lower())
            if lang and lang not in langs:
                langs.append(lang)
        proj_list.append(
            {
                "alias": alias,
                "languages": langs,
                "stack": stack,
            }
        )

    # Top 20 public pain/intent labels
    pain_labels: list[str] = []

    def _add_pain_label(raw: Any) -> None:
        s_lbl = str(raw or "").strip()
        if s_lbl and not _is_priv_str(s_lbl, active_priv_map, scopes):
            scrubbed_lbl = scrub_private_text(s_lbl, active_priv_map)
            if scrubbed_lbl not in pain_labels:
                pain_labels.append(scrubbed_lbl)

    pain_rows = ctx.db.execute(
        "SELECT u.attrs_json FROM usages u "
        "WHERE u.entity_id = 'svc:pain-labels' AND u.context = 'interest:pain' LIMIT 1"
    ).fetchall()
    for pr in pain_rows:
        if pr["attrs_json"]:
            try:
                attrs = json.loads(str(pr["attrs_json"]))
                for lbl in attrs.get("labels") or []:
                    _add_pain_label(lbl)
            except json.JSONDecodeError:
                pass

    fp_pain_rows = ctx.db.execute(
        "SELECT context, token, count FROM fingerprints "
        "WHERE token LIKE 'pain:%' OR token LIKE 'intent:%' "
        "ORDER BY count DESC, token ASC"
    ).fetchall()
    for fpr in fp_pain_rows:
        fp_ctx = str(fpr["context"] or "")
        if fp_ctx.startswith("project:"):
            pkey = fp_ctx[len("project:") :]
            if pkey in proj_lookup and proj_lookup[pkey][1] != "public":
                continue
        raw_tok = str(fpr["token"] or "")
        _add_pain_label(raw_tok.removeprefix("pain:").removeprefix("intent:"))
    pain_labels = pain_labels[:20]

    # Top starred topics
    starred_topics: dict[str, int] = {}
    star_rows = ctx.db.execute(
        "SELECT u.attrs_json FROM usages u JOIN entities e ON e.entity_id = u.entity_id "
        "WHERE u.context = 'interest:starred' AND e.visibility = 'public'"
    ).fetchall()
    for sr in star_rows:
        if sr["attrs_json"]:
            try:
                attrs = json.loads(str(sr["attrs_json"]))
                for t in attrs.get("topics") or []:
                    t_clean = str(t).strip().lower()
                    if t_clean and not _is_priv_str(t_clean, active_priv_map, scopes):
                        starred_topics[t_clean] = starred_topics.get(t_clean, 0) + 1
            except json.JSONDecodeError:
                pass

    # Top 10 token-heavy tools (Spec 12 A13)
    tok_rows = ctx.db.execute(
        "SELECT entity_id, MAX(value) AS val FROM metrics "
        "WHERE entity_id LIKE 'harness:tool/%' AND metric IN ('tokens', 'tokens_90d') "
        "GROUP BY entity_id ORDER BY val DESC LIMIT 10"
    ).fetchall()
    token_heavy_tools = [
        {
            "tool": str(tr["entity_id"])[len("harness:tool/") :],
            "tokens_90d": int(tr["val"] or 0),
        }
        for tr in tok_rows
    ]

    return {
        "profile": {
            "categories": [scrub_private_text(x, active_priv_map) for x in ctx.profile.categories],
            "preferences": [
                scrub_private_text(x, active_priv_map) for x in ctx.profile.preferences
            ],
            "anti_interests": [
                scrub_private_text(x, active_priv_map) for x in ctx.profile.anti_interests
            ],
            "languages": [scrub_private_text(x, active_priv_map) for x in ctx.profile.languages],
            "subscriptions": [
                scrub_private_text(x, active_priv_map) for x in ctx.profile.subscriptions
            ],
        },
        "watchlist": clean_watchlist,
        "in_use": in_use_obj,
        "projects": proj_list,
        "pain_labels": pain_labels,
        "starred_topics": starred_topics,
        "token_heavy_tools": token_heavy_tools,
    }


def _select_rotation_items(ctx: Ctx, limit: int = 4) -> list[dict[str, Any]]:
    """Select the 4 in-use public items with oldest `last_deep_inspect_at` (Spec 05 §7 MEM-11)."""
    rows = ctx.db.execute(
        "SELECT i.*, e.kind, e.name, e.repo, e.visibility "
        "FROM items i JOIN entities e ON e.entity_id = i.entity_id "
        "WHERE i.in_use = 1 AND e.visibility = 'public' "
        "ORDER BY CASE WHEN i.last_deep_inspect_at IS NULL THEN 0 ELSE 1 END ASC, "
        "i.last_deep_inspect_at ASC, i.item_id ASC "
        "LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def _select_stale_items(ctx: Ctx) -> list[dict[str, Any]]:
    """Select items in assess/trial/watch with no ring change > 60 days (Spec 12 A11)."""
    cutoff_60 = (ctx.now - timedelta(days=60)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = ctx.db.execute(
        "SELECT i.*, e.kind, e.name, e.repo, e.visibility, "
        "(SELECT MAX(h.at) FROM ring_history h WHERE h.item_id = i.item_id) AS last_ring_at "
        "FROM items i JOIN entities e ON e.entity_id = i.entity_id "
        "WHERE i.ring IN ('assess', 'trial', 'watch') AND e.visibility = 'public' "
        "ORDER BY i.item_id ASC"
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        last_at = str(r["last_ring_at"] or r["created_at"] or "")
        if last_at and last_at < cutoff_60:
            out.append(dict(r))
    return out


def generate(ctx: Ctx, run_id: str) -> dict[str, Any]:
    """Generate all brief files under `{run_root}/runs/{run_id}/brief/` (Spec 05 §4, A7, A8)."""
    run_row = ctx.db.execute(
        "SELECT mode FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    mode = (
        str(run_row["mode"])
        if run_row is not None
        else (run_id.rsplit("-", 1)[-1] if "-" in run_id else "daily")
    )

    priv_map = _private_scrub_map(ctx)
    proj_lookup = _project_alias_lookup(ctx)
    cutoff_seen = (
        (ctx.now - timedelta(days=items.SEEN_WINDOW_DAYS))
        .astimezone(UTC)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    promote_stats_raw = db.kv_get(ctx.db, "last_promote_stats_json")
    promote_suppressed: dict[str, int] = {}
    if promote_stats_raw:
        try:
            ps_obj = json.loads(promote_stats_raw)
            if isinstance(ps_obj, Mapping) and isinstance(ps_obj.get("suppressed"), Mapping):
                promote_suppressed = {k: int(v or 0) for k, v in ps_obj["suppressed"].items()}
        except json.JSONDecodeError:
            pass

    suppressed = {
        "renovate": int(promote_suppressed.get("renovate", 0)),
        "seen": int(promote_suppressed.get("seen", 0)),
        "anti_interest": int(promote_suppressed.get("anti_interest", 0)),
        "gate_failed": int(promote_suppressed.get("gate_failed", 0)),
        "private": int(promote_suppressed.get("private", 0)),
        "deferred": int(promote_suppressed.get("deferred", 0)),
    }

    sections_data: dict[str, list[dict[str, Any]]] = {name: [] for name in SECTION_NAMES}
    valid_refs: dict[str, dict[str, Any]] = {}

    def _item_row_to_brief_obj(item_row: Mapping[str, Any], *, section: str) -> dict[str, Any]:
        iid = str(item_row["item_id"])
        eid = str(item_row["entity_id"])
        kind = str(item_row.get("kind") or "package")
        vis = str(item_row.get("visibility") or "public")
        used_in = _used_in_for_entity(ctx, eid, proj_lookup)

        gates = json.loads(str(item_row["gates_json"])) if item_row.get("gates_json") else {}
        red_flags = (
            json.loads(str(item_row["red_flags_json"])) if item_row.get("red_flags_json") else []
        )
        reported = (
            json.loads(str(item_row["reported_json"])) if item_row.get("reported_json") else {}
        )
        gate_inputs_raw = db.kv_get(ctx.db, f"gate_inputs:{eid}")
        gate_inputs = json.loads(gate_inputs_raw) if gate_inputs_raw else {}

        sig_rows = ctx.db.execute(
            "SELECT signal_id, source_id, type, title, url, published_at, fetched_at, "
            "confidence, untrusted_excerpt, payload_json "
            "FROM signals WHERE entity_id = ? AND withdrawn = 0 ORDER BY signal_id DESC LIMIT 5",
            (eid,),
        ).fetchall()

        evidence: list[dict[str, str]] = []
        primary_url = f"https://github.com/{item_row.get('repo')}" if item_row.get("repo") else None
        primary_source = "inventory"
        primary_pub = str(item_row.get("created_at") or ctx.now_iso)
        primary_conf = "confirmed"
        matched_tokens: list[str] = []

        for idx, sr in enumerate(sig_rows):
            s_url = str(sr["url"] or primary_url or "https://github.com")
            if idx == 0:
                primary_url = s_url
                primary_source = str(sr["source_id"])
                primary_pub = str(sr["published_at"] or sr["fetched_at"] or primary_pub)
                primary_conf = str(sr["confidence"] or "confirmed")
            if sr["payload_json"]:
                try:
                    p = json.loads(str(sr["payload_json"]))
                    if isinstance(p, Mapping) and isinstance(p.get("matched_tokens"), list):
                        for mt in p["matched_tokens"]:
                            if str(mt) not in matched_tokens:
                                matched_tokens.append(str(mt))
                except json.JSONDecodeError:
                    pass
            raw_t = str(sr["title"] or "")
            raw_e = str(sr["untrusted_excerpt"] or "")
            combined_exc = f"{raw_t}: {raw_e}" if raw_t and raw_t != raw_e else (raw_e or raw_t)
            evidence.append(
                {
                    "url": s_url,
                    "kind": _evidence_kind(str(sr["source_id"]), str(sr["type"])),
                    "untrusted_excerpt": combined_exc,
                }
            )

        if not evidence:
            evidence.append(
                {
                    "url": primary_url or "https://github.com",
                    "kind": "readme",
                    "untrusted_excerpt": f"Entity {eid}",
                }
            )

        cap: str | None = None
        if any(gates.get(g) == "fail" for g in ("GATE-05", "GATE-06", "GATE-14")) or any(
            rf in red_flags for rf in ("REDF-05", "REDF-07", "REDF-10")
        ):
            cap = "hold"
        elif (
            any(
                gates.get(g) != "pass"
                for g in ("GATE-03", "GATE-04", "GATE-07", "GATE-08", "GATE-10")
            )
            or red_flags
        ):
            cap = "assess"

        # Script-composed title from structured identifiers so untrusted text stays in evidence
        composed_title = f"{eid} ({section})"
        valid_refs[iid] = {
            "ref": iid,
            "item_id": iid,
            "entity_id": eid,
            "repo": str(item_row["repo"]) if item_row.get("repo") else None,
            "evidence_urls": [str(ev["url"]) for ev in evidence if ev.get("url")],
            "section": section,
        }

        age_days = evaluate._days_between(
            ctx.now, gate_inputs.get("first_release_at") or gate_inputs.get("repo_created_at")
        )
        if age_days is None:
            raw_age = gate_inputs.get("age_days")
            age_days = int(raw_age) if isinstance(raw_age, int | float) else 0
        release_age_days = evaluate._days_between(ctx.now, gate_inputs.get("last_release_at"))
        if release_age_days is None:
            raw_rel_age = gate_inputs.get("release_age_days")
            release_age_days = int(raw_rel_age) if isinstance(raw_rel_age, int | float) else 0

        return _build_common_item_object(
            ref=iid,
            item_id=iid,
            entity_id=eid,
            kind=kind,
            visibility=vis,
            title=composed_title,
            url=primary_url,
            source_id=primary_source,
            published_at=primary_pub,
            confidence=primary_conf,
            used_in=used_in,
            matched_tokens=matched_tokens,
            metrics={
                "stars": gate_inputs.get("stars", 0),
                "star_velocity_7d": evaluate.compute_star_velocity(ctx, eid, gate_inputs) or 0.0,
                "downloads_30d": gate_inputs.get("downloads_30d", 0),
                "age_days": age_days,
                "release_age_days": release_age_days,
            },
            gates=gates,
            red_flags=red_flags,
            reported=reported,
            momentum=float(item_row.get("momentum") or 50.0),
            quality_base=float(item_row.get("quality") or 60.0),
            cap=cap,
            evidence=evidence,
            priv_map=priv_map,
        )

    def _signal_row_to_brief_obj(
        sig_row: Mapping[str, Any], *, section: str
    ) -> dict[str, Any] | None:
        sig_id = int(sig_row["signal_id"])
        ref = f"S-{sig_id}"
        eid = str(sig_row.get("entity_id") or "")
        vis = "public"
        kind = "package"
        item_id: str | None = None

        if eid:
            ent_r = ctx.db.execute(
                "SELECT kind, visibility FROM entities WHERE entity_id = ?",
                (eid,),
            ).fetchone()
            if ent_r is not None:
                kind = str(ent_r["kind"])
                vis = str(ent_r["visibility"] or "public")
            item_r = ctx.db.execute(
                "SELECT item_id FROM items WHERE entity_id = ?",
                (eid,),
            ).fetchone()
            if item_r is not None:
                item_id = str(item_r["item_id"])

        if vis != "public":
            suppressed["private"] += 1
            return None

        last_surf = sig_row.get("last_surfaced_at")
        if last_surf and str(last_surf) >= cutoff_seen and section not in ("security", "lifecycle"):
            suppressed["seen"] += 1
            return None

        used_in = _used_in_for_entity(ctx, eid, proj_lookup) if eid else []
        matched_tokens: list[str] = []
        if sig_row.get("payload_json"):
            try:
                p = json.loads(str(sig_row["payload_json"]))
                if isinstance(p, Mapping) and isinstance(p.get("matched_tokens"), list):
                    matched_tokens = [str(x) for x in p["matched_tokens"]]
            except json.JSONDecodeError:
                pass

        src_id = str(sig_row["source_id"])
        s_type = str(sig_row["type"])
        s_url = str(sig_row.get("url") or "https://github.com")
        raw_t = str(sig_row.get("title") or "")
        raw_e = str(sig_row.get("untrusted_excerpt") or "")
        combined_exc = f"{raw_t}: {raw_e}" if raw_t and raw_t != raw_e else (raw_e or raw_t)

        composed_title = f"{eid or src_id}: {s_type} ({ref})"
        valid_refs[ref] = {
            "ref": ref,
            "signal_id": sig_id,
            "item_id": item_id,
            "entity_id": eid or None,
            "evidence_urls": [s_url] if s_url else [],
            "section": section,
        }

        return _build_common_item_object(
            ref=ref,
            item_id=item_id,
            entity_id=eid or f"sig:{src_id}/{sig_id}",
            kind=kind,
            visibility="public",
            title=composed_title,
            url=s_url,
            source_id=src_id,
            published_at=str(
                sig_row.get("published_at") or sig_row.get("fetched_at") or ctx.now_iso
            ),
            confidence=str(sig_row.get("confidence") or "confirmed"),
            used_in=used_in,
            matched_tokens=matched_tokens,
            metrics={},
            gates={},
            red_flags=[],
            reported={},
            momentum=50.0,
            quality_base=60.0,
            cap=None,
            evidence=[
                {
                    "url": s_url,
                    "kind": _evidence_kind(src_id, s_type),
                    "untrusted_excerpt": combined_exc,
                }
            ],
            priv_map=priv_map,
        )

    # 1. Signals for security, lifecycle, features, pricing, inventory_changes, technique (S- refs)
    sig_section_map = {
        "compromise": "security",
        "kev": "security",
        "lifecycle": "lifecycle",
        "feature": "features",
        "pricing": "pricing",
        "inventory_change": "inventory_changes",
    }
    all_sigs = ctx.db.execute(
        "SELECT * FROM signals WHERE withdrawn = 0 ORDER BY signal_id ASC"
    ).fetchall()
    for sr in all_sigs:
        st = str(sr["type"])
        sec_name = sig_section_map.get(st)
        if sec_name:
            b_obj = _signal_row_to_brief_obj(dict(sr), section=sec_name)
            if b_obj is not None:
                sections_data[sec_name].append(b_obj)
        elif st in ("technique", "horizon") and not sr["entity_id"]:
            t_sec = "technique" if st == "technique" else "horizon"
            b_obj = _signal_row_to_brief_obj(dict(sr), section=t_sec)
            if b_obj is not None:
                sections_data[t_sec].append(b_obj)

    # 2. Candidate items for contenders, horizon, technique
    cand_rows = ctx.db.execute(
        "SELECT i.*, e.kind, e.name, e.repo, e.visibility "
        "FROM items i JOIN entities e ON e.entity_id = i.entity_id "
        "WHERE i.state = 'candidate' ORDER BY i.item_id ASC"
    ).fetchall()
    for cr in cand_rows:
        if str(cr["visibility"]) != "public":
            suppressed["private"] += 1
            continue
        eid = str(cr["entity_id"])
        # Determine section from signal types for this entity
        st_rows = ctx.db.execute(
            "SELECT DISTINCT type, last_surfaced_at FROM signals "
            "WHERE entity_id = ? AND withdrawn = 0",
            (eid,),
        ).fetchall()
        if st_rows and all(
            r["last_surfaced_at"] and str(r["last_surfaced_at"]) >= cutoff_seen for r in st_rows
        ):
            continue
        types = {str(r["type"]) for r in st_rows}
        if "contender" in types:
            sec = "contenders"
        elif "technique" in types or str(cr["kind"]) == "technique":
            sec = "technique"
        else:
            sec = "horizon"
        sections_data[sec].append(_item_row_to_brief_obj(dict(cr), section=sec))

    # 3. Rotation (4 oldest in-use public items)
    for rot_row in _select_rotation_items(ctx, limit=4):
        sections_data["rotation"].append(_item_row_to_brief_obj(rot_row, section="rotation"))

    # 4. Decisions due
    due_items = decisions.evaluate_due(ctx)
    sections_data["decisions_due"] = due_items
    for d_item in due_items:
        ref = str(d_item["ref"])
        valid_refs[ref] = {
            "ref": ref,
            "item_id": d_item["item_id"],
            "entity_id": d_item["entity_id"],
            "section": "decisions_due",
        }

    # 5. Monthly stale.jsonl (Spec 12 A11)
    stale_items: list[dict[str, Any]] = []
    if mode == "monthly":
        for st_row in _select_stale_items(ctx):
            stale_items.append(_item_row_to_brief_obj(st_row, section="horizon"))

    # Persist valid brief refs in radar.db so `verdict apply` reads from DB (Spec 12 A25)
    db.kv_set(
        ctx.db,
        f"brief_refs:{run_id}",
        json.dumps(valid_refs, separators=(",", ":"), sort_keys=True),
    )

    brief_dir = ctx.paths.run_dir(run_id) / "brief"
    runs_root = ctx.paths.run_root / "runs"

    sections_index: dict[str, dict[str, Any]] = {}
    for sec_name in SECTION_NAMES:
        fname = f"{sec_name}.jsonl"
        rows_list = sections_data[sec_name]
        lines = [
            scrub_private_text(json.dumps(obj, separators=(",", ":"), sort_keys=False), priv_map)
            for obj in rows_list
        ]
        body = ("\n".join(lines) + "\n") if lines else ""
        fsguard.safe_write(brief_dir / fname, body, mode=0o600, root=runs_root)
        sections_index[sec_name] = {"file": fname, "count": len(rows_list)}

    if mode == "monthly":
        stale_lines = [
            scrub_private_text(json.dumps(obj, separators=(",", ":"), sort_keys=False), priv_map)
            for obj in stale_items
        ]
        stale_body = ("\n".join(stale_lines) + "\n") if stale_lines else ""
        fsguard.safe_write(brief_dir / "stale.jsonl", stale_body, mode=0o600, root=runs_root)
        sections_index["stale"] = {"file": "stale.jsonl", "count": len(stale_items)}

    # Source health summary
    sh_rows = ctx.db.execute(
        "SELECT source_id, last_ok_at, last_error, consecutive_failures, skipped_reason "
        "FROM source_health ORDER BY source_id ASC"
    ).fetchall()
    source_health: list[dict[str, Any]] = []
    for sr in sh_rows:
        if sr["skipped_reason"]:
            status = "skipped"
        elif sr["last_error"]:
            status = "error"
        else:
            status = "ok"
        source_health.append(
            {
                "id": str(sr["source_id"]),
                "status": status,
                "consecutive_failures": int(sr["consecutive_failures"] or 0),
                "skipped_reason": sr["skipped_reason"],
            }
        )

    # Usage from previous run
    prev_usage_row = ctx.db.execute(
        "SELECT usage_json FROM runs WHERE run_id != ? AND usage_json IS NOT NULL "
        "ORDER BY started_at DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    usage_last_run: dict[str, Any] | None = None
    if prev_usage_row is not None and prev_usage_row["usage_json"]:
        try:
            with_usage = json.loads(str(prev_usage_row["usage_json"]))
            if isinstance(with_usage, dict):
                usage_last_run = with_usage
        except json.JSONDecodeError:
            pass

    index_obj = {
        "run_id": run_id,
        "mode": mode,
        "generated_at": ctx.now_iso,
        "sections": sections_index,
        "suppressed": suppressed,
        "source_health": source_health,
        "usage_last_run": usage_last_run,
    }
    index_str = scrub_private_text(json.dumps(index_obj, indent=2), priv_map) + "\n"
    fsguard.safe_write(brief_dir / "index.json", index_str, mode=0o600, root=runs_root)

    context_obj = build_context_json(ctx, priv_map=priv_map)
    context_str = scrub_private_text(json.dumps(context_obj, indent=2), priv_map) + "\n"
    fsguard.safe_write(brief_dir / "context.json", context_str, mode=0o600, root=runs_root)

    return index_obj


def _is_known_private_proposal(
    ctx: Ctx,
    name: str,
    repo: str | None,
    scopes: Sequence[str],
) -> bool:
    """Return True if `name`/`repo` matches `private_scopes` or a private entity/project."""
    for candidate in (name, repo or ""):
        clean = candidate.strip()
        if not clean:
            continue
        if classify.matches_private_scope(clean, scopes):
            return True

    ent_rows = ctx.db.execute(
        "SELECT entity_id, name, repo FROM entities WHERE visibility = 'private'"
    ).fetchall()
    name_low = name.strip().lower()
    repo_low = (repo or "").strip().lower()
    for er in ent_rows:
        e_name = str(er["name"] or "").strip().lower()
        e_repo = str(er["repo"] or "").strip().lower()
        if name_low and name_low in (e_name, e_repo):
            return True
        if repo_low and repo_low in (e_name, e_repo):
            return True

    proj_rows = ctx.db.execute(
        "SELECT project_key, display_path FROM projects WHERE visibility != 'public'"
    ).fetchall()
    for pr in proj_rows:
        pkey = str(pr["project_key"] or "").lower()
        dpath = str(pr["display_path"] or "").lower()
        tail = pkey.rstrip("/").rsplit("/", 1)[-1] if "/" in pkey else ""
        if name_low and name_low in (pkey, dpath, tail):
            return True
        if repo_low and (repo_low in pkey or repo_low == tail):
            return True

    return False


def _verify_primary_existence(
    ctx: Ctx,
    *,
    name: str,
    ecosystem: str,
    repo: str | None,
) -> tuple[bool, str | None, str | None, dict[str, Any]]:
    """Verify GATE-09 existence and `private == false` BEFORE any sub-endpoint or metric call (A5).

    Returns `(ok, rejected_reason, resolved_repo, initial_meta)`.
    """
    resolved_repo = repo.strip().lower() if repo else None
    meta: dict[str, Any] = {}

    # 1. Primary registry check for package ecosystems
    if ecosystem == "pypi":
        pkg_low = name.strip().lower()
        try:
            resp = ctx.http.fetch(f"https://pypi.org/pypi/{pkg_low}/json", ttl_hours=24)
            if resp.status != 200:
                return False, "not found", resolved_repo, meta
            obj = json.loads(resp.body.decode("utf-8"))
            if isinstance(obj, Mapping):
                info = obj.get("info") if isinstance(obj.get("info"), Mapping) else {}
                reg_repo = registries._extract_repo_slug(
                    info.get("project_urls") if isinstance(info, Mapping) else None
                ) or registries._extract_repo_slug(
                    info.get("home_page") if isinstance(info, Mapping) else None
                )
                if reg_repo:
                    meta["registry_repo"] = reg_repo
                    if not resolved_repo:
                        resolved_repo = reg_repo
        except (errors.RadarError, UnicodeDecodeError, json.JSONDecodeError):
            return False, "not found", resolved_repo, meta

    elif ecosystem == "npm":
        try:
            resp = ctx.http.fetch(f"https://registry.npmjs.org/{name.strip()}", ttl_hours=24)
            if resp.status != 200:
                return False, "not found", resolved_repo, meta
            obj = json.loads(resp.body.decode("utf-8"))
            if isinstance(obj, Mapping):
                reg_repo = registries._extract_repo_slug(obj.get("repository"))
                if reg_repo:
                    meta["registry_repo"] = reg_repo
                    if not resolved_repo:
                        resolved_repo = reg_repo
        except (errors.RadarError, UnicodeDecodeError, json.JSONDecodeError):
            return False, "not found", resolved_repo, meta

    elif ecosystem == "cargo":
        try:
            resp = ctx.http.fetch(f"https://crates.io/api/v1/crates/{name.strip()}", ttl_hours=24)
            if resp.status != 200:
                return False, "not found", resolved_repo, meta
            obj = json.loads(resp.body.decode("utf-8"))
            if isinstance(obj, Mapping):
                crate = obj.get("crate") if isinstance(obj.get("crate"), Mapping) else {}
                reg_repo = registries._extract_repo_slug(
                    crate.get("repository") if isinstance(crate, Mapping) else None
                )
                if reg_repo:
                    meta["registry_repo"] = reg_repo
                    if not resolved_repo:
                        resolved_repo = reg_repo
        except (errors.RadarError, UnicodeDecodeError, json.JSONDecodeError):
            return False, "not found", resolved_repo, meta

    elif ecosystem == "github" and not resolved_repo and "/" in name:
        resolved_repo = name.strip().lower()

    # 2. GitHub repo check (`private == false` required before any sub-endpoint or third-party call)
    if resolved_repo and "/" in resolved_repo:
        try:
            res = ctx.gh.api(f"repos/{resolved_repo}")
        except errors.RadarError:
            return False, "not found", resolved_repo, meta
        if res.returncode != 0 or not res.stdout.strip():
            return False, "not found", resolved_repo, meta
        try:
            r_obj = json.loads(res.stdout)
        except json.JSONDecodeError:
            return False, "not found", resolved_repo, meta
        if not isinstance(r_obj, Mapping):
            return False, "not found", resolved_repo, meta
        if r_obj.get("private") is True:
            # Record private visibility in DB and reject with identical "not found" reason (A5)
            gh_eid = f"gh:{resolved_repo}"
            ctx.db.execute(
                "INSERT INTO entities(entity_id, kind, name, repo, visibility, created_at) "
                "VALUES (?, 'repo', ?, ?, 'private', ?) "
                "ON CONFLICT(entity_id) DO UPDATE SET visibility = 'private'",
                (gh_eid, resolved_repo, resolved_repo, ctx.now_iso),
            )
            ctx.db.commit()
            return False, "not found", resolved_repo, meta
        if r_obj.get("private") is not False:
            return False, "not found", resolved_repo, meta
        meta["repo_verified_public"] = True
        meta["stars"] = int(r_obj.get("stargazers_count") or 0)
        meta["repo_created_at"] = r_obj.get("created_at")
    elif ecosystem in ("github", "mcp", "skill"):
        return False, "not found", resolved_repo, meta

    return True, None, resolved_repo, meta


def enrich(ctx: Ctx, run_id: str) -> dict[str, Any]:
    """Verify `shortlist.json` proposals (A5/A8 privacy gate + T10) and write `enriched.jsonl`."""
    ctx.reload_profile()
    run_dir = ctx.paths.run_dir(run_id)
    shortlist_path = run_dir / "shortlist.json"
    if not shortlist_path.is_file():
        raise errors.ValidationError(
            [{"field": "shortlist.json", "error": f"shortlist.json not found for run {run_id}"}]
        )

    try:
        raw_obj = json.loads(shortlist_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise errors.ValidationError(
            [{"field": "shortlist.json", "error": f"invalid JSON: {exc}"}]
        ) from exc

    val_errs = validate_shortlist(raw_obj)
    if val_errs:
        raise errors.ValidationError(val_errs)

    scopes = profile.load_private_scopes(ctx.db)
    priv_map = _private_scrub_map(ctx)

    brief_refs_raw = db.kv_get(ctx.db, f"brief_refs:{run_id}")
    brief_refs: dict[str, Any] = {}
    if brief_refs_raw:
        try:
            loaded_refs = json.loads(brief_refs_raw)
            if isinstance(loaded_refs, dict):
                brief_refs = loaded_refs
        except json.JSONDecodeError:
            brief_refs = {}

    suppressed_private = 0
    enriched_entries: list[dict[str, Any]] = []
    verified_count = 0
    rejected_count = 0

    for prop in raw_obj.get("proposals") or []:
        name = str(prop["name"]).strip()
        eco = str(prop["ecosystem"]).strip().lower()
        repo = str(prop["repo"]).strip().lower() if prop.get("repo") else None
        source_url = str(prop["source_url"]).strip()
        replaces = str(prop["replaces"]).strip() if prop.get("replaces") else None

        # Step 1: A5/A8 privacy pre-check — zero outbound calls if matching private scopes/entities
        if _is_known_private_proposal(ctx, name, repo, scopes):
            suppressed_private += 1
            continue

        # Step 2: Typosquat check (T10) before or alongside existence check
        if eco in ("pypi", "npm", "cargo"):
            typo_match = topnames.find_typosquat_match(ctx, name, ecosystem=eco)
            if typo_match is not None:
                rejected_count += 1
                enriched_entries.append(
                    {
                        "name": scrub_private_text(name, priv_map),
                        "ecosystem": eco,
                        "visibility": "public",
                        "rejected_reason": f"typosquat of {typo_match}",
                    }
                )
                continue

        # Step 3: GATE-09 existence + `private == false` check BEFORE sub-endpoints/metrics
        exists_ok, reject_reason, resolved_repo, init_meta = _verify_primary_existence(
            ctx,
            name=name,
            ecosystem=eco,
            repo=repo,
        )
        if not exists_ok:
            rejected_count += 1
            # Refresh priv_map in case a private repo was just discovered
            priv_map = _private_scrub_map(ctx)
            enriched_entries.append(
                {
                    "name": scrub_private_text(name, priv_map),
                    "ecosystem": eco,
                    "visibility": "public",
                    "rejected_reason": reject_reason or "not found",
                }
            )
            continue

        # Build canonical entity_id
        if eco in ("pypi", "npm", "cargo"):
            eid = f"pkg:{eco}/{name.lower() if eco == 'pypi' else name}"
        elif eco == "brew":
            eid = f"brew:formula/{name.lower()}"
        elif eco == "github":
            eid = f"gh:{resolved_repo or name.lower()}"
        elif eco == "mcp":
            eid = f"mcp:{name}"
        else:
            eid = f"skill:{resolved_repo or name}"

        eid, kind, _ = items.ensure_entity(ctx, eid, classify_visibility=False)
        ctx.db.execute(
            "UPDATE entities SET visibility = 'public', repo = COALESCE(?, repo) "
            "WHERE entity_id = ?",
            (resolved_repo, eid),
        )
        ctx.db.commit()

        item_id, _ = items.ensure_item(
            ctx,
            eid,
            in_use=False,
            state="candidate",
        )

        # Fetch full metrics now that public existence is confirmed
        m_data = registries.fetch_metrics(ctx, eid)
        m_data.update({k: v for k, v in init_meta.items() if k not in m_data})
        if eco in ("pypi", "npm", "cargo"):
            reg_repo_val = str(m_data.get("registry_repo") or "").strip().lower()
            expected_repo = (repo or resolved_repo or "").strip().lower()
            manifest_name_val = str(m_data.get("manifest_name") or "").strip().lower()
            norm_manifest = (
                manifest_name_val.replace("_", "-") if eco == "pypi" else manifest_name_val
            )
            norm_pkg = name.lower().replace("_", "-") if eco == "pypi" else name.lower()
            bidir_ok = (
                bool(reg_repo_val)
                and (not expected_repo or reg_repo_val == expected_repo)
                and (not norm_manifest or norm_manifest == norm_pkg)
                and m_data.get("bidirectional_link") is not False
            )
        else:
            bidir_ok = bool(
                not repo
                or not m_data.get("registry_repo")
                or str(m_data.get("registry_repo")).lower() == repo.lower()
            )
        m_data["bidirectional_verified"] = bidir_ok
        m_data["bidirectional_link"] = bidir_ok
        db.kv_set(
            ctx.db,
            f"gate_inputs:{eid}",
            json.dumps(m_data, separators=(",", ":"), sort_keys=True),
        )
        prescored = evaluate.prescore(ctx, item_id, gate_inputs=m_data)

        section = "contenders" if replaces else "horizon"
        brief_refs[item_id] = {
            "ref": item_id,
            "item_id": item_id,
            "entity_id": eid,
            "repo": resolved_repo,
            "evidence_urls": [source_url] if source_url else [],
            "section": section,
            "enriched": True,
        }
        db.kv_set(
            ctx.db,
            f"enrich_verified:{run_id}:{item_id}",
            json.dumps(
                {
                    "item_id": item_id,
                    "entity_id": eid,
                    "repo": resolved_repo,
                    "registry_repo": m_data.get("registry_repo"),
                    "replaces": replaces,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
        )

        verified_count += 1
        enriched_entries.append(
            _build_common_item_object(
                ref=item_id,
                item_id=item_id,
                entity_id=eid,
                kind=kind,
                visibility="public",
                title=f"{eid} (enriched)",
                url=source_url,
                source_id="enrich",
                published_at=ctx.now_iso,
                confidence="confirmed",
                used_in=[],
                matched_tokens=[],
                metrics=prescored["metrics_summary"],
                gates=prescored["gates"],
                red_flags=prescored["red_flags"],
                reported=prescored["reported"],
                momentum=prescored["momentum"],
                quality_base=prescored["quality_base"],
                cap=prescored["cap"],
                evidence=[
                    {
                        "url": source_url,
                        "kind": "blog",
                        "untrusted_excerpt": str(prop.get("why") or ""),
                    }
                ],
                priv_map=priv_map,
            )
        )

    db.kv_set(
        ctx.db,
        f"brief_refs:{run_id}",
        json.dumps(brief_refs, separators=(",", ":"), sort_keys=True),
    )

    brief_dir = run_dir / "brief"
    runs_root = ctx.paths.run_root / "runs"
    lines = [
        scrub_private_text(json.dumps(entry, separators=(",", ":"), sort_keys=False), priv_map)
        for entry in enriched_entries
    ]
    body = ("\n".join(lines) + "\n") if lines else ""
    fsguard.safe_write(brief_dir / "enriched.jsonl", body, mode=0o600, root=runs_root)

    # Update index.json suppressed.private if index.json exists
    index_path = brief_dir / "index.json"
    if index_path.is_file() and suppressed_private > 0:
        try:
            idx_obj = json.loads(index_path.read_text(encoding="utf-8"))
            if isinstance(idx_obj, dict):
                sup = idx_obj.setdefault("suppressed", {})
                if isinstance(sup, dict):
                    sup["private"] = int(sup.get("private") or 0) + suppressed_private
                fsguard.safe_write(
                    index_path,
                    scrub_private_text(json.dumps(idx_obj, indent=2), priv_map) + "\n",
                    mode=0o600,
                    root=runs_root,
                )
        except json.JSONDecodeError:
            pass

    return {
        "ok": True,
        "run_id": run_id,
        "verified": verified_count,
        "rejected": rejected_count,
        "suppressed": {"private": suppressed_private},
    }
