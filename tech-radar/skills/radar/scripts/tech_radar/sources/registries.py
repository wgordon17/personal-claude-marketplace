"""Registry and package-metric sources: npm, PyPI, endoflife, crates, brew, deps.dev."""

from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Mapping
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from .. import db
from ..errors import FetchError, RadarError
from ..inventory import classify
from .base import (
    FetchMeta,
    RawSignal,
    SourceSpec,
    infer_github_entity_hint,
    normalize_timestamp,
)
from .security_feeds import is_mal_advisory

if TYPE_CHECKING:
    from ..context import Ctx

_GITHUB_SLUG_FROM_URL_RE = re.compile(
    r"github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)
_FORMULA_PATH_RE = re.compile(r"(?:^|/)Formula/(?:[a-z0-9]+/)?([A-Za-z0-9+@._-]+)\.rb$")
_COMMIT_FORMULA_RE = re.compile(r"\b([a-z0-9][a-z0-9@._+-]{1,50}):\s+(?:new\s+formula|init\b|\d)")


def _extract_repo_slug(raw: Any) -> str | None:
    if isinstance(raw, Mapping):
        for k in ("url", "Repository", "Source", "Code", "Homepage", "homepage"):
            if raw.get(k):
                slug = _extract_repo_slug(raw[k])
                if slug:
                    return slug
        for v in raw.values():
            slug = _extract_repo_slug(v)
            if slug:
                return slug
        return None
    text = str(raw or "").strip()
    if not text:
        return None
    m = _GITHUB_SLUG_FROM_URL_RE.search(text)
    if not m:
        return None
    slug = m.group(1).rstrip("/.")
    if slug.endswith(".git"):
        slug = slug[:-4]
    parts = slug.split("/")
    if len(parts) == 2 and parts[0] and parts[1]:
        return f"{parts[0].lower()}/{parts[1].lower()}"
    return None


def parse_npm_registry_meta(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse npm registry package metadata JSON into lifecycle/integrity `RawSignal`s."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid npm registry JSON: {exc}") from exc

    if not isinstance(obj, Mapping):
        raise FetchError("npm registry payload must be a JSON object")

    pkg_name = str(obj.get("name") or obj.get("_id") or "").strip()
    if not pkg_name and meta.entity_id and meta.entity_id.startswith("pkg:npm/"):
        pkg_name = meta.entity_id[len("pkg:npm/") :]
    if not pkg_name:
        return []

    entity_id = f"pkg:npm/{pkg_name}"
    dist_tags = obj.get("dist-tags")
    latest_ver = str(dist_tags.get("latest") or "") if isinstance(dist_tags, Mapping) else ""
    versions_tbl = obj.get("versions") if isinstance(obj.get("versions"), Mapping) else {}
    time_tbl = obj.get("time") if isinstance(obj.get("time"), Mapping) else {}

    installed_ver = str(meta.params.get("installed_version") or "").strip()
    deprecated_versions: dict[str, str] = {}
    install_script_versions: dict[str, list[str]] = {}
    attestations_by_version: dict[str, bool] = {}
    repo_by_version: dict[str, str] = {}

    if isinstance(versions_tbl, Mapping):
        for ver_key, ver_meta in versions_tbl.items():
            if not isinstance(ver_meta, Mapping):
                continue
            v_str = str(ver_key)
            dep_msg = ver_meta.get("deprecated")
            if dep_msg:
                deprecated_versions[v_str] = str(dep_msg)

            scripts = ver_meta.get("scripts")
            if isinstance(scripts, Mapping):
                hook_keys = [k for k in ("preinstall", "install", "postinstall") if scripts.get(k)]
                if hook_keys:
                    install_script_versions[v_str] = hook_keys

            dist = ver_meta.get("dist")
            has_att = False
            if isinstance(dist, Mapping):
                att = dist.get("attestations") or dist.get("signatures")
                has_att = bool(att)
            attestations_by_version[v_str] = has_att

            v_repo = _extract_repo_slug(ver_meta.get("repository"))
            if v_repo:
                repo_by_version[v_str] = v_repo

    top_maintainers: list[str] = []
    for m_item in obj.get("maintainers") or []:
        if isinstance(m_item, Mapping) and m_item.get("name"):
            top_maintainers.append(str(m_item["name"]))
        elif isinstance(m_item, str):
            top_maintainers.append(m_item)

    top_repo = _extract_repo_slug(obj.get("repository"))
    latest_deprecated = deprecated_versions.get(latest_ver) if latest_ver else None
    installed_deprecated = deprecated_versions.get(installed_ver) if installed_ver else None
    top_deprecated = str(obj.get("deprecated") or "") or latest_deprecated or installed_deprecated

    pub_at = (
        normalize_timestamp(
            (time_tbl.get(latest_ver) if isinstance(time_tbl, Mapping) and latest_ver else None)
            or (time_tbl.get("modified") if isinstance(time_tbl, Mapping) else None)
        )
        or meta.fetched_at
    )

    payload: dict[str, Any] = {
        "name": pkg_name,
        "latest_version": latest_ver,
        "installed_version": installed_ver or None,
        "deprecated": top_deprecated,
        "deprecated_versions": deprecated_versions,
        "maintainers": sorted(set(top_maintainers)),
        "previous_maintainers": obj.get("previous_maintainers"),
        "install_script_versions": install_script_versions,
        "attestations_by_version": attestations_by_version,
        "repository": top_repo,
        "repo_by_version": repo_by_version,
    }

    title = (
        f"npm {pkg_name} deprecated: {top_deprecated}"
        if top_deprecated
        else f"npm metadata for {pkg_name} ({latest_ver or 'latest'})"
    )
    excerpt = str(obj.get("description") or top_deprecated or title)
    return [
        RawSignal(
            source_id="npm_registry_meta",
            type="lifecycle",
            external_id=f"npm:{pkg_name}:{latest_ver or 'meta'}",
            title=title,
            url=f"https://www.npmjs.com/package/{pkg_name}",
            published_at=pub_at,
            untrusted_excerpt=excerpt,
            entity_hint=entity_id,
            confidence="confirmed",
            payload=payload,
            dedupe_key=f"npm_registry_meta|lifecycle|{entity_id}|{latest_ver or 'meta'}",
        )
    ]


def _run_npm_registry_meta(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    rows = ctx.db.execute(
        "SELECT DISTINCT e.entity_id, e.name, u.version "
        "FROM entities e JOIN usages u ON u.entity_id = e.entity_id "
        "WHERE e.entity_id LIKE 'pkg:npm/%' "
        "AND e.visibility = 'public' "
        "AND (u.direct = 1 OR u.context = 'interest:watchlist')"
    ).fetchall()
    out: list[RawSignal] = []
    for row in rows:
        eid = str(row["entity_id"])
        name = str(row["name"] or eid[len("pkg:npm/") :]).strip()
        ver = str(row["version"] or "").strip()
        if not name:
            continue
        url = f"https://registry.npmjs.org/{name}"
        try:
            resp = ctx.http.fetch(url, ttl_hours=spec.ttl_hours)
        except RadarError:
            continue
        meta = FetchMeta(
            url=resp.url,
            fetched_at=ctx.now_iso,
            status=resp.status,
            headers=resp.headers,
            params={"installed_version": ver},
            source_id=spec.id,
            entity_id=eid,
        )
        out.extend(parse_npm_registry_meta(resp.body, meta))
    return out


def parse_pypi_meta(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse PyPI PEP 792 simple JSON or `/pypi/<name>/json` into `RawSignal`s."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid PyPI metadata JSON: {exc}") from exc

    if not isinstance(obj, Mapping):
        raise FetchError("PyPI metadata payload must be a JSON object")

    info = obj.get("info") if isinstance(obj.get("info"), Mapping) else {}
    pkg_name = (
        str(obj.get("name") or (info.get("name") if isinstance(info, Mapping) else "") or "")
        .strip()
        .lower()
    )
    if not pkg_name and meta.entity_id and meta.entity_id.startswith("pkg:pypi/"):
        pkg_name = meta.entity_id[len("pkg:pypi/") :].lower()
    if not pkg_name:
        return []

    entity_id = f"pkg:pypi/{pkg_name}"
    installed_ver = str(meta.params.get("installed_version") or "").strip()

    proj_status_obj = obj.get("project-status")
    project_status = "active"
    if isinstance(proj_status_obj, Mapping) and proj_status_obj.get("status"):
        project_status = str(proj_status_obj["status"]).strip().lower()
    elif isinstance(obj.get("project_status"), str):
        project_status = str(obj["project_status"]).strip().lower()

    yanked_versions: dict[str, str] = {}
    provenance_by_version: dict[str, bool] = {}
    wheel_versions: set[str] = set()
    sdist_versions: set[str] = set()
    pth_or_sitecustomize_versions: list[str] = []

    files_list = obj.get("files")
    if isinstance(files_list, list):
        for f_item in files_list:
            if not isinstance(f_item, Mapping):
                continue
            fname = str(f_item.get("filename") or "")
            ver_m = re.search(r"-(\d+\.\d+[A-Za-z0-9._-]*?)(?:-py|-cp|\.tar\.gz|\.whl)", fname)
            f_ver = str(f_item.get("version") or (ver_m.group(1) if ver_m else "")).strip()
            yanked_val = f_item.get("yanked")
            if yanked_val and f_ver:
                yanked_versions[f_ver] = (
                    str(yanked_val) if not isinstance(yanked_val, bool) else "yanked"
                )
            if f_ver:
                has_prov = bool(f_item.get("provenance"))
                provenance_by_version[f_ver] = provenance_by_version.get(f_ver, False) or has_prov
                if fname.endswith(".whl"):
                    wheel_versions.add(f_ver)
                elif fname.endswith((".tar.gz", ".zip")):
                    sdist_versions.add(f_ver)
            if (
                (f_item.get("contains_pth") or f_item.get("contains_sitecustomize"))
                and f_ver
                and f_ver not in pth_or_sitecustomize_versions
            ):
                pth_or_sitecustomize_versions.append(f_ver)

    releases_tbl = obj.get("releases")
    if isinstance(releases_tbl, Mapping):
        for r_ver, r_files in releases_tbl.items():
            if not isinstance(r_files, list):
                continue
            v_str = str(r_ver)
            for rf in r_files:
                if not isinstance(rf, Mapping):
                    continue
                fname = str(rf.get("filename") or "")
                packagetype = str(rf.get("packagetype") or "")
                if rf.get("yanked"):
                    yanked_versions[v_str] = str(rf.get("yanked_reason") or "yanked")
                has_prov = bool(rf.get("provenance") or rf.get("attestations"))
                provenance_by_version[v_str] = provenance_by_version.get(v_str, False) or has_prov
                if packagetype == "bdist_wheel" or fname.endswith(".whl"):
                    wheel_versions.add(v_str)
                elif packagetype == "sdist" or fname.endswith((".tar.gz", ".zip")):
                    sdist_versions.add(v_str)
                if (
                    rf.get("contains_pth") or rf.get("contains_sitecustomize")
                ) and v_str not in pth_or_sitecustomize_versions:
                    pth_or_sitecustomize_versions.append(v_str)

    info_ver = info.get("version") if isinstance(info, Mapping) else None
    latest_ver = str(info_ver or obj.get("latest_version") or "").strip()
    if not latest_ver and isinstance(obj.get("versions"), list) and obj["versions"]:
        latest_ver = str(obj["versions"][-1])

    project_urls = info.get("project_urls") if isinstance(info, Mapping) else None
    repo_slug = _extract_repo_slug(project_urls) or _extract_repo_slug(
        info.get("home_page") if isinstance(info, Mapping) else None
    )

    payload: dict[str, Any] = {
        "name": pkg_name,
        "project_status": project_status,
        "latest_version": latest_ver or None,
        "installed_version": installed_ver or None,
        "yanked_versions": yanked_versions,
        "provenance_by_version": provenance_by_version,
        "wheel_versions": sorted(wheel_versions),
        "sdist_only_versions": sorted(sdist_versions - wheel_versions),
        "pth_or_sitecustomize_versions": pth_or_sitecustomize_versions,
        "repository": repo_slug,
        "previous_repository": obj.get("previous_repository"),
        "maintainers": obj.get("maintainers")
        or ([str(info["author"])] if isinstance(info, Mapping) and info.get("author") else []),
        "previous_maintainers": obj.get("previous_maintainers"),
    }

    title = f"PyPI metadata for {pkg_name} (status: {project_status})"
    summary = str(
        (info.get("summary") if isinstance(info, Mapping) else "")
        or f"PyPI {pkg_name} status={project_status}"
    )
    return [
        RawSignal(
            source_id="pypi_meta",
            type="lifecycle",
            external_id=f"pypi:{pkg_name}:{latest_ver or project_status}",
            title=title,
            url=f"https://pypi.org/project/{pkg_name}/",
            published_at=meta.fetched_at,
            untrusted_excerpt=summary,
            entity_hint=entity_id,
            confidence="confirmed",
            payload=payload,
            dedupe_key=f"pypi_meta|lifecycle|{entity_id}|{latest_ver or project_status}",
        )
    ]


def _run_pypi_meta(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    rows = ctx.db.execute(
        "SELECT DISTINCT e.entity_id, e.name, u.version "
        "FROM entities e JOIN usages u ON u.entity_id = e.entity_id "
        "WHERE e.entity_id LIKE 'pkg:pypi/%' "
        "AND e.visibility = 'public' "
        "AND (u.direct = 1 OR u.context = 'interest:watchlist')"
    ).fetchall()
    out: list[RawSignal] = []
    for row in rows:
        eid = str(row["entity_id"])
        name = str(row["name"] or eid[len("pkg:pypi/") :]).strip().lower()
        ver = str(row["version"] or "").strip()
        if not name:
            continue

        combined_obj: dict[str, Any] = {"name": name}
        got_any = False

        simple_url = f"https://pypi.org/simple/{name}/"
        try:
            resp_s = ctx.http.fetch(
                simple_url,
                headers={"Accept": "application/vnd.pypi.simple.v1+json"},
                ttl_hours=spec.ttl_hours,
            )
            s_data = json.loads(resp_s.body.decode("utf-8"))
            if isinstance(s_data, Mapping):
                combined_obj.update(s_data)
                got_any = True
        except (RadarError, UnicodeDecodeError, json.JSONDecodeError):
            pass

        json_url = f"https://pypi.org/pypi/{name}/json"
        try:
            resp_j = ctx.http.fetch(json_url, ttl_hours=spec.ttl_hours)
            j_data = json.loads(resp_j.body.decode("utf-8"))
            if isinstance(j_data, Mapping):
                combined_obj.update(j_data)
                got_any = True
        except (RadarError, UnicodeDecodeError, json.JSONDecodeError):
            pass

        if not got_any:
            continue

        meta = FetchMeta(
            url=json_url,
            fetched_at=ctx.now_iso,
            status=200,
            params={"installed_version": ver},
            source_id=spec.id,
            entity_id=eid,
        )
        out.extend(
            parse_pypi_meta(
                json.dumps(combined_obj).encode("utf-8"),
                meta,
            )
        )
    return out


def parse_endoflife(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse `https://endoflife.date/api/v1/products/{product}` JSON into `RawSignal`s."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid endoflife JSON: {exc}") from exc

    product = str(meta.params.get("product") or "").strip().lower()
    releases: list[Mapping[str, Any]] = []

    if isinstance(obj, Mapping):
        res_tbl = obj.get("result")
        if isinstance(res_tbl, Mapping):
            product = str(res_tbl.get("name") or product).strip().lower()
            raw_rels = res_tbl.get("releases")
            if isinstance(raw_rels, list):
                releases = [r for r in raw_rels if isinstance(r, Mapping)]
        elif isinstance(obj.get("releases"), list):
            releases = [r for r in obj["releases"] if isinstance(r, Mapping)]
    elif isinstance(obj, list):
        releases = [r for r in obj if isinstance(r, Mapping)]

    if not product:
        product = meta.url.rstrip("/").split("/")[-1].lower()

    entity_hint = meta.entity_id or f"runtime:{product}"
    cycles: list[dict[str, Any]] = []
    for rel in releases:
        cycle_name = str(rel.get("name") or rel.get("cycle") or "").strip()
        if not cycle_name:
            continue
        eol_val = rel.get("eolFrom") if "eolFrom" in rel else rel.get("eol")
        is_eol = bool(rel.get("isEol") is True or eol_val is True)
        cycles.append(
            {
                "cycle": cycle_name,
                "eol": eol_val if isinstance(eol_val, str) else None,
                "is_maintained": rel.get("isMaintained", not is_eol),
                "is_eol": is_eol,
                "latest": (
                    rel.get("latest", {}).get("name")
                    if isinstance(rel.get("latest"), Mapping)
                    else rel.get("latest")
                ),
            }
        )

    if not cycles:
        return []

    return [
        RawSignal(
            source_id="endoflife",
            type="lifecycle",
            external_id=f"eol:{product}",
            title=f"End-of-life schedule for {product}",
            url=f"https://endoflife.date/{product}",
            published_at=meta.fetched_at,
            untrusted_excerpt=f"Lifecycle cycles for {product}: {len(cycles)} tracked releases",
            entity_hint=entity_hint,
            confidence="confirmed",
            payload={
                "product": product,
                "cycles": cycles,
            },
            dedupe_key=f"endoflife|lifecycle|{entity_hint}",
        )
    ]


_PRODUCT_MAP: dict[str, str] = {
    "python": "python",
    "node": "nodejs",
    "nodejs": "nodejs",
    "go": "go",
    "golang": "go",
    "rust": "rust",
    "macos": "macos",
    "postgresql": "postgresql",
    "redis": "redis",
}


def _run_endoflife(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    rows = ctx.db.execute(
        "SELECT DISTINCT e.entity_id, e.name, u.version "
        "FROM entities e JOIN usages u ON u.entity_id = e.entity_id "
        "WHERE e.kind = 'runtime' OR e.entity_id LIKE 'pkg:mise/%' OR e.entity_id LIKE 'runtime:%'"
    ).fetchall()
    seen_products: dict[str, str] = {}
    for row in rows:
        eid = str(row["entity_id"])
        raw_name = str(row["name"]).strip().lower()
        prod = _PRODUCT_MAP.get(raw_name)
        if prod and prod not in seen_products:
            seen_products[prod] = eid

    if not seen_products:
        seen_products["python"] = "runtime:python"

    out: list[RawSignal] = []
    for prod, eid in sorted(seen_products.items()):
        url = f"https://endoflife.date/api/v1/products/{prod}"
        try:
            resp = ctx.http.fetch(url, ttl_hours=spec.ttl_hours)
        except RadarError:
            continue
        meta = FetchMeta(
            url=resp.url,
            fetched_at=ctx.now_iso,
            status=resp.status,
            headers=resp.headers,
            params={"product": prod},
            source_id=spec.id,
            entity_id=eid,
        )
        out.extend(parse_endoflife(resp.body, meta))
    return out


def parse_ossinsight_trending(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse OSSInsight trending repos JSON (source is disabled/BROKEN in production)."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid ossinsight_trending JSON: {exc}") from exc

    rows: list[Any] = []
    if isinstance(obj, Mapping):
        data_obj = obj.get("data")
        if isinstance(data_obj, Mapping) and isinstance(data_obj.get("rows"), list):
            rows = data_obj["rows"]
        elif isinstance(data_obj, list):
            rows = data_obj
    elif isinstance(obj, list):
        rows = obj

    out: list[RawSignal] = []
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        repo_name = str(item.get("repo_name") or item.get("full_name") or "").strip().lower()
        if not repo_name or "/" not in repo_name:
            continue
        desc = str(item.get("description") or repo_name).strip()
        stars = int(item.get("stars") or item.get("stargazers_count") or 0)
        out.append(
            RawSignal(
                source_id="ossinsight_trending",
                type="horizon",
                external_id=repo_name,
                title=f"{repo_name}: {desc}" if desc != repo_name else repo_name,
                url=f"https://github.com/{repo_name}",
                published_at=meta.fetched_at,
                untrusted_excerpt=desc,
                entity_hint=f"gh:{repo_name}",
                metrics={"stars": stars},
                confidence="confirmed",
                dedupe_key=f"ossinsight_trending|horizon|{repo_name}",
            )
        )
    return out


def parse_ecosystems_pkg(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse ecosyste.ms package JSON into a metric `RawSignal`."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid ecosystems_pkg JSON: {exc}") from exc

    if not isinstance(obj, Mapping):
        raise FetchError("ecosystems_pkg payload must be a JSON object")

    name = str(obj.get("name") or "").strip()
    if not name:
        return []
    eco = str(obj.get("ecosystem") or "pypi").strip().lower()
    repo_slug = _extract_repo_slug(obj.get("repository_url") or obj.get("homepage"))
    downloads = int(obj.get("downloads") or 0)
    dependent_repos = int(obj.get("dependent_repos_count") or 0)
    first_rel = normalize_timestamp(obj.get("first_release_published_at"))
    latest_rel = normalize_timestamp(obj.get("latest_release_published_at"))
    latest_ver = str(obj.get("latest_release_number") or "")
    entity_hint = meta.entity_id or f"pkg:{eco}/{name}"

    return [
        RawSignal(
            source_id="ecosystems_pkg",
            type="horizon",
            external_id=f"{eco}:{name}",
            title=f"{name} ({latest_ver or 'latest'})",
            url=str(obj.get("homepage") or obj.get("repository_url") or "").strip() or None,
            published_at=latest_rel or meta.fetched_at,
            untrusted_excerpt=str(obj.get("description") or name),
            entity_hint=entity_hint,
            metrics={
                "downloads": downloads,
                "dependent_repos_count": dependent_repos,
            },
            payload={
                "name": name,
                "ecosystem": eco,
                "repository_slug": repo_slug,
                "first_release_published_at": first_rel,
                "latest_release_published_at": latest_rel,
                "latest_release_number": latest_ver,
                "normalized_licenses": obj.get("normalized_licenses") or [],
            },
        )
    ]


def parse_depsdev(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse deps.dev v3 package or project JSON into a metric `RawSignal`."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid depsdev JSON: {exc}") from exc

    if not isinstance(obj, Mapping):
        raise FetchError("depsdev payload must be a JSON object")

    pkg_key = obj.get("packageKey")
    proj_key = obj.get("projectKey")
    if isinstance(pkg_key, Mapping):
        sys_name = str(pkg_key.get("system") or "pypi").strip().lower()
        name = str(pkg_key.get("name") or "").strip()
        raw_versions = obj.get("versions")
        versions: list[Any] = raw_versions if isinstance(raw_versions, list) else []
        pub_dates = [
            normalize_timestamp(v.get("publishedAt"))
            for v in versions
            if isinstance(v, Mapping) and v.get("publishedAt")
        ]
        clean_dates = sorted(d for d in pub_dates if d)
        first_pub = clean_dates[0] if clean_dates else None
        latest_pub = clean_dates[-1] if clean_dates else None
        default_ver = next(
            (
                str(v.get("versionKey", {}).get("version") or "")
                for v in versions
                if isinstance(v, Mapping) and v.get("isDefault") is True
            ),
            "",
        )
        return [
            RawSignal(
                source_id="depsdev",
                type="horizon",
                external_id=f"{sys_name}:{name}",
                title=f"deps.dev {sys_name}/{name}",
                url=f"https://deps.dev/{sys_name}/{urllib.parse.quote(name, safe='')}",
                published_at=latest_pub or meta.fetched_at,
                untrusted_excerpt=f"deps.dev package {sys_name}/{name} ({len(versions)} versions)",
                entity_hint=meta.entity_id or f"pkg:{sys_name}/{name}",
                metrics={"versions_count": len(versions)},
                payload={
                    "system": sys_name,
                    "name": name,
                    "default_version": default_ver,
                    "first_published_at": first_pub,
                    "latest_published_at": latest_pub,
                },
            )
        ]

    if isinstance(proj_key, Mapping):
        proj_id = str(proj_key.get("id") or "").strip()
        slug = (
            proj_id[len("github.com/") :].lower() if proj_id.startswith("github.com/") else proj_id
        )
        stars = int(obj.get("starsCount") or 0)
        forks = int(obj.get("forksCount") or 0)
        open_issues = int(obj.get("openIssuesCount") or 0)
        scorecard = obj.get("scorecard")
        raw_overall = scorecard.get("overallScore") if isinstance(scorecard, Mapping) else None
        overall_score = float(raw_overall) if isinstance(raw_overall, int | float | str) else None
        metrics_map: dict[str, Any] = {
            "stars": stars,
            "forks": forks,
            "open_issues": open_issues,
        }
        if overall_score is not None:
            metrics_map["scorecard"] = overall_score
        return [
            RawSignal(
                source_id="depsdev",
                type="horizon",
                external_id=proj_id or "project",
                title=f"deps.dev project {slug}",
                url=f"https://{proj_id}" if proj_id else None,
                published_at=meta.fetched_at,
                untrusted_excerpt=str(obj.get("description") or slug),
                entity_hint=meta.entity_id or (f"gh:{slug}" if "/" in slug else None),
                metrics=metrics_map,
                payload={
                    "project_id": proj_id,
                    "license": obj.get("license"),
                    "scorecard": overall_score,
                },
            )
        ]

    return []


def parse_pypistats(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse pypistats `/api/packages/{name}/recent` JSON into a metric `RawSignal`."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid pypistats JSON: {exc}") from exc

    if not isinstance(obj, Mapping):
        raise FetchError("pypistats payload must be a JSON object")

    pkg = str(obj.get("package") or "").strip().lower()
    raw_data = obj.get("data")
    data_obj: Mapping[str, Any] = raw_data if isinstance(raw_data, Mapping) else {}
    last_day = int(data_obj.get("last_day") or 0)
    last_week = int(data_obj.get("last_week") or 0)
    last_month = int(data_obj.get("last_month") or 0)
    if not pkg:
        return []
    return [
        RawSignal(
            source_id="pypistats",
            type="horizon",
            external_id=f"pypistats:{pkg}",
            title=f"PyPI downloads for {pkg}",
            url=f"https://pypistats.org/packages/{pkg}",
            published_at=meta.fetched_at,
            untrusted_excerpt=f"{pkg} last_month={last_month} last_week={last_week}",
            entity_hint=meta.entity_id or f"pkg:pypi/{pkg}",
            metrics={
                "downloads_30d": last_month,
                "downloads_7d": last_week,
                "downloads_1d": last_day,
            },
        )
    ]


def parse_npm_downloads(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse npm `/downloads/point/last-month/{name}` JSON into a metric `RawSignal`."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid npm_downloads JSON: {exc}") from exc

    if not isinstance(obj, Mapping):
        raise FetchError("npm_downloads payload must be a JSON object")

    pkg = str(obj.get("package") or "").strip()
    downloads = int(obj.get("downloads") or 0)
    if not pkg:
        return []
    return [
        RawSignal(
            source_id="npm_downloads",
            type="horizon",
            external_id=f"npm_downloads:{pkg}",
            title=f"npm downloads for {pkg}",
            url=f"https://www.npmjs.com/package/{pkg}",
            published_at=meta.fetched_at,
            untrusted_excerpt=f"{pkg} downloads_30d={downloads}",
            entity_hint=meta.entity_id or f"pkg:npm/{pkg}",
            metrics={"downloads_30d": downloads},
        )
    ]


def parse_crates_new(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse `https://crates.io/api/v1/summary` JSON into horizon `RawSignal`s."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid crates_new JSON: {exc}") from exc

    if not isinstance(obj, Mapping):
        raise FetchError("crates_new payload must be a JSON object")

    out: list[RawSignal] = []
    seen: set[str] = set()
    for list_key in ("new_crates", "most_recently_downloaded"):
        items = obj.get(list_key)
        if not isinstance(items, list):
            continue
        for crate in items:
            if not isinstance(crate, Mapping):
                continue
            name = str(crate.get("name") or crate.get("id") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            desc = str(crate.get("description") or f"Rust crate {name}").strip()
            ver = str(crate.get("max_version") or crate.get("newest_version") or "").strip()
            downloads = int(crate.get("downloads") or 0)
            recent = int(crate.get("recent_downloads") or 0)
            pub = normalize_timestamp(crate.get("created_at") or crate.get("updated_at"))
            repo_url = str(crate.get("repository") or crate.get("homepage") or "").strip()
            url = repo_url or f"https://crates.io/crates/{name}"
            hint = infer_github_entity_hint(repo_url) or f"pkg:cargo/{name}"
            title = f"{name} {ver}: {desc}" if ver else f"{name}: {desc}"
            out.append(
                RawSignal(
                    source_id="crates_new",
                    type="horizon",
                    external_id=name,
                    title=title,
                    url=url,
                    published_at=pub or meta.fetched_at,
                    untrusted_excerpt=desc,
                    entity_hint=hint,
                    metrics={"downloads": downloads, "recent_downloads": recent},
                    confidence="confirmed",
                    dedupe_key=f"crates_new|horizon|{name}",
                )
            )
    return out


def parse_brew_new_formulae(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse Homebrew new formulae from `git log --name-only` lines or GitHub commits JSON."""
    text = data.decode("utf-8", errors="ignore").strip()
    if not text:
        return []

    out: list[RawSignal] = []
    seen: set[str] = set()

    if text.startswith(("[", "{")):
        try:
            arr = json.loads(text)
        except json.JSONDecodeError as exc:
            raise FetchError(f"invalid brew_new_formulae JSON: {exc}") from exc
        if isinstance(arr, Mapping) and isinstance(arr.get("items"), list):
            arr = arr["items"]
        if not isinstance(arr, list):
            return []
        for item in arr:
            if not isinstance(item, Mapping):
                continue
            sha = str(item.get("sha") or "").strip()
            raw_commit = item.get("commit")
            commit_obj: Mapping[str, Any] = raw_commit if isinstance(raw_commit, Mapping) else {}
            msg = str(commit_obj.get("message") or "").strip()
            first_line = msg.splitlines()[0].strip() if msg else ""
            raw_author = commit_obj.get("author")
            author_obj: Mapping[str, Any] | None = (
                raw_author if isinstance(raw_author, Mapping) else None
            )
            pub = normalize_timestamp(author_obj.get("date") if author_obj is not None else None)
            html_url = str(item.get("html_url") or "").strip() or None

            formula_names: list[str] = []
            files_list = item.get("files")
            if isinstance(files_list, list):
                for f_obj in files_list:
                    if isinstance(f_obj, Mapping):
                        fm = _FORMULA_PATH_RE.search(str(f_obj.get("filename") or ""))
                        if fm:
                            formula_names.append(fm.group(1))
            if not formula_names and first_line:
                cm = _COMMIT_FORMULA_RE.match(first_line)
                if cm:
                    formula_names.append(cm.group(1))

            if formula_names:
                for fname in formula_names:
                    if fname in seen:
                        continue
                    seen.add(fname)
                    out.append(
                        RawSignal(
                            source_id="brew_new_formulae",
                            type="horizon",
                            external_id=fname,
                            title=f"Homebrew formula: {fname} ({first_line or sha[:8]})",
                            url=html_url or f"https://formulae.brew.sh/formula/{fname}",
                            published_at=pub or meta.fetched_at,
                            untrusted_excerpt=msg or fname,
                            entity_hint=f"brew:formula/{fname}",
                            confidence="confirmed",
                            dedupe_key=f"brew_new_formulae|horizon|{fname}",
                        )
                    )
            elif sha:
                ext_id = sha[:12]
                if ext_id not in seen:
                    seen.add(ext_id)
                    out.append(
                        RawSignal(
                            source_id="brew_new_formulae",
                            type="horizon",
                            external_id=ext_id,
                            title=f"homebrew-core: {first_line or ext_id}",
                            url=html_url,
                            published_at=pub or meta.fetched_at,
                            untrusted_excerpt=msg or first_line or ext_id,
                            confidence="confirmed",
                            dedupe_key=f"brew_new_formulae|horizon|{ext_id}",
                        )
                    )
        return out

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _FORMULA_PATH_RE.search(line)
        fname = m.group(1) if m else (line[:-3] if line.endswith(".rb") else "")
        if not fname or fname in seen:
            continue
        seen.add(fname)
        out.append(
            RawSignal(
                source_id="brew_new_formulae",
                type="horizon",
                external_id=fname,
                title=f"New Homebrew formula: {fname}",
                url=f"https://formulae.brew.sh/formula/{fname}",
                published_at=meta.fetched_at,
                untrusted_excerpt=f"Added Homebrew formula {fname}",
                entity_hint=f"brew:formula/{fname}",
                confidence="confirmed",
                dedupe_key=f"brew_new_formulae|horizon|{fname}",
            )
        )
    return out


def _run_brew_new_formulae(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    since_iso = (ctx.now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    endpoint = f"repos/Homebrew/homebrew-core/commits?since={since_iso}&per_page=10"
    res = ctx.gh.api(endpoint)
    if res.returncode != 0:
        raise FetchError(f"gh api brew_new_formulae failed: {res.stderr}")
    meta = FetchMeta(
        url=endpoint,
        fetched_at=ctx.now_iso,
        status=200,
        source_id=spec.id,
    )
    return parse_brew_new_formulae(res.stdout.encode("utf-8"), meta)


def parse_topnames(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse ecosyste.ms top-downloaded packages list into a cache `RawSignal`."""
    try:
        arr = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid topnames JSON: {exc}") from exc

    if not isinstance(arr, list):
        raise FetchError("topnames payload must be a JSON list")

    reg = str(meta.params.get("registry") or "pypi.org").strip().lower()
    eco = "pypi" if "pypi" in reg else "npm"
    names: list[str] = []
    for item in arr:
        if isinstance(item, Mapping) and item.get("name"):
            n = str(item["name"]).strip()
            if n:
                names.append(n.lower() if eco == "pypi" else n)
        elif isinstance(item, str) and item.strip():
            names.append(item.strip())

    if not names:
        return []

    return [
        RawSignal(
            source_id="topnames",
            type="horizon",
            external_id=f"topnames:{eco}",
            title=f"Top {len(names)} {eco} package names cached",
            url=meta.url,
            published_at=meta.fetched_at,
            untrusted_excerpt=", ".join(names[:20]),
            confidence="confirmed",
            payload={"ecosystem": eco, "names": names},
            dedupe_key=f"topnames|horizon|{eco}",
        )
    ]


def _run_topnames(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    out: list[RawSignal] = []
    errors: list[str] = []
    for reg, eco in (("pypi.org", "pypi"), ("npmjs.org", "npm")):
        collected_names: list[str] = []
        seen_names: set[str] = set()
        for page in range(1, 6):
            url = (
                f"https://packages.ecosyste.ms/api/v1/registries/{reg}/packages"
                f"?sort=downloads&order=desc&per_page=100&page={page}"
            )
            try:
                resp = ctx.http.fetch(url, ttl_hours=spec.ttl_hours)
            except RadarError as exc:
                if page == 1:
                    errors.append(str(exc))
                break
            meta = FetchMeta(
                url=resp.url,
                fetched_at=ctx.now_iso,
                status=resp.status,
                headers=resp.headers,
                params={"registry": reg, "page": page},
                source_id=spec.id,
            )
            sigs = parse_topnames(resp.body, meta)
            for sig in sigs:
                for n in sig.payload.get("names") or []:
                    if n not in seen_names:
                        seen_names.add(n)
                        collected_names.append(n)
                out.append(sig)
        if collected_names:
            db.kv_set(ctx.db, f"topnames:{eco}", json.dumps(collected_names))

    if not out and errors:
        raise FetchError("; ".join(errors))
    return out


def _resolve_entity_info(ctx: Ctx, entity: str | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(entity, Mapping):
        eid = str(entity.get("entity_id") or "").strip()
        name = str(entity.get("name") or "").strip()
        eco = str(entity.get("ecosystem") or "").strip().lower()
        repo = str(entity.get("repo") or "").strip().lower() or None
        vis = str(entity.get("visibility") or "public").strip()
    else:
        eid = str(entity).strip()
        row = ctx.db.execute(
            "SELECT entity_id, kind, ecosystem, name, repo, visibility "
            "FROM entities WHERE entity_id = ?",
            (eid,),
        ).fetchone()
        if row is not None:
            name = str(row["name"] or "").strip()
            eco = str(row["ecosystem"] or "").strip().lower()
            repo = str(row["repo"] or "").strip().lower() or None
            vis = str(row["visibility"] or "public").strip()
        else:
            name = eid.split("/", 1)[-1] if "/" in eid else eid.split(":", 1)[-1]
            eco = ""
            repo = eid[len("gh:") :].lower() if eid.startswith("gh:") else None
            vis = "public"

    if not eco:
        if eid.startswith("pkg:pypi/"):
            eco = "pypi"
        elif eid.startswith("pkg:npm/"):
            eco = "npm"
        elif eid.startswith("pkg:cargo/"):
            eco = "cargo"
        elif eid.startswith("brew:formula/"):
            eco = "brew"
        elif eid.startswith("gh:"):
            eco = "github"

    if not name and "/" in eid:
        name = eid.split("/", 1)[1]
    return {
        "entity_id": eid,
        "name": name,
        "ecosystem": eco,
        "repo": repo,
        "visibility": vis,
    }


def _check_pypi_macos_arm64(releases_files: list[Mapping[str, Any]]) -> str:
    if not releases_files:
        return "unknown"
    has_wheel = False
    for rf in releases_files:
        fname = str(rf.get("filename") or "").lower()
        if fname.endswith(".whl"):
            has_wheel = True
            if (
                "none-any.whl" in fname
                or "universal2" in fname
                or ("macosx" in fname and "arm64" in fname)
            ):
                return "pass"
    if not has_wheel:
        return "pass"
    return "fail"


def _check_npm_macos_arm64(ver_meta: Mapping[str, Any]) -> str:
    os_list = ver_meta.get("os")
    cpu_list = ver_meta.get("cpu")
    if isinstance(os_list, list) and os_list:
        allowed_os = [str(x).lower() for x in os_list]
        if "!darwin" in allowed_os or (
            not any(x.startswith("!") for x in allowed_os) and "darwin" not in allowed_os
        ):
            return "fail"
    if isinstance(cpu_list, list) and cpu_list:
        allowed_cpu = [str(x).lower() for x in cpu_list]
        if "!arm64" in allowed_cpu or (
            not any(x.startswith("!") for x in allowed_cpu)
            and "arm64" not in allowed_cpu
            and "aarch64" not in allowed_cpu
        ):
            return "fail"
    return "pass"


def fetch_metrics(
    ctx: Ctx,
    entity: str | Mapping[str, Any],
    *,
    ttl_hours: int = 24 * 7,
) -> dict[str, Any]:
    """Fetch A5 gate inputs and popularity metrics for a verified-public entity (7-day cache).

    Queries primary registries, GitHub repo/releases/commits/contributors, ecosyste.ms,
    deps.dev, pypistats/npm_downloads, OSV `/v1/query` (no version), and Homebrew bottle info.
    """
    info = _resolve_entity_info(ctx, entity)
    eid = info["entity_id"]
    name = info["name"]
    eco = info["ecosystem"]
    repo = info["repo"]

    scopes = ctx.profile.private_scopes
    if (
        info["visibility"] == "private"
        or classify.matches_private_scope(eid, scopes)
        or classify.matches_private_scope(name, scopes)
        or (repo and classify.matches_private_scope(repo, scopes))
    ):
        return {"entity_id": eid, "skipped": "private"}

    metrics_out: dict[str, Any] = {
        "entity_id": eid,
        "name": name,
        "ecosystem": eco,
        "repo": repo,
        "osv_available": False,
        "has_mal_advisory": False,
        "has_unpatched_high_crit": False,
        "macos_arm64": "unknown",
    }

    # 1. Primary registry JSON + enrich-only metrics
    if eco == "pypi" and name:
        pkg_low = name.lower()
        try:
            resp = ctx.http.fetch(f"https://pypi.org/pypi/{pkg_low}/json", ttl_hours=ttl_hours)
            p_obj = json.loads(resp.body.decode("utf-8"))
            if isinstance(p_obj, Mapping):
                raw_p_info = p_obj.get("info")
                p_info: Mapping[str, Any] = raw_p_info if isinstance(raw_p_info, Mapping) else {}
                latest_ver = str(p_info.get("version") or "").strip()
                metrics_out["latest_version"] = latest_ver or None
                metrics_out["license"] = p_info.get("license")
                reg_repo = _extract_repo_slug(p_info.get("project_urls")) or _extract_repo_slug(
                    p_info.get("home_page")
                )
                if reg_repo:
                    metrics_out["registry_repo"] = reg_repo
                    if not repo:
                        repo = reg_repo
                        metrics_out["repo"] = reg_repo

                releases = p_obj.get("releases")
                all_dates: list[str] = []
                latest_files: list[Mapping[str, Any]] = []
                if isinstance(releases, Mapping):
                    for r_ver, r_files in releases.items():
                        if not isinstance(r_files, list):
                            continue
                        for rf in r_files:
                            if not isinstance(rf, Mapping):
                                continue
                            up_ts = normalize_timestamp(
                                rf.get("upload_time_iso_8601") or rf.get("upload_time")
                            )
                            if up_ts:
                                all_dates.append(up_ts)
                            if latest_ver and str(r_ver) == latest_ver:
                                latest_files.append(rf)
                if all_dates:
                    all_dates.sort()
                    metrics_out["first_release_at"] = all_dates[0]
                    metrics_out["last_release_at"] = all_dates[-1]
                if latest_files:
                    metrics_out["macos_arm64"] = _check_pypi_macos_arm64(latest_files)
        except (RadarError, UnicodeDecodeError, json.JSONDecodeError):
            pass

        try:
            resp_ps = ctx.http.fetch(
                f"https://pypistats.org/api/packages/{pkg_low}/recent",
                ttl_hours=ttl_hours,
            )
            ps_sigs = parse_pypistats(
                resp_ps.body,
                FetchMeta(url=resp_ps.url, fetched_at=ctx.now_iso, entity_id=eid),
            )
            if ps_sigs:
                metrics_out.update(ps_sigs[0].metrics)
        except RadarError:
            pass

        try:
            resp_eco = ctx.http.fetch(
                f"https://packages.ecosyste.ms/api/v1/registries/pypi.org/packages/{pkg_low}?mailto=",
                ttl_hours=ttl_hours,
            )
            eco_sigs = parse_ecosystems_pkg(
                resp_eco.body,
                FetchMeta(url=resp_eco.url, fetched_at=ctx.now_iso, entity_id=eid),
            )
            if eco_sigs:
                p_pay = eco_sigs[0].payload
                if (
                    p_pay.get("first_release_published_at")
                    and "first_release_at" not in metrics_out
                ):
                    metrics_out["first_release_at"] = p_pay["first_release_published_at"]
                if (
                    p_pay.get("latest_release_published_at")
                    and "last_release_at" not in metrics_out
                ):
                    metrics_out["last_release_at"] = p_pay["latest_release_published_at"]
                if p_pay.get("repository_slug") and not repo:
                    repo = str(p_pay["repository_slug"])
                    metrics_out["repo"] = repo
        except RadarError:
            pass

        try:
            resp_dd = ctx.http.fetch(
                f"https://api.deps.dev/v3/systems/pypi/packages/{pkg_low}",
                ttl_hours=ttl_hours,
            )
            dd_sigs = parse_depsdev(
                resp_dd.body,
                FetchMeta(url=resp_dd.url, fetched_at=ctx.now_iso, entity_id=eid),
            )
            if dd_sigs:
                dd_pay = dd_sigs[0].payload
                if dd_pay.get("first_published_at") and "first_release_at" not in metrics_out:
                    metrics_out["first_release_at"] = dd_pay["first_published_at"]
        except RadarError:
            pass

    elif eco == "npm" and name:
        enc_name = urllib.parse.quote(name, safe="@/")
        try:
            resp = ctx.http.fetch(f"https://registry.npmjs.org/{name}", ttl_hours=ttl_hours)
            n_obj = json.loads(resp.body.decode("utf-8"))
            if isinstance(n_obj, Mapping):
                dist_tags = n_obj.get("dist-tags")
                latest_ver = (
                    str(dist_tags.get("latest") or "") if isinstance(dist_tags, Mapping) else ""
                )
                metrics_out["latest_version"] = latest_ver or None
                metrics_out["license"] = n_obj.get("license")
                reg_repo = _extract_repo_slug(n_obj.get("repository"))
                if reg_repo:
                    metrics_out["registry_repo"] = reg_repo
                    if not repo:
                        repo = reg_repo
                        metrics_out["repo"] = reg_repo
                time_tbl = n_obj.get("time")
                if isinstance(time_tbl, Mapping):
                    created_ts = normalize_timestamp(time_tbl.get("created"))
                    latest_ts = normalize_timestamp(
                        time_tbl.get(latest_ver) or time_tbl.get("modified")
                    )
                    if created_ts:
                        metrics_out["first_release_at"] = created_ts
                    if latest_ts:
                        metrics_out["last_release_at"] = latest_ts
                versions_tbl = n_obj.get("versions")
                if (
                    isinstance(versions_tbl, Mapping)
                    and latest_ver
                    and isinstance(versions_tbl.get(latest_ver), Mapping)
                ):
                    metrics_out["macos_arm64"] = _check_npm_macos_arm64(versions_tbl[latest_ver])
        except (RadarError, UnicodeDecodeError, json.JSONDecodeError):
            pass

        try:
            resp_nd = ctx.http.fetch(
                f"https://api.npmjs.org/downloads/point/last-month/{enc_name}",
                ttl_hours=ttl_hours,
            )
            nd_sigs = parse_npm_downloads(
                resp_nd.body,
                FetchMeta(url=resp_nd.url, fetched_at=ctx.now_iso, entity_id=eid),
            )
            if nd_sigs:
                metrics_out.update(nd_sigs[0].metrics)
        except RadarError:
            pass

        try:
            resp_eco = ctx.http.fetch(
                f"https://packages.ecosyste.ms/api/v1/registries/npmjs.org/packages/{enc_name}?mailto=",
                ttl_hours=ttl_hours,
            )
            parse_ecosystems_pkg(
                resp_eco.body,
                FetchMeta(url=resp_eco.url, fetched_at=ctx.now_iso, entity_id=eid),
            )
        except RadarError:
            pass

    elif eco in ("cargo", "crates.io") and name:
        try:
            resp = ctx.http.fetch(f"https://crates.io/api/v1/crates/{name}", ttl_hours=ttl_hours)
            c_obj = json.loads(resp.body.decode("utf-8"))
            if isinstance(c_obj, Mapping):
                raw_crate = c_obj.get("crate")
                crate_info: Mapping[str, Any] = raw_crate if isinstance(raw_crate, Mapping) else {}
                metrics_out["downloads_30d"] = int(crate_info.get("recent_downloads") or 0)
                metrics_out["first_release_at"] = normalize_timestamp(crate_info.get("created_at"))
                metrics_out["last_release_at"] = normalize_timestamp(crate_info.get("updated_at"))
                metrics_out["latest_version"] = crate_info.get("max_version")
                metrics_out["macos_arm64"] = "pass"
                reg_repo = _extract_repo_slug(crate_info.get("repository"))
                if reg_repo:
                    metrics_out["registry_repo"] = reg_repo
                    if not repo:
                        repo = reg_repo
                        metrics_out["repo"] = reg_repo
        except (RadarError, UnicodeDecodeError, json.JSONDecodeError):
            pass

    elif eco == "brew" and name:
        try:
            res_b = ctx.procs.run(["brew", "info", "--json=v2", "--", name])
            if res_b.returncode == 0 and res_b.stdout.strip():
                b_obj = json.loads(res_b.stdout)
                formulae = b_obj.get("formulae") if isinstance(b_obj, Mapping) else None
                if isinstance(formulae, list) and formulae and isinstance(formulae[0], Mapping):
                    f0 = formulae[0]
                    bottle = f0.get("bottle")
                    stable = bottle.get("stable") if isinstance(bottle, Mapping) else None
                    files = stable.get("files") if isinstance(stable, Mapping) else None
                    if isinstance(files, Mapping):
                        tags = [str(k).lower() for k in files]
                        if any(k.startswith("arm64_") or k == "all" for k in tags):
                            metrics_out["macos_arm64"] = "pass"
                        else:
                            metrics_out["macos_arm64"] = "fail"
        except (RadarError, json.JSONDecodeError):
            pass

    # 2. OSV `/v1/query` by package name (no version) for GATE-05 / GATE-06 (A5)
    osv_eco_map = {"pypi": "PyPI", "npm": "npm", "cargo": "crates.io", "crates.io": "crates.io"}
    osv_eco = osv_eco_map.get(eco)
    if osv_eco and name:
        q_body = json.dumps(
            {"package": {"ecosystem": osv_eco, "name": name}},
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            resp_osv = ctx.http.fetch(
                "https://api.osv.dev/v1/querybatch",
                method="POST",
                body=json.dumps(
                    {"queries": [{"package": {"ecosystem": osv_eco, "name": name}}]},
                    separators=(",", ":"),
                ).encode("utf-8"),
                use_cache=False,
            )
            osv_obj = json.loads(resp_osv.body.decode("utf-8"))
            metrics_out["osv_available"] = True
            results_arr = osv_obj.get("results") if isinstance(osv_obj, Mapping) else None
            first_res = (
                results_arr[0]
                if isinstance(results_arr, list)
                and results_arr
                and isinstance(results_arr[0], Mapping)
                else None
            )
            vulns = first_res.get("vulns") if first_res is not None else []
            if isinstance(vulns, list):
                for v in vulns:
                    if isinstance(v, Mapping) and is_mal_advisory(v):
                        metrics_out["has_mal_advisory"] = True
        except (RadarError, UnicodeDecodeError, json.JSONDecodeError):
            _ = q_body

    # 3. GitHub repo + sub-endpoints (only if `private == false` on `repos/{o}/{r}`, Spec 12 A5)
    if repo and "/" in repo:
        try:
            res_repo = ctx.gh.api(f"repos/{repo}")
            if res_repo.returncode == 0 and res_repo.stdout.strip():
                r_obj = json.loads(res_repo.stdout)
                if isinstance(r_obj, Mapping):
                    if r_obj.get("private") is True:
                        metrics_out["private_repo"] = True
                        return metrics_out
                    metrics_out["stars"] = int(r_obj.get("stargazers_count") or 0)
                    metrics_out["forks"] = int(r_obj.get("forks_count") or 0)
                    metrics_out["archived"] = bool(r_obj.get("archived") is True)
                    metrics_out["repo_created_at"] = normalize_timestamp(r_obj.get("created_at"))
                    metrics_out["pushed_at"] = normalize_timestamp(r_obj.get("pushed_at"))
                    lic_obj = r_obj.get("license")
                    if isinstance(lic_obj, Mapping) and lic_obj.get("spdx_id"):
                        metrics_out.setdefault("license", lic_obj["spdx_id"])

                    # Sub-endpoints after confirming private == false
                    res_commits = ctx.gh.api(f"repos/{repo}/commits?per_page=1")
                    if res_commits.returncode == 0 and res_commits.stdout.strip():
                        c_arr = json.loads(res_commits.stdout)
                        if isinstance(c_arr, list) and c_arr and isinstance(c_arr[0], Mapping):
                            c_obj = c_arr[0].get("commit")
                            c_auth = c_obj.get("author") if isinstance(c_obj, Mapping) else None
                            c_date = (
                                normalize_timestamp(c_auth.get("date"))
                                if isinstance(c_auth, Mapping)
                                else None
                            )
                            if c_date:
                                metrics_out["last_commit_at"] = c_date

                    res_rels = ctx.gh.api(f"repos/{repo}/releases?per_page=5")
                    if res_rels.returncode == 0 and res_rels.stdout.strip():
                        rel_arr = json.loads(res_rels.stdout)
                        if isinstance(rel_arr, list) and rel_arr:
                            first_rel = rel_arr[0]
                            if isinstance(first_rel, Mapping):
                                r_pub = normalize_timestamp(
                                    first_rel.get("published_at") or first_rel.get("created_at")
                                )
                                if r_pub and "last_release_at" not in metrics_out:
                                    metrics_out["last_release_at"] = r_pub
                                assets = first_rel.get("assets")
                                if (
                                    isinstance(assets, list)
                                    and assets
                                    and metrics_out["macos_arm64"] == "unknown"
                                ):
                                    asset_names = [
                                        str(a.get("name") or "").lower()
                                        for a in assets
                                        if isinstance(a, Mapping)
                                    ]
                                    if any(
                                        ("darwin" in an or "macos" in an or "apple" in an)
                                        and ("arm64" in an or "aarch64" in an or "universal" in an)
                                        for an in asset_names
                                    ):
                                        metrics_out["macos_arm64"] = "pass"

                    res_contrib = ctx.gh.api(f"repos/{repo}/contributors?per_page=10")
                    if res_contrib.returncode == 0 and res_contrib.stdout.strip():
                        contrib_arr = json.loads(res_contrib.stdout)
                        if isinstance(contrib_arr, list):
                            metrics_out["contributors_count"] = len(contrib_arr)
                            contribs = [
                                int(c.get("contributions") or 0)
                                for c in contrib_arr
                                if isinstance(c, Mapping)
                            ]
                            total_c = sum(contribs)
                            if total_c > 0 and contribs:
                                metrics_out["top_contributor_share"] = round(
                                    max(contribs) / total_c, 3
                                )

                    enc_proj = urllib.parse.quote(f"github.com/{repo}", safe="")
                    try:
                        resp_proj = ctx.http.fetch(
                            f"https://api.deps.dev/v3/projects/{enc_proj}",
                            ttl_hours=ttl_hours,
                        )
                        p_sigs = parse_depsdev(
                            resp_proj.body,
                            FetchMeta(url=resp_proj.url, fetched_at=ctx.now_iso, entity_id=eid),
                        )
                        if p_sigs and "scorecard" in p_sigs[0].metrics:
                            metrics_out["scorecard"] = p_sigs[0].metrics["scorecard"]
                    except RadarError:
                        pass
        except (RadarError, json.JSONDecodeError):
            pass

    # Persist numeric metrics when entity exists in DB
    if eid:
        ent_exists = ctx.db.execute(
            "SELECT 1 FROM entities WHERE entity_id = ?",
            (eid,),
        ).fetchone()
        if ent_exists is not None:
            for metric_key in ("stars", "downloads_30d", "scorecard"):
                val = metrics_out.get(metric_key)
                if isinstance(val, int | float):
                    ctx.db.execute(
                        "INSERT INTO metrics(entity_id, metric, value, observed_at) "
                        "VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(entity_id, metric, observed_at) "
                        "DO UPDATE SET value = excluded.value",
                        (eid, metric_key, float(val), ctx.now_iso),
                    )
            ctx.db.commit()

    return metrics_out


NPM_REGISTRY_META_SPEC = SourceSpec(
    id="npm_registry_meta",
    req=("LIFE-02", "SEC-11", "SEC-12"),
    archetype="json_api",
    url="https://registry.npmjs.org/{name}",
    cadence="daily",
    hosts=("registry.npmjs.org",),
    signal_types=("lifecycle", "compromise"),
    ttl_hours=24,
    parser=parse_npm_registry_meta,
    runner=_run_npm_registry_meta,
)

PYPI_META_SPEC = SourceSpec(
    id="pypi_meta",
    req=("LIFE-03", "LIFE-04", "SEC-10", "SEC-11"),
    archetype="json_api",
    url="https://pypi.org/pypi/{name}/json",
    cadence="daily",
    hosts=("pypi.org",),
    signal_types=("lifecycle", "compromise"),
    ttl_hours=24,
    parser=parse_pypi_meta,
    runner=_run_pypi_meta,
)

ENDOFLIFE_SPEC = SourceSpec(
    id="endoflife",
    req=("LIFE-05",),
    archetype="json_api",
    url="https://endoflife.date/api/v1/products/{product}",
    cadence="weekly",
    hosts=("endoflife.date",),
    signal_types=("lifecycle",),
    ttl_hours=24 * 7,
    parser=parse_endoflife,
    runner=_run_endoflife,
)

OSSINSIGHT_TRENDING_SPEC = SourceSpec(
    id="ossinsight_trending",
    req=("SRC-CODE-05",),
    archetype="json_api",
    url="https://api.ossinsight.io/v1/trends/repos/?period=past_week&language=Python",
    cadence="weekly",
    hosts=("api.ossinsight.io",),
    signal_types=("horizon",),
    ttl_hours=24 * 7,
    parser=parse_ossinsight_trending,
    enabled=False,
    skipped_reason="BROKEN: The read operation timed out",
)

ECOSYSTEMS_PKG_SPEC = SourceSpec(
    id="ecosystems_pkg",
    req=("SRC-CODE-07",),
    archetype="json_api",
    url="https://packages.ecosyste.ms/api/v1/registries/pypi.org/packages/litellm?mailto=",
    cadence="on-demand",
    hosts=("packages.ecosyste.ms",),
    signal_types=(),
    ttl_hours=24 * 7,
    parser=parse_ecosystems_pkg,
)

DEPSDEV_SPEC = SourceSpec(
    id="depsdev",
    req=("SRC-CODE-08", "SRC-SEC-10"),
    archetype="json_api",
    url="https://api.deps.dev/v3/systems/pypi/packages/litellm",
    cadence="on-demand",
    hosts=("api.deps.dev",),
    signal_types=(),
    ttl_hours=24 * 7,
    parser=parse_depsdev,
)

PYPISTATS_SPEC = SourceSpec(
    id="pypistats",
    req=("SRC-CODE-09",),
    archetype="json_api",
    url="https://pypistats.org/api/packages/litellm/recent",
    cadence="on-demand",
    hosts=("pypistats.org",),
    signal_types=(),
    ttl_hours=24 * 7,
    parser=parse_pypistats,
)

NPM_DOWNLOADS_SPEC = SourceSpec(
    id="npm_downloads",
    req=("SRC-CODE-10",),
    archetype="json_api",
    url="https://api.npmjs.org/downloads/point/last-month/react",
    cadence="on-demand",
    hosts=("api.npmjs.org",),
    signal_types=(),
    ttl_hours=24 * 7,
    parser=parse_npm_downloads,
)

CRATES_NEW_SPEC = SourceSpec(
    id="crates_new",
    req=("SRC-CODE-11",),
    archetype="json_api",
    url="https://crates.io/api/v1/summary",
    cadence="weekly",
    hosts=("crates.io",),
    signal_types=("horizon",),
    ttl_hours=24 * 7,
    parser=parse_crates_new,
)

BREW_NEW_FORMULAE_SPEC = SourceSpec(
    id="brew_new_formulae",
    req=("SRC-CODE-12",),
    archetype="git_log",
    url=None,
    cadence="weekly",
    hosts=("api.github.com",),
    signal_types=("horizon",),
    ttl_hours=24 * 7,
    parser=parse_brew_new_formulae,
    runner=_run_brew_new_formulae,
)

TOPNAMES_SPEC = SourceSpec(
    id="topnames",
    req=("T10",),
    archetype="json_api",
    url="https://packages.ecosyste.ms/api/v1/registries/pypi.org/packages?sort=downloads&order=desc&per_page=100&page=1",
    cadence="monthly",
    hosts=("packages.ecosyste.ms",),
    signal_types=(),
    ttl_hours=24 * 30,
    parser=parse_topnames,
    runner=_run_topnames,
)

REGISTRY_SPECS: tuple[SourceSpec, ...] = (
    NPM_REGISTRY_META_SPEC,
    PYPI_META_SPEC,
    ENDOFLIFE_SPEC,
    OSSINSIGHT_TRENDING_SPEC,
    ECOSYSTEMS_PKG_SPEC,
    DEPSDEV_SPEC,
    PYPISTATS_SPEC,
    NPM_DOWNLOADS_SPEC,
    CRATES_NEW_SPEC,
    BREW_NEW_FORMULAE_SPEC,
    TOPNAMES_SPEC,
)
