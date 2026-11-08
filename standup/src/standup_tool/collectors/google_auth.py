"""Google OAuth2 authentication shared across Google API collectors."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import google_auth_httplib2
import httplib2
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from standup_tool.config import ConfigError, redact_secrets, tighten_mode, write_private

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

_CREDENTIALS_LOCK = threading.Lock()

HTTP_TIMEOUT = 30  # seconds, per Google API request


class GoogleAuthError(ConfigError):
    """Raised when Google OAuth token is missing or expired in non-interactive mode."""


def authorized_http(creds: Credentials) -> google_auth_httplib2.AuthorizedHttp:
    """HTTP client for googleapiclient with a bounded timeout (the default is unbounded)."""
    return google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=HTTP_TIMEOUT))


def credentials_path(config_dir: Path | None = None) -> Path:
    env = os.environ.get("STANDUP_GOOGLE_CREDENTIALS")
    if env:
        return Path(env)
    if config_dir:
        return config_dir / "credentials.json"
    raise FileNotFoundError(
        "Google credentials path not configured. Set STANDUP_GOOGLE_CREDENTIALS or pass config_dir."
    )


def _token_path(config_dir: Path | None = None) -> Path:
    env = os.environ.get("STANDUP_GOOGLE_TOKEN")
    if env:
        return Path(env)
    if config_dir:
        return config_dir / "token.json"
    raise FileNotFoundError(
        "Google token path not configured. Set STANDUP_GOOGLE_TOKEN or pass config_dir."
    )


def get_credentials(config_dir: Path | None = None, *, interactive: bool = False) -> Credentials:
    """Return valid Google OAuth2 credentials, refreshing or initiating flow when interactive.

    Token file is written with mode 0o600 via write_private().
    Raises FileNotFoundError if credentials.json is absent when no valid token is available.
    Raises GoogleAuthError if token is missing/expired and interactive=False.
    """
    with _CREDENTIALS_LOCK:
        creds: Credentials | None = None
        token_path = _token_path(config_dir)
        creds_path = credentials_path(config_dir)

        if token_path.exists():
            tighten_mode(token_path)
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

        if creds and creds.valid:
            return creds

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                _save_token(creds, token_path)
                return creds
            # tolerant: fall back to interactive OAuth flow when interactive=True; raise otherwise
            except Exception as exc:
                if not interactive:
                    detail = redact_secrets(str(exc))
                    raise GoogleAuthError(
                        f"Google token expired and refresh failed: {detail}\n"
                        "Run standup-tool google-auth to re-authenticate."
                    ) from exc

        if not creds_path.exists():
            raise FileNotFoundError(
                f"Google credentials not found at {creds_path}.\n"
                "See standup/README.md for OAuth setup instructions."
            )

        tighten_mode(creds_path)

        if not interactive:
            raise GoogleAuthError("Google token missing or expired; run standup-tool google-auth")

        flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
        print(
            "Google OAuth required. Open the URL printed below in your browser, then re-run.",
            file=sys.stderr,
        )
        creds = flow.run_local_server(port=0, open_browser=False)
        _save_token(creds, token_path)
        return creds


def _save_token(creds: Credentials, token_path: Path) -> None:
    token_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_private(token_path, creds.to_json())
