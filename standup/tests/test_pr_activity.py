"""Tests for pr_activity.py."""

import json
import subprocess
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from standup_tool.collectors.pr_activity import collect
from standup_tool.workspace import RepoInfo


def make_repo(name: str = "test-repo", slug: str | None = None) -> RepoInfo:
    effective_slug = f"acme/{name}" if slug is None else slug
    return RepoInfo(name=name, path=Path(f"/fake/{name}"), upstream_slug=effective_slug)


def fake_gh_run(items: list[dict]):
    def _run(cmd, **kwargs):
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = json.dumps(items)
        return mock

    return _run


def test_collect_returns_empty_for_no_repos():
    result = collect([], date(2026, 4, 20), date(2026, 4, 23))
    assert result == []


def test_skips_repo_without_slug(capsys):
    repo_no_slug = make_repo("local-only", slug="")
    with patch("subprocess.run") as mock_run:
        result = collect([repo_no_slug], date(2026, 4, 20), date(2026, 4, 23))

    assert result == []
    mock_run.assert_not_called()
    captured = capsys.readouterr()
    assert "/fake/local-only" in captured.err


def test_collect_returns_pr_within_date_range():
    items = [
        {
            "number": 42,
            "title": "feat: add thing",
            "state": "OPEN",
            "updatedAt": "2026-04-22T10:00:00Z",
            "createdAt": "2026-04-20T08:00:00Z",
            "url": "https://github.com/acme/test-repo/pull/42",
            "isDraft": False,
            "headRefName": "feat/add-thing",
            "reviewDecision": "REVIEW_REQUIRED",
            "reviewRequests": [{"login": "bo-sample"}],
            "statusCheckRollup": [
                {"name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ],
        }
    ]
    with patch("subprocess.run", side_effect=fake_gh_run(items)):
        result = collect([make_repo()], date(2026, 4, 20), date(2026, 4, 23))

    assert len(result) == 1
    assert result[0]["pr_number"] == 42
    assert result[0]["title"] == "feat: add thing"
    assert result[0]["state"] == "OPEN"
    assert result[0]["is_draft"] is False
    assert result[0]["repo"] == "test-repo"
    assert result[0]["head_branch"] == "feat/add-thing"
    assert result[0]["created_at"] == "2026-04-20T08:00:00Z"
    assert result[0]["review_decision"] == "REVIEW_REQUIRED"
    assert result[0]["review_requests"] == ["bo-sample"]
    assert result[0]["checks"] == "SUCCESS"
    assert result[0]["role"] == "author"


def test_collect_filters_pr_outside_date_range():
    items = [
        {
            "number": 1,
            "title": "old PR",
            "state": "MERGED",
            "updatedAt": "2026-04-10T10:00:00Z",
            "url": "https://github.com/acme/test-repo/pull/1",
            "isDraft": False,
        }
    ]
    with patch("subprocess.run", side_effect=fake_gh_run(items)):
        result = collect([make_repo()], date(2026, 4, 20), date(2026, 4, 23))

    assert result == []


def test_collect_includes_pr_at_since_boundary():
    items = [
        {
            "number": 10,
            "title": "boundary since",
            "state": "OPEN",
            "updatedAt": "2026-04-20T00:00:00Z",
            "url": "https://github.com/acme/test-repo/pull/10",
            "isDraft": False,
        }
    ]
    with patch("subprocess.run", side_effect=fake_gh_run(items)):
        result = collect([make_repo()], date(2026, 4, 20), date(2026, 4, 23))

    assert len(result) == 1
    assert result[0]["pr_number"] == 10


def test_collect_includes_pr_at_until_boundary():
    items = [
        {
            "number": 11,
            "title": "boundary until",
            "state": "OPEN",
            "updatedAt": "2026-04-23T23:59:59Z",
            "url": "https://github.com/acme/test-repo/pull/11",
            "isDraft": False,
        }
    ]
    with patch("subprocess.run", side_effect=fake_gh_run(items)):
        result = collect([make_repo()], date(2026, 4, 20), date(2026, 4, 23))

    assert len(result) == 1
    assert result[0]["pr_number"] == 11


def test_collect_excludes_closed_pr_after_until():
    items = [
        {
            "number": 13,
            "title": "closed after until",
            "state": "MERGED",
            "updatedAt": "2026-04-24T00:00:00Z",
            "url": "https://github.com/acme/test-repo/pull/13",
            "isDraft": False,
        }
    ]
    with patch("subprocess.run", side_effect=fake_gh_run(items)):
        result = collect([make_repo()], date(2026, 4, 20), date(2026, 4, 23))

    assert result == []


def test_collect_includes_open_pr_outside_date_range():
    items = [
        {
            "number": 14,
            "title": "open but old",
            "state": "OPEN",
            "updatedAt": "2026-04-10T00:00:00Z",
            "url": "https://github.com/acme/test-repo/pull/14",
            "isDraft": False,
        }
    ]
    with patch("subprocess.run", side_effect=fake_gh_run(items)):
        result = collect([make_repo()], date(2026, 4, 20), date(2026, 4, 23))

    assert len(result) == 1
    assert result[0]["pr_number"] == 14


def test_collect_skips_repo_on_timeout():
    good_items = [
        {
            "number": 50,
            "title": "good PR",
            "state": "OPEN",
            "updatedAt": "2026-04-22T10:00:00Z",
            "url": "https://github.com/acme/repo-b/pull/50",
            "isDraft": False,
        }
    ]

    def fake_run(cmd, **kwargs):
        if "acme/repo-a" in cmd:
            raise subprocess.TimeoutExpired(cmd, 30)
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = json.dumps(good_items)
        return mock

    repos = [make_repo("repo-a"), make_repo("repo-b")]
    with patch("subprocess.run", side_effect=fake_run):
        result = collect(repos, date(2026, 4, 20), date(2026, 4, 23))

    assert len(result) == 1
    assert result[0]["repo"] == "repo-b"


def test_collect_skips_repo_on_gh_error():
    def fake_run(cmd, **kwargs):
        mock = MagicMock()
        mock.returncode = 1
        mock.stdout = ""
        return mock

    with patch("subprocess.run", side_effect=fake_run):
        result = collect([make_repo()], date(2026, 4, 20), date(2026, 4, 23))

    assert result == []


def test_collect_skips_invalid_json():
    def fake_run(cmd, **kwargs):
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "not valid json"
        return mock

    with patch("subprocess.run", side_effect=fake_run):
        result = collect([make_repo()], date(2026, 4, 20), date(2026, 4, 23))

    assert result == []


def test_collect_includes_draft_pr():
    items = [
        {
            "number": 7,
            "title": "WIP: draft",
            "state": "OPEN",
            "updatedAt": "2026-04-21T09:00:00Z",
            "url": "https://github.com/acme/test-repo/pull/7",
            "isDraft": True,
        }
    ]
    with patch("subprocess.run", side_effect=fake_gh_run(items)):
        result = collect([make_repo()], date(2026, 4, 20), date(2026, 4, 23))

    assert len(result) == 1
    assert result[0]["is_draft"] is True


def test_collect_runs_three_query_types():
    captured_cmds = []

    def capturing_run(cmd, **kwargs):
        captured_cmds.append(list(cmd))
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "[]"
        return mock

    with patch("subprocess.run", side_effect=capturing_run):
        collect([make_repo()], date(2026, 4, 20), date(2026, 4, 23))

    assert len(captured_cmds) == 3
    assert any("--author" in cmd and "@me" in cmd for cmd in captured_cmds)
    assert any("review-requested:@me" in str(cmd) for cmd in captured_cmds)
    assert any("reviewed-by:@me" in str(cmd) for cmd in captured_cmds)


def test_collect_checks_summary_failure():
    items = [
        {
            "number": 50,
            "title": "ACME-999: failing PR",
            "state": "OPEN",
            "updatedAt": "2026-04-22T10:00:00Z",
            "url": "https://github.com/acme/test-repo/pull/50",
            "isDraft": False,
            "headRefName": "worktree-ACME-999-test",
            "statusCheckRollup": [
                {"name": "unit", "status": "COMPLETED", "conclusion": "SUCCESS"},
                {"name": "integration", "status": "COMPLETED", "conclusion": "FAILURE"},
            ],
        }
    ]
    with patch("subprocess.run", side_effect=fake_gh_run(items)):
        result = collect([make_repo()], date(2026, 4, 20), date(2026, 4, 23))

    assert result[0]["checks"] == "FAILURE"


def test_collect_checks_summary_pending():
    items = [
        {
            "number": 51,
            "title": "pending PR",
            "state": "OPEN",
            "updatedAt": "2026-04-22T10:00:00Z",
            "url": "https://github.com/acme/test-repo/pull/51",
            "isDraft": False,
            "headRefName": "feat/pending",
            "statusCheckRollup": [
                {"name": "ci", "status": "IN_PROGRESS", "conclusion": None},
            ],
        }
    ]
    with patch("subprocess.run", side_effect=fake_gh_run(items)):
        result = collect([make_repo()], date(2026, 4, 20), date(2026, 4, 23))

    assert result[0]["checks"] == "PENDING"


def test_collect_checks_summary_none_when_empty():
    items = [
        {
            "number": 52,
            "title": "no checks PR",
            "state": "OPEN",
            "updatedAt": "2026-04-22T10:00:00Z",
            "url": "https://github.com/acme/test-repo/pull/52",
            "isDraft": False,
            "headRefName": "feat/no-checks",
            "statusCheckRollup": [],
        }
    ]
    with patch("subprocess.run", side_effect=fake_gh_run(items)):
        result = collect([make_repo()], date(2026, 4, 20), date(2026, 4, 23))

    assert result[0]["checks"] == "NONE"


def test_collect_defaults_for_missing_review_fields():
    items = [
        {
            "number": 53,
            "title": "minimal PR",
            "state": "OPEN",
            "updatedAt": "2026-04-22T10:00:00Z",
            "url": "https://github.com/acme/test-repo/pull/53",
            "isDraft": False,
        }
    ]
    with patch("subprocess.run", side_effect=fake_gh_run(items)):
        result = collect([make_repo()], date(2026, 4, 20), date(2026, 4, 23))

    assert result[0]["review_decision"] == ""
    assert result[0]["review_requests"] == []
    assert result[0]["checks"] == "NONE"
    assert result[0]["created_at"] == ""


def test_collect_deduplicates_across_query_types():
    items = [
        {
            "number": 60,
            "title": "appears in all queries",
            "state": "OPEN",
            "updatedAt": "2026-04-22T10:00:00Z",
            "url": "https://github.com/acme/test-repo/pull/60",
            "isDraft": False,
        }
    ]
    with patch("subprocess.run", side_effect=fake_gh_run(items)):
        result = collect([make_repo()], date(2026, 4, 20), date(2026, 4, 23))

    assert len(result) == 1
    assert result[0]["role"] == "author"


def test_collect_skips_closed_pr_with_missing_or_invalid_updated_at():
    items = [
        {
            "number": 70,
            "title": "closed missing updatedAt",
            "state": "CLOSED",
            "updatedAt": "",
            "url": "https://github.com/acme/test-repo/pull/70",
            "isDraft": False,
        },
        {
            "number": 71,
            "title": "closed malformed updatedAt",
            "state": "MERGED",
            "updatedAt": "not-a-timestamp",
            "url": "https://github.com/acme/test-repo/pull/71",
            "isDraft": False,
        },
        {
            "number": 72,
            "title": "closed valid updatedAt",
            "state": "MERGED",
            "updatedAt": "2026-04-21T12:00:00Z",
            "url": "https://github.com/acme/test-repo/pull/72",
            "isDraft": False,
        },
    ]
    with patch("subprocess.run", side_effect=fake_gh_run(items)):
        result = collect([make_repo()], date(2026, 4, 20), date(2026, 4, 23))

    assert len(result) == 1
    assert result[0]["pr_number"] == 72
