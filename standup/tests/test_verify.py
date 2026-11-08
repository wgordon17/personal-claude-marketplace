"""Tests for standup_tool.verify."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from standup_tool.collectors.google_auth import GoogleAuthError
from standup_tool.collectors.registry import REGISTRY
from standup_tool.config import (
    Config,
    ConversationsConfig,
    GoogleConfig,
    LdapConfig,
    RepoEntry,
)
from standup_tool.slack_auth import SlackAuthError, SlackCredentials
from standup_tool.verify import VERIFY_NAMES, check_one, run_verify


def test_verify_names_cover_registry_plus_ldap_and_permissions():
    expected = tuple([c.name for c in REGISTRY] + ["ldap", "permissions"])
    assert expected == VERIFY_NAMES
    assert len(VERIFY_NAMES) == 11


def test_verify_git_ok_and_missing_binary_and_bad_repo(acme_config: Config, cfg_dir: Path):
    status, hint = check_one("git", acme_config, cfg_dir)
    assert status == "ok"
    assert "2 repo(s)" in hint

    with patch("standup_tool.verify.subprocess.run", side_effect=FileNotFoundError("git")):
        status, hint = check_one("git", acme_config, cfg_dir)
    assert status == "missing"
    assert "git" in hint.lower()

    bad_cfg = dataclasses.replace(
        acme_config,
        repos=(RepoEntry(path=cfg_dir / "nonexistent-repo"),),
    )
    status, hint = check_one("git", bad_cfg, cfg_dir)
    assert status == "failed"
    assert "nonexistent-repo" in hint


def test_verify_pr_states(acme_config: Config, cfg_dir: Path):
    with patch(
        "standup_tool.verify.subprocess.run",
        return_value=MagicMock(returncode=0, stdout="Logged in", stderr=""),
    ):
        status, hint = check_one("pr", acme_config, cfg_dir)
    assert status == "ok"

    with patch("standup_tool.verify.subprocess.run", side_effect=FileNotFoundError("gh")):
        status, hint = check_one("pr", acme_config, cfg_dir)
    assert status == "missing"
    assert "gh" in hint

    with patch(
        "standup_tool.verify.subprocess.run",
        return_value=MagicMock(returncode=1, stdout="", stderr="not logged into any hosts"),
    ):
        status, hint = check_one("pr", acme_config, cfg_dir)
    assert status == "failed"
    assert "not logged into any hosts" in hint


def test_verify_jira_me_and_jql_probe(acme_config: Config, cfg_dir: Path):
    # Both jira me and jira issue list succeed with results
    def ok_jira(cmd, **kwargs):
        if cmd[:2] == ["jira", "me"]:
            return MagicMock(returncode=0, stdout="ada@example.com\n", stderr="")
        return MagicMock(returncode=0, stdout="ACME-1\tBug\tOpen\n", stderr="")

    with patch("standup_tool.verify.subprocess.run", side_effect=ok_jira):
        status, hint = check_one("jira", acme_config, cfg_dir)
    assert status == "ok"
    assert "ACME" in hint

    # Missing jira binary
    with patch("standup_tool.verify.subprocess.run", side_effect=FileNotFoundError("jira")):
        status, hint = check_one("jira", acme_config, cfg_dir)
    assert status == "missing"
    assert "jira" in hint.lower()

    # jira me exits 0, but jira issue list returns empty output (missing /search/jql scope)
    def empty_probe(cmd, **kwargs):
        if cmd[:2] == ["jira", "me"]:
            return MagicMock(returncode=0, stdout="ada@example.com\n", stderr="")
        return MagicMock(returncode=0, stdout="   \n", stderr="")

    with patch("standup_tool.verify.subprocess.run", side_effect=empty_probe):
        status, hint = check_one("jira", acme_config, cfg_dir)
    assert status == "failed"
    assert (
        hint == "jira issue list returned no results for project ACME; "
        "check JIRA_API_TOKEN scope for /search/jql or run jira init"
    )

    # jira issue list returns "No result found"
    def no_result_probe(cmd, **kwargs):
        if cmd[:2] == ["jira", "me"]:
            return MagicMock(returncode=0, stdout="ada@example.com\n", stderr="")
        return MagicMock(returncode=1, stdout="", stderr="No result found\n")

    with patch("standup_tool.verify.subprocess.run", side_effect=no_result_probe):
        status, hint = check_one("jira", acme_config, cfg_dir)
    assert status == "failed"
    assert (
        hint == "jira issue list returned no results for project ACME; "
        "check JIRA_API_TOKEN scope for /search/jql or run jira init"
    )


@pytest.mark.parametrize("google_name", ["calendar", "gmail", "drive"])
def test_verify_google_collectors_map_to_shared_check(
    google_name: str, acme_config: Config, cfg_dir: Path
):
    with patch("standup_tool.verify.get_credentials", return_value=MagicMock()):
        status, _hint = check_one(google_name, acme_config, cfg_dir)
    assert status == "ok"

    with patch(
        "standup_tool.verify.get_credentials",
        side_effect=FileNotFoundError("credentials.json missing"),
    ):
        status, hint = check_one(google_name, acme_config, cfg_dir)
    assert status == "missing"
    assert "credentials.json" in hint

    with patch(
        "standup_tool.verify.get_credentials",
        side_effect=GoogleAuthError(
            "Google token missing or expired; run standup-tool google-auth"
        ),
    ):
        status, hint = check_one(google_name, acme_config, cfg_dir)
    assert status == "failed"
    assert "run standup-tool google-auth" in hint


def test_verify_slack_states_and_secret_redaction(
    acme_config: Config, cfg_dir: Path, capsys: pytest.CaptureFixture[str]
):
    fake_tok = "xox" + "c-1234567890-abcdef"
    fake_cookie = "xox" + "d-secret-cookie-value-999999"

    # Missing slack credential -> missing (checked before failed even though SlackAuthError
    # subclasses ConfigError)
    with patch(
        "standup_tool.verify.get_slack_credentials",
        side_effect=SlackAuthError(f"no Slack credential ({fake_cookie}); run /standup-onboard"),
    ):
        status, hint = check_one("slack", acme_config, cfg_dir)
    assert status == "missing"
    assert "run /standup-onboard" in hint
    assert fake_cookie not in hint

    # Valid auth.test -> ok, hint prints only user field
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"ok": True, "user": "ada_example", "token": fake_tok}
    with (
        patch(
            "standup_tool.verify.get_slack_credentials",
            return_value=SlackCredentials(token=fake_tok, cookie=fake_cookie),
        ),
        patch("standup_tool.verify.make_session", return_value=MagicMock()),
        patch("standup_tool.verify.safe_get", return_value=mock_resp),
    ):
        status, hint = check_one("slack", acme_config, cfg_dir)
    assert status == "ok"
    assert hint == "ada_example"
    assert fake_tok not in hint
    assert fake_cookie not in hint

    # HTTP failure containing fabricated token -> failed with redacted hint
    with (
        patch(
            "standup_tool.verify.get_slack_credentials",
            return_value=SlackCredentials(token=fake_tok, cookie=fake_cookie),
        ),
        patch("standup_tool.verify.make_session", return_value=MagicMock()),
        patch(
            "standup_tool.verify.safe_get",
            side_effect=requests.HTTPError(f"401 Unauthorized for Bearer {fake_tok}"),
        ),
    ):
        status, hint = check_one("slack", acme_config, cfg_dir)
    assert status == "failed"
    assert fake_tok not in hint
    assert "Bearer [REDACTED]" in hint

    # auth.test payload with ok=False -> failed
    bad_resp = MagicMock()
    bad_resp.raise_for_status.return_value = None
    bad_resp.json.return_value = {"ok": False, "error": "invalid_auth"}
    with (
        patch(
            "standup_tool.verify.get_slack_credentials",
            return_value=SlackCredentials(token=fake_tok, cookie=fake_cookie),
        ),
        patch("standup_tool.verify.make_session", return_value=MagicMock()),
        patch("standup_tool.verify.safe_get", return_value=bad_resp),
    ):
        status, hint = check_one("slack", acme_config, cfg_dir)
    assert status == "failed"
    assert "invalid_auth" in hint

    captured = capsys.readouterr()
    assert fake_tok not in captured.out
    assert fake_tok not in captured.err
    assert fake_cookie not in captured.out
    assert fake_cookie not in captured.err


def test_verify_memory_lists_repos_and_detected_dirs(acme_config: Config, cfg_dir: Path):
    widgets_hack = acme_config.repos[0].path / "hack"
    widgets_hack.mkdir(exist_ok=True)
    (widgets_hack / "PROJECT.md").write_text("# Project\n", encoding="utf-8")
    (widgets_hack / "TODO.md").write_text("# TODO\n", encoding="utf-8")

    status, hint = check_one("memory", acme_config, cfg_dir)
    assert status == "ok"
    assert f"widgets: {widgets_hack.resolve()}" in hint
    assert "gadgets: none" in hint


def test_verify_conversations_and_ldap(
    acme_config: Config, cfg_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Disabled by default in acme_config
    assert check_one("conversations", acme_config, cfg_dir)[0] == "disabled"
    assert check_one("ldap", acme_config, cfg_dir)[0] == "disabled"

    # Enabled conversations with vertex_project and existing projects dir
    projects_dir = tmp_path / "claude-projects"
    projects_dir.mkdir()
    conv_cfg = dataclasses.replace(
        acme_config,
        collectors=dataclasses.replace(
            acme_config.collectors,
            conversations=ConversationsConfig(
                enabled=True,
                project_match=("widgets",),
                vertex_project="acme-vertex-123",
                region="us-east5",
            ),
        ),
    )
    with patch("standup_tool.verify.conversation_activity._CLAUDE_PROJECTS_DIR", projects_dir):
        status, hint = check_one("conversations", conv_cfg, cfg_dir)
    assert status == "ok"
    assert "acme-vertex-123" in hint

    # Enabled conversations with missing projects dir -> failed
    with patch(
        "standup_tool.verify.conversation_activity._CLAUDE_PROJECTS_DIR",
        tmp_path / "missing-projects",
    ):
        status, _hint = check_one("conversations", conv_cfg, cfg_dir)
    assert status == "failed"

    # Enabled ldap: missing binary -> missing; present -> ok
    ldap_cfg = dataclasses.replace(
        acme_config,
        ldap=LdapConfig(
            enabled=True,
            host="ldaps://ldap.example.com",
            base_dn="dc=example,dc=com",
            email_domain="example.com",
        ),
    )
    with patch("standup_tool.verify.subprocess.run", side_effect=FileNotFoundError("ldapsearch")):
        status, hint = check_one("ldap", ldap_cfg, cfg_dir)
    assert status == "missing"
    assert "ldapsearch" in hint

    with (
        patch("standup_tool.verify.shutil.which", return_value="/usr/bin/ldapsearch"),
        patch(
            "standup_tool.verify.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="", stderr=""),
        ),
    ):
        status, _hint = check_one("ldap", ldap_cfg, cfg_dir)
    assert status == "ok"


def test_verify_permissions_check_and_non_failing_default_exit_code(
    acme_config: Config,
    cfg_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(tmp_path)

    # Without settings.json, permissions is missing with copy-paste snippet
    status, hint = check_one("permissions", acme_config, cfg_dir)
    assert status == "missing"
    assert f"Read({cfg_dir / 'slack-token'})" in hint
    assert f"Read({cfg_dir / 'credentials.json'})" in hint
    assert f"Read({cfg_dir / 'token.json'})" in hint
    assert str(cfg_dir) in hint

    # Full run_verify with all enabled checks mocked ok and permissions missing -> exits 0!
    only_memory_cfg = dataclasses.replace(
        acme_config,
        collectors=dataclasses.replace(
            acme_config.collectors,
            git=dataclasses.replace(acme_config.collectors.git, enabled=False),
            pr=dataclasses.replace(acme_config.collectors.pr, enabled=False),
            jira=dataclasses.replace(acme_config.collectors.jira, enabled=False),
            calendar=GoogleConfig(enabled=False),
            gmail=GoogleConfig(enabled=False),
            drive=GoogleConfig(enabled=False),
            slack=dataclasses.replace(acme_config.collectors.slack, enabled=False),
        ),
    )
    exit_code = run_verify(only_memory_cfg, cfg_dir, only=None)
    out = capsys.readouterr().out
    assert "check | status | hint" in out
    assert "permissions | missing |" in out
    assert exit_code == 0

    # But --only permissions exits 1 when permissions is missing
    assert run_verify(only_memory_cfg, cfg_dir, only="permissions") == 1

    # And --only <disabled collector> prints disabled and exits 0
    assert run_verify(only_memory_cfg, cfg_dir, only="pr") == 0
    out_disabled = capsys.readouterr().out
    assert "pr | disabled |" in out_disabled

    # Now populate ~/.claude/settings.json with required rules -> permissions is ok
    claude_dir = fake_home / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    settings_data = {
        "permissions": {
            "deny": [
                f"Read({cfg_dir / 'slack-token'})",
                f"Read({cfg_dir / 'credentials.json'})",
                f"Read({cfg_dir / 'token.json'})",
            ],
            "additionalDirectories": [str(cfg_dir)],
        }
    }
    (claude_dir / "settings.json").write_text(json.dumps(settings_data), encoding="utf-8")

    status, _hint = check_one("permissions", acme_config, cfg_dir)
    assert status == "ok"
    assert run_verify(only_memory_cfg, cfg_dir, only="permissions") == 0


def test_verify_edge_cases_unknown_check_google_cache_and_full_failure(
    acme_config: Config,
    cfg_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    with pytest.raises(ValueError, match="Unknown verify check"):
        check_one("unknown_collector", acme_config, cfg_dir)

    # Shared google_cache invokes get_credentials once across calendar, gmail, drive
    cache: dict[str, tuple[str, str]] = {}
    with patch("standup_tool.verify.get_credentials", return_value=MagicMock()) as mock_creds:
        assert check_one("calendar", acme_config, cfg_dir, google_cache=cache)[0] == "ok"
        assert check_one("gmail", acme_config, cfg_dir, google_cache=cache)[0] == "ok"
        assert check_one("drive", acme_config, cfg_dir, google_cache=cache)[0] == "ok"
    mock_creds.assert_called_once_with(cfg_dir, interactive=False)

    # Conversations missing vertex_project and env vars -> failed
    monkeypatch.delenv("ANTHROPIC_VERTEX_PROJECT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    no_proj_cfg = dataclasses.replace(
        acme_config,
        collectors=dataclasses.replace(
            acme_config.collectors,
            conversations=ConversationsConfig(
                enabled=True,
                project_match=("widgets",),
                vertex_project="",
                region="us-east5",
            ),
        ),
    )
    status, hint = check_one("conversations", no_proj_cfg, cfg_dir)
    assert status == "failed"
    assert "vertex_project" in hint

    # Full run_verify exits 1 when an enabled non-permissions check fails
    bad_repo_cfg = dataclasses.replace(
        acme_config,
        repos=(RepoEntry(path=tmp_path / "missing-repo-dir"),),
        collectors=dataclasses.replace(
            acme_config.collectors,
            pr=dataclasses.replace(acme_config.collectors.pr, enabled=False),
            jira=dataclasses.replace(acme_config.collectors.jira, enabled=False),
            calendar=GoogleConfig(enabled=False),
            gmail=GoogleConfig(enabled=False),
            drive=GoogleConfig(enabled=False),
            slack=dataclasses.replace(acme_config.collectors.slack, enabled=False),
        ),
    )
    assert run_verify(bad_repo_cfg, cfg_dir, only=None) == 1
