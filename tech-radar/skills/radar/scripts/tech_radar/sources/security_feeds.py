"""Security feed sources: osv_batch, ghsa_malware, cisa_kev, sec blogs, and hn_compromise."""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from .. import db
from ..errors import FetchError, RadarError
from .base import (
    FetchMeta,
    RawSignal,
    SourceSpec,
    lookback_since,
    make_feed_parser,
    normalize_timestamp,
    parse_feed_bytes,
)

if TYPE_CHECKING:
    from ..context import Ctx

OSV_CHUNK_SIZE = 1000

HN_COMPROMISE_KEYWORDS: tuple[str, ...] = (
    "compromised",
    "malware",
    "backdoor",
    "malicious",
)

VENDOR_SEC_BLOG_URLS: tuple[str, ...] = (
    "https://securitylabs.datadoghq.com/rss/feed.xml",
    "https://www.wiz.io/feed/rss.xml",
    "https://snyk.io/blog/feed/",
)

REGISTRY_SEC_NEWS_URLS: tuple[str, ...] = (
    "https://blog.pypi.org/feed_rss_created.xml",
    "https://github.blog/changelog/feed/",
)

_ECOSYSTEM_TO_OSV: dict[str, str] = {
    "pypi": "PyPI",
    "npm": "npm",
    "cargo": "crates.io",
    "crates.io": "crates.io",
    "golang": "Go",
    "go": "Go",
    "github-actions": "GitHub Actions",
    "github actions": "GitHub Actions",
    "rubygems": "RubyGems",
    "packagist": "Packagist",
    "maven": "Maven",
    "nuget": "NuGet",
}

_OSV_TO_ENTITY_PREFIX: dict[str, str] = {
    "pypi": "pkg:pypi/",
    "npm": "pkg:npm/",
    "crates.io": "pkg:cargo/",
    "cargo": "pkg:cargo/",
    "go": "pkg:golang/",
    "golang": "pkg:golang/",
    "github actions": "gha:",
    "github-actions": "gha:",
}


def osv_ecosystem(ecosystem: str | None, entity_id: str = "") -> str | None:
    """Map an internal ecosystem or entity_id prefix to the OSV ecosystem string."""
    if ecosystem:
        mapped = _ECOSYSTEM_TO_OSV.get(ecosystem.strip().lower())
        if mapped:
            return mapped
    if entity_id.startswith("pkg:pypi/"):
        return "PyPI"
    if entity_id.startswith("pkg:npm/"):
        return "npm"
    if entity_id.startswith("pkg:cargo/"):
        return "crates.io"
    if entity_id.startswith("pkg:golang/"):
        return "Go"
    if entity_id.startswith("gha:"):
        return "GitHub Actions"
    return None


def entity_id_from_osv_package(ecosystem: str | None, name: str | None) -> str | None:
    """Convert an OSV/GHSA `(ecosystem, name)` pair to a canonical `entity_id`."""
    if not ecosystem or not name:
        return None
    eco_low = ecosystem.strip().lower()
    prefix = _OSV_TO_ENTITY_PREFIX.get(eco_low)
    if not prefix:
        return None
    clean_name = name.strip()
    if eco_low in ("pypi", "github actions", "github-actions"):
        clean_name = clean_name.lower()
    return f"{prefix}{clean_name}"


def is_mal_advisory(vuln: Mapping[str, Any]) -> bool:
    """Return True if an OSV or GHSA advisory record represents malware/compromise (Spec 07 §1)."""
    vuln_id = str(vuln.get("id") or vuln.get("ghsa_id") or "").strip().upper()
    if vuln_id.startswith("MAL-"):
        return True

    aliases = vuln.get("aliases") or []
    if isinstance(aliases, list):
        for alias in aliases:
            if str(alias).strip().upper().startswith("MAL-"):
                return True

    identifiers = vuln.get("identifiers") or []
    if isinstance(identifiers, list):
        for ident in identifiers:
            if isinstance(ident, dict) and str(ident.get("value") or "").upper().startswith("MAL-"):
                return True

    if str(vuln.get("type") or "").strip().lower() == "malware":
        return True

    db_spec = vuln.get("database_specific")
    cwe_list: list[str] = []
    if isinstance(db_spec, dict):
        raw_cwes = db_spec.get("cwe_ids") or db_spec.get("cwes") or []
        if isinstance(raw_cwes, list):
            for c in raw_cwes:
                if isinstance(c, dict):
                    cwe_list.append(str(c.get("cwe_id") or c.get("id") or "").upper())
                else:
                    cwe_list.append(str(c).upper())
    raw_top_cwes = vuln.get("cwe_ids") or vuln.get("cwes") or []
    if isinstance(raw_top_cwes, list):
        for c in raw_top_cwes:
            if isinstance(c, dict):
                cwe_list.append(str(c.get("cwe_id") or c.get("id") or "").upper())
            else:
                cwe_list.append(str(c).upper())
    if "CWE-506" in cwe_list:
        return True

    summary = str(vuln.get("summary") or "").strip().lower()
    return summary.startswith("malicious package in") or summary.startswith("malicious code in")


def is_stub_worth_fetching_in_hourly(
    stub: Mapping[str, Any],
    *,
    is_direct: bool,
    kev_cves: frozenset[str] = frozenset(),
) -> bool:
    """Determine if a querybatch stub should have its `/v1/vulns/{id}` detail fetched (A36).

    Transitive usages only keep MAL-* advisories. Direct usages fetch MAL-*, GHSA-*
    (which may be malware or have MAL/KEV aliases), or known KEV CVEs.
    """
    vid = str(stub.get("id") or "").strip().upper()
    aliases = [str(a).strip().upper() for a in (stub.get("aliases") or []) if a]
    all_ids = [vid, *aliases]

    has_mal_id = any(i.startswith("MAL-") for i in all_ids)
    if not is_direct:
        return has_mal_id

    if has_mal_id:
        return True
    if any(i in kev_cves for i in all_ids):
        return True
    if vid.startswith("GHSA-"):
        return True
    return str(stub.get("type") or "").lower() == "malware"


def build_osv_queries(
    usages_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Build sorted, deduplicated OSV query items from usages rows (Spec 12 A36).

    Each item has keys: `ecosystem`, `name`, `version`, `entity_id`, `direct`.
    Sorted by `(ecosystem, name, version)`.
    """
    by_tuple: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in usages_rows:
        vis = str(row.get("visibility") or "public")
        if vis != "public":
            continue
        ver = str(row.get("version") or "").strip()
        if not ver:
            continue
        entity_id = str(row.get("entity_id") or "")
        eco = osv_ecosystem(row.get("ecosystem"), entity_id)
        if not eco:
            continue
        raw_name = str(row.get("name") or "").strip()
        if not raw_name and "/" in entity_id:
            raw_name = entity_id.split("/", 1)[1]
        if not raw_name:
            continue
        direct = 1 if int(row.get("direct") or 0) > 0 else 0
        key = (eco, raw_name, ver)
        existing = by_tuple.get(key)
        if existing is None:
            by_tuple[key] = {
                "ecosystem": eco,
                "name": raw_name,
                "version": ver,
                "entity_id": entity_id,
                "direct": direct,
            }
        else:
            existing["direct"] = max(int(existing["direct"]), direct)

    return [by_tuple[k] for k in sorted(by_tuple.keys())]


def chunk_osv_queries(
    queries: Sequence[dict[str, Any]],
    chunk_size: int = OSV_CHUNK_SIZE,
) -> list[list[dict[str, Any]]]:
    """Split sorted OSV query items into chunks of at most `chunk_size` (default 1,000, A36)."""
    if chunk_size <= 0:
        chunk_size = OSV_CHUNK_SIZE
    return [list(queries[i : i + chunk_size]) for i in range(0, len(queries), chunk_size)]


def filter_querybatch_hits(
    queries: Sequence[dict[str, Any]],
    results: Sequence[Mapping[str, Any]],
    *,
    kev_cves: frozenset[str] = frozenset(),
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Pair each query with its returned vuln stubs, discarding transitive non-MAL stubs."""
    kept: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for query_item, res_item in zip(queries, results, strict=False):
        if not isinstance(res_item, Mapping):
            continue
        vulns = res_item.get("vulns") or []
        if not isinstance(vulns, list):
            continue
        is_direct = bool(int(query_item.get("direct") or 0) > 0)
        for stub in vulns:
            if not isinstance(stub, Mapping):
                continue
            if not is_stub_worth_fetching_in_hourly(stub, is_direct=is_direct, kev_cves=kev_cves):
                continue
            kept.append((query_item, dict(stub)))
    return kept


def _extract_affected_versions_and_entity(
    vuln: Mapping[str, Any],
    fallback_entity_id: str | None = None,
) -> tuple[str | None, list[str], list[dict[str, Any]]]:
    entity_hint = fallback_entity_id
    explicit_versions: list[str] = []
    ranges_out: list[dict[str, Any]] = []

    affected = vuln.get("affected")
    if isinstance(affected, list):
        for aff in affected:
            if not isinstance(aff, Mapping):
                continue
            pkg = aff.get("package")
            if isinstance(pkg, Mapping) and entity_hint is None:
                entity_hint = entity_id_from_osv_package(
                    str(pkg.get("ecosystem") or ""),
                    str(pkg.get("name") or ""),
                )
            vers = aff.get("versions")
            if isinstance(vers, list):
                for v in vers:
                    if v and str(v) not in explicit_versions:
                        explicit_versions.append(str(v))
            rngs = aff.get("ranges")
            if isinstance(rngs, list):
                for r in rngs:
                    if isinstance(r, Mapping):
                        ranges_out.append(dict(r))

    return entity_hint, explicit_versions, ranges_out


def vuln_detail_to_raw_signal(
    vuln: Mapping[str, Any],
    *,
    fetched_at: str,
    entity_hint: str | None = None,
    queried_version: str | None = None,
    is_direct: bool = True,
    kev_cves: frozenset[str] = frozenset(),
) -> RawSignal | None:
    """Convert an OSV `/v1/vulns/{id}` JSON object into a `RawSignal`."""
    vuln_id = str(vuln.get("id") or "").strip()
    if not vuln_id:
        return None

    is_mal = is_mal_advisory(vuln)
    aliases = [str(a).strip() for a in (vuln.get("aliases") or []) if a]
    all_ids_upper = {vuln_id.upper(), *(a.upper() for a in aliases)}
    matched_kev = sorted(all_ids_upper & {c.upper() for c in kev_cves})

    if not is_direct and not is_mal:
        return None

    sig_type = "compromise" if is_mal else ("kev" if matched_kev else "compromise")
    resolved_hint, affected_versions, affected_ranges = _extract_affected_versions_and_entity(
        vuln,
        fallback_entity_id=entity_hint,
    )

    summary = str(vuln.get("summary") or "").strip()
    details = str(vuln.get("details") or "").strip()
    title = summary or f"OSV advisory {vuln_id}"
    excerpt = details or summary or title
    published_at = normalize_timestamp(vuln.get("published") or vuln.get("modified")) or fetched_at
    modified_at = normalize_timestamp(vuln.get("modified") or vuln.get("published")) or published_at
    withdrawn_raw = vuln.get("withdrawn")
    withdrawn = 1 if withdrawn_raw else 0

    refs = vuln.get("references") or []
    url: str | None = f"https://osv.dev/vulnerability/{vuln_id}"
    if isinstance(refs, list):
        for ref in refs:
            if isinstance(ref, Mapping) and ref.get("url"):
                url = str(ref["url"])
                break

    db_spec = vuln.get("database_specific")
    cwe_ids: list[str] = []
    if isinstance(db_spec, Mapping) and isinstance(db_spec.get("cwe_ids"), list):
        cwe_ids = [str(c) for c in db_spec["cwe_ids"]]

    payload: dict[str, Any] = {
        "advisory_id": vuln_id,
        "aliases": aliases,
        "is_mal": is_mal,
        "kev_cves": matched_kev,
        "cwe_ids": cwe_ids,
        "published": published_at,
        "modified": modified_at,
        "withdrawn": withdrawn_raw,
        "affected_versions": affected_versions,
        "affected_ranges": affected_ranges,
        "queried_version": queried_version,
        "direct": is_direct,
    }
    dedupe = f"osv_batch|{sig_type}|{resolved_hint or 'unknown'}|{vuln_id}|{queried_version or ''}"
    return RawSignal(
        source_id="osv_batch",
        type=sig_type,
        external_id=vuln_id,
        title=title,
        url=url,
        published_at=published_at,
        untrusted_excerpt=excerpt,
        entity_hint=resolved_hint,
        confidence="confirmed",
        payload=payload,
        dedupe_key=dedupe,
        withdrawn=withdrawn,
    )


def parse_osv_batch(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Pure parser for OSV JSON (supports either a vuln detail object or querybatch response)."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid OSV JSON: {exc}") from exc

    if not isinstance(obj, Mapping):
        raise FetchError("OSV payload must be a JSON object")

    if "id" in obj:
        sig = vuln_detail_to_raw_signal(
            obj,
            fetched_at=meta.fetched_at,
            entity_hint=meta.entity_id,
        )
        return [sig] if sig is not None else []

    results = obj.get("results")
    if not isinstance(results, list):
        return []

    out: list[RawSignal] = []
    for idx, item in enumerate(results):
        if not isinstance(item, Mapping):
            continue
        for stub in item.get("vulns") or []:
            if not isinstance(stub, Mapping):
                continue
            if "summary" in stub or "affected" in stub or "database_specific" in stub:
                sig = vuln_detail_to_raw_signal(stub, fetched_at=meta.fetched_at)
                if sig is not None:
                    out.append(sig)
            else:
                vid = str(stub.get("id") or f"osv-{idx}").strip()
                is_mal = vid.upper().startswith("MAL-")
                out.append(
                    RawSignal(
                        source_id="osv_batch",
                        type="compromise" if is_mal else "kev",
                        external_id=vid,
                        title=f"OSV advisory {vid}",
                        url=f"https://osv.dev/vulnerability/{vid}",
                        published_at=normalize_timestamp(stub.get("modified")) or meta.fetched_at,
                        untrusted_excerpt=f"OSV hit {vid}",
                        confidence="confirmed",
                        payload={
                            "advisory_id": vid,
                            "aliases": list(stub.get("aliases") or []),
                            "is_mal": is_mal,
                            "modified": stub.get("modified"),
                        },
                    )
                )
    return out


def _fetch_osv_vuln_detail(
    ctx: Ctx,
    vuln_id: str,
    modified: str | None,
) -> dict[str, Any] | None:
    """Fetch `/v1/vulns/{id}` with `(id, modified)` caching in `cache.db` (Spec 12 A36)."""
    cache_key = f"osv_vuln|{vuln_id}|{modified or ''}"
    if modified and ctx.cache_db is not None:
        cached = db.cache_get(ctx.cache_db, cache_key)
        if cached is not None and cached.get("body"):
            try:
                loaded = json.loads(bytes(cached["body"]).decode("utf-8"))
                if isinstance(loaded, dict):
                    return loaded
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass

    url = f"https://api.osv.dev/v1/vulns/{vuln_id}"
    resp = ctx.http.fetch(url, method="GET", ttl_hours=24)
    try:
        detail = json.loads(resp.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid OSV vuln detail JSON for {vuln_id}: {exc}") from exc

    if not isinstance(detail, dict):
        return None

    actual_mod = str(detail.get("modified") or modified or "")
    if ctx.cache_db is not None and actual_mod:
        db.cache_put(
            ctx.cache_db,
            key=f"osv_vuln|{vuln_id}|{actual_mod}",
            url=url,
            etag=resp.headers.get("etag"),
            last_modified=resp.headers.get("last-modified"),
            fetched_at=ctx.now_iso,
            status=resp.status,
            body=resp.body,
        )
    return detail


def _known_kev_cves(ctx: Ctx) -> frozenset[str]:
    rows = ctx.db.execute(
        "SELECT external_id FROM signals WHERE source_id = 'cisa_kev' AND external_id IS NOT NULL"
    ).fetchall()
    return frozenset(str(r["external_id"]).strip().upper() for r in rows if r["external_id"])


def _run_osv_batch(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    _ = spec
    rows = ctx.db.execute(
        "SELECT u.entity_id, u.version, MAX(u.direct) AS direct, "
        "e.ecosystem, e.name, e.visibility "
        "FROM usages u JOIN entities e ON e.entity_id = u.entity_id "
        "WHERE u.version IS NOT NULL AND u.version != '' "
        "AND u.context NOT LIKE 'interest:%' "
        "AND e.visibility = 'public' "
        "GROUP BY u.entity_id, u.version"
    ).fetchall()
    usage_dicts = [dict(r) for r in rows]
    queries = build_osv_queries(usage_dicts)
    if not queries:
        return []

    kev_cves = _known_kev_cves(ctx)
    signals: list[RawSignal] = []

    for chunk in chunk_osv_queries(queries, OSV_CHUNK_SIZE):
        payload_queries = [
            {
                "package": {
                    "ecosystem": q["ecosystem"],
                    "name": q["name"],
                },
                "version": q["version"],
            }
            for q in chunk
        ]
        body_bytes = json.dumps({"queries": payload_queries}, separators=(",", ":")).encode("utf-8")
        resp = ctx.http.fetch(
            "https://api.osv.dev/v1/querybatch",
            method="POST",
            body=body_bytes,
            use_cache=False,
        )
        try:
            batch_obj = json.loads(resp.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FetchError(f"invalid OSV querybatch JSON: {exc}") from exc

        results_list = batch_obj.get("results") if isinstance(batch_obj, dict) else None
        if not isinstance(results_list, list):
            continue

        pairs = filter_querybatch_hits(chunk, results_list, kev_cves=kev_cves)
        for q_item, stub in pairs:
            vid = str(stub.get("id") or "").strip()
            if not vid:
                continue
            mod = str(stub.get("modified") or "") or None
            detail = _fetch_osv_vuln_detail(ctx, vid, mod)
            if detail is None:
                continue
            sig = vuln_detail_to_raw_signal(
                detail,
                fetched_at=ctx.now_iso,
                entity_hint=str(q_item["entity_id"]),
                queried_version=str(q_item["version"]),
                is_direct=bool(int(q_item["direct"]) > 0),
                kev_cves=kev_cves,
            )
            if sig is not None:
                signals.append(sig)

    return signals


def parse_ghsa_malware(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse GitHub REST `/advisories?type=malware` JSON array into `RawSignal`s."""
    try:
        arr = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid GHSA JSON: {exc}") from exc

    if not isinstance(arr, list):
        raise FetchError("GHSA response must be a JSON list")

    out: list[RawSignal] = []
    for item in arr:
        if not isinstance(item, Mapping):
            continue
        ghsa_id = str(item.get("ghsa_id") or item.get("id") or "").strip()
        if not ghsa_id:
            continue
        summary = str(item.get("summary") or f"GHSA malware {ghsa_id}").strip()
        desc = str(item.get("description") or summary).strip()
        html_url = str(item.get("html_url") or item.get("url") or "").strip() or None
        pub = normalize_timestamp(item.get("published_at") or item.get("updated_at"))
        upd = normalize_timestamp(item.get("updated_at") or item.get("published_at"))
        withdrawn_at = item.get("withdrawn_at")

        vulns = item.get("vulnerabilities") or []
        entity_hint: str | None = None
        affected_ranges: list[str] = []
        affected_packages: list[dict[str, Any]] = []
        if isinstance(vulns, list):
            for v in vulns:
                if not isinstance(v, Mapping):
                    continue
                pkg = v.get("package")
                if isinstance(pkg, Mapping):
                    eco = str(pkg.get("ecosystem") or "")
                    name = str(pkg.get("name") or "")
                    cand_hint = entity_id_from_osv_package(eco, name)
                    if entity_hint is None and cand_hint is not None:
                        entity_hint = cand_hint
                    v_range = str(v.get("vulnerable_version_range") or "").strip()
                    if v_range:
                        affected_ranges.append(v_range)
                    affected_packages.append(
                        {
                            "ecosystem": eco,
                            "name": name,
                            "entity_id": cand_hint,
                            "vulnerable_version_range": v_range,
                            "first_patched_version": v.get("first_patched_version"),
                        }
                    )

        out.append(
            RawSignal(
                source_id="ghsa_malware",
                type="compromise",
                external_id=ghsa_id,
                title=summary,
                url=html_url,
                published_at=pub or meta.fetched_at,
                untrusted_excerpt=desc,
                entity_hint=entity_hint,
                confidence="confirmed",
                payload={
                    "advisory_id": ghsa_id,
                    "cve_id": item.get("cve_id"),
                    "type": item.get("type", "malware"),
                    "is_mal": True,
                    "published": pub or meta.fetched_at,
                    "modified": upd or pub or meta.fetched_at,
                    "withdrawn": withdrawn_at,
                    "vulnerabilities": affected_packages,
                    "vulnerable_version_ranges": affected_ranges,
                },
                dedupe_key=f"ghsa_malware|compromise|{entity_hint or 'any'}|{ghsa_id}",
                withdrawn=1 if withdrawn_at else 0,
            )
        )
    return out


def _run_ghsa_malware(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    since_date = lookback_since(ctx, timedelta(days=7)).strftime("%Y-%m-%d")
    endpoint = f"advisories?type=malware&updated=>={since_date}&per_page=100"
    try:
        res = ctx.gh.api(endpoint)
    except RadarError:
        res = ctx.gh.api("advisories?type=malware&per_page=100")
    if res.returncode != 0:
        raise FetchError(f"gh api advisories failed: {res.stderr}")
    meta = FetchMeta(
        url=endpoint,
        fetched_at=ctx.now_iso,
        status=200,
        source_id=spec.id,
    )
    return parse_ghsa_malware(res.stdout.encode("utf-8"), meta)


def parse_cisa_kev(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse CISA Known Exploited Vulnerabilities JSON catalog into `RawSignal`s."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid CISA KEV JSON: {exc}") from exc

    if not isinstance(obj, Mapping):
        raise FetchError("CISA KEV payload must be a JSON object")

    vulns = obj.get("vulnerabilities")
    if not isinstance(vulns, list):
        return []

    out: list[RawSignal] = []
    for v in vulns:
        if not isinstance(v, Mapping):
            continue
        cve_id = str(v.get("cveID") or "").strip().upper()
        if not cve_id:
            continue
        vendor = str(v.get("vendorProject") or "").strip()
        product = str(v.get("product") or "").strip()
        vname = str(v.get("vulnerabilityName") or cve_id).strip()
        desc = str(v.get("shortDescription") or vname).strip()
        date_added = normalize_timestamp(v.get("dateAdded")) or meta.fetched_at
        out.append(
            RawSignal(
                source_id="cisa_kev",
                type="kev",
                external_id=cve_id,
                title=f"{cve_id}: {vname}",
                url=f"https://nvd.nist.gov/vuln/detail/{cve_id}",
                published_at=date_added,
                untrusted_excerpt=desc,
                confidence="confirmed",
                payload={
                    "cve_id": cve_id,
                    "vendorProject": vendor,
                    "product": product,
                    "dateAdded": v.get("dateAdded"),
                    "dueDate": v.get("dueDate"),
                    "knownRansomwareCampaignUse": v.get("knownRansomwareCampaignUse"),
                },
                dedupe_key=f"cisa_kev|kev|{cve_id}",
            )
        )
    return out


def parse_vendor_sec_blogs(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse Datadog / Wiz / Snyk RSS feeds into unconfirmed compromise signals."""
    return parse_feed_bytes(
        data,
        meta,
        source_id="vendor_sec_blogs",
        signal_type="compromise",
        confidence="unconfirmed",
    )


def _run_multi_feed(
    spec: SourceSpec,
    ctx: Ctx,
    urls: Sequence[str],
    *,
    signal_type: str,
    confidence: str,
) -> list[RawSignal]:
    all_signals: list[RawSignal] = []
    errors: list[str] = []
    for url in urls:
        try:
            resp = ctx.http.fetch(url, ttl_hours=spec.ttl_hours)
            meta = FetchMeta(
                url=resp.url,
                fetched_at=ctx.now_iso,
                status=resp.status,
                headers=resp.headers,
                source_id=spec.id,
            )
            parser_fn = spec.parser or make_feed_parser(
                spec.id,
                signal_type=signal_type,
                confidence=confidence,
            )
            all_signals.extend(parser_fn(resp.body, meta))
        except RadarError as exc:
            errors.append(str(exc))
    if not all_signals and errors:
        raise FetchError("; ".join(errors))
    return all_signals


def parse_registry_sec_news(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse PyPI blog / GitHub changelog RSS feeds into compromise or technique signals."""
    raw_items = parse_feed_bytes(
        data,
        meta,
        source_id="registry_sec_news",
        signal_type="compromise",
        confidence="unconfirmed",
    )
    out: list[RawSignal] = []
    sec_re = (
        "compromis",
        "malware",
        "backdoor",
        "malicious",
        "phish",
        "quarantin",
        "hijack",
        "typosquat",
        "stealer",
        "exfiltrat",
        "vulnerability",
        "advisory",
    )
    for sig in raw_items:
        combined = f"{sig.title} {sig.untrusted_excerpt}".lower()
        if any(tok in combined for tok in sec_re):
            sig.type = "compromise"
            sig.confidence = "unconfirmed"
        else:
            sig.type = "technique"
            sig.confidence = "confirmed"
        out.append(sig)
    return out


def parse_hn_compromise(data: bytes, meta: FetchMeta) -> list[RawSignal]:
    """Parse HN Algolia `search_by_date` JSON hits into unconfirmed compromise signals."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError(f"invalid HN Algolia JSON: {exc}") from exc

    if not isinstance(obj, Mapping):
        raise FetchError("HN Algolia payload must be a JSON object")

    hits = obj.get("hits")
    if not isinstance(hits, list):
        return []

    out: list[RawSignal] = []
    for hit in hits:
        if not isinstance(hit, Mapping):
            continue
        obj_id = str(hit.get("objectID") or hit.get("story_id") or "").strip()
        if not obj_id:
            continue
        title = str(
            hit.get("title") or hit.get("story_title") or hit.get("comment_text") or obj_id
        ).strip()
        excerpt = str(
            hit.get("story_text") or hit.get("comment_text") or hit.get("title") or title
        ).strip()
        url = str(
            hit.get("url")
            or hit.get("story_url")
            or f"https://news.ycombinator.com/item?id={obj_id}"
        ).strip()
        pub = normalize_timestamp(hit.get("created_at") or hit.get("created_at_i"))
        out.append(
            RawSignal(
                source_id="hn_compromise",
                type="compromise",
                external_id=obj_id,
                title=title,
                url=url,
                published_at=pub or meta.fetched_at,
                untrusted_excerpt=excerpt,
                confidence="unconfirmed",
                metrics={
                    "points": int(hit.get("points") or 0),
                    "num_comments": int(hit.get("num_comments") or 0),
                },
                dedupe_key=f"hn_compromise|compromise|{obj_id}",
            )
        )
    return out


def _run_hn_compromise(spec: SourceSpec, ctx: Ctx) -> list[RawSignal]:
    """Issue one HN Algolia request per keyword (4 requests total, no package names — A42)."""
    since_epoch = int((ctx.now - timedelta(hours=2)).timestamp())
    seen_ids: set[str] = set()
    signals: list[RawSignal] = []

    for kw in HN_COMPROMISE_KEYWORDS:
        q_enc = urllib.parse.quote(kw, safe="")
        url = (
            f"https://hn.algolia.com/api/v1/search_by_date"
            f"?query={q_enc}&numericFilters=created_at_i>{since_epoch}"
        )
        resp = ctx.http.fetch(url, ttl_hours=1)
        meta = FetchMeta(
            url=resp.url,
            fetched_at=ctx.now_iso,
            status=resp.status,
            headers=resp.headers,
            params={"query": kw},
            source_id=spec.id,
        )
        for sig in parse_hn_compromise(resp.body, meta):
            if sig.external_id not in seen_ids:
                seen_ids.add(sig.external_id)
                signals.append(sig)
    return signals


OSV_BATCH_SPEC = SourceSpec(
    id="osv_batch",
    req=("SRC-SEC-01", "SRC-SEC-02"),
    archetype="json_api",
    url="https://api.osv.dev/v1/querybatch",
    cadence="hourly",
    hosts=("api.osv.dev",),
    signal_types=("compromise", "kev"),
    ttl_hours=1,
    parser=parse_osv_batch,
    runner=_run_osv_batch,
)

GHSA_MALWARE_SPEC = SourceSpec(
    id="ghsa_malware",
    req=("SRC-SEC-03",),
    archetype="gh_api",
    url="advisories?type=malware&per_page=100",
    cadence="hourly",
    hosts=("api.github.com",),
    signal_types=("compromise",),
    ttl_hours=1,
    parser=parse_ghsa_malware,
    runner=_run_ghsa_malware,
)

CISA_KEV_SPEC = SourceSpec(
    id="cisa_kev",
    req=("SRC-SEC-04",),
    archetype="json_api",
    url="https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
    cadence="hourly",
    hosts=("www.cisa.gov",),
    signal_types=("kev",),
    ttl_hours=1,
    parser=parse_cisa_kev,
)

SOCKET_BLOG_SPEC = SourceSpec(
    id="socket_blog",
    req=("SRC-SEC-06",),
    archetype="feed",
    url="https://socket.dev/api/blog/feed.atom",
    cadence="hourly",
    hosts=("socket.dev",),
    signal_types=("compromise",),
    ttl_hours=1,
    parser=make_feed_parser("socket_blog", signal_type="compromise", confidence="unconfirmed"),
)

STEPSECURITY_BLOG_SPEC = SourceSpec(
    id="stepsecurity_blog",
    req=("SRC-SEC-07",),
    archetype="feed",
    url="https://www.stepsecurity.io/blog/rss.xml",
    cadence="hourly",
    hosts=("www.stepsecurity.io",),
    signal_types=("compromise",),
    ttl_hours=1,
    parser=make_feed_parser(
        "stepsecurity_blog",
        signal_type="compromise",
        confidence="unconfirmed",
    ),
)

VENDOR_SEC_BLOGS_SPEC = SourceSpec(
    id="vendor_sec_blogs",
    req=("SRC-SEC-08",),
    archetype="feed",
    url=VENDOR_SEC_BLOG_URLS[0],
    cadence="hourly",
    hosts=("securitylabs.datadoghq.com", "www.wiz.io", "snyk.io"),
    signal_types=("compromise",),
    ttl_hours=1,
    params={"urls": VENDOR_SEC_BLOG_URLS},
    parser=parse_vendor_sec_blogs,
    runner=lambda spec, ctx: _run_multi_feed(
        spec,
        ctx,
        VENDOR_SEC_BLOG_URLS,
        signal_type="compromise",
        confidence="unconfirmed",
    ),
)

REGISTRY_SEC_NEWS_SPEC = SourceSpec(
    id="registry_sec_news",
    req=("SRC-SEC-09",),
    archetype="feed",
    url=REGISTRY_SEC_NEWS_URLS[0],
    cadence="daily",
    hosts=("blog.pypi.org", "github.blog"),
    signal_types=("compromise", "technique"),
    ttl_hours=12,
    params={"urls": REGISTRY_SEC_NEWS_URLS},
    parser=parse_registry_sec_news,
    runner=lambda spec, ctx: _run_multi_feed(
        spec,
        ctx,
        REGISTRY_SEC_NEWS_URLS,
        signal_type="compromise",
        confidence="unconfirmed",
    ),
)

HN_COMPROMISE_SPEC = SourceSpec(
    id="hn_compromise",
    req=("SEC-01",),
    archetype="json_api",
    url="https://hn.algolia.com/api/v1/search_by_date",
    cadence="hourly",
    hosts=("hn.algolia.com",),
    signal_types=("compromise",),
    ttl_hours=1,
    params={"keywords": HN_COMPROMISE_KEYWORDS},
    parser=parse_hn_compromise,
    runner=_run_hn_compromise,
)

SECURITY_FEED_SPECS: tuple[SourceSpec, ...] = (
    OSV_BATCH_SPEC,
    GHSA_MALWARE_SPEC,
    CISA_KEV_SPEC,
    SOCKET_BLOG_SPEC,
    STEPSECURITY_BLOG_SPEC,
    VENDOR_SEC_BLOGS_SPEC,
    REGISTRY_SEC_NEWS_SPEC,
    HN_COMPROMISE_SPEC,
)
