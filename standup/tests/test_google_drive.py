"""Tests for google_drive.py."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from standup_tool.collectors import google_drive


def _make_event(summary: str, description: str = "", attachments: list | None = None) -> dict:
    event = {
        "summary": summary,
        "start": {"dateTime": "2026-04-25T10:00:00Z"},
        "description": description,
    }
    if attachments:
        event["attachments"] = attachments
    return event


def test_extract_file_ids_from_description():
    events = [
        _make_event(
            "Standup",
            description="Notes: https://docs.google.com/document/d/doc-alpha/edit",
        ),
    ]
    refs = google_drive._extract_file_ids(events)
    assert len(refs) == 1
    assert refs[0]["file_id"] == "doc-alpha"
    assert refs[0]["event_summary"] == "Standup"


def test_extract_file_ids_from_attachment():
    events = [
        _make_event("Review", attachments=[{"fileId": "att-xyz", "title": "Notes"}]),
    ]
    refs = google_drive._extract_file_ids(events)
    assert len(refs) == 1
    assert refs[0]["file_id"] == "att-xyz"
    assert refs[0]["file_title"] == "Notes"


def test_extract_file_ids_drive_url():
    events = [
        _make_event("Sync", description="https://drive.google.com/file/d/drv-beta/view"),
    ]
    refs = google_drive._extract_file_ids(events)
    assert len(refs) == 1
    assert refs[0]["file_id"] == "drv-beta"


def test_extract_file_ids_no_links():
    events = [_make_event("sync", description="No links here")]
    refs = google_drive._extract_file_ids(events)
    assert refs == []


def test_extract_file_ids_dedup_in_description():
    events = [
        _make_event(
            "Meeting",
            description=(
                "Agenda: https://docs.google.com/document/d/same-id/edit "
                "and also https://docs.google.com/document/d/same-id/preview"
            ),
        ),
    ]
    refs = google_drive._extract_file_ids(events)
    assert len(refs) == 1


@patch("standup_tool.collectors.google_drive.get_credentials")
@patch("standup_tool.collectors.google_drive.build")
def test_collect_deduplicates_files(mock_build, mock_creds):
    mock_creds.return_value = MagicMock()
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.files().get().execute.return_value = {
        "id": "abc",
        "name": "Notes",
        "mimeType": "application/pdf",
        "webViewLink": "https://drive.google.com/file/d/abc/view",
    }
    mock_service.about().get().execute.return_value = {"user": {"emailAddress": "ada@example.com"}}
    mock_service.files().list().execute.return_value = {"files": [], "nextPageToken": None}

    events = [
        _make_event("Meeting 1", description="https://docs.google.com/document/d/abc/edit"),
        _make_event("Meeting 2", description="https://docs.google.com/document/d/abc/edit"),
    ]
    result = google_drive.collect(events, date(2026, 4, 24), date(2026, 4, 26))
    assert len(result) == 1
    assert result[0]["file_id"] == "abc"


@patch("standup_tool.collectors.google_drive.get_credentials")
@patch("standup_tool.collectors.google_drive.build")
def test_collect_returns_empty_for_no_events(mock_build, mock_creds):
    mock_creds.return_value = MagicMock()
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.about().get().execute.return_value = {"user": {"emailAddress": "ada@example.com"}}
    mock_service.files().list().execute.return_value = {"files": [], "nextPageToken": None}
    result = google_drive.collect([], date(2026, 4, 24), date(2026, 4, 26))
    assert result == []


def test_fetch_comments_returns_comments_with_replies():
    service = MagicMock()
    service.comments().list().execute.return_value = {
        "comments": [
            {
                "id": "c1",
                "content": "Please update the estimate",
                "author": {"displayName": "Ada Example", "emailAddress": "ada@example.com"},
                "createdTime": "2026-04-29T10:00:00Z",
                "resolved": False,
                "replies": [
                    {
                        "content": "Done",
                        "author": {"displayName": "Bo Sample", "emailAddress": "bo@example.com"},
                        "createdTime": "2026-04-29T11:00:00Z",
                    }
                ],
            }
        ],
    }
    result = google_drive._fetch_comments(service, "file-123")
    assert len(result) == 1
    assert result[0]["author"] == "Ada Example"
    assert result[0]["author_email"] == "ada@example.com"
    assert result[0]["content"] == "Please update the estimate"
    assert result[0]["resolved"] is False
    assert len(result[0]["replies"]) == 1
    assert result[0]["replies"][0]["author"] == "Bo Sample"
    assert result[0]["replies"][0]["content"] == "Done"


def test_fetch_comments_returns_empty_on_non_fatal_item_error():
    service = MagicMock()
    service.comments().list().execute.side_effect = Exception("404 File not found")
    result = google_drive._fetch_comments(service, "file-123")
    assert result == []


def test_fetch_comments_propagates_forbidden_error():
    service = MagicMock()
    service.comments().list().execute.side_effect = Exception("403 Forbidden")
    with pytest.raises(Exception, match="403 Forbidden"):
        google_drive._fetch_comments(service, "file-123")


def test_fetch_comments_passes_start_modified_time():
    service = MagicMock()
    service.comments().list().execute.return_value = {"comments": []}
    google_drive._fetch_comments(service, "file-123", since=date(2026, 4, 30))
    call_kwargs = service.comments().list.call_args[1]
    assert call_kwargs["startModifiedTime"] == "2026-04-30T00:00:00Z"


def test_build_result_includes_comments_for_spreadsheet():
    service = MagicMock()
    service.comments().list().execute.return_value = {
        "comments": [
            {
                "id": "c1",
                "content": "Check this",
                "author": {"displayName": "Ada Example"},
                "createdTime": "2026-04-29T09:00:00Z",
                "resolved": True,
                "replies": [],
            }
        ],
    }
    meta = {
        "id": "sheet-1",
        "name": "Roadmap",
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "webViewLink": "https://docs.google.com/spreadsheets/d/sheet-1/edit",
        "modifiedTime": "2026-04-29T12:00:00Z",
    }
    result = google_drive._build_result(meta, service, "meeting")
    assert "comments" in result
    assert len(result["comments"]) == 1
    assert result["comments"][0]["author"] == "Ada Example"
    assert result["comments"][0]["resolved"] is True


def test_build_result_skips_comments_for_plain_text():
    service = MagicMock()
    meta = {
        "id": "txt-1",
        "name": "notes.txt",
        "mimeType": "text/plain",
        "webViewLink": "https://drive.google.com/file/d/txt-1/view",
        "modifiedTime": "2026-04-29T12:00:00Z",
    }
    result = google_drive._build_result(meta, service, "activity")
    assert result["comments"] == []
    service.comments().list.assert_not_called()


@patch("standup_tool.collectors.google_drive._download_text")
def test_build_result_skips_download_when_candownload_false(mock_dl):
    service = MagicMock()
    meta = {
        "id": "chat-1",
        "name": "notes.txt",
        "mimeType": "text/plain",
        "webViewLink": "https://drive.google.com/file/d/chat-1/view",
        "modifiedTime": "2026-04-29T12:00:00Z",
        "capabilities": {"canDownload": False},
    }
    result = google_drive._build_result(meta, service, "meeting")
    assert result["text_preview"] is None
    mock_dl.assert_not_called()


@patch("standup_tool.collectors.google_drive._export_text")
def test_build_result_skips_export_when_candownload_false(mock_export):
    service = MagicMock()
    service.comments().list().execute.return_value = {"comments": []}
    meta = {
        "id": "doc-1",
        "name": "notes",
        "mimeType": "application/vnd.google-apps.document",
        "webViewLink": "https://docs.google.com/document/d/doc-1/edit",
        "modifiedTime": "2026-04-29T12:00:00Z",
        "capabilities": {"canDownload": False},
    }
    result = google_drive._build_result(meta, service, "meeting")
    assert result["text_preview"] is None
    mock_export.assert_not_called()


@patch("standup_tool.collectors.google_drive._download_text", return_value="hello")
def test_build_result_downloads_when_candownload_true(mock_dl):
    service = MagicMock()
    meta = {
        "id": "txt-2",
        "name": "notes.txt",
        "mimeType": "text/plain",
        "webViewLink": "https://drive.google.com/file/d/txt-2/view",
        "modifiedTime": "2026-04-29T12:00:00Z",
        "capabilities": {"canDownload": True},
    }
    result = google_drive._build_result(meta, service, "activity")
    mock_dl.assert_called_once_with(service, "txt-2")
    assert result["text_preview"] == "hello"


@patch("standup_tool.collectors.google_drive._download_text")
def test_build_result_skips_download_when_capabilities_absent(mock_dl):
    service = MagicMock()
    meta = {
        "id": "txt-3",
        "name": "notes.txt",
        "mimeType": "text/plain",
        "webViewLink": "https://drive.google.com/file/d/txt-3/view",
        "modifiedTime": "2026-04-29T12:00:00Z",
    }
    result = google_drive._build_result(meta, service, "activity")
    mock_dl.assert_not_called()
    assert result["text_preview"] is None


@patch("standup_tool.collectors.google_drive.get_credentials")
@patch("standup_tool.collectors.google_drive.build")
def test_collect_passes_supports_all_drives(mock_build, mock_creds):
    mock_creds.return_value = MagicMock()
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.files().get().execute.return_value = {
        "id": "shared-1",
        "name": "notes",
        "mimeType": "application/pdf",
        "webViewLink": "https://drive.google.com/file/d/shared-1/view",
    }
    mock_service.about().get().execute.return_value = {"user": {"emailAddress": "ada@example.com"}}
    mock_service.files().list().execute.return_value = {"files": []}

    events = [_make_event("Meeting", description="https://drive.google.com/file/d/shared-1/view")]
    mock_service.files().get.reset_mock()
    mock_service.files().list.reset_mock()
    google_drive.collect(events, date(2026, 4, 24), date(2026, 4, 26))

    get_calls = mock_service.files().get.call_args_list
    assert any(call.kwargs.get("supportsAllDrives") is True for call in get_calls)
    list_calls = mock_service.files().list.call_args_list
    assert any(call.kwargs.get("supportsAllDrives") is True for call in list_calls)
    assert any(call.kwargs.get("includeItemsFromAllDrives") is True for call in list_calls)
