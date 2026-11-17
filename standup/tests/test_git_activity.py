"""Tests for git_activity.py."""

import subprocess
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from standup_tool.collectors.git_activity import collect
from standup_tool.workspace import RepoInfo


def make_repo(name: str = "test-repo", path: Path = Path("/fake/repo")) -> RepoInfo:
    return RepoInfo(name=name, path=path, upstream_slug=f"acme/{name}")


# Helpers for building git log --name-only output with leading NUL marker.
# Format per commit: \x00<hash>\x1f<subject>\x1f<iso-date>\x1f<email>\n\n<file1>\n<file2>\n
_SEP = "\x1f"


def _make_log_output(commits: list[tuple[str, str, str, str, list[str]]]) -> str:
    """Build synthetic git log --name-only output (no -z, leading NUL per commit).

    Each commit is (hash, subject, date, email, [files]).
    """
    chunks = []
    for commit_hash, subject, commit_date, email, files in commits:
        meta = f"{commit_hash}{_SEP}{subject}{_SEP}{commit_date}{_SEP}{email}"
        files_block = "\n".join(files)
        chunks.append(f"\x00{meta}\n\n{files_block}\n")
    return "".join(chunks)


def _make_run(log_output: str = ""):
    """Return a fake subprocess.run that yields log_output for any git command."""

    def fake_run(cmd, **kwargs):
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = log_output
        return mock

    return fake_run


def test_collect_returns_empty_for_no_repos():
    result = collect([], date(2026, 4, 20), date(2026, 4, 23))
    assert result == []


def test_collect_parses_commit_fields():
    log_output = _make_log_output(
        [
            (
                "abc123",
                "feat: add thing",
                "2026-04-22T10:00:00+00:00",
                "ada@example.com",
                ["src/main.go"],
            )
        ]
    )

    with patch("subprocess.run", side_effect=_make_run(log_output)):
        result = collect([make_repo()], date(2026, 4, 20), date(2026, 4, 23))

    assert len(result) == 1
    assert result[0]["hash"] == "abc123"
    assert result[0]["subject"] == "feat: add thing"
    assert result[0]["repo"] == "test-repo"
    assert result[0]["author_email"] == "ada@example.com"


def test_collect_filters_dev_only_commits():
    log_output = _make_log_output(
        [
            (
                "abc123",
                "memory update",
                "2026-04-22T10:00:00+00:00",
                "ada@example.com",
                [".dev/PROJECT.md"],
            )
        ]
    )

    with patch("subprocess.run", side_effect=_make_run(log_output)):
        result = collect([make_repo()], date(2026, 4, 20), date(2026, 4, 23))

    assert result == []


def test_collect_includes_mixed_file_commits():
    """Commits touching both .dev/ and real files must NOT be filtered."""
    log_output = _make_log_output(
        [
            (
                "abc123",
                "mixed commit",
                "2026-04-22T10:00:00+00:00",
                "ada@example.com",
                [".dev/PROJECT.md", "src/main.go"],
            )
        ]
    )

    with patch("subprocess.run", side_effect=_make_run(log_output)):
        result = collect([make_repo()], date(2026, 4, 20), date(2026, 4, 23))

    assert len(result) == 1
    assert result[0]["hash"] == "abc123"


def test_collect_skips_single_repo_on_git_error_and_raises_when_all_fail():
    good_output = _make_log_output(
        [
            (
                "def456",
                "feat: other",
                "2026-04-22T11:00:00+00:00",
                "ada@example.com",
                ["src/other.go"],
            )
        ]
    )

    def partial_run(cmd, **kwargs):
        mock = MagicMock()
        if kwargs.get("cwd") == Path("/fake/a"):
            mock.returncode = 1
            mock.stdout = ""
        else:
            mock.returncode = 0
            mock.stdout = good_output
        return mock

    repos = [make_repo("repo-a", Path("/fake/a")), make_repo("repo-b", Path("/fake/b"))]
    with patch("subprocess.run", side_effect=partial_run):
        result = collect(repos, date(2026, 4, 20), date(2026, 4, 23))

    assert len(result) == 1
    assert result[0]["repo"] == "repo-b"

    def all_fail_run(cmd, **kwargs):
        mock = MagicMock()
        mock.returncode = 1
        mock.stdout = ""
        return mock

    with patch("subprocess.run", side_effect=all_fail_run), pytest.raises(RuntimeError):
        collect([make_repo()], date(2026, 4, 20), date(2026, 4, 23))


def test_collect_skips_repo_on_timeout():
    call_count = 0
    good_output = _make_log_output(
        [
            (
                "def456",
                "feat: other",
                "2026-04-22T11:00:00+00:00",
                "ada@example.com",
                ["src/other.go"],
            )
        ]
    )

    def fake_run(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise subprocess.TimeoutExpired(cmd, 30)
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = good_output
        return mock

    repos = [make_repo("repo-a", Path("/fake/a")), make_repo("repo-b", Path("/fake/b"))]
    with patch("subprocess.run", side_effect=fake_run):
        result = collect(repos, date(2026, 4, 20), date(2026, 4, 23))

    assert len(result) == 1
    assert result[0]["repo"] == "repo-b"


def test_collect_filters_commits_outside_date_range():
    """git's --after/--before only prune the traversal; a merge-heavy branch can still
    leak commits from outside the window, so collect() must re-filter by parsed date."""
    log_output = _make_log_output(
        [
            (
                "old1",
                "stale work",
                "2026-04-03T18:48:36-04:00",
                "ada@example.com",
                ["src/old.go"],
            ),
            (
                "in1",
                "recent work",
                "2026-04-22T10:00:00+00:00",
                "ada@example.com",
                ["src/main.go"],
            ),
        ]
    )

    with patch("subprocess.run", side_effect=_make_run(log_output)):
        result = collect([make_repo()], date(2026, 4, 20), date(2026, 4, 23))

    assert len(result) == 1
    assert result[0]["hash"] == "in1"


def test_collect_skips_malformed_log_lines():
    # A record with fewer than 4 fields should be skipped without error.
    log_output = "\x00not-enough-fields\n\n"

    with patch("subprocess.run", side_effect=_make_run(log_output)):
        result = collect([make_repo()], date(2026, 4, 20), date(2026, 4, 23))

    assert result == []


def test_collect_no_extra_subprocess_calls():
    """Single-pass: only one subprocess.run call per repo (no diff-tree)."""
    log_output = _make_log_output(
        [
            (
                "abc123",
                "feat: thing",
                "2026-04-22T10:00:00+00:00",
                "ada@example.com",
                ["src/main.go"],
            )
        ]
    )
    call_count = 0

    def counting_run(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = log_output
        return mock

    with patch("subprocess.run", side_effect=counting_run):
        collect([make_repo()], date(2026, 4, 20), date(2026, 4, 23))

    # Exactly one subprocess call per repo — no N+1 diff-tree calls.
    assert call_count == 1


def test_collect_uses_fixed_strings_for_author():
    """--fixed-strings prevents regex interpretation of email metacharacters like + and ."""
    log_output = _make_log_output([])
    captured_cmd = []

    def spy_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = log_output
        return mock

    with patch("subprocess.run", side_effect=spy_run):
        collect(
            [make_repo()],
            date(2026, 4, 20),
            date(2026, 4, 23),
            author_email="ada+tag@example.com",
        )

    assert "--fixed-strings" in captured_cmd
    assert "--author=ada+tag@example.com" in captured_cmd
