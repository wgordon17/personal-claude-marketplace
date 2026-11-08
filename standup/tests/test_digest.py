"""Tests for the digest module."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from standup_tool.digest import (
    _extract_timestamp,
    _summarize_memory,
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


def test_extract_entities_alphanumeric_and_underscore_jira_keys():
    result = extract_entities("Fixed PROJ2-42 and TEAM_A-101 on branch PROJ2-42-fix-auth")
    assert "PROJ2-42" in result
    assert "TEAM_A-101" in result
    assert "PROJ2-42-fix-auth" in result


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


def test_build_digest_memory_activity_uncorrelated():
    data = _load_fixture()
    digest = build_digest(
        data, TEAM, date(2026, 5, 4), date(2026, 5, 4), self_dm="DM with Ada Example"
    )
    mem_uncorrelated = [u for u in digest["uncorrelated"] if u.get("source") == "memory_activity"]
    assert len(mem_uncorrelated) >= 1
    assert mem_uncorrelated[0]["text"] == "Updates build task definitions"
    assert "timestamp" in mem_uncorrelated[0]


def test_build_digest_memory_activity_linked_to_entity():
    data = {
        "memory_activity": [
            {
                "repo": "widgets",
                "memory_dir": "/Users/ada/src/widgets/hack",
                "category": "plans",
                "path": "plans/2026-05-04-acme-plan.md",
                "modified": "2026-05-04",
                "title": "ACME-123: Design widget quota limits",
            }
        ]
    }
    digest = build_digest(data, TEAM, date(2026, 5, 4), date(2026, 5, 4))
    wu = next((w for w in digest["work_units"] if w["entity"] == "ACME-123"), None)
    assert wu is not None
    assert "memory_activity" in wu["sources"]
    assert wu["memory_activity"] == {
        "repo": "widgets",
        "category": "plans",
        "title": "ACME-123: Design widget quota limits",
        "path": "plans/2026-05-04-acme-plan.md",
    }
    assert _summarize_memory(data["memory_activity"][0])["repo"] == "widgets"


def test_build_digest_multiple_memory_activity_items_and_uncorrelated_title_truncation():
    long_title = "Research note " + ("x" * 250)
    data = {
        "memory_activity": [
            {
                "repo": "widgets",
                "memory_dir": "/Users/ada/src/widgets/hack",
                "category": "plans",
                "path": "plans/plan-1.md",
                "modified": "2026-05-04",
                "title": "ACME-555 Phase 1",
            },
            {
                "repo": "widgets",
                "memory_dir": "/Users/ada/src/widgets/hack",
                "category": "research",
                "path": "research/spike-1.md",
                "modified": "2026-05-04",
                "title": "ACME-555 Spike",
            },
            {
                "repo": "widgets",
                "memory_dir": "/Users/ada/src/widgets/hack",
                "category": "research",
                "path": "research/general-notes.md",
                "modified": "2026-05-04",
                "title": long_title,
            },
        ]
    }
    digest = build_digest(data, TEAM, date(2026, 5, 4), date(2026, 5, 4))
    wu = next((w for w in digest["work_units"] if w["entity"] == "ACME-555"), None)
    assert wu is not None
    assert isinstance(wu["memory_activity"], list)
    assert len(wu["memory_activity"]) == 2

    mem_uncorr = [u for u in digest["uncorrelated"] if u.get("source") == "memory_activity"]
    assert len(mem_uncorr) == 1
    assert len(mem_uncorr[0]["text"]) == 200
    assert mem_uncorr[0]["text"] == long_title[:200]


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


def test_pr_activity_real_schema_and_relevance_scoring():
    """Verify pr_activity.collect() schema (pr_number, review_requests, uppercase state,
    title, url, is_draft, review_decision) and case-insensitive state relevance scoring."""
    data = {
        "prs": [
            {
                "repo": "widgets",
                "pr_number": 88,
                "title": "PROJ2-42: Add widget caching",
                "state": "OPEN",
                "head_branch": "PROJ2-42-widget-cache",
                "created_at": "2026-04-25T10:00:00Z",
                "updated_at": "2026-05-04T09:00:00Z",
                "url": "https://github.com/acme/widgets/pull/88",
                "is_draft": False,
                "role": "author",
                "checks": "SUCCESS",
                "review_decision": "REVIEW_REQUIRED",
                "review_requests": ["ada-example", "bo-sample"],
            },
            {
                "repo": "widgets",
                "pr_number": 89,
                "title": "TEAM_A-101: Old merged PR",
                "state": "MERGED",
                "head_branch": "TEAM_A-101-merged",
                "created_at": "2026-05-03T10:00:00Z",
                "updated_at": "2026-05-04T08:00:00Z",
                "url": "https://github.com/acme/widgets/pull/89",
                "is_draft": False,
                "role": "author",
                "checks": "SUCCESS",
                "review_decision": "APPROVED",
                "review_requests": [],
            },
        ]
    }
    digest = build_digest(data, TEAM, date(2026, 5, 4), date(2026, 5, 4))
    by_entity = {wu["entity"]: wu for wu in digest["work_units"]}

    wu_open = by_entity["PROJ2-42"]
    assert wu_open["pr"] == {
        "number": 88,
        "repo": "widgets",
        "title": "PROJ2-42: Add widget caching",
        "url": "https://github.com/acme/widgets/pull/88",
        "state": "OPEN",
        "is_draft": False,
        "ci": "SUCCESS",
        "review_decision": "REVIEW_REQUIRED",
        "reviewers": ["ada-example", "bo-sample"],
        "role": "author",
    }
    # updated_at (2026-05-04) takes precedence over created_at (2026-04-25), so not stale
    assert wu_open["stale"] is False

    wu_merged = by_entity["TEAM_A-101"]
    assert wu_merged["pr"]["number"] == 89
    assert wu_merged["pr"]["state"] == "MERGED"
    # Open PR gets +1 relevance bonus over uppercase MERGED PR
    assert wu_open["relevance_score"] == wu_merged["relevance_score"] + 1


def test_collector_to_digest_field_and_timestamp_mappings():
    """Verify uncorrelated text/timestamp extraction and Slack message_count thread_replies."""
    data = {
        "slack": [
            {
                "source": "participation",
                "channel": "#eng",
                "thread_ts": "1714816800.123456",
                "date": "1714816800",
                "messages": [
                    {
                        "user": "ada-example",
                        "text": "Discussing ACME-303 rollout",
                        "ts": "1714816800.1",
                    },
                    {"user": "bo-sample", "text": "Sounds good", "ts": "1714816805.2"},
                    {"user": "ada-example", "text": "Shipping now", "ts": "1714816810.3"},
                ],
                "message_count": 3,
            },
            {
                "source": "dm",
                "channel": "DM with Bo Sample",
                "thread_ts": "1714816900.000100",
                "date": "1714816900",
                "messages": [
                    {"user": "bo-sample", "text": "Quick sync later?", "ts": "1714816900.0"}
                ],
                "message_count": 1,
            },
        ],
        "calendar_events": [
            {
                "summary": "Architecture sync",
                "start": "2026-05-04T14:00:00Z",
                "description": "",
            }
        ],
        "drive_docs": [
            {
                "source": "activity",
                "file_name": "Q2 Planning Notes",
                "modified_time": "2026-05-04T15:30:00.123Z",
                "text_preview": "Draft objectives",
                "comments": [{"author": "Bo Sample", "content": "Add timeline table"}],
            }
        ],
        "emails": [
            {
                "subject": "Release checklist",
                "date": "Mon, 04 May 2026 16:20:00 +0000",
                "body_snippet": "Please verify",
            }
        ],
    }
    digest = build_digest(data, TEAM, date(2026, 5, 4), date(2026, 5, 4))

    wu_303 = next(wu for wu in digest["work_units"] if wu["entity"] == "ACME-303")
    assert wu_303["slack"][0]["thread_replies"] == 2

    by_source = {u["source"]: u for u in digest["uncorrelated"]}
    assert by_source["calendar"]["text"] == "Architecture sync"
    assert "timestamp" in by_source["calendar"]

    assert by_source["drive"]["text"] == "Q2 Planning Notes: Add timeline table"
    assert "timestamp" in by_source["drive"]

    assert by_source["email"]["text"] == "Release checklist"
    assert "timestamp" in by_source["email"]

    assert by_source["slack"]["text"] == "Quick sync later?"
    assert "timestamp" in by_source["slack"]


def test_cross_stage_collector_producers_into_build_digest():
    """Feed actual collector output builders (pr_activity._build_pr_dict,
    slack_activity._format_match, google_drive._build_result, google_calendar.collect)
    directly into digest.build_digest() to verify end-to-end schema contracts."""
    from unittest.mock import MagicMock, patch

    from standup_tool.collectors import google_calendar, google_drive, pr_activity, slack_activity

    # 1. PR emitted by pr_activity._build_pr_dict
    raw_gh_pr = {
        "number": 142,
        "title": "PROJ2-77: Wire collector schemas into digest",
        "state": "OPEN",
        "updatedAt": "2026-05-04T11:15:00Z",
        "createdAt": "2026-04-20T08:00:00Z",
        "url": "https://github.com/acme/widgets/pull/142",
        "isDraft": False,
        "headRefName": "PROJ2-77-collector-digest",
        "reviewDecision": "REVIEW_REQUIRED",
        "reviewRequests": [{"login": "bo-sample"}],
        "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "COMPLETED"}],
    }
    pr_item = pr_activity._build_pr_dict(raw_gh_pr, "widgets", "author")

    # 2. Slack item emitted by slack_activity._format_match + thread enrichment
    slack_session = MagicMock()
    user_cache = {"UEXAMPLE01": "Ada Example", "UBOSAMPLE": "Bo Sample"}
    raw_slack_match = {
        "channel": {"name": "eng"},
        "ts": "1714820000.000200",
        "username": "ada-example",
        "text": "Following up on PROJ2-77 with <@UBOSAMPLE>",
        "permalink": "https://acme.slack.com/archives/C123/p1714820000000200?thread_ts=1714819000.000100",
    }
    slack_correlated = slack_activity._format_match(
        raw_slack_match, "participation", slack_session, user_cache
    )
    slack_correlated["messages"].append(
        {"user": "bo-sample", "text": "LGTM!", "ts": "1714820060.000300"}
    )
    slack_correlated["message_count"] = 2

    raw_uncorr_slack = {
        "channel": {"name": "UBOSAMPLE"},
        "ts": "1714821000.000100",
        "username": "bo-sample",
        "text": "Are you joining the demo?",
        "permalink": "https://acme.slack.com/archives/D123/p1714821000000100",
    }
    slack_uncorrelated = slack_activity._format_match(
        raw_uncorr_slack, "dm", slack_session, user_cache
    )

    # 3. Calendar events emitted by google_calendar.collect
    mock_cal_service = MagicMock()
    mock_cal_service.events().list().execute.return_value = {
        "items": [
            {
                "summary": "Sprint retro",
                "start": {"dateTime": "2026-05-04T15:00:00Z"},
                "end": {"dateTime": "2026-05-04T15:30:00Z"},
                "organizer": {"email": "ada@example.com"},
                "attendees": [
                    {"email": "ada@example.com", "self": True, "responseStatus": "accepted"}
                ],
            }
        ]
    }
    with (
        patch("standup_tool.collectors.google_calendar.get_credentials", return_value=MagicMock()),
        patch("standup_tool.collectors.google_calendar.build", return_value=mock_cal_service),
    ):
        cal_events = google_calendar.collect(date(2026, 5, 4), date(2026, 5, 4))

    # 4. Drive doc emitted by google_drive._build_result
    mock_drive_service = MagicMock()
    mock_drive_service.comments().list().execute.return_value = {
        "comments": [
            {
                "id": "c-1",
                "content": "Review section 2",
                "author": {"displayName": "Bo Sample", "emailAddress": "bo@example.com"},
                "createdTime": "2026-05-04T16:00:00Z",
                "resolved": False,
                "replies": [],
            }
        ]
    }
    drive_meta = {
        "id": "doc-uncorr-1",
        "name": "Architecture RFC",
        "mimeType": "application/vnd.google-apps.document",
        "webViewLink": "https://docs.google.com/document/d/doc-uncorr-1/edit",
        "modifiedTime": "2026-05-04T16:05:00Z",
        "capabilities": {"canDownload": False},
    }
    drive_item = google_drive._build_result(
        drive_meta, mock_drive_service, "activity", since=date(2026, 5, 4)
    )

    digest = build_digest(
        {
            "prs": [pr_item],
            "slack": [slack_correlated, slack_uncorrelated],
            "calendar_events": cal_events,
            "drive_docs": [drive_item],
        },
        TEAM,
        date(2026, 5, 4),
        date(2026, 5, 4),
        self_dm="DM with Ada Example",
    )

    by_entity = {wu["entity"]: wu for wu in digest["work_units"]}
    wu = by_entity["PROJ2-77"]
    assert wu["stale"] is False
    assert wu["pr"]["number"] == 142
    assert wu["pr"]["title"] == "PROJ2-77: Wire collector schemas into digest"
    assert wu["pr"]["url"] == "https://github.com/acme/widgets/pull/142"
    assert wu["pr"]["is_draft"] is False
    assert wu["pr"]["review_decision"] == "REVIEW_REQUIRED"
    assert wu["pr"]["reviewers"] == ["bo-sample"]
    assert wu["slack"][0]["thread_replies"] == 1
    assert "Bo Sample" in wu["slack"][0]["text"]

    uncorr_by_source = {u["source"]: u for u in digest["uncorrelated"]}
    assert uncorr_by_source["slack"]["text"] == "Are you joining the demo?"
    assert "timestamp" in uncorr_by_source["slack"]
    assert uncorr_by_source["calendar"]["text"] == "Sprint retro"
    assert "timestamp" in uncorr_by_source["calendar"]
    assert uncorr_by_source["drive"]["text"] == "Architecture RFC: Review section 2"
    assert "timestamp" in uncorr_by_source["drive"]
