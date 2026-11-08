"""Collector registry and tri-state execution engine for standup-tool."""

from __future__ import annotations

import copy
import sys
import traceback
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from standup_tool.collectors import (
    conversation_activity,
    git_activity,
    gmail,
    google_calendar,
    google_drive,
    jira_activity,
    memory_activity,
    pr_activity,
    slack_activity,
)
from standup_tool.config import Config, redact_secrets, secret_path
from standup_tool.workspace import RepoInfo


@dataclass(frozen=True)
class Ctx:
    """Execution context passed to every registered collector."""

    config: Config
    since: date
    until: date
    repos: list[RepoInfo]
    author_email: str
    cfg_dir: Path


@dataclass(frozen=True)
class Collector:
    """Single collector registration entry."""

    name: str
    output_key: str
    run: Callable[[Ctx, dict[str, Any]], Any]
    empty: Any
    depends_on: tuple[str, ...] = ()


def _count_output(output: Any) -> int:
    if isinstance(output, dict):
        return sum(len(v) for v in output.values() if isinstance(v, list))
    if isinstance(output, list | tuple | set):
        return len(output)
    return 0


def run_one(
    collector: Collector,
    ctx: Ctx,
    selected: set[str] | None,
    outputs: dict[str, Any],
    status: dict[str, dict[str, Any]],
) -> tuple[Any, dict[str, Any]]:
    """Execute *collector* and return `(output, status_record)`.

    `status` is read-only here; `run_all` accumulates status across collectors.
    """
    cfg_entry = getattr(ctx.config.collectors, collector.name, None)
    if cfg_entry is not None and not cfg_entry.enabled:
        return copy.deepcopy(collector.empty), {
            "state": "disabled",
            "reason": "disabled in config",
            "count": 0,
        }

    if selected is not None and collector.name not in selected:
        return copy.deepcopy(collector.empty), {
            "state": "disabled",
            "reason": "not selected",
            "count": 0,
        }

    for dep in collector.depends_on:
        dep_status = status.get(dep)
        if dep_status is not None:
            dep_state = dep_status.get("state")
            if dep_state == "disabled":
                return copy.deepcopy(collector.empty), {
                    "state": "disabled",
                    "reason": f"requires {dep}",
                    "count": 0,
                }
            if dep_state == "failed":
                return copy.deepcopy(collector.empty), {
                    "state": "failed",
                    "reason": f"dependency {dep} failed",
                    "count": 0,
                }
        else:
            dep_cfg = getattr(ctx.config.collectors, dep, None)
            if (dep_cfg is not None and not dep_cfg.enabled) or (
                selected is not None and dep not in selected
            ):
                return copy.deepcopy(collector.empty), {
                    "state": "disabled",
                    "reason": f"requires {dep}",
                    "count": 0,
                }

    try:
        output = collector.run(ctx, outputs)
    except Exception as exc:
        reason = redact_secrets(f"{type(exc).__name__}: {exc}")[:300]
        redacted_tb = redact_secrets(traceback.format_exc())
        sys.stderr.write(f"ERROR in collector '{collector.name}':\n{redacted_tb}")
        return copy.deepcopy(collector.empty), {
            "state": "failed",
            "reason": reason,
            "count": 0,
        }

    return output, {
        "state": "ran",
        "reason": None,
        "count": _count_output(output),
    }


def run_all(
    registry: Sequence[Collector],
    ctx: Ctx,
    selected: set[str] | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Run independent collectors concurrently, then dependents in registry order."""
    independents = [c for c in registry if not c.depends_on]
    dependents = [c for c in registry if c.depends_on]

    raw_outputs: dict[str, Any] = {}
    raw_status: dict[str, dict[str, Any]] = {}

    if independents:
        with ThreadPoolExecutor(max_workers=len(independents)) as executor:
            future_to_col = {
                executor.submit(run_one, col, ctx, selected, {}, {}): col for col in independents
            }
            for future in as_completed(future_to_col):
                col = future_to_col[future]
                out, st = future.result()
                raw_outputs[col.output_key] = out
                raw_status[col.name] = st

    for col in dependents:
        out, st = run_one(col, ctx, selected, raw_outputs, raw_status)
        raw_outputs[col.output_key] = out
        raw_status[col.name] = st

    outputs_by_key = {col.output_key: raw_outputs[col.output_key] for col in registry}
    status_by_name = {col.name: raw_status[col.name] for col in registry}
    return outputs_by_key, status_by_name


def _run_drive(ctx: Ctx, outputs: dict[str, Any]) -> list[dict]:
    calendar_events = outputs.get("calendar_events") or []
    if not calendar_events:
        return []
    return google_drive.collect(calendar_events, ctx.since, ctx.until, config_dir=ctx.cfg_dir)


REGISTRY: list[Collector] = [
    Collector(
        name="git",
        output_key="git_commits",
        run=lambda ctx, _out: git_activity.collect(
            ctx.repos, ctx.since, ctx.until, author_email=ctx.author_email
        ),
        empty=[],
    ),
    Collector(
        name="pr",
        output_key="prs",
        run=lambda ctx, _out: pr_activity.collect(ctx.repos, ctx.since, ctx.until),
        empty=[],
    ),
    Collector(
        name="jira",
        output_key="jira_activity",
        run=lambda ctx, _out: jira_activity.collect(
            since_date=ctx.since,
            project=ctx.config.collectors.jira.project or "",
        ),
        empty={},
    ),
    Collector(
        name="calendar",
        output_key="calendar_events",
        run=lambda ctx, _out: google_calendar.collect(ctx.since, ctx.until, config_dir=ctx.cfg_dir),
        empty=[],
    ),
    Collector(
        name="gmail",
        output_key="emails",
        run=lambda ctx, _out: gmail.collect(
            ctx.since, ctx.until, my_email=ctx.author_email, config_dir=ctx.cfg_dir
        ),
        empty=[],
    ),
    Collector(
        name="drive",
        output_key="drive_docs",
        run=_run_drive,
        empty=[],
        depends_on=("calendar",),
    ),
    Collector(
        name="slack",
        output_key="slack",
        run=lambda ctx, _out: slack_activity.collect(
            ctx.since,
            ctx.until,
            token_path=secret_path("slack-token"),
            workspace_url=ctx.config.collectors.slack.workspace_url,
            channels=ctx.config.collectors.slack.channels,
        ),
        empty=[],
    ),
    Collector(
        name="memory",
        output_key="memory_activity",
        run=lambda ctx, _out: memory_activity.collect(ctx.repos, ctx.since, ctx.until),
        empty=[],
    ),
    Collector(
        name="conversations",
        output_key="conversations",
        run=lambda ctx, _out: conversation_activity.collect(
            ctx.since,
            ctx.until,
            repo_keys=conversation_activity.build_repo_keys(ctx.repos),
            patterns=ctx.config.collectors.conversations.project_match,
            vertex_project=ctx.config.collectors.conversations.vertex_project,
            region=ctx.config.collectors.conversations.region,
        ),
        empty=[],
    ),
]
