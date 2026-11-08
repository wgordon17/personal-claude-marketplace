"""Tests for google_auth.py."""

import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from standup_tool.collectors import google_auth
from standup_tool.collectors.google_auth import GoogleAuthError
from standup_tool.config import ConfigError


def test_google_auth_error_subclasses_config_error():
    assert issubclass(GoogleAuthError, ConfigError)


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


def test_get_credentials_returns_valid_creds_and_tightens_mode_without_checking_creds_file(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("STANDUP_GOOGLE_CREDENTIALS", str(tmp_path / "missing-creds.json"))
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


def test_get_credentials_non_interactive_raises_google_auth_error_on_failing_refresh(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("STANDUP_GOOGLE_CREDENTIALS", str(tmp_path / "creds.json"))
    monkeypatch.setenv("STANDUP_GOOGLE_TOKEN", str(tmp_path / "token.json"))

    ya29_secret = "ya29.a0AfH6SMBxSecretToken123"
    mock_creds = MagicMock()
    mock_creds.valid = False
    mock_creds.expired = True
    mock_creds.refresh_token = "fake_refresh"
    mock_creds.refresh.side_effect = Exception(f"Token revoked ({ya29_secret})")

    with patch(
        "standup_tool.collectors.google_auth.Credentials.from_authorized_user_file",
        return_value=mock_creds,
    ):
        (tmp_path / "token.json").write_text('{"token": "x"}')
        with pytest.raises(GoogleAuthError, match="refresh failed") as exc_info:
            google_auth.get_credentials(interactive=False)

    assert "a0AfH6SMBxSecretToken123" not in str(exc_info.value)


def test_get_credentials_interactive_continues_to_local_server_on_failing_refresh(
    tmp_path, monkeypatch
):
    creds_path = tmp_path / "creds.json"
    creds_path.write_text('{"installed": {}}')
    token_path = tmp_path / "token.json"
    token_path.write_text('{"token": "x"}')
    monkeypatch.setenv("STANDUP_GOOGLE_CREDENTIALS", str(creds_path))
    monkeypatch.setenv("STANDUP_GOOGLE_TOKEN", str(token_path))

    stale_creds = MagicMock()
    stale_creds.valid = False
    stale_creds.expired = True
    stale_creds.refresh_token = "stale_refresh"
    stale_creds.refresh.side_effect = Exception("Token revoked")

    new_creds = MagicMock()
    new_creds.to_json.return_value = '{"token": "fresh"}'
    mock_flow = MagicMock()
    mock_flow.run_local_server.return_value = new_creds

    with (
        patch(
            "standup_tool.collectors.google_auth.Credentials.from_authorized_user_file",
            return_value=stale_creds,
        ),
        patch(
            "standup_tool.collectors.google_auth.InstalledAppFlow.from_client_secrets_file",
            return_value=mock_flow,
        ),
    ):
        result = google_auth.get_credentials(interactive=True)

    mock_flow.run_local_server.assert_called_once_with(port=0, open_browser=False)
    assert result is new_creds
    assert token_path.read_text() == '{"token": "fresh"}'


def test_get_credentials_non_interactive_with_no_token_raises_and_never_calls_local_server(
    tmp_path, monkeypatch
):
    creds_path = tmp_path / "creds.json"
    creds_path.write_text('{"installed": {}}')
    monkeypatch.setenv("STANDUP_GOOGLE_CREDENTIALS", str(creds_path))
    monkeypatch.setenv("STANDUP_GOOGLE_TOKEN", str(tmp_path / "token.json"))

    mock_flow = MagicMock()
    with (
        patch(
            "standup_tool.collectors.google_auth.InstalledAppFlow.from_client_secrets_file",
            return_value=mock_flow,
        ),
        pytest.raises(
            GoogleAuthError,
            match="Google token missing or expired; run standup-tool google-auth",
        ),
    ):
        google_auth.get_credentials(interactive=False)

    mock_flow.run_local_server.assert_not_called()


def test_get_credentials_interactive_with_no_token_calls_run_local_server_once(
    tmp_path, monkeypatch
):
    creds_path = tmp_path / "creds.json"
    creds_path.write_text('{"installed": {}}')
    monkeypatch.setenv("STANDUP_GOOGLE_CREDENTIALS", str(creds_path))
    monkeypatch.setenv("STANDUP_GOOGLE_TOKEN", str(tmp_path / "token.json"))

    mock_creds = MagicMock()
    mock_creds.to_json.return_value = '{"token": "new"}'
    mock_flow = MagicMock()
    mock_flow.run_local_server.return_value = mock_creds

    with patch(
        "standup_tool.collectors.google_auth.InstalledAppFlow.from_client_secrets_file",
        return_value=mock_flow,
    ):
        result = google_auth.get_credentials(interactive=True)

    mock_flow.run_local_server.assert_called_once_with(port=0, open_browser=False)
    assert result is mock_creds


def test_get_credentials_refreshes_expired_token_and_saves(tmp_path, monkeypatch):
    monkeypatch.setenv("STANDUP_GOOGLE_CREDENTIALS", str(tmp_path / "creds.json"))
    token_file = tmp_path / "token.json"
    token_file.write_text('{"token": "expired"}')
    monkeypatch.setenv("STANDUP_GOOGLE_TOKEN", str(token_file))

    mock_creds = MagicMock()
    mock_creds.valid = False
    mock_creds.expired = True
    mock_creds.refresh_token = "valid_refresh"
    mock_creds.to_json.return_value = '{"token": "refreshed"}'

    with patch(
        "standup_tool.collectors.google_auth.Credentials.from_authorized_user_file",
        return_value=mock_creds,
    ):
        result = google_auth.get_credentials()

    assert result is mock_creds
    mock_creds.refresh.assert_called_once()
    assert token_file.read_text() == '{"token": "refreshed"}'


def test_get_credentials_tightens_creds_file_and_rejects_symlinks(tmp_path, monkeypatch):
    creds_path = tmp_path / "creds.json"
    creds_path.write_text('{"installed": {}}')
    creds_path.chmod(0o644)
    monkeypatch.setenv("STANDUP_GOOGLE_CREDENTIALS", str(creds_path))
    monkeypatch.setenv("STANDUP_GOOGLE_TOKEN", str(tmp_path / "token.json"))

    with pytest.raises(GoogleAuthError):
        google_auth.get_credentials(interactive=False)
    assert stat.S_IMODE(creds_path.lstat().st_mode) == 0o600

    # Symlinked token.json rejected via tighten_mode
    outside = tmp_path / "outside-token.json"
    outside.write_text('{"token": "x"}')
    link_tok = tmp_path / "link-token.json"
    link_tok.symlink_to(outside)
    monkeypatch.setenv("STANDUP_GOOGLE_TOKEN", str(link_tok))
    with pytest.raises(ConfigError, match="symlink"):
        google_auth.get_credentials(interactive=False)


def test_get_credentials_serializes_concurrent_refresh_across_threads(tmp_path, monkeypatch):
    import concurrent.futures

    creds_path = tmp_path / "creds.json"
    creds_path.write_text('{"installed": {}}')
    token_file = tmp_path / "token.json"
    token_file.write_text('{"token": "expired"}')
    monkeypatch.setenv("STANDUP_GOOGLE_CREDENTIALS", str(creds_path))
    monkeypatch.setenv("STANDUP_GOOGLE_TOKEN", str(token_file))

    refresh_calls = 0

    def fake_from_file(path_str: str, scopes):
        content = Path(path_str).read_text(encoding="utf-8")
        assert content, "token.json must never be read as empty during concurrent refresh"
        mock_c = MagicMock()
        if "refreshed" in content:
            mock_c.valid = True
            mock_c.expired = False
        else:
            mock_c.valid = False
            mock_c.expired = True
            mock_c.refresh_token = "refresh-tok"

            def do_refresh(_req):
                nonlocal refresh_calls
                refresh_calls += 1

            mock_c.refresh.side_effect = do_refresh
            mock_c.to_json.return_value = '{"token": "refreshed"}'
        return mock_c

    with (
        patch(
            "standup_tool.collectors.google_auth.Credentials.from_authorized_user_file",
            side_effect=fake_from_file,
        ),
        concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool,
    ):
        futures = [pool.submit(google_auth.get_credentials) for _ in range(4)]
        results = [f.result() for f in futures]

    assert len(results) == 4
    assert refresh_calls == 1
    assert token_file.read_text(encoding="utf-8") == '{"token": "refreshed"}'
