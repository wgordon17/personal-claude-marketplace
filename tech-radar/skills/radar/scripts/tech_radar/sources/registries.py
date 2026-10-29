"""Registry sources: npm_registry_meta, pypi_meta, and endoflife (Spec 03, Spec 07 §1 & §3)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..errors import FetchError, RadarError
from .base import FetchMeta, RawSignal, SourceSpec, normalize_timestamp

if TYPE_CHECKING:
    from ..context import Ctx

_GITHUB_SLUG_FROM_URL_RE = re.compile(
    r"github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)


def _extract_repo_slug(raw: Any) -> str | None:
    if isinstance(raw, Mapping):
        raw = raw.get("url") or raw.get("Repository") or raw.get("Source") or ""
    text = str(raw or "").strip()
    if not text:
        return None
    m = _GITHUB_SLUG_FROM_URL_RE.search(text)
    if not m:
        return None
    slug = m.group(1).rstrip("/.")
    if slug.endswith(".git"):
        slug = slug[:-4]
    return slug.lower()


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
                "is_eol": is_eol,
                "is_maintained": rel.get("isMaintained", not is_eol),
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

REGISTRY_SPECS: tuple[SourceSpec, ...] = (
    NPM_REGISTRY_META_SPEC,
    PYPI_META_SPEC,
    ENDOFLIFE_SPEC,
)
