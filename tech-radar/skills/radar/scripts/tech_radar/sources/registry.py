"""Source registry with explicit static imports (Spec 03, Spec 07 §8a, Spec 11 §5, Spec 12 A16)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import ai, base, github, registries, security_feeds
from .base import SourceResult, SourceSpec, run_source

if TYPE_CHECKING:
    from ..context import Ctx

_STATIC_SOURCE_MODULES = (
    ai,
    base,
    github,
    registries,
    security_feeds,
)

SOURCES: tuple[SourceSpec, ...] = (
    *security_feeds.SECURITY_FEED_SPECS,
    *registries.REGISTRY_SPECS,
    *github.GITHUB_SPECS,
    *ai.AI_SPECS,
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
    resolved_mode = (mode or "daily").lower()
    if ctx.run_id and "-" in ctx.run_id:
        suffix = ctx.run_id.rsplit("-", 1)[-1].lower()
        if suffix in ("daily", "weekly", "monthly"):
            resolved_mode = suffix

    include_weekly = resolved_mode in ("weekly", "monthly") or ctx.now.weekday() == 0
    include_monthly = resolved_mode == "monthly" or (
        ctx.now.weekday() == 0 and 1 <= ctx.now.day <= 7
    )

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
