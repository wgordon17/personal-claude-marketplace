"""User decisions, wake triggers, and DECISIONS.md staging/application (Spec 05 §6, Spec 12 A41)."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from . import db, errors, runlog, sanitize

if TYPE_CHECKING:
    from .context import Ctx

VALID_VERBS: frozenset[str] = frozenset(
    {
        "adopt",
        "trial",
        "assess",
        "hold",
        "reject",
        "snooze",
    }
)
RING_VERBS: frozenset[str] = frozenset({"adopt", "trial", "assess", "hold"})
VALID_CHANNELS: frozenset[str] = frozenset({"thread", "decisions_md", "cli", "bootstrap"})

DECISIONS_MD_EDIT_RE = re.compile(r"^(adopt|trial|assess|hold|reject|snooze)(?: (\d+d))?$")
_TTL_RE = re.compile(r"^(\d+)d?$")
_MAJOR_SEMVER_RE = re.compile(r"^v?(\d+)\.0\.0(?:[-+].*)?$")


def parse_ttl_days(ttl: str | int | None, default_days: int = 90) -> int:
    """Parse a TTL string like `'90d'` or `'30d'` into integer days (default 90)."""
    if ttl is None or str(ttl).strip() == "":
        return default_days
    if isinstance(ttl, int):
        if ttl <= 0:
            raise errors.ValidationError([{"field": "ttl", "error": "ttl must be positive"}])
        return ttl
    clean = str(ttl).strip().lower()
    m = _TTL_RE.match(clean)
    if not m:
        raise errors.ValidationError([{"field": "ttl", "error": f"invalid ttl: {ttl}"}])
    days = int(m.group(1))
    if days <= 0:
        raise errors.ValidationError([{"field": "ttl", "error": "ttl must be positive"}])
    return days


def validate_wake_triggers(wake: str | Mapping[str, Any] | None) -> str | None:
    """Validate and serialize wake triggers JSON (`major_release`, `feature`, `stars_gte`)."""
    if wake is None or wake == "":
        return None
    if isinstance(wake, str):
        try:
            obj = json.loads(wake)
        except json.JSONDecodeError as exc:
            raise errors.ValidationError(
                [{"field": "wake", "error": f"invalid wake JSON: {exc}"}]
            ) from exc
    elif isinstance(wake, Mapping):
        obj = dict(wake)
    else:
        raise errors.ValidationError([{"field": "wake", "error": "wake must be a JSON object"}])

    if not isinstance(obj, Mapping) or not obj:
        raise errors.ValidationError(
            [{"field": "wake", "error": "wake must be a non-empty JSON object"}]
        )

    allowed = {"major_release", "feature", "stars_gte"}
    unknown = set(obj.keys()) - allowed
    if unknown:
        raise errors.ValidationError(
            [{"field": "wake", "error": f"unsupported wake keys: {sorted(unknown)}"}]
        )

    cleaned: dict[str, Any] = {}
    if "major_release" in obj:
        if not isinstance(obj["major_release"], bool):
            raise errors.ValidationError(
                [{"field": "wake.major_release", "error": "must be boolean"}]
            )
        cleaned["major_release"] = obj["major_release"]
    if "feature" in obj:
        feat_str = str(obj["feature"] or "").strip()
        if not feat_str:
            raise errors.ValidationError(
                [{"field": "wake.feature", "error": "must be non-empty string"}]
            )
        cleaned["feature"] = sanitize.clean(feat_str, limit=120)
    if "stars_gte" in obj:
        val = obj["stars_gte"]
        if isinstance(val, bool) or not isinstance(val, int | float):
            raise errors.ValidationError([{"field": "wake.stars_gte", "error": "must be numeric"}])
        cleaned["stars_gte"] = int(val)

    return db.scrub_text(json.dumps(cleaned, separators=(",", ":"), sort_keys=True))


def resolve_item_id(ctx: Ctx, item_or_entity: str) -> tuple[str, str, str | None, str]:
    """Resolve `item_id` or `entity_id` to `(item_id, entity_id, current_ring, current_state)`."""
    key = str(item_or_entity).strip()
    row = ctx.db.execute(
        "SELECT item_id, entity_id, ring, state FROM items WHERE item_id = ? OR entity_id = ?",
        (key, key),
    ).fetchone()
    if row is None:
        alias_row = ctx.db.execute(
            "SELECT i.item_id, i.entity_id, i.ring, i.state "
            "FROM items i JOIN projects p ON 'project:' || p.project_key IN ("
            "  SELECT context FROM usages u WHERE u.entity_id = i.entity_id"
            ") WHERE p.alias = ? LIMIT 1",
            (key,),
        ).fetchone()
        if alias_row is not None:
            row = alias_row
    if row is None:
        raise errors.ValidationError([{"field": "item", "error": f"unknown item: {key}"}])
    return (
        str(row["item_id"]),
        str(row["entity_id"]),
        str(row["ring"]) if row["ring"] else None,
        str(row["state"]),
    )


def alias_for_item(ctx: Ctx, item_id: str) -> tuple[str, str, bool]:
    """Return `(display_alias, visibility, is_private)` for `item_id` per Spec 12 A8."""
    row = ctx.db.execute(
        "SELECT i.item_id, i.entity_id, e.name, e.visibility "
        "FROM items i JOIN entities e ON e.entity_id = i.entity_id "
        "WHERE i.item_id = ?",
        (item_id,),
    ).fetchone()
    if row is None:
        return item_id, "public", False

    vis = str(row["visibility"] or "public")
    eid = str(row["entity_id"])
    if vis == "public":
        return str(row["name"] or eid), "public", False

    proj_row = ctx.db.execute(
        "SELECT p.alias FROM projects p "
        "JOIN usages u ON u.context = 'project:' || p.project_key "
        "WHERE u.entity_id = ? AND p.alias IS NOT NULL "
        "ORDER BY p.alias ASC LIMIT 1",
        (eid,),
    ).fetchone()
    if proj_row is not None and proj_row["alias"]:
        return str(proj_row["alias"]), "private", True

    alias_row = ctx.db.execute(
        "SELECT alias FROM entity_aliases WHERE entity_id = ? "
        "AND alias LIKE 'private-%' ORDER BY alias ASC LIMIT 1",
        (eid,),
    ).fetchone()
    if alias_row is not None and alias_row["alias"]:
        return str(alias_row["alias"]), "private", True

    any_priv_proj = ctx.db.execute(
        "SELECT alias FROM projects WHERE visibility != 'public' AND alias IS NOT NULL "
        "ORDER BY alias ASC LIMIT 1"
    ).fetchone()
    if any_priv_proj is not None and any_priv_proj["alias"]:
        return str(any_priv_proj["alias"]), "private", True

    return f"private-{item_id.lower()}", "private", True


def _ensure_run_id_for_history(ctx: Ctx, channel: str) -> str:
    if ctx.run_id:
        row = ctx.db.execute("SELECT 1 FROM runs WHERE run_id = ?", (ctx.run_id,)).fetchone()
        if row is not None:
            return ctx.run_id

    latest = ctx.db.execute("SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
    if latest is not None:
        return str(latest["run_id"])

    mode = "bootstrap" if channel == "bootstrap" else "adhoc"
    return runlog.start_run(ctx, mode=mode, requested_mode=mode)


def apply(
    ctx: Ctx,
    item: str,
    verb: str,
    *,
    ttl: str | int | None = None,
    reason: str | None = None,
    wake: str | Mapping[str, Any] | None = None,
    channel: str = "cli",
) -> dict[str, Any]:
    """Apply a user decision (`actor='user'`), update prior rows, and update item state/ring."""
    clean_verb = str(verb).strip().lower()
    if clean_verb not in VALID_VERBS:
        raise errors.ValidationError([{"field": "verb", "error": f"invalid verb: {verb}"}])
    clean_channel = str(channel).strip().lower()
    if clean_channel not in VALID_CHANNELS:
        raise errors.ValidationError([{"field": "channel", "error": f"invalid channel: {channel}"}])

    item_id, _entity_id, old_ring, _old_state = resolve_item_id(ctx, item)
    ttl_days = parse_ttl_days(ttl, default_days=90)
    expires_at = (ctx.now + timedelta(days=ttl_days)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    wake_json = validate_wake_triggers(wake)
    safe_reason = (
        db.scrub_text(sanitize.clean(str(reason), limit=400)) if reason is not None else None
    )

    cur = ctx.db.execute(
        "INSERT INTO decisions("
        "item_id, verb, reason, channel, actor, created_at, expires_at, "
        "wake_triggers_json, superseded_by"
        ") VALUES (?, ?, ?, ?, 'user', ?, ?, ?, NULL)",
        (
            item_id,
            clean_verb,
            safe_reason,
            clean_channel,
            ctx.now_iso,
            expires_at,
            wake_json,
        ),
    )
    decision_id = int(cur.lastrowid) if cur.lastrowid else 0
    if not decision_id:
        r_id = ctx.db.execute(
            "SELECT decision_id FROM decisions WHERE item_id = ? ORDER BY decision_id DESC LIMIT 1",
            (item_id,),
        ).fetchone()
        decision_id = int(r_id[0]) if r_id else 0

    # Mark earlier un-obsoleted decisions for this item as obsoleted by this decision
    ctx.db.execute(
        "UPDATE decisions SET superseded_by = ? "
        "WHERE item_id = ? AND decision_id != ? AND superseded_by IS NULL",
        (decision_id, item_id, decision_id),
    )

    if clean_verb in RING_VERBS:
        new_ring = clean_verb
        new_state = "active"
        ctx.db.execute(
            "UPDATE items SET ring = ?, state = ? WHERE item_id = ?",
            (new_ring, new_state, item_id),
        )
        if old_ring != new_ring:
            hist_run_id = _ensure_run_id_for_history(ctx, clean_channel)
            ctx.db.execute(
                "INSERT INTO ring_history(item_id, run_id, from_ring, to_ring, at, reason) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    item_id,
                    hist_run_id,
                    old_ring,
                    new_ring,
                    ctx.now_iso,
                    safe_reason or f"decision:{clean_verb}",
                ),
            )
    elif clean_verb == "reject":
        ctx.db.execute(
            "UPDATE items SET state = 'rejected' WHERE item_id = ?",
            (item_id,),
        )
    elif clean_verb == "snooze":
        ctx.db.execute(
            "UPDATE items SET state = 'snoozed' WHERE item_id = ?",
            (item_id,),
        )

    ctx.db.commit()
    disp_alias, vis, _is_priv = alias_for_item(ctx, item_id)
    return {
        "decision_id": decision_id,
        "item_id": item_id,
        "alias": disp_alias,
        "visibility": vis,
        "verb": clean_verb,
        "channel": clean_channel,
        "actor": "user",
        "expires_at": expires_at,
    }


def _check_wake_fired(
    ctx: Ctx,
    entity_id: str,
    created_at: str,
    wake_json: str | None,
) -> str | None:
    if not wake_json:
        return None
    try:
        triggers = json.loads(wake_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(triggers, Mapping):
        return None

    if triggers.get("major_release") is True:
        rel_rows = ctx.db.execute(
            "SELECT title, payload_json FROM signals "
            "WHERE entity_id = ? AND source_id IN ('gh_releases', 'gh_changelog_file') "
            "AND COALESCE(published_at, fetched_at) >= ? AND withdrawn = 0",
            (entity_id, created_at),
        ).fetchall()
        for r in rel_rows:
            tag = ""
            if r["payload_json"]:
                try:
                    p = json.loads(str(r["payload_json"]))
                    if isinstance(p, Mapping):
                        tag = str(p.get("tag_name") or "")
                        if p.get("is_major") is True:
                            return "major_release"
                except json.JSONDecodeError:
                    pass
            if _MAJOR_SEMVER_RE.match(tag.strip()):
                return "major_release"
            title_str = str(r["title"] or "")
            for tok in title_str.split():
                if _MAJOR_SEMVER_RE.match(tok.strip(":,()")):
                    return "major_release"

    if triggers.get("feature"):
        sub = str(triggers["feature"]).strip().lower()
        if sub:
            sig_rows = ctx.db.execute(
                "SELECT title, untrusted_excerpt FROM signals "
                "WHERE entity_id = ? AND COALESCE(published_at, fetched_at) >= ? AND withdrawn = 0",
                (entity_id, created_at),
            ).fetchall()
            for r in sig_rows:
                combined = f"{r['title'] or ''}\n{r['untrusted_excerpt'] or ''}".lower()
                if sub in combined:
                    return f"feature:{triggers['feature']}"

    if triggers.get("stars_gte") is not None:
        try:
            threshold = float(triggers["stars_gte"])
        except (TypeError, ValueError):
            threshold = None
        if threshold is not None:
            m_row = ctx.db.execute(
                "SELECT value FROM metrics WHERE entity_id = ? AND metric = 'stars' "
                "AND value IS NOT NULL ORDER BY observed_at DESC LIMIT 1",
                (entity_id,),
            ).fetchone()
            if m_row is not None and float(m_row["value"]) >= threshold:
                return f"stars_gte:{int(threshold)}"

    return None


def evaluate_due(ctx: Ctx) -> list[dict[str, Any]]:
    """Return active decisions that have expired or had a wake trigger fire (Spec 05 §6)."""
    rows = ctx.db.execute(
        "SELECT d.decision_id, d.item_id, d.verb, d.reason, d.channel, d.created_at, "
        "d.expires_at, d.wake_triggers_json, i.entity_id, i.ring, i.state, "
        "e.name, e.kind, e.visibility "
        "FROM decisions d "
        "JOIN items i ON i.item_id = d.item_id "
        "JOIN entities e ON e.entity_id = i.entity_id "
        "WHERE d.superseded_by IS NULL "
        "ORDER BY d.decision_id ASC"
    ).fetchall()

    due_list: list[dict[str, Any]] = []
    now_iso = ctx.now_iso
    now_dt = ctx.now

    for r in rows:
        verb = str(r["verb"])
        exp_iso = str(r["expires_at"]) if r["expires_at"] else None
        wake_reason = _check_wake_fired(
            ctx,
            str(r["entity_id"]),
            str(r["created_at"]),
            str(r["wake_triggers_json"]) if r["wake_triggers_json"] else None,
        )

        is_expired = False
        if exp_iso and verb in ("snooze", "reject", "hold"):
            try:
                exp_dt = datetime.fromisoformat(exp_iso.replace("Z", "+00:00"))
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=UTC)
                is_expired = exp_dt <= now_dt
            except ValueError:
                is_expired = exp_iso <= now_iso

        if not is_expired and wake_reason is None:
            continue

        item_id = str(r["item_id"])
        disp_alias, vis, is_priv = alias_for_item(ctx, item_id)
        if is_priv and str(r["kind"]) == "package":
            # Private packages are excluded from per-item brief lists (Spec 12 A8)
            continue

        due_list.append(
            {
                "ref": item_id,
                "item_id": item_id,
                "entity_id": disp_alias if is_priv else str(r["entity_id"]),
                "name": disp_alias,
                "kind": str(r["kind"]),
                "visibility": vis,
                "decision_id": int(r["decision_id"]),
                "verb": verb,
                "ring": r["ring"],
                "expires_at": exp_iso,
                "due_reason": wake_reason or "expired",
                "reason": r["reason"],
            }
        )
    return due_list


def stage_decisions_md(ctx: Ctx) -> dict[str, Any]:
    """Hash-gated parser that stages `DECISIONS.md` edits into `pending` (Spec 05 §6, A41)."""
    from . import pending

    dec_path = ctx.paths.sync / "DECISIONS.md"
    if not dec_path.is_file():
        return {"staged": 0, "malformed": [], "changed": False}

    raw_bytes = dec_path.read_bytes()
    file_hash = hashlib.sha256(raw_bytes).hexdigest()
    prev_hash = db.kv_get(ctx.db, "decisions_md_hash")
    if prev_hash == file_hash:
        return {"staged": 0, "malformed": [], "changed": False}

    text = raw_bytes.decode("utf-8", errors="ignore")
    staged_count = 0
    malformed: list[dict[str, str]] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 6:
            continue
        item_cell = cells[0]
        edit_cell = cells[5]
        if item_cell.lower() in ("item", "item_id", "---") or set(item_cell) <= {"-", ":"}:
            continue
        if not edit_cell:
            continue

        m = DECISIONS_MD_EDIT_RE.match(edit_cell)
        if not m:
            malformed.append(
                {
                    "item": item_cell,
                    "edit": edit_cell,
                    "error": (
                        "malformed edit cell; expected "
                        "^(adopt|trial|assess|hold|reject|snooze)( \\d+d)?$"
                    ),
                }
            )
            continue

        verb = m.group(1)
        ttl = m.group(2)
        try:
            pending.stage_decision(
                ctx,
                item_cell,
                verb,
                ttl=ttl,
                reason="DECISIONS.md edit",
                channel="decisions_md",
            )
            staged_count += 1
        except errors.RadarError as exc:
            malformed.append({"item": item_cell, "edit": edit_cell, "error": str(exc)})

    db.kv_set(ctx.db, "decisions_md_hash", file_hash)
    if malformed:
        db.kv_set(
            ctx.db,
            "decisions_md_malformed_json",
            json.dumps(malformed, separators=(",", ":")),
        )
    else:
        ctx.db.execute("DELETE FROM kv WHERE key = 'decisions_md_malformed_json'")
        ctx.db.commit()

    return {
        "staged": staged_count,
        "malformed": malformed,
        "changed": True,
    }


def apply_decisions_md_cli(ctx: Ctx) -> dict[str, Any]:
    """Apply staged `DECISIONS.md` edits directly from an interactive terminal."""
    ingest_res = stage_decisions_md(ctx)
    rows = ctx.db.execute(
        "SELECT pending_id, payload_json FROM pending "
        "WHERE kind = 'decision' AND channel = 'decisions_md' "
        "AND status IN ('staged', 'batched') ORDER BY pending_id ASC"
    ).fetchall()

    applied: list[dict[str, Any]] = []
    for r in rows:
        pid = int(r["pending_id"])
        try:
            payload = json.loads(str(r["payload_json"]))
        except json.JSONDecodeError:
            continue
        res = apply(
            ctx,
            str(payload.get("item_id") or ""),
            str(payload.get("verb") or ""),
            ttl=payload.get("ttl"),
            reason=payload.get("reason"),
            wake=payload.get("wake"),
            channel="decisions_md",
        )
        ctx.db.execute("UPDATE pending SET status = 'applied' WHERE pending_id = ?", (pid,))
        applied.append(res)

    ctx.db.commit()
    return {
        "ok": True,
        "applied": len(applied),
        "items": applied,
        "malformed": ingest_res["malformed"],
    }
