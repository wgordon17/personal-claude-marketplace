"""Discovery seeds: GitHub stars/watched, WATCHLIST, taste-makers, pain bigrams, and intent."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import RadarError
from . import fingerprint as fp
from .projects import entity_for_usage
from .system import EntityRecord, UsageRecord

if TYPE_CHECKING:
    from ..context import Ctx

_GH_URL_SLUG_RE = re.compile(
    r"https?://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git|/|$)"
)


@dataclass
class SeedsInventoryOutput:
    """Aggregated output from seeds.collect(ctx)."""

    entities: list[EntityRecord] = field(default_factory=list)
    usages: list[UsageRecord] = field(default_factory=list)
    fingerprints: list[fp.FingerprintRecord] = field(default_factory=list)
    bin_repo_links: dict[str, str] = field(default_factory=dict)


def _parse_paginated_json_list(raw: str) -> list[dict[str, Any]]:
    """Parse JSON list output from `gh api --paginate` (handles concatenated JSON arrays)."""
    text = raw.strip()
    if not text:
        return []
    try:
        loaded = json.loads(text)
        if isinstance(loaded, list):
            return [x for x in loaded if isinstance(x, dict)]
    except json.JSONDecodeError:
        pass

    normalized = re.sub(r"\]\s*\[", ",", text)
    try:
        loaded = json.loads(normalized)
        if isinstance(loaded, list):
            return [x for x in loaded if isinstance(x, dict)]
    except json.JSONDecodeError:
        return []
    return []


def _watchlist_entry_to_entity_id(entry: str) -> str | None:
    cleaned = entry.strip()
    if not cleaned:
        return None
    first_tok = cleaned.split()[0].strip(",;()")
    if not first_tok:
        return None

    m = _GH_URL_SLUG_RE.search(first_tok)
    if m:
        return f"gh:{m.group(1).lower()}"

    if first_tok.startswith(
        (
            "pkg:",
            "gh:",
            "brew:",
            "app:",
            "gha:",
            "oci:",
            "nix:",
            "tf:",
            "helm:",
            "raycast:",
            "mcp:",
            "skill:",
            "plugin:",
            "model:",
        )
    ):
        if first_tok.startswith("gh:"):
            return f"gh:{first_tok[3:].lower()}"
        return first_tok

    if "/" in first_tok and not first_tok.startswith("@"):
        return f"gh:{first_tok.lower()}"

    return f"pkg:pypi/{first_tok.lower()}"


def collect(
    ctx: Ctx,
    *,
    cmd_bigrams: Mapping[tuple[str, str], int] | None = None,
    existing_entities: Sequence[EntityRecord] = (),
    project_dirs: Sequence[tuple[str, Path]] = (),
) -> SeedsInventoryOutput:
    """Collect GitHub stars/watched, WATCHLIST, taste-maker logins, and pain bigrams."""
    out = SeedsInventoryOutput()
    ctx.reload_profile()

    known_repo_slugs: dict[str, str] = {}

    try:
        star_res = ctx.gh(["api", "user/starred", "--paginate"])
        if star_res.returncode == 0 and star_res.stdout.strip():
            for item in _parse_paginated_json_list(star_res.stdout):
                full_name = str(item.get("full_name") or "").strip().lower()
                if not full_name or "/" not in full_name:
                    continue
                is_priv = bool(item.get("private") is True)
                entity_id = f"gh:{full_name}"
                topics = item.get("topics") if isinstance(item.get("topics"), list) else []
                attrs: dict[str, Any] = {}
                if topics:
                    attrs["topics"] = [str(t) for t in topics if t]
                out.entities.append(
                    EntityRecord(
                        entity_id=entity_id,
                        kind="repo",
                        ecosystem="github",
                        name=full_name,
                        repo=full_name,
                        visibility="private" if is_priv else "public",
                    )
                )
                out.usages.append(
                    UsageRecord(
                        entity_id=entity_id,
                        context="interest:starred",
                        version=None,
                        direct=1,
                        attrs=attrs,
                    )
                )
                short_name = full_name.split("/")[-1]
                known_repo_slugs.setdefault(short_name, full_name)
    except RadarError:
        pass

    try:
        sub_res = ctx.gh(["api", "user/subscriptions"])
        if sub_res.returncode == 0 and sub_res.stdout.strip():
            for item in _parse_paginated_json_list(sub_res.stdout):
                full_name = str(item.get("full_name") or "").strip().lower()
                if not full_name or "/" not in full_name:
                    continue
                is_priv = bool(item.get("private") is True)
                entity_id = f"gh:{full_name}"
                out.entities.append(
                    EntityRecord(
                        entity_id=entity_id,
                        kind="repo",
                        ecosystem="github",
                        name=full_name,
                        repo=full_name,
                        visibility="private" if is_priv else "public",
                    )
                )
                out.usages.append(
                    UsageRecord(
                        entity_id=entity_id,
                        context="interest:watched",
                        version=None,
                        direct=1,
                    )
                )
                short_name = full_name.split("/")[-1]
                known_repo_slugs.setdefault(short_name, full_name)
    except RadarError:
        pass

    for entry in ctx.profile.watchlist:
        entity_id = _watchlist_entry_to_entity_id(entry)
        if not entity_id:
            continue
        ent = entity_for_usage(entity_id)
        out.entities.append(ent)
        out.usages.append(
            UsageRecord(
                entity_id=entity_id,
                context="interest:watchlist",
                version=None,
                direct=1,
            )
        )
        if entity_id.startswith("gh:"):
            slug = entity_id[3:]
            short_name = slug.split("/")[-1]
            known_repo_slugs.setdefault(short_name, slug)

    for ent in existing_entities:
        if ent.entity_id.startswith("bin:"):
            bname = ent.name.lower()
            if bname in known_repo_slugs:
                out.bin_repo_links[ent.entity_id] = known_repo_slugs[bname]

    for raw_tm in ctx.profile.taste_makers:
        login = raw_tm.strip().lstrip("@").split()[0] if raw_tm.strip() else ""
        if login:
            out.fingerprints.append(
                fp.FingerprintRecord(
                    context="interest:watchlist",
                    kind="config_key",
                    token=f"tastemaker:{login}",
                )
            )

    if cmd_bigrams:
        counter = Counter(cmd_bigrams)
        for (c1, c2), cnt in counter.most_common(25):
            if c1 and c2 and cnt >= 1:
                out.fingerprints.append(
                    fp.FingerprintRecord(
                        context="system:shell-history",
                        kind="cmd",
                        token=f"bigram:{c1} {c2}",
                        count=cnt,
                    )
                )

    for project_key, pdir in project_dirs:
        intent_fps, _ = fp.extract_project_intent_fingerprints(pdir, f"project:{project_key}")
        out.fingerprints.extend(intent_fps)

    out.fingerprints = fp.merge_fingerprints(out.fingerprints)
    return out
