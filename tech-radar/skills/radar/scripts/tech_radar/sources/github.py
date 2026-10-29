"""GitHub sources for Task 5: gh_releases, gh_changelog_file, and gh_repo_meta (Spec 03)."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .. import sanitize
from ..errors import FetchError, RadarError
from .base import FetchMeta, RawSignal, SourceSpec, normalize_timestamp

if TYPE_CHECKING:
    from ..context import Ctx

_REPO_IN_ENDPOINT_RE = re.compile(r"repos/([^/]+/[^/?]+)")


def _repo_from_meta(meta: FetchMeta, obj: Mapping[str, Any] | None = None) -> str | None:
    if meta.params.get("repo"):
        return str(meta.params["repo"]).strip().lower()
    if meta.entity_id and meta.entity_id.startswith("gh:"):
        return meta.entity_id[len("gh:") :].strip().lower()
    if obj and isinstance(obj.get("full_name"), str) and "/" in obj["full_name"]:
        return str(obj["full_name"]).strip().lower()
    m = _REPO_IN_ENDPOINT_RE.search(meta.url)
    if m:
        return m.group(1).strip().lower()
    return None


def covered_public_repos(ctx: Ctx) -> list[tuple[str, str]]:
    """Return sorted `(repo_slug, entity_id)` for used public entities + watchlist (Spec 12 A22)."""
    rows = ctx.db.execute(
        "SELECT DISTINCT e.entity_id, e.repo "
        "FROM entities e JOIN usages u ON u.entity_id = e.entity_id "
        "WHERE e.visibility = 'public' "
        "AND (u.context NOT LIKE 'interest:%' OR u.context = 'interest:watchlist')"
    ).fetchall()
    seen: dict[str, str] = {}
    for row in rows:
        eid = str(row["entity_id"])
        repo = str(row["repo"] or "").strip().lower()
        if not repo and eid.startswith("gh:"):
            repo = eid[len("gh:") :].strip().lower()
        if "/" in repo and repo not in seen:
            seen[repo] = eid
    return sorted(seen.items())


def parse_gh_releases(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse `repos/{o}/{r}/releases` JSON list into feature/lifecycle `RawSignal`s."""
    try:
        arr = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid gh_releases JSON: {exc}") from exc

    if not isinstance(arr, list):
        raise FetchError("gh_releases payload must be a JSON list")

    repo_slug = _repo_from_meta(meta)
    out: list[RawSignal] = []

    for rel in arr:
        if not isinstance(rel, Mapping):
            continue
        if rel.get("draft") is True:
            continue
        tag = str(rel.get("tag_name") or rel.get("name") or "").strip()
        if not tag:
            continue
        html_url = str(rel.get("html_url") or rel.get("url") or "").strip()
        rel_repo = repo_slug
        if not rel_repo and html_url:
            m = re.search(r"github\.com/([^/]+/[^/]+)", html_url)
            if m:
                rel_repo = m.group(1).lower()
        entity_hint = meta.entity_id or (f"gh:{rel_repo}" if rel_repo else None)

        rel_id = str(rel.get("id") or tag)
        name = str(rel.get("name") or tag).strip()
        body_raw = str(rel.get("body") or "").strip()
        pub = (
            normalize_timestamp(
                rel.get("published_at") or rel.get("created_at") or rel.get("updated_at")
            )
            or meta.fetched_at
        )
        title = (
            f"{rel_repo or 'repo'} {tag}: {name}" if name != tag else f"{rel_repo or 'repo'} {tag}"
        )
        cleaned_body = sanitize.clean(body_raw, limit=4000)

        out.append(
            RawSignal(
                source_id="gh_releases",
                type="feature",
                external_id=f"{rel_repo or 'repo'}:{tag}:{rel_id}",
                title=title,
                url=html_url or None,
                published_at=pub,
                untrusted_excerpt=body_raw or title,
                entity_hint=entity_hint,
                confidence="confirmed",
                payload={
                    "repo": rel_repo,
                    "tag_name": tag,
                    "name": name,
                    "prerelease": bool(rel.get("prerelease")),
                    "target_commitish": rel.get("target_commitish"),
                    "published_at": pub,
                    "body": cleaned_body,
                },
                dedupe_key=f"gh_releases|feature|{entity_hint or rel_repo or 'repo'}|{tag}",
            )
        )
    return out


def _run_gh_releases(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    out: list[RawSignal] = []
    for repo_slug, eid in covered_public_repos(ctx):
        endpoint = f"repos/{repo_slug}/releases?per_page=10"
        try:
            res = ctx.gh.api(endpoint)
        except RadarError:
            continue
        if res.returncode != 0 or not res.stdout.strip():
            continue
        meta = FetchMeta(
            url=endpoint,
            fetched_at=ctx.now_iso,
            status=200,
            params={"repo": repo_slug},
            source_id=spec.id,
            entity_id=eid,
        )
        try:
            out.extend(parse_gh_releases(res.stdout.encode("utf-8"), meta))
        except FetchError:
            continue
    return out


def decode_github_contents(obj: Mapping[str, Any]) -> str:
    """Decode a GitHub `/contents/<path>` JSON object's `content` field."""
    raw_content = str(obj.get("content") or "")
    encoding = str(obj.get("encoding") or "base64").lower()
    if not raw_content:
        return ""
    if encoding == "base64":
        try:
            decoded_bytes = base64.b64decode(raw_content)
            return decoded_bytes.decode("utf-8", errors="ignore")
        except (binascii.Error, ValueError):
            return raw_content
    return raw_content


def parse_gh_changelog_file(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse `repos/{o}/{r}/contents/{CHANGELOG.md|NEWS.md|HISTORY.md}` JSON into `RawSignal`s."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid gh_changelog_file JSON: {exc}") from exc

    if not isinstance(obj, Mapping):
        raise FetchError("gh_changelog_file payload must be a JSON object")

    sha = str(obj.get("sha") or "head").strip()
    path = str(obj.get("path") or obj.get("name") or "CHANGELOG.md").strip()
    html_url = str(obj.get("html_url") or obj.get("url") or "").strip() or None
    repo_slug = _repo_from_meta(meta, obj)
    if not repo_slug and html_url:
        m = re.search(r"github\.com/([^/]+/[^/]+)", html_url)
        if m:
            repo_slug = m.group(1).lower()

    decoded = decode_github_contents(obj)
    if not decoded.strip():
        return []

    entity_hint = meta.entity_id or (f"gh:{repo_slug}" if repo_slug else None)
    cleaned_body = sanitize.clean(decoded, limit=4000)
    return [
        RawSignal(
            source_id="gh_changelog_file",
            type="feature",
            external_id=f"{repo_slug or 'repo'}:{path}:{sha}",
            title=f"{repo_slug or 'repo'} {path} ({sha[:8]})",
            url=html_url,
            published_at=meta.fetched_at,
            untrusted_excerpt=decoded,
            entity_hint=entity_hint,
            confidence="confirmed",
            payload={
                "repo": repo_slug,
                "path": path,
                "sha": sha,
                "body": cleaned_body,
            },
            dedupe_key=f"gh_changelog_file|feature|{entity_hint or repo_slug or 'repo'}|{sha}",
        )
    ]


def _run_gh_changelog_file(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    out: list[RawSignal] = []
    for repo_slug, eid in covered_public_repos(ctx):
        has_rel = ctx.db.execute(
            "SELECT 1 FROM signals WHERE source_id = 'gh_releases' AND entity_id = ? LIMIT 1",
            (eid,),
        ).fetchone()
        if has_rel is not None:
            continue
        for fname in ("CHANGELOG.md", "NEWS.md", "HISTORY.md"):
            endpoint = f"repos/{repo_slug}/contents/{fname}"
            try:
                res = ctx.gh.api(endpoint)
            except RadarError:
                continue
            if res.returncode != 0 or not res.stdout.strip():
                continue
            meta = FetchMeta(
                url=endpoint,
                fetched_at=ctx.now_iso,
                status=200,
                params={"repo": repo_slug},
                source_id=spec.id,
                entity_id=eid,
            )
            try:
                parsed = parse_gh_changelog_file(res.stdout.encode("utf-8"), meta)
                if parsed:
                    out.extend(parsed)
                    break
            except FetchError:
                continue
    return out


def parse_gh_repo_meta(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse `repos/{o}/{r}` JSON into a lifecycle `RawSignal` with repo health metadata."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid gh_repo_meta JSON: {exc}") from exc

    if not isinstance(obj, Mapping):
        raise FetchError("gh_repo_meta payload must be a JSON object")

    repo_slug = _repo_from_meta(meta, obj)
    if not repo_slug:
        name = str(obj.get("name") or "").strip().lower()
        owner_obj = obj.get("owner")
        owner = (
            str(owner_obj.get("login") or "").strip().lower()
            if isinstance(owner_obj, Mapping)
            else ""
        )
        if owner and name:
            repo_slug = f"{owner}/{name}"
    if not repo_slug:
        return []

    entity_hint = meta.entity_id or f"gh:{repo_slug}"
    archived = bool(obj.get("archived") is True)
    disabled = bool(obj.get("disabled") is True)
    pushed_at = normalize_timestamp(obj.get("pushed_at") or obj.get("updated_at"))
    stars = int(obj.get("stargazers_count") or 0)
    forks = int(obj.get("forks_count") or 0)
    owner_obj = obj.get("owner")
    owner_login = (
        str(owner_obj.get("login") or "").strip() if isinstance(owner_obj, Mapping) else ""
    )
    lic_obj = obj.get("license")
    spdx_id = (
        str(lic_obj.get("spdx_id") or lic_obj.get("key") or "")
        if isinstance(lic_obj, Mapping)
        else ""
    )
    desc = str(obj.get("description") or f"GitHub repository {repo_slug}").strip()
    html_url = str(obj.get("html_url") or f"https://github.com/{repo_slug}").strip()

    title = (
        f"GitHub repository {repo_slug} is archived"
        if archived
        else f"GitHub repository metadata for {repo_slug}"
    )
    return [
        RawSignal(
            source_id="gh_repo_meta",
            type="lifecycle",
            external_id=f"gh_repo_meta:{repo_slug}",
            title=title,
            url=html_url,
            published_at=pushed_at or meta.fetched_at,
            untrusted_excerpt=desc,
            entity_hint=entity_hint,
            metrics={"stars": stars, "forks": forks},
            confidence="confirmed",
            payload={
                "repo": repo_slug,
                "archived": archived,
                "disabled": disabled,
                "pushed_at": pushed_at,
                "stargazers_count": stars,
                "forks_count": forks,
                "owner_login": owner_login,
                "previous_owner_login": obj.get("previous_owner_login"),
                "license": spdx_id or None,
                "default_branch": obj.get("default_branch", "main"),
            },
            dedupe_key=f"gh_repo_meta|lifecycle|{entity_hint}",
        )
    ]


def _run_gh_repo_meta(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    out: list[RawSignal] = []
    for repo_slug, eid in covered_public_repos(ctx):
        endpoint = f"repos/{repo_slug}"
        try:
            res = ctx.gh.api(endpoint)
        except RadarError:
            continue
        if res.returncode != 0 or not res.stdout.strip():
            continue
        meta = FetchMeta(
            url=endpoint,
            fetched_at=ctx.now_iso,
            status=200,
            params={"repo": repo_slug},
            source_id=spec.id,
            entity_id=eid,
        )
        try:
            out.extend(parse_gh_repo_meta(res.stdout.encode("utf-8"), meta))
        except FetchError:
            continue
    return out


GH_RELEASES_SPEC = SourceSpec(
    id="gh_releases",
    req=("FEAT-01",),
    archetype="gh_api",
    url="repos/{o}/{r}/releases?per_page=10",
    cadence="daily",
    hosts=("api.github.com",),
    signal_types=("feature", "lifecycle"),
    ttl_hours=24,
    parser=parse_gh_releases,
    runner=_run_gh_releases,
)

GH_CHANGELOG_FILE_SPEC = SourceSpec(
    id="gh_changelog_file",
    req=("FEAT-02",),
    archetype="gh_api",
    url="repos/{o}/{r}/contents/CHANGELOG.md",
    cadence="weekly",
    hosts=("api.github.com",),
    signal_types=("feature",),
    ttl_hours=24 * 7,
    parser=parse_gh_changelog_file,
    runner=_run_gh_changelog_file,
)

GH_REPO_META_SPEC = SourceSpec(
    id="gh_repo_meta",
    req=("LIFE-01", "LIFE-06", "LIFE-07", "LIFE-09"),
    archetype="gh_api",
    url="repos/{o}/{r}",
    cadence="daily",
    hosts=("api.github.com",),
    signal_types=("lifecycle",),
    ttl_hours=24,
    parser=parse_gh_repo_meta,
    runner=_run_gh_repo_meta,
)

GITHUB_SPECS: tuple[SourceSpec, ...] = (
    GH_RELEASES_SPEC,
    GH_CHANGELOG_FILE_SPEC,
    GH_REPO_META_SPEC,
)
