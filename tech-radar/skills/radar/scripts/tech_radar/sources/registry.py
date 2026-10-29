"""Source registry with explicit static imports (Spec 03, Spec 07 §8a, Spec 11 §5, Spec 12 A16)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import ai, community, feeds, github, registries, security_feeds
from .base import SourceResult, SourceSpec, run_source

if TYPE_CHECKING:
    from ..context import Ctx

SOURCES: tuple[SourceSpec, ...] = (
    *security_feeds.SECURITY_FEED_SPECS,
    *registries.REGISTRY_SPECS,
    *github.GITHUB_SPECS,
    *ai.AI_SPECS,
    *feeds.FEED_SPECS,
    *community.COMMUNITY_SPECS,
)

SOURCES_BY_ID: dict[str, SourceSpec] = {spec.id: spec for spec in SOURCES}


def get_source(source_id: str) -> SourceSpec:
    """Return a registered SourceSpec by id."""
    return SOURCES_BY_ID[source_id]


def by_cadence(cadence: str) -> tuple[SourceSpec, ...]:
    """Return all enabled SourceSpecs matching `cadence` (on-demand is never returned here)."""
    if cadence == "on-demand":
        return ()
    return tuple(s for s in SOURCES if s.enabled and s.cadence == cadence)


def collect_sources_for_run(ctx: Ctx, mode: str | None = None) -> tuple[SourceSpec, ...]:
    """Return the union `by_cadence('hourly') ∪ by_cadence('daily') ∪ due weekly/monthly` (A16)."""
    explicit_mode: str | None = mode.lower() if mode else None
    if explicit_mode is None and ctx.run_id and "-" in ctx.run_id:
        suffix = ctx.run_id.rsplit("-", 1)[-1].lower()
        if suffix in ("daily", "weekly", "monthly"):
            explicit_mode = suffix

    if explicit_mode is not None:
        include_weekly = explicit_mode in ("weekly", "monthly")
        include_monthly = explicit_mode == "monthly"
    else:
        include_weekly = ctx.now.weekday() == 0
        include_monthly = ctx.now.weekday() == 0 and 1 <= ctx.now.day <= 7

    ordered: list[SourceSpec] = []
    seen: set[str] = set()

    cadences: list[str] = ["hourly", "daily"]
    if include_weekly:
        cadences.append("weekly")
    if include_monthly:
        cadences.append("monthly")

    for cad in cadences:
        for spec in by_cadence(cad):
            if spec.id not in seen:
                seen.add(spec.id)
                ordered.append(spec)
    return tuple(ordered)


def run_collect_sources(ctx: Ctx, mode: str | None = None) -> list[SourceResult]:
    """Run all due sources for a `collect` invocation in deterministic registry order."""
    specs = collect_sources_for_run(ctx, mode=mode)
    return [run_source(spec, ctx) for spec in specs]


def list_sources(ctx: Ctx) -> list[dict[str, Any]]:
    """Return status dictionaries for all registered `SOURCES` (for `sources list`)."""
    health_rows = ctx.db.execute(
        "SELECT source_id, last_ok_at, last_error, consecutive_failures, "
        "last_item_count, skipped_reason FROM source_health"
    ).fetchall()
    by_id = {str(r["source_id"]): dict(r) for r in health_rows}

    out: list[dict[str, Any]] = []
    for spec in SOURCES:
        h = by_id.get(spec.id, {})
        out.append(
            {
                "id": spec.id,
                "archetype": spec.archetype,
                "cadence": spec.cadence,
                "enabled": spec.enabled,
                "credential": spec.credential,
                "skipped_reason": h.get("skipped_reason") or spec.skipped_reason,
                "last_ok_at": h.get("last_ok_at"),
                "last_error": h.get("last_error"),
                "consecutive_failures": int(h.get("consecutive_failures") or 0),
                "last_item_count": h.get("last_item_count"),
            }
        )
    return out


def check_sources(ctx: Ctx, *, live: bool = False) -> dict[str, Any]:
    """Validate source registry or run each enabled source once when `live=True`."""
    checked: list[dict[str, Any]] = []
    failed = 0
    for spec in SOURCES:
        if not spec.enabled:
            checked.append(
                {
                    "id": spec.id,
                    "ok": True,
                    "enabled": False,
                    "skipped_reason": spec.skipped_reason or "disabled",
                }
            )
            continue
        if not live:
            has_handler = bool(spec.parser is not None or spec.runner is not None)
            if not has_handler:
                failed += 1
            checked.append(
                {
                    "id": spec.id,
                    "ok": has_handler,
                    "enabled": True,
                    "cadence": spec.cadence,
                }
            )
            continue
        if spec.cadence == "on-demand":
            checked.append(
                {
                    "id": spec.id,
                    "ok": True,
                    "enabled": True,
                    "skipped_reason": "on-demand",
                }
            )
            continue
        res = run_source(spec, ctx)
        if not res.ok:
            failed += 1
        checked.append(
            {
                "id": spec.id,
                "ok": res.ok,
                "count": len(res.signals),
                "skipped_reason": res.skipped_reason,
                "error": res.error,
            }
        )
    return {
        "ok": failed == 0,
        "live": live,
        "total": len(SOURCES),
        "failed": failed,
        "sources": checked,
    }
