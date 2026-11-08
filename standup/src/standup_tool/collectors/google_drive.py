"""Collect Google Drive documents linked to calendar events.

Extracts meeting notes, transcripts, and Gemini summaries from
Google Docs and plain text files attached to calendar events.
"""

from __future__ import annotations

import concurrent.futures
import io
import logging
import re
import threading
from datetime import date
from pathlib import Path

import google_auth_httplib2
import httplib2
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from standup_tool.collectors.google_auth import get_credentials

logger = logging.getLogger(__name__)

_DOC_RE = re.compile(
    r"https://docs\.google\.com/(?:document|spreadsheets|presentation)/d/([a-zA-Z0-9_-]+)"
)
_DRIVE_RE = re.compile(r"https://drive\.google\.com/(?:file/d/|open\?id=)([a-zA-Z0-9_-]+)")

_HTTP_TIMEOUT = 30  # seconds, per Drive API request

_thread_local = threading.local()


def _is_fatal_drive_error(exc: BaseException) -> bool:
    """Return True if *exc* is an authentication, HTTP 401/403, connection, or timeout error."""
    if isinstance(exc, PermissionError | ConnectionError | TimeoutError):
        return True
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "resp", None), "status", None
    )
    if status in (401, 403):
        return True
    cls_name = type(exc).__name__.lower()
    if "auth" in cls_name or "refresherror" in cls_name:
        return True
    msg = str(exc).lower()
    return any(
        tok in msg
        for tok in (
            "401",
            "403",
            "unauthorized",
            "forbidden",
            "unauthenticated",
            "invalid_grant",
            "timed out",
            "timeout",
            "connection",
        )
    )


def _get_thread_service(config_dir: Path | None = None):
    """Get or create a thread-local Drive API service object."""
    if not hasattr(_thread_local, "service"):
        creds = get_credentials(config_dir)
        authed_http = google_auth_httplib2.AuthorizedHttp(
            creds, http=httplib2.Http(timeout=_HTTP_TIMEOUT)
        )
        _thread_local.service = build("drive", "v3", http=authed_http)
    return _thread_local.service


def _extract_file_ids(calendar_events: list[dict]) -> list[dict]:
    """Extract Drive file references from calendar event descriptions and attachments."""
    refs: list[dict] = []
    for event in calendar_events:
        summary = event.get("summary", "")
        start = event.get("start", "")
        event_date = (
            start if isinstance(start, str) else start.get("dateTime", start.get("date", ""))
        )

        description = event.get("description", "")
        file_ids: set[str] = set()
        file_ids.update(_DOC_RE.findall(description))
        file_ids.update(_DRIVE_RE.findall(description))
        for fid in file_ids:
            refs.append({"file_id": fid, "event_summary": summary, "event_date": event_date})

        for att in event.get("attachments", []):
            if "fileId" in att:
                refs.append(
                    {
                        "file_id": att["fileId"],
                        "file_title": att.get("title", ""),
                        "event_summary": summary,
                        "event_date": event_date,
                    }
                )

    return refs


def _export_text(service, file_id: str) -> str | None:
    """Export a Google Doc as plain text."""
    try:
        request = service.files().export_media(fileId=file_id, mimeType="text/plain")
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue().decode("utf-8", errors="replace")
    # tolerant: skip text export for single Google Doc when non-fatal export error occurs
    except Exception as exc:
        if _is_fatal_drive_error(exc):
            raise
        logger.debug("Failed to export doc %s: %s", file_id, exc)
        logger.warning("Failed to export doc %s: %s", file_id, exc)
        return None


def _download_text(service, file_id: str) -> str | None:
    """Download a plain text file from Drive."""
    try:
        request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue().decode("utf-8", errors="replace")
    # tolerant: skip text download for single Drive file when non-fatal download error occurs
    except Exception as exc:
        if _is_fatal_drive_error(exc):
            raise
        logger.debug("Failed to download file %s: %s", file_id, exc)
        logger.warning("Failed to download file %s: %s", file_id, exc)
        return None


def _fetch_comments(service, file_id: str, since: date | None = None) -> list[dict]:
    """Fetch comment threads on a Drive file, including replies."""
    try:
        comments = []
        page_token = None
        kwargs: dict = {
            "fileId": file_id,
            "fields": (
                "nextPageToken,comments(id,content,author(displayName,emailAddress),"
                "createdTime,resolved,replies(content,author(displayName,emailAddress),createdTime))"
            ),
            "pageSize": 100,
        }
        if since:
            kwargs["startModifiedTime"] = f"{since.isoformat()}T00:00:00Z"
        while True:
            if page_token:
                kwargs["pageToken"] = page_token
            resp = service.comments().list(**kwargs).execute()
            for c in resp.get("comments", []):
                entry = {
                    "author": c.get("author", {}).get("displayName", ""),
                    "author_email": c.get("author", {}).get("emailAddress", ""),
                    "content": c.get("content", ""),
                    "created": c.get("createdTime", ""),
                    "resolved": c.get("resolved", False),
                    "replies": [
                        {
                            "author": r.get("author", {}).get("displayName", ""),
                            "author_email": r.get("author", {}).get("emailAddress", ""),
                            "content": r.get("content", ""),
                            "created": r.get("createdTime", ""),
                        }
                        for r in c.get("replies", [])
                    ],
                }
                comments.append(entry)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return comments
    # tolerant: skip comments for single Drive file when non-fatal comment fetch error occurs
    except Exception as exc:
        if _is_fatal_drive_error(exc):
            raise
        logger.debug("Failed to fetch comments for %s: %s", file_id, exc)
        logger.warning("Failed to fetch comments for %s: %s", file_id, exc)
        return []


_COMMENTABLE_MIMES = frozenset(
    {
        "application/vnd.google-apps.spreadsheet",
        "application/vnd.google-apps.document",
        "application/vnd.google-apps.presentation",
    }
)


def _build_result(
    meta: dict,
    service,
    source: str,
    event_summary: str = "",
    event_date: str = "",
    since: date | None = None,
) -> dict:
    """Build a result dict from Drive file metadata, optionally exporting text content."""
    fid = meta.get("id", "")
    mime = meta.get("mimeType", "")
    text_preview = None

    can_download = meta.get("capabilities", {}).get("canDownload", False)

    if mime == "application/vnd.google-apps.document":
        if can_download:
            content = _export_text(service, fid)
            if content:
                text_preview = content[:2000]
        else:
            logger.info(
                "Skipping export of %s — downloads restricted "
                "(common for Meet-generated artifacts)",
                fid,
            )
    elif mime == "text/plain":
        if can_download:
            content = _download_text(service, fid)
            if content:
                text_preview = content[:2000]
        else:
            logger.info(
                "Skipping download of %s — downloads restricted "
                "(common for Meet-generated artifacts)",
                fid,
            )

    comments = _fetch_comments(service, fid, since) if mime in _COMMENTABLE_MIMES else []

    return {
        "source": source,
        "event_summary": event_summary,
        "event_date": event_date,
        "file_name": meta.get("name", ""),
        "file_id": fid,
        "mime_type": mime,
        "modified_time": meta.get("modifiedTime", ""),
        "link": meta.get("webViewLink", ""),
        "text_preview": text_preview,
        "comments": comments,
    }


_META_FIELDS = (
    "id,name,mimeType,webViewLink,modifiedTime,lastModifyingUser,capabilities(canDownload)"
)
_SKIP_MIMES = frozenset(
    {
        "application/vnd.google-apps.folder",
        "application/vnd.google-apps.shortcut",
        "application/vnd.google-apps.form",
    }
)


def _process_meeting_ref(ref: dict, since_date: date, config_dir: Path | None) -> dict | None:
    service = _get_thread_service(config_dir)
    fid = ref["file_id"]
    try:
        meta = (
            service.files().get(fileId=fid, fields=_META_FIELDS, supportsAllDrives=True).execute()
        )
        return _build_result(
            meta,
            service,
            "meeting",
            ref.get("event_summary", ""),
            ref.get("event_date", ""),
            since=since_date,
        )
    # tolerant: skip single calendar-linked Drive file when non-fatal metadata fetch fails
    except Exception as exc:
        if _is_fatal_drive_error(exc):
            raise
        logger.debug("Failed to get metadata for %s: %s", fid, exc)
        logger.warning("Failed to get metadata for %s: %s", fid, exc)
        return None


def _process_activity_item(item: dict, since_date: date, config_dir: Path | None) -> dict | None:
    service = _get_thread_service(config_dir)
    try:
        return _build_result(item, service, "activity", since=since_date)
    # tolerant: skip single modified Drive file when non-fatal item processing fails
    except Exception as exc:
        if _is_fatal_drive_error(exc):
            raise
        logger.debug("Failed to process activity file %s: %s", item.get("id"), exc)
        logger.warning("Failed to process activity file %s: %s", item.get("id"), exc)
        return None


def collect(
    calendar_events: list[dict],
    since_date: date,
    until_date: date,
    config_dir: Path | None = None,
) -> list[dict]:
    """Collect Drive documents from two sources:

    1. Files linked in calendar event descriptions/attachments (meeting notes, transcripts)
    2. Files the user modified during the date range (docs, sheets, slides)
    Returns list of dicts with source="meeting" or source="activity".
    """
    creds = get_credentials(config_dir)
    authed_http = google_auth_httplib2.AuthorizedHttp(
        creds, http=httplib2.Http(timeout=_HTTP_TIMEOUT)
    )
    service = build("drive", "v3", http=authed_http)

    seen: set[str] = set()
    results: list[dict] = []

    # --- Source 1: Calendar-linked files ---
    refs = _extract_file_ids(calendar_events)
    logger.info("Found %d Drive file references in calendar events", len(refs))

    unique_refs = []
    for ref in refs:
        if ref["file_id"] not in seen:
            seen.add(ref["file_id"])
            unique_refs.append(ref)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = []

        for ref in unique_refs:
            futures.append(executor.submit(_process_meeting_ref, ref, since_date, config_dir))

        # --- Source 2: Files the user edited in the date range ---
        since_rfc = f"{since_date.isoformat()}T00:00:00"
        until_rfc = f"{until_date.isoformat()}T23:59:59"
        query = (
            f"modifiedTime >= '{since_rfc}' and modifiedTime <= '{until_rfc}' and trashed = false"
        )

        try:
            # Get the authenticated user's email for filtering
            about = service.about().get(fields="user(emailAddress)").execute()
            my_email = about.get("user", {}).get("emailAddress", "")

            activity_items = []
            page_token = None
            while True:
                resp = (
                    service.files()
                    .list(
                        q=query,
                        fields=f"nextPageToken,files({_META_FIELDS})",
                        pageSize=100,
                        pageToken=page_token,
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True,
                    )
                    .execute()
                )
                for item in resp.get("files", []):
                    fid = item.get("id", "")
                    if fid in seen:
                        continue
                    if item.get("mimeType", "") in _SKIP_MIMES:
                        continue
                    # Only include files the user actually modified
                    last_modifier = item.get("lastModifyingUser", {}).get("emailAddress", "")
                    if my_email and last_modifier != my_email:
                        continue
                    seen.add(fid)
                    activity_items.append(item)
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break

            for item in activity_items:
                futures.append(
                    executor.submit(_process_activity_item, item, since_date, config_dir)
                )

            for fut in concurrent.futures.as_completed(futures):
                res = fut.result()
                if res:
                    results.append(res)
        # fatal
        except Exception:
            raise

    meeting_count = sum(1 for r in results if r["source"] == "meeting")
    activity_count = sum(1 for r in results if r["source"] == "activity")
    logger.info(
        "Collected %d Drive documents (%d meeting, %d activity)",
        len(results),
        meeting_count,
        activity_count,
    )
    return results
