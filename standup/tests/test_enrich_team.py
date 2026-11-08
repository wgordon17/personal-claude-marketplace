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
