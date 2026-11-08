"""Tests for gmail.py."""

import base64
from datetime import date
from unittest.mock import MagicMock, patch

from standup_tool.collectors import gmail


def _setup_batch_service(mock_service, raw_messages: list[dict]) -> None:
    """Configure mock_service to simulate BatchHttpRequest for gmail batch fetch."""
    mock_service.users().messages().list().execute.return_value = {
        "messages": [{"id": f"item_{i}"} for i in range(len(raw_messages))],
        "nextPageToken": None,
    }

    def make_batch(callback=None):
        batch = MagicMock()
        requests: list = []

        def orig_add(req):
            requests.append(req)

        batch.add = orig_add

        def do_execute():
            for i in range(len(requests)):
                if i < len(raw_messages):
                    callback(str(i), raw_messages[i], None)

        batch.execute = do_execute
        return batch

    mock_service.new_batch_http_request = make_batch


def _encode(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def make_raw_msg(
    subject: str = "Test",
    from_addr: str = "bo@example.com",
    to_addr: str = "ada@example.com",
    body: str = "Hello world",
) -> dict:
    return {
        "id": "item_one",
        "threadId": "thread_one",
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": from_addr},
                {"name": "To", "value": to_addr},
                {"name": "Date", "value": "Wed, 22 Apr 2026 10:00:00 +0000"},
            ],
            "body": {"data": _encode(body)},
        },
    }


def test_gmail_date_format():
    result = gmail._gmail_date(date(2026, 4, 22))
    assert result == "2026/04/22"


def test_collect_returns_empty_on_api_error():
    mock_creds = MagicMock()
    mock_service = MagicMock()
    mock_service.users().messages().list().execute.side_effect = Exception("API error")

    with (
        patch("standup_tool.collectors.gmail.get_credentials", return_value=mock_creds),
        patch("standup_tool.collectors.gmail.build", return_value=mock_service),
    ):
        result = gmail.collect(date(2026, 4, 20), date(2026, 4, 23))

    assert result == []


def test_collect_returns_parsed_messages():
    mock_creds = MagicMock()
    mock_service = MagicMock()
    raw = make_raw_msg(subject="Hello", from_addr="bo@example.com", body="Body text")
    _setup_batch_service(mock_service, [raw])

    with (
        patch("standup_tool.collectors.gmail.get_credentials", return_value=mock_creds),
        patch("standup_tool.collectors.gmail.build", return_value=mock_service),
    ):
        result = gmail.collect(date(2026, 4, 20), date(2026, 4, 23), my_email="ada@example.com")

    assert len(result) == 1
    assert result[0]["subject"] == "Hello"
    assert result[0]["from"] == "bo@example.com"
    assert result[0]["direction"] == "received"
    assert "Body text" in result[0]["body_snippet"]


def test_collect_sets_direction_sent_when_my_email_in_from():
    mock_creds = MagicMock()
    mock_service = MagicMock()
    raw = make_raw_msg(from_addr="ada@example.com", to_addr="bo@example.com")
    _setup_batch_service(mock_service, [raw])

    with (
        patch("standup_tool.collectors.gmail.get_credentials", return_value=mock_creds),
        patch("standup_tool.collectors.gmail.build", return_value=mock_service),
    ):
        result = gmail.collect(date(2026, 4, 20), date(2026, 4, 23), my_email="ada@example.com")

    assert result[0]["direction"] == "sent"


def test_collect_direction_no_false_positive_on_partial_match():
    """'ada@example.com' must not match 'notada@example.com' as sender."""
    mock_creds = MagicMock()
    mock_service = MagicMock()
    raw = make_raw_msg(from_addr="notada@example.com", to_addr="ada@example.com")
    _setup_batch_service(mock_service, [raw])

    with (
        patch("standup_tool.collectors.gmail.get_credentials", return_value=mock_creds),
        patch("standup_tool.collectors.gmail.build", return_value=mock_service),
    ):
        result = gmail.collect(date(2026, 4, 20), date(2026, 4, 23), my_email="ada@example.com")

    assert result[0]["direction"] == "received"


def test_collect_direction_handles_display_name_format():
    mock_creds = MagicMock()
    mock_service = MagicMock()
    raw = make_raw_msg(from_addr="Ada Example <ada@example.com>", to_addr="bo@example.com")
    _setup_batch_service(mock_service, [raw])

    with (
        patch("standup_tool.collectors.gmail.get_credentials", return_value=mock_creds),
        patch("standup_tool.collectors.gmail.build", return_value=mock_service),
    ):
        result = gmail.collect(date(2026, 4, 20), date(2026, 4, 23), my_email="ada@example.com")

    assert result[0]["direction"] == "sent"


def test_collect_truncates_body_to_3000_chars():
    long_body = "x" * 5000
    mock_creds = MagicMock()
    mock_service = MagicMock()
    raw = make_raw_msg(body=long_body)
    _setup_batch_service(mock_service, [raw])

    with (
        patch("standup_tool.collectors.gmail.get_credentials", return_value=mock_creds),
        patch("standup_tool.collectors.gmail.build", return_value=mock_service),
    ):
        result = gmail.collect(date(2026, 4, 20), date(2026, 4, 23))

    assert len(result[0]["body_snippet"]) <= 3000


def test_collect_handles_batch_chunking_over_20_messages():
    mock_creds = MagicMock()
    mock_service = MagicMock()
    raw_msgs = [make_raw_msg(subject=f"Email {i}") for i in range(25)]

    mock_service.users().messages().list().execute.return_value = {
        "messages": [{"id": f"item_{i}"} for i in range(25)],
        "nextPageToken": None,
    }

    delivered = [0]
    batch_call_count = [0]

    def make_batch(callback=None):
        batch_call_count[0] += 1
        batch = MagicMock()
        reqs: list = []

        def add(req):
            reqs.append(req)

        def execute():
            for _ in reqs:
                if delivered[0] < len(raw_msgs):
                    callback(str(delivered[0]), raw_msgs[delivered[0]], None)
                    delivered[0] += 1

        batch.add = add
        batch.execute = execute
        return batch

    mock_service.new_batch_http_request = make_batch

    with (
        patch("standup_tool.collectors.gmail.get_credentials", return_value=mock_creds),
        patch("standup_tool.collectors.gmail.build", return_value=mock_service),
    ):
        result = gmail.collect(date(2026, 4, 20), date(2026, 4, 23))

    assert len(result) == 25
    assert batch_call_count[0] == 2
    subjects = {r["subject"] for r in result}
    assert "Email 0" in subjects
    assert "Email 24" in subjects


def test_collect_batch_partial_failure_skips_failed_message():
    mock_creds = MagicMock()
    mock_service = MagicMock()

    good_msg = make_raw_msg(subject="Good Message", from_addr="bo@example.com")

    mock_service.users().messages().list().execute.return_value = {
        "messages": [{"id": "item_a"}, {"id": "item_b"}],
        "nextPageToken": None,
    }

    def make_batch(callback=None):
        batch = MagicMock()
        requests: list = []

        def add(req):
            requests.append(req)

        def execute():
            callback("0", good_msg, None)
            callback("1", None, Exception("fetch failed"))

        batch.add = add
        batch.execute = execute
        return batch

    mock_service.new_batch_http_request = make_batch

    with (
        patch("standup_tool.collectors.gmail.get_credentials", return_value=mock_creds),
        patch("standup_tool.collectors.gmail.build", return_value=mock_service),
    ):
        result = gmail.collect(date(2026, 4, 20), date(2026, 4, 23), my_email="ada@example.com")

    assert len(result) == 1
    assert result[0]["subject"] == "Good Message"
