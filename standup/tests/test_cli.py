"""Tests for cli.py."""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import sys
import threading
import time
import urllib.error
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from standup_tool import cli
from standup_tool.cli import (
    _build_standup_queries,
    _extract_jira_auth,
    _get_author_email,
    _get_jira_server,
    _is_standup,
    _parse_inline_elements,
    _standup_to_blocks,
    _write_last_run_marker,
    build_parser,
    read_last_run,
    subcommand_names,
)
from standup_tool.config import Config, ConfigError, IdentityConfig, JiraConfig
from standup_tool.template import load_template

_FAKE_SLACK_TOK = "xox" + "b-fake"


def test_parser_subcommands_match_expected_set():
    assert subcommand_names(build_parser()) == {
        "collect",
        "digest",
        "post-standup",
        "backlog",
        "enrich-team",
        "standup-history",
        "config",
    }


def test_collect_requires_since():
    with (
        pytest.raises(SystemExit),
        patch.object(sys, "argv", ["standup-tool", "collect"]),
    ):
        cli.main()


def test_no_subcommand_exits_nonzero():
    with pytest.raises(SystemExit) as exc_info, patch.object(sys, "argv", ["standup-tool"]):
        cli.main()
    assert exc_info.value.code != 0


def test_missing_config_exits_with_onboard_hint(cfg_dir: Path, capsys):
    with (
        patch.object(sys, "argv", ["standup-tool", "collect", "--since", "2026-04-20"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        cli.main()
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "run /standup-onboard" in captured.err


def test_collect_outputs_json_with_all_keys_and_status_first(acme_config: Config, capsys):
    argv = ["standup-tool", "collect", "--since", "2026-04-20"]
    with (
        patch.object(sys, "argv", argv),
        patch("standup_tool.cli.list_repos", return_value=[]),
        patch("standup_tool.collectors.registry.git_activity.collect", return_value=[]),
        patch("standup_tool.collectors.registry.pr_activity.collect", return_value=[]),
        patch("standup_tool.collectors.registry.jira_activity.collect", return_value={}),
        patch("standup_tool.collectors.registry.google_calendar.collect", return_value=[]),
        patch("standup_tool.collectors.registry.gmail.collect", return_value=[]),
        patch("standup_tool.collectors.registry.slack_activity.collect", return_value=[]),
        patch("standup_tool.collectors.registry.memory_activity.collect", return_value=[]),
        patch("standup_tool.collectors.registry.conversation_activity.collect", return_value=[]),
    ):
        cli.main()

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    keys = list(output.keys())
    assert keys[0] == "_status"
    assert set(keys) == {
        "_status",
        "git_commits",
        "prs",
        "jira_activity",
        "calendar_events",
        "emails",
        "slack",
        "drive_docs",
        "memory_activity",
        "conversations",
    }
    assert "COLLECTION SUMMARY:" in captured.err
    assert "git=ran:0" in captured.err
    assert "conversations=disabled" in captured.err


def test_collect_continues_on_collector_failure_and_skips_marker(
    cfg_dir: Path, acme_config: Config, capsys
):
    argv = ["standup-tool", "collect", "--since", "2026-04-20"]
    with (
        patch.object(sys, "argv", argv),
        patch("standup_tool.cli.list_repos", return_value=[]),
        patch(
            "standup_tool.collectors.registry.git_activity.collect",
            side_effect=Exception("git error"),
        ),
        patch(
            "standup_tool.collectors.registry.pr_activity.collect",
            side_effect=Exception("pr error"),
        ),
        patch(
            "standup_tool.collectors.registry.jira_activity.collect",
            side_effect=Exception("jira error"),
        ),
        patch(
            "standup_tool.collectors.registry.google_calendar.collect",
            side_effect=Exception("cal error"),
        ),
        patch(
            "standup_tool.collectors.registry.gmail.collect",
            side_effect=Exception("gmail error"),
        ),
        patch(
            "standup_tool.collectors.registry.slack_activity.collect",
            side_effect=Exception("slack error"),
        ),
        patch(
            "standup_tool.collectors.registry.memory_activity.collect",
            side_effect=Exception("mem error"),
        ),
    ):
        cli.main()

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["git_commits"] == []
    assert output["prs"] == []
    assert output["jira_activity"] == {}
    assert output["calendar_events"] == []
    assert output["emails"] == []
    assert output["slack"] == []
    assert output["memory_activity"] == []
    assert output["conversations"] == []
    assert output["_status"]["git"]["state"] == "failed"
    assert output["_status"]["drive"]["state"] == "failed"
    assert output["_status"]["drive"]["reason"] == "dependency calendar failed"
    assert "git=failed" in captured.err
    assert "drive=failed" in captured.err
    assert not (cfg_dir / ".last-run").exists()


def test_collect_list_repos_failure_exits_with_config_error(acme_config: Config, capsys):
    argv = ["standup-tool", "collect", "--since", "2026-04-20"]
    with (
        patch.object(sys, "argv", argv),
        patch("standup_tool.cli.list_repos", side_effect=RuntimeError("workspace error")),
        pytest.raises(SystemExit) as exc_info,
    ):
        cli.main()
    assert exc_info.value.code == 1
    assert "workspace error" in capsys.readouterr().err


def test_collect_uses_until_default_yesterday(acme_config: Config):
    captured_args: dict = {}

    def fake_git_collect(repos, since, until, **kwargs):
        captured_args["until"] = until
        return []

    argv = ["standup-tool", "collect", "--since", "2026-04-20"]
    captured = StringIO()
    with (
        patch.object(sys, "argv", argv),
        patch("standup_tool.cli.list_repos", return_value=[]),
        patch(
            "standup_tool.collectors.registry.git_activity.collect",
            side_effect=fake_git_collect,
        ),
        patch("standup_tool.collectors.registry.pr_activity.collect", return_value=[]),
        patch("standup_tool.collectors.registry.jira_activity.collect", return_value={}),
        patch("standup_tool.collectors.registry.google_calendar.collect", return_value=[]),
        patch("standup_tool.collectors.registry.gmail.collect", return_value=[]),
        patch("standup_tool.collectors.registry.slack_activity.collect", return_value=[]),
        patch("standup_tool.collectors.registry.memory_activity.collect", return_value=[]),
        patch("standup_tool.collectors.registry.conversation_activity.collect", return_value=[]),
        patch("sys.stdout", captured),
    ):
        cli.main()

    assert captured_args.get("until") == date.today() - timedelta(days=1)


def test_collect_rejects_invalid_since_date(acme_config: Config):
    argv = ["standup-tool", "collect", "--since", "not-a-date"]
    with patch.object(sys, "argv", argv), pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code != 0


def test_collect_rejects_invalid_until_date(acme_config: Config):
    argv = [
        "standup-tool",
        "collect",
        "--since",
        "2026-04-20",
        "--until",
        "bad-date",
    ]
    with patch.object(sys, "argv", argv), pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code != 0


def test_collect_runs_collectors_in_parallel(acme_config: Config):
    active_threads: set = set()
    lock = threading.Lock()
    max_concurrent = [0]
    running = [0]

    def tracking_collector(*args, **kwargs):
        tid = threading.get_ident()
        with lock:
            active_threads.add(tid)
            running[0] += 1
            if running[0] > max_concurrent[0]:
                max_concurrent[0] = running[0]
        time.sleep(0.05)
        with lock:
            running[0] -= 1
        return []

    def tracking_jira(*args, **kwargs):
        tracking_collector(*args, **kwargs)
        return {}

    argv = ["standup-tool", "collect", "--since", "2026-04-20"]
    captured = StringIO()
    with (
        patch.object(sys, "argv", argv),
        patch("standup_tool.cli.list_repos", return_value=[]),
        patch(
            "standup_tool.collectors.registry.git_activity.collect",
            side_effect=tracking_collector,
        ),
        patch(
            "standup_tool.collectors.registry.pr_activity.collect",
            side_effect=tracking_collector,
        ),
        patch(
            "standup_tool.collectors.registry.jira_activity.collect",
            side_effect=tracking_jira,
        ),
        patch(
            "standup_tool.collectors.registry.google_calendar.collect",
            side_effect=tracking_collector,
        ),
        patch(
            "standup_tool.collectors.registry.gmail.collect",
            side_effect=tracking_collector,
        ),
        patch(
            "standup_tool.collectors.registry.slack_activity.collect",
            side_effect=tracking_collector,
        ),
        patch(
            "standup_tool.collectors.registry.memory_activity.collect",
            side_effect=tracking_collector,
        ),
        patch(
            "standup_tool.collectors.registry.conversation_activity.collect",
            side_effect=tracking_collector,
        ),
        patch("sys.stdout", captured),
    ):
        cli.main()

    assert max_concurrent[0] >= 2


def test_collect_with_collectors_flag_runs_subset(acme_config: Config):
    git_called = []
    jira_called = []

    def fake_git(*args, **kwargs):
        git_called.append(True)
        return []

    def fake_jira(*args, **kwargs):
        jira_called.append(True)
        return {}

    should_not_run = RuntimeError("collector should not have been called")

    argv = [
        "standup-tool",
        "collect",
        "--since",
        "2026-04-20",
        "--collectors",
        "git",
    ]
    captured = StringIO()
    with (
        patch.object(sys, "argv", argv),
        patch("standup_tool.cli.list_repos", return_value=[]),
        patch("standup_tool.collectors.registry.git_activity.collect", side_effect=fake_git),
        patch("standup_tool.collectors.registry.jira_activity.collect", side_effect=fake_jira),
        patch("standup_tool.collectors.registry.pr_activity.collect", side_effect=should_not_run),
        patch(
            "standup_tool.collectors.registry.google_calendar.collect",
            side_effect=should_not_run,
        ),
        patch("standup_tool.collectors.registry.gmail.collect", side_effect=should_not_run),
        patch("sys.stdout", captured),
    ):
        cli.main()

    assert len(git_called) == 1
    assert len(jira_called) == 0
    output = json.loads(captured.getvalue())
    assert "git_commits" in output
    assert output["jira_activity"] == {}
    assert output["_status"]["git"] == {"state": "ran", "reason": None, "count": 0}
    assert output["_status"]["jira"] == {"state": "disabled", "reason": "not selected", "count": 0}


def test_collect_with_empty_collectors_flag_runs_all_enabled(acme_config: Config):
    called_collectors: list = []

    def tracking(*args, **kwargs):
        called_collectors.append(True)
        return []

    def tracking_jira(*args, **kwargs):
        called_collectors.append(True)
        return {}

    argv = [
        "standup-tool",
        "collect",
        "--since",
        "2026-04-20",
        "--collectors",
    ]
    captured = StringIO()
    with (
        patch.object(sys, "argv", argv),
        patch("standup_tool.cli.list_repos", return_value=[]),
        patch("standup_tool.collectors.registry.git_activity.collect", side_effect=tracking),
        patch("standup_tool.collectors.registry.pr_activity.collect", side_effect=tracking),
        patch("standup_tool.collectors.registry.jira_activity.collect", side_effect=tracking_jira),
        patch("standup_tool.collectors.registry.google_calendar.collect", side_effect=tracking),
        patch("standup_tool.collectors.registry.gmail.collect", side_effect=tracking),
        patch("standup_tool.collectors.registry.slack_activity.collect", side_effect=tracking),
        patch("standup_tool.collectors.registry.memory_activity.collect", side_effect=tracking),
        patch(
            "standup_tool.collectors.registry.conversation_activity.collect",
            side_effect=tracking,
        ),
        patch("sys.stdout", captured),
    ):
        cli.main()

    # 7 enabled independent collectors in acme_config; conversations is disabled;
    # drive skips google_drive.collect when calendar returns empty.
    assert len(called_collectors) == 7
    output = json.loads(captured.getvalue())
    assert set(output.keys()) == {
        "_status",
        "git_commits",
        "prs",
        "jira_activity",
        "calendar_events",
        "emails",
        "slack",
        "drive_docs",
        "memory_activity",
        "conversations",
    }


def test_backlog_rejects_invalid_key(acme_config: Config):
    argv = ["standup-tool", "backlog", "invalid-key"]
    with patch.object(sys, "argv", argv), pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code != 0


def test_backlog_exits_on_missing_auth(acme_config: Config):
    argv = ["standup-tool", "backlog", "ACME-123"]
    with (
        patch.object(sys, "argv", argv),
        patch("standup_tool.cli._extract_jira_auth", return_value=""),
        pytest.raises(SystemExit) as exc_info,
    ):
        cli.main()
    assert exc_info.value.code != 0


def test_backlog_sends_request_on_success(acme_config: Config):
    argv = ["standup-tool", "backlog", "ACME-123"]
    mock_resp = type("Resp", (), {"status": 204})()
    with (
        patch.object(sys, "argv", argv),
        patch("standup_tool.cli._extract_jira_auth", return_value="Bearer tok"),
        patch("standup_tool.cli._get_jira_server", return_value="https://jira.example.com"),
        patch("standup_tool.cli.urllib.request.urlopen", return_value=mock_resp) as mock_urlopen,
    ):
        cli.main()

    mock_urlopen.assert_called_once()
    req = mock_urlopen.call_args[0][0]
    assert req.full_url == "https://jira.example.com/rest/agile/1.0/backlog/issue"
    assert req.get_header("Authorization") == "Bearer tok"
    assert json.loads(req.data) == {"issues": ["ACME-123"]}


def test_backlog_prints_success_per_key(acme_config: Config):
    argv = ["standup-tool", "backlog", "ACME-111", "ACME-222"]
    mock_resp = type("Resp", (), {"status": 204})()
    captured = StringIO()
    with (
        patch.object(sys, "argv", argv),
        patch("standup_tool.cli._extract_jira_auth", return_value="Bearer tok"),
        patch("standup_tool.cli._get_jira_server", return_value="https://jira.example.com"),
        patch("standup_tool.cli.urllib.request.urlopen", return_value=mock_resp),
        patch("sys.stdout", captured),
    ):
        cli.main()
    output = captured.getvalue()
    assert "ACME-111: removed from sprint" in output
    assert "ACME-222: removed from sprint" in output


def test_backlog_exits_on_http_error(acme_config: Config):
    argv = ["standup-tool", "backlog", "ACME-123"]
    with (
        patch.object(sys, "argv", argv),
        patch("standup_tool.cli._extract_jira_auth", return_value="Bearer tok"),
        patch("standup_tool.cli._get_jira_server", return_value="https://jira.example.com"),
        patch(
            "standup_tool.cli.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("url", 400, "Bad Request", {}, None),
        ),
        pytest.raises(SystemExit) as exc_info,
    ):
        cli.main()
    assert exc_info.value.code != 0


# =============================================================================
# CLI utility functions (_get_author_email, _extract_jira_auth, _get_jira_server)
# =============================================================================


def test_get_author_email_prefers_config_identity(acme_config: Config):
    with patch("subprocess.run") as mock_run:
        result = _get_author_email(acme_config)
    assert result == "ada@example.com"
    mock_run.assert_not_called()


def test_get_author_email_falls_back_to_git_config(acme_config: Config):
    cfg_no_email = dataclasses.replace(acme_config, identity=IdentityConfig(email=None))

    def fake_run(cmd, **kwargs):
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "bo@example.com\n"
        return mock

    with patch("subprocess.run", side_effect=fake_run) as mock_run:
        result = _get_author_email(cfg_no_email)

    assert result == "bo@example.com"
    mock_run.assert_called_once()
    assert mock_run.call_args.kwargs.get("cwd") == cfg_no_email.repos[0].path


def test_get_author_email_returns_empty_on_failure(acme_config: Config):
    cfg_no_email = dataclasses.replace(acme_config, identity=IdentityConfig(email=None))

    def fake_run(cmd, **kwargs):
        mock = MagicMock()
        mock.returncode = 1
        mock.stdout = ""
        return mock

    with patch("subprocess.run", side_effect=fake_run):
        result = _get_author_email(cfg_no_email)

    assert result == ""


def test_extract_jira_auth_parses_authorization_header():
    def fake_run(cmd, **kwargs):
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = "ada@example.com\n"
        mock.stderr = (
            "REQUEST DETAILS\n"
            "GET /rest/api/2/myself HTTP/1.1\n"
            "Authorization: Basic dXNlcjp0b2tlbg==\n"
            "Accept: application/json\n"
        )
        return mock

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("JIRA_API_TOKEN", None)
        with patch("subprocess.run", side_effect=fake_run):
            result = _extract_jira_auth()

    assert result == "Basic dXNlcjp0b2tlbg=="


def test_extract_jira_auth_returns_empty_on_no_header():
    def fake_run(cmd, **kwargs):
        mock = MagicMock()
        mock.returncode = 1
        mock.stdout = ""
        mock.stderr = "Error: auth failed\n"
        return mock

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("JIRA_API_TOKEN", None)
        with patch("subprocess.run", side_effect=fake_run):
            result = _extract_jira_auth()

    assert result == ""


def test_extract_jira_auth_uses_env_var(tmp_path):
    config_file = tmp_path / ".config.yml"
    config_file.write_text("login: ada@example.com\n")

    with (
        patch.dict(os.environ, {"JIRA_API_TOKEN": "my-token"}),
        patch("standup_tool.cli.Path.expanduser", return_value=config_file),
    ):
        result = _extract_jira_auth()

    import base64

    expected = base64.b64encode(b"ada@example.com:my-token").decode()
    assert result == f"Basic {expected}"


def _config_without_jira_server(acme_config: Config) -> Config:
    return dataclasses.replace(
        acme_config,
        collectors=dataclasses.replace(
            acme_config.collectors,
            jira=JiraConfig(enabled=True, project="ACME", server=None),
        ),
    )


def test_get_jira_server_prefers_config_server(acme_config: Config, tmp_path):
    config_file = tmp_path / ".config.yml"
    config_file.write_text("server: https://other-jira.example.com\n")
    assert _get_jira_server(acme_config, config_path=config_file) == "https://acme.atlassian.net"


def test_get_jira_server_reads_from_jira_cli_config(acme_config: Config, tmp_path):
    cfg = _config_without_jira_server(acme_config)
    config_file = tmp_path / ".config.yml"
    config_file.write_text("server: https://my-jira.example.com\nlogin: ada@example.com\n")

    result = _get_jira_server(cfg, config_path=config_file)
    assert result == "https://my-jira.example.com"


def test_get_jira_server_raises_config_error_when_missing(acme_config: Config, tmp_path):
    cfg = _config_without_jira_server(acme_config)
    missing = tmp_path / "nonexistent"
    with pytest.raises(ConfigError, match="Jira server URL"):
        _get_jira_server(cfg, config_path=missing)


def test_get_jira_server_rejects_non_https(acme_config: Config, tmp_path):
    cfg = _config_without_jira_server(acme_config)
    config_file = tmp_path / ".config.yml"
    config_file.write_text("server: http://insecure.example.com\n")

    with pytest.raises(ConfigError, match="https"):
        _get_jira_server(cfg, config_path=config_file)


def test_get_jira_server_handles_yaml_quoted_value(acme_config: Config, tmp_path):
    cfg = _config_without_jira_server(acme_config)
    config_file = tmp_path / ".config.yml"
    config_file.write_text('server: "https://my-jira.example.com"\n')

    result = _get_jira_server(cfg, config_path=config_file)
    assert result == "https://my-jira.example.com"


def test_get_jira_server_handles_single_quoted_value(acme_config: Config, tmp_path):
    cfg = _config_without_jira_server(acme_config)
    config_file = tmp_path / ".config.yml"
    config_file.write_text("server: 'https://my-jira.example.com'\n")

    result = _get_jira_server(cfg, config_path=config_file)
    assert result == "https://my-jira.example.com"


# =============================================================================
# post-standup subcommand
# =============================================================================


def _make_mock_session(
    auth_ok=True, auth_error=None, dm_ok=True, dm_error=None, post_ok=True, post_error=None
):
    session = MagicMock()

    auth_resp = MagicMock()
    auth_resp.raise_for_status.return_value = None
    auth_data = {"ok": auth_ok, "user_id": "UEXAMPLE01"}
    if not auth_ok:
        auth_data["error"] = auth_error or "invalid_auth"
    auth_resp.json.return_value = auth_data

    dm_resp = MagicMock()
    dm_resp.raise_for_status.return_value = None
    dm_data = {"ok": dm_ok, "channel": {"id": "DEXAMPLE01"}}
    if not dm_ok:
        dm_data["error"] = dm_error or "cannot_dm_bot"
    dm_resp.json.return_value = dm_data

    post_resp = MagicMock()
    post_resp.raise_for_status.return_value = None
    post_data = {"ok": post_ok, "ts": "1234567890.123456"}
    if not post_ok:
        post_data["error"] = post_error or "channel_not_found"
    post_resp.json.return_value = post_data

    session.get.return_value = auth_resp
    session.post.side_effect = [dm_resp, post_resp]

    return session


def _patch_slack(session):
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        with (
            patch(
                "standup_tool.collectors.slack_activity._get_credentials",
                return_value=(_FAKE_SLACK_TOK, None),
            ),
            patch(
                "standup_tool.collectors.slack_activity._make_session",
                return_value=session,
            ),
        ):
            yield

    return _ctx()


def test_post_standup_file_not_found(tmp_path, acme_config: Config):
    missing = tmp_path / "standup.txt"
    argv = ["standup-tool", "post-standup", "--file", str(missing)]
    with patch.object(sys, "argv", argv), pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code != 0


def test_post_standup_empty_file(tmp_path, acme_config: Config):
    standup = tmp_path / "standup.txt"
    standup.write_text("   \n  ")
    argv = ["standup-tool", "post-standup", "--file", str(standup)]
    with patch.object(sys, "argv", argv), pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code != 0


def test_post_standup_auth_slack_error(tmp_path, acme_config: Config):
    standup = tmp_path / "standup.txt"
    standup.write_text("My standup text")
    session = _make_mock_session(auth_ok=False, auth_error="token_revoked")
    argv = ["standup-tool", "post-standup", "--file", str(standup)]
    with (
        patch.object(sys, "argv", argv),
        _patch_slack(session),
        pytest.raises(SystemExit) as exc_info,
    ):
        cli.main()
    assert exc_info.value.code != 0


def test_post_standup_dm_open_failure(tmp_path, acme_config: Config):
    standup = tmp_path / "standup.txt"
    standup.write_text("My standup text")
    session = _make_mock_session(dm_ok=False, dm_error="cannot_dm_bot")
    argv = ["standup-tool", "post-standup", "--file", str(standup)]
    with (
        patch.object(sys, "argv", argv),
        _patch_slack(session),
        pytest.raises(SystemExit) as exc_info,
    ):
        cli.main()
    assert exc_info.value.code != 0


def test_post_standup_post_failure(tmp_path, acme_config: Config):
    standup = tmp_path / "standup.txt"
    standup.write_text("My standup text")
    session = _make_mock_session(post_ok=False, post_error="channel_not_found")
    argv = ["standup-tool", "post-standup", "--file", str(standup)]
    with (
        patch.object(sys, "argv", argv),
        _patch_slack(session),
        pytest.raises(SystemExit) as exc_info,
    ):
        cli.main()
    assert exc_info.value.code != 0


def test_post_standup_success(tmp_path, acme_config: Config):
    standup = tmp_path / "standup.txt"
    standup.write_text(":dart: Key Effort\n• Fixed bug\n\n:rocket: Progress & Wins\n• Wrote tests")
    session = _make_mock_session()
    captured = StringIO()
    argv = ["standup-tool", "post-standup", "--file", str(standup)]
    with patch.object(sys, "argv", argv), _patch_slack(session), patch("sys.stdout", captured):
        cli.main()
    output = captured.getvalue()
    assert "DEXAMPLE01" in output
    assert "1234567890.123456" in output


def test_post_standup_auth_http_error(tmp_path, acme_config: Config):
    standup = tmp_path / "standup.txt"
    standup.write_text("My standup text")
    session = MagicMock()
    auth_resp = MagicMock()
    auth_resp.raise_for_status.side_effect = requests.HTTPError("503 Server Error")
    session.get.return_value = auth_resp
    argv = ["standup-tool", "post-standup", "--file", str(standup)]
    with (
        patch.object(sys, "argv", argv),
        _patch_slack(session),
        pytest.raises(SystemExit) as exc_info,
    ):
        cli.main()
    assert exc_info.value.code != 0


def test_post_standup_dm_http_error(tmp_path, acme_config: Config):
    standup = tmp_path / "standup.txt"
    standup.write_text("My standup text")
    session = _make_mock_session()
    dm_resp = MagicMock()
    dm_resp.raise_for_status.side_effect = requests.HTTPError("502 Bad Gateway")
    session.post.side_effect = [dm_resp]
    argv = ["standup-tool", "post-standup", "--file", str(standup)]
    with (
        patch.object(sys, "argv", argv),
        _patch_slack(session),
        pytest.raises(SystemExit) as exc_info,
    ):
        cli.main()
    assert exc_info.value.code != 0


def test_post_standup_post_http_error(tmp_path, acme_config: Config):
    standup = tmp_path / "standup.txt"
    standup.write_text("My standup text")
    session = _make_mock_session()
    good_dm_resp = MagicMock()
    good_dm_resp.raise_for_status.return_value = None
    good_dm_resp.json.return_value = {"ok": True, "channel": {"id": "DEXAMPLE01"}}
    bad_post_resp = MagicMock()
    bad_post_resp.raise_for_status.side_effect = requests.HTTPError("500 Internal Server Error")
    session.post.side_effect = [good_dm_resp, bad_post_resp]
    argv = ["standup-tool", "post-standup", "--file", str(standup)]
    with (
        patch.object(sys, "argv", argv),
        _patch_slack(session),
        pytest.raises(SystemExit) as exc_info,
    ):
        cli.main()
    assert exc_info.value.code != 0


# =============================================================================
# Last-run marker (read_last_run, _write_last_run_marker)
# =============================================================================


def test_read_last_run_returns_date(tmp_path):
    marker = tmp_path / ".last-run"
    marker.write_text("2026-04-25\n")

    result = read_last_run(tmp_path)
    assert result == date(2026, 4, 25)


def test_read_last_run_returns_none_when_missing(tmp_path):
    result = read_last_run(tmp_path)
    assert result is None


def test_read_last_run_returns_none_on_invalid_date(tmp_path):
    marker = tmp_path / ".last-run"
    marker.write_text("not-a-date\n")

    result = read_last_run(tmp_path)
    assert result is None


def test_collect_writes_last_run_marker(cfg_dir: Path, acme_config: Config):
    argv = [
        "standup-tool",
        "collect",
        "--since",
        "2026-04-20",
        "--until",
        "2026-04-24",
    ]
    captured = StringIO()
    with (
        patch.object(sys, "argv", argv),
        patch("standup_tool.cli.list_repos", return_value=[]),
        patch("standup_tool.collectors.registry.git_activity.collect", return_value=[]),
        patch("standup_tool.collectors.registry.pr_activity.collect", return_value=[]),
        patch("standup_tool.collectors.registry.jira_activity.collect", return_value={}),
        patch("standup_tool.collectors.registry.google_calendar.collect", return_value=[]),
        patch("standup_tool.collectors.registry.gmail.collect", return_value=[]),
        patch("standup_tool.collectors.registry.slack_activity.collect", return_value=[]),
        patch("standup_tool.collectors.registry.memory_activity.collect", return_value=[]),
        patch("standup_tool.collectors.registry.conversation_activity.collect", return_value=[]),
        patch("sys.stdout", captured),
    ):
        cli.main()

    marker = cfg_dir / ".last-run"
    assert marker.exists()
    assert marker.read_text().strip() == "2026-04-25"


def test_collect_does_not_write_marker_with_no_marker_flag(cfg_dir: Path, acme_config: Config):
    argv = [
        "standup-tool",
        "collect",
        "--since",
        "2026-04-20",
        "--until",
        "2026-04-24",
        "--no-marker",
    ]
    captured = StringIO()
    with (
        patch.object(sys, "argv", argv),
        patch("standup_tool.cli.list_repos", return_value=[]),
        patch("standup_tool.collectors.registry.git_activity.collect", return_value=[]),
        patch("standup_tool.collectors.registry.pr_activity.collect", return_value=[]),
        patch("standup_tool.collectors.registry.jira_activity.collect", return_value={}),
        patch("standup_tool.collectors.registry.google_calendar.collect", return_value=[]),
        patch("standup_tool.collectors.registry.gmail.collect", return_value=[]),
        patch("standup_tool.collectors.registry.slack_activity.collect", return_value=[]),
        patch("standup_tool.collectors.registry.memory_activity.collect", return_value=[]),
        patch("standup_tool.collectors.registry.conversation_activity.collect", return_value=[]),
        patch("sys.stdout", captured),
    ):
        cli.main()

    marker = cfg_dir / ".last-run"
    assert not marker.exists()


def test_write_last_run_marker_logs_warning_on_oserror(tmp_path):
    missing_dir = tmp_path / "nonexistent" / "config"
    with patch.object(logging, "warning") as mock_warn:
        _write_last_run_marker(missing_dir, date(2026, 4, 24))

    mock_warn.assert_called_once()
    assert "Failed to write last-run marker" in mock_warn.call_args[0][0]


def test_collect_uses_until_clamped_to_since_when_since_is_today(acme_config: Config):
    captured_args: dict = {}

    def fake_git_collect(repos, since, until, **kwargs):
        captured_args["since"] = since
        captured_args["until"] = until
        return []

    today = date.today().isoformat()
    argv = ["standup-tool", "collect", "--since", today]
    captured = StringIO()
    with (
        patch.object(sys, "argv", argv),
        patch("standup_tool.cli.list_repos", return_value=[]),
        patch(
            "standup_tool.collectors.registry.git_activity.collect",
            side_effect=fake_git_collect,
        ),
        patch("standup_tool.collectors.registry.pr_activity.collect", return_value=[]),
        patch("standup_tool.collectors.registry.jira_activity.collect", return_value={}),
        patch("standup_tool.collectors.registry.google_calendar.collect", return_value=[]),
        patch("standup_tool.collectors.registry.gmail.collect", return_value=[]),
        patch("standup_tool.collectors.registry.slack_activity.collect", return_value=[]),
        patch("standup_tool.collectors.registry.memory_activity.collect", return_value=[]),
        patch("standup_tool.collectors.registry.conversation_activity.collect", return_value=[]),
        patch("sys.stdout", captured),
    ):
        cli.main()

    assert "since" in captured_args
    assert "until" in captured_args
    assert captured_args["until"] == captured_args["since"]


def test_collect_does_not_write_marker_on_partial_collectors(cfg_dir: Path, acme_config: Config):
    argv = [
        "standup-tool",
        "collect",
        "--since",
        "2026-04-20",
        "--until",
        "2026-04-24",
        "--collectors",
        "git",
    ]
    captured = StringIO()
    with (
        patch.object(sys, "argv", argv),
        patch("standup_tool.cli.list_repos", return_value=[]),
        patch("standup_tool.collectors.registry.git_activity.collect", return_value=[]),
        patch("sys.stdout", captured),
    ):
        cli.main()

    marker = cfg_dir / ".last-run"
    assert not marker.exists()


# =============================================================================
# _parse_inline_elements / _standup_to_blocks / template-driven standup history
# =============================================================================


@pytest.fixture
def sample_headers(tmp_path: Path):
    tpl = tmp_path / "tpl.md"
    tpl.write_text(
        ":dart: Key Effort\n"
        "<!-- criteria: main focus -->\n"
        "• item\n\n"
        ":rotating_light: Blockers & Timelines\n"
        "• None\n\n"
        ":rocket: Progress & Wins\n"
        "• Done\n",
        encoding="utf-8",
    )
    return load_template(tpl).headers


def test_parse_inline_elements_unescapes_ampersand_in_text():
    elements = _parse_inline_elements("Risks &amp; Challenges")
    assert elements == [{"type": "text", "text": "Risks & Challenges"}]


def test_parse_inline_elements_unescapes_lt_gt_in_text():
    elements = _parse_inline_elements("a &lt; b &gt; c")
    assert elements == [{"type": "text", "text": "a < b > c"}]


def test_parse_inline_elements_unescapes_link_label():
    elements = _parse_inline_elements("<https://example.com/x|Fix A &amp; B>")
    assert elements == [{"type": "link", "url": "https://example.com/x", "text": "Fix A & B"}]


def test_parse_inline_elements_link_syntax_unaffected_by_unescaping():
    elements = _parse_inline_elements(
        "See <https://acme.atlassian.net/browse/ACME-1|ACME-1> for details"
    )
    assert elements == [
        {"type": "text", "text": "See "},
        {"type": "link", "url": "https://acme.atlassian.net/browse/ACME-1", "text": "ACME-1"},
        {"type": "text", "text": " for details"},
    ]


def test_parse_inline_elements_emoji_unaffected_by_unescaping():
    elements = _parse_inline_elements("Shipped it :tada: nice &amp; clean")
    assert elements == [
        {"type": "text", "text": "Shipped it "},
        {"type": "emoji", "name": "tada"},
        {"type": "text", "text": " nice & clean"},
    ]


def test_standup_to_blocks_header_renders_unescaped_ampersand(sample_headers):
    blocks = _standup_to_blocks(
        ":rotating_light: Blockers &amp; Timelines\n• None",
        sample_headers,
    )
    header_texts = [
        el["text"]
        for block_el in blocks[0]["elements"]
        if block_el["type"] == "rich_text_section"
        for el in block_el["elements"]
        if el["type"] == "text"
    ]
    assert "Blockers & Timelines" in "".join(header_texts)


def test_is_standup_matches_new_and_legacy_formats(sample_headers):
    """_is_standup matches when at least 2 template headers appear (including &amp; escaped)
    and drops legacy headers."""
    valid_text = ":dart: Key Effort\n• focus\n\n:rotating_light: Blockers &amp; Timelines\n• none\n"
    assert _is_standup(valid_text, sample_headers) is True
    assert _is_standup(":dart: Key Effort\n• only one header", sample_headers) is False
    assert (
        _is_standup("Accomplishments\n• legacy header\nIn Progress\n• work", sample_headers)
        is False
    )
    assert _is_standup("Just a regular message with no standup headers", sample_headers) is False


def test_standup_to_blocks_formats_new_template_headers_and_bullets(sample_headers):
    standup_text = (
        ":dart: Key Effort\n"
        "• <https://acme.atlassian.net/browse/ACME-1|ACME-1> review\n\n"
        ":rotating_light: Blockers &amp; Timelines\n"
        "• None\n\n"
        ":rocket: Progress &amp; Wins\n"
        "• Shipped feature\n"
    )
    blocks = _standup_to_blocks(standup_text, sample_headers)
    assert len(blocks) == 1
    elements = blocks[0]["elements"]

    section1 = elements[0]
    assert section1["type"] == "rich_text_section"
    assert section1["elements"][0] == {"type": "emoji", "name": "dart"}
    assert "style" not in section1["elements"][0]
    assert section1["elements"][1]["type"] == "text"
    assert section1["elements"][1]["style"]["bold"] is True

    assert elements[1]["type"] == "rich_text_list"

    section2 = elements[2]
    assert section2["elements"][0] == {"type": "text", "text": "\n"}
    assert section2["elements"][1] == {"type": "emoji", "name": "rotating_light"}
    assert "style" not in section2["elements"][1]
    assert "Blockers & Timelines" in section2["elements"][2]["text"]
    assert "&amp;" not in section2["elements"][2]["text"]
    assert section2["elements"][2]["style"]["bold"] is True

    assert elements[3]["type"] == "rich_text_list"

    section3 = elements[4]
    assert section3["elements"][0] == {"type": "text", "text": "\n"}
    assert section3["elements"][1] == {"type": "emoji", "name": "rocket"}
    assert "style" not in section3["elements"][1]
    assert "Progress & Wins" in section3["elements"][2]["text"]
    assert section3["elements"][2]["style"]["bold"] is True


def test_standup_to_blocks_handles_unbulleted_items_and_section_spacing(sample_headers):
    standup_text = (
        ":dart: Key Effort\n"
        "Land ACME-301 and ACME-302 driver\n"
        "Refine ACME-400 architecture\n\n"
        ":rotating_light: Blockers & Timelines\n"
        "None\n\n"
        ":rocket: Progress & Wins\n"
        "Cleanly reprovisioned cluster\n"
    )
    blocks = _standup_to_blocks(standup_text, sample_headers)
    assert len(blocks) == 1
    elements = blocks[0]["elements"]

    assert elements[0]["type"] == "rich_text_section"
    assert elements[0]["elements"][0] == {"type": "emoji", "name": "dart"}
    assert elements[0]["elements"][1]["style"]["bold"] is True

    assert elements[1]["type"] == "rich_text_section"
    assert "bold" not in (elements[1]["elements"][0].get("style") or {})
    assert elements[1]["elements"][0]["text"] == "Land ACME-301 and ACME-302 driver"

    assert elements[2]["type"] == "rich_text_section"
    assert "bold" not in (elements[2]["elements"][0].get("style") or {})
    assert elements[2]["elements"][0]["text"] == "Refine ACME-400 architecture"

    assert elements[3]["type"] == "rich_text_section"
    assert elements[3]["elements"][0] == {"type": "text", "text": "\n"}
    assert elements[3]["elements"][1] == {"type": "emoji", "name": "rotating_light"}
    assert elements[3]["elements"][2]["style"]["bold"] is True

    assert elements[4]["type"] == "rich_text_section"
    assert "bold" not in (elements[4]["elements"][0].get("style") or {})
    assert elements[4]["elements"][0]["text"] == "None"

    assert elements[5]["type"] == "rich_text_section"
    assert elements[5]["elements"][0] == {"type": "text", "text": "\n"}
    assert elements[5]["elements"][1] == {"type": "emoji", "name": "rocket"}
    assert elements[5]["elements"][2]["style"]["bold"] is True

    assert elements[6]["type"] == "rich_text_section"
    assert "bold" not in (elements[6]["elements"][0].get("style") or {})
    assert elements[6]["elements"][0]["text"] == "Cleanly reprovisioned cluster"


def test_standup_queries_derive_from_template(sample_headers):
    queries = _build_standup_queries(sample_headers)
    assert queries == [
        ":dart:",
        "Key Effort",
        ":rotating_light:",
        "Blockers &",
        ":rocket:",
        "Progress &",
    ]


def test_load_template_rejects_single_header(tmp_path: Path):
    single = tmp_path / "single.md"
    single.write_text(":dart: Only One\n• item\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="at least 2"):
        load_template(single)


def test_standup_history_uses_config_channels(acme_config: Config):
    searched_queries: list[str] = []

    def fake_search(session, query, count=20):
        searched_queries.append(query)
        return []

    session = _make_mock_session()
    captured = StringIO()
    argv = ["standup-tool", "standup-history", "--days", "1"]
    with (
        patch.object(sys, "argv", argv),
        _patch_slack(session),
        patch("standup_tool.collectors.slack_activity._search", side_effect=fake_search),
        patch("time.sleep"),
        patch("sys.stdout", captured),
    ):
        cli.main()

    assert any("in:eng" in q for q in searched_queries)
    assert any("in:ops" in q for q in searched_queries)


@pytest.mark.parametrize("bad_days", ["0", "-3", "91"])
def test_standup_history_rejects_out_of_range_days(acme_config: Config, bad_days: str):
    argv = ["standup-tool", "standup-history", "--days", bad_days]
    with patch.object(sys, "argv", argv), pytest.raises(SystemExit) as exc_info:
        cli.main()
    assert exc_info.value.code != 0


def test_get_author_email_returns_empty_when_no_repos(acme_config: Config):
    cfg_empty = dataclasses.replace(
        acme_config,
        identity=IdentityConfig(email=None),
        repos=(),
    )
    with patch("subprocess.run") as mock_run:
        assert _get_author_email(cfg_empty) == ""
    mock_run.assert_not_called()
