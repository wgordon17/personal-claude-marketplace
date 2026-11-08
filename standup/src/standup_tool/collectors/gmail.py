"""Collect emails from Gmail API."""

from __future__ import annotations

import base64
import logging
import re
from datetime import date, timedelta
from pathlib import Path

from googleapiclient.discovery import build

from standup_tool.collectors.google_auth import get_credentials

_BODY_LIMIT = 3000

# Matches the email address inside angle brackets: "Name <addr@example.com>"
_ANGLE_RE = re.compile(r"<([^>]+)>")


def _gmail_date(d: date) -> str:
    """Format date as YYYY/MM/DD for Gmail query syntax."""
    return d.strftime("%Y/%m/%d")


def _extract_email_address(header_value: str) -> str:
    """Return the bare email address from a From/To header value.

    Handles both 'addr@example.com' and 'Name <addr@example.com>' formats.
    Comparison must be exact (not substring) to avoid false positives.
    """
    m = _ANGLE_RE.search(header_value)
    return m.group(1).strip().lower() if m else header_value.strip().lower()


def _extract_body(payload: dict) -> str:
    """Extract plain-text body from a Gmail message payload, handling multipart and simple MIME."""
    body = ""
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
                    break
    else:
        data = payload.get("body", {}).get("data", "")
        if data:
            body = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
    return body[:_BODY_LIMIT]


def _parse_message(msg: dict, my_email: str) -> dict:
    headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
    from_addr = headers.get("From", "")
    to_addr = headers.get("To", "")
    # Compare exact email address, not substring, to avoid false positives
    # e.g. "bob@x.com" should not match "notbob@x.com"
    from_email = _extract_email_address(from_addr)
    direction = "sent" if my_email and from_email == my_email.lower() else "received"
    return {
        "subject": headers.get("Subject", ""),
        "from": from_addr,
        "to": to_addr,
        "date": headers.get("Date", ""),
        "body_snippet": _extract_body(msg["payload"]),
        "direction": direction,
    }


def _search(service, query: str, max_results: int = 100) -> list[dict]:
    """Fetch messages matching query.

    Uses messages.list() to retrieve IDs, then batches messages.get(format='full')
    calls via BatchHttpRequest (up to 100 per batch) to retrieve headers and body
    in a single HTTP round trip instead of one per message.
    """
    msg_ids: list[str] = []
    page_token = None
    while len(msg_ids) < max_results:
        res = (
            service.users()
            .messages()
            .list(
                userId="me",
                q=query,
                maxResults=min(100, max_results - len(msg_ids)),
                pageToken=page_token,
            )
            .execute()
        )
        for item in res.get("messages", []):
            msg_ids.append(item["id"])
            if len(msg_ids) >= max_results:
                break
        page_token = res.get("nextPageToken")
        if not page_token:
            break

    if not msg_ids:
        return []

    msgs: list[dict] = []

    def _callback(_request_id: str, response: dict, exception: Exception | None) -> None:
        if exception:
            logging.warning("Failed to fetch message: %s", exception)
        elif response:
            msgs.append(response)

    # Gmail limits concurrent requests per user — chunk batches to avoid 429s
    chunk_size = 20
    for i in range(0, len(msg_ids), chunk_size):
        chunk = msg_ids[i : i + chunk_size]
        batch = service.new_batch_http_request(callback=_callback)
        for mid in chunk:
            batch.add(service.users().messages().get(userId="me", id=mid, format="full"))
        batch.execute()

    return msgs


def collect(
    since_date: date, until_date: date, my_email: str = "", config_dir: Path | None = None
) -> list[dict]:
    """Return emails sent/received between since_date and until_date.

    Each dict: {subject, from, to, date, body_snippet, direction}
    Gmail date format: YYYY/MM/DD (not YYYY-MM-DD).
    """
    creds = get_credentials(config_dir)
    service = build("gmail", "v1", credentials=creds)

    # Gmail after:/before: are exclusive — adjust by 1 day to include endpoints
    after = _gmail_date(since_date - timedelta(days=1))
    before = _gmail_date(until_date + timedelta(days=1))
    query = f"after:{after} before:{before}"

    try:
        raw_msgs = _search(service, query, max_results=100)
    # fatal
    except Exception:
        raise

    return [_parse_message(m, my_email) for m in raw_msgs]
