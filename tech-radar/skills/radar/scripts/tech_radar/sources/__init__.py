"""Source registry and runner re-exports (Spec 03, Spec 11 §5)."""

from __future__ import annotations

from . import ai, base, community, feeds, github, registries, registry, security_feeds
from .base import FetchMeta, RawSignal, SourceResult, SourceSpec, run_source
from .registries import fetch_metrics
from .registry import (
    SOURCES,
    SOURCES_BY_ID,
    by_cadence,
    check_sources,
    collect_sources_for_run,
    get_source,
    list_sources,
    run_collect_sources,
)

__all__ = [
    "SOURCES",
    "SOURCES_BY_ID",
    "FetchMeta",
    "RawSignal",
    "SourceResult",
    "SourceSpec",
    "ai",
    "base",
    "by_cadence",
    "check_sources",
    "collect_sources_for_run",
    "community",
    "feeds",
    "fetch_metrics",
    "get_source",
    "github",
    "list_sources",
    "registries",
    "registry",
    "run_collect_sources",
    "run_source",
    "security_feeds",
]
