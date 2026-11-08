"""Cookie-only Slack authentication, token bootstrap, and redirect-safe HTTP helper."""

from __future__ import annotations

import logging
import os
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from standup_tool.config import (
    ConfigError,
    SlackConfig,
    redact_secrets,
    tighten_mode,
    validate_workspace_url,
)

logger = logging.getLogger(__name__)

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_XOXC_PREFIX = "xox" + "c-"
_XOXD_PREFIX = "xox" + "d-"
_COOKIE_RE = re.compile(r"^" + _XOXD_PREFIX + r"[A-Za-z0-9%_.~-]+$")
_API_TOKEN_RE = re.compile(r'"api_token"\s*:\s*"(' + _XOXC_PREFIX + r'[^"]+)"')
_FALLBACK_TOKEN_RE = re.compile(r'"token"\s*:\s*"(' + _XOXC_PREFIX + r'[^"]+)"')
_SLACK_API_HOST_RE = re.compile(r"^([a-z0-9-]+(\.[a-z0-9-]+)*\.)?slack\.com$")
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class SlackAuthError(ConfigError):
    """Raised when Slack cookie credentials, bootstrap, or request validation fail."""


@dataclass(frozen=True)
class SlackCredentials:
    """In-memory Slack session credentials (bootstrapped bearer token + d cookie)."""

    token: str
    cookie: str


def make_session(creds: SlackCredentials) -> requests.Session:
    """Build an authenticated requests.Session for Slack API calls."""
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {creds.token}"
    session.headers["Content-Type"] = "application/json; charset=utf-8"
    session.headers["User-Agent"] = _BROWSER_UA
    session.cookies.set(
        "d", urllib.parse.quote(creds.cookie, safe=""), domain=".slack.com", secure=True
    )
    return session


def _extract_token_from_html(html_text: str, status_code: int) -> str:
    match = _API_TOKEN_RE.search(html_text)
    if not match:
        match = _FALLBACK_TOKEN_RE.search(html_text)
    if not match:
        raise SlackAuthError(
            f"Could not extract client token from Slack workspace page (HTTP {status_code}). "
            "The d cookie may be expired — grab a fresh one from your browser."
        )
    return match.group(1)


def _bootstrap_xoxc(cookie: str, workspace_url: str | None) -> str:
    """Extract client API token from the Slack workspace page with at most 1 validated redirect."""
    if not workspace_url:
        raise SlackAuthError("collectors.slack.workspace_url is required to bootstrap Slack cookie")
    try:
        validate_workspace_url(workspace_url)
    # fatal
    except ConfigError as exc:
        raise SlackAuthError(str(exc)) from exc

    encoded = urllib.parse.quote(cookie, safe="")
    req_headers = {"User-Agent": _BROWSER_UA}
    req_cookies = {"d": encoded}

    try:
        resp = requests.get(
            workspace_url,
            cookies=req_cookies,
            headers=req_headers,
            timeout=30,
            allow_redirects=False,
        )
    # fatal
    except requests.RequestException as exc:
        raise SlackAuthError(redact_secrets(f"Slack bootstrap request failed: {exc}")) from exc

    if resp.status_code in _REDIRECT_STATUSES:
        location = (resp.headers or {}).get("Location")
        if not location:
            raise SlackAuthError("Slack workspace redirect missing Location header")
        if "\\" in location or any(ord(c) <= 32 for c in location):
            raise SlackAuthError("Slack workspace redirect Location contains invalid characters")

        target = urllib.parse.urljoin(workspace_url, location)
        parsed = urllib.parse.urlsplit(target)
        origin = f"{parsed.scheme}://{parsed.netloc}/"
        try:
            validate_workspace_url(origin, "collectors.slack.workspace_url redirect")
        # fatal
        except ConfigError as exc:
            raise SlackAuthError(f"Slack workspace redirect rejected: {exc}") from exc

        try:
            resp = requests.get(
                target,
                cookies=req_cookies,
                headers=req_headers,
                timeout=30,
                allow_redirects=False,
            )
        # fatal
        except requests.RequestException as exc:
            raise SlackAuthError(
                redact_secrets(f"Slack bootstrap redirect request failed: {exc}")
            ) from exc

        if resp.status_code in _REDIRECT_STATUSES:
            raise SlackAuthError("Slack workspace redirected more than once")

    return _extract_token_from_html(resp.text, resp.status_code)


def get_slack_credentials(slack_cfg: SlackConfig, token_path: Path) -> SlackCredentials:
    """Load the Slack d cookie and bootstrap an in-memory client token."""
    if not slack_cfg.workspace_url:
        raise SlackAuthError("collectors.slack.workspace_url: required when Slack is enabled")
    try:
        validate_workspace_url(slack_cfg.workspace_url)
    # fatal
    except ConfigError as exc:
        raise SlackAuthError(str(exc)) from exc

    env_cookie = os.environ.get("STANDUP_SLACK_COOKIE", "").strip()
    if env_cookie:
        cookie = env_cookie
    else:
        if token_path.is_symlink():
            raise SlackAuthError(f"Refusing symlink at {token_path}")
        if not token_path.is_file():
            raise SlackAuthError("no Slack credential; run /standup-onboard")
        try:
            tighten_mode(token_path)
            raw_text = token_path.read_text(encoding="utf-8")
        # fatal
        except (OSError, ConfigError) as exc:
            raise SlackAuthError(f"Failed to read Slack credential file {token_path}") from exc

        lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
        if not lines:
            raise SlackAuthError("no Slack credential; run /standup-onboard")
        if len(lines) > 1:
            raise SlackAuthError(
                f"Slack credential file {token_path} must contain exactly one non-empty line"
            )
        cookie = lines[0]

    if not _COOKIE_RE.match(cookie):
        raise SlackAuthError(
            "Invalid Slack credential format (expected d session cookie; run /standup-onboard)"
        )

    token = _bootstrap_xoxc(cookie, slack_cfg.workspace_url)
    return SlackCredentials(token=token, cookie=cookie)


def _validate_slack_api_url(url: str) -> urllib.parse.SplitResult:
    if not isinstance(url, str) or not url or "\\" in url or any(ord(c) <= 32 for c in url):
        raise SlackAuthError("Invalid Slack API URL")
    try:
        parsed = urllib.parse.urlsplit(url)
        prepared_url = requests.Request("GET", url).prepare().url
        if not prepared_url:
            raise SlackAuthError("Invalid Slack API URL")
        prepared_parsed = urllib.parse.urlsplit(prepared_url)
    # fatal
    except (ValueError, requests.exceptions.RequestException) as exc:
        raise SlackAuthError("Invalid Slack API URL") from exc

    if parsed.scheme != "https":
        raise SlackAuthError("Slack API URL must use https")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise SlackAuthError("Slack API URL must not contain userinfo")
    try:
        port = parsed.port
    # fatal
    except ValueError as exc:
        raise SlackAuthError("Slack API URL has invalid port") from exc
    if port is not None or ":" in parsed.netloc:
        raise SlackAuthError("Slack API URL must not specify an explicit port")
    if not parsed.hostname or parsed.netloc != parsed.hostname or "%" in parsed.netloc:
        raise SlackAuthError("Slack API URL has invalid hostname")
    if not _SLACK_API_HOST_RE.match(parsed.hostname):
        raise SlackAuthError("Slack API URL host must be slack.com or *.slack.com")
    if (
        prepared_parsed.scheme != "https"
        or prepared_parsed.hostname != parsed.hostname
        or prepared_parsed.username is not None
        or prepared_parsed.port is not None
    ):
        raise SlackAuthError("Slack API URL parser mismatch")
    return parsed


_REQUEST_TIMEOUT = (5, 30)  # (connect, read) seconds


def safe_get(session: Any, url: str, **kwargs: Any) -> Any:
    """GET *url* via :func:`_safe_request` (https on slack.com only, no redirects)."""
    return _safe_request(session, "GET", url, **kwargs)


def safe_post(session: Any, url: str, **kwargs: Any) -> Any:
    """POST to *url* via :func:`_safe_request` (https on slack.com only, no redirects)."""
    return _safe_request(session, "POST", url, **kwargs)


def _safe_request(session: Any, method: str, url: str, **kwargs: Any) -> Any:
    """Validate *url* is https on slack.com/*.slack.com, disable redirects, bound the
    request with a timeout, and redact secrets from any raised error."""
    parsed = _validate_slack_api_url(url)
    if kwargs.get("allow_redirects"):
        raise SlackAuthError("Redirects are not allowed on Slack API requests")
    kwargs.setdefault("allow_redirects", False)
    kwargs.setdefault("timeout", _REQUEST_TIMEOUT)

    try:
        resp = getattr(session, method.lower())(url, **kwargs)
    # fatal
    except requests.RequestException as exc:
        redacted = redact_secrets(str(exc))
        try:
            raise type(exc)(redacted) from None
        # fatal
        except TypeError:
            raise requests.RequestException(redacted) from None
    # fatal
    except Exception as exc:
        if isinstance(exc, SlackAuthError):
            raise
        redacted = redact_secrets(str(exc))
        try:
            raise type(exc)(redacted) from None
        # fatal
        except TypeError:
            raise RuntimeError(redacted) from None

    status = getattr(resp, "status_code", 200)
    logger.debug("%s %s -> %s", method, parsed.path or "/", status)
    if isinstance(status, int) and 300 <= status < 400:
        raise SlackAuthError(f"Unexpected redirect (HTTP {status}) on Slack API call")
    return resp
