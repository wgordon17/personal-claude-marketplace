"""Tests for google_auth.py."""

import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from standup_tool.collectors import google_auth


def test_credentials_path_uses_env_override(tmp_path, monkeypatch):
    custom = str(tmp_path / "custom_creds.json")
    monkeypatch.setenv("STANDUP_GOOGLE_CREDENTIALS", custom)
    assert google_auth._credentials_path() == Path(custom)


def test_token_path_uses_env_override(tmp_path, monkeypatch):
    custom = str(tmp_path / "custom_token.json")
    monkeypatch.setenv("STANDUP_GOOGLE_TOKEN", custom)
    assert google_auth._token_path() == Path(custom)


def test_credentials_path_uses_config_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("STANDUP_GOOGLE_CREDENTIALS", raising=False)
    assert google_auth._credentials_path(config_dir=tmp_path) == tmp_path / "credentials.json"


def test_token_path_uses_config_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("STANDUP_GOOGLE_TOKEN", raising=False)
    assert google_auth._token_path(config_dir=tmp_path) == tmp_path / "token.json"


def test_credentials_path_raises_without_env_or_config_dir(monkeypatch):
    monkeypatch.delenv("STANDUP_GOOGLE_CREDENTIALS", raising=False)
    with pytest.raises(FileNotFoundError, match="config_dir"):
        google_auth._credentials_path()


def test_token_path_raises_without_env_or_config_dir(monkeypatch):
    monkeypatch.delenv("STANDUP_GOOGLE_TOKEN", raising=False)
    with pytest.raises(FileNotFoundError, match="config_dir"):
        google_auth._token_path()


def test_get_credentials_raises_when_no_creds_file(tmp_path, monkeypatch):
    monkeypatch.setenv("STANDUP_GOOGLE_CREDENTIALS", str(tmp_path / "missing.json"))
    monkeypatch.setenv("STANDUP_GOOGLE_TOKEN", str(tmp_path / "token.json"))
    with pytest.raises(FileNotFoundError, match="credentials not found"):
        google_auth.get_credentials()


def test_get_credentials_returns_valid_creds_and_tightens_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("STANDUP_GOOGLE_CREDENTIALS", str(tmp_path / "creds.json"))
    token_file = tmp_path / "token.json"
    monkeypatch.setenv("STANDUP_GOOGLE_TOKEN", str(token_file))

    mock_creds = MagicMock()
    mock_creds.valid = True

    with patch(
        "standup_tool.collectors.google_auth.Credentials.from_authorized_user_file",
        return_value=mock_creds,
    ):
        token_file.write_text('{"token": "x"}')
        token_file.chmod(0o644)
        result = google_auth.get_credentials()

    assert result is mock_creds
    assert stat.S_IMODE(token_file.lstat().st_mode) == 0o600


def test_save_token_uses_secure_permissions(tmp_path):
    token_path = tmp_path / "token.json"
    mock_creds = MagicMock()
    mock_creds.to_json.return_value = '{"token": "abc"}'

    google_auth._save_token(mock_creds, token_path)

    assert token_path.exists()
    mode = oct(token_path.stat().st_mode)
    assert mode.endswith("600"), f"Expected 0o600, got {mode}"


def test_get_credentials_raises_on_expired_no_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv("STANDUP_GOOGLE_CREDENTIALS", str(tmp_path / "creds.json"))
    monkeypatch.setenv("STANDUP_GOOGLE_TOKEN", str(tmp_path / "token.json"))

    mock_creds = MagicMock()
    mock_creds.valid = False
    mock_creds.expired = True
    mock_creds.refresh_token = "fake_refresh"
    mock_creds.refresh.side_effect = Exception("Token revoked")

    with patch(
        "standup_tool.collectors.google_auth.Credentials.from_authorized_user_file",
        return_value=mock_creds,
    ):
        (tmp_path / "token.json").write_text('{"token": "x"}')
        with pytest.raises(RuntimeError, match="refresh failed"):
            google_auth.get_credentials()


def test_get_credentials_raises_when_non_interactive_and_no_token(tmp_path, monkeypatch):
    """Non-interactive context (stdin not a tty) raises RuntimeError instead of blocking."""
    creds_path = tmp_path / "creds.json"
    creds_path.write_text('{"installed": {}}')
    monkeypatch.setenv("STANDUP_GOOGLE_CREDENTIALS", str(creds_path))
    monkeypatch.setenv("STANDUP_GOOGLE_TOKEN", str(tmp_path / "token.json"))

    with patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = False
        with pytest.raises(RuntimeError, match="stdin is not a terminal"):
            google_auth.get_credentials()


def test_get_credentials_starts_flow_when_interactive(tmp_path, monkeypatch):
    """Interactive context (stdin is a tty) proceeds with OAuth flow."""
    creds_path = tmp_path / "creds.json"
    creds_path.write_text('{"installed": {}}')
    monkeypatch.setenv("STANDUP_GOOGLE_CREDENTIALS", str(creds_path))
    monkeypatch.setenv("STANDUP_GOOGLE_TOKEN", str(tmp_path / "token.json"))

    mock_creds = MagicMock()
    mock_flow = MagicMock()
    mock_flow.run_local_server.return_value = mock_creds
    mock_creds.to_json.return_value = '{"token": "new"}'

    flow_patch = "standup_tool.collectors.google_auth.InstalledAppFlow.from_client_secrets_file"
    with patch("sys.stdin") as mock_stdin:
        mock_stdin.isatty.return_value = True
        with patch(flow_patch, return_value=mock_flow):
            result = google_auth.get_credentials()

    mock_flow.run_local_server.assert_called_once()
    assert result is mock_creds
