"""Tests for google_calendar.py."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from standup_tool.collectors import google_calendar


def make_event(
    summary: str, start: str, end: str, attendees: list | None = None, organizer: str = ""
) -> dict:
    atts = attendees or []
    return {
        "summary": summary,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
        "attendees": [{"email": e} for e in atts],
        "organizer": {"email": organizer},
    }


def test_collect_propagates_api_error():
    mock_creds = MagicMock()
    mock_service = MagicMock()
    mock_service.events().list().execute.side_effect = Exception("API error")

    with (
        patch("standup_tool.collectors.google_calendar.get_credentials", return_value=mock_creds),
        patch("standup_tool.collectors.google_calendar.build", return_value=mock_service),
        pytest.raises(Exception, match="API error"),
    ):
        google_calendar.collect(date(2026, 4, 20), date(2026, 4, 23))


def test_collect_returns_structured_events():
    mock_creds = MagicMock()
    mock_service = MagicMock()
    raw_event = make_event(
        "Team standup",
        "2026-04-22T09:00:00Z",
        "2026-04-22T09:30:00Z",
        attendees=["ada@example.com", "bo@example.com"],
        organizer="ada@example.com",
    )
    mock_service.events().list().execute.return_value = {"items": [raw_event]}

    with (
        patch("standup_tool.collectors.google_calendar.get_credentials", return_value=mock_creds),
        patch("standup_tool.collectors.google_calendar.build", return_value=mock_service),
    ):
        result = google_calendar.collect(date(2026, 4, 20), date(2026, 4, 23))

    assert len(result) == 1
    assert result[0]["summary"] == "Team standup"
    assert result[0]["start"] == "2026-04-22T09:00:00Z"
    assert "ada@example.com" in result[0]["attendees"]
    assert result[0]["organizer"] == "ada@example.com"


def test_collect_detects_self_response_status():
    mock_creds = MagicMock()
    mock_service = MagicMock()
    raw_event = {
        "summary": "Review",
        "start": {"dateTime": "2026-04-22T10:00:00Z"},
        "end": {"dateTime": "2026-04-22T11:00:00Z"},
        "attendees": [
            {"email": "ada@example.com", "self": True, "responseStatus": "tentative"},
            {"email": "bo@example.com"},
        ],
        "organizer": {"email": "bo@example.com"},
    }
    mock_service.events().list().execute.return_value = {"items": [raw_event]}

    with (
        patch("standup_tool.collectors.google_calendar.get_credentials", return_value=mock_creds),
        patch("standup_tool.collectors.google_calendar.build", return_value=mock_service),
    ):
        result = google_calendar.collect(date(2026, 4, 20), date(2026, 4, 23))

    assert result[0]["response_status"] == "tentative"


def test_collect_returns_empty_list_when_no_events():
    mock_creds = MagicMock()
    mock_service = MagicMock()
    mock_service.events().list().execute.return_value = {"items": []}

    with (
        patch("standup_tool.collectors.google_calendar.get_credentials", return_value=mock_creds),
        patch("standup_tool.collectors.google_calendar.build", return_value=mock_service),
    ):
        result = google_calendar.collect(date(2026, 4, 20), date(2026, 4, 23))

    assert result == []


def test_collect_handles_all_day_events():
    mock_creds = MagicMock()
    mock_service = MagicMock()
    raw_event = {
        "summary": "holiday",
        "start": {"date": "2026-04-25"},
        "end": {"date": "2026-04-26"},
        "attendees": [],
        "organizer": {"email": "bo@example.com"},
    }
    mock_service.events().list().execute.return_value = {"items": [raw_event]}

    with (
        patch("standup_tool.collectors.google_calendar.get_credentials", return_value=mock_creds),
        patch("standup_tool.collectors.google_calendar.build", return_value=mock_service),
    ):
        result = google_calendar.collect(date(2026, 4, 25), date(2026, 4, 25))

    assert len(result) == 1
    assert result[0]["summary"] == "holiday"
    assert result[0]["start"] == "2026-04-25"
    assert result[0]["end"] == "2026-04-26"
    assert result[0]["organizer"] == "bo@example.com"
