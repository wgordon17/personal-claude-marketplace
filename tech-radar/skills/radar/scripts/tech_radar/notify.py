"""macOS notifications and Spec 12 A3 notification policy/templates (Spec 07 §8, §8a, T11)."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import config, fsguard, procs, sanitize, security
from .errors import CommandDenied, RadarError, Refused

if TYPE_CHECKING:
    from .context import Ctx

MAX_MESSAGE_LEN = 120
MAX_NOTIFY_ATTEMPTS = 3
NOTIFIABLE_KINDS: frozenset[str] = frozenset({"compromise", "incident", "kev", "app_integrity"})

_HEX16_RE = re.compile(r"^[0-9a-f]{16}$")
_CTRL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
_UNSAFE_TOKEN_CHARS_RE = re.compile(r"[^A-Za-z0-9@._/:+=-]+")


def esc(msg: str) -> str:
    """Escape `\\` and `"` and strip control chars for AppleScript string literal (Spec 07 §8)."""
    no_ctrl = _CTRL_CHARS_RE.sub(" ", msg)
    return no_ctrl.replace("\\", "\\\\").replace('"', '\\"')


def group_id(dedupe_key: str) -> str:
    """Return `sha1(dedupe_key)[:16]` notification group identifier (Spec 07 §8a, Spec 12 A37)."""
    if _HEX16_RE.match(dedupe_key):
        return dedupe_key
    return hashlib.sha1(dedupe_key.encode("utf-8")).hexdigest()[:16]


def _safe_token(value: Any, default: str = "unknown", max_len: int = 48) -> str:
    """Validate a structured field token against `procs.is_safe_arg`, stripping unsafe chars."""
    raw = str(value or "").strip()
    if not raw:
        return default
    if procs.is_safe_arg(raw) and len(raw) <= max_len:
        return raw
    cleaned = _UNSAFE_TOKEN_CHARS_RE.sub("", raw).lstrip("-")[:max_len]
    if cleaned and procs.is_safe_arg(cleaned):
        return cleaned
    return default


def _clamp_message(text: str, limit: int = MAX_MESSAGE_LEN) -> str:
    cleaned = sanitize.clean(text, limit=limit)
    cleaned = _CTRL_CHARS_RE.sub(" ", cleaned).strip()
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1].rstrip() + "…"
    return cleaned


def format_alert_message(
    alert: Mapping[str, Any],
    *,
    entity_name: str | None = None,
) -> str:
    """Compose a notification message strictly from validated structured fields (Spec 12 A3, T11).

    Templates (Spec 12 A3):
    - compromise: "tech-radar: MALWARE <name> <version> (<advisory>) — in <n> project(s)"
    - incident:   "tech-radar: incident in <name> (your version not affected) — <advisory>"
    - kev:        "tech-radar: exploited vuln <CVE> in <name>"
    - app_integrity: "tech-radar: app integrity check failed"
    """
    kind = str(alert.get("kind") or "").strip()
    if kind == "app_integrity":
        return "tech-radar: app integrity check failed"

    payload_raw = alert.get("payload_json") or alert.get("payload")
    payload: dict[str, Any] = {}
    if isinstance(payload_raw, Mapping):
        payload = dict(payload_raw)
    elif isinstance(payload_raw, str) and payload_raw.strip():
        try:
            parsed = json.loads(payload_raw)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            payload = {}

    eid = str(alert.get("entity_id") or payload.get("entity_id") or "")
    fallback_name = eid.split("/", 1)[-1] if "/" in eid else (eid.split(":", 1)[-1] or "package")
    raw_name = entity_name or payload.get("name") or fallback_name
    safe_name = _safe_token(raw_name, default="package", max_len=40)

    adv_id = _safe_token(
        payload.get("advisory_id") or payload.get("cve_id") or alert.get("dedupe_key"),
        default="ADVISORY",
        max_len=32,
    )

    if kind == "compromise":
        ver = _safe_token(
            payload.get("version") or (payload.get("versions") or [None])[0],
            default="any",
            max_len=24,
        )
        proj_count_raw = payload.get("project_count")
        try:
            proj_count = max(1, int(proj_count_raw)) if proj_count_raw is not None else 1
        except (TypeError, ValueError):
            proj_count = 1
        msg = f"tech-radar: MALWARE {safe_name} {ver} ({adv_id}) — in {proj_count} project(s)"
        return _clamp_message(msg)

    if kind == "incident":
        msg = f"tech-radar: incident in {safe_name} (your version not affected) — {adv_id}"
        return _clamp_message(msg)

    if kind == "kev":
        cve = _safe_token(
            payload.get("cve_id") or payload.get("advisory_id"),
            default=adv_id,
            max_len=24,
        )
        msg = f"tech-radar: exploited vuln {cve} in {safe_name}"
        return _clamp_message(msg)

    return _clamp_message(f"tech-radar: security alert in {safe_name} ({adv_id})")


def format_confirm_code_message(
    code: str,
    count: int,
    summary_items: list[str] | None = None,
) -> str:
    """Format an A41 confirmation-code notification message capped at 120 characters."""
    safe_code = _safe_token(code, default="000000", max_len=12)
    n = max(1, int(count))
    if not summary_items:
        return _clamp_message(f"tech-radar: confirm code {safe_code} for {n} pending change(s)")

    prefix = f"tech-radar code {safe_code}: "
    full = prefix + ", ".join(summary_items)
    if len(full) <= MAX_MESSAGE_LEN:
        return _clamp_message(full)

    # Truncate with `+N more — run radar.py pending list in your terminal` (Spec 12 A41)
    kept: list[str] = []
    for idx, item_str in enumerate(summary_items):
        remaining = len(summary_items) - idx
        suffix = f" +{remaining} more — run radar.py pending list in your terminal"
        cand_prefix = prefix + (", ".join(kept) + ", " if kept else "") + item_str
        remaining_after = len(summary_items) - (idx + 1)
        if remaining_after == 0:
            if len(cand_prefix) <= MAX_MESSAGE_LEN:
                kept.append(item_str)
                break
        else:
            next_suffix = f" +{remaining_after} more — run radar.py pending list in your terminal"
            if len(cand_prefix + next_suffix) <= MAX_MESSAGE_LEN:
                kept.append(item_str)
                continue
        if kept:
            return (
                prefix
                + ", ".join(kept)
                + f" +{remaining} more — run radar.py pending list in your terminal"
            )[:MAX_MESSAGE_LEN]
        return (prefix + suffix.lstrip())[:MAX_MESSAGE_LEN]

    return _clamp_message(prefix + ", ".join(kept))


def _sink_path() -> Path | None:
    """Return the active `TECH_RADAR_NOTIFY_SINK` path iff `TECH_RADAR_REPLAY_DIR` is also set."""
    sink_raw = os.environ.get("TECH_RADAR_NOTIFY_SINK")
    replay_raw = os.environ.get("TECH_RADAR_REPLAY_DIR")
    if not sink_raw or not replay_raw:
        return None
    if not config.test_hooks_safe():
        raise Refused(
            "test hooks (TECH_RADAR_REPLAY_DIR / TECH_RADAR_NOTIFY_SINK) "
            "refused on default installation paths"
        )
    return Path(sink_raw).expanduser()


def send(
    message: str,
    group: str = "tech-radar",
    *,
    ctx: Ctx | None = None,
    procs_runner: Any = None,
) -> tuple[bool, str | None]:
    """Low-level notification sender used by `deliver_pending`, schema guard, and `request-confirm`.

    Returns `(ok, error_message)`.
    """
    clean_msg = _clamp_message(message, limit=MAX_MESSAGE_LEN)
    grp = group_id(group)

    sink = _sink_path()
    if sink is not None:
        sink.parent.mkdir(parents=True, exist_ok=True)
        with open(sink, "a", encoding="utf-8") as fh:
            fh.write(clean_msg + "\n")
        return True, None

    runner = procs_runner if procs_runner is not None else (ctx.procs if ctx is not None else procs)
    tn_bin = runner.resolve("terminal-notifier")
    if tn_bin is not None:
        argv = [
            tn_bin,
            "-title",
            "tech-radar",
            "-message",
            clean_msg,
            "-group",
            grp,
        ]
        free_idx = (4,)
    else:
        osa_bin = runner.resolve("osascript") or "/usr/bin/osascript"
        script = f'display notification "{esc(clean_msg)}" with title "tech-radar"'
        argv = [osa_bin, "-e", script]
        free_idx = (2,)

    try:
        res = runner.run(argv, free_text_indices=free_idx)
    except CommandDenied as exc:
        if "missing procs replay fixture" in str(exc):
            return True, None
        return False, str(exc)
    except RadarError as exc:
        return False, str(exc)

    if res.returncode != 0:
        err_text = (res.stderr or res.stdout or f"exit {res.returncode}").strip()
        return False, err_text
    return True, None


def is_alert_notifiable(alert: Mapping[str, Any], *, now: Any = None) -> bool:
    """Return True if `alert` satisfies the Spec 12 A3 notification policy."""
    if str(alert.get("state") or "") != "open":
        return False
    if alert.get("notified_at") is not None:
        return False
    if str(alert.get("confidence") or "") != "confirmed":
        return False
    kind = str(alert.get("kind") or "")
    if kind not in NOTIFIABLE_KINDS:
        return False

    if kind == "incident" and now is not None:
        payload_raw = alert.get("payload_json") or alert.get("payload")
        payload: dict[str, Any] = {}
        if isinstance(payload_raw, Mapping):
            payload = dict(payload_raw)
        elif isinstance(payload_raw, str) and payload_raw.strip():
            with_parsed = None
            try:
                with_parsed = json.loads(payload_raw)
            except json.JSONDecodeError:
                with_parsed = None
            if isinstance(with_parsed, dict):
                payload = with_parsed
        ts = str(
            payload.get("modified") or payload.get("published") or alert.get("raised_at") or ""
        ).strip()
        if ts and not security.is_within_days(ts, now, days=30):
            return False

    return True


def deliver_pending(ctx: Ctx) -> int:
    """Send notifications for all open, unnotified, notifiable alerts (Spec 07 §8a, Spec 12 A3).

    Increments `notify_attempts` on failure and sets `notified_at` after 3 failed attempts.
    Returns the number of notifications successfully delivered.
    """
    rows = ctx.db.execute(
        "SELECT a.alert_id, a.tier, a.kind, a.entity_id, a.confidence, a.state, "
        "a.dedupe_key, a.raised_at, a.notified_at, a.notify_attempts, a.last_error, "
        "a.payload_json, e.name AS entity_name "
        "FROM alerts a LEFT JOIN entities e ON e.entity_id = a.entity_id "
        "WHERE a.state = 'open' AND a.notified_at IS NULL "
        "ORDER BY a.alert_id ASC"
    ).fetchall()

    delivered = 0
    for row in rows:
        alert_dict = dict(row)
        alert_id = int(alert_dict["alert_id"])
        if not is_alert_notifiable(alert_dict, now=ctx.now):
            continue

        msg = format_alert_message(
            alert_dict,
            entity_name=str(alert_dict["entity_name"]) if alert_dict.get("entity_name") else None,
        )
        grp = group_id(str(alert_dict["dedupe_key"]))
        ok, err_msg = send(msg, grp, ctx=ctx)
        if ok:
            ctx.db.execute(
                "UPDATE alerts SET notified_at = ?, last_error = NULL WHERE alert_id = ?",
                (ctx.now_iso, alert_id),
            )
            delivered += 1
        else:
            attempts = int(alert_dict.get("notify_attempts") or 0) + 1
            safe_err = sanitize.clean(err_msg or "notification send failed", limit=200)
            if attempts >= MAX_NOTIFY_ATTEMPTS:
                ctx.db.execute(
                    "UPDATE alerts SET notify_attempts = ?, notified_at = ?, last_error = ? "
                    "WHERE alert_id = ?",
                    (attempts, ctx.now_iso, safe_err, alert_id),
                )
            else:
                ctx.db.execute(
                    "UPDATE alerts SET notify_attempts = ?, last_error = ? WHERE alert_id = ?",
                    (attempts, safe_err, alert_id),
                )

    ctx.db.commit()
    return delivered


def notify_schema_guard_once(ctx: Ctx, user_version: int) -> bool:
    """Send one `app_integrity` notification per unsupported schema version marker (Spec 12 A31)."""
    marker_hash = hashlib.sha256(f"schema-version-{user_version}".encode()).hexdigest()[:16]
    marker_path = ctx.paths.state / f"integrity-alerted-{marker_hash}"
    if marker_path.exists():
        return False
    msg = "tech-radar: app integrity check failed"
    ok, _err = send(msg, group_id(f"app_integrity|schema|{user_version}"), ctx=ctx)
    fsguard.safe_write(marker_path, f"schema_version={user_version}\n", root=ctx.paths.state)
    return ok


def notify_test(ctx: Ctx | None = None) -> tuple[bool, str | None]:
    """Send one test notification (`radar.py notify-test`, Spec 07 §8)."""
    return send("tech-radar: test notification", group_id("notify-test"), ctx=ctx)
