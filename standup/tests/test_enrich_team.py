"""Tests for standup_tool.enrich_team."""

from __future__ import annotations

import dataclasses
import logging
import stat
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from standup_tool import enrich_team
from standup_tool.config import Config, LdapConfig
from standup_tool.enrich_team import (
    _enumerate_reports,
    _escape_ldap_filter_value,
    _ldap_available,
    _ldap_lookup_cn,
    _ldap_lookup_uid,
    _ldap_query,
    _resolve_supervisor_uid,
    _walk_up_chain,
    enrich_from_github_profiles,
    enrich_from_ldap,
    get_github_org_members,
    run_enrichment,
)


def _enabled_ldap() -> LdapConfig:
    return LdapConfig(
        enabled=True,
        host="ldaps://ldap-internal.example.com",
        base_dn="ou=users,dc=example,dc=com",
        email_domain="example.com",
        auth_mech="GSSAPI",
        uid_attr="uid",
        worker_id_attr="employeeNumber",
        manager_id_attr="managerEmployeeNumber",
        social_url_attr="socialURL",
    )


def test_escape_ldap_filter_value_rfc4515() -> None:
    assert _escape_ldap_filter_value("*") == r"\2a"
    assert _escape_ldap_filter_value("(") == r"\28"
    assert _escape_ldap_filter_value(")") == r"\29"
    assert _escape_ldap_filter_value("\\") == r"\5c"
    assert _escape_ldap_filter_value("\x00") == r"\00"
    assert _escape_ldap_filter_value("é") == r"\c3\a9"
    assert _escape_ldap_filter_value("x)(uid=*") == r"x\29\28uid=\2a"
    assert _escape_ldap_filter_value(r"a\2ab") == r"a\5c2ab"


def test_ldap_lookup_cn_accepts_names_and_escapes_injection_payloads() -> None:
    ldap = _enabled_ldap()
    captured_filters: list[str] = []

    def fake_query(ldap_filter: str, attrs: list[str], cfg: LdapConfig) -> str:
        captured_filters.append(ldap_filter)
        return (
            "dn: uid=aexample,ou=users,dc=example,dc=com\n"
            "uid: aexample\n"
            "cn: Ada Example\n"
            "mail: ada@example.com\n"
            "socialURL: Github->https://github.com/ada-example\n"
        )

    with patch.object(enrich_team, "_ldap_query", side_effect=fake_query):
        res1 = _ldap_lookup_cn("Ada Example", "ada-example", ldap)
        res2 = _ldap_lookup_cn("O'Brien", "ada-example", ldap)
        _ldap_lookup_cn("x)(uid=*", "ada-example", ldap)
        _ldap_lookup_cn(r"a\2ab", "ada-example", ldap)

    assert res1 is not None and res1["uid"] == "aexample"
    assert res2 is not None
    assert captured_filters == [
        "(cn=Ada Example)",
        "(cn=O'Brien)",
        r"(cn=x\29\28uid=\2a)",
        r"(cn=a\5c2ab)",
    ]


@pytest.mark.parametrize(
    "bad_cn",
    [
        "",
        "   ",
        "A" * 129,
        "Ada\nExample",
        "Ada\rExample",
        "Ada\tExample",
        "Ada\x00Example",
        "Ada\x1fExample",
    ],
)
def test_ldap_lookup_cn_rejects_control_chars_and_out_of_bounds_lengths(bad_cn: str) -> None:
    ldap = _enabled_ldap()
    with patch.object(enrich_team, "_ldap_query") as mock_query:
        assert _ldap_lookup_cn(bad_cn, "ada-example", ldap) is None
    mock_query.assert_not_called()


def test_ldap_lookup_cn_accepts_max_length_128_and_utf8_accents() -> None:
    ldap = _enabled_ldap()
    captured: list[str] = []

    def fake_query(ldap_filter: str, attrs: list[str], cfg: LdapConfig) -> str:
        captured.append(ldap_filter)
        return (
            "dn: uid=aexample,ou=users,dc=example,dc=com\n"
            "uid: aexample\n"
            "cn: René Example\n"
            "mail: rene@example.com\n"
            "socialURL: Github->https://github.com/ada-example\n"
        )

    with patch.object(enrich_team, "_ldap_query", side_effect=fake_query):
        assert _ldap_lookup_cn("A" * 128, "ada-example", ldap) is not None
        assert _ldap_lookup_cn("René", "ada-example", ldap) is not None

    assert captured == [f"(cn={'A' * 128})", r"(cn=Ren\c3\a9)"]


def test_ldap_identifier_callers_reject_metacharacters_and_slashes() -> None:
    ldap = _enabled_ldap()
    with patch.object(enrich_team, "_ldap_query") as mock_query:
        assert _ldap_lookup_uid("a*b", "ada-example", ldap) is None
        assert _ldap_lookup_uid("ada/example", "ada-example", ldap) is None
        assert _walk_up_chain("a*b", 5, ldap) is None
        assert _walk_up_chain("ada/example", 5, ldap) is None
        assert _resolve_supervisor_uid("1)2", ldap) is None
        _enumerate_reports("1)2", {}, 3, ldap)
    mock_query.assert_not_called()


def test_manager_chain_resolution_skipped_when_worker_or_manager_attr_none() -> None:
    ldap_no_worker = dataclasses.replace(_enabled_ldap(), worker_id_attr=None)
    ldap_no_manager = dataclasses.replace(_enabled_ldap(), manager_id_attr=None)
    members = [{"github": "ada-example"}]
    existing = {"ada-example": {"directory_uid": "aexample", "jira_email": "ada@example.com"}}

    with patch.object(enrich_team, "_ldap_query") as mock_query:
        assert enrich_team._build_chain_map(members, existing, ldap_no_worker) == {}
        assert enrich_team._build_chain_map(members, existing, ldap_no_manager) == {}
    mock_query.assert_not_called()


def test_github_org_and_login_validation_skips_traversal_entries(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with patch("subprocess.run") as mock_run:
        assert get_github_org_members("../evil") == []
    mock_run.assert_not_called()

    proc = MagicMock(
        returncode=0,
        stdout="ada-example\n../x\nbo_sample\nuser/bad\n",
        stderr="",
    )
    with caplog.at_level(logging.WARNING), patch("subprocess.run", return_value=proc):
        members = get_github_org_members("acme")

    assert [m["github"] for m in members] == ["ada-example", "bo_sample"]
    assert "../x" in caplog.text

    # enrich_from_github_profiles also skips invalid login without calling subprocess.run
    with patch("subprocess.run") as mock_profile_run:
        res = enrich_from_github_profiles([{"github": "../x"}], {}, _enabled_ldap())
    assert res == [{"github": "../x"}]
    mock_profile_run.assert_not_called()


def test_ldap_failures_never_log_host_or_base_dn(caplog: pytest.LogCaptureFixture) -> None:
    ldap = _enabled_ldap()
    fail_proc = MagicMock(
        returncode=1,
        stdout="",
        stderr="ldap_sasl_bind(SIMPLE): Can't contact LDAP server ldaps://ldap-internal.example.com",
    )

    with caplog.at_level(logging.WARNING), patch("subprocess.run", return_value=fail_proc):
        assert _ldap_available(ldap) is False
        assert _ldap_query("(uid=aexample)", ["cn"], ldap) is None

    assert "LDAP lookup failed for (uid=aexample)" in caplog.text
    assert "ldap-internal.example.com" not in caplog.text
    assert "dc=example" not in caplog.text

    caplog.clear()
    with (
        caplog.at_level(logging.WARNING),
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ldapsearch", timeout=5)),
    ):
        assert _ldap_query("(uid=aexample)", ["cn"], ldap) is None
    assert "LDAP lookup failed for (uid=aexample)" in caplog.text
    assert "ldap-internal.example.com" not in caplog.text


def test_enrich_from_ldap_skips_when_disabled() -> None:
    disabled_ldap = LdapConfig(enabled=False)
    members = [{"github": "ada-example"}]
    with patch("subprocess.run") as mock_run:
        assert enrich_from_ldap(members, {}, disabled_ldap) == members
    mock_run.assert_not_called()


def test_run_enrichment_without_slack_credential_skips_slack_and_writes_private_file(
    acme_config: Config,
    cfg_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STANDUP_SLACK_COOKIE", raising=False)

    def fake_gh(cmd: list[str], **kwargs):
        mock = MagicMock(returncode=0, stderr="")
        if "orgs/acme/members" in cmd:
            mock.stdout = "ada-example\n"
        else:
            mock.stdout = "Ada Example\tada@example.com\n"
        return mock

    with patch("subprocess.run", side_effect=fake_gh):
        run_enrichment(acme_config)

    team_file = cfg_dir / "team-members.yaml"
    assert team_file.exists()
    assert stat.S_IMODE(team_file.lstat().st_mode) == 0o600
    content = team_file.read_text(encoding="utf-8")
    assert "Ada Example" in content
    assert "ada-example" in content


def test_merge_team_preserves_existing_replaces_placeholder_and_adds_new() -> None:
    existing = [
        {
            "name": "ada-example",
            "github": "ada-example",
            "jira_email": "custom@example.com",
        },
        {
            "name": "Bo Custom",
            "github": "bo-sample",
            "slack_id": "UBOCUSTOM",
        },
    ]
    enriched = [
        {
            "github": "ada-example",
            "name": "Ada Example",
            "jira_email": "ldap@example.com",
            "slack_id": "UADA01",
            "directory_uid": "aexample",
        },
        {
            "github": "bo-sample",
            "name": "Bo LDAP",
            "jira_email": "bo@example.com",
            "slack_id": "UBOLDAP",
        },
        {
            "github": "cy-new",
            "name": "Cy New",
            "jira_email": "cy@example.com",
            "slack_id": "UCY01",
            "directory_uid": "cnew",
        },
    ]
    merged = enrich_team.merge_team(existing, enriched)
    by_gh = {m["github"]: m for m in merged}

    # Placeholder name matching github login is replaced; existing jira_email is preserved
    assert by_gh["ada-example"]["name"] == "Ada Example"
    assert by_gh["ada-example"]["jira_email"] == "custom@example.com"
    assert by_gh["ada-example"]["slack_id"] == "UADA01"
    assert by_gh["ada-example"]["directory_uid"] == "aexample"

    # Real existing name and slack_id are preserved; missing jira_email is populated
    assert by_gh["bo-sample"]["name"] == "Bo Custom"
    assert by_gh["bo-sample"]["slack_id"] == "UBOCUSTOM"
    assert by_gh["bo-sample"]["jira_email"] == "bo@example.com"

    # New member is appended
    assert by_gh["cy-new"] == {
        "name": "Cy New",
        "github": "cy-new",
        "directory_uid": "cnew",
        "jira_email": "cy@example.com",
        "slack_id": "UCY01",
    }


def test_enrich_from_github_profiles_derives_directory_uid_and_handles_errors() -> None:
    ldap = _enabled_ldap()
    members = [{"github": "ada-example"}, {"github": "bo-sample"}]
    existing = {"bo-sample": {"name": "Bo Sample", "jira_email": "bo@example.com"}}

    def fake_run(cmd, **kwargs):
        mock = MagicMock(returncode=0, stderr="")
        mock.stdout = "Ada Example\taexample@EXAMPLE.COM\n"
        return mock

    with patch("subprocess.run", side_effect=fake_run):
        res = enrich_from_github_profiles(members, existing, ldap)

    assert res[0]["name"] == "Ada Example"
    assert res[0]["directory_uid"] == "aexample"

    # FileNotFoundError returns members gracefully
    with patch("subprocess.run", side_effect=FileNotFoundError("no gh")):
        res2 = enrich_from_github_profiles([{"github": "cy-new"}], {}, ldap)
    assert res2 == [{"github": "cy-new"}]


def test_build_slack_email_map_and_enrich_from_slack_pagination_and_retries(
    tmp_path: Path,
) -> None:
    from standup_tool.config import SlackConfig
    from standup_tool.slack_auth import SlackCredentials

    slack_cfg = SlackConfig(
        enabled=True,
        workspace_url="https://acme.slack.com",
        channels=("eng",),
        standup_channels=("eng",),
    )
    tok_path = tmp_path / "slack-token"

    # 1. All members already have slack_id -> skips immediately
    already = [{"github": "ada-example", "jira_email": "ada@example.com"}]
    existing_all = {"ada-example": {"slack_id": "UADA"}}
    assert enrich_team.enrich_from_slack(already, existing_all, slack_cfg, tok_path) == already

    # 2. Pagination + ratelimited retry + transient exception retry
    rate_resp = MagicMock(status_code=200, headers={"Retry-After": "1"})
    rate_resp.raise_for_status.return_value = None
    rate_resp.json.return_value = {"ok": False, "error": "ratelimited"}

    page1_resp = MagicMock(status_code=200)
    page1_resp.raise_for_status.return_value = None
    page1_resp.json.return_value = {
        "ok": True,
        "members": [{"name": "ada.slack", "profile": {"email": "Ada@Example.com"}}],
        "response_metadata": {"next_cursor": "cursor-2"},
    }

    page2_resp = MagicMock(status_code=200)
    page2_resp.raise_for_status.return_value = None
    page2_resp.json.return_value = {
        "ok": True,
        "members": [{"name": "bo.slack", "profile": {"email": "bo@example.com"}}],
        "response_metadata": {"next_cursor": ""},
    }

    session = MagicMock()
    session.get.side_effect = [
        RuntimeError("transient network error"),
        rate_resp,
        page1_resp,
        page2_resp,
    ]

    members = [
        {"github": "ada-example", "jira_email": "ada@example.com"},
        {"github": "bo-sample", "jira_email": "bo@example.com"},
    ]
    with (
        patch(
            "standup_tool.enrich_team.get_slack_credentials",
            return_value=SlackCredentials(token="xox" + "c-1", cookie="xox" + "d-1"),
        ),
        patch("standup_tool.enrich_team.make_session", return_value=session),
        patch("standup_tool.enrich_team.time.sleep"),
    ):
        enriched = enrich_team.enrich_from_slack(members, {}, slack_cfg, tok_path)

    assert enriched[0]["slack_id"] == "ada.slack"
    assert enriched[1]["slack_id"] == "bo.slack"

    # 3. Auth error stops pagination cleanly
    auth_fail_resp = MagicMock(status_code=200)
    auth_fail_resp.raise_for_status.return_value = None
    auth_fail_resp.json.return_value = {"ok": False, "error": "invalid_auth"}
    session_auth_fail = MagicMock()
    session_auth_fail.get.return_value = auth_fail_resp
    with patch("standup_tool.enrich_team.time.sleep"):
        assert (
            enrich_team._build_slack_email_map(
                session_auth_fail, {"ada@example.com": {"github": "ada-example"}}
            )
            == {}
        )


def test_ldap_chain_walk_and_enrich_from_ldap_strategies() -> None:
    ldap = _enabled_ldap()
    members = [
        {"github": "ada-example"},
        {"github": "bo-sample"},
        {"github": "cy-cn", "name": "Cy CnName"},
        {"github": "di-done"},
    ]
    existing = {
        "di-done": {
            "name": "Di Done",
            "jira_email": "di@example.com",
            "directory_uid": "ddone",
        }
    }

    def fake_ldap_query(ldap_filter: str, attrs: list[str], cfg: LdapConfig) -> str | None:
        if ldap_filter == "(uid=ddone)":
            return "dn: uid=ddone\nemployeeNumber: W100\nmanagerEmployeeNumber: W200\n"
        if ldap_filter == "(employeeNumber=W200)":
            # Two candidates to exercise _resolve_supervisor_uid disambiguation
            return (
                "dn: uid=mgr-inactive\nuid: mgrinact\n\n"
                "dn: uid=mgr-active\nuid: mgract\nmanagerEmployeeNumber: W200\n"
            )
        if ldap_filter == "(uid=mgract)":
            return "dn: uid=mgract\nemployeeNumber: W200\nmanagerEmployeeNumber: W200\n"
        if ldap_filter == "(managerEmployeeNumber=W200)":
            return (
                "dn: uid=aexample\n"
                "uid: aexample\n"
                "cn: Ada Example\n"
                "mail: ada@example.com\n"
                "employeeNumber: W101\n"
                "socialURL: Github->https://github.com/ada-example\n"
            )
        if ldap_filter == "(managerEmployeeNumber=W101)":
            return ""
        if ldap_filter == "(uid=bo-sample)":
            return (
                "dn: uid=bsample\n"
                "cn: Bo Sample\n"
                "mail: bo@example.com\n"
                "socialURL: Github->https://github.com/bo-sample\n"
            )
        if ldap_filter == "(uid=cy-cn)":
            return None
        if ldap_filter == "(cn=Cy CnName)":
            return (
                "dn: uid=ccn\n"
                "uid: ccn\n"
                "cn: Cy CnName\n"
                "mail: cy@example.com\n"
                "socialURL: Github->https://github.com/cy-cn\n"
            )
        return None

    with (
        patch.object(enrich_team, "_ldap_available", return_value=True),
        patch.object(enrich_team, "_ldap_query", side_effect=fake_ldap_query),
    ):
        result = enrich_from_ldap(members, existing, ldap)

    by_gh = {m["github"]: m for m in result}
    assert by_gh["ada-example"]["name"] == "Ada Example"
    assert by_gh["ada-example"]["jira_email"] == "ada@example.com"
    assert by_gh["ada-example"]["directory_uid"] == "aexample"

    assert by_gh["bo-sample"]["name"] == "Bo Sample"
    assert by_gh["bo-sample"]["jira_email"] == "bo@example.com"

    assert by_gh["cy-cn"]["name"] == "Cy CnName"
    assert by_gh["cy-cn"]["jira_email"] == "cy@example.com"
    assert by_gh["cy-cn"]["directory_uid"] == "ccn"


def test_enrich_from_ldap_does_not_guess_identity_without_social_url_attr() -> None:
    """A GitHub login or display name is attacker-chosen; without a social-URL attribute to
    verify against, neither may be used to pick a directory entry. An explicit directory_uid
    is still trusted."""
    ldap = dataclasses.replace(_enabled_ldap(), social_url_attr=None, worker_id_attr=None)
    queries: list[str] = []

    def fake_ldap_query(ldap_filter: str, attrs: list[str], cfg: LdapConfig) -> str | None:
        queries.append(ldap_filter)
        return "dn: uid=victim\nuid: victim\ncn: Victim Person\nmail: victim@example.com\n"

    members = [
        {"github": "mallory", "name": "Victim Person"},
        {"github": "trent", "directory_uid": "victim"},
    ]
    with (
        patch.object(enrich_team, "_ldap_available", return_value=True),
        patch.object(enrich_team, "_ldap_query", side_effect=fake_ldap_query),
    ):
        result = enrich_from_ldap(members, {}, ldap)

    by_gh = {m["github"]: m for m in result}
    assert "jira_email" not in by_gh["mallory"]
    assert by_gh["mallory"]["name"] == "Victim Person"
    assert "directory_uid" not in by_gh["mallory"]
    assert queries == ["(uid=victim)"]
    assert by_gh["trent"]["jira_email"] == "victim@example.com"


def test_run_enrichment_dry_run_outputs_yaml_without_writing_file(
    acme_config: Config,
    cfg_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("STANDUP_SLACK_COOKIE", raising=False)

    def fake_gh(cmd: list[str], **kwargs):
        mock = MagicMock(returncode=0, stderr="")
        if "orgs/acme/members" in cmd:
            mock.stdout = "ada-example\n"
        else:
            mock.stdout = "Ada Example\tada@example.com\n"
        return mock

    with patch("subprocess.run", side_effect=fake_gh):
        run_enrichment(acme_config, dry_run=True)

    captured = capsys.readouterr()
    assert "Ada Example" in captured.out
    assert "ada-example" in captured.out
    assert not (cfg_dir / "team-members.yaml").exists()
