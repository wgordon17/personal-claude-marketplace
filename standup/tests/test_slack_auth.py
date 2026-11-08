"""Tests for standup_tool.slack_auth."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from standup_tool.config import ConfigError, SlackConfig
from standup_tool.slack_auth import (
    _BROWSER_UA,
    SlackAuthError,
    SlackCredentials,
    _bootstrap_xoxc,
    get_slack_credentials,
    make_session,
    safe_get,
)

_XOXC_VAL = "xox" + "c-bootstrapped-secret-token-999"
_XOXD_VAL = "xox" + "d-browser-session-cookie-secret-111"
_XOXP_VAL = "xox" + "p-legacy-user-token-222"


def _slack_cfg(workspace_url: str | None = "https://acme.slack.com") -> SlackConfig:
    return SlackConfig(
        enabled=True,
        workspace_url=workspace_url,
        channels=("eng",),
        standup_channels=("eng",),
        self_dm_name=None,
    )


def test_slack_auth_error_subclasses_config_error() -> None:
    assert issubclass(SlackAuthError, ConfigError)


@patch("standup_tool.slack_auth._bootstrap_xoxc", return_value=_XOXC_VAL)
def test_get_slack_credentials_from_single_line_file(
    mock_bootstrap: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STANDUP_SLACK_COOKIE", raising=False)
    tok_path = tmp_path / "slack-token"
    tok_path.write_text(_XOXD_VAL + "\n", encoding="utf-8")

    creds = get_slack_credentials(_slack_cfg(), tok_path)
    assert creds == SlackCredentials(token=_XOXC_VAL, cookie=_XOXD_VAL)
    mock_bootstrap.assert_called_once_with(_XOXD_VAL, "https://acme.slack.com")


@patch("standup_tool.slack_auth._bootstrap_xoxc", return_value=_XOXC_VAL)
def test_get_slack_credentials_from_env_cookie(
    mock_bootstrap: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STANDUP_SLACK_COOKIE", _XOXD_VAL)
    creds = get_slack_credentials(_slack_cfg(), tmp_path / "missing")
    assert creds == SlackCredentials(token=_XOXC_VAL, cookie=_XOXD_VAL)
    mock_bootstrap.assert_called_once_with(_XOXD_VAL, "https://acme.slack.com")


def test_get_slack_credentials_ignores_legacy_token_env_vars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STANDUP_SLACK_COOKIE", raising=False)
    monkeypatch.setenv("STANDUP_SLACK_TOKEN", _XOXP_VAL)
    monkeypatch.setenv("STANDUP_SLACK_XOXC", _XOXC_VAL)

    with pytest.raises(SlackAuthError, match=r"^no Slack credential; run /standup-onboard$"):
        get_slack_credentials(_slack_cfg(), tmp_path / "missing")


def test_get_slack_credentials_missing_file_and_unset_env_raises_exact_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STANDUP_SLACK_COOKIE", raising=False)
    with pytest.raises(SlackAuthError) as exc_info:
        get_slack_credentials(_slack_cfg(), tmp_path / "missing")
    assert str(exc_info.value) == "no Slack credential; run /standup-onboard"


def test_get_slack_credentials_rejects_multiline_token_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("STANDUP_SLACK_COOKIE", raising=False)
    tok_path = tmp_path / "slack-token"
    tok_path.write_text(f"{_XOXC_VAL}\n{_XOXD_VAL}\n", encoding="utf-8")

    with caplog.at_level(logging.DEBUG), pytest.raises(SlackAuthError) as exc_info:
        get_slack_credentials(_slack_cfg(), tok_path)

    assert str(tok_path) in str(exc_info.value)
    assert _XOXC_VAL not in str(exc_info.value)
    assert _XOXD_VAL not in str(exc_info.value)
    assert _XOXC_VAL not in caplog.text
    assert _XOXD_VAL not in caplog.text


def test_get_slack_credentials_rejects_non_xoxd_value_without_leaking_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("STANDUP_SLACK_COOKIE", raising=False)
    tok_path = tmp_path / "slack-token"
    tok_path.write_text(_XOXP_VAL + "\n", encoding="utf-8")

    with caplog.at_level(logging.DEBUG), pytest.raises(SlackAuthError) as exc_info:
        get_slack_credentials(_slack_cfg(), tok_path)

    assert _XOXP_VAL not in str(exc_info.value)
    assert _XOXP_VAL not in caplog.text


def test_get_slack_credentials_validates_workspace_url_before_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STANDUP_SLACK_COOKIE", _XOXD_VAL)
    bad_cfg = _slack_cfg("http://insecure.slack.com")
    with (
        patch("requests.get") as mock_get,
        pytest.raises(SlackAuthError),
    ):
        get_slack_credentials(bad_cfg, tmp_path / "slack-token")
    mock_get.assert_not_called()


def test_bootstrap_xoxc_extracts_token_from_403_html_body() -> None:
    resp = MagicMock()
    resp.status_code = 403
    resp.headers = {}
    resp.text = f'<html><script>boot_data = {{"api_token": "{_XOXC_VAL}"}};</script></html>'

    with patch("requests.get", return_value=resp) as mock_get:
        token = _bootstrap_xoxc(_XOXD_VAL + "/special", "https://acme.slack.com")

    assert token == _XOXC_VAL
    mock_get.assert_called_once_with(
        "https://acme.slack.com",
        cookies={"d": _XOXD_VAL + "%2Fspecial"},
        headers={"User-Agent": _BROWSER_UA},
        timeout=30,
        allow_redirects=False,
    )


@pytest.mark.parametrize(
    "location",
    [
        "/ssb/redirect",
        "https://acme.slack.com/ssb/redirect",
        "https://app.slack.com/client/T123",
    ],
)
def test_bootstrap_xoxc_follows_single_validated_slack_redirect(location: str) -> None:
    redirect_resp = MagicMock()
    redirect_resp.status_code = 302
    redirect_resp.headers = {"Location": location}
    redirect_resp.text = ""

    final_resp = MagicMock()
    final_resp.status_code = 200
    final_resp.headers = {}
    final_resp.text = f'{{"token": "{_XOXC_VAL}"}}'

    with patch("requests.get", side_effect=[redirect_resp, final_resp]) as mock_get:
        token = _bootstrap_xoxc(_XOXD_VAL, "https://acme.slack.com")

    assert token == _XOXC_VAL
    assert mock_get.call_count == 2
    for call in mock_get.call_args_list:
        assert call.kwargs.get("allow_redirects") is False


@pytest.mark.parametrize(
    "bad_location",
    [
        "https://evil.example.com/ssb/redirect",
        "http://acme.slack.com/ssb/redirect",
        "https://user@acme.slack.com/ssb/redirect",
        "https://acme.slack.com:8443/ssb/redirect",
    ],
)
def test_bootstrap_xoxc_rejects_off_host_or_insecure_redirect_after_one_call(
    bad_location: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    redirect_resp = MagicMock()
    redirect_resp.status_code = 302
    redirect_resp.headers = {"Location": bad_location}
    redirect_resp.text = ""

    with (
        caplog.at_level(logging.DEBUG),
        patch("requests.get", return_value=redirect_resp) as mock_get,
        pytest.raises(SlackAuthError) as exc_info,
    ):
        _bootstrap_xoxc(_XOXD_VAL, "https://acme.slack.com")

    assert mock_get.call_count == 1
    assert _XOXD_VAL not in str(exc_info.value)
    assert _XOXD_VAL not in caplog.text


def test_bootstrap_xoxc_rejects_second_redirect_or_missing_location() -> None:
    r1 = MagicMock(status_code=302, headers={"Location": "/step1"}, text="")
    r2 = MagicMock(status_code=302, headers={"Location": "/step2"}, text="")

    with (
        patch("requests.get", side_effect=[r1, r2]) as mock_get,
        pytest.raises(SlackAuthError) as exc_info,
    ):
        _bootstrap_xoxc(_XOXD_VAL, "https://acme.slack.com")

    assert mock_get.call_count == 2
    assert _XOXD_VAL not in str(exc_info.value)

    no_loc = MagicMock(status_code=301, headers={}, text="")
    with (
        patch("requests.get", return_value=no_loc) as mock_get_no_loc,
        pytest.raises(SlackAuthError),
    ):
        _bootstrap_xoxc(_XOXD_VAL, "https://acme.slack.com")
    assert mock_get_no_loc.call_count == 1


def test_bootstrap_xoxc_missing_token_raises_slack_auth_error_without_cookie(
    caplog: pytest.LogCaptureFixture,
) -> None:
    resp = MagicMock(status_code=200, headers={}, text="<html>sign in</html>")
    with (
        caplog.at_level(logging.DEBUG),
        patch("requests.get", return_value=resp),
        pytest.raises(SlackAuthError) as exc_info,
    ):
        _bootstrap_xoxc(_XOXD_VAL, "https://acme.slack.com")

    assert _XOXD_VAL not in str(exc_info.value)
    assert _XOXD_VAL not in caplog.text


def test_safe_get_enforces_https_slack_host_and_no_redirects(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = MagicMock()
    ok_resp = MagicMock(status_code=200)
    session.get.return_value = ok_resp

    with caplog.at_level(logging.DEBUG):
        res = safe_get(session, "https://slack.com/api/auth.test", params={"secret": _XOXC_VAL})

    assert res is ok_resp
    session.get.assert_called_once_with(
        "https://slack.com/api/auth.test",
        params={"secret": _XOXC_VAL},
        allow_redirects=False,
    )
    assert "/api/auth.test" in caplog.text
    assert _XOXC_VAL not in caplog.text

    # Rejects http:// or non-slack host
    session.reset_mock()
    for bad_url in (
        "http://slack.com/api/auth.test",
        "https://evil.example.com/api/auth.test",
        "https://slack.com.evil.com/api/auth.test",
        "https://user@slack.com/api/auth.test",
        "https://slack.com:8443/api/auth.test",
    ):
        with pytest.raises(SlackAuthError):
            safe_get(session, bad_url)
    session.get.assert_not_called()

    # Rejects 3xx response on API call
    redirect_resp = MagicMock(status_code=302)
    session.get.return_value = redirect_resp
    with pytest.raises(SlackAuthError, match="302"):
        safe_get(session, "https://slack.com/api/users.info")


def test_safe_get_redacts_secrets_in_request_exceptions() -> None:
    session = MagicMock()
    session.get.side_effect = requests.ConnectionError(
        f"Failed to connect with cookie {_XOXD_VAL} and Bearer {_XOXC_VAL}"
    )

    with pytest.raises(requests.RequestException) as exc_info:
        safe_get(session, "https://slack.com/api/search.messages")

    assert _XOXD_VAL not in str(exc_info.value)
    assert _XOXC_VAL not in str(exc_info.value)
    assert "[REDACTED]" in str(exc_info.value)


def test_session_get_only_called_inside_safe_get() -> None:
    pkg_root = Path(__file__).resolve().parents[1] / "src" / "standup_tool"
    offenders: list[str] = []
    for py_file in sorted(pkg_root.rglob("*.py")):
        for lineno, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), start=1):
            if "session.get(" in line and py_file.name != "slack_auth.py":
                offenders.append(f"{py_file.relative_to(pkg_root)}:{lineno}: {line.strip()}")
    assert offenders == [], f"Direct session.get( calls found outside slack_auth.py: {offenders}"


def test_get_slack_credentials_rejects_symlink_and_blank_file_and_missing_workspace_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STANDUP_SLACK_COOKIE", raising=False)

    # Missing workspace_url
    with pytest.raises(SlackAuthError, match="workspace_url"):
        get_slack_credentials(_slack_cfg(None), tmp_path / "slack-token")

    # Symlinked token file rejected
    real = tmp_path / "real-token"
    real.write_text(_XOXD_VAL + "\n", encoding="utf-8")
    link = tmp_path / "link-token"
    link.symlink_to(real)
    with pytest.raises(SlackAuthError, match="symlink"):
        get_slack_credentials(_slack_cfg(), link)

    # Whitespace-only token file treated as missing credential
    blank = tmp_path / "blank-token"
    blank.write_text("   \n\n  ", encoding="utf-8")
    with pytest.raises(SlackAuthError, match=r"^no Slack credential; run /standup-onboard$"):
        get_slack_credentials(_slack_cfg(), blank)


def test_bootstrap_xoxc_redacts_secrets_on_network_exception() -> None:
    with (
        patch(
            "requests.get",
            side_effect=requests.ConnectionError(f"boom with {_XOXD_VAL}"),
        ),
        pytest.raises(SlackAuthError) as exc_info,
    ):
        _bootstrap_xoxc(_XOXD_VAL, "https://acme.slack.com")

    assert _XOXD_VAL not in str(exc_info.value)
    assert "xox*-[REDACTED]" in str(exc_info.value)


def test_safe_get_rejects_explicit_allow_redirects_true_and_make_session_sets_headers() -> None:
    creds = SlackCredentials(token=_XOXC_VAL, cookie=_XOXD_VAL + "/x")
    sess = make_session(creds)
    assert sess.headers["Authorization"] == f"Bearer {_XOXC_VAL}"
    assert sess.headers["User-Agent"] == _BROWSER_UA
    assert sess.cookies.get("d", domain=".slack.com") == _XOXD_VAL + "%2Fx"

    mock_sess = MagicMock()
    with pytest.raises(SlackAuthError, match="Redirects are not allowed"):
        safe_get(mock_sess, "https://slack.com/api/auth.test", allow_redirects=True)
    mock_sess.get.assert_not_called()
