"""Alert lifecycle CLI transitions and automatic resolution (Spec 12 A4)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from . import errors, security

if TYPE_CHECKING:
    from .context import Ctx

ACTION_TO_STATE: dict[str, str] = {
    "ack": "acked",
    "resolve": "resolved",
    "false-positive": "false_positive",
}

_ALERT_ID_RE = re.compile(r"^(?:A-)?(\d+)$", re.IGNORECASE)


def parse_alert_id(raw: str | int) -> int:
    """Parse an alert identifier (`12`, `'12'`, or `'A-12'`) into an integer `alert_id`."""
    if isinstance(raw, int):
        return raw
    m = _ALERT_ID_RE.match(str(raw).strip())
    if not m:
        raise errors.ValidationError([{"field": "alert_id", "error": f"invalid alert_id: {raw}"}])
    return int(m.group(1))


def transition(ctx: Ctx, action: str, alert_id_raw: str | int) -> dict[str, Any]:
    """Transition an alert to `acked`, `resolved`, or `false_positive` (Spec 12 A4)."""
    clean_action = str(action).strip().lower()
    if clean_action not in ACTION_TO_STATE:
        raise errors.ValidationError(
            [{"field": "action", "error": f"invalid alert action: {action}"}]
        )
    alert_id = parse_alert_id(alert_id_raw)
    new_state = ACTION_TO_STATE[clean_action]

    row = ctx.db.execute(
        "SELECT alert_id, state, kind, entity_id FROM alerts WHERE alert_id = ?",
        (alert_id,),
    ).fetchone()
    if row is None:
        raise errors.ValidationError(
            [{"field": "alert_id", "error": f"alert not found: {alert_id_raw}"}]
        )

    ctx.db.execute(
        "UPDATE alerts SET state = ? WHERE alert_id = ?",
        (new_state, alert_id),
    )
    ctx.db.commit()
    return {
        "ok": True,
        "alert_id": alert_id,
        "from_state": str(row["state"]),
        "state": new_state,
    }


def _lifecycle_condition_cleared(
    ctx: Ctx,
    entity_id: str | None,
    payload: Mapping[str, Any],
    dedupe_key: str,
    installed_versions: list[str],
) -> bool:
    if not entity_id:
        return False

    usage_row = ctx.db.execute(
        "SELECT 1 FROM usages WHERE entity_id = ? "
        "AND (context NOT LIKE 'interest:%' OR context = 'interest:watchlist') LIMIT 1",
        (entity_id,),
    ).fetchone()
    if usage_row is None:
        return True

    rule = str(payload.get("check") or "").strip().upper()
    if not rule and "|" in dedupe_key:
        rule = dedupe_key.rsplit("|", 1)[-1].strip().upper()

    if rule == "LIFE-01":
        sig_row = ctx.db.execute(
            "SELECT payload_json FROM signals "
            "WHERE entity_id = ? AND source_id = 'gh_repo_meta' "
            "ORDER BY signal_id DESC LIMIT 1",
            (entity_id,),
        ).fetchone()
        if sig_row is not None and sig_row["payload_json"]:
            try:
                p = json.loads(str(sig_row["payload_json"]))
                if isinstance(p, Mapping) and p.get("archived") is False:
                    return True
            except json.JSONDecodeError:
                pass

    elif rule == "LIFE-02":
        sig_row = ctx.db.execute(
            "SELECT payload_json FROM signals "
            "WHERE entity_id = ? AND source_id = 'npm_registry_meta' "
            "ORDER BY signal_id DESC LIMIT 1",
            (entity_id,),
        ).fetchone()
        if sig_row is not None and sig_row["payload_json"]:
            try:
                p = json.loads(str(sig_row["payload_json"]))
                if isinstance(p, Mapping) and not p.get("deprecated"):
                    dep_vers = p.get("deprecated_versions")
                    if not isinstance(dep_vers, Mapping) or not any(
                        iv in dep_vers for iv in installed_versions
                    ):
                        return True
            except json.JSONDecodeError:
                pass

    elif rule == "LIFE-03":
        sig_row = ctx.db.execute(
            "SELECT payload_json FROM signals "
            "WHERE entity_id = ? AND source_id = 'pypi_meta' "
            "ORDER BY signal_id DESC LIMIT 1",
            (entity_id,),
        ).fetchone()
        if sig_row is not None and sig_row["payload_json"]:
            try:
                p = json.loads(str(sig_row["payload_json"]))
                if (
                    isinstance(p, Mapping)
                    and str(p.get("project_status") or "").lower() == "active"
                ):
                    return True
            except json.JSONDecodeError:
                pass

    elif rule == "LIFE-04":
        bad_ver = str(payload.get("version") or "").strip()
        if bad_ver and bad_ver not in installed_versions:
            return True

    elif rule == "LIFE-05":
        cycle = str(payload.get("cycle") or "").strip()
        if (
            cycle
            and installed_versions
            and not any(iv == cycle or iv.startswith(f"{cycle}.") for iv in installed_versions)
        ):
            return True

    elif rule == "LIFE-06":
        sig_row = ctx.db.execute(
            "SELECT payload_json FROM signals "
            "WHERE entity_id = ? AND source_id = 'gh_repo_meta' "
            "ORDER BY signal_id DESC LIMIT 1",
            (entity_id,),
        ).fetchone()
        if sig_row is not None and sig_row["payload_json"]:
            try:
                p = json.loads(str(sig_row["payload_json"]))
                pushed = str(p.get("pushed_at") or "").strip() if isinstance(p, Mapping) else ""
                if pushed:
                    dt = datetime.fromisoformat(pushed.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    if (ctx.now - dt) <= timedelta(days=180):
                        return True
            except (ValueError, json.JSONDecodeError):
                pass

    elif rule == "LIFE-07":
        cutoff_365 = (ctx.now - timedelta(days=365)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        rel_row = ctx.db.execute(
            "SELECT 1 FROM signals WHERE entity_id = ? AND source_id = 'gh_releases' "
            "AND published_at >= ? LIMIT 1",
            (entity_id, cutoff_365),
        ).fetchone()
        if rel_row is not None:
            return True

    return False


def auto_resolve(ctx: Ctx) -> list[int]:
    """Auto-resolve version-specific alerts that left `usages` and cleared lifecycle alerts (A4)."""
    open_rows = ctx.db.execute(
        "SELECT alert_id, kind, entity_id, dedupe_key, payload_json "
        "FROM alerts WHERE state = 'open' ORDER BY alert_id ASC"
    ).fetchall()

    resolved_ids: list[int] = []
    for row in open_rows:
        alert_id = int(row["alert_id"])
        kind = str(row["kind"])
        entity_id = str(row["entity_id"]) if row["entity_id"] else None
        dedupe_key = str(row["dedupe_key"])

        payload: dict[str, Any] = {}
        if row["payload_json"]:
            try:
                parsed = json.loads(str(row["payload_json"]))
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                payload = {}

        installed_versions: list[str] = []
        has_usage = False
        if entity_id:
            u_rows = ctx.db.execute(
                "SELECT version FROM usages WHERE entity_id = ? AND context NOT LIKE 'interest:%'",
                (entity_id,),
            ).fetchall()
            has_usage = len(u_rows) > 0
            installed_versions = [
                str(ur["version"]).strip()
                for ur in u_rows
                if ur["version"] and str(ur["version"]).strip()
            ]

        should_resolve = False

        if kind in ("compromise", "kev", "integrity"):
            if entity_id and not has_usage:
                should_resolve = True
            elif entity_id:
                bad_versions: list[str] = []
                if isinstance(payload.get("versions"), list):
                    bad_versions.extend(str(v).strip() for v in payload["versions"] if v)
                if payload.get("version") and str(payload["version"]).strip() != "any":
                    v_single = str(payload["version"]).strip()
                    if v_single not in bad_versions:
                        bad_versions.append(v_single)
                if (
                    bad_versions
                    and not any(iv in bad_versions for iv in installed_versions)
                    or (
                        (payload.get("affected_ranges") or payload.get("affected_versions"))
                        and installed_versions
                        and not any(
                            security.version_is_affected(iv, payload) for iv in installed_versions
                        )
                    )
                ):
                    should_resolve = True

        elif kind == "lifecycle" and _lifecycle_condition_cleared(
            ctx,
            entity_id,
            payload,
            dedupe_key,
            installed_versions,
        ):
            should_resolve = True

        if should_resolve:
            ctx.db.execute(
                "UPDATE alerts SET state = 'resolved' WHERE alert_id = ?",
                (alert_id,),
            )
            resolved_ids.append(alert_id)

    if resolved_ids:
        ctx.db.commit()
    return resolved_ids
