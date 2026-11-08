"""Tests for workspace.py."""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from standup_tool.config import Config, RepoEntry
from standup_tool.workspace import _parse_upstream_slug, list_repos


def test_parse_upstream_slug_prefers_upstream_remote(tmp_path: Path) -> None:
    def fake_run(cmd, **kwargs):
        mock = MagicMock()
        if cmd[-1] == "upstream":
            mock.returncode = 0
            mock.stdout = "git@github.com:acme/widgets.git\n"
        else:
            mock.returncode = 0
            mock.stdout = "git@github.com:fork-user/widgets.git\n"
        return mock

    with patch("subprocess.run", side_effect=fake_run):
        assert _parse_upstream_slug(tmp_path) == "acme/widgets"


def test_parse_upstream_slug_falls_back_to_origin_https(tmp_path: Path) -> None:
    def fake_run(cmd, **kwargs):
        mock = MagicMock()
        if cmd[-1] == "upstream":
            mock.returncode = 1
            mock.stdout = ""
        else:
            mock.returncode = 0
            mock.stdout = "https://github.com/acme/gadgets.git\n"
        return mock

    with patch("subprocess.run", side_effect=fake_run):
        assert _parse_upstream_slug(tmp_path) == "acme/gadgets"


def test_parse_upstream_slug_returns_empty_when_no_remotes_parse(tmp_path: Path) -> None:
    def fake_run(cmd, **kwargs):
        mock = MagicMock()
        mock.returncode = 1
        mock.stdout = ""
        return mock

    with patch("subprocess.run", side_effect=fake_run):
        assert _parse_upstream_slug(tmp_path) == ""


def test_parse_upstream_slug_rejects_invalid_slug(tmp_path: Path) -> None:
    def fake_run(cmd, **kwargs):
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "git@github.com:-bad/repo.git\n"
        return mock

    with patch("subprocess.run", side_effect=fake_run):
        assert _parse_upstream_slug(tmp_path) == ""


def test_list_repos_returns_configured_repos(acme_config: Config) -> None:
    repos = list_repos(acme_config)
    assert [r.name for r in repos] == ["widgets", "gadgets"]
    assert [r.upstream_slug for r in repos] == ["acme/widgets", "acme/gadgets"]


def test_list_repos_auto_discovers_nested_child_matching_org(
    tmp_path: Path,
    acme_config: Config,
) -> None:
    parent = tmp_path / "widgets"

    matching_child = parent / "sub-widget"
    matching_child.mkdir()
    subprocess.run(["git", "init"], cwd=matching_child, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "upstream", "https://github.com/acme/sub-widget.git"],
        cwd=matching_child,
        check=True,
        capture_output=True,
    )

    origin_only_child = parent / "readonly-ref"
    origin_only_child.mkdir()
    subprocess.run(["git", "init"], cwd=origin_only_child, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/acme/readonly-ref.git"],
        cwd=origin_only_child,
        check=True,
        capture_output=True,
    )

    other_org_child = parent / "external-lib"
    other_org_child.mkdir()
    subprocess.run(["git", "init"], cwd=other_org_child, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "upstream", "https://github.com/other-org/external-lib.git"],
        cwd=other_org_child,
        check=True,
        capture_output=True,
    )

    dot_child = parent / ".hidden-repo"
    dot_child.mkdir()
    subprocess.run(["git", "init"], cwd=dot_child, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "upstream", "https://github.com/acme/hidden-repo.git"],
        cwd=dot_child,
        check=True,
        capture_output=True,
    )

    repos = list_repos(acme_config)
    names = [r.name for r in repos]
    assert "sub-widget" in names
    assert "readonly-ref" not in names
    assert "external-lib" not in names
    assert ".hidden-repo" not in names


def test_list_repos_handles_nonexistent_repo_path_and_deduplicates(
    tmp_path: Path,
    acme_config: Config,
) -> None:
    missing_path = tmp_path / "nonexistent-repo"
    widgets_path = tmp_path / "widgets"
    cfg = dataclasses.replace(
        acme_config,
        repos=(
            RepoEntry(path=widgets_path),
            RepoEntry(path=widgets_path),
            RepoEntry(path=missing_path),
        ),
    )
    repos = list_repos(cfg)
    assert [r.name for r in repos] == ["widgets", "nonexistent-repo"]
    assert repos[0].upstream_slug == "acme/widgets"
    assert repos[1].upstream_slug == ""


def test_parse_upstream_slug_returns_empty_on_timeout(tmp_path: Path) -> None:
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["git"], 10)):
        assert _parse_upstream_slug(tmp_path) == ""
