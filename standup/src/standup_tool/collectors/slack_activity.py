"""Collect Slack activity from configured team channels, private DMs, and involved threads.

Uses search.messages API which works with Enterprise Grid org-level tokens
(unlike conversations.list/history which return enterprise_is_restricted).
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, timedelta
from pathlib import Path

import requests

from standup_tool.config import SlackConfig
from standup_tool.slack_auth import (
    SlackCredentials,
    get_slack_credentials,
    make_session,
    safe_get,
)

logger = logging.getLogger(__name__)

REQUEST_DELAY = 2.0
_THREAD_FETCH_DELAY = 1.0
_MAX_RETRIES = 3


def _make_session(bearer_token: str, cookie: str) -> requests.Session:
    return make_session(SlackCredentials(token=bearer_token, cookie=cookie))


def _search(session: requests.Session, query: str, count: int = 100) -> list[dict]:
    """Search messages with pagination and 429 retry."""
    all_matches: list[dict] = []
    page = 1
    json_retries = 0
    while True:
        for attempt in range(_MAX_RETRIES):
            resp = safe_get(
                session,
                "https://slack.com/api/search.messages",
                params={
                    "query": query,
                    "count": str(count),
                    "page": str(page),
                    "sort": "timestamp",
                },
            )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 5))
                logger.warning(
                    "Slack 429, retrying in %ds (attempt %d/%d)",
                    retry_after,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            break
        else:
            logger.warning(
                "Slack 429 persisted after %d retries, skipping query: %s", _MAX_RETRIES, query
            )
            break
        data = resp.json()
        if not data.get("ok"):
            error = data.get("error", "unknown")
            if error in ("invalid_auth", "token_revoked", "account_inactive"):
                raise PermissionError(f"Slack auth failed: {error}")
            if error == "ratelimited" and json_retries < _MAX_RETRIES:
                json_retries += 1
                logger.warning(
                    "Slack ratelimited (JSON), retrying in 5s (attempt %d/%d)",
                    json_retries,
                    _MAX_RETRIES,
                )
                time.sleep(5)
                continue
            logger.warning("Slack search error: %s (query: %s)", error, query)
            break
        json_retries = 0
        messages = data.get("messages", {})
        matches = messages.get("matches", [])
        all_matches.extend(matches)
        paging = messages.get("paging", {})
        if page >= paging.get("pages", 1):
            break
        time.sleep(REQUEST_DELAY)
        page += 1
    return all_matches


_USER_ID_RE = re.compile(r"^U[A-Z0-9]{6,}$")
_MENTION_RE = re.compile(r"<@(U[A-Z0-9]{6,})(?:\|[^>]*)?>")
_USER_RESOLVE_DELAY = 0.5


def _is_fatal_slack_error(exc: BaseException) -> bool:
    """Return True if *exc* represents an authentication, 401/403, connection, or timeout error."""
    if isinstance(
        exc,
        PermissionError
        | ConnectionError
        | TimeoutError
        | requests.Timeout
        | requests.ConnectionError,
    ):
        return True
    if isinstance(exc, requests.HTTPError):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (401, 403):
            return True
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "401",
            "403",
            "unauthorized",
            "forbidden",
            "invalid_auth",
            "token_revoked",
            "account_inactive",
            "timed out",
            "timeout",
            "connection",
        )
    )


def _resolve_user_id(session: requests.Session, user_id: str, cache: dict[str, str]) -> str:
    """Resolve a Slack user ID to a display name, with caching."""
    if user_id in cache:
        return cache[user_id]
    time.sleep(_USER_RESOLVE_DELAY)
    try:
        resp = safe_get(session, "https://slack.com/api/users.info", params={"user": user_id})
        if getattr(resp, "status_code", 200) in (401, 403):
            raise PermissionError(f"Slack users.info HTTP {resp.status_code}")
        data = resp.json()
        if data.get("ok"):
            profile = data["user"].get("profile", {})
            name = profile.get("real_name") or profile.get("display_name") or user_id
        else:
            err = data.get("error", "")
            if err in ("invalid_auth", "token_revoked", "account_inactive"):
                raise PermissionError(f"Slack auth failed: {err}")
            name = user_id
    # tolerant: fall back to raw user_id when non-fatal user profile lookup fails
    except Exception as exc:
        if _is_fatal_slack_error(exc):
            raise
        logger.debug("User resolve failed for %s: %s", user_id, exc)
        name = user_id
    cache[user_id] = name
    return name


def _resolve_mentions(text: str, session: requests.Session, cache: dict[str, str]) -> str:
    """Replace ``<@UXXXX>`` mentions in *text* with resolved display names."""
    return _MENTION_RE.sub(lambda m: _resolve_user_id(session, m.group(1), cache), text)


def _resolve_channel_name(
    session: requests.Session, channel_name: str, cache: dict[str, str]
) -> str:
    """Resolve channel names that are user IDs (DMs) to human-readable names."""
    if _USER_ID_RE.match(channel_name):
        return _resolve_user_id(session, channel_name, cache)
    return channel_name


def _fetch_thread(session: requests.Session, channel_id: str, thread_ts: str) -> list[dict]:
    """Fetch thread replies for context enrichment."""
    try:
        resp = safe_get(
            session,
            "https://slack.com/api/conversations.replies",
            params={"channel": channel_id, "ts": thread_ts, "limit": "10"},
        )
        if getattr(resp, "status_code", 200) in (401, 403):
            raise PermissionError(f"Slack conversations.replies HTTP {resp.status_code}")
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok"):
            return [
                {"user": m.get("user", ""), "text": m.get("text", ""), "ts": m.get("ts", "")}
                for m in data.get("messages", [])
            ]
        err = data.get("error", "")
        if err in ("invalid_auth", "token_revoked", "account_inactive"):
            raise PermissionError(f"Slack auth failed: {err}")
    # tolerant: skip thread enrichment for single message when non-fatal reply fetch fails
    except Exception as exc:
        if _is_fatal_slack_error(exc):
            raise
        logger.debug("Thread fetch failed for %s/%s: %s", channel_id, thread_ts, exc)
    return []


def _format_match(
    match: dict, source: str, session: requests.Session, cache: dict[str, str]
) -> dict:
    """Convert a search.messages match to our standard output format."""
    channel = match.get("channel", {})
    channel_name = channel.get("name", "")
    ts = match.get("ts", "0")
    try:
        msg_date = match.get("date_str", ts[:10])
    # tolerant: use empty date string when match timestamp is malformed
    except (TypeError, IndexError):
        logger.debug("Skipping malformed timestamp %r in Slack match", ts)
        msg_date = ""

    resolved_name = _resolve_channel_name(session, channel_name, cache)
    if source == "dm":
        display_channel = f"DM with {resolved_name}" if resolved_name else "DM"
    else:
        display_channel = f"#{resolved_name}" if resolved_name else source

    raw_permalink = match.get("permalink", "")
    parent_ts = match.get("ts", "")
    if "thread_ts=" in raw_permalink:
        parent_ts = raw_permalink.split("thread_ts=")[-1].split("&")[0]

    return {
        "source": source,
        "channel": display_channel,
        "thread_ts": parent_ts,
        "date": msg_date,
        "messages": [
            {
                "user": match.get("username", match.get("user", "")),
                "text": _resolve_mentions(match.get("text", ""), session, cache),
                "ts": ts,
            }
        ],
        "user_participated": True,
        "message_count": 1,
        "permalink": match.get("permalink", ""),
    }


def collect(
    since_date: date,
    until_date: date,
    *,
    slack_cfg: SlackConfig,
    token_path: Path,
) -> list[dict]:
    """Collect Slack activity via search.messages.

    Runs searches across target channels plus three cross-channel queries:
    1. in:#<channel> for each configured channel — all channel activity for team context
    2. is:dm — DMs sent and received
    3. to:me — messages where the user is @mentioned (excluding target channels)
    4. from:me (excluding DMs and target channels) — threads user participated in
    """
    creds = get_slack_credentials(slack_cfg, token_path)
    session = make_session(creds)
    channels = slack_cfg.channels

    resp = safe_get(session, "https://slack.com/api/auth.test")
    resp.raise_for_status()
    auth_data = resp.json()
    if not auth_data.get("ok"):
        raise PermissionError("Slack auth.test failed — check your token")
    user_name = auth_data.get("user", "unknown")
    logger.info("Slack authenticated as %s", user_name)

    # Slack's after:/before: are exclusive and use UTC boundaries, which
    # silently drops local-time messages. Use on:YYYY-MM-DD per day instead
    # — it matches messages on that calendar date regardless of UTC offset.
    days: list[date] = []
    current = since_date
    while current <= until_date:
        days.append(current)
        current += timedelta(days=1)

    def search_days(base_query: str) -> list[dict]:
        all_matches: list[dict] = []
        for day in days:
            all_matches.extend(_search(session, f"{base_query} on:{day.isoformat()}"))
        return all_matches

    results: list[dict] = []
    seen_permalinks: set[str] = set()
    user_cache: dict[str, str] = {}

    def add_matches(matches: list[dict], source: str) -> None:
        for m in matches:
            permalink = m.get("permalink", "")
            if permalink and permalink in seen_permalinks:
                continue
            if permalink:
                seen_permalinks.add(permalink)
            results.append(_format_match(m, source, session, user_cache))

    # 1. Target channel messages
    for channel in channels:
        channel_matches = search_days(f"in:#{channel}")
        logger.info("#%s: %d messages", channel, len(channel_matches))
        add_matches(channel_matches, "channel")

    # 2. DMs (sent and received)
    dm_matches = search_days("is:dm")
    logger.info("DMs: %d messages", len(dm_matches))
    add_matches(dm_matches, "dm")

    # 3. Mentions (threads user is @mentioned in, outside the target channels)
    target_set = set(channels)
    mention_matches = search_days("to:me")
    non_channel = [m for m in mention_matches if m.get("channel", {}).get("name") not in target_set]
    logger.info("Mentions (to:me, non-channel): %d messages", len(non_channel))
    add_matches(non_channel, "mention")

    # 4. Participation (threads user replied in but wasn't @mentioned)
    exclusions = "".join(f" -in:#{ch}" for ch in channels)
    my_matches = search_days(f"from:me -is:dm{exclusions}")
    logger.info("Participation (from:me, other channels): %d messages", len(my_matches))
    add_matches(my_matches, "participation")

    # Enrich DM, mention, and participation results with thread context
    enriched = 0
    for item in results:
        if item["source"] == "channel":
            continue
        permalink = item.get("permalink", "")
        parts = permalink.split("/archives/")
        if len(parts) < 2:
            continue
        channel_id = parts[1].split("/")[0]
        thread_ts = item.get("thread_ts", "")
        if not thread_ts:
            continue
        time.sleep(_THREAD_FETCH_DELAY)
        thread_msgs = _fetch_thread(session, channel_id, thread_ts)
        if len(thread_msgs) > 1:
            for msg in thread_msgs:
                msg["text"] = _resolve_mentions(msg["text"], session, user_cache)
            item["messages"] = thread_msgs
            item["message_count"] = len(thread_msgs)
            enriched += 1

    logger.info(
        "Slack collection complete: %d items (%d enriched with thread context)",
        len(results),
        enriched,
    )
    return results
