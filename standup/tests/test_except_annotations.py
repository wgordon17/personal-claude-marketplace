"""AST exception-annotation check and collector error-propagation tests."""

from __future__ import annotations

import ast
import dataclasses
import logging
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from standup_tool.collectors import (
    conversation_activity,
    git_activity,
    google_drive,
    pr_activity,
    slack_activity,
)
from standup_tool.collectors.registry import REGISTRY, Ctx, run_one
from standup_tool.config import Config, ConversationsConfig
from standup_tool.workspace import RepoInfo

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "standup_tool"

AUDITED_MODULES: tuple[Path, ...] = (
    PACKAGE_ROOT / "collectors" / "git_activity.py",
    PACKAGE_ROOT / "collectors" / "pr_activity.py",
    PACKAGE_ROOT / "collectors" / "jira_activity.py",
    PACKAGE_ROOT / "collectors" / "slack_activity.py",
    PACKAGE_ROOT / "collectors" / "gmail.py",
    PACKAGE_ROOT / "collectors" / "google_calendar.py",
    PACKAGE_ROOT / "collectors" / "google_drive.py",
    PACKAGE_ROOT / "collectors" / "google_auth.py",
    PACKAGE_ROOT / "collectors" / "conversation_activity.py",
    PACKAGE_ROOT / "memory.py",
    PACKAGE_ROOT / "collectors" / "memory_activity.py",
    PACKAGE_ROOT / "slack_auth.py",
)


def _get_collector(name: str):
    return next(c for c in REGISTRY if c.name == name)


def _make_ctx(acme_config: Config, cfg_dir: Path, repos: list[RepoInfo] | None = None) -> Ctx:
    cfg = dataclasses.replace(
        acme_config,
        collectors=dataclasses.replace(
            acme_config.collectors,
            conversations=ConversationsConfig(
                enabled=True,
                project_match=("widgets",),
                vertex_project="acme-vertex-prod",
                region="us-east5",
            ),
        ),
    )
    return Ctx(
        config=cfg,
        since=date(2026, 4, 20),
        until=date(2026, 4, 21),
        repos=repos
        if repos is not None
        else [RepoInfo(name="widgets", path=Path("/fake/widgets"), upstream_slug="acme/widgets")],
        author_email="ada@example.com",
        cfg_dir=cfg_dir,
    )


@pytest.mark.parametrize("module_path", AUDITED_MODULES, ids=lambda p: p.name)
def test_every_except_handler_has_tolerant_or_fatal_annotation(module_path: Path):
    assert module_path.is_file(), f"Missing audited module: {module_path}"
    source = module_path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source, filename=str(module_path))

    handlers = [node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)]
    for handler in handlers:
        assert handler.lineno >= 2, f"{module_path.name}:{handler.lineno} has no preceding line"
        prev_line = lines[handler.lineno - 2].strip()
        assert prev_line.startswith("# tolerant:") or prev_line == "# fatal", (
            f"{module_path.name}:{handler.lineno} ExceptHandler must be preceded immediately by "
            f"'# tolerant: <what is skipped>' or '# fatal', found: {prev_line!r}"
        )
        if prev_line.startswith("# tolerant:"):
            reason = prev_line.removeprefix("# tolerant:").strip()
            assert reason, f"{module_path.name}:{handler.lineno} '# tolerant:' must state skip"


def test_google_collectors_propagate_auth_error_and_report_failed(
    acme_config: Config,
    cfg_dir: Path,
):
    ctx = _make_ctx(acme_config, cfg_dir)
    for col_name, target in (
        ("calendar", "standup_tool.collectors.google_calendar.get_credentials"),
        ("gmail", "standup_tool.collectors.gmail.get_credentials"),
        ("drive", "standup_tool.collectors.google_drive.get_credentials"),
    ):
        col = _get_collector(col_name)
        outputs = {"calendar_events": [{"summary": "Sync"}]} if col_name == "drive" else {}
        status = (
            {"calendar": {"state": "ran", "reason": None, "count": 1}}
            if col_name == "drive"
            else {}
        )
        with patch(target, side_effect=PermissionError("Google OAuth invalid_grant")):
            out, st = run_one(col, ctx, None, outputs, status)
        assert out == col.empty
        assert st["state"] == "failed"
        assert "PermissionError" in (st["reason"] or "")


def test_slack_collector_propagates_auth_error_and_reports_failed(
    acme_config: Config,
    cfg_dir: Path,
):
    ctx = _make_ctx(acme_config, cfg_dir)
    col = _get_collector("slack")
    with patch(
        "standup_tool.collectors.slack_activity.get_slack_credentials",
        side_effect=PermissionError("Slack auth failed: invalid_auth"),
    ):
        out, st = run_one(col, ctx, None, {}, {})
    assert out == []
    assert st["state"] == "failed"
    assert "PermissionError" in (st["reason"] or "")


@pytest.mark.parametrize("col_name", ["git", "pr", "jira"])
def test_cli_binary_collectors_propagate_missing_binary_file_not_found(
    col_name: str,
    acme_config: Config,
    cfg_dir: Path,
):
    ctx = _make_ctx(acme_config, cfg_dir)
    col = _get_collector(col_name)
    with patch("subprocess.run", side_effect=FileNotFoundError(2, "No such file", col_name)):
        out, st = run_one(col, ctx, None, {}, {})
    assert out == col.empty
    assert st["state"] == "failed"
    assert "FileNotFoundError" in (st["reason"] or "")


def test_jira_nonzero_completed_process_propagates_and_reports_failed(
    acme_config: Config,
    cfg_dir: Path,
):
    ctx = _make_ctx(acme_config, cfg_dir)
    col = _get_collector("jira")
    bad_proc = MagicMock(returncode=1, stdout="", stderr="401 Unauthorized")
    with patch("subprocess.run", return_value=bad_proc):
        out, st = run_one(col, ctx, None, {}, {})
    assert out == {}
    assert st["state"] == "failed"
    assert "jira issue list failed" in (st["reason"] or "")


def test_git_partial_repo_failure_yields_other_repo_and_warns_while_all_failing_raises(
    acme_config: Config,
    cfg_dir: Path,
    caplog: pytest.LogCaptureFixture,
):
    repo_a = RepoInfo(name="repo-a", path=Path("/fake/repo-a"), upstream_slug="acme/repo-a")
    repo_b = RepoInfo(name="repo-b", path=Path("/fake/repo-b"), upstream_slug="acme/repo-b")
    good_log = (
        "\x00abc123\x1ffeat: widget\x1f2026-04-20T12:00:00+00:00\x1fada@example.com\n\nsrc/a.py\n"
    )

    def partial_run(cmd, **kwargs):
        mock = MagicMock()
        if kwargs.get("cwd") == repo_a.path:
            mock.returncode = 128
            mock.stdout = ""
            mock.stderr = "fatal: bad repo"
        else:
            mock.returncode = 0
            mock.stdout = good_log
            mock.stderr = ""
        return mock

    with caplog.at_level(logging.WARNING), patch("subprocess.run", side_effect=partial_run):
        res = git_activity.collect([repo_a, repo_b], date(2026, 4, 20), date(2026, 4, 21))

    assert len(res) == 1
    assert res[0]["repo"] == "repo-b"
    assert "/fake/repo-a" in caplog.text

    # When every configured repo fails, git collector raises and run_one reports failed
    all_fail_proc = MagicMock(returncode=128, stdout="", stderr="fatal")
    ctx = _make_ctx(acme_config, cfg_dir, repos=[repo_a, repo_b])
    with patch("subprocess.run", return_value=all_fail_proc):
        out, st = run_one(_get_collector("git"), ctx, None, {}, {})
    assert out == []
    assert st["state"] == "failed"


def test_pr_partial_repo_failure_yields_other_repo_and_warns_while_all_failing_raises(
    acme_config: Config,
    cfg_dir: Path,
    caplog: pytest.LogCaptureFixture,
):
    repo_a = RepoInfo(name="repo-a", path=Path("/fake/repo-a"), upstream_slug="acme/repo-a")
    repo_b = RepoInfo(name="repo-b", path=Path("/fake/repo-b"), upstream_slug="acme/repo-b")

    def partial_gh(cmd, **kwargs):
        mock = MagicMock()
        if "acme/repo-a" in cmd:
            mock.returncode = 1
            mock.stdout = ""
            mock.stderr = "HTTP 401"
        else:
            mock.returncode = 0
            mock.stdout = (
                '[{"number": 7, "title": "feat: ok", "state": "OPEN", '
                '"updatedAt": "2026-04-20T10:00:00Z", '
                '"url": "https://github.com/acme/repo-b/pull/7", "isDraft": false}]'
            )
            mock.stderr = ""
        return mock

    with caplog.at_level(logging.WARNING), patch("subprocess.run", side_effect=partial_gh):
        res = pr_activity.collect([repo_a, repo_b], date(2026, 4, 20), date(2026, 4, 21))

    assert len(res) == 1
    assert res[0]["repo"] == "repo-b"
    assert "/fake/repo-a" in caplog.text

    # When every configured repo fails, pr collector raises and run_one reports failed
    all_fail_proc = MagicMock(returncode=1, stdout="", stderr="not logged in")
    ctx = _make_ctx(acme_config, cfg_dir, repos=[repo_a, repo_b])
    with patch("subprocess.run", return_value=all_fail_proc):
        out, st = run_one(_get_collector("pr"), ctx, None, {}, {})
    assert out == []
    assert st["state"] == "failed"


def test_conversations_missing_sessions_dir_propagates_and_reports_failed(
    acme_config: Config,
    cfg_dir: Path,
    tmp_path: Path,
):
    ctx = _make_ctx(acme_config, cfg_dir)
    missing_dir = tmp_path / "missing-claude-projects"
    with patch.object(conversation_activity, "_CLAUDE_PROJECTS_DIR", missing_dir):
        out, st = run_one(_get_collector("conversations"), ctx, None, {}, {})
    assert out == []
    assert st["state"] == "failed"
    assert "FileNotFoundError" in (st["reason"] or "")


def test_slack_and_drive_re_raise_fatal_errors_inside_item_helpers():
    session = MagicMock()
    session.get.side_effect = ConnectionError("connection reset")
    with (
        patch("standup_tool.collectors.slack_activity.time.sleep"),
        pytest.raises(ConnectionError),
    ):
        slack_activity._resolve_user_id(session, "UEXAMPLE02", {})

    service = MagicMock()
    service.comments().list().execute.side_effect = PermissionError("403 Forbidden")
    with pytest.raises(PermissionError):
        google_drive._fetch_comments(service, "file-1")
