"""CLI parser, terminal-only gate, and subcommand dispatch for tech-radar."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from collections.abc import Sequence
from typing import Any

from . import (
    appsync,
    config,
    context,
    credentials,
    db,
    errors,
    exposure,
    features,
    fsguard,
    http,
    inventory,
    launchd,
    lifecycle,
    lock,
    notify,
    procs,
    profile,
    redact,
    runlog,
    sanitize,
    security,
    sources,
)

_CORE_MODULES = (
    appsync,
    context,
    credentials,
    db,
    errors,
    exposure,
    features,
    fsguard,
    http,
    inventory,
    launchd,
    lifecycle,
    lock,
    notify,
    procs,
    profile,
    redact,
    runlog,
    sanitize,
    security,
    sources,
)

RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z-(daily|weekly|monthly)$")
DECIDE_VERBS = ("adopt", "trial", "assess", "hold", "reject", "snooze")
FEEDBACK_VERBS = ("useful", "not_useful", "acted")
ALERT_VERBS = ("ack", "resolve", "false-positive")
LAUNCHD_VERBS = ("install", "uninstall", "status")
PRIVATE_SCOPE_VERBS = ("add", "remove", "list")
SOURCES_VERBS = ("list", "check")


def emit(status: dict[str, Any], code: int = 0) -> int:
    """Print exactly one JSON status line to stdout and return the exit code."""
    sys.stdout.write(json.dumps(status, separators=(",", ":"), sort_keys=False) + "\n")
    sys.stdout.flush()
    return code


def _format_terminal_command(raw_argv: Sequence[str]) -> str:
    tokens = list(raw_argv)
    has_channel_cli = False
    i = 0
    cleaned: list[str] = []
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--channel":
            if i + 1 < len(tokens) and tokens[i + 1] == "cli":
                has_channel_cli = True
                cleaned.extend(["--channel", "cli"])
            i += 2
            continue
        if tok.startswith("--channel="):
            if tok == "--channel=cli":
                has_channel_cli = True
                cleaned.extend(["--channel", "cli"])
            i += 1
            continue
        cleaned.append(tok)
        i += 1
    if not has_channel_cli:
        cleaned.extend(["--channel", "cli"])
    joined = " ".join(shlex.quote(part) for part in cleaned)
    return f"radar.py {joined}" if joined else "radar.py --channel cli"


def require_terminal(args: argparse.Namespace, raw_argv: Sequence[str] | None = None) -> int | None:
    """Enforce the A41 terminal-only rule (--channel cli + interactive TTY on stdin and stdout).

    Returns None when allowed, or exits/returns exit code 4 after emitting the refusal JSON.
    No environment variable can bypass this check.
    """
    argv_list = list(raw_argv) if raw_argv is not None else sys.argv[1:]
    is_tty = bool(sys.stdin.isatty() and sys.stdout.isatty())
    channel = getattr(args, "channel", None)
    if channel == "cli" and is_tty:
        return None
    cmd = _format_terminal_command(argv_list)
    msg = (
        "terminal-only command requires --channel cli and an interactive TTY; "
        f"run in your terminal: {cmd}"
    )
    return emit(
        {
            "ok": False,
            "error": msg,
            "command": cmd,
        },
        4,
    )


def _not_implemented(_args: argparse.Namespace) -> int:
    return emit({"ok": False, "error": "not implemented"}, 1)


def _terminal_not_implemented(args: argparse.Namespace, raw_argv: Sequence[str]) -> int:
    refused = require_terminal(args, raw_argv)
    if refused is not None:
        return refused
    return _not_implemented(args)


def _handle_taste(args: argparse.Namespace, raw_argv: Sequence[str]) -> int:
    if args.taste_cmd == "apply":
        return _terminal_not_implemented(args, raw_argv)
    return _not_implemented(args)


def _handle_app(args: argparse.Namespace, raw_argv: Sequence[str]) -> int:
    if getattr(args, "from_path", None) is not None or getattr(args, "channel", None) is not None:
        refused = require_terminal(args, raw_argv)
        if refused is not None:
            return refused
    try:
        ctx = context.build()
        res = appsync.sync(ctx, from_path=getattr(args, "from_path", None))
    except errors.RadarError as exc:
        return emit({"ok": False, "error": str(exc)}, exc.exit_code)

    if not res.ok:
        code = 4 if res.status == "refused" else 1
        return emit(
            {
                "ok": False,
                "status": res.status,
                "error": res.reason or "app sync failed",
                "alert_id": res.alert_id,
                "warning": res.coverage_warning,
            },
            code,
        )
    return emit(
        {
            "ok": True,
            "status": res.status,
            "manifest_sha": res.manifest_sha,
            "tree_sha": res.tree_sha,
            "warning": res.coverage_warning,
        },
        0,
    )


def _handle_launchd(args: argparse.Namespace, raw_argv: Sequence[str]) -> int:
    refused = require_terminal(args, raw_argv)
    if refused is not None:
        return refused
    action = str(getattr(args, "action", "status"))
    try:
        ctx = context.build()
        if action == "install":
            return emit(launchd.install(ctx), 0)
        if action == "uninstall":
            return emit(launchd.uninstall(ctx), 0)
        return emit(launchd.status(ctx), 0)
    except errors.RadarError as exc:
        return emit({"ok": False, "error": str(exc)}, exc.exit_code)


def _handle_notify_test(_args: argparse.Namespace, _raw_argv: Sequence[str]) -> int:
    try:
        ctx = context.build()
        ok, err_msg = notify.notify_test(ctx)
    except errors.RadarError as exc:
        return emit({"ok": False, "error": str(exc)}, exc.exit_code)
    if not ok:
        return emit({"ok": False, "error": err_msg or "notify-test failed"}, 1)
    return emit({"ok": True, "notified": True}, 0)


def _handle_hourly(_args: argparse.Namespace, _raw_argv: Sequence[str]) -> int:
    try:
        ctx = context.build()
        user_ver = db.get_user_version(ctx.db)
        if user_ver > db.SCHEMA_VERSION:
            notify.notify_schema_guard_once(ctx, user_ver)
            return emit(
                {
                    "ok": False,
                    "error": f"schema version {user_ver} exceeds supported {db.SCHEMA_VERSION}",
                },
                4,
            )

        hourly_lock = lock.acquire(
            "hourly",
            mode="hourly",
            state_dir=ctx.paths.state,
            now=ctx.now,
        )
        if hourly_lock is None:
            return emit({"ok": True, "skipped": "hourly in progress"}, 0)

        try:
            usage_cnt = int(
                ctx.db.execute(
                    "SELECT COUNT(*) FROM usages WHERE context NOT LIKE 'interest:%'"
                ).fetchone()[0]
            )
            if usage_cnt == 0:
                return emit({"ok": True, "skipped": "no inventory yet"}, 0)

            run_id = runlog.start_run(ctx, mode="hourly", requested_mode="hourly")
            refreshed = inventory.projects.refresh_manifests(ctx)
            hourly_specs = sources.by_cadence("hourly")
            source_results = [sources.run_source(spec, ctx) for spec in hourly_specs]
            new_alerts = security.evaluate(ctx, source_results, mode="hourly")
            notified = notify.deliver_pending(ctx)
            counts = {
                "refreshed": len(refreshed),
                "sources_run": len(source_results),
                "new_alerts": len(new_alerts),
                "notified": notified,
            }
            db.kv_set(ctx.db, "last_hourly_at", ctx.now_iso)
            runlog.finish_run(ctx, run_id, "ok", counts=counts)
            return emit(
                {
                    "ok": True,
                    "run_id": run_id,
                    **counts,
                },
                0,
            )
        finally:
            lock.release(hourly_lock)
    except errors.RadarError as exc:
        return emit({"ok": False, "error": str(exc)}, exc.exit_code)


def _handle_collect(args: argparse.Namespace, _raw_argv: Sequence[str]) -> int:
    run_id = getattr(args, "run_id", None)
    if run_id is not None and not RUN_ID_RE.match(str(run_id)):
        return emit(
            {"ok": False, "error": f"invalid run_id: {run_id}"},
            2,
        )
    inv_only = bool(getattr(args, "inventory_only", False))
    try:
        ctx = context.build(run_id=run_id)
        if run_id is not None and not inv_only:
            run_row = ctx.db.execute(
                "SELECT 1 FROM runs WHERE run_id = ?",
                (str(run_id),),
            ).fetchone()
            if run_row is None and not procs.is_replay_mode():
                return emit(
                    {"ok": False, "error": f"run_id not found: {run_id}"},
                    2,
                )
        inv_res = inventory.collect(ctx)
        if inv_only:
            return emit(
                {
                    "ok": True,
                    "run_id": run_id,
                    "inventory_only": True,
                    "added": len(inv_res.added),
                    "removed": len(inv_res.removed),
                    "counts": inv_res.counts,
                },
                0,
            )

        was_baseline = security.is_baseline_collect(ctx)
        source_results = sources.run_collect_sources(ctx)
        sec_alerts = security.evaluate(ctx, source_results, mode="daily")
        life_alerts = lifecycle.evaluate(ctx, source_results)
        feat_out = features.evaluate(ctx, source_results)
        if was_baseline and source_results:
            db.kv_set(ctx.db, "baseline_done", ctx.now_iso)
        notified = notify.deliver_pending(ctx)
    except errors.RadarError as exc:
        return emit({"ok": False, "error": str(exc)}, exc.exit_code)

    return emit(
        {
            "ok": True,
            "run_id": run_id,
            "inventory_only": False,
            "added": len(inv_res.added),
            "removed": len(inv_res.removed),
            "sources_run": len(source_results),
            "security_alerts": len(sec_alerts),
            "lifecycle_alerts": len(life_alerts),
            "feature_signals": len(feat_out["features"]),
            "pricing_signals": len(feat_out["pricing"]),
            "notified": notified,
            "counts": inv_res.counts,
        },
        0,
    )


def _handle_exposure(args: argparse.Namespace, _raw_argv: Sequence[str]) -> int:
    show_id = getattr(args, "show_alert_id", None)
    purl = getattr(args, "purl", None)
    raw_versions = getattr(args, "versions", None)
    try:
        ctx = context.build()
        if show_id is not None:
            return emit(exposure.show(ctx, show_id), 0)
        if not purl:
            return emit({"ok": False, "error": "exposure requires PURL or --show ALERT_ID"}, 2)
        ver_list = (
            [v.strip() for v in str(raw_versions).split(",") if v.strip()] if raw_versions else []
        )
        rep = exposure.check(ctx, str(purl), versions=ver_list)
        return emit(
            {
                "ok": True,
                "purl": rep.purl,
                "versions": rep.versions,
                "report_path": str(rep.report_path),
                "commits": len(rep.commits),
                "pth_files": len(rep.pth_files),
            },
            0,
        )
    except errors.RadarError as exc:
        return emit({"ok": False, "error": str(exc)}, exc.exit_code)


def _handle_private_scopes(args: argparse.Namespace, raw_argv: Sequence[str]) -> int:
    refused = require_terminal(args, raw_argv)
    if refused is not None:
        return refused
    try:
        ctx = context.build()
        current = profile.load_private_scopes(ctx.db)
        action = str(getattr(args, "action", "list"))
        incoming = [str(s) for s in (getattr(args, "scopes", None) or []) if str(s).strip()]
        if action == "add":
            updated = profile.save_private_scopes(ctx.db, [*current, *incoming])
        elif action == "remove":
            remove_set = {s.strip() for s in incoming}
            kept = [s for s in current if s not in remove_set]
            updated = profile.save_private_scopes(ctx.db, kept)
        else:
            updated = current
        return emit({"ok": True, "action": action, "scopes": updated}, 0)
    except errors.RadarError as exc:
        return emit({"ok": False, "error": str(exc)}, exc.exit_code)


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for all Spec 01 CLI subcommands."""
    epilog = (
        "Subcommands:\n"
        "  doctor\n"
        "  bootstrap init | bootstrap apply | bootstrap lockdown (terminal-only)\n"
        "  credentials status | credentials guide NAME\n"
        "  plan-run --requested daily [--now ISO] [--thread-id ID]\n"
        "  collect --run ID | collect --inventory-only\n"
        "  enrich --run ID\n"
        "  verdict apply --run ID\n"
        "  render --run ID\n"
        "  decide ITEM VERB [--ttl 90d] [--reason TEXT] [--wake JSON] --channel cli\n"
        "  decide-pending ITEM VERB [--ttl 90d] [--reason TEXT] [--wake JSON]\n"
        "  feedback ITEM useful|not_useful|acted\n"
        "  taste diff | taste propose | taste apply --channel cli (apply is terminal-only)\n"
        "  request-confirm [--run ID]\n"
        "  pending list --channel cli (terminal-only)\n"
        "  profile private-scopes add|remove|list SCOPE... --channel cli (terminal-only)\n"
        "  confirm CODE\n"
        "  decisions apply --channel cli (terminal-only)\n"
        "  alert ack|resolve|false-positive ALERT_ID --channel cli (terminal-only)\n"
        "  exposure PURL --versions V[,V] | exposure --show ALERT_ID\n"
        "  hourly\n"
        "  launchd install|uninstall|status --channel cli (terminal-only)\n"
        "  app sync [--from S --channel cli]\n"
        "  notify-test\n"
        "  sources list | sources check [--live]\n"
        "  search QUERY\n"
        "  usage record --run ID --json FILE\n"
    )
    parser = argparse.ArgumentParser(
        prog="radar.py",
        description="Personal tech radar CLI.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # doctor
    p_doctor = sub.add_parser("doctor", help="Check preconditions and environment health")
    p_doctor.set_defaults(_handler=lambda a, _raw: _not_implemented(a))

    # bootstrap init | apply | lockdown (all terminal-only)
    p_boot = sub.add_parser(
        "bootstrap",
        help="First-run setup: bootstrap init, bootstrap apply, bootstrap lockdown (terminal-only)",
    )
    boot_sub = p_boot.add_subparsers(dest="bootstrap_cmd", required=True)
    p_boot_init = boot_sub.add_parser("init", help="Initialize dirs, DB, app copy, and inventory")
    p_boot_init.add_argument("--channel", default=None)
    p_boot_init.set_defaults(_handler=_terminal_not_implemented)

    p_boot_apply = boot_sub.add_parser("apply", help="Ingest interview answers JSON")
    p_boot_apply.add_argument("--answers", required=True, help="Path to answers.json")
    p_boot_apply.add_argument("--channel", default=None)
    p_boot_apply.set_defaults(_handler=_terminal_not_implemented)

    p_boot_lock = boot_sub.add_parser("lockdown", help="Write run-project settings.json")
    p_boot_lock.add_argument("--channel", default=None)
    p_boot_lock.set_defaults(_handler=_terminal_not_implemented)

    # credentials status | guide NAME
    p_creds = sub.add_parser(
        "credentials",
        help="Keychain status and setup guide: credentials status, credentials guide NAME",
    )
    creds_sub = p_creds.add_subparsers(dest="credentials_cmd", required=True)
    p_creds_status = creds_sub.add_parser("status", help="Show Keychain item availability")
    p_creds_status.set_defaults(_handler=lambda a, _raw: _not_implemented(a))
    p_creds_guide = creds_sub.add_parser("guide", help="Print setup guide for a credential")
    p_creds_guide.add_argument("name", help="Credential account name")
    p_creds_guide.set_defaults(_handler=lambda a, _raw: _not_implemented(a))

    # plan-run
    p_plan = sub.add_parser("plan-run", help="Sync app, stage DECISIONS.md, acquire lock, plan run")
    p_plan.add_argument(
        "--requested",
        default="daily",
        choices=("daily", "weekly", "monthly"),
        help="Requested cadence",
    )
    p_plan.add_argument("--now", default=None, help="ISO timestamp override")
    p_plan.add_argument("--thread-id", default=None, help="T3 thread id")
    p_plan.set_defaults(_handler=lambda a, _raw: _not_implemented(a))

    # collect
    p_collect = sub.add_parser(
        "collect",
        help="Collect inventory, due sources, gates, scores, and brief files",
    )
    collect_group = p_collect.add_mutually_exclusive_group(required=True)
    collect_group.add_argument("--run", dest="run_id", help="Run identifier")
    collect_group.add_argument(
        "--inventory-only",
        action="store_true",
        help="Run inventory and item promotion only",
    )
    p_collect.set_defaults(_handler=_handle_collect)

    # enrich
    p_enrich = sub.add_parser(
        "enrich", help="Verify shortlist proposals and fetch per-item metrics"
    )
    p_enrich.add_argument("--run", dest="run_id", required=True, help="Run identifier")
    p_enrich.set_defaults(_handler=lambda a, _raw: _not_implemented(a))

    # verdict apply
    p_verdict = sub.add_parser("verdict", help="Validate and apply LLM verdicts: verdict apply")
    verdict_sub = p_verdict.add_subparsers(dest="verdict_cmd", required=True)
    p_verdict_apply = verdict_sub.add_parser(
        "apply", help="Validate and apply runs/ID/verdicts.json"
    )
    p_verdict_apply.add_argument("--run", dest="run_id", required=True, help="Run identifier")
    p_verdict_apply.set_defaults(_handler=lambda a, _raw: _not_implemented(a))

    # render
    p_render = sub.add_parser("render", help="Render digest, projections, BYOR, HTML, and snapshot")
    p_render.add_argument("--run", dest="run_id", required=True, help="Run identifier")
    p_render.set_defaults(_handler=lambda a, _raw: _not_implemented(a))

    # decide (terminal-only)
    p_decide = sub.add_parser("decide", help="Apply a decision directly (terminal-only)")
    p_decide.add_argument("item", help="Item identifier (e.g. R-0001)")
    p_decide.add_argument("verb", choices=DECIDE_VERBS, help="Decision verb")
    p_decide.add_argument("--ttl", default=None, help="Snooze/hold TTL (e.g. 90d)")
    p_decide.add_argument("--reason", default=None, help="Decision rationale")
    p_decide.add_argument("--wake", default=None, help="Wake condition JSON")
    p_decide.add_argument("--channel", default=None, help="Must be 'cli' from an interactive TTY")
    p_decide.set_defaults(_handler=_terminal_not_implemented)

    # decide-pending
    p_decide_pending = sub.add_parser("decide-pending", help="Stage a decision from a thread reply")
    p_decide_pending.add_argument("item", help="Item identifier (e.g. R-0001)")
    p_decide_pending.add_argument("verb", choices=DECIDE_VERBS, help="Decision verb")
    p_decide_pending.add_argument("--ttl", default=None, help="Snooze/hold TTL (e.g. 90d)")
    p_decide_pending.add_argument("--reason", default=None, help="Decision rationale")
    p_decide_pending.add_argument("--wake", default=None, help="Wake condition JSON")
    p_decide_pending.set_defaults(_handler=lambda a, _raw: _not_implemented(a))

    # feedback
    p_feedback = sub.add_parser("feedback", help="Stage digest-item feedback")
    p_feedback.add_argument("item", help="Item identifier")
    p_feedback.add_argument("verdict", choices=FEEDBACK_VERBS, help="Feedback value")
    p_feedback.set_defaults(_handler=lambda a, _raw: _not_implemented(a))

    # taste diff | propose | apply
    p_taste = sub.add_parser(
        "taste",
        help="Manage taste profile updates: taste diff, taste propose, taste apply",
    )
    taste_sub = p_taste.add_subparsers(dest="taste_cmd", required=True)
    taste_sub.add_parser("diff", help="Diff TASTE_PROFILE.proposed.md vs TASTE_PROFILE.md")
    taste_sub.add_parser("propose", help="Stage applying TASTE_PROFILE.proposed.md")
    p_taste_apply = taste_sub.add_parser(
        "apply",
        help="Apply TASTE_PROFILE.proposed.md directly (terminal-only)",
    )
    p_taste_apply.add_argument("--channel", default=None)
    p_taste.set_defaults(_handler=_handle_taste)

    # request-confirm
    p_req_confirm = sub.add_parser(
        "request-confirm",
        help="Batch staged entries and send confirmation code via macOS notification",
    )
    p_req_confirm.add_argument("--run", dest="run_id", default=None, help="Optional run identifier")
    p_req_confirm.set_defaults(_handler=lambda a, _raw: _not_implemented(a))

    # pending list (terminal-only)
    p_pending = sub.add_parser(
        "pending", help="Inspect staged changes: pending list (terminal-only)"
    )
    pending_sub = p_pending.add_subparsers(dest="pending_cmd", required=True)
    p_pending_list = pending_sub.add_parser("list", help="Print full staged batch (terminal-only)")
    p_pending_list.add_argument("--channel", default=None)
    p_pending_list.set_defaults(_handler=_terminal_not_implemented)

    # profile private-scopes add|remove|list (terminal-only)
    p_profile = sub.add_parser(
        "profile",
        help="Manage profile settings: profile private-scopes add|remove|list (terminal-only)",
    )
    profile_sub = p_profile.add_subparsers(dest="profile_cmd", required=True)
    p_priv = profile_sub.add_parser(
        "private-scopes",
        help="Manage private orgs/scopes/index names in radar.db",
    )
    p_priv.add_argument("action", choices=PRIVATE_SCOPE_VERBS, help="Action to perform")
    p_priv.add_argument("scopes", nargs="*", help="Scope values")
    p_priv.add_argument("--channel", default=None)
    p_priv.set_defaults(_handler=_handle_private_scopes)

    # confirm CODE
    p_confirm = sub.add_parser("confirm", help="Apply a staged batch using a confirmation code")
    p_confirm.add_argument("code", help="6-character confirmation code")
    p_confirm.set_defaults(_handler=lambda a, _raw: _not_implemented(a))

    # decisions apply (terminal-only)
    p_decisions = sub.add_parser(
        "decisions",
        help="Apply staged DECISIONS.md edits: decisions apply (terminal-only)",
    )
    decisions_sub = p_decisions.add_subparsers(dest="decisions_cmd", required=True)
    p_dec_apply = decisions_sub.add_parser("apply", help="Apply staged DECISIONS.md edits")
    p_dec_apply.add_argument("--channel", default=None)
    p_dec_apply.set_defaults(_handler=_terminal_not_implemented)

    # alert ack|resolve|false-positive ALERT_ID (terminal-only)
    p_alert = sub.add_parser(
        "alert",
        help="Update alert lifecycle: alert ack|resolve|false-positive ALERT_ID (terminal-only)",
    )
    p_alert.add_argument("action", choices=ALERT_VERBS, help="Alert transition")
    p_alert.add_argument("alert_id", help="Alert identifier")
    p_alert.add_argument("--channel", default=None)
    p_alert.set_defaults(_handler=_terminal_not_implemented)

    # exposure
    p_exposure = sub.add_parser(
        "exposure",
        help="Run SEC-03 exposure check or show an alert exposure report",
    )
    p_exposure.add_argument("purl", nargs="?", default=None, help="Package PURL")
    p_exposure.add_argument("--versions", default=None, help="Comma-separated bad versions")
    p_exposure.add_argument(
        "--show", dest="show_alert_id", default=None, help="Alert ID to display"
    )
    p_exposure.set_defaults(_handler=_handle_exposure)

    # hourly
    p_hourly = sub.add_parser("hourly", help="Run LLM-free hourly compromise check")
    p_hourly.set_defaults(_handler=_handle_hourly)

    # launchd install|uninstall|status (terminal-only)
    p_launchd = sub.add_parser(
        "launchd",
        help="Manage hourly launchd job: launchd install|uninstall|status (terminal-only)",
    )
    p_launchd.add_argument("action", choices=LAUNCHD_VERBS, help="launchd action")
    p_launchd.add_argument("--channel", default=None)
    p_launchd.set_defaults(_handler=_handle_launchd)

    # app sync
    p_app = sub.add_parser("app", help="Manage verified app copy: app sync")
    app_sub = p_app.add_subparsers(dest="app_cmd", required=True)
    p_app_sync = app_sub.add_parser("sync", help="Sync and verify the scripts tree into state/app")
    p_app_sync.add_argument("--from", dest="from_path", default=None, help="Dev source scripts dir")
    p_app_sync.add_argument("--channel", default=None)
    p_app_sync.set_defaults(_handler=_handle_app)

    # notify-test
    p_notify = sub.add_parser("notify-test", help="Fire one test macOS notification")
    p_notify.set_defaults(_handler=_handle_notify_test)

    # sources list|check [--live]
    p_sources = sub.add_parser("sources", help="List or check sources: sources list, sources check")
    p_sources.add_argument("action", choices=SOURCES_VERBS, help="list or check")
    p_sources.add_argument("--live", action="store_true", help="Run each source live once")
    p_sources.set_defaults(_handler=lambda a, _raw: _not_implemented(a))

    # search QUERY
    p_search = sub.add_parser("search", help="FTS5 search over signals and digests")
    p_search.add_argument("query", help="FTS5 query string")
    p_search.set_defaults(_handler=lambda a, _raw: _not_implemented(a))

    # usage record --run ID --json FILE
    p_usage = sub.add_parser("usage", help="Record run token usage: usage record")
    usage_sub = p_usage.add_subparsers(dest="usage_cmd", required=True)
    p_usage_rec = usage_sub.add_parser("record", help="Record usage JSON for a run")
    p_usage_rec.add_argument("--run", dest="run_id", required=True, help="Run identifier")
    p_usage_rec.add_argument("--json", dest="json_file", required=True, help="Path to usage JSON")
    p_usage_rec.set_defaults(_handler=lambda a, _raw: _not_implemented(a))

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and dispatch to the selected subcommand handler."""
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    parser = build_parser()
    try:
        args = parser.parse_args(raw_argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        return code

    if not config.test_hooks_safe():
        hook_err = (
            "test hooks (TECH_RADAR_REPLAY_DIR / TECH_RADAR_NOTIFY_SINK) "
            "refused on default installation paths"
        )
        return emit({"ok": False, "error": hook_err}, 4)

    config.paths(ensure_dirs=True)
    handler = getattr(args, "_handler", None)
    if handler is None:
        return emit({"ok": False, "error": "no subcommand handler"}, 1)
    return int(handler(args, raw_argv))
