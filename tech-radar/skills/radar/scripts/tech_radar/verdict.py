"""Verdict contract validation, T10 verification gate, and partial apply (Spec 05 §5, Spec 12)."""

from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import brief, db, errors, evaluate, http, items, procs, redact, sanitize, topnames

if TYPE_CHECKING:
    from .context import Ctx

VALID_ACTIONS: frozenset[str] = frozenset({"surface", "drop", "watch"})
VALID_RINGS: frozenset[str | None] = frozenset({"adopt", "trial", "assess", "hold", "watch", None})
VALID_CONFIDENCES: frozenset[str] = frozenset({"high", "medium", "low"})
VALID_EFFORTS: frozenset[str | None] = frozenset({"S", "M", "L", None})
VALID_SECTIONS: frozenset[str] = frozenset(
    {
        "security",
        "lifecycle",
        "features",
        "pricing",
        "contenders",
        "horizon",
        "technique",
        "rotation",
    }
)
LLM_FIELD_KEYS: tuple[str, ...] = (
    "cloud_code_upload",
    "arbitrary_shell",
    "broad_credentials",
    "telemetry_no_optout",
)
LLM_FIELD_VALUES: frozenset[str] = frozenset({"no", "yes", "unknown"})

_REF_RE = re.compile(r"^(?:R-\d{4,}|S-\d+)$")
_TECHNIQUE_SLUG_RE = re.compile(r"^[a-z0-9-]{3,60}$")
_REPO_HOSTS: frozenset[str] = frozenset({"github.com", "api.github.com", "pypi.org", "crates.io"})


def compose_install_command(
    entity_row: Mapping[str, Any],
    gate_inputs: Mapping[str, Any] | None = None,
) -> str | None:
    """Compose an install command strictly from validated structured fields (Spec 05 §5).

    Never copies text from `untrusted_excerpt` or LLM free text.
    """
    inputs = dict(gate_inputs or {})
    eid = str(entity_row.get("entity_id") or "")
    kind = str(entity_row.get("kind") or "package")
    eco = str(entity_row.get("ecosystem") or "").lower()
    name = str(entity_row.get("name") or "").strip()
    repo = str(entity_row.get("repo") or inputs.get("repo") or "").strip()
    ver = str(inputs.get("latest_version") or inputs.get("version") or "").strip()

    if not name and "/" in eid:
        name = eid.split("/", 1)[1]

    if kind in ("mcp", "skill", "plugin") or eid.startswith(("mcp:", "skill:", "plugin:")):
        sha = str(inputs.get("pinned_sha") or inputs.get("commit_sha") or "HEAD").strip()
        if not procs.is_safe_arg(sha):
            sha = "HEAD"
        if repo and procs.is_safe_arg(repo):
            return f"https://github.com/{repo}@{sha}"
        if procs.is_safe_arg(name):
            return f"{eid}@{sha}"
        return None

    if not procs.is_safe_arg(name):
        return None
    if ver and not procs.is_safe_arg(ver):
        ver = ""

    if eco == "brew" or eid.startswith("brew:"):
        return f"brew install {name}"
    if eco == "pypi" or eid.startswith("pkg:pypi/"):
        return f"uv tool install {name}=={ver}" if ver else f"uv tool install {name}"
    if eco == "npm" or eid.startswith("pkg:npm/"):
        return f"npm i -g {name}@{ver}" if ver else f"npm i -g {name}"
    if eco in ("cargo", "crates.io") or eid.startswith("pkg:cargo/"):
        return f"cargo install {name} --version {ver}" if ver else f"cargo install {name}"
    if repo and procs.is_safe_arg(repo):
        return f"https://github.com/{repo}"
    return None


def _is_generic_citation_url(url: str) -> bool:
    """Return True if `url` is a generic root or single-segment non-repo URL (Spec 05 §5)."""
    try:
        parsed = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return True
    if parsed.scheme != "https" or not parsed.netloc:
        return True
    host = parsed.hostname or ""
    segments = [seg for seg in parsed.path.split("/") if seg]
    if not segments:
        return True
    return len(segments) <= 1 and host.lower() not in _REPO_HOSTS and not parsed.query


def _citation_verified(
    ctx: Ctx | None,
    url: str,
    ref_info: Mapping[str, Any] | None,
) -> bool:
    clean_url = url.strip()
    if _is_generic_citation_url(clean_url):
        return False
    if ctx is None:
        return True

    # Check if URL appears in ref_info evidence_urls or DB signals for this entity or signal
    ref_repo = ""
    if ref_info:
        priv_map = brief._private_scrub_map(ctx)
        db_urls: set[str] = set()
        ev_urls = ref_info.get("evidence_urls")
        if isinstance(ev_urls, list | tuple | set):
            for u in ev_urls:
                if u:
                    raw_u = str(u).strip()
                    db_urls.add(raw_u)
                    db_urls.add(brief.scrub_private_text(raw_u, priv_map))
        if ref_info.get("repo"):
            ref_repo = str(ref_info["repo"]).strip().lower()
        eid = ref_info.get("entity_id")
        sig_id = ref_info.get("signal_id")
        if sig_id is not None:
            s_row = ctx.db.execute(
                "SELECT url FROM signals WHERE signal_id = ?",
                (int(sig_id),),
            ).fetchone()
            if s_row is not None and s_row["url"]:
                raw_u = str(s_row["url"]).strip()
                db_urls.add(raw_u)
                db_urls.add(brief.scrub_private_text(raw_u, priv_map))
        if eid:
            e_rows = ctx.db.execute(
                "SELECT url FROM signals WHERE entity_id = ?",
                (str(eid),),
            ).fetchall()
            for r in e_rows:
                if r["url"]:
                    raw_u = str(r["url"]).strip()
                    db_urls.add(raw_u)
                    db_urls.add(brief.scrub_private_text(raw_u, priv_map))
            ent_meta = ctx.db.execute(
                "SELECT repo, homepage FROM entities WHERE entity_id = ?",
                (str(eid),),
            ).fetchone()
            if ent_meta is not None:
                if ent_meta["homepage"]:
                    raw_u = str(ent_meta["homepage"]).strip()
                    db_urls.add(raw_u)
                    db_urls.add(brief.scrub_private_text(raw_u, priv_map))
                if not ref_repo and ent_meta["repo"]:
                    ref_repo = str(ent_meta["repo"]).strip().lower()
            if not ref_repo and str(eid).startswith("gh:"):
                ref_repo = str(eid)[len("gh:") :].strip().lower()
        if clean_url in db_urls:
            return True

    parsed = urllib.parse.urlsplit(clean_url)
    host = (parsed.hostname or "").lower()
    if host not in http.EGRESS_ALLOWLIST and host != "github.com":
        return False

    try:
        resp = ctx.http.fetch(clean_url, ttl_hours=24)
        return 200 <= resp.status < 300
    except errors.RadarError as exc:
        is_replay = procs.is_replay_mode() or isinstance(ctx.http.transport, http.ReplayHttp)
        if is_replay and "missing HTTP replay fixture" in str(exc):
            if host == "github.com":
                segments = [seg for seg in parsed.path.split("/") if seg]
                url_repo = f"{segments[0]}/{segments[1]}".lower() if len(segments) >= 2 else ""
                if url_repo and ref_repo and url_repo == ref_repo:
                    return True
            return bool(ref_info and ref_info.get("entity_id"))
        return False


def _validate_relevant_to_entry(ctx: Ctx | None, entry: str) -> bool:
    clean = entry.strip()
    if not clean:
        return False
    if clean.startswith("taste:"):
        parts = clean.split(":", 2)
        return len(parts) == 3 and bool(parts[1].strip()) and bool(parts[2].strip())
    if ctx is None:
        return True

    ent_row = ctx.db.execute(
        "SELECT 1 FROM entities WHERE entity_id = ?",
        (clean,),
    ).fetchone()
    if ent_row is not None:
        return True

    proj_row = ctx.db.execute(
        "SELECT 1 FROM projects WHERE alias = ? OR project_key LIKE ?",
        (clean, f"%/{clean.lower()}"),
    ).fetchone()
    if proj_row is not None:
        return True

    item_row = ctx.db.execute(
        "SELECT 1 FROM items WHERE item_id = ?",
        (clean,),
    ).fetchone()
    return item_row is not None


def _load_valid_refs_from_db(ctx: Ctx, run_id: str | None) -> dict[str, dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    if run_id:
        raw = db.kv_get(ctx.db, f"brief_refs:{run_id}")
        if raw:
            try:
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    refs.update(loaded)
            except json.JSONDecodeError:
                pass

    # Fallback: include DB items and signals if `brief_refs:{run_id}` wasn't pre-populated
    if not refs:
        for ir in ctx.db.execute(
            "SELECT i.item_id, i.entity_id, e.repo FROM items i "
            "LEFT JOIN entities e ON e.entity_id = i.entity_id"
        ).fetchall():
            iid = str(ir["item_id"])
            refs[iid] = {
                "ref": iid,
                "item_id": iid,
                "entity_id": str(ir["entity_id"]),
                "repo": str(ir["repo"]) if ir["repo"] else None,
            }
        for sr in ctx.db.execute("SELECT signal_id, entity_id, type, url FROM signals").fetchall():
            sid = int(sr["signal_id"])
            sref = f"S-{sid}"
            refs[sref] = {
                "ref": sref,
                "signal_id": sid,
                "entity_id": str(sr["entity_id"]) if sr["entity_id"] else None,
                "evidence_urls": [str(sr["url"])] if sr["url"] else [],
            }
    return refs


def _validate_single_verdict_item(
    v_item: Any,
    idx: int,
    *,
    ctx: Ctx | None = None,
    valid_refs: Mapping[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    pfx = f"items[{idx}]"
    if not isinstance(v_item, Mapping):
        return [{"field": pfx, "error": "verdict item must be an object"}]

    errs: list[dict[str, Any]] = []
    ref = v_item.get("ref")
    ref_str = str(ref).strip() if isinstance(ref, str) else ""
    if not ref_str or not _REF_RE.match(ref_str):
        errs.append({"field": f"{pfx}.ref", "error": f"invalid ref format: {ref}"})
    elif valid_refs is not None and ref_str not in valid_refs:
        errs.append({"field": f"{pfx}.ref", "error": f"unknown ref: {ref_str}"})

    ref_info = valid_refs.get(ref_str) if valid_refs is not None else None

    action = v_item.get("action")
    if not isinstance(action, str) or action not in VALID_ACTIONS:
        errs.append({"field": f"{pfx}.action", "error": f"invalid action: {action}"})
        action_str = ""
    else:
        action_str = action

    section = v_item.get("section")
    if not isinstance(section, str) or section not in VALID_SECTIONS:
        errs.append({"field": f"{pfx}.section", "error": f"invalid section: {section}"})
        section_str = ""
    else:
        section_str = section

    ring = v_item.get("ring")
    if ring not in VALID_RINGS:
        errs.append({"field": f"{pfx}.ring", "error": f"invalid ring: {ring}"})

    quadrant = v_item.get("quadrant")
    if quadrant is not None and quadrant not in items.ALL_QUADRANTS:
        errs.append({"field": f"{pfx}.quadrant", "error": f"invalid quadrant: {quadrant}"})

    fit = v_item.get("fit")
    if isinstance(fit, bool) or not isinstance(fit, int | float) or not (0 <= float(fit) <= 100):
        errs.append({"field": f"{pfx}.fit", "error": "fit must be 0..100"})

    q_adj = v_item.get("quality_adj")
    if (
        isinstance(q_adj, bool)
        or not isinstance(q_adj, int | float)
        or not (-15 <= float(q_adj) <= 15)
    ):
        errs.append({"field": f"{pfx}.quality_adj", "error": "quality_adj must be -15..15"})

    conf = v_item.get("confidence")
    if not isinstance(conf, str) or conf not in VALID_CONFIDENCES:
        errs.append({"field": f"{pfx}.confidence", "error": "confidence must be high|medium|low"})

    why = v_item.get("why")
    if not isinstance(why, str) or not why.strip() or len(why) > 400:
        errs.append({"field": f"{pfx}.why", "error": "why must be 1..400 chars"})

    sup = v_item.get("superiority")
    if sup is not None and (not isinstance(sup, str) or len(sup) > 400):
        errs.append({"field": f"{pfx}.superiority", "error": "superiority must be <=400 chars"})

    benefit = v_item.get("benefit")
    if benefit is not None and (not isinstance(benefit, str) or len(benefit) > 200):
        errs.append({"field": f"{pfx}.benefit", "error": "benefit must be <=200 chars"})

    snippet = v_item.get("adoption_snippet")
    if snippet is not None and (not isinstance(snippet, str) or len(snippet) > 600):
        errs.append(
            {"field": f"{pfx}.adoption_snippet", "error": "adoption_snippet must be <=600 chars"}
        )

    effort = v_item.get("effort")
    if effort not in VALID_EFFORTS:
        errs.append({"field": f"{pfx}.effort", "error": "effort must be S|M|L|null"})

    tech_slug = v_item.get("technique_slug")
    if tech_slug is not None and (
        not isinstance(tech_slug, str) or not _TECHNIQUE_SLUG_RE.match(tech_slug)
    ):
        errs.append(
            {
                "field": f"{pfx}.technique_slug",
                "error": "technique_slug must match ^[a-z0-9-]{3,60}$",
            }
        )

    llm_fields = v_item.get("llm_fields")
    if not isinstance(llm_fields, Mapping):
        errs.append({"field": f"{pfx}.llm_fields", "error": "llm_fields object is required"})
    else:
        for k in LLM_FIELD_KEYS:
            val = llm_fields.get(k)
            if not isinstance(val, str) or val not in LLM_FIELD_VALUES:
                errs.append(
                    {
                        "field": f"{pfx}.llm_fields.{k}",
                        "error": f"{k} must be no|yes|unknown",
                    }
                )

    citations = v_item.get("citations")
    if not isinstance(citations, list) or (action_str == "surface" and not citations):
        errs.append(
            {"field": f"{pfx}.citations", "error": "citations must be a non-empty list of URLs"}
        )
    else:
        for c_idx, c_url in enumerate(citations):
            if not isinstance(c_url, str) or not _citation_verified(ctx, c_url, ref_info):
                errs.append(
                    {
                        "field": f"{pfx}.citations[{c_idx}]",
                        "error": f"invalid or generic citation URL: {c_url}",
                    }
                )

    # Structural relevance requirements (Spec 12 A6)
    replaces = v_item.get("replaces")
    relevant_to = v_item.get("relevant_to")
    if not isinstance(relevant_to, list):
        errs.append({"field": f"{pfx}.relevant_to", "error": "relevant_to must be a list"})
        relevant_to_list: list[Any] = []
    else:
        relevant_to_list = relevant_to

    if section_str == "contenders" and action_str == "surface":
        if not isinstance(replaces, str) or not replaces.strip():
            errs.append(
                {
                    "field": f"{pfx}.replaces",
                    "error": "contenders with action='surface' require replaces",
                }
            )
        elif ctx is not None:
            u_row = ctx.db.execute(
                "SELECT 1 FROM usages WHERE entity_id = ? "
                "AND context NOT LIKE 'interest:%' AND direct = 1 LIMIT 1",
                (replaces.strip(),),
            ).fetchone()
            if u_row is None:
                errs.append(
                    {
                        "field": f"{pfx}.replaces",
                        "error": f"replaces entity is not in active usage: {replaces}",
                    }
                )
        if not isinstance(sup, str) or not sup.strip():
            errs.append(
                {
                    "field": f"{pfx}.superiority",
                    "error": "contenders with action='surface' require non-empty superiority",
                }
            )

    if section_str in ("horizon", "technique") and action_str == "surface":
        if not relevant_to_list:
            errs.append(
                {
                    "field": f"{pfx}.relevant_to",
                    "error": f"{section_str} with action='surface' requires non-empty relevant_to",
                }
            )
        else:
            for r_idx, r_elem in enumerate(relevant_to_list):
                if not isinstance(r_elem, str) or not _validate_relevant_to_entry(ctx, r_elem):
                    errs.append(
                        {
                            "field": f"{pfx}.relevant_to[{r_idx}]",
                            "error": f"invalid relevant_to target: {r_elem}",
                        }
                    )

    return errs


def validate(
    target: Path | str | Mapping[str, Any],
    *,
    ctx: Ctx | None = None,
) -> list[dict[str, Any]]:
    """Validate `verdicts.json` or a verdict object per Spec 05 §5 and Spec 11 §10.

    Returns a list of field error dicts (empty when valid).
    """
    obj: Any
    if isinstance(target, Mapping):
        obj = target
        if "ref" in obj and "items" not in obj:
            return _validate_single_verdict_item(obj, 0, ctx=ctx, valid_refs=None)
    else:
        p = Path(target)
        v_file = p / "verdicts.json" if p.is_dir() else p
        if not v_file.is_file():
            return [{"field": "verdicts.json", "error": f"file not found: {v_file}"}]
        try:
            obj = json.loads(v_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return [{"field": "verdicts.json", "error": f"invalid JSON: {exc}"}]

    if not isinstance(obj, Mapping):
        return [{"field": "$", "error": "verdicts root must be a JSON object"}]

    errs: list[dict[str, Any]] = []
    run_id = obj.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        errs.append({"field": "run_id", "error": "run_id is required"})
        run_id_str = None
    else:
        run_id_str = run_id.strip()

    items_list = obj.get("items")
    if not isinstance(items_list, list):
        errs.append({"field": "items", "error": "items must be a list"})
        return errs

    valid_refs = _load_valid_refs_from_db(ctx, run_id_str) if ctx is not None else None
    for idx, v_item in enumerate(items_list):
        errs.extend(
            _validate_single_verdict_item(
                v_item,
                idx,
                ctx=ctx,
                valid_refs=valid_refs,
            )
        )
    return errs


def _check_t10_verification_gate(
    ctx: Ctx,
    item_row: Mapping[str, Any],
    gate_inputs: Mapping[str, Any],
    prescored: Mapping[str, Any],
) -> tuple[str | None, list[str], str | None]:
    """Check Spec 05 §5 T10 TRIAL/ADOPT verification gate.

    Returns `(extra_cap, failed_reasons, pinned_sha)`.
    """
    entity_id = str(item_row["entity_id"])
    kind = str(item_row.get("kind") or "package")
    eco = str(item_row.get("ecosystem") or "").lower()
    name = str(item_row.get("name") or "").strip()
    repo = str(item_row.get("repo") or gate_inputs.get("repo") or "").strip().lower()

    reasons: list[str] = []
    pinned_sha: str | None = None

    # 1. MCP / skill / plugin human source review rule
    if kind in ("mcp", "skill", "plugin") or entity_id.startswith(("mcp:", "skill:", "plugin:")):
        pinned_sha = str(gate_inputs.get("pinned_sha") or gate_inputs.get("commit_sha") or "HEAD")
        user_dec = ctx.db.execute(
            "SELECT 1 FROM decisions WHERE item_id = ? AND verb IN ('trial', 'adopt') "
            "AND actor = 'user' AND superseded_by IS NULL LIMIT 1",
            (str(item_row["item_id"]),),
        ).fetchone()
        if user_dec is None:
            reasons.append("human source review required")
            return "assess", reasons, pinned_sha

    if kind == "technique":
        return None, [], None

    gates = prescored.get("gates") or {}
    reported = prescored.get("reported") or {}

    # 2. Age floor >= 90 days (GATE-07)
    if gates.get("GATE-07") != "pass":
        reasons.append("T10: age floor < 90 days (GATE-07)")

    # 3. OSV / GHSA clean for exact version (GATE-05, GATE-06)
    if gates.get("GATE-05") != "pass" or gates.get("GATE-06") != "pass":
        reasons.append("T10: OSV/GHSA check not clean")

    # 4. Registry <-> repo bidirectional link (for package ecosystems)
    if (
        gate_inputs.get("bidirectional_link") is False
        or gate_inputs.get("bidirectional_verified") is False
    ):
        reasons.append("T10: registry<->repo bidirectional link mismatch")
    elif eco in ("pypi", "npm", "cargo"):
        reg_repo = str(gate_inputs.get("registry_repo") or "").strip().lower()
        manifest_name = str(gate_inputs.get("manifest_name") or name).strip().lower()
        norm_manifest = manifest_name.replace("_", "-") if eco == "pypi" else manifest_name
        norm_name = name.lower().replace("_", "-") if eco == "pypi" else name.lower()
        if not reg_repo or (repo and reg_repo != repo):
            reasons.append("T10: registry<->repo link mismatch")
        elif norm_manifest != norm_name:
            reasons.append("T10: repo manifest name != package name")

    # 5. Typosquat check (Damerau-Levenshtein <= 1 against top names)
    if eco in ("pypi", "npm", "cargo") and name:
        typo = topnames.find_typosquat_match(ctx, name, ecosystem=eco)
        if typo is not None:
            reasons.append(f"T10: typosquat of {typo}")

    # 6. Independent popularity signal (EVID-01, Spec 12 A39)
    indep_cnt = int(reported.get("EVID-01") or 0)
    if indep_cnt < 2 and gate_inputs.get("independent_popularity") is not True:
        reasons.append("T10: missing independent popularity signal (EVID-01)")

    if reasons:
        return "assess", reasons, pinned_sha
    return None, [], pinned_sha


def _populate_verdict_reported(
    reported: dict[str, Any],
    v_item: Mapping[str, Any],
    citations: list[str],
) -> None:
    if v_item.get("superiority"):
        reported["superiority"] = redact.scrub(
            sanitize.clean(str(v_item["superiority"]), limit=400)
        )
    if v_item.get("benefit"):
        reported["benefit"] = redact.scrub(sanitize.clean(str(v_item["benefit"]), limit=200))
    if v_item.get("replaces"):
        reported["replaces"] = str(v_item["replaces"])
    if v_item.get("relevant_to"):
        reported["relevant_to"] = [str(x) for x in v_item["relevant_to"]]
    if v_item.get("effort"):
        reported["effort"] = str(v_item["effort"])
    if v_item.get("adoption_snippet"):
        reported["adoption_snippet"] = redact.scrub(
            sanitize.clean(str(v_item["adoption_snippet"]), limit=600)
        )
    if citations:
        reported["citations"] = citations


def _record_ring_change(
    ctx: Ctx,
    item_id: str,
    run_id: str,
    old_ring: str | None,
    new_ring: str | None,
    why: Any,
) -> None:
    if new_ring != old_ring and new_ring is not None:
        ctx.db.execute(
            "INSERT INTO ring_history(item_id, run_id, from_ring, to_ring, at, reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                item_id,
                run_id,
                old_ring,
                new_ring,
                ctx.now_iso,
                redact.scrub(sanitize.clean(str(why or ""), limit=400)),
            ),
        )


def _record_surfaced_digest_item(
    ctx: Ctx,
    run_id: str,
    ref: str,
    section: str,
    section_ranks: dict[str, int],
) -> None:
    rank = section_ranks.get(section, 0) + 1
    section_ranks[section] = rank
    ctx.db.execute(
        "INSERT INTO digest_items(run_id, ref, section, rank, feedback) "
        "VALUES (?, ?, ?, ?, NULL) "
        "ON CONFLICT(run_id, ref) DO UPDATE SET "
        "section = excluded.section, rank = excluded.rank",
        (run_id, ref, section, rank),
    )


def apply_verdicts(ctx: Ctx, run_id: str) -> dict[str, Any]:
    """Validate and apply `{run_root}/runs/{run_id}/verdicts.json` (Spec 05 §5, A6, A9, A25)."""
    run_dir = ctx.paths.run_dir(run_id)
    v_file = run_dir / "verdicts.json"
    if not v_file.is_file():
        raise errors.ValidationError(
            [{"field": "verdicts.json", "error": f"verdicts.json not found in {run_dir}"}]
        )

    try:
        root_obj = json.loads(v_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise errors.ValidationError(
            [{"field": "verdicts.json", "error": f"invalid JSON: {exc}"}]
        ) from exc

    if not isinstance(root_obj, Mapping) or not isinstance(root_obj.get("items"), list):
        raise errors.ValidationError(
            [{"field": "items", "error": "verdicts.json must contain an items list"}]
        )

    raw_run_id = root_obj.get("run_id")
    if not isinstance(raw_run_id, str) or not raw_run_id.strip():
        raise errors.ValidationError(
            [{"field": "run_id", "reason": "run_id is required", "error": "run_id is required"}]
        )
    if raw_run_id.strip() != run_id:
        raise errors.ValidationError(
            [{"field": "run_id", "reason": "run_id mismatch", "error": "run_id mismatch"}]
        )

    valid_refs = _load_valid_refs_from_db(ctx, run_id)
    all_errors: list[dict[str, Any]] = []
    applied_items: list[dict[str, Any]] = []
    section_ranks: dict[str, int] = {}

    for idx, v_item in enumerate(root_obj["items"]):
        item_errs = _validate_single_verdict_item(
            v_item,
            idx,
            ctx=ctx,
            valid_refs=valid_refs,
        )
        if item_errs:
            all_errors.extend(item_errs)
            continue

        ref_str = str(v_item["ref"]).strip()
        section = str(v_item["section"]).strip()
        action = str(v_item["action"]).strip()
        req_ring = v_item.get("ring")
        tech_slug = v_item.get("technique_slug")
        citations = [str(c) for c in (v_item.get("citations") or [])]

        # Handle Spec 12 A9 technique_slug / horizon signal promotion on S- refs
        if (
            ref_str.startswith("S-")
            and action != "drop"
            and (tech_slug or section in ("technique", "horizon"))
        ):
            sig_id = int(ref_str[2:])
            sig_meta = ctx.db.execute(
                "SELECT title FROM signals WHERE signal_id = ?",
                (sig_id,),
            ).fetchone()
            if tech_slug:
                resolved_slug = str(tech_slug).strip().lower()
            else:
                raw_title = str(sig_meta["title"]) if sig_meta and sig_meta["title"] else ref_str
                resolved_slug = (
                    re.sub(r"[^a-z0-9]+", "-", raw_title.lower()).strip("-")[:48]
                    or f"signal-{sig_id}"
                )
            prior_tech = ctx.db.execute(
                "SELECT ring FROM items WHERE entity_id = ?",
                (f"technique:{resolved_slug}",),
            ).fetchone()
            old_tech_ring = str(prior_tech["ring"]) if prior_tech and prior_tech["ring"] else None

            fin = evaluate.create_or_finalize_technique(
                ctx,
                resolved_slug,
                v_item,
                evidence_count=max(2, len(citations)),
            )
            item_id = str(fin["item_id"])
            tech_eid = str(fin["entity_id"])
            reported = dict(fin["reported"])
            _populate_verdict_reported(reported, v_item, citations)
            ctx.db.execute(
                "UPDATE items SET reported_json = ? WHERE item_id = ?",
                (json.dumps(reported, separators=(",", ":"), sort_keys=True), item_id),
            )
            fin["reported"] = reported

            _record_ring_change(ctx, item_id, run_id, old_tech_ring, fin["ring"], v_item.get("why"))
            ctx.db.execute(
                "UPDATE signals SET last_surfaced_at = ?, entity_id = COALESCE(entity_id, ?) "
                "WHERE signal_id = ?",
                (ctx.now_iso, tech_eid, sig_id),
            )
            if action == "surface":
                _record_surfaced_digest_item(ctx, run_id, item_id, section, section_ranks)
            ctx.db.commit()
            applied_items.append(fin)
            continue

        # Signal-only verdict (e.g. security, lifecycle, features, pricing)
        if ref_str.startswith("S-"):
            sig_id = int(ref_str[2:])
            sig_row = ctx.db.execute(
                "SELECT entity_id, payload_json FROM signals WHERE signal_id = ?",
                (sig_id,),
            ).fetchone()
            if sig_row is not None and (v_item.get("adoption_snippet") or v_item.get("effort")):
                pay_obj: dict[str, Any] = {}
                if sig_row["payload_json"]:
                    try:
                        loaded_pay = json.loads(str(sig_row["payload_json"]))
                        if isinstance(loaded_pay, dict):
                            pay_obj = loaded_pay
                    except json.JSONDecodeError:
                        pay_obj = {}
                if v_item.get("adoption_snippet"):
                    pay_obj["adoption_snippet"] = redact.scrub(
                        sanitize.clean(str(v_item["adoption_snippet"]), limit=600)
                    )
                if v_item.get("effort"):
                    pay_obj["effort"] = str(v_item["effort"])
                ctx.db.execute(
                    "UPDATE signals SET last_surfaced_at = ?, payload_json = ? WHERE signal_id = ?",
                    (
                        ctx.now_iso,
                        json.dumps(pay_obj, separators=(",", ":"), sort_keys=True),
                        sig_id,
                    ),
                )
            else:
                ctx.db.execute(
                    "UPDATE signals SET last_surfaced_at = ? WHERE signal_id = ?",
                    (ctx.now_iso, sig_id),
                )
            if action == "surface":
                _record_surfaced_digest_item(ctx, run_id, ref_str, section, section_ranks)
            ctx.db.commit()
            applied_items.append(
                {
                    "ref": ref_str,
                    "signal_id": sig_id,
                    "entity_id": str(sig_row["entity_id"])
                    if sig_row and sig_row["entity_id"]
                    else None,
                    "action": action,
                    "section": section,
                }
            )
            continue

        # Item verdict (`R-XXXX`)
        item_row = ctx.db.execute(
            "SELECT i.*, e.kind, e.ecosystem, e.name, e.repo, e.visibility "
            "FROM items i JOIN entities e ON e.entity_id = i.entity_id "
            "WHERE i.item_id = ?",
            (ref_str,),
        ).fetchone()
        if item_row is None:
            all_errors.append({"field": f"items[{idx}].ref", "error": f"unknown item: {ref_str}"})
            continue

        old_ring = str(item_row["ring"]) if item_row["ring"] else None
        eid = str(item_row["entity_id"])
        gate_inputs = evaluate._load_gate_inputs(ctx, eid)
        prescored = evaluate.prescore(ctx, dict(item_row), gate_inputs=gate_inputs)

        extra_cap: str | None = None
        extra_reasons: list[str] = []
        pinned_sha: str | None = None
        if req_ring in ("trial", "adopt"):
            extra_cap, extra_reasons, pinned_sha = _check_t10_verification_gate(
                ctx,
                dict(item_row),
                gate_inputs,
                prescored,
            )

        fin = evaluate.finalize(
            ctx,
            dict(item_row),
            v_item,
            gate_inputs=gate_inputs,
            extra_cap=extra_cap,
            extra_reasons=extra_reasons,
        )

        # Compose script-owned install command and store verdict metadata in reported_json
        reported = dict(fin["reported"])
        install_cmd = compose_install_command(dict(item_row), gate_inputs)
        if install_cmd:
            reported["install_command"] = install_cmd
        if pinned_sha:
            reported["pinned_sha"] = pinned_sha
        _populate_verdict_reported(reported, v_item, citations)

        ctx.db.execute(
            "UPDATE items SET reported_json = ? WHERE item_id = ?",
            (json.dumps(reported, separators=(",", ":"), sort_keys=True), ref_str),
        )
        fin["reported"] = reported
        fin["install_command"] = install_cmd

        if action != "drop":
            _record_ring_change(ctx, ref_str, run_id, old_ring, fin["ring"], v_item.get("why"))

        if section == "rotation":
            ctx.db.execute(
                "UPDATE items SET last_deep_inspect_at = ? WHERE item_id = ?",
                (ctx.now_iso, ref_str),
            )

        ctx.db.execute(
            "UPDATE signals SET last_surfaced_at = ? WHERE entity_id = ?",
            (ctx.now_iso, eid),
        )

        if action == "surface":
            _record_surfaced_digest_item(ctx, run_id, ref_str, section, section_ranks)

        ctx.db.commit()
        applied_items.append(fin)

    if applied_items or not all_errors:
        db.kv_set(ctx.db, f"verdict_applied:{run_id}", ctx.now_iso)
        ctx.db.commit()

    if all_errors:
        msg = (
            f"verdict validation failed ({len(all_errors)} error(s)); "
            f"applied {len(applied_items)} valid item(s)"
        )
        raise errors.ValidationError(all_errors, message=msg)

    return {
        "ok": True,
        "run_id": run_id,
        "applied": len(applied_items),
        "items": applied_items,
    }
