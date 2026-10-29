"""First-run bootstrap (`init`, `apply`, `lockdown`) and answers validation (Spec 08, A43)."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import (
    appsync,
    brief,
    decisions,
    errors,
    fsguard,
    inventory,
    items,
    launchd,
    redact,
    sanitize,
)
from .inventory import seeds
from .profile import Profile

if TYPE_CHECKING:
    from .context import Ctx

_PKG_DIR = Path(__file__).resolve().parent
_SETTINGS_TEMPLATE_PATH = _PKG_DIR / "templates" / "run-project-settings.json"

# Recorded from spike-results.md ## T3 enforcement (Spec 12 A34):
# ENFORCEMENT_HONOURED: no -> False
# ALLOWED_MCP_WORKS: no (fetchaller server connection error during probe; MCP tools kept per A25)
# SKILL_NEEDS_ALLOW_RULE: yes -> Skill(tech-radar:radar) and ToolSearch kept in template
ENFORCEMENT_HONOURED: bool = False
ALLOWED_MCP_WORKS: bool = False
SKILL_NEEDS_ALLOW_RULE: bool = True

# Spec 08 § Bootstrap constants (checked WATCH-* rows)
DEFAULT_CATEGORIES: tuple[str, ...] = (
    "context/token reducers",
    "codebase packers/context builders",
    "semantic code navigation for agents",
    "compaction/caching/context techniques",
    "agent memory systems",
    "parallel session/multi-agent orchestrators",
    "coding agent harnesses",
    "skills and plugins",
    "MCP servers",
    "hooks/guardrail/permission tooling",
    "agent sandboxing/isolation",
    "package-install gates for agents",
    "browser automation for agents",
    "evals/prompt testing",
    "AI code-review bots",
    "model releases and pricing",
    "Python tooling",
    "JS/TS runtimes and tooling",
    "modern CLI replacements",
    "shell/prompt/terminal/fish plugins",
    "git tooling",
    "task runners/env managers",
    "testing frameworks",
    "security tooling",
    "containers/dev environments/nix",
    "local data tooling",
    "CI/CD tooling",
    "docs/diagram tooling",
)

# Spec 08 § Bootstrap constants (unchecked WATCH-* rows)
DEFAULT_ANTI_INTERESTS: tuple[str, ...] = (
    "agent observability and cost tracking",
    "local inference",
    "statusline/TUI/UX add-ons",
    "Rust tooling",
)

DEFAULT_SUBSCRIPTIONS: tuple[str, ...] = ("claude-max",)

ALLOWED_ANSWER_KEYS: frozenset[str] = frozenset(
    {
        "languages",
        "categories",
        "anti_interests",
        "preferences",
        "taste_makers",
        "watchlist",
        "initial_rings",
        "subscriptions",
    }
)
INITIAL_RING_VALUES: frozenset[str] = frozenset({"adopt", "trial", "assess", "hold"})


def _dedupe_preserve(values: Sequence[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        cleaned = redact.scrub(sanitize.clean(str(raw), limit=200)).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return out


def render_taste_profile_markdown(
    *,
    categories: Sequence[str],
    anti_interests: Sequence[str],
    preferences: Sequence[str],
    taste_makers: Sequence[str],
    languages: Sequence[str],
    subscriptions: Sequence[str],
    watchlist: Sequence[str] = (),
) -> str:
    """Render `TASTE_PROFILE.md` with all Spec 04 section headings."""
    sections: list[tuple[str, list[str]]] = [
        ("Categories", _dedupe_preserve(categories)),
        ("Anti-interests", _dedupe_preserve(anti_interests)),
        ("Preferences", _dedupe_preserve(preferences)),
        ("Taste-makers", _dedupe_preserve(taste_makers)),
        ("Languages", _dedupe_preserve(languages)),
        ("Subscriptions", _dedupe_preserve(subscriptions)),
        ("Watchlist", _dedupe_preserve(watchlist)),
    ]
    lines: list[str] = ["# Taste Profile", ""]
    for heading, items_list in sections:
        lines.append(f"## {heading}")
        for entry in items_list:
            lines.append(f"- {entry}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_watchlist_markdown(watchlist: Sequence[str]) -> str:
    """Render `WATCHLIST.md` with the `## Watchlist` heading."""
    lines: list[str] = ["# Watchlist", "", "## Watchlist"]
    for entry in _dedupe_preserve(watchlist):
        lines.append(f"- {entry}")
    lines.append("")
    return "\n".join(lines)


def build_radar_cmd(ctx: Ctx) -> str:
    """Build the single-line `RADAR_CMD` string (Spec 12 A25)."""
    uv_bin = ctx.procs.resolve("uv") or "/opt/homebrew/bin/uv"
    app_radar = os.path.abspath(ctx.paths.app / "radar.py")
    return f"{uv_bin} run --script --locked --isolated {app_radar}"


def build_interview_context(ctx: Ctx) -> dict[str, Any]:
    """Build `{run_root}/runs/bootstrap/interview-context.json` (Spec 08, Spec 11 §9, Spec 12 A8).

    Contains only public entity names and `private-project-N` aliases.
    """
    priv_map = brief._private_scrub_map(ctx)
    base_ctx = brief.build_context_json(ctx, priv_map=priv_map)

    # Top ~30 in-use public tools by usage count for initial HOLD vs ADOPT question
    top_rows = ctx.db.execute(
        "SELECT i.item_id, i.entity_id, i.ring, i.quadrant, e.name, e.kind, "
        "COUNT(u.context) AS usage_count "
        "FROM items i "
        "JOIN entities e ON e.entity_id = i.entity_id "
        "LEFT JOIN usages u ON u.entity_id = i.entity_id AND u.context NOT LIKE 'interest:%' "
        "WHERE i.in_use = 1 AND e.visibility = 'public' "
        "GROUP BY i.item_id "
        "ORDER BY usage_count DESC, i.item_id ASC "
        "LIMIT 30"
    ).fetchall()

    top_in_use_public: list[dict[str, Any]] = []
    for r in top_rows:
        top_in_use_public.append(
            {
                "item_id": str(r["item_id"]),
                "entity_id": brief.scrub_private_text(str(r["entity_id"]), priv_map),
                "name": brief.scrub_private_text(str(r["name"] or r["entity_id"]), priv_map),
                "kind": str(r["kind"]),
                "ring": str(r["ring"] or "adopt"),
                "quadrant": str(r["quadrant"] or ""),
                "usage_count": int(r["usage_count"] or 0),
                "visibility": "public",
            }
        )

    detected_langs: list[str] = list(base_ctx["profile"].get("languages") or [])
    for proj in base_ctx.get("projects") or []:
        for lang in proj.get("languages") or []:
            if lang not in detected_langs:
                detected_langs.append(str(lang))

    return {
        "detected_languages": detected_langs,
        "default_categories": list(DEFAULT_CATEGORIES),
        "default_anti_interests": list(DEFAULT_ANTI_INTERESTS),
        "default_subscriptions": list(DEFAULT_SUBSCRIPTIONS),
        "profile": base_ctx["profile"],
        "watchlist": base_ctx["watchlist"],
        "projects": base_ctx["projects"],
        "in_use": base_ctx["in_use"],
        "top_in_use_public": top_in_use_public,
        "pain_labels": base_ctx["pain_labels"],
        "starred_topics": base_ctx["starred_topics"],
    }


def init(ctx: Ctx) -> dict[str, Any]:
    """Execute `radar.py bootstrap init --channel cli` (Spec 08, Spec 11 §9, Spec 12 A25, A43)."""
    existing = Profile.load(ctx.paths.sync, conn=ctx.db)

    merged_categories = _dedupe_preserve([*DEFAULT_CATEGORIES, *existing.categories])
    merged_anti = _dedupe_preserve([*DEFAULT_ANTI_INTERESTS, *existing.anti_interests])
    merged_subs = _dedupe_preserve(existing.subscriptions or DEFAULT_SUBSCRIPTIONS)

    taste_md = render_taste_profile_markdown(
        categories=merged_categories,
        anti_interests=merged_anti,
        preferences=existing.preferences,
        taste_makers=existing.taste_makers,
        languages=existing.languages,
        subscriptions=merged_subs,
        watchlist=existing.watchlist,
    )
    watchlist_md = render_watchlist_markdown(existing.watchlist)

    fsguard.safe_write(
        ctx.paths.sync / "TASTE_PROFILE.md",
        taste_md,
        mode=0o600,
        root=ctx.paths.sync,
    )
    fsguard.safe_write(
        ctx.paths.sync / "WATCHLIST.md",
        watchlist_md,
        mode=0o600,
        root=ctx.paths.sync,
    )
    ctx.reload_profile()

    radar_cmd_str = build_radar_cmd(ctx)
    fsguard.safe_write(
        ctx.paths.radar_cmd,
        radar_cmd_str + "\n",
        mode=0o600,
        root=ctx.paths.run_root,
    )

    # Sync app and install hourly launchd job
    sync_res = appsync.sync(ctx)
    launchd_status = "skipped"
    if sync_res.ok and sync_res.status in ("synced", "noop"):
        launchd_res = launchd.install(ctx)
        launchd_status = str(launchd_res.get("sync_status") or "installed")

    # Run collect --inventory-only (inventory + items.promote for in-use entities; no sources)
    inv_res = inventory.collect(ctx)
    promo_stats = items.promote(ctx, inventory_only=True, fetch_gates=False)

    # Write interview-context.json under {run_root}/runs/bootstrap/
    interview_ctx = build_interview_context(ctx)
    interview_path = ctx.paths.runs / "bootstrap" / "interview-context.json"
    fsguard.safe_write(
        interview_path,
        json.dumps(interview_ctx, indent=2, sort_keys=True) + "\n",
        mode=0o600,
        root=ctx.paths.runs,
    )

    return {
        "ok": True,
        "action": "init",
        "radar_cmd": radar_cmd_str,
        "radar_cmd_path": str(ctx.paths.radar_cmd),
        "app_sync": sync_res.status,
        "launchd": launchd_status,
        "inventory_added": len(inv_res.added),
        "in_use_items": promo_stats["in_use_created"],
        "interview_context": str(interview_path),
    }


def validate_answers(
    obj: Any,
    *,
    ctx: Ctx | None = None,
) -> list[dict[str, Any]]:
    """Validate bootstrap `answers.json` per Spec 08 and Spec 11 §9–§10.

    Returns a list of field error dicts (empty when valid).
    """
    if not isinstance(obj, Mapping):
        return [{"field": "$", "error": "answers must be a JSON object"}]

    errs: list[dict[str, Any]] = []

    for forbidden_key in ("private_scopes", "private-scopes", "privateScopes"):
        if forbidden_key in obj:
            errs.append(
                {
                    "field": forbidden_key,
                    "error": (
                        "private_scopes must not be in answers.json; register private scopes "
                        "in a terminal via `radar.py profile private-scopes add ... --channel cli`"
                    ),
                }
            )

    unknown_keys = (
        set(obj.keys())
        - ALLOWED_ANSWER_KEYS
        - {
            "private_scopes",
            "private-scopes",
            "privateScopes",
        }
    )
    for uk in sorted(unknown_keys):
        errs.append({"field": str(uk), "error": f"unsupported field: {uk}"})

    list_str_fields = (
        "languages",
        "categories",
        "anti_interests",
        "preferences",
        "taste_makers",
        "watchlist",
        "subscriptions",
    )
    for field_name in list_str_fields:
        if field_name not in obj:
            errs.append({"field": field_name, "error": f"{field_name} is required"})
            continue
        val = obj.get(field_name)
        if not isinstance(val, list):
            errs.append({"field": field_name, "error": f"{field_name} must be a list of strings"})
            continue
        for idx, elem in enumerate(val):
            if not isinstance(elem, str) or not elem.strip():
                errs.append(
                    {
                        "field": f"{field_name}[{idx}]",
                        "error": "must be a non-empty string",
                    }
                )

    if "initial_rings" not in obj:
        errs.append({"field": "initial_rings", "error": "initial_rings is required"})
        return errs

    init_rings = obj.get("initial_rings")
    if not isinstance(init_rings, list):
        errs.append({"field": "initial_rings", "error": "initial_rings must be a list"})
        return errs

    for idx, entry in enumerate(init_rings):
        pfx = f"initial_rings[{idx}]"
        if not isinstance(entry, Mapping):
            errs.append({"field": pfx, "error": "must be an object"})
            continue

        eid = entry.get("entity_id")
        if not isinstance(eid, str) or not eid.strip():
            errs.append({"field": f"{pfx}.entity_id", "error": "entity_id is required"})
        elif ctx is not None:
            row = ctx.db.execute(
                "SELECT item_id FROM items WHERE entity_id = ? OR item_id = ? LIMIT 1",
                (eid.strip(), eid.strip()),
            ).fetchone()
            if row is None:
                errs.append(
                    {
                        "field": f"{pfx}.entity_id",
                        "error": f"entity has no item in inventory: {eid.strip()}",
                    }
                )

        ring = entry.get("ring")
        if not isinstance(ring, str) or ring.strip().lower() not in INITIAL_RING_VALUES:
            errs.append(
                {
                    "field": f"{pfx}.ring",
                    "error": f"ring must be one of {sorted(INITIAL_RING_VALUES)}",
                }
            )

        reason = entry.get("reason")
        if reason is not None and not isinstance(reason, str):
            errs.append({"field": f"{pfx}.reason", "error": "reason must be a string"})

    return errs


def apply_answers(ctx: Ctx, answers_path: Path | str) -> dict[str, Any]:
    """Validate and apply bootstrap `answers.json`, then delete the file (Spec 12 A8/A43)."""
    apath = Path(answers_path).expanduser()
    if not apath.is_file():
        raise errors.ValidationError(
            [{"field": "answers", "error": f"answers file not found: {apath}"}]
        )

    try:
        raw_obj = json.loads(apath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise errors.ValidationError(
            [{"field": "answers", "error": f"invalid JSON in answers file: {exc}"}]
        ) from exc

    field_errors = validate_answers(raw_obj, ctx=ctx)
    if field_errors:
        raise errors.ValidationError(field_errors)

    assert isinstance(raw_obj, Mapping)
    languages = [str(x) for x in raw_obj["languages"]]
    categories = [str(x) for x in raw_obj["categories"]]
    anti_interests = [str(x) for x in raw_obj["anti_interests"]]
    preferences = [str(x) for x in raw_obj["preferences"]]
    taste_makers = [str(x).lstrip("@") for x in raw_obj["taste_makers"]]
    watchlist = [str(x) for x in raw_obj["watchlist"]]
    subscriptions = [str(x) for x in raw_obj["subscriptions"]]

    taste_md = render_taste_profile_markdown(
        categories=categories,
        anti_interests=anti_interests,
        preferences=preferences,
        taste_makers=taste_makers,
        languages=languages,
        subscriptions=subscriptions,
        watchlist=watchlist,
    )
    watchlist_md = render_watchlist_markdown(watchlist)

    fsguard.safe_write(
        ctx.paths.sync / "TASTE_PROFILE.md",
        taste_md,
        mode=0o600,
        root=ctx.paths.sync,
    )
    fsguard.safe_write(
        ctx.paths.sync / "WATCHLIST.md",
        watchlist_md,
        mode=0o600,
        root=ctx.paths.sync,
    )
    ctx.reload_profile()

    # Seed any newly added watchlist entries
    for w_entry in ctx.profile.watchlist:
        eid = seeds._watchlist_entry_to_entity_id(w_entry)
        if eid:
            items.ensure_entity(ctx, eid, classify_visibility=False)

    applied_decisions: list[dict[str, Any]] = []
    for entry in raw_obj["initial_rings"]:
        eid = str(entry["entity_id"]).strip()
        ring = str(entry["ring"]).strip().lower()
        reason = str(entry.get("reason") or "bootstrap initial ring")
        res = decisions.apply(
            ctx,
            eid,
            ring,
            reason=reason,
            channel="bootstrap",
        )
        applied_decisions.append(res)

    # Spec 12 A8: delete answers.json after a successful ingest
    apath.unlink()

    return {
        "ok": True,
        "action": "apply",
        "decisions_applied": len(applied_decisions),
        "answers_deleted": not apath.exists(),
    }


def expand_settings_template(ctx: Ctx) -> dict[str, Any]:
    """Load `templates/run-project-settings.json` and expand `{RADAR_CMD}` and path tokens."""
    if ctx.paths.radar_cmd.is_file():
        radar_cmd_str = ctx.paths.radar_cmd.read_text(encoding="utf-8").strip()
    else:
        radar_cmd_str = build_radar_cmd(ctx)

    raw_tmpl = _SETTINGS_TEMPLATE_PATH.read_text(encoding="utf-8")
    expanded_text = (
        raw_tmpl.replace("{RADAR_CMD}", radar_cmd_str)
        .replace("{RUN_ROOT}", str(ctx.paths.run_root))
        .replace("{SYNC}", str(ctx.paths.sync))
        .replace("{STATE}", str(ctx.paths.state))
    )
    loaded = json.loads(expanded_text)
    assert isinstance(loaded, dict)
    return loaded


def lockdown(ctx: Ctx) -> dict[str, Any]:
    """Execute `radar.py bootstrap lockdown --channel cli` (Spec 08, Spec 12 A25, A34, A43).

    Writes `{run_root}/.claude/settings.json` iff `ENFORCEMENT_HONOURED` is True.
    """
    settings_path = ctx.paths.run_root / ".claude" / "settings.json"
    expanded = expand_settings_template(ctx)

    if not ENFORCEMENT_HONOURED:
        return {
            "ok": True,
            "action": "lockdown",
            "enforcement": "not honoured",
            "message": "enforcement: not honoured",
            "settings_written": False,
        }

    fsguard.safe_write(
        settings_path,
        json.dumps(expanded, indent=2) + "\n",
        mode=0o600,
        root=ctx.paths.run_root,
    )
    return {
        "ok": True,
        "action": "lockdown",
        "enforcement": "honoured",
        "message": "enforcement: honoured",
        "settings_written": True,
        "settings_path": str(settings_path),
    }
