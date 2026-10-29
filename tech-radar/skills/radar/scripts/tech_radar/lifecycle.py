"""Lifecycle signals and alerts for used and watchlist entities (Spec 07 §3, Spec 12 A3/A22)."""

from __future__ import annotations

import base64
import binascii
import json
import re
import statistics
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from . import db, security
from .errors import RadarError
from .sources.base import RawSignal, SourceResult, normalize_timestamp, persist_raw_signal

if TYPE_CHECKING:
    from .context import Ctx

MAINTAINER_LEAVING_RE = re.compile(
    r"(?:unmaintained|looking for (?:new )?maintainers"
    r"|no longer maintained|deprecated in favou?r of)",
    re.IGNORECASE,
)

ACQUISITION_SUNSET_RE = re.compile(
    r"\b(?:acquired|sunset|shutting down|end of life)\b",
    re.IGNORECASE,
)


def _parse_iso_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    txt = str(raw).strip()
    if not txt:
        return None
    if len(txt) == 10 and txt[4] == "-" and txt[7] == "-":
        txt = f"{txt}T00:00:00Z"
    try:
        dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def _emit_lifecycle_alert(
    ctx: Ctx,
    *,
    rule_id: str,
    tier: str,
    entity_id: str,
    title: str,
    excerpt: str,
    confidence: str = "confirmed",
    source_id: str = "lifecycle",
    url: str | None = None,
    project_key: str | None = None,
    extra_payload: Mapping[str, Any] | None = None,
) -> int | None:
    payload: dict[str, Any] = {
        "check": rule_id,
        "entity_id": entity_id,
    }
    if extra_payload:
        payload.update(extra_payload)

    dedupe = f"lifecycle|{entity_id}|{rule_id}"
    sig_id = persist_raw_signal(
        ctx,
        RawSignal(
            source_id=source_id,
            type="lifecycle",
            external_id=f"{rule_id}:{entity_id}",
            title=title,
            url=url,
            published_at=ctx.now_iso,
            untrusted_excerpt=excerpt,
            entity_hint=entity_id,
            confidence=confidence,
            payload=payload,
            dedupe_key=dedupe,
        ),
        fetched_at=ctx.now_iso,
    )
    aid, is_new = security.upsert_alert(
        ctx,
        tier=tier,
        kind="lifecycle",
        confidence=confidence,
        entity_id=entity_id,
        signal_id=sig_id,
        project_key=project_key,
        dedupe_key=dedupe,
        payload=payload,
    )
    return aid if is_new else None


def _renovate_suppresses_npm_deprecation(ctx: Ctx, entity_id: str) -> bool:
    """Return True if every project using `entity_id` has renovate.json enabling npm deprecation."""
    rows = ctx.db.execute(
        "SELECT p.renovate_json FROM usages u "
        "JOIN projects p ON u.context = 'project:' || p.project_key "
        "WHERE u.entity_id = ?",
        (entity_id,),
    ).fetchall()
    if not rows:
        return False

    for row in rows:
        raw_ren = row["renovate_json"]
        if not raw_ren:
            return False
        try:
            cfg = json.loads(str(raw_ren))
        except json.JSONDecodeError:
            return False
        if not isinstance(cfg, Mapping):
            return False
        enabled = False
        if cfg.get("npmDeprecations") is True or cfg.get("deprecationWarnings") is True:
            enabled = True
        if cfg.get("replacementRules") is True:
            enabled = True
        extends = cfg.get("extends")
        if isinstance(extends, list):
            ext_strs = [str(x).lower() for x in extends]
            if any(
                "deprecation" in x or "replacements" in x or "workarounds:all" in x
                for x in ext_strs
            ):
                enabled = True
        pkg_rules = cfg.get("packageRules")
        if isinstance(pkg_rules, list):
            for pr in pkg_rules:
                if isinstance(pr, Mapping) and (
                    pr.get("npmDeprecations") is True
                    or pr.get("replacementName")
                    or pr.get("deprecations") is True
                ):
                    enabled = True
        if not enabled:
            return False
    return True


def _covered_entities(ctx: Ctx) -> dict[str, dict[str, Any]]:
    """Return public used + `interest:watchlist` entities (Spec 12 A22)."""
    rows = ctx.db.execute(
        "SELECT e.entity_id, e.kind, e.ecosystem, e.name, e.repo, e.visibility, "
        "u.context, u.version, u.direct "
        "FROM entities e JOIN usages u ON u.entity_id = e.entity_id "
        "WHERE e.visibility = 'public' "
        "AND (u.context NOT LIKE 'interest:%' OR u.context = 'interest:watchlist')"
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        eid = str(r["entity_id"])
        rec = out.get(eid)
        if rec is None:
            repo = str(r["repo"] or "").strip().lower()
            if not repo and eid.startswith("gh:"):
                repo = eid[len("gh:") :].strip().lower()
            out[eid] = {
                "entity_id": eid,
                "kind": str(r["kind"]),
                "ecosystem": str(r["ecosystem"] or ""),
                "name": str(r["name"] or ""),
                "repo": repo or None,
                "versions": [str(r["version"])] if r["version"] else [],
                "contexts": [str(r["context"])],
                "direct": int(r["direct"] or 0),
            }
        else:
            if r["version"] and str(r["version"]) not in rec["versions"]:
                rec["versions"].append(str(r["version"]))
            if str(r["context"]) not in rec["contexts"]:
                rec["contexts"].append(str(r["context"]))
            rec["direct"] = max(int(rec["direct"]), int(r["direct"] or 0))
    return out


def _check_github_extra_lifecycle(
    ctx: Ctx,
    eid: str,
    repo: str,
    meta_payload: Mapping[str, Any],
) -> list[int]:
    """Run LIFE-06, LIFE-08, LIFE-09, LIFE-12, and LIFE-14 GitHub checks."""
    new_ids: list[int] = []
    upstream_stars = int(meta_payload.get("stargazers_count") or 0)

    # LIFE-06: default branch commit date via `repos/{o}/{r}/commits?per_page=1` or `pushed_at`
    last_commit_iso = str(meta_payload.get("pushed_at") or "").strip() or None
    try:
        c_res = ctx.gh.api(f"repos/{repo}/commits?per_page=1")
        if c_res.returncode == 0 and c_res.stdout.strip():
            c_arr = json.loads(c_res.stdout)
            if isinstance(c_arr, list) and c_arr and isinstance(c_arr[0], Mapping):
                commit_obj = c_arr[0].get("commit")
                if isinstance(commit_obj, Mapping):
                    committer = commit_obj.get("committer") or commit_obj.get("author")
                    if isinstance(committer, Mapping) and committer.get("date"):
                        last_commit_iso = normalize_timestamp(committer["date"])
    except (RadarError, json.JSONDecodeError):
        pass

    commit_dt = _parse_iso_dt(last_commit_iso)
    if commit_dt is not None and (ctx.now - commit_dt) > timedelta(days=180):
        aid = _emit_lifecycle_alert(
            ctx,
            rule_id="LIFE-06",
            tier="P2",
            entity_id=eid,
            title=f"No commits in 6+ months on {repo}",
            excerpt=f"Last commit on {repo} was {last_commit_iso}",
            source_id="gh_repo_meta",
            url=f"https://github.com/{repo}",
            extra_payload={"last_commit_at": last_commit_iso},
        )
        if aid is not None:
            new_ids.append(aid)

    # LIFE-08: issue/PR response collapse (`search/issues` sample of 30, median first comment > 30d)
    since_90 = (ctx.now - timedelta(days=90)).strftime("%Y-%m-%d")
    try:
        iss_res = ctx.gh(
            [
                "api",
                "search/issues",
                "-f",
                f"q=repo:{repo} is:issue created:>{since_90}",
                "-f",
                "per_page=30",
            ]
        )
        if iss_res.returncode == 0 and iss_res.stdout.strip():
            iss_obj = json.loads(iss_res.stdout)
            items = iss_obj.get("items") if isinstance(iss_obj, Mapping) else None
            if isinstance(items, list) and items:
                response_days: list[float] = []
                for issue in items[:30]:
                    if not isinstance(issue, Mapping):
                        continue
                    created_dt = _parse_iso_dt(str(issue.get("created_at") or ""))
                    if created_dt is None:
                        continue
                    first_comment_iso = issue.get("first_comment_at")
                    if (
                        not first_comment_iso
                        and issue.get("number")
                        and int(issue.get("comments") or 0) > 0
                    ):
                        num = int(issue["number"])
                        try:
                            cm_res = ctx.gh.api(f"repos/{repo}/issues/{num}/comments?per_page=1")
                            if cm_res.returncode == 0 and cm_res.stdout.strip():
                                cm_arr = json.loads(cm_res.stdout)
                                if (
                                    isinstance(cm_arr, list)
                                    and cm_arr
                                    and isinstance(cm_arr[0], Mapping)
                                ):
                                    first_comment_iso = cm_arr[0].get("created_at")
                        except (RadarError, json.JSONDecodeError):
                            pass
                    first_dt = _parse_iso_dt(str(first_comment_iso or ""))
                    if first_dt is not None:
                        response_days.append(
                            max(0.0, (first_dt - created_dt).total_seconds() / 86400.0)
                        )
                    else:
                        age_days = (ctx.now - created_dt).total_seconds() / 86400.0
                        if age_days > 30.0:
                            response_days.append(age_days)

                if response_days and statistics.median(response_days) > 30.0:
                    med = round(statistics.median(response_days), 1)
                    aid = _emit_lifecycle_alert(
                        ctx,
                        rule_id="LIFE-08",
                        tier="P2",
                        entity_id=eid,
                        title=f"Issue response time collapse on {repo} (median {med}d)",
                        excerpt=(
                            f"Median time-to-first-comment across {len(response_days)} "
                            f"issues is {med} days."
                        ),
                        source_id="gh_repo_meta",
                        url=f"https://github.com/{repo}/issues",
                        extra_payload={"median_response_days": med},
                    )
                    if aid is not None:
                        new_ids.append(aid)
    except (RadarError, json.JSONDecodeError):
        pass

    # LIFE-09: maintainer-leaving notice in README / description
    readme_texts: list[str] = []
    if meta_payload.get("readme_text"):
        readme_texts.append(str(meta_payload["readme_text"]))
    try:
        rm_res = ctx.gh.api(f"repos/{repo}/readme")
        if rm_res.returncode == 0 and rm_res.stdout.strip():
            rm_obj = json.loads(rm_res.stdout)
            if isinstance(rm_obj, Mapping):
                raw_c = str(rm_obj.get("content") or "")
                if rm_obj.get("encoding") == "base64" and raw_c:
                    try:
                        readme_texts.append(
                            base64.b64decode(raw_c).decode("utf-8", errors="ignore")
                        )
                    except (binascii.Error, ValueError):
                        readme_texts.append(raw_c)
                elif raw_c:
                    readme_texts.append(raw_c)
    except (RadarError, json.JSONDecodeError):
        pass

    for r_txt in readme_texts:
        m = MAINTAINER_LEAVING_RE.search(r_txt)
        if m:
            aid = _emit_lifecycle_alert(
                ctx,
                rule_id="LIFE-09",
                tier="P1",
                entity_id=eid,
                title=f"Maintainer-leaving notice on {repo}: '{m.group(0)}'",
                excerpt=r_txt[:300],
                source_id="gh_repo_meta",
                url=f"https://github.com/{repo}",
                extra_payload={"matched_phrase": m.group(0)},
            )
            if aid is not None:
                new_ids.append(aid)
            break

    # LIFE-12: community fork overtaking (stars > 0.5 * upstream and more commits in 90d)
    try:
        fk_res = ctx.gh.api(f"repos/{repo}/forks?sort=stargazers&per_page=5")
        if fk_res.returncode == 0 and fk_res.stdout.strip():
            fk_arr = json.loads(fk_res.stdout)
            if isinstance(fk_arr, list):
                upstream_commits_90d = int(meta_payload.get("commits_90d") or 0)
                for fk in fk_arr:
                    if not isinstance(fk, Mapping):
                        continue
                    fk_stars = int(fk.get("stargazers_count") or 0)
                    fk_slug = str(fk.get("full_name") or "").strip()
                    fk_commits_90d = int(fk.get("commits_90d") or 0)
                    fk_pushed = _parse_iso_dt(str(fk.get("pushed_at") or ""))
                    more_active = fk_commits_90d > upstream_commits_90d or (
                        fk_pushed is not None
                        and commit_dt is not None
                        and fk_pushed > commit_dt
                        and (ctx.now - fk_pushed) <= timedelta(days=90)
                    )
                    if upstream_stars > 0 and fk_stars > 0.5 * upstream_stars and more_active:
                        aid = _emit_lifecycle_alert(
                            ctx,
                            rule_id="LIFE-12",
                            tier="P2",
                            entity_id=eid,
                            title=f"Community fork {fk_slug} overtaking {repo}",
                            excerpt=(
                                f"Fork {fk_slug} has {fk_stars} stars vs "
                                f"{upstream_stars} upstream and higher recent activity."
                            ),
                            source_id="gh_repo_meta",
                            url=f"https://github.com/{fk_slug}",
                            extra_payload={
                                "fork": fk_slug,
                                "fork_stars": fk_stars,
                                "upstream_stars": upstream_stars,
                            },
                        )
                        if aid is not None:
                            new_ids.append(aid)
                        break
    except (RadarError, json.JSONDecodeError):
        pass

    # LIFE-14: top maintainer inactive 90d
    try:
        ct_res = ctx.gh.api(f"repos/{repo}/contributors?per_page=1")
        if ct_res.returncode == 0 and ct_res.stdout.strip():
            ct_arr = json.loads(ct_res.stdout)
            if isinstance(ct_arr, list) and ct_arr and isinstance(ct_arr[0], Mapping):
                top_c = ct_arr[0]
                top_login = str(top_c.get("login") or "").strip()
                last_c_iso = normalize_timestamp(top_c.get("last_commit_at"))
                if not last_c_iso and top_login:
                    try:
                        ac_res = ctx.gh.api(f"repos/{repo}/commits?author={top_login}&per_page=1")
                        if ac_res.returncode == 0 and ac_res.stdout.strip():
                            ac_arr = json.loads(ac_res.stdout)
                            if (
                                isinstance(ac_arr, list)
                                and ac_arr
                                and isinstance(ac_arr[0], Mapping)
                            ):
                                c_inner = ac_arr[0].get("commit")
                                if isinstance(c_inner, Mapping):
                                    auth_obj = c_inner.get("author") or c_inner.get("committer")
                                    if isinstance(auth_obj, Mapping):
                                        last_c_iso = normalize_timestamp(auth_obj.get("date"))
                    except (RadarError, json.JSONDecodeError):
                        pass
                last_c_dt = _parse_iso_dt(last_c_iso)
                if last_c_dt is not None and (ctx.now - last_c_dt) > timedelta(days=90):
                    aid = _emit_lifecycle_alert(
                        ctx,
                        rule_id="LIFE-14",
                        tier="P3",
                        entity_id=eid,
                        title=f"Top maintainer {top_login} inactive 90+ days on {repo}",
                        excerpt=f"Top contributor {top_login} last committed on {last_c_iso}.",
                        source_id="gh_repo_meta",
                        url=f"https://github.com/{repo}",
                        extra_payload={
                            "top_maintainer": top_login,
                            "last_commit_at": last_c_iso,
                        },
                    )
                    if aid is not None:
                        new_ids.append(aid)
    except (RadarError, json.JSONDecodeError):
        pass

    return new_ids


def _check_brew_deprecation(
    ctx: Ctx,
    covered: Mapping[str, dict[str, Any]],
) -> list[int]:
    """Check LIFE-13: Homebrew formula deprecated or disabled via brew info."""
    brew_entities = {
        rec["name"].lower(): eid for eid, rec in covered.items() if eid.startswith("brew:formula/")
    }
    if not brew_entities:
        return []

    try:
        res = ctx.procs.run(["brew", "info", "--json=v2", "--installed"])
    except RadarError:
        return []
    if res.returncode != 0 or not res.stdout.strip():
        return []

    try:
        obj = json.loads(res.stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(obj, Mapping):
        return []

    new_ids: list[int] = []
    for formula in obj.get("formulae") or []:
        if not isinstance(formula, Mapping):
            continue
        fname = str(formula.get("name") or "").strip().lower()
        eid = brew_entities.get(fname)
        if not eid:
            continue
        is_dep = bool(formula.get("deprecated") is True)
        is_dis = bool(formula.get("disabled") is True)
        if is_dep or is_dis:
            status_word = "disabled" if is_dis else "deprecated"
            reason = str(
                formula.get("disable_reason") or formula.get("deprecation_reason") or status_word
            )
            aid = _emit_lifecycle_alert(
                ctx,
                rule_id="LIFE-13",
                tier="P1",
                entity_id=eid,
                title=f"Homebrew formula {fname} is {status_word} ({reason})",
                excerpt=f"Homebrew formula {fname} marked {status_word}: {reason}",
                source_id="brew_info",
                extra_payload={"status": status_word, "reason": reason},
            )
            if aid is not None:
                new_ids.append(aid)
    return new_ids


def evaluate(
    ctx: Ctx,
    results: Sequence[SourceResult] = (),
) -> list[int]:
    """Evaluate Spec 07 §3 lifecycle checks over `results` and covered entities."""
    covered = _covered_entities(ctx)
    repo_to_eid: dict[str, str] = {}
    name_to_eid: dict[str, str] = {}
    for eid, rec in covered.items():
        if rec.get("repo"):
            repo_to_eid[str(rec["repo"]).lower()] = eid
        if rec.get("name"):
            name_to_eid[str(rec["name"]).lower()] = eid

    all_signals: list[RawSignal] = []
    for res in results:
        for sig in res.signals:
            persist_raw_signal(ctx, sig, fetched_at=ctx.now_iso)
            all_signals.append(sig)

    new_alert_ids: list[int] = []
    releases_by_entity: dict[str, list[RawSignal]] = {}

    for sig in all_signals:
        if sig.source_id == "gh_releases" and sig.entity_hint:
            target = sig.entity_hint
            if target not in covered and target.startswith("gh:"):
                target = repo_to_eid.get(target[len("gh:") :].lower(), target)
            releases_by_entity.setdefault(target, []).append(sig)

    for sig in all_signals:
        eid = sig.entity_hint
        if eid and eid not in covered and eid.startswith("gh:"):
            eid = repo_to_eid.get(eid[len("gh:") :].lower(), eid)

        payload = sig.payload

        # LIFE-01, LIFE-06, LIFE-08, LIFE-09, LIFE-12, LIFE-14 via gh_repo_meta
        if sig.source_id == "gh_repo_meta" and eid and eid in covered:
            repo_slug = str(payload.get("repo") or covered[eid].get("repo") or "").lower()
            if payload.get("archived") is True:
                aid = _emit_lifecycle_alert(
                    ctx,
                    rule_id="LIFE-01",
                    tier="P1",
                    entity_id=eid,
                    title=f"Repository {repo_slug or eid} is archived",
                    excerpt=sig.untrusted_excerpt,
                    source_id="gh_repo_meta",
                    url=sig.url,
                    extra_payload={"repo": repo_slug},
                )
                if aid is not None:
                    new_alert_ids.append(aid)

            if MAINTAINER_LEAVING_RE.search(sig.untrusted_excerpt):
                aid = _emit_lifecycle_alert(
                    ctx,
                    rule_id="LIFE-09",
                    tier="P1",
                    entity_id=eid,
                    title=f"Maintainer-leaving notice on {repo_slug or eid}",
                    excerpt=sig.untrusted_excerpt,
                    source_id="gh_repo_meta",
                    url=sig.url,
                )
                if aid is not None:
                    new_alert_ids.append(aid)

            if repo_slug:
                new_alert_ids.extend(_check_github_extra_lifecycle(ctx, eid, repo_slug, payload))

        # LIFE-02: npm deprecated (with INTEG-01 Renovate suppression)
        elif sig.source_id == "npm_registry_meta" and eid and eid in covered:
            dep_versions = payload.get("deprecated_versions")
            installed_vers = covered[eid]["versions"]
            dep_msg = str(payload.get("deprecated") or "").strip()
            if not dep_msg and isinstance(dep_versions, Mapping):
                for iv in installed_vers:
                    if iv in dep_versions and dep_versions[iv]:
                        dep_msg = str(dep_versions[iv])
                        break
            if dep_msg and not _renovate_suppresses_npm_deprecation(ctx, eid):
                aid = _emit_lifecycle_alert(
                    ctx,
                    rule_id="LIFE-02",
                    tier="P1",
                    entity_id=eid,
                    title=f"npm package {covered[eid]['name']} is deprecated: {dep_msg}",
                    excerpt=dep_msg,
                    source_id="npm_registry_meta",
                    url=sig.url,
                    extra_payload={"deprecated": dep_msg},
                )
                if aid is not None:
                    new_alert_ids.append(aid)

        # LIFE-03 & LIFE-04: PyPI status archived/deprecated/quarantined & yanked pinned release
        elif sig.source_id == "pypi_meta" and eid and eid in covered:
            p_status = str(payload.get("project_status") or "active").strip().lower()
            if p_status in ("archived", "deprecated", "quarantined"):
                tier = "P0" if p_status == "quarantined" else "P1"
                aid = _emit_lifecycle_alert(
                    ctx,
                    rule_id="LIFE-03",
                    tier=tier,
                    entity_id=eid,
                    title=f"PyPI package {covered[eid]['name']} status is {p_status}",
                    excerpt=f"PEP 792 project-status is '{p_status}'",
                    source_id="pypi_meta",
                    url=sig.url,
                    extra_payload={"project_status": p_status},
                )
                if aid is not None:
                    new_alert_ids.append(aid)

            yanked_map = payload.get("yanked_versions")
            if isinstance(yanked_map, Mapping) and yanked_map:
                installed_vers = covered[eid]["versions"]
                for iv in installed_vers:
                    if iv in yanked_map:
                        reason = str(yanked_map[iv])
                        aid = _emit_lifecycle_alert(
                            ctx,
                            rule_id="LIFE-04",
                            tier="P1",
                            entity_id=eid,
                            title=f"Pinned PyPI release {covered[eid]['name']}=={iv} was yanked",
                            excerpt=f"Release {iv} yanked: {reason}",
                            source_id="pypi_meta",
                            url=sig.url,
                            extra_payload={"version": iv, "yanked_reason": reason},
                        )
                        if aid is not None:
                            new_alert_ids.append(aid)

        # LIFE-05: endoflife cycle eol <= 90 days away or passed
        elif sig.source_id == "endoflife" and eid and eid in covered:
            cycles = payload.get("cycles")
            installed_vers = covered[eid]["versions"]
            if isinstance(cycles, list):
                for cyc in cycles:
                    if not isinstance(cyc, Mapping):
                        continue
                    c_name = str(cyc.get("cycle") or "").strip()
                    if not c_name:
                        continue
                    matches_installed = not installed_vers or any(
                        iv == c_name or iv.startswith(f"{c_name}.") for iv in installed_vers
                    )
                    if not matches_installed:
                        continue
                    is_eol = bool(cyc.get("is_eol") is True)
                    eol_str = str(cyc.get("eol") or "").strip()
                    eol_dt = _parse_iso_dt(eol_str)
                    within_90d = eol_dt is not None and (eol_dt - ctx.now) <= timedelta(days=90)
                    if is_eol or within_90d:
                        aid = _emit_lifecycle_alert(
                            ctx,
                            rule_id="LIFE-05",
                            tier="P1",
                            entity_id=eid,
                            title=f"{covered[eid]['name']} {c_name} EOL ({eol_str or 'passed'})",
                            excerpt=(
                                f"Cycle {c_name} reaches end-of-life on {eol_str or 'already EOL'}."
                            ),
                            source_id="endoflife",
                            url=sig.url,
                            extra_payload={"cycle": c_name, "eol": eol_str},
                        )
                        if aid is not None:
                            new_alert_ids.append(aid)
                        break

        # LIFE-11: acquisition / shutdown / sunset in discussions/blog/news signals
        combined_text = f"{sig.title}\n{sig.untrusted_excerpt}"
        if ACQUISITION_SUNSET_RE.search(combined_text):
            matched_eids: list[str] = []
            if eid and eid in covered:
                matched_eids.append(eid)
            else:
                for cand_name, cand_eid in name_to_eid.items():
                    if len(cand_name) >= 3 and re.search(
                        rf"\b{re.escape(cand_name)}\b", combined_text, re.IGNORECASE
                    ):
                        matched_eids.append(cand_eid)
            for m_eid in matched_eids:
                aid = _emit_lifecycle_alert(
                    ctx,
                    rule_id="LIFE-11",
                    tier="P2",
                    entity_id=m_eid,
                    title=f"Possible acquisition/sunset notice mentioning {covered[m_eid]['name']}",
                    excerpt=sig.untrusted_excerpt or sig.title,
                    confidence="unconfirmed",
                    source_id=sig.source_id,
                    url=sig.url,
                )
                if aid is not None:
                    new_alert_ids.append(aid)

    # LIFE-07: no release in 12 months (365 days) for entities with releases
    for eid, rel_sigs in releases_by_entity.items():
        if eid not in covered or not rel_sigs:
            continue
        rel_dates = [
            dt for dt in (_parse_iso_dt(s.published_at) for s in rel_sigs) if dt is not None
        ]
        if rel_dates:
            newest_rel = max(rel_dates)
            if (ctx.now - newest_rel) > timedelta(days=365):
                aid = _emit_lifecycle_alert(
                    ctx,
                    rule_id="LIFE-07",
                    tier="P2",
                    entity_id=eid,
                    title=f"No release in 12+ months for {covered[eid]['name']}",
                    excerpt=f"Latest release was published on {newest_rel.strftime('%Y-%m-%d')}.",
                    source_id="gh_releases",
                    url=rel_sigs[0].url,
                    extra_payload={"latest_release_at": newest_rel.strftime("%Y-%m-%dT%H:%M:%SZ")},
                )
                if aid is not None:
                    new_alert_ids.append(aid)

    # LIFE-13: Homebrew formula deprecated/disabled
    new_alert_ids.extend(_check_brew_deprecation(ctx, covered))

    if security.is_baseline_collect(ctx) and results:
        db.kv_set(ctx.db, "baseline_done", ctx.now_iso)

    return new_alert_ids
