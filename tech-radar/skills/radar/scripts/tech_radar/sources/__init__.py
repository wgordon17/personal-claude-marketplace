"""Source registry and runner re-exports (Spec 03, Spec 11 §5)."""

from __future__ import annotations

from . import ai, base, github, registries, registry, security_feeds
from .base import FetchMeta, RawSignal, SourceResult, SourceSpec, run_source
from .registry import (
    SOURCES,
    SOURCES_BY_ID,
    by_cadence,
    collect_sources_for_run,
    get_source,
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
    "collect_sources_for_run",
    "get_source",
    "github",
    "registries",
    "registry",
    "run_collect_sources",
    "run_source",
    "security_feeds",
]
