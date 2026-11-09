"""Collect Jira issue activity via jira CLI subprocess."""

from __future__ import annotations

import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from standup_tool.config import ConfigError

_COLUMNS = ["KEY", "SUMMARY", "STATUS", "TYPE"]
_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*-[0-9]+$")


def _run_jql(jql: str) -> list[dict]:
    """Run a JQL query via jira CLI and return parsed issue dicts.

    Note: `jira issue list` exits 0 with empty output when an API token lacks
    `/search/jql` scope; `standup-tool verify` probes `/rest/api/3/search/jql`
    explicitly to detect that condition.
    """
    result = subprocess.run(
        [
            "jira",
            "issue",
            "list",
            "--plain",
            "--no-headers",
            "--no-truncate",
            "--columns",
            ",".join(_COLUMNS),
            "--paginate",
            "0:50",
            "--jql",
            jql,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(
            f"jira issue list failed (exit {result.returncode})" + (f": {stderr}" if stderr else "")
        )

    issues = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # Squeeze consecutive tabs — jira CLI tab-pads columns for alignment
        parts = re.split(r"\t+", line)
        if len(parts) < 4:
            continue
        key, summary, status, issue_type = parts[0], parts[1], parts[2], parts[3]
        if not _KEY_RE.match(key):
            continue
        issues.append({"key": key, "summary": summary, "status": status, "type": issue_type})
    return issues


def collect(since_date: date | None = None, project: str = "") -> dict:
    """Return Jira activity across 5 queries.

    Args:
        since_date: Start date for recently_updated query. Defaults to 7 days ago.
        project: Configured Jira project key.

    Returns:
        {
            assigned: list of issues assigned to me,
            recently_updated: list of issues I updated recently,
            in_sprint: list of issues in the current sprint,
            created_by_me: list of issues I created,
            unassigned_pool: list of unassigned project issues,
        }

    Queries 1 (assigned) and 3 (in_sprint) are deduplicated by key.
    """
    if not project:
        raise ConfigError("collectors.jira.project is required")

    if since_date is not None:
        updated_filter = f'updatedDate >= "{since_date.isoformat()}"'
    else:
        updated_filter = "updatedDate >= -7d"

    queries = {
        "assigned": f"project = {project} AND assignee = currentUser() AND statusCategory != Done",
        "recently_updated": (
            f"project = {project} AND {updated_filter} AND assignee = currentUser()"
        ),
        "in_sprint_raw": (
            f"project = {project} AND sprint in openSprints() AND assignee = currentUser()"
        ),
        "created_by_me": (
            f"project = {project} AND reporter = currentUser() AND statusCategory != Done"
        ),
        "unassigned_pool": (
            f"project = {project} AND sprint in openSprints() AND assignee is EMPTY"
            " AND reporter = currentUser() AND statusCategory != Done"
        ),
    }

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {name: executor.submit(_run_jql, jql) for name, jql in queries.items()}
        results = {name: fut.result() for name, fut in futures.items()}

    assigned = results["assigned"]
    in_sprint_raw = results["in_sprint_raw"]

    in_sprint_raw_keys = {i["key"] for i in in_sprint_raw}
    for issue in assigned:
        if issue["key"] in in_sprint_raw_keys:
            issue["in_sprint"] = True

    assigned_keys = {i["key"] for i in assigned}
    in_sprint = [i for i in in_sprint_raw if i["key"] not in assigned_keys]

    return {
        "assigned": assigned,
        "recently_updated": results["recently_updated"],
        "in_sprint": in_sprint,
        "created_by_me": results["created_by_me"],
        "unassigned_pool": results["unassigned_pool"],
    }
