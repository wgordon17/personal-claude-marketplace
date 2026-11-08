"""Tests for conversation_activity.py."""

import json
import os
import time
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from standup_tool.collectors import conversation_activity

_REPO_KEYS = {"-Users-ada-src-widgets": "widgets"}


def _write_conversation(path: Path, messages: list[dict]) -> None:
    with open(path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg) + "\n")


def _make_user_msg(text: str, ts: str = "2026-04-22T10:00:00Z") -> dict:
    return {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
        "timestamp": ts,
    }


def _make_assistant_msg(text: str, ts: str = "2026-04-22T10:01:00Z") -> dict:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        "timestamp": ts,
    }


def _make_meta_msg(text: str) -> dict:
    return {
        "type": "user",
        "isMeta": True,
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def _make_tool_result_msg() -> dict:
    return {
        "type": "user",
        "toolUseResult": {"output": "some output"},
        "message": {"role": "user", "content": [{"type": "text", "text": "tool result"}]},
    }


def _make_progress_msg() -> dict:
    return {"type": "progress", "data": {"step": 1}}


class TestFindConversations:
    def test_finds_jsonl_in_date_range(self, tmp_path):
        project_dir = tmp_path / "-Users-ada-src-widgets"
        project_dir.mkdir()
        conv_file = project_dir / "session-1.jsonl"
        conv_file.write_text("{}\n")

        with (
            patch.object(conversation_activity, "_CLAUDE_PROJECTS_DIR", tmp_path),
            patch.object(conversation_activity, "_OMP_SESSIONS_DIR", tmp_path / "omp"),
        ):
            result = conversation_activity._find_conversations(
                date(2026, 4, 20), date(2030, 12, 31), _REPO_KEYS, ()
            )

        assert len(result) == 1
        assert result[0]["project_dir"] == "-Users-ada-src-widgets"
        assert result[0]["repos_touched"] == ["widgets"]

    def test_skips_files_outside_date_range(self, tmp_path):
        project_dir = tmp_path / "-Users-ada-src-widgets"
        project_dir.mkdir()
        conv_file = project_dir / "old-session.jsonl"
        conv_file.write_text("{}\n")
        old_time = 1000000000  # 2001
        os.utime(conv_file, (old_time, old_time))

        with (
            patch.object(conversation_activity, "_CLAUDE_PROJECTS_DIR", tmp_path),
            patch.object(conversation_activity, "_OMP_SESSIONS_DIR", tmp_path / "omp"),
        ):
            result = conversation_activity._find_conversations(
                date(2026, 4, 20), date(2030, 12, 31), _REPO_KEYS, ()
            )

        assert result == []

    def test_raises_when_claude_projects_dir_missing(self, tmp_path):
        missing = tmp_path / "nonexistent"
        with (
            patch.object(conversation_activity, "_CLAUDE_PROJECTS_DIR", missing),
            patch.object(conversation_activity, "_OMP_SESSIONS_DIR", missing),
            pytest.raises(FileNotFoundError, match="Claude projects directory"),
        ):
            conversation_activity._find_conversations(
                date(2026, 4, 20), date(2030, 12, 31), _REPO_KEYS, ()
            )

    def test_returns_all_matching_conversations(self, tmp_path):
        project_dir = tmp_path / "-Users-ada-src-widgets"
        project_dir.mkdir()
        for i in range(15):
            f = project_dir / f"session-{i}.jsonl"
            f.write_text("{}\n")
            time.sleep(0.01)

        with (
            patch.object(conversation_activity, "_CLAUDE_PROJECTS_DIR", tmp_path),
            patch.object(conversation_activity, "_OMP_SESSIONS_DIR", tmp_path / "omp"),
        ):
            result = conversation_activity._find_conversations(
                date(2026, 4, 20), date(2030, 12, 31), _REPO_KEYS, ()
            )

        assert len(result) == 15

    def test_skips_file_when_stat_raises(self, tmp_path):
        project_dir = tmp_path / "-Users-ada-src-widgets"
        project_dir.mkdir()
        conv_file = project_dir / "session.jsonl"
        conv_file.write_text("{}\n")

        original_stat = Path.stat

        def stat_raises(self_path, **kwargs):
            if self_path.name == "session.jsonl":
                raise OSError("permission denied")
            return original_stat(self_path, **kwargs)

        with (
            patch.object(conversation_activity, "_CLAUDE_PROJECTS_DIR", tmp_path),
            patch.object(conversation_activity, "_OMP_SESSIONS_DIR", tmp_path / "omp"),
            patch.object(Path, "stat", stat_raises),
        ):
            result = conversation_activity._find_conversations(
                date(2026, 4, 20), date(2030, 12, 31), _REPO_KEYS, ()
            )

        assert result == []


class TestExtractContext:
    def test_extracts_user_and_assistant_text(self, tmp_path):
        conv = tmp_path / "session.jsonl"
        _write_conversation(
            conv,
            [
                _make_user_msg("Fix the login bug"),
                _make_assistant_msg("I found the issue in auth.py"),
            ],
        )

        result = conversation_activity._extract_context(conv)

        assert "User: Fix the login bug" in result
        assert "Assistant: I found the issue in auth.py" in result

    def test_skips_meta_messages(self, tmp_path):
        conv = tmp_path / "session.jsonl"
        _write_conversation(
            conv,
            [
                _make_meta_msg("system context injection"),
                _make_user_msg("real user prompt"),
            ],
        )

        result = conversation_activity._extract_context(conv)

        assert "system context" not in result
        assert "User: real user prompt" in result

    def test_skips_tool_results(self, tmp_path):
        conv = tmp_path / "session.jsonl"
        _write_conversation(
            conv,
            [
                _make_user_msg("Read the file"),
                _make_tool_result_msg(),
                _make_assistant_msg("Here's what the file contains"),
            ],
        )

        result = conversation_activity._extract_context(conv)

        assert "tool result" not in result
        assert "User: Read the file" in result

    def test_skips_progress_events(self, tmp_path):
        conv = tmp_path / "session.jsonl"
        _write_conversation(
            conv,
            [
                _make_progress_msg(),
                _make_user_msg("Hello"),
            ],
        )

        result = conversation_activity._extract_context(conv)

        assert "progress" not in result.lower()
        assert "User: Hello" in result

    def test_returns_empty_for_unreadable_file(self, tmp_path):
        missing = tmp_path / "missing.jsonl"
        result = conversation_activity._extract_context(missing)
        assert result == ""

    def test_caps_individual_messages(self, tmp_path):
        conv = tmp_path / "session.jsonl"
        long_text = "x " * 5000
        _write_conversation(conv, [_make_user_msg(long_text)])

        result = conversation_activity._extract_context(conv)

        assert len(result) <= conversation_activity._MAX_MESSAGE_CHARS + 20


class TestProjectNameFromDir:
    def test_converts_dash_separated_path(self):
        result = conversation_activity._project_name_from_dir("-Users-ada-src-widgets")
        assert result == "/Users/ada/src/widgets"

    def test_handles_no_leading_dash(self):
        result = conversation_activity._project_name_from_dir("some-project")
        assert result == "some-project"


class TestMatchesProject:
    def test_matches_exact_canonical_repo_key_only(self):
        repo_keys = {
            "-Users-ada-src-acme-docs": "docs",
            "-Users-ada-src-acme-widgets": "widgets",
        }
        assert conversation_activity._matches_project(
            "-Users-ada-src-acme-docs", repo_keys, ()
        ) == ["docs"]
        assert (
            conversation_activity._matches_project("-Users-ada-src-unrelated-docs", repo_keys, ())
            == []
        )

    def test_matches_explicit_project_match_patterns(self):
        assert conversation_activity._matches_project(
            "-Users-ada-src-acme-monorepo", {}, ("acme-monorepo",)
        ) == ["acme-monorepo"]

    def test_build_repo_keys_maps_canonical_paths(self, tmp_path):
        from standup_tool.workspace import RepoInfo

        repo_dir = tmp_path / "widgets"
        repo_dir.mkdir()
        keys = conversation_activity.build_repo_keys(
            [RepoInfo(name="widgets", path=repo_dir, upstream_slug="acme/widgets")]
        )
        expected_key = str(repo_dir.resolve()).replace("/", "-")
        assert keys == {expected_key: "widgets"}


class TestCollect:
    def test_raises_when_no_vertex_project(self):
        with (
            patch.dict("os.environ", {}, clear=True),
            pytest.raises(RuntimeError, match="Vertex project not set"),
        ):
            conversation_activity.collect(
                date(2026, 4, 20),
                date(2030, 12, 31),
                repo_keys=_REPO_KEYS,
            )

    def test_returns_empty_when_no_conversations(self, tmp_path):
        with (
            patch.dict("os.environ", {"GOOGLE_CLOUD_PROJECT": "test-project"}),
            patch.object(conversation_activity, "_CLAUDE_PROJECTS_DIR", tmp_path),
            patch.object(conversation_activity, "_OMP_SESSIONS_DIR", tmp_path / "omp"),
        ):
            result = conversation_activity.collect(
                date(2026, 4, 20),
                date(2030, 12, 31),
                repo_keys=_REPO_KEYS,
            )

        assert result == []

    def test_short_conversation_passes_through_without_compression(self, tmp_path):
        project_dir = tmp_path / "-Users-ada-src-widgets"
        project_dir.mkdir()
        conv = project_dir / "session-alpha.jsonl"
        _write_conversation(
            conv,
            [
                _make_user_msg("Fix the auth bug"),
                _make_assistant_msg("Fixed by updating the token validation"),
            ],
        )

        mock_client_instance = MagicMock()

        with (
            patch.dict("os.environ", {"GOOGLE_CLOUD_PROJECT": "test-project"}),
            patch.object(conversation_activity, "_CLAUDE_PROJECTS_DIR", tmp_path),
            patch.object(conversation_activity, "_OMP_SESSIONS_DIR", tmp_path / "omp"),
            patch("anthropic.AsyncAnthropicVertex", return_value=mock_client_instance),
        ):
            result = conversation_activity.collect(
                date(2026, 4, 20),
                date(2030, 12, 31),
                repo_keys=_REPO_KEYS,
            )

        assert len(result) == 1
        assert result[0]["project"] == "/Users/ada/src/widgets"
        assert result[0]["conversation_id"] == "session-alpha"
        assert "Fix the auth bug" in result[0]["context"]
        assert result[0]["repos_touched"] == ["widgets"]
        mock_client_instance.messages.create.assert_not_called()

    def test_short_conversation_redacts_secrets_on_uncompressed_path(self, tmp_path):
        project_dir = tmp_path / "-Users-ada-src-widgets"
        project_dir.mkdir()
        conv = project_dir / "session-short-secret.jsonl"
        fake_tok = "xox" + "c-shortpathsecrettoken999"
        _write_conversation(
            conv,
            [
                _make_user_msg(f"Debug token {fake_tok}"),
                _make_assistant_msg("Token validated"),
            ],
        )

        mock_client_instance = MagicMock()

        with (
            patch.dict("os.environ", {"GOOGLE_CLOUD_PROJECT": "test-project"}),
            patch.object(conversation_activity, "_CLAUDE_PROJECTS_DIR", tmp_path),
            patch.object(conversation_activity, "_OMP_SESSIONS_DIR", tmp_path / "omp"),
            patch("anthropic.AsyncAnthropicVertex", return_value=mock_client_instance),
        ):
            result = conversation_activity.collect(
                date(2026, 4, 20),
                date(2030, 12, 31),
                repo_keys=_REPO_KEYS,
            )

        assert len(result) == 1
        assert "shortpathsecrettoken999" not in result[0]["context"]
        assert "[REDACTED]" in result[0]["context"]
        mock_client_instance.messages.create.assert_not_called()

    def test_long_conversation_gets_compressed_and_redacts_secrets_before_egress(self, tmp_path):
        project_dir = tmp_path / "-Users-ada-src-widgets"
        project_dir.mkdir()
        conv = project_dir / "long-session.jsonl"
        fake_cookie = "xox" + "d-secretcookievalue123456"
        msgs = [_make_user_msg(f"Message {i} {fake_cookie} " * 150) for i in range(20)]
        _write_conversation(conv, msgs)

        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Compressed conversation content.")]

        mock_client_instance = MagicMock()
        mock_client_instance.messages.create = AsyncMock(return_value=mock_response)

        with (
            patch.dict("os.environ", {"GOOGLE_CLOUD_PROJECT": "test-project"}),
            patch.object(conversation_activity, "_CLAUDE_PROJECTS_DIR", tmp_path),
            patch.object(conversation_activity, "_OMP_SESSIONS_DIR", tmp_path / "omp"),
            patch("anthropic.AsyncAnthropicVertex", return_value=mock_client_instance),
        ):
            result = conversation_activity.collect(
                date(2026, 4, 20),
                date(2030, 12, 31),
                repo_keys=_REPO_KEYS,
            )

        assert len(result) == 1
        assert "Compressed conversation content" in result[0]["context"]
        assert mock_client_instance.messages.create.call_count >= 1
        for call in mock_client_instance.messages.create.call_args_list:
            sent_payload = str(call.kwargs.get("messages", ""))
            assert "secretcookievalue123456" not in sent_payload

    def test_compression_failure_falls_back_to_redacted_truncated_raw(self, tmp_path):
        project_dir = tmp_path / "-Users-ada-src-widgets"
        project_dir.mkdir()
        conv = project_dir / "fail-compress.jsonl"
        fake_bearer = "Bearer supersecrettokenvalue"
        msgs = [_make_user_msg(f"Message {i} {fake_bearer} " * 150) for i in range(20)]
        _write_conversation(conv, msgs)

        mock_client_instance = MagicMock()
        mock_client_instance.messages.create = AsyncMock(side_effect=Exception("API error"))

        with (
            patch.dict("os.environ", {"GOOGLE_CLOUD_PROJECT": "test-project"}),
            patch.object(conversation_activity, "_CLAUDE_PROJECTS_DIR", tmp_path),
            patch.object(conversation_activity, "_OMP_SESSIONS_DIR", tmp_path / "omp"),
            patch("anthropic.AsyncAnthropicVertex", return_value=mock_client_instance),
        ):
            result = conversation_activity.collect(
                date(2026, 4, 20),
                date(2030, 12, 31),
                repo_keys=_REPO_KEYS,
            )

        assert len(result) == 1
        assert "Message 0" in result[0]["context"]
        assert "supersecrettokenvalue" not in result[0]["context"]

    def test_empty_chunk_compression_filtered(self, tmp_path):
        project_dir = tmp_path / "-Users-ada-src-widgets"
        project_dir.mkdir()
        conv = project_dir / "empty-session.jsonl"
        msgs = [_make_user_msg("x " * 800) for _ in range(20)]
        _write_conversation(conv, msgs)

        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="EMPTY")]

        mock_client_instance = MagicMock()
        mock_client_instance.messages.create = AsyncMock(return_value=mock_response)

        with (
            patch.dict("os.environ", {"GOOGLE_CLOUD_PROJECT": "test-project"}),
            patch.object(conversation_activity, "_CLAUDE_PROJECTS_DIR", tmp_path),
            patch.object(conversation_activity, "_OMP_SESSIONS_DIR", tmp_path / "omp"),
            patch("anthropic.AsyncAnthropicVertex", return_value=mock_client_instance),
        ):
            result = conversation_activity.collect(
                date(2026, 4, 20),
                date(2030, 12, 31),
                repo_keys=_REPO_KEYS,
            )

        assert result == []
