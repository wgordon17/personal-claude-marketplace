"""GitHub sources: releases, changelog, repo meta, search, trending, topics, stars, diffs."""

from __future__ import annotations

import base64
import binascii
import json
import re
import urllib.parse
from collections.abc import Mapping
from datetime import timedelta
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any

from .. import sanitize
from ..errors import FetchError, RadarError
from .base import (
    FetchMeta,
    RawSignal,
    SourceSpec,
    infer_github_entity_hint,
    lookback_since,
    normalize_timestamp,
)

if TYPE_CHECKING:
    from ..context import Ctx

_REPO_IN_ENDPOINT_RE = re.compile(r"repos/([^/]+/[^/?]+)")
_SAFE_SLUG_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

DEFAULT_TRENDING_LANGUAGES: tuple[str, ...] = ("python", "rust", "typescript", "go")
GH_TOPICS: tuple[str, ...] = (
    "mcp",
    "claude-code",
    "agent-skills",
    "cli",
    "developer-tools",
)
AWESOME_REPOS: tuple[str, ...] = (
    "hesreallyhim/awesome-claude-code",
    "punkpeye/awesome-mcp-servers",
    "ibraheemdev/modern-unix",
    "vinta/awesome-python",
)


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
        "AND ((u.context NOT LIKE 'interest:%' AND u.direct = 1) "
        "OR u.context = 'interest:watchlist')"
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


def adopt_trial_public_repos(ctx: Ctx) -> list[tuple[str, str]]:
    """Return sorted `(repo_slug, entity_id)` for ADOPT/TRIAL public items + watchlist."""
    rows = ctx.db.execute(
        "SELECT DISTINCT e.entity_id, e.repo "
        "FROM entities e "
        "LEFT JOIN items i ON i.entity_id = e.entity_id "
        "LEFT JOIN usages u ON u.entity_id = e.entity_id "
        "WHERE e.visibility = 'public' "
        "AND ("
        "(i.ring IN ('adopt', 'trial') AND i.state != 'rejected') "
        "OR (u.context = 'interest:watchlist' "
        "AND COALESCE(i.ring, '') != 'hold' "
        "AND COALESCE(i.state, '') != 'rejected')"
        ")"
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


def _parse_repo_items_array(
    items: list[Any],
    meta: FetchMeta,
    *,
    source_id: str,
    signal_type: str = "horizon",
) -> list[RawSignal]:
    out: list[RawSignal] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        full_name = str(item.get("full_name") or "").strip().lower()
        if not full_name or "/" not in full_name:
            name = str(item.get("name") or "").strip().lower()
            owner_obj = item.get("owner")
            owner = (
                str(owner_obj.get("login") or "").strip().lower()
                if isinstance(owner_obj, Mapping)
                else ""
            )
            if owner and name:
                full_name = f"{owner}/{name}"
        if not full_name:
            continue
        html_url = str(item.get("html_url") or f"https://github.com/{full_name}").strip()
        desc = str(item.get("description") or full_name).strip()
        stars = int(item.get("stargazers_count") or item.get("stars") or 0)
        forks = int(item.get("forks_count") or item.get("forks") or 0)
        pub = normalize_timestamp(
            item.get("pushed_at") or item.get("created_at") or item.get("updated_at")
        )
        out.append(
            RawSignal(
                source_id=source_id,
                type=signal_type,
                external_id=full_name,
                title=f"{full_name}: {desc}" if desc and desc != full_name else full_name,
                url=html_url,
                published_at=pub or meta.fetched_at,
                untrusted_excerpt=desc,
                entity_hint=f"gh:{full_name}",
                metrics={"stars": stars, "forks": forks},
                confidence="confirmed",
                payload={
                    "repo": full_name,
                    "stars": stars,
                    "forks": forks,
                    "language": item.get("language"),
                },
                dedupe_key=f"{source_id}|{signal_type}|{full_name}",
            )
        )
    return out


def parse_gh_search_rising(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse GitHub `search/repositories` rising repos JSON into horizon `RawSignal`s."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid gh_search_rising JSON: {exc}") from exc

    items = obj.get("items") if isinstance(obj, Mapping) else obj
    if not isinstance(items, list):
        raise FetchError("gh_search_rising payload must contain an items list")
    return _parse_repo_items_array(items, meta, source_id="gh_search_rising", signal_type="horizon")


def _profile_languages(ctx: Ctx) -> list[str]:
    langs: list[str] = []
    for raw in ctx.profile.languages:
        clean = re.sub(r"[^A-Za-z0-9+#-]", "", raw.strip().lower())
        if clean and clean not in langs:
            langs.append(clean)
    if not langs:
        langs = list(DEFAULT_TRENDING_LANGUAGES)
    return langs[:5]


def _run_gh_search_rising(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    since_date = (ctx.now - timedelta(days=90)).strftime("%Y-%m-%d")
    out: list[RawSignal] = []
    seen: set[str] = set()
    errors: list[str] = []

    for lang in _profile_languages(ctx):
        q = f"created:%3E{since_date}+stars:%3E100+language:{urllib.parse.quote(lang, safe='')}"
        endpoint = f"search/repositories?q={q}&sort=stars&per_page=10"
        try:
            res = ctx.gh.api(endpoint)
            if res.returncode != 0:
                errors.append(res.stderr or f"gh api failed for {lang}")
                continue
            meta = FetchMeta(
                url=endpoint,
                fetched_at=ctx.now_iso,
                status=200,
                params={"language": lang},
                source_id=spec.id,
            )
            for sig in parse_gh_search_rising(res.stdout.encode("utf-8"), meta):
                if sig.external_id not in seen:
                    seen.add(sig.external_id)
                    out.append(sig)
        except RadarError as exc:
            errors.append(str(exc))

    if not out and errors:
        raise FetchError("; ".join(errors))
    return out


class _GitHubTrendingHTMLParser(HTMLParser):
    """Stdlib HTMLParser for `https://github.com/trending/{lang}?since=weekly`."""

    def __init__(self) -> None:
        super().__init__()
        self.repos: list[tuple[str, str, int]] = []
        self._in_article = False
        self._in_h2 = False
        self._in_p = False
        self._current_slug: str | None = None
        self._desc_buf: list[str] = []
        self._article_text_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        t_low = tag.lower()
        if t_low == "article":
            self._in_article = True
            self._current_slug = None
            self._desc_buf = []
            self._article_text_buf = []
        elif t_low in ("h1", "h2", "h3") and self._in_article:
            self._in_h2 = True
        elif t_low == "a" and (self._in_h2 or self._in_article):
            href = attr_map.get("href", "").strip()
            if href.startswith("/") and href.count("/") == 2 and self._current_slug is None:
                slug = href.lstrip("/").lower()
                parts = slug.split("/")
                if len(parts) == 2 and parts[0] not in (
                    "trending",
                    "topics",
                    "orgs",
                    "login",
                    "signup",
                    "features",
                    "sponsors",
                ):
                    self._current_slug = slug
        elif t_low == "p" and self._in_article:
            self._in_p = True

    def handle_endtag(self, tag: str) -> None:
        t_low = tag.lower()
        if t_low in ("h1", "h2", "h3"):
            self._in_h2 = False
        elif t_low == "p":
            self._in_p = False
        elif t_low == "article" and self._in_article:
            self._in_article = False
            if self._current_slug:
                desc = " ".join("".join(self._desc_buf).split()).strip()
                art_txt = " ".join("".join(self._article_text_buf).split())
                stars_m = re.search(r"([\d,]+)\s+stars\s+this\s+week", art_txt, re.IGNORECASE)
                stars = int(stars_m.group(1).replace(",", "")) if stars_m else 0
                self.repos.append((self._current_slug, desc, stars))

    def handle_data(self, data: str) -> None:
        if self._in_p:
            self._desc_buf.append(data)
        if self._in_article:
            self._article_text_buf.append(data)


def parse_gh_trending(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse GitHub Trending HTML into horizon `RawSignal`s."""
    text = data.decode("utf-8", errors="ignore")
    if not text.strip():
        return []
    parser = _GitHubTrendingHTMLParser()
    parser.feed(text)

    out: list[RawSignal] = []
    seen: set[str] = set()
    for slug, desc, weekly_stars in parser.repos:
        if slug in seen:
            continue
        seen.add(slug)
        url = f"https://github.com/{slug}"
        title = f"{slug}: {desc}" if desc else slug
        metrics: dict[str, Any] = {}
        if weekly_stars > 0:
            metrics["stars_7d"] = weekly_stars
            metrics["star_velocity"] = round(weekly_stars / 7.0, 2)
        out.append(
            RawSignal(
                source_id="gh_trending",
                type="horizon",
                external_id=slug,
                title=title,
                url=url,
                published_at=meta.fetched_at,
                untrusted_excerpt=desc or slug,
                entity_hint=f"gh:{slug}",
                metrics=metrics,
                confidence="confirmed",
                dedupe_key=f"gh_trending|horizon|{slug}",
            )
        )
    return out


def _run_gh_trending(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    out: list[RawSignal] = []
    seen: set[str] = set()
    errors: list[str] = []

    for lang in _profile_languages(ctx):
        url = f"https://github.com/trending/{urllib.parse.quote(lang, safe='')}?since=weekly"
        try:
            resp = ctx.http.fetch(url, ttl_hours=spec.ttl_hours)
            meta = FetchMeta(
                url=resp.url,
                fetched_at=ctx.now_iso,
                status=resp.status,
                headers=resp.headers,
                params={"language": lang},
                source_id=spec.id,
            )
            for sig in parse_gh_trending(resp.body, meta):
                if sig.external_id not in seen:
                    seen.add(sig.external_id)
                    out.append(sig)
        except RadarError as exc:
            errors.append(str(exc))

    if not out and errors:
        raise FetchError("; ".join(errors))
    return out


def parse_gh_topics(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse GitHub `search/repositories?q=topic:...` JSON into horizon `RawSignal`s."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid gh_topics JSON: {exc}") from exc

    items = obj.get("items") if isinstance(obj, Mapping) else obj
    if not isinstance(items, list):
        raise FetchError("gh_topics payload must contain an items list")
    return _parse_repo_items_array(items, meta, source_id="gh_topics", signal_type="horizon")


def _run_gh_topics(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    since_date = (ctx.now - timedelta(days=30)).strftime("%Y-%m-%d")
    topics = spec.params.get("topics") or GH_TOPICS
    out: list[RawSignal] = []
    seen: set[str] = set()
    errors: list[str] = []

    for topic in topics:
        t_clean = str(topic).strip().lower()
        if not _SAFE_SLUG_TOKEN_RE.match(t_clean):
            continue
        endpoint = f"search/repositories?q=topic:{t_clean}+pushed:%3E{since_date}&per_page=10"
        try:
            res = ctx.gh.api(endpoint)
            if res.returncode != 0:
                errors.append(res.stderr or f"gh_topics failed for {t_clean}")
                continue
            meta = FetchMeta(
                url=endpoint,
                fetched_at=ctx.now_iso,
                status=200,
                params={"topic": t_clean},
                source_id=spec.id,
            )
            for sig in parse_gh_topics(res.stdout.encode("utf-8"), meta):
                if sig.external_id not in seen:
                    seen.add(sig.external_id)
                    out.append(sig)
        except RadarError as exc:
            errors.append(str(exc))

    if not out and errors:
        raise FetchError("; ".join(errors))
    return out


def parse_gh_tastemaker_stars(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse `users/{login}/starred` JSON list into horizon `RawSignal`s (context only, A22)."""
    try:
        arr = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid gh_tastemaker_stars JSON: {exc}") from exc

    if isinstance(arr, Mapping) and isinstance(arr.get("items"), list):
        arr = arr["items"]
    if not isinstance(arr, list):
        raise FetchError("gh_tastemaker_stars payload must be a JSON list")

    unwrapped: list[Any] = []
    for item in arr:
        if isinstance(item, Mapping) and isinstance(item.get("repo"), Mapping):
            unwrapped.append(item["repo"])
        else:
            unwrapped.append(item)

    return _parse_repo_items_array(
        unwrapped,
        meta,
        source_id="gh_tastemaker_stars",
        signal_type="horizon",
    )


def _run_gh_tastemaker_stars(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    logins: list[str] = []
    for raw_login in ctx.profile.taste_makers:
        clean = raw_login.strip().lstrip("@")
        if "/" in clean:
            clean = clean.split("/")[0]
        if _SAFE_SLUG_TOKEN_RE.match(clean) and clean not in logins:
            logins.append(clean)
    if not logins:
        logins = ["simonw"]

    out: list[RawSignal] = []
    seen: set[str] = set()
    errors: list[str] = []

    for login in logins[:10]:
        endpoint = f"users/{login}/starred?per_page=10"
        try:
            res = ctx.gh.api(endpoint)
            if res.returncode != 0:
                errors.append(res.stderr or f"gh_tastemaker_stars failed for {login}")
                continue
            meta = FetchMeta(
                url=endpoint,
                fetched_at=ctx.now_iso,
                status=200,
                params={"login": login},
                source_id=spec.id,
            )
            for sig in parse_gh_tastemaker_stars(res.stdout.encode("utf-8"), meta):
                if sig.external_id not in seen:
                    seen.add(sig.external_id)
                    out.append(sig)
        except RadarError as exc:
            errors.append(str(exc))

    if not out and errors:
        raise FetchError("; ".join(errors))
    return out


_DISCUSSIONS_GRAPHQL_QUERY = (
    "query($owner: String!, $name: String!) { "
    "repository(owner: $owner, name: $name) { "
    "discussions(first: 10, orderBy: {field: CREATED_AT, direction: DESC}) { "
    "nodes { id title url bodyText publishedAt category { name slug } } "
    "} } }"
)

_DISCUSSION_LIFECYCLE_RE = re.compile(
    r"(?:unmaintained|looking for (?:new )?maintainers"
    r"|no longer maintained|deprecated in favou?r of"
    r"|\b(?:acquired|sunset|shutting down|end of life)\b)",
    re.IGNORECASE,
)


def parse_gh_discussions_announce(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse GitHub GraphQL `repository.discussions` JSON into feature/lifecycle `RawSignal`s."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid gh_discussions_announce JSON: {exc}") from exc

    if not isinstance(obj, Mapping):
        raise FetchError("gh_discussions_announce payload must be a JSON object")

    repo_slug = _repo_from_meta(meta)
    data_obj = obj.get("data") if isinstance(obj.get("data"), Mapping) else obj
    repo_obj = data_obj.get("repository") if isinstance(data_obj, Mapping) else None
    disc_obj = repo_obj.get("discussions") if isinstance(repo_obj, Mapping) else None
    nodes = disc_obj.get("nodes") if isinstance(disc_obj, Mapping) else None
    if not isinstance(nodes, list):
        return []

    out: list[RawSignal] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        cat = node.get("category")
        if isinstance(cat, Mapping):
            cat_slug = str(cat.get("slug") or cat.get("name") or "").strip().lower()
            if cat_slug and "announce" not in cat_slug and "release" not in cat_slug:
                continue
        disc_id = str(node.get("id") or node.get("number") or "").strip()
        title = str(node.get("title") or disc_id).strip()
        if not disc_id or not title:
            continue
        url = str(node.get("url") or "").strip() or None
        body = str(node.get("bodyText") or node.get("body") or title).strip()
        pub = normalize_timestamp(node.get("publishedAt") or node.get("createdAt"))
        resolved_repo = repo_slug
        if not resolved_repo and url:
            m = re.search(r"github\.com/([^/]+/[^/]+)", url)
            if m:
                resolved_repo = m.group(1).lower()
        entity_hint = meta.entity_id or (f"gh:{resolved_repo}" if resolved_repo else None)
        sig_type = "lifecycle" if _DISCUSSION_LIFECYCLE_RE.search(f"{title}\n{body}") else "feature"
        out.append(
            RawSignal(
                source_id="gh_discussions_announce",
                type=sig_type,
                external_id=disc_id,
                title=title,
                url=url,
                published_at=pub or meta.fetched_at,
                untrusted_excerpt=body,
                entity_hint=entity_hint,
                confidence="confirmed",
                payload={"repo": resolved_repo, "body": sanitize.clean(body, limit=2000)},
                dedupe_key=f"gh_discussions_announce|{sig_type}|{disc_id}",
            )
        )
    return out


def _run_gh_discussions_announce(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    out: list[RawSignal] = []
    for repo_slug, eid in adopt_trial_public_repos(ctx):
        if "/" not in repo_slug:
            continue
        owner, name = repo_slug.split("/", 1)
        if not (_SAFE_SLUG_TOKEN_RE.match(owner) and _SAFE_SLUG_TOKEN_RE.match(name)):
            continue
        vars_json = json.dumps({"owner": owner, "name": name}, separators=(",", ":"))
        try:
            res = ctx.gh.api(
                [
                    "api",
                    "-X",
                    "POST",
                    "graphql",
                    "-f",
                    f"query={_DISCUSSIONS_GRAPHQL_QUERY}",
                    "-f",
                    f"variables={vars_json}",
                ]
            )
        except RadarError:
            continue
        if res.returncode != 0 or not res.stdout.strip():
            continue
        meta = FetchMeta(
            url="graphql",
            fetched_at=ctx.now_iso,
            status=200,
            params={"repo": repo_slug},
            source_id=spec.id,
            entity_id=eid,
        )
        try:
            out.extend(parse_gh_discussions_announce(res.stdout.encode("utf-8"), meta))
        except FetchError:
            continue
    return out


def _parse_commits_list(
    data: bytes,
    meta: FetchMeta,
    *,
    source_id: str,
    default_repo: str | None = None,
) -> list[RawSignal]:
    try:
        arr = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid {source_id} commits JSON: {exc}") from exc

    if not isinstance(arr, list):
        raise FetchError(f"{source_id} commits payload must be a JSON list")

    repo_slug = _repo_from_meta(meta) or default_repo
    out: list[RawSignal] = []
    for item in arr:
        if not isinstance(item, Mapping):
            continue
        sha = str(item.get("sha") or "").strip()
        if not sha:
            continue
        raw_commit = item.get("commit")
        commit_obj: Mapping[str, Any] = raw_commit if isinstance(raw_commit, Mapping) else {}
        msg = str(commit_obj.get("message") or sha).strip()
        first_line = msg.splitlines()[0].strip() if msg else sha[:8]
        raw_author = commit_obj.get("author")
        author_obj: Mapping[str, Any] | None = (
            raw_author if isinstance(raw_author, Mapping) else None
        )
        date_raw = author_obj.get("date") if author_obj is not None else None
        pub = normalize_timestamp(date_raw) or meta.fetched_at
        html_url = str(item.get("html_url") or item.get("url") or "").strip() or None
        resolved_repo = repo_slug
        if not resolved_repo and html_url:
            m = re.search(r"github\.com/([^/]+/[^/]+)", html_url)
            if m:
                resolved_repo = m.group(1).lower()
        hint = infer_github_entity_hint(msg) or (f"gh:{resolved_repo}" if resolved_repo else None)
        title = f"{resolved_repo or source_id}: {first_line}"
        out.append(
            RawSignal(
                source_id=source_id,
                type="horizon",
                external_id=f"{resolved_repo or source_id}:{sha[:12]}",
                title=title,
                url=html_url,
                published_at=pub,
                untrusted_excerpt=msg,
                entity_hint=hint,
                confidence="confirmed",
                payload={"repo": resolved_repo, "sha": sha},
                dedupe_key=f"{source_id}|horizon|{resolved_repo or source_id}|{sha[:12]}",
            )
        )
    return out


def parse_awesome_diffs(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse `repos/{o}/{r}/commits?path=README.md` JSON into horizon `RawSignal`s."""
    return _parse_commits_list(data, meta, source_id="awesome_diffs")


def _run_awesome_diffs(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    since_iso = lookback_since(ctx, timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    repos = spec.params.get("repos") or AWESOME_REPOS
    out: list[RawSignal] = []
    errors: list[str] = []

    for repo_slug in repos:
        endpoint = f"repos/{repo_slug}/commits?path=README.md&since={since_iso}&per_page=5"
        try:
            res = ctx.gh.api(endpoint)
            if res.returncode != 0:
                errors.append(res.stderr or f"awesome_diffs failed for {repo_slug}")
                continue
            meta = FetchMeta(
                url=endpoint,
                fetched_at=ctx.now_iso,
                status=200,
                params={"repo": repo_slug},
                source_id=spec.id,
            )
            out.extend(parse_awesome_diffs(res.stdout.encode("utf-8"), meta))
        except RadarError as exc:
            errors.append(str(exc))

    if not out and errors:
        raise FetchError("; ".join(errors))
    return out


def parse_anthropic_skills(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse `repos/anthropics/skills/commits` JSON into horizon `RawSignal`s."""
    return _parse_commits_list(
        data,
        meta,
        source_id="anthropic_skills",
        default_repo="anthropics/skills",
    )


def _run_anthropic_skills(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    since_iso = lookback_since(ctx, timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    endpoint = f"repos/anthropics/skills/commits?since={since_iso}&per_page=10"
    res = ctx.gh.api(endpoint)
    if res.returncode != 0:
        raise FetchError(f"gh api anthropic_skills failed: {res.stderr}")
    meta = FetchMeta(
        url=endpoint,
        fetched_at=ctx.now_iso,
        status=200,
        params={"repo": "anthropics/skills"},
        source_id=spec.id,
    )
    return parse_anthropic_skills(res.stdout.encode("utf-8"), meta)


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

GH_SEARCH_RISING_SPEC = SourceSpec(
    id="gh_search_rising",
    req=("SRC-CODE-01",),
    archetype="gh_search",
    url="search/repositories?q=stars:%3E100&sort=stars&per_page=10",
    cadence="weekly",
    hosts=("api.github.com",),
    signal_types=("horizon", "contender"),
    ttl_hours=24 * 7,
    parser=parse_gh_search_rising,
    runner=_run_gh_search_rising,
)

GH_TRENDING_SPEC = SourceSpec(
    id="gh_trending",
    req=("SRC-CODE-02",),
    archetype="html",
    url="https://github.com/trending/python?since=weekly",
    cadence="weekly",
    hosts=("github.com",),
    signal_types=("horizon",),
    ttl_hours=24 * 7,
    parser=parse_gh_trending,
    runner=_run_gh_trending,
)

GH_TOPICS_SPEC = SourceSpec(
    id="gh_topics",
    req=("SRC-CODE-03",),
    archetype="gh_search",
    url="search/repositories?q=topic:mcp&per_page=10",
    cadence="weekly",
    hosts=("api.github.com",),
    signal_types=("horizon",),
    ttl_hours=24 * 7,
    params={"topics": GH_TOPICS},
    parser=parse_gh_topics,
    runner=_run_gh_topics,
)

GH_TASTEMAKER_STARS_SPEC = SourceSpec(
    id="gh_tastemaker_stars",
    req=("SRC-CODE-04",),
    archetype="gh_api",
    url="users/simonw/starred?per_page=10",
    cadence="weekly",
    hosts=("api.github.com",),
    signal_types=("horizon",),
    ttl_hours=24 * 7,
    parser=parse_gh_tastemaker_stars,
    runner=_run_gh_tastemaker_stars,
)

GH_DISCUSSIONS_ANNOUNCE_SPEC = SourceSpec(
    id="gh_discussions_announce",
    req=("SRC-CODE-13",),
    archetype="gh_api",
    url="graphql",
    cadence="weekly",
    hosts=("api.github.com",),
    signal_types=("feature", "lifecycle"),
    ttl_hours=24 * 7,
    parser=parse_gh_discussions_announce,
    runner=_run_gh_discussions_announce,
)

AWESOME_DIFFS_SPEC = SourceSpec(
    id="awesome_diffs",
    req=("SRC-CUR-11",),
    archetype="gh_api",
    url="repos/hesreallyhim/awesome-claude-code/commits?path=README.md&per_page=5",
    cadence="weekly",
    hosts=("api.github.com",),
    signal_types=("horizon",),
    ttl_hours=24 * 7,
    params={"repos": AWESOME_REPOS},
    parser=parse_awesome_diffs,
    runner=_run_awesome_diffs,
)

ANTHROPIC_SKILLS_SPEC = SourceSpec(
    id="anthropic_skills",
    req=("SRC-AI-05",),
    archetype="gh_api",
    url="repos/anthropics/skills/commits?per_page=10",
    cadence="weekly",
    hosts=("api.github.com",),
    signal_types=("horizon",),
    ttl_hours=24 * 7,
    parser=parse_anthropic_skills,
    runner=_run_anthropic_skills,
)

GITHUB_SPECS: tuple[SourceSpec, ...] = (
    GH_RELEASES_SPEC,
    GH_CHANGELOG_FILE_SPEC,
    GH_REPO_META_SPEC,
    GH_SEARCH_RISING_SPEC,
    GH_TRENDING_SPEC,
    GH_TOPICS_SPEC,
    GH_TASTEMAKER_STARS_SPEC,
    GH_DISCUSSIONS_ANNOUNCE_SPEC,
    AWESOME_DIFFS_SPEC,
    ANTHROPIC_SKILLS_SPEC,
)
