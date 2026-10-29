"""Staged pending changes and macOS notification confirmation codes (Spec 12 A41, A8)."""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import db, decisions, errors, fsguard, notify, profile, redact, runlog, sanitize

if TYPE_CHECKING:
    from .context import Ctx

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6
MAX_CONFIRM_ATTEMPTS = 5
CONFIRM_EXPIRY_HOURS = 24
FEEDBACK_VALUES: frozenset[str] = frozenset({"useful", "not_useful", "acted"})


def _read_sanitized_profile_file(path: Path) -> str:
    raw_bytes = path.read_bytes()[: profile.MAX_PROFILE_BYTES]
    raw_text = raw_bytes.decode("utf-8", errors="ignore")
    cleaned_lines: list[str] = []
    for line in raw_text.splitlines():
        if not line.strip():
            cleaned_lines.append("")
        else:
            cleaned_lines.append(redact.scrub(sanitize.clean(line, limit=300)))
    return "\n".join(cleaned_lines) + "\n"


def stage_decision(
    ctx: Ctx,
    item: str,
    verb: str,
    *,
    ttl: str | int | None = None,
    reason: str | None = None,
    wake: str | dict[str, Any] | None = None,
    channel: str = "thread",
) -> int:
    """Stage a decision in `pending` (`decide-pending` or `DECISIONS.md` ingest, Spec 12 A41)."""
    clean_verb = str(verb).strip().lower()
    if clean_verb not in decisions.VALID_VERBS:
        raise errors.ValidationError([{"field": "verb", "error": f"invalid verb: {verb}"}])
    if channel not in ("thread", "decisions_md"):
        raise errors.ValidationError(
            [{"field": "channel", "error": f"invalid pending channel: {channel}"}]
        )

    item_id, _eid, _ring, _state = decisions.resolve_item_id(ctx, item)
    ttl_days = decisions.parse_ttl_days(ttl, default_days=90)
    wake_str = decisions.validate_wake_triggers(wake)
    safe_reason = (
        db.scrub_text(sanitize.clean(str(reason), limit=400)) if reason is not None else None
    )

    payload = {
        "item_id": item_id,
        "verb": clean_verb,
        "ttl": f"{ttl_days}d",
        "reason": safe_reason,
        "wake": json.loads(wake_str) if wake_str else None,
    }
    payload_str = db.scrub_text(json.dumps(payload, separators=(",", ":"), sort_keys=True)) or "{}"

    cur = ctx.db.execute(
        "INSERT INTO pending(batch_id, kind, payload_json, channel, created_at, status) "
        "VALUES (NULL, 'decision', ?, ?, ?, 'staged')",
        (payload_str, channel, ctx.now_iso),
    )
    ctx.db.commit()
    return int(cur.lastrowid) if cur.lastrowid else 0


def stage_feedback(
    ctx: Ctx,
    item_ref: str,
    feedback_val: str,
    *,
    channel: str = "thread",
) -> int:
    """Stage digest-item feedback (`useful|not_useful|acted`) in `pending` (Spec 05 §6, A41)."""
    clean_fb = str(feedback_val).strip().lower()
    if clean_fb not in FEEDBACK_VALUES:
        raise errors.ValidationError(
            [{"field": "feedback", "error": f"invalid feedback value: {feedback_val}"}]
        )
    ref = str(item_ref).strip()
    if not ref:
        raise errors.ValidationError([{"field": "item", "error": "empty item ref"}])

    payload_str = json.dumps(
        {"ref": ref, "feedback": clean_fb},
        separators=(",", ":"),
        sort_keys=True,
    )
    cur = ctx.db.execute(
        "INSERT INTO pending(batch_id, kind, payload_json, channel, created_at, status) "
        "VALUES (NULL, 'feedback', ?, ?, ?, 'staged')",
        (payload_str, channel, ctx.now_iso),
    )
    ctx.db.commit()
    return int(cur.lastrowid) if cur.lastrowid else 0


def taste_diff(ctx: Ctx) -> dict[str, Any]:
    """Return a unified diff of `TASTE_PROFILE.proposed.md` vs `TASTE_PROFILE.md`."""
    proposed_path = ctx.paths.sync / "TASTE_PROFILE.proposed.md"
    current_path = ctx.paths.sync / "TASTE_PROFILE.md"
    if not proposed_path.is_file():
        raise errors.Refused("TASTE_PROFILE.proposed.md not found in sync dir")

    proposed_text = _read_sanitized_profile_file(proposed_path)
    current_text = _read_sanitized_profile_file(current_path) if current_path.is_file() else ""

    diff_lines = list(
        difflib.unified_diff(
            current_text.splitlines(keepends=True),
            proposed_text.splitlines(keepends=True),
            fromfile="TASTE_PROFILE.md",
            tofile="TASTE_PROFILE.proposed.md",
        )
    )
    return {
        "ok": True,
        "has_changes": len(diff_lines) > 0,
        "diff": "".join(diff_lines),
        "sha256": hashlib.sha256(proposed_text.encode("utf-8")).hexdigest(),
    }


def stage_taste(ctx: Ctx, *, channel: str = "thread") -> dict[str, Any]:
    """Stage the current content and sha256 of `TASTE_PROFILE.proposed.md` (Spec 12 A41, T1.6)."""
    proposed_path = ctx.paths.sync / "TASTE_PROFILE.proposed.md"
    if not proposed_path.is_file():
        raise errors.Refused("TASTE_PROFILE.proposed.md not found in sync dir")

    content = _read_sanitized_profile_file(proposed_path)
    content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    payload = {
        "content": content,
        "sha256": content_sha,
    }
    payload_str = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    cur = ctx.db.execute(
        "INSERT INTO pending(batch_id, kind, payload_json, channel, created_at, status) "
        "VALUES (NULL, 'taste', ?, ?, ?, 'staged')",
        (payload_str, channel, ctx.now_iso),
    )
    ctx.db.commit()
    pid = int(cur.lastrowid) if cur.lastrowid else 0
    return {
        "ok": True,
        "pending_id": pid,
        "kind": "taste",
        "sha256": content_sha,
    }


def taste_apply_cli(ctx: Ctx) -> dict[str, Any]:
    """Apply `TASTE_PROFILE.proposed.md` directly (terminal-only `taste apply --channel cli`)."""
    proposed_path = ctx.paths.sync / "TASTE_PROFILE.proposed.md"
    target_path = ctx.paths.sync / "TASTE_PROFILE.md"
    if not proposed_path.is_file():
        raise errors.Refused("TASTE_PROFILE.proposed.md not found in sync dir")

    content = _read_sanitized_profile_file(proposed_path)
    content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    fsguard.safe_write(target_path, content, mode=0o600, root=ctx.paths.sync)
    with contextlib.suppress(OSError):
        proposed_path.unlink()
    ctx.reload_profile()
    return {
        "ok": True,
        "applied": "taste",
        "sha256": content_sha,
    }


def _summarize_pending_row(
    ctx: Ctx,
    row: Any,
) -> tuple[str, dict[str, Any]]:
    """Return `(notification_token, summary_dict)` with Spec 12 A8 privacy aliasing."""
    kind = str(row["kind"])
    try:
        payload = json.loads(str(row["payload_json"]))
    except json.JSONDecodeError:
        payload = {}

    if kind == "decision":
        item_id = str(payload.get("item_id") or "")
        verb = str(payload.get("verb") or "")
        disp_alias, _vis, is_priv = decisions.alias_for_item(ctx, item_id)
        if is_priv:
            notif_tok = f"{item_id} ({disp_alias}) {verb}"
            summary_obj = {
                "id": disp_alias,
                "item_id": item_id,
                "alias": disp_alias,
                "verb": verb,
            }
        else:
            notif_tok = f"{item_id} {verb}"
            summary_obj = {
                "id": item_id,
                "verb": verb,
            }
        return notif_tok, summary_obj

    if kind == "feedback":
        ref = str(payload.get("ref") or "")
        fb_val = str(payload.get("feedback") or "")
        disp_alias, _vis, is_priv = decisions.alias_for_item(ctx, ref)
        safe_id = disp_alias if is_priv else ref
        return f"{safe_id} {fb_val}", {"id": safe_id, "verb": fb_val}

    if kind == "taste":
        return "taste-update", {"id": "taste-update", "verb": "taste"}

    return kind, {"id": kind, "verb": kind}


def request_confirm(ctx: Ctx) -> dict[str, Any]:
    """Group staged entries into a batch and send the confirmation code via macOS notification."""
    rows = ctx.db.execute(
        "SELECT pending_id, kind, payload_json, channel, created_at, status "
        "FROM pending WHERE status IN ('staged', 'batched') "
        "ORDER BY pending_id ASC"
    ).fetchall()
    if not rows:
        raise errors.Refused("no staged pending changes to confirm")

    # Cancel any earlier open batches before issuing the new batch code
    ctx.db.execute("UPDATE confirm_batches SET status = 'cancelled' WHERE status = 'open'")

    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    salt = secrets.token_hex(16)
    code_hash = hashlib.sha256((salt + code).encode("utf-8")).hexdigest()
    expires_at = (
        (ctx.now + timedelta(hours=CONFIRM_EXPIRY_HOURS))
        .astimezone(UTC)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    cur = ctx.db.execute(
        "INSERT INTO confirm_batches(code_hash, salt, created_at, expires_at, attempts, status) "
        "VALUES (?, ?, ?, ?, 0, 'open')",
        (code_hash, salt, ctx.now_iso, expires_at),
    )
    batch_id = int(cur.lastrowid) if cur.lastrowid else 0
    if not batch_id:
        b_row = ctx.db.execute(
            "SELECT batch_id FROM confirm_batches ORDER BY batch_id DESC LIMIT 1"
        ).fetchone()
        batch_id = int(b_row[0]) if b_row else 0

    notif_tokens: list[str] = []
    summary_list: list[dict[str, Any]] = []
    for r in rows:
        pid = int(r["pending_id"])
        ctx.db.execute(
            "UPDATE pending SET batch_id = ?, status = 'batched' WHERE pending_id = ?",
            (batch_id, pid),
        )
        tok, s_obj = _summarize_pending_row(ctx, r)
        notif_tokens.append(tok)
        summary_list.append(s_obj)

    ctx.db.commit()

    msg = notify.format_confirm_code_message(code, len(rows), notif_tokens)
    ok, err_msg = notify.send(msg, group=notify.group_id(f"confirm|{batch_id}"), ctx=ctx)
    if not ok:
        raise errors.RadarError(f"failed to send confirmation notification: {err_msg}", exit_code=1)

    return {
        "ok": True,
        "pending": len(rows),
        "expires_at": expires_at,
        "summary": summary_list,
    }


def _apply_feedback_row(ctx: Ctx, ref: str, feedback_val: str) -> None:
    updated = ctx.db.execute(
        "UPDATE digest_items SET feedback = ? WHERE ref = ?",
        (feedback_val, ref),
    ).rowcount
    if updated and updated > 0:
        return

    latest_run = ctx.db.execute(
        "SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    run_id = (
        str(latest_run["run_id"])
        if latest_run is not None
        else runlog.start_run(ctx, mode="adhoc", requested_mode="adhoc")
    )
    ctx.db.execute(
        "INSERT INTO digest_items(run_id, ref, section, rank, feedback) "
        "VALUES (?, ?, 'horizon', 1, ?) "
        "ON CONFLICT(run_id, ref) DO UPDATE SET feedback = excluded.feedback",
        (run_id, ref, feedback_val),
    )


def confirm(ctx: Ctx, code: str) -> dict[str, Any]:
    """Verify `code` and apply the active confirmation batch (Spec 12 A41)."""
    clean_code = str(code or "").strip().upper()
    if not clean_code:
        raise errors.Refused("confirmation code is required")

    batch = ctx.db.execute(
        "SELECT batch_id, code_hash, salt, created_at, expires_at, attempts, status "
        "FROM confirm_batches WHERE status = 'open' ORDER BY batch_id DESC LIMIT 1"
    ).fetchone()
    if batch is None:
        raise errors.Refused("no open confirmation batch")

    batch_id = int(batch["batch_id"])
    exp_iso = str(batch["expires_at"])
    try:
        exp_dt = datetime.fromisoformat(exp_iso.replace("Z", "+00:00"))
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=UTC)
        is_expired = exp_dt <= ctx.now
    except ValueError:
        is_expired = exp_iso <= ctx.now_iso

    if is_expired:
        ctx.db.execute(
            "UPDATE confirm_batches SET status = 'expired' WHERE batch_id = ?",
            (batch_id,),
        )
        ctx.db.execute(
            "UPDATE pending SET status = 'expired' WHERE batch_id = ? AND status = 'batched'",
            (batch_id,),
        )
        ctx.db.commit()
        raise errors.Refused("confirmation code expired")

    salt = str(batch["salt"])
    expected_hash = str(batch["code_hash"])
    candidate_hash = hashlib.sha256((salt + clean_code).encode("utf-8")).hexdigest()

    if not secrets.compare_digest(candidate_hash, expected_hash):
        attempts = int(batch["attempts"] or 0) + 1
        if attempts >= MAX_CONFIRM_ATTEMPTS:
            ctx.db.execute(
                "UPDATE confirm_batches SET attempts = ?, status = 'cancelled' WHERE batch_id = ?",
                (attempts, batch_id),
            )
            ctx.db.execute(
                "UPDATE pending SET status = 'cancelled' WHERE batch_id = ? AND status = 'batched'",
                (batch_id,),
            )
            ctx.db.commit()
            raise errors.Refused("confirmation batch cancelled after 5 wrong attempts")

        ctx.db.execute(
            "UPDATE confirm_batches SET attempts = ? WHERE batch_id = ?",
            (attempts, batch_id),
        )
        ctx.db.commit()
        raise errors.Refused("invalid confirmation code")

    rows = ctx.db.execute(
        "SELECT pending_id, kind, payload_json, channel, created_at, status "
        "FROM pending WHERE batch_id = ? AND status = 'batched' ORDER BY pending_id ASC",
        (batch_id,),
    ).fetchall()

    # Pre-validate all taste entries before mutating anything (Spec 12 A41)
    parsed_rows: list[tuple[Any, dict[str, Any]]] = []
    for r in rows:
        try:
            p_obj = json.loads(str(r["payload_json"]))
        except json.JSONDecodeError as exc:
            raise errors.Refused(f"corrupted pending payload: {exc}") from exc
        if not isinstance(p_obj, dict):
            raise errors.Refused("corrupted pending payload")
        if str(r["kind"]) == "taste":
            stored_content = str(p_obj.get("content") or "")
            stored_sha = str(p_obj.get("sha256") or "")
            actual_sha = hashlib.sha256(stored_content.encode("utf-8")).hexdigest()
            if not stored_sha or actual_sha != stored_sha:
                raise errors.Refused("taste payload sha256 mismatch; batch left pending")
        parsed_rows.append((r, p_obj))

    summary_list: list[dict[str, Any]] = []
    for r, p_obj in parsed_rows:
        pid = int(r["pending_id"])
        kind = str(r["kind"])
        channel = str(r["channel"])

        if kind == "decision":
            decisions.apply(
                ctx,
                str(p_obj["item_id"]),
                str(p_obj["verb"]),
                ttl=p_obj.get("ttl"),
                reason=p_obj.get("reason"),
                wake=p_obj.get("wake"),
                channel=channel,
            )
        elif kind == "feedback":
            _apply_feedback_row(
                ctx,
                str(p_obj["ref"]),
                str(p_obj["feedback"]),
            )
        elif kind == "taste":
            target_path = ctx.paths.sync / "TASTE_PROFILE.md"
            fsguard.safe_write(
                target_path,
                str(p_obj["content"]),
                mode=0o600,
                root=ctx.paths.sync,
            )
            proposed_path = ctx.paths.sync / "TASTE_PROFILE.proposed.md"
            if proposed_path.is_file():
                with contextlib.suppress(OSError):
                    proposed_path.unlink()
            ctx.reload_profile()

        _tok, s_obj = _summarize_pending_row(ctx, r)
        summary_list.append(s_obj)
        ctx.db.execute(
            "UPDATE pending SET status = 'applied' WHERE pending_id = ?",
            (pid,),
        )

    ctx.db.execute(
        "UPDATE confirm_batches SET status = 'confirmed' WHERE batch_id = ?",
        (batch_id,),
    )
    ctx.db.commit()
    return {
        "ok": True,
        "confirmed": len(parsed_rows),
        "summary": summary_list,
    }


def list_pending_cli(ctx: Ctx) -> dict[str, Any]:
    """Return full details of all staged/batched entries (`pending list --channel cli`)."""
    rows = ctx.db.execute(
        "SELECT pending_id, batch_id, kind, payload_json, channel, created_at, status "
        "FROM pending WHERE status IN ('staged', 'batched') ORDER BY pending_id ASC"
    ).fetchall()

    entries: list[dict[str, Any]] = []
    for r in rows:
        try:
            payload = json.loads(str(r["payload_json"]))
        except json.JSONDecodeError:
            payload = {}
        entry: dict[str, Any] = {
            "pending_id": int(r["pending_id"]),
            "batch_id": r["batch_id"],
            "kind": str(r["kind"]),
            "channel": str(r["channel"]),
            "status": str(r["status"]),
            "created_at": str(r["created_at"]),
        }
        if str(r["kind"]) == "decision":
            entry["item_id"] = payload.get("item_id")
            entry["verb"] = payload.get("verb")
            entry["ttl"] = payload.get("ttl")
            entry["reason"] = payload.get("reason")
            entry["wake"] = payload.get("wake")
        elif str(r["kind"]) == "feedback":
            entry["ref"] = payload.get("ref")
            entry["feedback"] = payload.get("feedback")
        elif str(r["kind"]) == "taste":
            entry["sha256"] = payload.get("sha256")
            current_path = ctx.paths.sync / "TASTE_PROFILE.md"
            cur_text = _read_sanitized_profile_file(current_path) if current_path.is_file() else ""
            prop_text = str(payload.get("content") or "")
            entry["diff"] = "".join(
                difflib.unified_diff(
                    cur_text.splitlines(keepends=True),
                    prop_text.splitlines(keepends=True),
                    fromfile="TASTE_PROFILE.md",
                    tofile="TASTE_PROFILE.proposed.md",
                )
            )
        entries.append(entry)

    return {
        "ok": True,
        "count": len(entries),
        "items": entries,
    }
