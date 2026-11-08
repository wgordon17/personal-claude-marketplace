"""Google OAuth2 authentication shared across Google API collectors."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from standup_tool.config import tighten_mode

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def _credentials_path(config_dir: Path | None = None) -> Path:
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


def get_credentials(config_dir: Path | None = None) -> Credentials:
    """Return valid Google OAuth2 credentials, refreshing or initiating flow as needed.

    Token file is written with mode 0o600 to protect it at creation time.
    Raises FileNotFoundError if credentials.json is absent.
    Raises RuntimeError if token is expired and cannot be refreshed (instructs user).
    """
    creds: Credentials | None = None
    token_path = _token_path(config_dir)
    creds_path = _credentials_path(config_dir)

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
        # fatal
        except Exception as exc:
            raise RuntimeError(
                f"Google token expired and refresh failed: {exc}\n"
                f"Delete {token_path} in the config directory and re-run to re-authenticate."
            ) from exc

    if not creds_path.exists():
        raise FileNotFoundError(
            f"Google credentials not found at {creds_path}.\n"
            "See standup/README.md for OAuth setup instructions."
        )

    tighten_mode(creds_path)

    # Only start the browser-based OAuth flow when running interactively.
    # In non-interactive contexts (e.g., /standup slash command running as a subagent),
    # flow.run_local_server() would block indefinitely waiting for a browser callback.
    # Instead, raise a clear error so the user knows what to do.
    if not sys.stdin.isatty():
        raise RuntimeError(
            "Google OAuth required but no valid token found, and stdin is not a terminal.\n"
            "Complete OAuth setup by running this command interactively in a terminal:\n"
            "  standup-tool collect --since YYYY-MM-DD\n"
            f"Then delete {token_path} in the config directory and re-run if the token is stale."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
    # open_browser=False prints the auth URL to stderr instead of blocking on a browser launch.
    # Run this manually once in a terminal.
    print(
        "Google OAuth required. Open the URL printed below in your browser, then re-run.",
        file=sys.stderr,
    )
    creds = flow.run_local_server(port=0, open_browser=False)
    _save_token(creds, token_path)
    return creds


def _save_token(creds: Credentials, token_path: Path) -> None:
    token_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Open with O_CREAT|O_WRONLY|O_TRUNC at mode 0o600 — avoids window where
    # file exists with world-readable permissions before chmod could be called.
    fd = os.open(str(token_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(creds.to_json())
