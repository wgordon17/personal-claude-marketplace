"""Tests for Slack activity collector."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from standup_tool.collectors import slack_activity
from standup_tool.config import SlackConfig
from standup_tool.slack_auth import SlackCredentials

_XOXC_TEST = "xox" + "c-client-token"
_XOXD_TEST = "xox" + "d-session-cookie"
_SLACK_CFG = SlackConfig(
    enabled=True,
    workspace_url="https://acme.slack.com",
    channels=("eng", "ops"),
    standup_channels=("eng", "ops"),
    self_dm_name=None,
)


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    path = tmp_path / "slack-token"
    path.write_text(_XOXD_TEST + "\n", encoding="utf-8")
    return path


def _mock_search_responses():
    """Build a side_effect function for search_messages that returns canned results."""

    def _has_positive(query: str, token: str) -> bool:
        idx = query.find(token)
        while idx != -1:
            if idx == 0 or query[idx - 1] in (" ", "\t"):
                return True
            idx = query.find(token, idx + 1)
        return False

    def side_effect(session, query, count=100):
        if _has_positive(query, "in:#eng"):
            return [
                {
                    "username": "bo-sample",
                    "text": "working on ACME-101",
                    "ts": "1714000000.000100",
                    "channel": {"name": "eng"},
                    "permalink": "https://acme.slack.com/archives/CEXAMPLE01/p1",
                },
                {
                    "username": "ada-example",
                    "text": "sounds good",
                    "ts": "1714000000.000200",
                    "channel": {"name": "eng"},
                    "permalink": "https://acme.slack.com/archives/CEXAMPLE01/p2",
                },
            ]
        if _has_positive(query, "in:#ops"):
            return [
                {
                    "username": "ada-example",
                    "text": "widget quota provisioning ready for review",
                    "ts": "1714000000.000300",
                    "channel": {"name": "ops"},
                    "permalink": "https://acme.slack.com/archives/CEXAMPLE02/p3",
                },
            ]
        if _has_positive(query, "is:dm"):
            return [
                {
                    "username": "ada-example",
                    "text": "hey, question about widgets",
                    "ts": "1714000000.000400",
                    "channel": {"name": "bo-sample"},
                    "permalink": "https://acme.slack.com/archives/DEXAMPLE01/p4",
                },
            ]
        if _has_positive(query, "to:me"):
            return [
                {
                    "username": "bo-sample",
                    "text": "hey @ada-example check this out",
                    "ts": "1714000000.000500",
                    "channel": {"name": "general"},
                    "permalink": "https://acme.slack.com/archives/CEXAMPLE03/p5",
                },
                {
                    "username": "bo-sample",
                    "text": "cc @ada-example",
                    "ts": "1714000000.000600",
                    "channel": {"name": "eng"},
                    "permalink": "https://acme.slack.com/archives/CEXAMPLE01/p6",
                },
                {
                    "username": "ops-bot",
                    "text": "alert @ada-example capacity 90%",
                    "ts": "1714000000.000700",
                    "channel": {"name": "ops"},
                    "permalink": "https://acme.slack.com/archives/CEXAMPLE02/p7",
                },
            ]
        if _has_positive(query, "from:me"):
            return [
                {
                    "username": "ada-example",
                    "text": "thoughts on this approach?",
                    "ts": "1714000000.000800",
                    "channel": {"name": "random"},
                    "permalink": "https://acme.slack.com/archives/CEXAMPLE04/p8",
                },
            ]
        return []

    return side_effect


def _mock_auth(*_args, **_kwargs):
    """Mock auth.test response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "user_id": "UEXAMPLE01", "user": "ada-example"}
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


@patch(
    "standup_tool.collectors.slack_activity.get_slack_credentials",
    return_value=SlackCredentials(token=_XOXC_TEST, cookie=_XOXD_TEST),
)
@patch("requests.Session.get", side_effect=_mock_auth)
@patch.object(slack_activity, "search_messages")
def test_collect_returns_all_source_types(mock_search, _mock_get, _mock_creds, token_file):
    mock_search.side_effect = _mock_search_responses()
    results = slack_activity.collect(
        date(2026, 4, 24),
        date(2026, 4, 24),
        slack_cfg=_SLACK_CFG,
        token_path=token_file,
    )

    sources = {r["source"] for r in results}
    assert "channel" in sources
    assert "dm" in sources
    assert "mention" in sources
    assert "participation" in sources


@patch(
    "standup_tool.collectors.slack_activity.get_slack_credentials",
    return_value=SlackCredentials(token=_XOXC_TEST, cookie=_XOXD_TEST),
)
@patch("requests.Session.get", side_effect=_mock_auth)
@patch.object(slack_activity, "search_messages")
def test_collect_finds_target_channels(mock_search, _mock_get, _mock_creds, token_file):
    mock_search.side_effect = _mock_search_responses()
    results = slack_activity.collect(
        date(2026, 4, 24),
        date(2026, 4, 24),
        slack_cfg=_SLACK_CFG,
        token_path=token_file,
    )

    eng_results = [r for r in results if r["channel"] == "#eng"]
    assert len(eng_results) == 2
    ops_results = [r for r in results if r["channel"] == "#ops"]
    assert len(ops_results) == 1


@patch(
    "standup_tool.collectors.slack_activity.get_slack_credentials",
    return_value=SlackCredentials(token=_XOXC_TEST, cookie=_XOXD_TEST),
)
@patch("requests.Session.get", side_effect=_mock_auth)
@patch.object(slack_activity, "search_messages")
def test_collect_includes_dm_messages(mock_search, _mock_get, _mock_creds, token_file):
    mock_search.side_effect = _mock_search_responses()
    results = slack_activity.collect(
        date(2026, 4, 24),
        date(2026, 4, 24),
        slack_cfg=_SLACK_CFG,
        token_path=token_file,
    )

    dm_results = [r for r in results if r["source"] == "dm"]
    assert len(dm_results) == 1
    assert "DM with bo-sample" in dm_results[0]["channel"]


@patch(
    "standup_tool.collectors.slack_activity.get_slack_credentials",
    return_value=SlackCredentials(token=_XOXC_TEST, cookie=_XOXD_TEST),
)
@patch("requests.Session.get", side_effect=_mock_auth)
@patch.object(slack_activity, "search_messages")
def test_collect_deduplicates_mentions_in_target_channels(
    mock_search, _mock_get, _mock_creds, token_file
):
    mock_search.side_effect = _mock_search_responses()
    results = slack_activity.collect(
        date(2026, 4, 24),
        date(2026, 4, 24),
        slack_cfg=_SLACK_CFG,
        token_path=token_file,
    )

    mention_results = [r for r in results if r["source"] == "mention"]
    assert len(mention_results) == 1
    assert mention_results[0]["channel"] == "#general"


@patch(
    "standup_tool.collectors.slack_activity.get_slack_credentials",
    return_value=SlackCredentials(token=_XOXC_TEST, cookie=_XOXD_TEST),
)
@patch("requests.Session.get", side_effect=_mock_auth)
@patch.object(slack_activity, "search_messages")
def test_collect_deduplicates_by_permalink(mock_search, _mock_get, _mock_creds, token_file):
    def duped_search(session, query, count=100):
        return [
            {
                "username": "bo-sample",
                "text": "same message",
                "ts": "1714000000.000100",
                "channel": {"name": "general"},
                "permalink": "https://acme.slack.com/same-link",
            },
        ]

    mock_search.side_effect = duped_search
    results = slack_activity.collect(
        date(2026, 4, 24),
        date(2026, 4, 24),
        slack_cfg=_SLACK_CFG,
        token_path=token_file,
    )

    permalinks = [r["permalink"] for r in results]
    assert permalinks.count("https://acme.slack.com/same-link") == 1


@patch(
    "standup_tool.collectors.slack_activity.get_slack_credentials",
    return_value=SlackCredentials(token=_XOXC_TEST, cookie=_XOXD_TEST),
)
@patch("requests.Session.get", side_effect=_mock_auth)
@patch.object(slack_activity, "search_messages")
def test_collect_iterates_per_day_with_on_filter(mock_search, _mock_get, _mock_creds, token_file):
    mock_search.side_effect = _mock_search_responses()
    slack_activity.collect(
        date(2026, 4, 25),
        date(2026, 4, 27),
        slack_cfg=_SLACK_CFG,
        token_path=token_file,
    )

    queries = [call.args[1] for call in mock_search.call_args_list]
    for day in ["2026-04-25", "2026-04-26", "2026-04-27"]:
        assert any(f"on:{day}" in q for q in queries), f"missing on:{day} query"
    assert not any("after:" in q or "before:" in q for q in queries)


@patch("standup_tool.collectors.slack_activity.time.sleep")
def test_search_paginates_across_multiple_pages(_mock_sleep):
    session = MagicMock()
    page1 = MagicMock(status_code=200)
    page1.json.return_value = {
        "ok": True,
        "messages": {
            "matches": [{"text": "first", "ts": "1"}],
            "paging": {"pages": 2, "page": 1},
        },
    }
    page2 = MagicMock(status_code=200)
    page2.json.return_value = {
        "ok": True,
        "messages": {
            "matches": [{"text": "second", "ts": "2"}],
            "paging": {"pages": 2, "page": 2},
        },
    }
    session.get.side_effect = [page1, page2]

    results = slack_activity.search_messages(session, "test query")
    assert len(results) == 2
    assert results[0]["text"] == "first"
    assert results[1]["text"] == "second"
    assert session.get.call_count == 2


@pytest.mark.parametrize("error_code", ["invalid_auth", "token_revoked", "account_inactive"])
@patch("standup_tool.collectors.slack_activity.time.sleep")
def test_search_raises_on_auth_error(_mock_sleep, error_code):
    session = MagicMock()
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"ok": False, "error": error_code}
    session.get.return_value = resp

    with pytest.raises(PermissionError, match=error_code):
        slack_activity.search_messages(session, "test query")


@patch("standup_tool.collectors.slack_activity.time.sleep")
def test_search_soft_error_returns_empty_list(_mock_sleep):
    session = MagicMock()
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"ok": False, "error": "ratelimited"}
    session.get.return_value = resp

    results = slack_activity.search_messages(session, "test query")
    assert results == []


def test_format_match_extracts_parent_thread_ts_from_permalink():
    session = MagicMock()
    match = {
        "username": "ada-example",
        "text": "deploy fails",
        "ts": "1777563233.932079",
        "channel": {"name": "ops"},
        "permalink": "https://acme.slack.com/archives/CEXAMPLE01/p1777563233932079?thread_ts=1777377976.150609",
    }
    result = slack_activity._format_match(match, "participation", session, {})
    assert result["thread_ts"] == "1777377976.150609"


def test_format_match_uses_own_ts_when_no_thread_ts_in_permalink():
    session = MagicMock()
    match = {
        "username": "ada-example",
        "text": "top-level message",
        "ts": "1777563233.932079",
        "channel": {"name": "eng"},
        "permalink": "https://acme.slack.com/archives/CEXAMPLE01/p1777563233932079",
    }
    result = slack_activity._format_match(match, "channel", session, {})
    assert result["thread_ts"] == "1777563233.932079"


@patch("standup_tool.collectors.slack_activity.time.sleep")
def test_resolve_user_id_success(_mock_sleep):
    session = MagicMock()
    resp = MagicMock(status_code=200)
    resp.json.return_value = {
        "ok": True,
        "user": {"profile": {"real_name": "Bo Sample", "display_name": "bo-sample"}},
    }
    session.get.return_value = resp

    cache: dict[str, str] = {}
    result = slack_activity._resolve_user_id(session, "UEXAMPLE02", cache)

    assert result == "Bo Sample"
    assert cache["UEXAMPLE02"] == "Bo Sample"
    session.get.assert_called_once_with(
        "https://slack.com/api/users.info",
        params={"user": "UEXAMPLE02"},
        allow_redirects=False,
        timeout=(5, 30),
    )


@patch("standup_tool.collectors.slack_activity.time.sleep")
def test_resolve_user_id_falls_back_to_display_name(_mock_sleep):
    session = MagicMock()
    resp = MagicMock(status_code=200)
    resp.json.return_value = {
        "ok": True,
        "user": {"profile": {"real_name": "", "display_name": "bo-sample"}},
    }
    session.get.return_value = resp

    cache: dict[str, str] = {}
    result = slack_activity._resolve_user_id(session, "UEXAMPLE02", cache)

    assert result == "bo-sample"


@patch("standup_tool.collectors.slack_activity.time.sleep")
def test_resolve_user_id_api_failure_returns_id(_mock_sleep):
    session = MagicMock()
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"ok": False, "error": "user_not_found"}
    session.get.return_value = resp

    cache: dict[str, str] = {}
    result = slack_activity._resolve_user_id(session, "UEXAMPLE02", cache)

    assert result == "UEXAMPLE02"
    assert cache["UEXAMPLE02"] == "UEXAMPLE02"


@patch("standup_tool.collectors.slack_activity.time.sleep")
def test_resolve_user_id_non_fatal_parse_error_returns_id(_mock_sleep):
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("malformed json")
    session.get.return_value = resp
    cache: dict[str, str] = {}
    result = slack_activity._resolve_user_id(session, "UEXAMPLE02", cache)

    assert result == "UEXAMPLE02"
    assert cache["UEXAMPLE02"] == "UEXAMPLE02"


@patch("standup_tool.collectors.slack_activity.time.sleep")
def test_resolve_user_id_network_error_propagates(_mock_sleep):
    session = MagicMock()
    session.get.side_effect = ConnectionError("network down")
    with pytest.raises(ConnectionError, match="network down"):
        slack_activity._resolve_user_id(session, "UEXAMPLE02", {})


@patch("standup_tool.collectors.slack_activity.time.sleep")
def test_resolve_user_id_uses_cache(_mock_sleep):
    session = MagicMock()
    cache = {"UEXAMPLE02": "Bo Sample"}

    result = slack_activity._resolve_user_id(session, "UEXAMPLE02", cache)

    assert result == "Bo Sample"
    session.get.assert_not_called()


@patch("standup_tool.collectors.slack_activity.time.sleep")
def test_resolve_channel_name_calls_resolve_for_user_id(_mock_sleep):
    session = MagicMock()
    resp = MagicMock(status_code=200)
    resp.json.return_value = {
        "ok": True,
        "user": {"profile": {"real_name": "Bo Sample", "display_name": ""}},
    }
    session.get.return_value = resp

    cache: dict[str, str] = {}
    result = slack_activity._resolve_channel_name(session, "UEXAMPLE02", cache)

    assert result == "Bo Sample"


def test_resolve_channel_name_passthrough_for_plain_name():
    session = MagicMock()
    cache: dict[str, str] = {}
    result = slack_activity._resolve_channel_name(session, "bo-sample", cache)

    assert result == "bo-sample"
    session.get.assert_not_called()


@patch("standup_tool.collectors.slack_activity.time.sleep")
def test_resolve_mentions_replaces_inline_user_ids(_mock_sleep):
    session = MagicMock()
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"ok": True, "user": {"profile": {"real_name": "Bo Sample"}}}
    session.get.return_value = resp
    cache: dict[str, str] = {}

    text = "Hey <@UEXAMPLE02> can you check this?"
    result = slack_activity._resolve_mentions(text, session, cache)

    assert result == "Hey Bo Sample can you check this?"


def test_resolve_mentions_handles_multiple_mentions():
    cache = {"UEXAMPLE01": "Ada Example", "UEXAMPLE02": "Bo Sample"}
    session = MagicMock()

    text = "<@UEXAMPLE01> and <@UEXAMPLE02> please review"
    result = slack_activity._resolve_mentions(text, session, cache)

    assert result == "Ada Example and Bo Sample please review"
    session.get.assert_not_called()


def test_resolve_mentions_passthrough_when_no_mentions():
    session = MagicMock()
    cache: dict[str, str] = {}

    result = slack_activity._resolve_mentions("no mentions here", session, cache)

    assert result == "no mentions here"
    session.get.assert_not_called()


@patch("standup_tool.collectors.slack_activity.time.sleep")
def test_resolve_mentions_handles_pipe_variant(_mock_sleep):
    session = MagicMock()
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"ok": True, "user": {"profile": {"real_name": "Bo Sample"}}}
    session.get.return_value = resp
    cache: dict[str, str] = {}

    text = "Hey <@UEXAMPLE02|bo-sample> can you check this?"
    result = slack_activity._resolve_mentions(text, session, cache)

    assert result == "Hey Bo Sample can you check this?"


@patch(
    "standup_tool.collectors.slack_activity.get_slack_credentials",
    return_value=SlackCredentials(token=_XOXC_TEST, cookie=_XOXD_TEST),
)
@patch("requests.Session.get", side_effect=_mock_auth)
@patch.object(slack_activity, "_resolve_user_id", return_value="Bo Sample")
@patch.object(slack_activity, "_fetch_thread")
@patch.object(slack_activity, "search_messages")
def test_collect_resolves_mentions_in_thread_replies(
    mock_search, mock_fetch_thread, _mock_resolve, _mock_get, _mock_creds, token_file
):
    def search_side_effect(session, query, count=100):
        if "is:dm" in query:
            return [
                {
                    "username": "bo-sample",
                    "text": "plain text",
                    "ts": "1714000000.000400",
                    "channel": {"name": "UEXAMPLE02"},
                    "permalink": "https://acme.slack.com/archives/DEXAMPLE01/p1714000000000400",
                },
            ]
        return []

    mock_search.side_effect = search_side_effect
    mock_fetch_thread.return_value = [
        {"user": "UEXAMPLE01", "text": "original message", "ts": "1714000000.000400"},
        {
            "user": "UEXAMPLE02",
            "text": "hey <@UEXAMPLE02> can you help?",
            "ts": "1714000000.000500",
        },
    ]

    results = slack_activity.collect(
        date(2026, 4, 24),
        date(2026, 4, 24),
        slack_cfg=_SLACK_CFG,
        token_path=token_file,
    )

    assert mock_fetch_thread.called
    dm_results = [r for r in results if r["source"] == "dm"]
    assert len(dm_results) == 1
    assert dm_results[0]["message_count"] == 2
    enriched_texts = [msg["text"] for msg in dm_results[0]["messages"]]
    assert any("Bo Sample" in t for t in enriched_texts)


@patch(
    "standup_tool.collectors.slack_activity.get_slack_credentials",
    return_value=SlackCredentials(token=_XOXC_TEST, cookie=_XOXD_TEST),
)
@patch("requests.Session.get", side_effect=_mock_auth)
@patch("standup_tool.collectors.slack_activity.time.sleep")
@patch.object(slack_activity, "_resolve_user_id", return_value="Bo Sample")
@patch.object(slack_activity, "_fetch_thread")
@patch.object(slack_activity, "search_messages")
def test_collect_fetches_each_thread_once_and_gives_each_item_its_own_copy(
    mock_search, mock_fetch_thread, _mock_resolve, _mock_sleep, _mock_get, _mock_creds, token_file
):
    thread = "https://acme.slack.com/archives/DEXAMPLE01/p{ts}?thread_ts=1714000000.000100"

    def search_side_effect(session, query, count=100):
        if "is:dm" not in query:
            return []
        return [
            {
                "username": "bo-sample",
                "text": text,
                "ts": ts,
                "channel": {"name": "UEXAMPLE02"},
                "permalink": thread.format(ts=ts.replace(".", "")),
            }
            for text, ts in (("first", "1714000000.000200"), ("second", "1714000000.000300"))
        ]

    mock_search.side_effect = search_side_effect
    mock_fetch_thread.return_value = [
        {"user": "UEXAMPLE01", "text": "root", "ts": "1714000000.000100"},
        {"user": "UEXAMPLE02", "text": "reply", "ts": "1714000000.000200"},
    ]

    results = slack_activity.collect(
        date(2026, 4, 24), date(2026, 4, 24), slack_cfg=_SLACK_CFG, token_path=token_file
    )

    dm_results = [r for r in results if r["source"] == "dm"]
    assert len(dm_results) == 2
    assert mock_fetch_thread.call_count == 1
    assert all(r["message_count"] == 2 for r in dm_results)
    dm_results[0]["messages"][0]["text"] = "mutated"
    assert dm_results[1]["messages"][0]["text"] == "root"


@patch(
    "standup_tool.collectors.slack_activity.get_slack_credentials",
    return_value=SlackCredentials(token=_XOXC_TEST, cookie=_XOXD_TEST),
)
@patch("requests.Session.get", side_effect=_mock_auth)
@patch("standup_tool.collectors.slack_activity.time.sleep")
@patch.object(slack_activity, "_fetch_thread", return_value=[])
@patch.object(slack_activity, "search_messages")
@patch.object(slack_activity, "_resolve_user_id", return_value="Bo Sample")
def test_collect_resolves_mentions_in_initial_message_text(
    _mock_resolve, mock_search, _mock_fetch_thread, _mock_sleep, _mock_get, _mock_creds, token_file
):
    """collect() resolves <@U...> mentions in the initial search result text via _format_match."""

    def search_side_effect(session, query, count=100):
        if "is:dm" in query:
            return [
                {
                    "username": "bo-sample",
                    "text": "hey <@UEXAMPLE02> can you review this?",
                    "ts": "1714000000.000400",
                    "channel": {"name": "UEXAMPLE02"},
                    "permalink": "https://acme.slack.com/archives/DEXAMPLE01/p1714000000000400",
                },
            ]
        return []

    mock_search.side_effect = search_side_effect

    results = slack_activity.collect(
        date(2026, 4, 24),
        date(2026, 4, 24),
        slack_cfg=_SLACK_CFG,
        token_path=token_file,
    )

    dm_results = [r for r in results if r["source"] == "dm"]
    assert len(dm_results) == 1
    assert dm_results[0]["messages"][0]["text"] == "hey Bo Sample can you review this?"
