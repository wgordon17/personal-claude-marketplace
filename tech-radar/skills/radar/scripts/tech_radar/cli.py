"""CLI parser, terminal-only gate, and subcommand dispatch for tech-radar."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from collections.abc import Sequence
from typing import Any

from . import (
    alerts,
    appsync,
    bootstrap,
    brief,
    config,
    context,
    credentials,
    db,
    decisions,
    doctor,
    errors,
    exposure,
    features,
    inventory,
    items,
    launchd,
    lifecycle,
    lock,
    notify,
    pending,
    profile,
    render,
    runlog,
    runs,
    security,
    sources,
    usage,
    verdict,
)

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


def _emit_radar_error(exc: errors.RadarError) -> int:
    payload: dict[str, Any] = {"ok": False, "error": str(exc)}
    if isinstance(exc, errors.ValidationError) and exc.errors:
        payload["errors"] = exc.errors
    return emit(payload, exc.exit_code)


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


def _handle_doctor(_args: argparse.Namespace, _raw_argv: Sequence[str]) -> int:
    try:
        ctx = context.build()
        res = doctor.run_doctor(ctx)
        code = 0 if res.get("ok") else 4
        return emit(res, code)
    except errors.RadarError as exc:
        return _emit_radar_error(exc)


def _handle_bootstrap(args: argparse.Namespace, raw_argv: Sequence[str]) -> int:
    refused = require_terminal(args, raw_argv)
    if refused is not None:
        return refused
    subcmd = str(getattr(args, "bootstrap_cmd", "init"))
    try:
        ctx = context.build()
        if subcmd == "init":
            return emit(bootstrap.init(ctx), 0)
        if subcmd == "apply":
            answers_path = str(getattr(args, "answers", "") or "")
            return emit(bootstrap.apply_answers(ctx, answers_path), 0)
        return emit(bootstrap.lockdown(ctx), 0)
    except errors.RadarError as exc:
        return _emit_radar_error(exc)


def _handle_plan_run(args: argparse.Namespace, _raw_argv: Sequence[str]) -> int:
    requested = str(getattr(args, "requested", "daily") or "daily")
    now_arg = getattr(args, "now", None)
    thread_id = getattr(args, "thread_id", None)
    try:
        ctx = context.build(now=now_arg)
        res = runs.plan_run(ctx, requested=requested, thread_id=thread_id)
    except errors.RadarError as exc:
        return _emit_radar_error(exc)

    exit_code = int(res.pop("_exit_code", 0 if res.get("ok") else 4))
    return emit(res, exit_code)


def _handle_collect(args: argparse.Namespace, _raw_argv: Sequence[str]) -> int:
    run_id = getattr(args, "run_id", None)
    inv_only = bool(getattr(args, "inventory_only", False))
    try:
        ctx = context.build(run_id=str(run_id) if run_id is not None else None)
        if not inv_only:
            valid_run_id = runs.validate_run_id(ctx, str(run_id) if run_id is not None else None)
            ctx.run_id = valid_run_id
        else:
            valid_run_id = None

        inv_res = inventory.collect(ctx)
        if inv_only:
            promo_stats = items.promote(ctx, inventory_only=True, fetch_gates=False)
            return emit(
                {
                    "ok": True,
                    "run_id": None,
                    "inventory_only": True,
                    "added": len(inv_res.added),
                    "removed": len(inv_res.removed),
                    "in_use_created": promo_stats["in_use_created"],
                    "counts": inv_res.counts,
                },
                0,
            )

        assert valid_run_id is not None
        was_baseline = security.is_baseline_collect(ctx)
        source_results = sources.run_collect_sources(ctx)
        sec_alerts = security.evaluate(ctx, source_results, mode="daily")
        life_alerts = lifecycle.evaluate(ctx, source_results)
        alerts.auto_resolve(ctx)
        feat_out = features.evaluate(ctx, source_results)
        promo_stats = items.promote(ctx, source_results, inventory_only=False, fetch_gates=True)
        brief_idx = brief.generate(ctx, valid_run_id)
        if was_baseline and source_results:
            db.kv_set(ctx.db, "baseline_done", ctx.now_iso)
        notified = notify.deliver_pending(ctx)
    except errors.RadarError as exc:
        return _emit_radar_error(exc)

    degraded = any(not r.ok and not r.skipped_reason for r in source_results)
    return emit(
        {
            "ok": True,
            "run_id": valid_run_id,
            "inventory_only": False,
            "added": len(inv_res.added),
            "removed": len(inv_res.removed),
            "sources_run": len(source_results),
            "security_alerts": len(sec_alerts),
            "lifecycle_alerts": len(life_alerts),
            "feature_signals": len(feat_out["features"]),
            "pricing_signals": len(feat_out["pricing"]),
            "candidates_promoted": promo_stats["candidates_promoted"],
            "deferred": promo_stats["deferred"],
            "brief_sections": len(brief_idx.get("sections", {})),
            "notified": notified,
            "counts": inv_res.counts,
        },
        3 if degraded else 0,
    )


def _handle_enrich(args: argparse.Namespace, _raw_argv: Sequence[str]) -> int:
    raw_run_id = getattr(args, "run_id", None)
    try:
        ctx = context.build(run_id=str(raw_run_id) if raw_run_id else None)
        valid_run_id = runs.validate_run_id(ctx, str(raw_run_id) if raw_run_id else None)
        res = brief.enrich(ctx, valid_run_id)
        return emit(res, 0)
    except errors.RadarError as exc:
        return _emit_radar_error(exc)


def _handle_verdict_apply(args: argparse.Namespace, _raw_argv: Sequence[str]) -> int:
    raw_run_id = getattr(args, "run_id", None)
    try:
        ctx = context.build(run_id=str(raw_run_id) if raw_run_id else None)
        valid_run_id = runs.validate_run_id(ctx, str(raw_run_id) if raw_run_id else None)
        res = verdict.apply_verdicts(ctx, valid_run_id)
        return emit(res, 0)
    except errors.RadarError as exc:
        return _emit_radar_error(exc)


def _handle_render(args: argparse.Namespace, _raw_argv: Sequence[str]) -> int:
    raw_run_id = getattr(args, "run_id", None)
    try:
        ctx = context.build(run_id=str(raw_run_id) if raw_run_id else None)
        valid_run_id = runs.validate_run_id(ctx, str(raw_run_id) if raw_run_id else None)
        res = render.render_run(ctx, valid_run_id)
        return emit(res, 0)
    except errors.RadarError as exc:
        return _emit_radar_error(exc)


def _handle_decide(args: argparse.Namespace, raw_argv: Sequence[str]) -> int:
    refused = require_terminal(args, raw_argv)
    if refused is not None:
        return refused
    try:
        ctx = context.build()
        res = decisions.apply(
            ctx,
            str(args.item),
            str(args.verb),
            ttl=getattr(args, "ttl", None),
            reason=getattr(args, "reason", None),
            wake=getattr(args, "wake", None),
            channel="cli",
        )
        return emit({"ok": True, **res}, 0)
    except errors.RadarError as exc:
        return _emit_radar_error(exc)


def _handle_decide_pending(args: argparse.Namespace, _raw_argv: Sequence[str]) -> int:
    try:
        ctx = context.build()
        pid = pending.stage_decision(
            ctx,
            str(args.item),
            str(args.verb),
            ttl=getattr(args, "ttl", None),
            reason=getattr(args, "reason", None),
            wake=getattr(args, "wake", None),
            channel="thread",
        )
        disp_alias, vis, _is_priv = decisions.alias_for_item(ctx, str(args.item))
        return emit(
            {
                "ok": True,
                "pending_id": pid,
                "item": disp_alias,
                "visibility": vis,
                "verb": str(args.verb),
                "status": "staged",
            },
            0,
        )
    except errors.RadarError as exc:
        return _emit_radar_error(exc)


def _handle_feedback(args: argparse.Namespace, _raw_argv: Sequence[str]) -> int:
    try:
        ctx = context.build()
        pid = pending.stage_feedback(
            ctx,
            str(args.item),
            str(args.verdict),
            channel="thread",
        )
        return emit(
            {
                "ok": True,
                "pending_id": pid,
                "item": str(args.item),
                "feedback": str(args.verdict),
                "status": "staged",
            },
            0,
        )
    except errors.RadarError as exc:
        return _emit_radar_error(exc)


def _handle_taste(args: argparse.Namespace, raw_argv: Sequence[str]) -> int:
    subcmd = str(getattr(args, "taste_cmd", "diff"))
    if subcmd == "apply":
        refused = require_terminal(args, raw_argv)
        if refused is not None:
            return refused
    try:
        ctx = context.build()
        if subcmd == "diff":
            return emit(pending.taste_diff(ctx), 0)
        if subcmd == "propose":
            return emit(pending.stage_taste(ctx, channel="thread"), 0)
        return emit(pending.taste_apply_cli(ctx), 0)
    except errors.RadarError as exc:
        return _emit_radar_error(exc)


def _handle_request_confirm(args: argparse.Namespace, _raw_argv: Sequence[str]) -> int:
    raw_run_id = getattr(args, "run_id", None)
    try:
        ctx = context.build(run_id=str(raw_run_id) if raw_run_id else None)
        if raw_run_id is not None:
            runs.validate_run_id(ctx, str(raw_run_id))
        res = pending.request_confirm(ctx)
        return emit(res, 0)
    except errors.RadarError as exc:
        return _emit_radar_error(exc)


def _handle_confirm(args: argparse.Namespace, _raw_argv: Sequence[str]) -> int:
    try:
        ctx = context.build()
        res = pending.confirm(ctx, str(args.code))
        return emit(res, 0)
    except errors.RadarError as exc:
        return _emit_radar_error(exc)


def _handle_pending_list(args: argparse.Namespace, raw_argv: Sequence[str]) -> int:
    refused = require_terminal(args, raw_argv)
    if refused is not None:
        return refused
    try:
        ctx = context.build()
        res = pending.list_pending_cli(ctx)
        return emit(res, 0)
    except errors.RadarError as exc:
        return _emit_radar_error(exc)


def _handle_decisions_apply(args: argparse.Namespace, raw_argv: Sequence[str]) -> int:
    refused = require_terminal(args, raw_argv)
    if refused is not None:
        return refused
    try:
        ctx = context.build()
        res = decisions.apply_decisions_md_cli(ctx)
        return emit(res, 0)
    except errors.RadarError as exc:
        return _emit_radar_error(exc)


def _handle_alert(args: argparse.Namespace, raw_argv: Sequence[str]) -> int:
    refused = require_terminal(args, raw_argv)
    if refused is not None:
        return refused
    try:
        ctx = context.build()
        res = alerts.transition(ctx, str(args.action), str(args.alert_id))
        return emit(res, 0)
    except errors.RadarError as exc:
        return _emit_radar_error(exc)


def _handle_search(args: argparse.Namespace, _raw_argv: Sequence[str]) -> int:
    try:
        ctx = context.build()
        res = runs.search(ctx, str(args.query))
        return emit(res, 0)
    except errors.RadarError as exc:
        return _emit_radar_error(exc)


def _handle_usage_record(args: argparse.Namespace, _raw_argv: Sequence[str]) -> int:
    raw_run_id = getattr(args, "run_id", None)
    json_file = getattr(args, "json_file", None)
    try:
        ctx = context.build(run_id=str(raw_run_id) if raw_run_id else None)
        valid_run_id = runs.validate_run_id(ctx, str(raw_run_id) if raw_run_id else None)
        res = usage.record_cli(ctx, valid_run_id, str(json_file or ""))
        return emit(res, 0)
    except errors.RadarError as exc:
        return _emit_radar_error(exc)


def _handle_app(args: argparse.Namespace, raw_argv: Sequence[str]) -> int:
    if getattr(args, "from_path", None) is not None or getattr(args, "channel", None) is not None:
        refused = require_terminal(args, raw_argv)
        if refused is not None:
            return refused
    try:
        ctx = context.build()
        res = appsync.sync(ctx, from_path=getattr(args, "from_path", None))
    except errors.RadarError as exc:
        return _emit_radar_error(exc)

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
        return _emit_radar_error(exc)


def _handle_notify_test(_args: argparse.Namespace, _raw_argv: Sequence[str]) -> int:
    try:
        ctx = context.build()
        ok, err_msg = notify.notify_test(ctx)
    except errors.RadarError as exc:
        return _emit_radar_error(exc)
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

        run_id: str | None = None
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
        except Exception as exc:
            if run_id is not None:
                runlog.finish_run(ctx, run_id, "failed", error=str(exc))
            raise
        finally:
            lock.release(hourly_lock)
    except errors.RadarError as exc:
        return _emit_radar_error(exc)


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
        return _emit_radar_error(exc)


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
        return _emit_radar_error(exc)


def _handle_credentials(args: argparse.Namespace, _raw_argv: Sequence[str]) -> int:
    subcmd = str(getattr(args, "credentials_cmd", "status"))
    try:
        if subcmd == "guide":
            name = str(getattr(args, "name", "producthunt"))
            info = credentials.guide(name)
            return emit({"ok": True, **info}, 0)
        ctx = context.build()
        st = credentials.status(ctx)
        return emit({"ok": True, **st}, 0)
    except errors.RadarError as exc:
        return _emit_radar_error(exc)


def _handle_sources(args: argparse.Namespace, _raw_argv: Sequence[str]) -> int:
    action = str(getattr(args, "action", "list"))
    live = bool(getattr(args, "live", False))
    try:
        ctx = context.build()
        if action == "list":
            src_items = sources.list_sources(ctx)
            return emit({"ok": True, "count": len(src_items), "sources": src_items}, 0)
        res = sources.check_sources(ctx, live=live)
        code = 0 if res["ok"] else 3
        return emit(res, code)
    except errors.RadarError as exc:
        return _emit_radar_error(exc)


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
    p_doctor.set_defaults(_handler=_handle_doctor)

    # bootstrap init | apply | lockdown (all terminal-only)
    p_boot = sub.add_parser(
        "bootstrap",
        help="First-run setup: bootstrap init, bootstrap apply, bootstrap lockdown (terminal-only)",
    )
    boot_sub = p_boot.add_subparsers(dest="bootstrap_cmd", required=True)
    p_boot_init = boot_sub.add_parser("init", help="Initialize dirs, DB, app copy, and inventory")
    p_boot_init.add_argument("--channel", default=None)
    p_boot_init.set_defaults(_handler=_handle_bootstrap)

    p_boot_apply = boot_sub.add_parser("apply", help="Ingest interview answers JSON")
    p_boot_apply.add_argument("--answers", required=True, help="Path to answers.json")
    p_boot_apply.add_argument("--channel", default=None)
    p_boot_apply.set_defaults(_handler=_handle_bootstrap)

    p_boot_lock = boot_sub.add_parser("lockdown", help="Write run-project settings.json")
    p_boot_lock.add_argument("--channel", default=None)
    p_boot_lock.set_defaults(_handler=_handle_bootstrap)

    # credentials status | guide NAME
    p_creds = sub.add_parser(
        "credentials",
        help="Keychain status and setup guide: credentials status, credentials guide NAME",
    )
    creds_sub = p_creds.add_subparsers(dest="credentials_cmd", required=True)
    p_creds_status = creds_sub.add_parser("status", help="Show Keychain item availability")
    p_creds_status.set_defaults(_handler=_handle_credentials)
    p_creds_guide = creds_sub.add_parser("guide", help="Print setup guide for a credential")
    p_creds_guide.add_argument("name", help="Credential account name")
    p_creds_guide.set_defaults(_handler=_handle_credentials)

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
    p_plan.set_defaults(_handler=_handle_plan_run)

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
    p_enrich.set_defaults(_handler=_handle_enrich)

    # verdict apply
    p_verdict = sub.add_parser("verdict", help="Validate and apply LLM verdicts: verdict apply")
    verdict_sub = p_verdict.add_subparsers(dest="verdict_cmd", required=True)
    p_verdict_apply = verdict_sub.add_parser(
        "apply", help="Validate and apply runs/ID/verdicts.json"
    )
    p_verdict_apply.add_argument("--run", dest="run_id", required=True, help="Run identifier")
    p_verdict_apply.set_defaults(_handler=_handle_verdict_apply)

    # render
    p_render = sub.add_parser("render", help="Render digest, projections, BYOR, HTML, and snapshot")
    p_render.add_argument("--run", dest="run_id", required=True, help="Run identifier")
    p_render.set_defaults(_handler=_handle_render)

    # decide (terminal-only)
    p_decide = sub.add_parser("decide", help="Apply a decision directly (terminal-only)")
    p_decide.add_argument("item", help="Item identifier (e.g. R-0001)")
    p_decide.add_argument("verb", choices=DECIDE_VERBS, help="Decision verb")
    p_decide.add_argument("--ttl", default=None, help="Snooze/hold TTL (e.g. 90d)")
    p_decide.add_argument("--reason", default=None, help="Decision rationale")
    p_decide.add_argument("--wake", default=None, help="Wake condition JSON")
    p_decide.add_argument("--channel", default=None, help="Must be 'cli' from an interactive TTY")
    p_decide.set_defaults(_handler=_handle_decide)

    # decide-pending
    p_decide_pending = sub.add_parser("decide-pending", help="Stage a decision from a thread reply")
    p_decide_pending.add_argument("item", help="Item identifier (e.g. R-0001)")
    p_decide_pending.add_argument("verb", choices=DECIDE_VERBS, help="Decision verb")
    p_decide_pending.add_argument("--ttl", default=None, help="Snooze/hold TTL (e.g. 90d)")
    p_decide_pending.add_argument("--reason", default=None, help="Decision rationale")
    p_decide_pending.add_argument("--wake", default=None, help="Wake condition JSON")
    p_decide_pending.set_defaults(_handler=_handle_decide_pending)

    # feedback
    p_feedback = sub.add_parser("feedback", help="Stage digest-item feedback")
    p_feedback.add_argument("item", help="Item identifier")
    p_feedback.add_argument("verdict", choices=FEEDBACK_VERBS, help="Feedback value")
    p_feedback.set_defaults(_handler=_handle_feedback)

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
    p_req_confirm.set_defaults(_handler=_handle_request_confirm)

    # pending list (terminal-only)
    p_pending = sub.add_parser(
        "pending", help="Inspect staged changes: pending list (terminal-only)"
    )
    pending_sub = p_pending.add_subparsers(dest="pending_cmd", required=True)
    p_pending_list = pending_sub.add_parser("list", help="Print full staged batch (terminal-only)")
    p_pending_list.add_argument("--channel", default=None)
    p_pending_list.set_defaults(_handler=_handle_pending_list)

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
    p_confirm.set_defaults(_handler=_handle_confirm)

    # decisions apply (terminal-only)
    p_decisions = sub.add_parser(
        "decisions",
        help="Apply staged DECISIONS.md edits: decisions apply (terminal-only)",
    )
    decisions_sub = p_decisions.add_subparsers(dest="decisions_cmd", required=True)
    p_dec_apply = decisions_sub.add_parser("apply", help="Apply staged DECISIONS.md edits")
    p_dec_apply.add_argument("--channel", default=None)
    p_dec_apply.set_defaults(_handler=_handle_decisions_apply)

    # alert ack|resolve|false-positive ALERT_ID (terminal-only)
    p_alert = sub.add_parser(
        "alert",
        help="Update alert lifecycle: alert ack|resolve|false-positive ALERT_ID (terminal-only)",
    )
    p_alert.add_argument("action", choices=ALERT_VERBS, help="Alert transition")
    p_alert.add_argument("alert_id", help="Alert identifier")
    p_alert.add_argument("--channel", default=None)
    p_alert.set_defaults(_handler=_handle_alert)

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
    p_sources.set_defaults(_handler=_handle_sources)

    # search QUERY
    p_search = sub.add_parser("search", help="FTS5 search over signals and digests")
    p_search.add_argument("query", help="FTS5 query string")
    p_search.set_defaults(_handler=_handle_search)

    # usage record --run ID --json FILE
    p_usage = sub.add_parser("usage", help="Record run token usage: usage record")
    usage_sub = p_usage.add_subparsers(dest="usage_cmd", required=True)
    p_usage_rec = usage_sub.add_parser("record", help="Record usage JSON for a run")
    p_usage_rec.add_argument("--run", dest="run_id", required=True, help="Run identifier")
    p_usage_rec.add_argument("--json", dest="json_file", required=True, help="Path to usage JSON")
    p_usage_rec.set_defaults(_handler=_handle_usage_record)

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
