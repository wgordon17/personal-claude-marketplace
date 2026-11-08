"""Collect recent PR activity across workspace repos."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from standup_tool.workspace import RepoInfo

logger = logging.getLogger(__name__)


def _parse_updated_at(updated_at: str) -> datetime:
    return datetime.fromisoformat(updated_at.replace("Z", "+00:00"))


_JSON_FIELDS = (
    "number,title,state,updatedAt,createdAt,url,isDraft,"
    "headRefName,reviewDecision,reviewRequests,statusCheckRollup"
)


def _gh_pr_list(
    repo_slug: str,
    extra_args: list[str],
    repo_path: Path | None = None,
) -> list[dict] | None:
    """Run gh pr list and return parsed JSON items, or None when the repo query fails."""
    target_label = str(repo_path) if repo_path is not None else repo_slug
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repo_slug,
                "--state",
                "all",
                "--json",
                _JSON_FIELDS,
                "--limit",
                "100",
                *extra_args,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    # tolerant: skip single timed-out repository query and continue unless all repositories fail
    except subprocess.TimeoutExpired:
        logger.debug("gh pr list timed out for %s", target_label)
        logger.warning("gh pr list timed out for %s", target_label)
        return None
    if result.returncode != 0:
        logger.debug("gh pr list failed for %s (exit %d)", target_label, result.returncode)
        logger.warning("gh pr list failed for %s (exit %d)", target_label, result.returncode)
        return None
    try:
        return json.loads(result.stdout)
    # tolerant: skip repository with malformed gh JSON output and continue unless all repos fail
    except json.JSONDecodeError:
        logger.debug("gh pr list returned invalid JSON for %s", target_label)
        logger.warning("gh pr list returned invalid JSON for %s", target_label)
        return None


def _summarize_checks(checks_rollup: list) -> str:
    if not checks_rollup:
        return "NONE"
    failed = any(
        c.get("conclusion") in ("FAILURE", "ERROR", "TIMED_OUT", "CANCELLED")
        or c.get("state") in ("FAILURE", "ERROR")
        for c in checks_rollup
        if isinstance(c, dict)
    )
    pending = any(
        (c.get("conclusion") is None and c.get("status") not in (None, "COMPLETED"))
        or c.get("state") in ("PENDING", "EXPECTED")
        for c in checks_rollup
        if isinstance(c, dict)
    )
    return "FAILURE" if failed else ("PENDING" if pending else "SUCCESS")


def _build_pr_dict(item: dict, repo_name: str, role: str) -> dict:
    review_requests = item.get("reviewRequests") or []
    reviewer_logins = [
        r.get("login") or r.get("slug") or r.get("name", "")
        for r in review_requests
        if isinstance(r, dict) and (r.get("login") or r.get("slug") or r.get("name"))
    ]
    return {
        "repo": repo_name,
        "pr_number": item.get("number"),
        "title": item.get("title", ""),
        "state": item.get("state", ""),
        "updated_at": item.get("updatedAt", ""),
        "created_at": item.get("createdAt", ""),
        "url": item.get("url", ""),
        "is_draft": item.get("isDraft", False),
        "head_branch": item.get("headRefName", ""),
        "review_decision": item.get("reviewDecision") or "",
        "review_requests": reviewer_logins,
        "checks": _summarize_checks(item.get("statusCheckRollup") or []),
        "role": role,
    }


def collect(
    repos: list[RepoInfo],
    since_date: date,
    until_date: date,
) -> list[dict]:
    """Return list of PR dicts: all open PRs plus closed PRs updated in the date range.

    Open PRs are always included regardless of updatedAt — they represent active
    deliverables, review obligations, or in-flight work. Closed PRs are only
    included if updated within the date window.

    Runs three queries per repo:
    1. --author @me — PRs you authored
    2. --search "review-requested:@me" — PRs awaiting your review
    3. --search "reviewed-by:@me" — PRs you've already reviewed

    Each dict: {repo, pr_number, title, state, updated_at, created_at, url,
    is_draft, head_branch, review_decision, review_requests, checks, role}
    """
    since_dt = datetime(since_date.year, since_date.month, since_date.day, tzinfo=UTC)
    until_dt = datetime(until_date.year, until_date.month, until_date.day, 23, 59, 59, tzinfo=UTC)
    prs: list[dict] = []
    seen_urls: set[str] = set()

    queries = [
        (["--author", "@me"], "author"),
        (["--search", "review-requested:@me"], "review_requested"),
        (["--search", "reviewed-by:@me"], "reviewer"),
    ]

    eligible_repos = 0
    failed_repos = 0

    for repo in repos:
        if not repo.upstream_slug:
            sys.stderr.write(f"WARNING: skipping PR collection for {repo.path} (no remote slug)\n")
            continue
        eligible_repos += 1
        repo_failed = False
        for extra_args, role in queries:
            items = _gh_pr_list(repo.upstream_slug, extra_args, repo_path=repo.path)
            if items is None:
                repo_failed = True
                break
            for item in items:
                state = item.get("state", "")
                is_open = state in ("OPEN", "open")

                if not is_open:
                    updated_at = item.get("updatedAt", "")
                    if not updated_at:
                        continue
                    try:
                        updated_dt = _parse_updated_at(updated_at)
                    # tolerant: skip closed PR with malformed updatedAt timestamp
                    except ValueError:
                        logger.debug(
                            "Skipping PR %s in %s with invalid updatedAt %r",
                            item.get("number"),
                            repo.name,
                            updated_at,
                        )
                        continue
                    if not (since_dt <= updated_dt <= until_dt):
                        continue

                url = item.get("url", "")
                if url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                prs.append(_build_pr_dict(item, repo.name, role))
        if repo_failed:
            failed_repos += 1

    if eligible_repos > 0 and failed_repos == eligible_repos:
        raise RuntimeError(f"gh pr list failed across all {eligible_repos} configured repositories")

    return prs
