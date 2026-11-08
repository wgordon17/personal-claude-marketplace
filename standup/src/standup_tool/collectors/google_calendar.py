"""Collect calendar events from Google Calendar API."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from googleapiclient.discovery import build

from standup_tool.collectors.google_auth import get_credentials


def collect(since_date: date, until_date: date, config_dir: Path | None = None) -> list[dict]:
    """Return calendar events between since_date and until_date (inclusive).

    Each dict: {summary, start, end, attendees, organizer, response_status}
    """
    creds = get_credentials(config_dir)
    service = build("calendar", "v3", credentials=creds)

    time_min = f"{since_date.isoformat()}T00:00:00Z"
    time_max = f"{until_date.isoformat()}T23:59:59Z"

    try:
        result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                maxResults=2500,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
    except Exception as e:
        logging.warning("Google Calendar API error: %s", e)
        return []

    events = []
    for item in result.get("items", []):
        start = item.get("start", {})
        end = item.get("end", {})
        organizer = item.get("organizer", {})
        attendees = item.get("attendees", [])

        # Determine my response status
        response_status = "accepted"
        for att in attendees:
            if att.get("self"):
                response_status = att.get("responseStatus", "accepted")
                break

        event = {
            "summary": item.get("summary", ""),
            "start": start.get("dateTime") or start.get("date", ""),
            "end": end.get("dateTime") or end.get("date", ""),
            "attendees": [a.get("email", "") for a in attendees],
            "organizer": organizer.get("email", ""),
            "response_status": response_status,
        }
        description = item.get("description", "")
        if description:
            event["description"] = description
        attachments = item.get("attachments", [])
        if attachments:
            event["attachments"] = attachments
        events.append(event)

    return events
