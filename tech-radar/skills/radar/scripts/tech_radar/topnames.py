"""Top-names cache and Damerau-Levenshtein typosquat check (Spec 05 §5 T10, Spec 12 A5)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from . import db, security
from .sources import registries
from .sources.base import run_source

if TYPE_CHECKING:
    from .context import Ctx

BUILTIN_FALLBACK_TOP_NAMES: tuple[str, ...] = (
    "requests",
    "urllib3",
    "pydantic",
    "fastapi",
    "litellm",
    "anthropic",
    "openai",
    "pytest",
    "ruff",
    "click",
    "rich",
    "numpy",
    "pandas",
    "scipy",
    "react",
    "next",
    "express",
    "lodash",
    "axios",
    "typescript",
    "serde",
    "tokio",
    "clap",
)


def load_top_names(ctx: Ctx, ecosystem: str | None = None) -> list[str]:
    """Load cached top package names for `ecosystem` (`pypi` or `npm`), fetching if absent."""
    ecos = (
        [ecosystem.strip().lower()]
        if ecosystem and ecosystem.strip().lower() in ("pypi", "npm")
        else ["pypi", "npm"]
    )

    names: list[str] = []
    seen: set[str] = set()
    missing_any = False

    for eco in ecos:
        raw = db.kv_get(ctx.db, f"topnames:{eco}")
        if not raw:
            missing_any = True
            continue
        try:
            arr = json.loads(raw)
            if isinstance(arr, list):
                for item in arr:
                    s = str(item).strip().lower()
                    if s and s not in seen:
                        seen.add(s)
                        names.append(s)
        except json.JSONDecodeError:
            missing_any = True

    if missing_any:
        run_source(registries.TOPNAMES_SPEC, ctx)
        for eco in ecos:
            raw = db.kv_get(ctx.db, f"topnames:{eco}")
            if not raw:
                continue
            try:
                arr = json.loads(raw)
                if isinstance(arr, list):
                    for item in arr:
                        s = str(item).strip().lower()
                        if s and s not in seen:
                            seen.add(s)
                            names.append(s)
            except json.JSONDecodeError:
                pass

    for fb in BUILTIN_FALLBACK_TOP_NAMES:
        if fb not in seen:
            seen.add(fb)
            names.append(fb)

    return names


def find_typosquat_match(
    ctx: Ctx,
    name: str,
    ecosystem: str | None = None,
) -> str | None:
    """Return the top-names entry within Damerau-Levenshtein distance 1 of `name`, or None."""
    clean = name.strip().lower()
    if not clean:
        return None
    if "/" in clean:
        clean = clean.rsplit("/", 1)[-1]

    top_list = load_top_names(ctx, ecosystem=ecosystem)
    if clean in top_list:
        return None

    for candidate in top_list:
        dist = security.damerau_levenshtein(clean, candidate)
        if 0 < dist <= 1:
            return candidate
    return None
