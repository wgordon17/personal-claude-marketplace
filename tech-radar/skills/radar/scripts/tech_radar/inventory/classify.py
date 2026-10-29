"""Visibility classification (T3 control, Spec 04, Spec 12 A8) and public_terms() builder."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from .. import profile as profile_mod
from ..errors import EgressDenied, FetchError, RadarError

if TYPE_CHECKING:
    from ..context import Ctx

CACHE_TTL_DAYS = 30


def matches_private_scope(name_or_slug: str, private_scopes: Sequence[str]) -> bool:
    """Return True if a package name, repo slug, or entity_id matches a private scope."""
    raw = name_or_slug.strip().lower()
    if ":" in raw and not raw.startswith("http"):
        raw = raw.split(":", 1)[1]
    for prefix in ("pypi/", "npm/", "cargo/", "golang/", "mise/", "formula/", "cask/"):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
            break
    stripped = raw.lstrip("@")

    for scope_entry in private_scopes:
        scope = scope_entry.strip().lower().lstrip("@").rstrip("/")
        if not scope:
            continue
        if scope.endswith("*"):
            stem = scope[:-1].rstrip("/-")
            if stem and (
                stripped == stem
                or stripped.startswith(f"{stem}/")
                or stripped.startswith(f"{stem}-")
                or stripped.startswith(f"{stem}_")
            ):
                return True
            continue
        if (
            stripped == scope
            or stripped.startswith(f"{scope}/")
            or stripped.startswith(f"{scope}-")
            or stripped.startswith(f"{scope}_")
        ):
            return True
    return False


def _has_fresh_visibility_cache(ctx: Ctx, entity_id: str) -> str | None:
    ent_row = ctx.db.execute(
        "SELECT visibility FROM entities WHERE entity_id = ?",
        (entity_id,),
    ).fetchone()
    if ent_row is None:
        return None
    cached_vis = str(ent_row["visibility"] or "unknown")
    if cached_vis not in ("public", "private"):
        return None

    m_row = ctx.db.execute(
        "SELECT observed_at FROM metrics "
        "WHERE entity_id = ? AND metric = 'visibility_checked' "
        "ORDER BY observed_at DESC LIMIT 1",
        (entity_id,),
    ).fetchone()
    if m_row is None or not m_row["observed_at"]:
        return None

    try:
        obs_dt = datetime.fromisoformat(str(m_row["observed_at"]).replace("Z", "+00:00"))
        if obs_dt.tzinfo is None:
            obs_dt = obs_dt.replace(tzinfo=UTC)
        if (ctx.now - obs_dt) < timedelta(days=CACHE_TTL_DAYS):
            return cached_vis
    except ValueError:
        return None
    return None


def _has_private_usage_marker(ctx: Ctx, entity_id: str) -> bool:
    rows = ctx.db.execute(
        "SELECT attrs_json FROM usages WHERE entity_id = ? AND attrs_json IS NOT NULL",
        (entity_id,),
    ).fetchall()
    for row in rows:
        try:
            attrs = json.loads(str(row["attrs_json"]))
        except json.JSONDecodeError:
            continue
        if isinstance(attrs, dict) and (
            attrs.get("private") is True or attrs.get("private_index") is True
        ):
            return True
    return False


def _record_visibility(
    ctx: Ctx,
    entity_id: str,
    vis: str,
    *,
    cache_check: bool,
    commit: bool = True,
) -> str:
    ctx.db.execute(
        "UPDATE entities SET visibility = ? WHERE entity_id = ?",
        (vis, entity_id),
    )
    if cache_check:
        val = 1.0 if vis == "public" else 0.0
        ctx.db.execute(
            "INSERT INTO metrics(entity_id, metric, value, observed_at) "
            "VALUES (?, 'visibility_checked', ?, ?) "
            "ON CONFLICT(entity_id, metric, observed_at) DO UPDATE SET value = excluded.value",
            (entity_id, val, ctx.now_iso),
        )
    if commit:
        ctx.db.commit()
    return vis


def _check_registry_http(
    ctx: Ctx,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
) -> tuple[str, bool]:
    """Return (visibility, should_cache) from an HTTP GET registry check."""
    try:
        resp = ctx.http.fetch(url, method="GET", headers=headers, ttl_hours=24 * CACHE_TTL_DAYS)
        if resp.status == 200:
            return "public", True
        return "private", True
    except FetchError as exc:
        if exc.status == 404:
            return "private", True
        return "private", False
    except EgressDenied:
        return "private", False


def _check_github_repo(ctx: Ctx, slug: str) -> tuple[str, bool]:
    """Return (visibility, should_cache) from `gh api repos/<o>/<r>`."""
    clean_slug = slug.strip("/").lower()
    if "/" not in clean_slug:
        return "private", True
    try:
        res = ctx.gh(["api", f"repos/{clean_slug}"])
    except RadarError:
        return "private", False
    if res.returncode != 0 or not res.stdout.strip():
        return "private", False
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        return "private", False
    if isinstance(data, dict):
        if data.get("private") is False:
            return "public", True
        if data.get("private") is True:
            return "private", True
    return "private", False


def visibility(
    ctx: Ctx,
    entity_id: str,
    *,
    force_refresh: bool = False,
    private_scopes: Sequence[str] | None = None,
    commit: bool = True,
) -> str:
    """Classify an entity's visibility as 'public' or 'private' (fail closed on errors)."""
    if private_scopes is None:
        scopes = profile_mod.load_private_scopes(ctx.db)
        ctx.profile.private_scopes = scopes
    else:
        scopes = list(private_scopes)

    ent_row = ctx.db.execute(
        "SELECT name, repo, kind, ecosystem FROM entities WHERE entity_id = ?",
        (entity_id,),
    ).fetchone()
    name = str(ent_row["name"]) if ent_row is not None else entity_id.split("/")[-1]
    repo = str(ent_row["repo"]) if ent_row is not None and ent_row["repo"] else None

    if (
        matches_private_scope(entity_id, scopes)
        or matches_private_scope(name, scopes)
        or (repo and matches_private_scope(repo, scopes))
    ):
        return _record_visibility(ctx, entity_id, "private", cache_check=True, commit=commit)

    if _has_private_usage_marker(ctx, entity_id):
        return _record_visibility(ctx, entity_id, "private", cache_check=True, commit=commit)

    if not force_refresh:
        cached = _has_fresh_visibility_cache(ctx, entity_id)
        if cached is not None:
            return cached

    if entity_id.startswith("pkg:pypi/"):
        pkg = entity_id[len("pkg:pypi/") :].strip().lower()
        vis, cache_ok = _check_registry_http(ctx, f"https://pypi.org/simple/{pkg}/")
        return _record_visibility(ctx, entity_id, vis, cache_check=cache_ok, commit=commit)

    if entity_id.startswith("pkg:npm/"):
        pkg = entity_id[len("pkg:npm/") :].strip()
        vis, cache_ok = _check_registry_http(
            ctx,
            f"https://registry.npmjs.org/{pkg}",
            headers={"Accept": "application/vnd.npm.install-v1+json"},
        )
        return _record_visibility(ctx, entity_id, vis, cache_check=cache_ok, commit=commit)

    if entity_id.startswith("pkg:cargo/"):
        crate = entity_id[len("pkg:cargo/") :].strip()
        vis, cache_ok = _check_registry_http(ctx, f"https://crates.io/api/v1/crates/{crate}")
        return _record_visibility(ctx, entity_id, vis, cache_check=cache_ok, commit=commit)

    if entity_id.startswith("gh:"):
        slug = entity_id[len("gh:") :].strip().lower()
        vis, cache_ok = _check_github_repo(ctx, slug)
        return _record_visibility(ctx, entity_id, vis, cache_check=cache_ok, commit=commit)

    if entity_id.startswith("bin:"):
        if repo:
            vis, cache_ok = _check_github_repo(ctx, repo)
            return _record_visibility(ctx, entity_id, vis, cache_check=cache_ok, commit=commit)
        return _record_visibility(ctx, entity_id, "private", cache_check=True, commit=commit)

    if entity_id.startswith("svc:"):
        return _record_visibility(ctx, entity_id, "private", cache_check=True, commit=commit)

    if entity_id.startswith(
        (
            "brew:formula/",
            "brew:cask/",
            "pkg:mise/",
            "pkg:golang/",
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
            "runtime:",
            "technique:",
            "harness:tool/",
        )
    ):
        return _record_visibility(ctx, entity_id, "public", cache_check=True, commit=commit)

    return _record_visibility(ctx, entity_id, "private", cache_check=False, commit=commit)


def classify_all(ctx: Ctx, *, force_refresh: bool = False) -> dict[str, str]:
    """Classify every entity in radar.db and return {entity_id: visibility}."""
    scopes = profile_mod.load_private_scopes(ctx.db)
    ctx.profile.private_scopes = scopes
    rows = ctx.db.execute("SELECT entity_id FROM entities ORDER BY entity_id").fetchall()
    results: dict[str, str] = {}
    for row in rows:
        eid = str(row["entity_id"])
        results[eid] = visibility(
            ctx,
            eid,
            force_refresh=force_refresh,
            private_scopes=scopes,
            commit=False,
        )
    ctx.db.commit()
    return results


def public_terms(ctx: Ctx) -> list[str]:
    """Return sorted public package/repo names + TASTE_PROFILE category terms (Spec 04, Spec 12 A8).

    Never includes project names, local paths, `local:` keys, or any private package/repo/scope.
    """
    scopes = profile_mod.load_private_scopes(ctx.db)
    ctx.profile.private_scopes = scopes

    private_ban: set[str] = set()
    priv_ent_rows = ctx.db.execute(
        "SELECT entity_id, name, repo FROM entities WHERE visibility != 'public'"
    ).fetchall()
    for r in priv_ent_rows:
        if r["name"]:
            private_ban.add(str(r["name"]).strip().lower())
        if r["repo"]:
            private_ban.add(str(r["repo"]).strip().lower())
            private_ban.add(str(r["repo"]).strip().lower().split("/")[-1])

    proj_rows = ctx.db.execute(
        "SELECT project_key, display_path, main_worktree, visibility, alias FROM projects"
    ).fetchall()
    all_project_names: set[str] = set()
    for pr in proj_rows:
        pkey = str(pr["project_key"])
        dpath = str(pr["display_path"])
        mwt = str(pr["main_worktree"])
        all_project_names.add(pkey.lower())
        all_project_names.add(dpath.lower())
        all_project_names.add(mwt.lower())
        if "/" in dpath:
            all_project_names.add(dpath.rstrip("/").split("/")[-1].lower())
        if "/" in pkey and not pkey.startswith("local:"):
            repo_tail = pkey.rstrip("/").split("/")[-1].lower()
            all_project_names.add(repo_tail)
            if str(pr["visibility"]) != "public":
                private_ban.add(repo_tail)

    terms: set[str] = set()
    pub_rows = ctx.db.execute(
        "SELECT entity_id, kind, name, repo FROM entities "
        "WHERE visibility = 'public' AND kind NOT IN ('service', 'runtime')"
    ).fetchall()
    for row in pub_rows:
        eid = str(row["entity_id"])
        if eid.startswith("harness:tool/"):
            continue
        name = str(row["name"]).strip()
        if not name or name.startswith(("local:", "/")):
            continue
        low = name.lower()
        if (
            low in private_ban
            or low in all_project_names
            or matches_private_scope(name, scopes)
            or any(priv and priv in low for priv in private_ban if len(priv) >= 4)
        ):
            continue
        terms.add(name)

    for cat in ctx.profile.categories:
        clean_cat = cat.strip()
        if not clean_cat or clean_cat.startswith(("local:", "/")):
            continue
        low_cat = clean_cat.lower()
        if (
            low_cat in private_ban
            or low_cat in all_project_names
            or matches_private_scope(clean_cat, scopes)
        ):
            continue
        terms.add(clean_cat)

    return sorted(terms)
