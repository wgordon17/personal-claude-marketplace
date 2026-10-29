"""Candidate promotion, default quadrant mapping, and A5 prescore relevance filter (Spec 11 §6)."""

from __future__ import annotations

import functools
import json
import re
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, timedelta
from typing import TYPE_CHECKING, Any

from . import db, evaluate
from .inventory import classify, projects
from .sources import registries
from .sources.base import RawSignal, SourceResult, infer_github_entity_hint

if TYPE_CHECKING:
    from .context import Ctx

DAILY_CANDIDATE_CAP = 50
SEEN_WINDOW_DAYS = 30

QUADRANT_BY_KIND: dict[str, str] = {
    "package": "languages-libraries",
    "cli": "cli-dev-tools",
    "app": "cli-dev-tools",
    "mcp": "skills-mcp-plugins",
    "skill": "skills-mcp-plugins",
    "plugin": "skills-mcp-plugins",
    "model": "ai-agents-harness",
    "service": "platforms-services",
    "runtime": "platforms-services",
    "action": "platforms-services",
    "technique": "techniques",
    "repo": "languages-libraries",
}

ALL_QUADRANTS: tuple[str, ...] = (
    "languages-libraries",
    "cli-dev-tools",
    "ai-agents-harness",
    "skills-mcp-plugins",
    "platforms-services",
    "techniques",
)

_ENTITY_ID_PREFIXES: tuple[str, ...] = (
    "pkg:",
    "gh:",
    "brew:",
    "gha:",
    "mcp:",
    "skill:",
    "plugin:",
    "model:",
    "technique:",
    "app:",
    "svc:",
    "runtime:",
    "bin:",
    "oci:",
    "nix:",
    "tf:",
    "helm:",
    "raycast:",
    "harness:",
)

_ITEM_NUM_RE = re.compile(r"^R-(\d+)$")


def default_quadrant(
    kind: str,
    *,
    entity_id: str | None = None,
    existing_quadrant: str | None = None,
) -> str:
    """Return the Spec 05 §3 quadrant for an entity kind."""
    if existing_quadrant and existing_quadrant in ALL_QUADRANTS:
        return existing_quadrant
    if entity_id:
        if entity_id.startswith(("mcp:", "skill:", "plugin:")):
            return "skills-mcp-plugins"
        if entity_id.startswith("model:"):
            return "ai-agents-harness"
        if entity_id.startswith(("brew:", "bin:", "app:", "raycast:")):
            return "cli-dev-tools"
        if entity_id.startswith(("svc:", "runtime:", "gha:", "oci:", "nix:", "tf:", "helm:")):
            return "platforms-services"
        if entity_id.startswith("technique:"):
            return "techniques"
    return QUADRANT_BY_KIND.get(kind, "languages-libraries")


def next_item_id(conn: sqlite3.Connection) -> str:
    """Allocate the next sequential `R-XXXX` item identifier."""
    rows = conn.execute("SELECT item_id FROM items").fetchall()
    max_num = 0
    for row in rows:
        m = _ITEM_NUM_RE.match(str(row["item_id"]))
        if m:
            max_num = max(max_num, int(m.group(1)))
    return f"R-{max_num + 1:04d}"


def ensure_entity(
    ctx: Ctx,
    entity_hint: str,
    *,
    quadrant: str | None = None,
    classify_visibility: bool = True,
) -> tuple[str, str, str]:
    """Resolve or create an `entities` row for `entity_hint`.

    Returns `(entity_id, kind, visibility)`.
    """
    hint = entity_hint.strip()
    row = ctx.db.execute(
        "SELECT entity_id, kind, visibility, quadrant FROM entities WHERE entity_id = ?",
        (hint,),
    ).fetchone()
    if row is None:
        alias_row = ctx.db.execute(
            "SELECT e.entity_id, e.kind, e.visibility, e.quadrant "
            "FROM entity_aliases a JOIN entities e ON e.entity_id = a.entity_id "
            "WHERE a.alias = ?",
            (hint,),
        ).fetchone()
        if alias_row is not None:
            row = alias_row

    if row is not None:
        eid = str(row["entity_id"])
        kind = str(row["kind"])
        vis = str(row["visibility"] or "unknown")
        if vis == "unknown" and classify_visibility:
            vis = classify.visibility(ctx, eid)
        return eid, kind, vis

    ent_rec = projects.entity_for_usage(hint)
    quad = default_quadrant(
        ent_rec.kind,
        entity_id=ent_rec.entity_id,
        existing_quadrant=quadrant or ent_rec.quadrant,
    )
    safe_name = db.scrub_text(ent_rec.name) or ent_rec.entity_id
    safe_repo = db.scrub_text(ent_rec.repo)
    ctx.db.execute(
        "INSERT INTO entities("
        "entity_id, kind, ecosystem, name, repo, homepage, quadrant, visibility, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(entity_id) DO NOTHING",
        (
            ent_rec.entity_id,
            ent_rec.kind,
            ent_rec.ecosystem,
            safe_name,
            safe_repo,
            ent_rec.homepage,
            quad,
            ent_rec.visibility,
            ctx.now_iso,
        ),
    )
    ctx.db.commit()
    vis = (
        classify.visibility(ctx, ent_rec.entity_id)
        if classify_visibility
        else (ent_rec.visibility if ent_rec.visibility != "unknown" else "public")
    )
    return ent_rec.entity_id, ent_rec.kind, vis


def ensure_item(
    ctx: Ctx,
    entity_id: str,
    *,
    in_use: bool = False,
    ring: str | None = None,
    state: str = "candidate",
    quadrant: str | None = None,
) -> tuple[str, bool]:
    """Return `(item_id, created)` for `entity_id`, creating the row if absent."""
    existing = ctx.db.execute(
        "SELECT item_id, ring, state, in_use, quadrant FROM items WHERE entity_id = ?",
        (entity_id,),
    ).fetchone()
    if existing is not None:
        item_id = str(existing["item_id"])
        if in_use and int(existing["in_use"] or 0) == 0:
            dec_row = ctx.db.execute(
                "SELECT verb FROM decisions WHERE item_id = ? AND superseded_by IS NULL "
                "ORDER BY decision_id DESC LIMIT 1",
                (item_id,),
            ).fetchone()
            new_ring = existing["ring"]
            new_state = existing["state"]
            if dec_row is None and new_ring is None:
                new_ring = ring or "adopt"
                new_state = "active"
            ctx.db.execute(
                "UPDATE items SET in_use = 1, ring = ?, state = ? WHERE item_id = ?",
                (new_ring, new_state, item_id),
            )
            ctx.db.commit()
        return item_id, False

    ent_row = ctx.db.execute(
        "SELECT kind, quadrant FROM entities WHERE entity_id = ?",
        (entity_id,),
    ).fetchone()
    kind = str(ent_row["kind"]) if ent_row is not None else "package"
    ent_quad = str(ent_row["quadrant"]) if ent_row is not None and ent_row["quadrant"] else None
    resolved_quad = default_quadrant(
        kind, entity_id=entity_id, existing_quadrant=quadrant or ent_quad
    )

    item_id = next_item_id(ctx.db)
    resolved_ring = ring if ring is not None else ("adopt" if in_use else None)
    resolved_state = "active" if in_use and state == "candidate" else state

    ctx.db.execute(
        "INSERT INTO items("
        "item_id, entity_id, ring, quadrant, state, in_use, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            item_id,
            entity_id,
            resolved_ring,
            resolved_quad,
            resolved_state,
            1 if in_use else 0,
            ctx.now_iso,
        ),
    )
    ctx.db.commit()
    return item_id, True


def _extract_signal_entity_hint(row: Mapping[str, Any]) -> str | None:
    if row.get("entity_id"):
        return str(row["entity_id"]).strip()

    payload_raw = row.get("payload_json")
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

    if payload.get("entity_hint"):
        return str(payload["entity_hint"]).strip()
    if payload.get("entity_id"):
        return str(payload["entity_id"]).strip()
    if payload.get("repo") and "/" in str(payload["repo"]):
        return f"gh:{str(payload['repo']).strip().lower()}"

    ext_id = str(row.get("external_id") or "").strip()
    if ext_id.startswith(_ENTITY_ID_PREFIXES):
        return ext_id

    src_id = str(row.get("source_id") or "").strip()
    if (
        src_id in ("gh_search_rising", "gh_trending", "gh_topics", "gh_tastemaker_stars")
        and "/" in ext_id
    ):
        return f"gh:{ext_id.lower()}"
    if src_id == "crates_new" and ext_id:
        return f"pkg:cargo/{ext_id}"
    if src_id == "brew_new_formulae" and ext_id and len(ext_id) < 40:
        return f"brew:formula/{ext_id}"
    if src_id == "mcp_registry" and ext_id:
        return f"mcp:{ext_id.split(':', 1)[0]}"
    if src_id in ("models_dev", "openrouter_models") and ext_id:
        return f"model:{ext_id}"

    gh_hint = infer_github_entity_hint(
        str(row.get("url") or ""),
        str(row.get("untrusted_excerpt") or ""),
        str(row.get("title") or ""),
    )
    if gh_hint:
        return gh_hint

    dedupe = str(row.get("dedupe_key") or "")
    parts = dedupe.split("|")
    if len(parts) >= 3 and parts[2].startswith(_ENTITY_ID_PREFIXES):
        return parts[2]
    return None


def _extract_signal_metrics(row: Mapping[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    payload_raw = row.get("payload_json")
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

    metrics_obj = payload.get("metrics") if isinstance(payload.get("metrics"), Mapping) else {}
    for source_map in (payload, metrics_obj):
        if not isinstance(source_map, Mapping):
            continue
        for key in (
            "stars",
            "star_velocity",
            "stars_7d",
            "downloads",
            "downloads_30d",
            "recent_downloads",
            "forks",
            "votes",
            "score",
        ):
            val = source_map.get(key)
            if isinstance(val, int | float):
                out[key] = float(val)
    return out


_WATCH_TAG_RE = re.compile(r"\[watch-[a-z0-9-]+\]")
_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9+#-]+")
_ANTI_STOPWORDS: frozenset[str] = frozenset(
    {
        "with",
        "from",
        "that",
        "this",
        "tools",
        "tool",
        "only",
        "apps",
        "wrappers",
        "heavy",
        "unmaintained",
    }
)
_CAT_STOPWORDS: frozenset[str] = frozenset({"and", "the", "for", "tools", "dev"})


@functools.lru_cache(maxsize=64)
def _compile_word_boundary_re(terms: frozenset[str], min_len: int = 1) -> re.Pattern[str] | None:
    escaped = sorted(
        (re.escape(t) for t in terms if len(t) >= min_len),
        key=len,
        reverse=True,
    )
    if not escaped:
        return None
    return re.compile(rf"\b(?:{'|'.join(escaped)})\b")


def _prepare_anti_interest_matcher(
    profile_anti: Sequence[str],
) -> tuple[tuple[str, ...], re.Pattern[str] | None]:
    phrases: list[str] = []
    tokens: set[str] = set()
    for entry in profile_anti:
        clean = entry.strip().lower()
        if not clean:
            continue
        clean = _WATCH_TAG_RE.sub("", clean).strip()
        if not clean:
            continue
        phrases.append(clean)
        for tok in _TOKEN_SPLIT_RE.split(clean):
            if len(tok) >= 4 and tok not in _ANTI_STOPWORDS:
                tokens.add(tok)
    return tuple(phrases), _compile_word_boundary_re(frozenset(tokens), min_len=4)


def _category_parts(category_terms: Iterable[str]) -> frozenset[str]:
    parts: set[str] = set()
    for cat in category_terms:
        if not cat:
            continue
        for part in _TOKEN_SPLIT_RE.split(cat):
            if len(part) >= 3 and part not in _CAT_STOPWORDS:
                parts.add(part)
    return frozenset(parts)


def _matches_anti_interest(
    profile_anti: Sequence[str],
    *,
    entity_hint: str,
    title: str,
    excerpt: str,
    quadrant: str,
    compiled_anti: tuple[tuple[str, ...], re.Pattern[str] | None] | None = None,
) -> bool:
    if not profile_anti:
        return False
    combined = f"{entity_hint} {title} {excerpt} {quadrant}".lower()
    phrases, token_re = (
        compiled_anti if compiled_anti is not None else _prepare_anti_interest_matcher(profile_anti)
    )
    if any(phrase in combined for phrase in phrases):
        return True
    return bool(token_re is not None and token_re.search(combined))


def _matches_relevance_criteria(
    ctx: Ctx,
    *,
    entity_hint: str,
    title: str,
    excerpt: str,
    quadrant: str,
    source_id: str,
    sig_type: str,
    payload: Mapping[str, Any],
    relevance_terms: set[str],
    in_use_names: set[str],
    category_terms: set[str],
    cat_parts: frozenset[str] | None = None,
    cat_part_re: re.Pattern[str] | None = None,
    relevance_re: re.Pattern[str] | None = None,
    in_use_re: re.Pattern[str] | None = None,
) -> bool:
    """Return True if candidate matches A5 relevance criteria:
    (quadrant/category matches profile.categories OR entity matches watchlist/starred/taste-maker
    OR signal names an in-use public entity as the thing it replaces/competes with).
    """
    _ = (ctx, sig_type)
    if source_id == "gh_tastemaker_stars":
        return True

    combined = f"{entity_hint} {title} {excerpt}".lower()
    quad_low = quadrant.lower()

    # 1. Category / quadrant match against profile.categories
    for cat in category_terms:
        if cat and (cat == quad_low or cat in quad_low or cat in combined):
            return True
    resolved_cat_parts = cat_parts if cat_parts is not None else _category_parts(category_terms)
    if any(part in quad_low for part in resolved_cat_parts):
        return True
    resolved_cat_re = (
        cat_part_re
        if cat_part_re is not None or not resolved_cat_parts
        else _compile_word_boundary_re(resolved_cat_parts, min_len=3)
    )
    if resolved_cat_re is not None and resolved_cat_re.search(combined):
        return True

    # 2. Watchlist / starred / taste-maker term match
    hint_low = entity_hint.lower()
    hint_tail = hint_low.split("/", 1)[-1] if "/" in hint_low else hint_low.split(":", 1)[-1]
    if hint_low in relevance_terms or hint_tail in relevance_terms:
        return True
    resolved_rel_re = (
        relevance_re
        if relevance_re is not None or not relevance_terms
        else _compile_word_boundary_re(frozenset(relevance_terms), min_len=3)
    )
    if resolved_rel_re is not None and resolved_rel_re.search(combined):
        return True

    # 3. Names an in-use public entity (contender / replacement / mentions in-use tool)
    replaces_val = (
        str(payload.get("replaces") or payload.get("replaces_tool") or "").strip().lower()
    )
    if replaces_val and (
        replaces_val in in_use_names or replaces_val.split("/")[-1] in in_use_names
    ):
        return True
    resolved_in_use_re = (
        in_use_re
        if in_use_re is not None or not in_use_names
        else _compile_word_boundary_re(frozenset(in_use_names), min_len=2)
    )
    return bool(resolved_in_use_re is not None and resolved_in_use_re.search(combined))


def _build_relevance_sets(ctx: Ctx) -> tuple[set[str], set[str], set[str]]:
    category_terms: set[str] = set()
    for c in ctx.profile.categories:
        c_low = c.strip().lower()
        if c_low:
            category_terms.add(c_low)
    for lang in ctx.profile.languages:
        l_low = lang.strip().lower()
        if l_low:
            category_terms.add(l_low)

    relevance_terms: set[str] = set()
    for w in ctx.profile.watchlist:
        w_low = w.strip().lower()
        if w_low:
            relevance_terms.add(w_low)
            if "/" in w_low:
                relevance_terms.add(w_low.rsplit("/", 1)[-1])
    for tm in ctx.profile.taste_makers:
        t_low = tm.strip().lstrip("@").lower()
        if t_low:
            relevance_terms.add(t_low)

    seed_rows = ctx.db.execute(
        "SELECT e.entity_id, e.name, e.repo FROM entities e "
        "JOIN usages u ON u.entity_id = e.entity_id "
        "WHERE u.context LIKE 'interest:%' AND e.visibility = 'public'"
    ).fetchall()
    for r in seed_rows:
        relevance_terms.add(str(r["entity_id"]).lower())
        if r["name"]:
            relevance_terms.add(str(r["name"]).lower())
            if "/" in str(r["name"]):
                relevance_terms.add(str(r["name"]).lower().rsplit("/", 1)[-1])
        if r["repo"]:
            relevance_terms.add(str(r["repo"]).lower())
            relevance_terms.add(str(r["repo"]).lower().rsplit("/", 1)[-1])

    in_use_rows = ctx.db.execute(
        "SELECT DISTINCT e.entity_id, e.name, e.repo FROM entities e "
        "JOIN usages u ON u.entity_id = e.entity_id "
        "WHERE u.context NOT LIKE 'interest:%' AND u.direct = 1 AND e.visibility = 'public'"
    ).fetchall()
    in_use_names: set[str] = set()
    for r in in_use_rows:
        in_use_names.add(str(r["entity_id"]).lower())
        if r["name"]:
            n_low = str(r["name"]).strip().lower()
            in_use_names.add(n_low)
            if "/" in n_low:
                in_use_names.add(n_low.rsplit("/", 1)[-1])
        if r["repo"]:
            rp_low = str(r["repo"]).strip().lower()
            in_use_names.add(rp_low)
            if "/" in rp_low:
                in_use_names.add(rp_low.rsplit("/", 1)[-1])

    return category_terms, relevance_terms, in_use_names


def _source_momentum_score(metrics: Mapping[str, float]) -> float:
    vel = float(metrics.get("star_velocity") or (metrics.get("stars_7d", 0.0) / 7.0) or 0.0)
    stars = float(metrics.get("stars") or 0.0)
    dl = float(
        metrics.get("downloads_30d")
        or metrics.get("recent_downloads")
        or metrics.get("downloads")
        or 0.0
    )
    votes = float(metrics.get("votes") or metrics.get("score") or 0.0)
    return vel * 100.0 + stars + (dl / 100.0) + votes * 10.0


def promote(
    ctx: Ctx,
    results: Sequence[SourceResult | RawSignal] | None = None,
    *,
    inventory_only: bool = False,
    fetch_gates: bool = True,
) -> dict[str, Any]:
    """Promote in-use entities and relevant discovery candidates into `items` (Spec 11 §6, A5)."""
    ctx.reload_profile()

    # 1. Sync in_use items from usages (non-interest contexts, direct dependencies only)
    used_rows = ctx.db.execute(
        "SELECT DISTINCT e.entity_id, e.kind, e.quadrant "
        "FROM entities e JOIN usages u ON u.entity_id = e.entity_id "
        "WHERE u.context NOT LIKE 'interest:%' AND u.direct = 1"
    ).fetchall()
    used_eids = {str(r["entity_id"]) for r in used_rows}

    in_use_created = 0
    for r in used_rows:
        eid = str(r["entity_id"])
        _iid, created = ensure_item(
            ctx,
            eid,
            in_use=True,
            ring="adopt",
            state="active",
            quadrant=str(r["quadrant"]) if r["quadrant"] else None,
        )
        if created:
            in_use_created += 1

    # Clear in_use flag on items whose entities are no longer in non-interest usages
    all_in_use_items = ctx.db.execute(
        "SELECT item_id, entity_id FROM items WHERE in_use = 1"
    ).fetchall()
    for ir in all_in_use_items:
        if str(ir["entity_id"]) not in used_eids:
            ctx.db.execute(
                "UPDATE items SET in_use = 0 WHERE item_id = ?",
                (str(ir["item_id"]),),
            )
    ctx.db.commit()

    if inventory_only:
        stats = {
            "in_use_created": in_use_created,
            "candidates_promoted": 0,
            "candidates_fetched": 0,
            "deferred": 0,
            "suppressed": {
                "anti_interest": 0,
                "seen": 0,
                "not_relevant": 0,
                "private": 0,
                "deferred": 0,
            },
        }
        db.kv_set(ctx.db, "last_promote_stats_json", json.dumps(stats, separators=(",", ":")))
        return stats

    # If raw signals/results were passed, persist any that aren't in DB yet and record hints
    hint_by_dedupe: dict[str, str] = {}
    metrics_by_dedupe: dict[str, dict[str, float]] = {}
    if results:
        for entry in results:
            sigs = entry.signals if isinstance(entry, SourceResult) else [entry]
            for s in sigs:
                dedupe = s.dedupe_key or f"{s.source_id}|{s.type}|{s.external_id}"
                if s.entity_hint:
                    hint_by_dedupe[dedupe] = s.entity_hint
                if s.metrics:
                    metrics_by_dedupe[dedupe] = {
                        k: float(v) for k, v in s.metrics.items() if isinstance(v, int | float)
                    }

    sig_rows = ctx.db.execute(
        "SELECT signal_id, entity_id, source_id, type, confidence, external_id, "
        "title, url, published_at, fetched_at, untrusted_excerpt, payload_json, "
        "dedupe_key, last_surfaced_at "
        "FROM signals "
        "WHERE type IN ('horizon', 'contender', 'technique') AND withdrawn = 0 "
        "ORDER BY signal_id ASC"
    ).fetchall()

    category_terms, relevance_terms, in_use_names = _build_relevance_sets(ctx)
    compiled_anti = _prepare_anti_interest_matcher(ctx.profile.anti_interests)
    cat_parts = _category_parts(category_terms)
    cat_part_re = _compile_word_boundary_re(cat_parts, min_len=3)
    relevance_re = _compile_word_boundary_re(frozenset(relevance_terms), min_len=3)
    in_use_re = _compile_word_boundary_re(frozenset(in_use_names), min_len=2)
    cutoff_seen = (
        (ctx.now - timedelta(days=SEEN_WINDOW_DAYS)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    suppressed = {
        "anti_interest": 0,
        "seen": 0,
        "not_relevant": 0,
        "private": 0,
        "deferred": 0,
    }

    # Group signals by entity_hint
    grouped: dict[str, dict[str, Any]] = {}
    for row in sig_rows:
        r_dict = dict(row)
        dedupe = str(r_dict["dedupe_key"])
        hint = hint_by_dedupe.get(dedupe) or _extract_signal_entity_hint(r_dict)
        if not hint:
            continue

        # Seen within 30 days check
        last_surf = r_dict.get("last_surfaced_at")
        if last_surf and str(last_surf) >= cutoff_seen:
            suppressed["seen"] += 1
            continue

        ent_preview = projects.entity_for_usage(hint)
        quad = default_quadrant(ent_preview.kind, entity_id=ent_preview.entity_id)
        title = str(r_dict.get("title") or "")
        excerpt = str(r_dict.get("untrusted_excerpt") or "")

        if _matches_anti_interest(
            ctx.profile.anti_interests,
            entity_hint=ent_preview.entity_id,
            title=title,
            excerpt=excerpt,
            quadrant=quad,
            compiled_anti=compiled_anti,
        ):
            suppressed["anti_interest"] += 1
            continue

        payload_raw = r_dict.get("payload_json")
        payload: dict[str, Any] = {}
        if isinstance(payload_raw, str) and payload_raw.strip():
            try:
                parsed = json.loads(payload_raw)
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                payload = {}

        if not _matches_relevance_criteria(
            ctx,
            entity_hint=ent_preview.entity_id,
            title=title,
            excerpt=excerpt,
            quadrant=quad,
            source_id=str(r_dict.get("source_id") or ""),
            sig_type=str(r_dict.get("type") or "horizon"),
            payload=payload,
            relevance_terms=relevance_terms,
            in_use_names=in_use_names,
            category_terms=category_terms,
            cat_parts=cat_parts,
            cat_part_re=cat_part_re,
            relevance_re=relevance_re,
            in_use_re=in_use_re,
        ):
            suppressed["not_relevant"] += 1
            continue

        sig_metrics = {**_extract_signal_metrics(r_dict), **metrics_by_dedupe.get(dedupe, {})}
        canon_eid = ent_preview.entity_id
        bucket = grouped.get(canon_eid)
        if bucket is None:
            grouped[canon_eid] = {
                "entity_id": canon_eid,
                "quadrant": quad,
                "signal_ids": [int(r_dict["signal_id"])],
                "metrics": sig_metrics,
                "observed_at": str(r_dict.get("fetched_at") or ctx.now_iso),
            }
        else:
            bucket["signal_ids"].append(int(r_dict["signal_id"]))
            bucket["metrics"].update(sig_metrics)

    # Check if any grouped entity already has a recently surfaced signal in DB
    qualifying: list[dict[str, Any]] = []
    for canon_eid, info in grouped.items():
        surf_row = ctx.db.execute(
            "SELECT 1 FROM signals WHERE entity_id = ? AND last_surfaced_at IS NOT NULL "
            "AND last_surfaced_at >= ? LIMIT 1",
            (canon_eid, cutoff_seen),
        ).fetchone()
        if surf_row is not None:
            suppressed["seen"] += 1
            continue

        eid, _kind, vis = ensure_entity(ctx, canon_eid, quadrant=info["quadrant"])
        for sig_id in info["signal_ids"]:
            ctx.db.execute(
                "UPDATE signals SET entity_id = ? WHERE signal_id = ?",
                (eid, sig_id),
            )

        # Write source-provided metrics each time (Spec 11 §6)
        obs_at = info["observed_at"]
        for m_name, m_val in info["metrics"].items():
            ctx.db.execute(
                "INSERT INTO metrics(entity_id, metric, value, observed_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(entity_id, metric, observed_at) DO UPDATE SET value = excluded.value",
                (eid, m_name, float(m_val), obs_at),
            )
        ctx.db.commit()

        if vis != "public":
            suppressed["private"] += 1
            continue

        # Check if already an in-use item
        if eid in used_eids:
            continue

        info["entity_id"] = eid
        info["momentum_sort"] = _source_momentum_score(info["metrics"])
        qualifying.append(info)

    # Sort by source momentum descending, tie-breaking deterministically by entity_id
    qualifying.sort(key=lambda x: (-float(x["momentum_sort"]), str(x["entity_id"])))

    selected = qualifying[:DAILY_CANDIDATE_CAP]
    deferred_count = max(0, len(qualifying) - DAILY_CANDIDATE_CAP)
    suppressed["deferred"] = deferred_count

    promoted_items: list[str] = []
    created_items: list[str] = []
    fetched_count = 0

    for cand in selected:
        eid = str(cand["entity_id"])
        item_id, created = ensure_item(
            ctx,
            eid,
            in_use=False,
            state="candidate",
            quadrant=str(cand["quadrant"]),
        )
        promoted_items.append(item_id)
        if created:
            created_items.append(item_id)

        gate_inputs: dict[str, Any] = {}
        if fetch_gates:
            gate_inputs = registries.fetch_metrics(ctx, eid)
            fetched_count += 1
            db.kv_set(
                ctx.db,
                f"gate_inputs:{eid}",
                json.dumps(gate_inputs, separators=(",", ":"), sort_keys=True),
            )
        evaluate.prescore(ctx, item_id, gate_inputs=gate_inputs)

    stats = {
        "in_use_created": in_use_created,
        "candidates_promoted": len(created_items),
        "candidates_active": len(promoted_items),
        "candidates_fetched": fetched_count,
        "deferred": deferred_count,
        "promoted_item_ids": promoted_items,
        "suppressed": suppressed,
    }
    db.kv_set(ctx.db, "last_promote_stats_json", json.dumps(stats, separators=(",", ":")))
    return stats
