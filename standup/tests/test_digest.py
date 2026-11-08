"""Tests for the digest module."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from standup_tool.digest import (
    _extract_timestamp,
    build_digest,
    extract_entities,
    group_into_work_units,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture() -> dict:
    with open(FIXTURES / "sample-collect-output.json") as f:
        return json.load(f)


TEAM = [
    {
        "name": "Ada Example",
        "github": "ada-example",
        "jira_email": "ada@example.com",
        "slack_id": "UEXAMPLE01",
    },
]


# --- Entity extraction ---


def test_extract_entities_jira_key():
    assert extract_entities("Fixed ACME-101") == {"ACME-101"}


def test_extract_entities_pr_refs():
    result = extract_entities("See #47 and widgets#48")
    assert "#47" in result
    assert "widgets#48" in result


def test_extract_entities_branch_names():
    result = extract_entities("branch ACME-101-cli-widgets")
    assert "ACME-101-cli-widgets" in result
    assert "ACME-101" in result


def test_extract_entities_no_matches():
    assert extract_entities("Regular text without entities") == set()


def test_extract_entities_mixed():
    result = extract_entities("ACME-101 PR #47 branch ACME-101-cli")
    assert "ACME-101" in result
    assert "#47" in result
    assert "ACME-101-cli" in result


def test_extract_entities_empty_string():
    assert extract_entities("") == set()


def test_extract_entities_none_input():
    assert extract_entities(None) == set()


# --- Work-unit grouping ---


def test_group_work_units():
    data = _load_fixture()
    work_units, uncorrelated = group_into_work_units(data, self_dm="DM with Ada Example")

    entity_map = {wu["entity"]: wu for wu in work_units}
    assert "ACME-101" in entity_map
    wu_101 = entity_map["ACME-101"]
    assert "git" in wu_101["sources"]
    assert "pr" in wu_101["sources"]
    assert "jira" in wu_101["sources"]

    assert "ACME-102" in entity_map

    assert len(uncorrelated) > 0


def test_group_work_units_multi_source():
    data = _load_fixture()
    work_units, _ = group_into_work_units(data, self_dm="DM with Ada Example")
    entity_map = {wu["entity"]: wu for wu in work_units}
    wu_101 = entity_map["ACME-101"]
    assert len(wu_101["sources"]) >= 3


# --- Relevance scoring ---


def test_relevance_scoring():
    data = _load_fixture()
    digest = build_digest(
        data, TEAM, date(2026, 5, 4), date(2026, 5, 4), self_dm="DM with Ada Example"
    )
    work_units = digest["work_units"]

    scores = {wu["entity"]: wu["relevance_score"] for wu in work_units}

    assert scores.get("ACME-101", 0) > scores.get("ACME-102", 0)

    assert scores["ACME-101"] >= 6
    assert scores["ACME-102"] >= 1


def test_work_units_sorted_by_relevance():
    data = _load_fixture()
    digest = build_digest(
        data, TEAM, date(2026, 5, 4), date(2026, 5, 4), self_dm="DM with Ada Example"
    )
    scores = [wu["relevance_score"] for wu in digest["work_units"]]
    assert scores == sorted(scores, reverse=True)


# --- Slack filtering ---


def test_slack_filtering():
    data = _load_fixture()
    _, uncorrelated = group_into_work_units(data, self_dm="DM with Ada Example")

    self_dm_in_uncorrelated = any(
        item.get("channel") == "DM with Ada Example" for item in uncorrelated
    )
    assert not self_dm_in_uncorrelated

    participation_sources = [
        item
        for item in uncorrelated
        if item.get("_source") == "slack" and item.get("source") == "participation"
    ]
    assert len(participation_sources) == 0 or all(
        not extract_entities(str(item.get("messages", []))) for item in participation_sources
    )


def test_slack_self_dm_excluded():
    data = {
        "slack": [
            {"channel": "DM with Ada Example", "source": "dm", "messages": [{"text": "self note"}]},
            {
                "channel": "DM with Bo Sample",
                "source": "dm",
                "messages": [{"text": "hey ACME-100"}],
            },
        ]
    }
    work_units, uncorrelated = group_into_work_units(data, self_dm="DM with Ada Example")
    all_channels = set()
    for wu in work_units:
        for item in wu.get("items", []):
            if item.get("channel"):
                all_channels.add(item["channel"])
    for item in uncorrelated:
        if item.get("channel"):
            all_channels.add(item["channel"])
    assert "DM with Ada Example" not in all_channels


def test_slack_channel_source_excluded():
    """source='channel' falls through _is_high_signal_slack and returns False."""
    from standup_tool.digest import _is_high_signal_slack

    item = {"source": "channel"}
    assert _is_high_signal_slack(item, self_dm="") is False


# --- Staleness and window ---


def test_staleness_tagging():
    data = _load_fixture()
    digest = build_digest(
        data, TEAM, date(2026, 5, 10), date(2026, 5, 10), self_dm="DM with Ada Example"
    )
    for wu in digest["work_units"]:
        if wu["entity"] == "ACME-101":
            assert wu["stale"] is True


def test_staleness_not_stale():
    data = _load_fixture()
    digest = build_digest(
        data, TEAM, date(2026, 5, 4), date(2026, 5, 4), self_dm="DM with Ada Example"
    )
    for wu in digest["work_units"]:
        if wu["entity"] == "ACME-101":
            assert wu["stale"] is False


def test_window_weekday():
    data = _load_fixture()
    digest = build_digest(
        data, TEAM, date(2026, 5, 4), date(2026, 5, 4), self_dm="DM with Ada Example"
    )
    assert digest["window"]["type"] == "weekday"
    assert digest["window"]["days"] == 1


def test_window_weekend():
    data = _load_fixture()
    digest = build_digest(
        data, TEAM, date(2026, 5, 2), date(2026, 5, 4), self_dm="DM with Ada Example"
    )
    assert digest["window"]["type"] == "weekend"
    assert digest["window"]["days"] == 3


def test_window_extended():
    data = _load_fixture()
    digest = build_digest(
        data, TEAM, date(2026, 4, 28), date(2026, 5, 4), self_dm="DM with Ada Example"
    )
    assert digest["window"]["type"] == "extended"
    assert digest["window"]["days"] == 7


# --- Full build_digest integration ---


def test_build_digest_schema():
    data = _load_fixture()
    digest = build_digest(
        data, TEAM, date(2026, 5, 4), date(2026, 5, 4), self_dm="DM with Ada Example"
    )

    assert "generated_at" in digest
    assert "window" in digest
    assert "work_units" in digest
    assert "uncorrelated" in digest
    assert "team" in digest

    assert digest["window"]["since"] == "2026-05-04"
    assert digest["window"]["until"] == "2026-05-04"

    assert len(digest["work_units"]) > 0
    assert digest["work_units"][0]["relevance_score"] >= digest["work_units"][-1]["relevance_score"]

    assert digest["team"][0]["github"] == "ada-example"


def test_build_digest_work_unit_fields():
    data = _load_fixture()
    digest = build_digest(
        data, TEAM, date(2026, 5, 4), date(2026, 5, 4), self_dm="DM with Ada Example"
    )

    wu_101 = None
    for wu in digest["work_units"]:
        if wu["entity"] == "ACME-101":
            wu_101 = wu
            break
    assert wu_101 is not None
    assert wu_101["git"] is not None
    assert "commits" in wu_101["git"]
    assert wu_101["pr"] is not None
    assert wu_101["pr"]["number"] == 47
    assert wu_101["jira"] is not None
    assert wu_101["jira"]["key"] == "ACME-101"


# --- Partial/empty data ---


def test_partial_data_empty():
    digest = build_digest({}, TEAM, date(2026, 5, 4), date(2026, 5, 4))
    assert digest["work_units"] == []
    assert digest["uncorrelated"] == []


def test_partial_data_missing_keys():
    data = {"git_commits": [{"subject": "ACME-100: fix", "timestamp": "2026-05-04T10:00:00Z"}]}
    digest = build_digest(data, TEAM, date(2026, 5, 4), date(2026, 5, 4))
    assert len(digest["work_units"]) >= 1


def test_partial_data_jira_empty_dict():
    data = {"jira_activity": {}}
    digest = build_digest(data, TEAM, date(2026, 5, 4), date(2026, 5, 4))
    assert len(digest["work_units"]) == 0


def test_partial_data_slack_empty_list():
    data = {"slack": []}
    digest = build_digest(data, TEAM, date(2026, 5, 4), date(2026, 5, 4))
    assert len(digest["work_units"]) == 0
    assert len(digest["uncorrelated"]) == 0


def test_partial_data_jira_dict_of_lists():
    data = {
        "jira_activity": {
            "assigned": [{"key": "ACME-999", "summary": "Test", "status": "Open", "type": "Task"}],
            "recently_updated": [],
        }
    }
    digest = build_digest(data, TEAM, date(2026, 5, 4), date(2026, 5, 4))
    entities = {wu["entity"] for wu in digest["work_units"]}
    assert "ACME-999" in entities


# --- _extract_timestamp ---


def test_extract_timestamp_iso8601_with_tz():
    item = {"timestamp": "2026-05-04T10:30:00+00:00"}
    ts = _extract_timestamp(item)
    assert ts is not None
    assert ts.year == 2026
    assert ts.month == 5
    assert ts.day == 4


def test_extract_timestamp_iso8601_no_tz():
    item = {"timestamp": "2026-05-04T10:30:00"}
    ts = _extract_timestamp(item)
    assert ts is not None
    assert ts.tzinfo is not None


def test_extract_timestamp_date_only():
    item = {"date": "2026-05-04"}
    ts = _extract_timestamp(item)
    assert ts is not None
    assert ts.year == 2026


def test_extract_timestamp_unix_float():
    item = {"ts": 1714834200.0}
    ts = _extract_timestamp(item)
    assert ts is not None


def test_extract_timestamp_missing_fields():
    item = {"unrelated": "value"}
    ts = _extract_timestamp(item)
    assert ts is None


def test_extract_timestamp_empty_string():
    item = {"timestamp": ""}
    ts = _extract_timestamp(item)
    assert ts is None


# --- Collector section coverage ---


def test_build_digest_calendar_flows_to_work_unit():
    data = _load_fixture()
    digest = build_digest(
        data, TEAM, date(2026, 5, 4), date(2026, 5, 4), self_dm="DM with Ada Example"
    )
    wu_101 = next((wu for wu in digest["work_units"] if wu["entity"] == "ACME-101"), None)
    assert wu_101 is not None
    assert "calendar" in wu_101["sources"]
    assert wu_101["calendar"] is not None


def test_build_digest_drive_flows_to_work_unit():
    data = _load_fixture()
    digest = build_digest(
        data, TEAM, date(2026, 5, 4), date(2026, 5, 4), self_dm="DM with Ada Example"
    )
    wu_101 = next((wu for wu in digest["work_units"] if wu["entity"] == "ACME-101"), None)
    assert wu_101 is not None
    assert "drive" in wu_101["sources"]
    assert wu_101["drive_docs"] is not None


def test_build_digest_conversations_flow_to_work_unit():
    data = _load_fixture()
    digest = build_digest(
        data, TEAM, date(2026, 5, 4), date(2026, 5, 4), self_dm="DM with Ada Example"
    )
    wu_101 = next((wu for wu in digest["work_units"] if wu["entity"] == "ACME-101"), None)
    assert wu_101 is not None
    assert "conversations" in wu_101["sources"]
    assert wu_101["conversations"] is not None


def test_build_digest_email_uncorrelated():
    data = _load_fixture()
    digest = build_digest(
        data, TEAM, date(2026, 5, 4), date(2026, 5, 4), self_dm="DM with Ada Example"
    )
    email_uncorrelated = [u for u in digest["uncorrelated"] if u.get("source") == "email"]
    assert len(email_uncorrelated) >= 1


def test_build_digest_dev_activity_uncorrelated():
    data = _load_fixture()
    digest = build_digest(
        data, TEAM, date(2026, 5, 4), date(2026, 5, 4), self_dm="DM with Ada Example"
    )
    dev_uncorrelated = [u for u in digest["uncorrelated"] if u.get("source") == "dev_activity"]
    assert len(dev_uncorrelated) >= 1


# --- Mention filtering ---


def test_slack_mention_without_replies_excluded():
    data = {
        "slack": [
            {"channel": "#general", "source": "mention", "messages": [{"text": "hey ACME-100"}]},
        ]
    }
    work_units, uncorrelated = group_into_work_units(data, self_dm="DM with Ada Example")
    assert len(work_units) == 0
    assert len(uncorrelated) == 0


def test_slack_mention_with_replies_included():
    data = {
        "slack": [
            {
                "channel": "#general",
                "source": "mention",
                "messages": [
                    {"text": "hey ACME-100 check this"},
                    {"text": "reply: will do"},
                ],
            },
        ]
    }
    work_units, _ = group_into_work_units(data, self_dm="DM with Ada Example")
    entity_map = {wu["entity"]: wu for wu in work_units}
    assert "ACME-100" in entity_map


# --- Conversation registry (dedup + traceability) ---


def _multi_entity_conversation_data() -> dict:
    return {
        "conversations": [
            {
                "conversation_id": "conv-alpha",
                "date": "2026-07-22",
                "context": (
                    "# summary\n"
                    "Worked across ACME-200 and ACME-201 today.\n"
                    "Wired up the shared client for both tickets.\n"
                    "Outcome: both PRs ready for review."
                ),
            }
        ]
    }


def test_conversation_registry_dedupes_multi_entity_conversation():
    """A conversation mentioning 2 entities must appear ONCE in the registry, not twice."""
    data = _multi_entity_conversation_data()
    digest = build_digest(data, TEAM, date(2026, 7, 22), date(2026, 7, 22))

    registry = digest["conversation_registry"]
    matching = [r for r in registry if r["conversation_id"] == "conv-alpha"]
    assert len(matching) == 1


def test_conversation_registry_tracks_linked_entities():
    """The registry entry must list every entity the conversation was fanned out to."""
    data = _multi_entity_conversation_data()
    digest = build_digest(data, TEAM, date(2026, 7, 22), date(2026, 7, 22))

    record = next(
        r for r in digest["conversation_registry"] if r["conversation_id"] == "conv-alpha"
    )
    assert set(record["linked_entities"]) == {"ACME-200", "ACME-201"}


def test_conversation_registry_preserves_context_and_date():
    """Full fidelity -- context and date must survive in the registry (traceability)."""
    data = _multi_entity_conversation_data()
    digest = build_digest(data, TEAM, date(2026, 7, 22), date(2026, 7, 22))

    record = next(
        r for r in digest["conversation_registry"] if r["conversation_id"] == "conv-alpha"
    )
    assert record["date"] == "2026-07-22"
    assert "ACME-200 and ACME-201" in record["context"]


def test_work_unit_conversation_ref_excludes_context_but_keeps_signal():
    """Work units embed a lightweight ref: no context duplication, but id/date/summary survive."""
    data = _multi_entity_conversation_data()
    digest = build_digest(data, TEAM, date(2026, 7, 22), date(2026, 7, 22))

    wu_200 = next(wu for wu in digest["work_units"] if wu["entity"] == "ACME-200")
    ref = wu_200["conversations"]
    assert isinstance(ref, dict)
    assert "context" not in ref
    assert ref["conversation_id"] == "conv-alpha"
    assert ref["date"] == "2026-07-22"


def test_conversation_registry_handles_missing_conversation_id():
    """Older/fixture-style data without conversation_id must still dedupe via content hash."""
    data = _load_fixture()
    digest = build_digest(
        data, TEAM, date(2026, 5, 4), date(2026, 5, 4), self_dm="DM with Ada Example"
    )

    registry = digest["conversation_registry"]
    assert len(registry) >= 1
    assert all(r["conversation_id"] for r in registry)

    wu_101 = next(wu for wu in digest["work_units"] if wu["entity"] == "ACME-101")
    assert wu_101["conversations"]["conversation_id"] == registry[0]["conversation_id"]


def test_conversation_registry_includes_uncorrelated_conversations():
    """A conversation matching no entity still lands in the registry with empty linked_entities,
    and its uncorrelated entry references it by conversation_id."""
    data = {
        "conversations": [
            {
                "conversation_id": "conv-noentity",
                "date": "2026-07-22",
                "context": "General chat about the roadmap, nothing ticket-specific.",
            }
        ]
    }
    digest = build_digest(data, TEAM, date(2026, 7, 22), date(2026, 7, 22))

    record = next(
        r for r in digest["conversation_registry"] if r["conversation_id"] == "conv-noentity"
    )
    assert record["linked_entities"] == []

    uncorrelated_conv = next(
        u for u in digest["uncorrelated"] if u.get("conversation_id") == "conv-noentity"
    )
    assert uncorrelated_conv["source"] == "conversations"
