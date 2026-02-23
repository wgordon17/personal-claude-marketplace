"""Tests for cmux-hook.py

Comprehensive tests for CMUX Integration Hook event handlers and utilities.
Uses mocking for subprocess calls and stdin parsing.
"""

import importlib.util
import io
import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest

SCRIPT = Path(__file__).parent.parent / "hooks" / "cmux-hook.py"
spec = importlib.util.spec_from_file_location("cmux_hook", SCRIPT)
cmux_hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cmux_hook)


# ═══════════════════════════════════════════════════════════════════════════════
# _first_sentence() tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestFirstSentence:
    """Tests for _first_sentence() extraction and truncation."""

    def test_normal_sentence_with_period(self):
        result = cmux_hook._first_sentence("Hello world. More text.")
        assert result == "Hello world."

    def test_sentence_ending_with_exclamation(self):
        result = cmux_hook._first_sentence("Done! Next.")
        assert result == "Done!"

    def test_sentence_ending_with_question(self):
        result = cmux_hook._first_sentence("Really? Yes.")
        assert result == "Really?"

    def test_multiline_with_newline(self):
        result = cmux_hook._first_sentence("First line.\nSecond line.")
        assert result == "First line."

    def test_empty_string(self):
        result = cmux_hook._first_sentence("")
        assert result == ""

    def test_none_input(self):
        result = cmux_hook._first_sentence(None)
        assert result == ""

    def test_no_sentence_boundary(self):
        result = cmux_hook._first_sentence("No punctuation here")
        assert result == "No punctuation here"

    def test_very_long_text_truncated(self):
        long_text = "A" * 130 + ". More text."
        result = cmux_hook._first_sentence(long_text)
        assert result == "A" * 117 + "..."
        assert len(result) == 120

    def test_very_long_first_sentence_truncated(self):
        long_sentence = "A" * 150 + ". Next."
        result = cmux_hook._first_sentence(long_sentence)
        assert result == "A" * 117 + "..."
        assert len(result) == 120

    def test_sentence_with_trailing_whitespace(self):
        result = cmux_hook._first_sentence("  Hello world.  More text.")
        assert result == "Hello world."

    def test_sentence_with_multiple_spaces_after_period(self):
        result = cmux_hook._first_sentence("First.   Second.")
        assert result == "First."


# ═══════════════════════════════════════════════════════════════════════════════
# _parse_hook_input() tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestParseHookInput:
    """Tests for _parse_hook_input() JSON parsing."""

    def test_valid_json_on_stdin(self):
        payload = json.dumps({"hook_event_name": "Stop", "data": "test"})
        with mock.patch("sys.stdin.buffer.read", return_value=payload.encode()):
            result = cmux_hook._parse_hook_input()
            assert result == {"hook_event_name": "Stop", "data": "test"}

    def test_invalid_json_exits_zero(self):
        with (
            mock.patch("sys.stdin.buffer.read", return_value=b"not json"),
            mock.patch("sys.stderr"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                cmux_hook._parse_hook_input()
            assert exc_info.value.code == 0

    def test_empty_stdin_exits_zero(self):
        with mock.patch("sys.stdin.buffer.read", return_value=b""):
            with pytest.raises(SystemExit) as exc_info:
                cmux_hook._parse_hook_input()
            assert exc_info.value.code == 0

    def test_oversized_input_exits_zero(self):
        large_input = b"x" * (10 * 1024 * 1024 + 1)
        with mock.patch("sys.stdin.buffer.read", return_value=large_input):
            with pytest.raises(SystemExit) as exc_info:
                cmux_hook._parse_hook_input()
            assert exc_info.value.code == 0

    def test_error_printed_to_stderr(self):
        with (
            mock.patch("sys.stdin.buffer.read", return_value=b"invalid"),
            mock.patch("sys.stderr"),
            pytest.raises(SystemExit),
        ):
            cmux_hook._parse_hook_input()


# ═══════════════════════════════════════════════════════════════════════════════
# _cmux_available() tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestCmuxAvailable:
    """Tests for _cmux_available() subprocess check and caching."""

    def setup_method(self):
        cmux_hook._CMUX_AVAILABLE = None

    def test_cmux_ping_success(self):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            result = cmux_hook._cmux_available()
            assert result is True
            mock_run.assert_called_once_with(["cmux", "ping"], capture_output=True, timeout=2)

    def test_cmux_ping_failure(self):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=1)
            result = cmux_hook._cmux_available()
            assert result is False

    def test_cmux_not_in_path(self):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            result = cmux_hook._cmux_available()
            assert result is False

    def test_cmux_timeout(self):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("cmux", 2)
            result = cmux_hook._cmux_available()
            assert result is False

    def test_cmux_os_error(self):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = OSError()
            result = cmux_hook._cmux_available()
            assert result is False

    def test_result_cached(self):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            result1 = cmux_hook._cmux_available()
            result2 = cmux_hook._cmux_available()
            assert result1 is True
            assert result2 is True
            assert mock_run.call_count == 1


# ═══════════════════════════════════════════════════════════════════════════════
# _cmux() tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestCmuxCall:
    """Tests for _cmux() fire-and-forget subprocess calls."""

    def test_successful_call(self):
        with mock.patch("subprocess.run") as mock_run:
            cmux_hook._cmux("notify", "--title", "Test", "--body", "Message")
            mock_run.assert_called_once_with(
                ["cmux", "notify", "--title", "Test", "--body", "Message"],
                capture_output=True,
                timeout=5,
            )

    def test_exception_suppressed(self):
        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = RuntimeError("Test error")
            cmux_hook._cmux("notify", "--title", "Test", "--body", "Message")


# ═══════════════════════════════════════════════════════════════════════════════
# Handler tests (with _cmux mocked)
# ═══════════════════════════════════════════════════════════════════════════════


class TestHandleNotification:
    """Tests for _handle_notification() event handler."""

    def test_permission_prompt_type(self):
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {"notification_type": "permission_prompt", "message": "Need permission"}
            cmux_hook._handle_notification(data)
            mock_cmux.assert_called_once_with(
                "notify", "--title", "Permission Needed", "--body", "Need permission"
            )

    def test_idle_prompt_type(self):
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {"notification_type": "idle_prompt", "message": "Claude is idle"}
            cmux_hook._handle_notification(data)
            mock_cmux.assert_called_once_with(
                "notify", "--title", "Claude Idle", "--body", "Claude is idle"
            )

    def test_auth_success_type(self):
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {"notification_type": "auth_success", "message": "Authenticated"}
            cmux_hook._handle_notification(data)
            mock_cmux.assert_called_once_with(
                "notify", "--title", "Auth Success", "--body", "Authenticated"
            )

    def test_elicitation_dialog_type(self):
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {"notification_type": "elicitation_dialog", "message": "Respond please"}
            cmux_hook._handle_notification(data)
            mock_cmux.assert_called_once_with(
                "notify", "--title", "Claude Code", "--body", "Respond please"
            )

    def test_unknown_type_uses_title_field(self):
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {
                "notification_type": "unknown_type",
                "message": "Message body",
                "title": "Custom Title",
            }
            cmux_hook._handle_notification(data)
            mock_cmux.assert_called_once_with(
                "notify", "--title", "Custom Title", "--body", "Message body"
            )

    def test_unknown_type_fallback_to_default(self):
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {"notification_type": "unknown_type", "message": "Message"}
            cmux_hook._handle_notification(data)
            mock_cmux.assert_called_once_with(
                "notify", "--title", "Claude Code", "--body", "Message"
            )

    def test_empty_message_uses_empty_body(self):
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {"notification_type": "permission_prompt"}
            cmux_hook._handle_notification(data)
            mock_cmux.assert_called_once_with(
                "notify", "--title", "Permission Needed", "--body", ""
            )


class TestHandleSubagentStop:
    """Tests for _handle_subagent_stop() event handler."""

    def test_with_last_message(self):
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {
                "agent_type": "architect",
                "last_assistant_message": "Analysis complete. Summary here.",
            }
            cmux_hook._handle_subagent_stop(data)
            mock_cmux.assert_called_once_with(
                "notify",
                "--title",
                "Agent Complete",
                "--body",
                "Agent architect finished: Analysis complete.",
            )

    def test_without_last_message(self):
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {"agent_type": "security"}
            cmux_hook._handle_subagent_stop(data)
            mock_cmux.assert_called_once_with(
                "notify", "--title", "Agent Complete", "--body", "Agent security finished"
            )

    def test_unknown_agent_type(self):
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {}
            cmux_hook._handle_subagent_stop(data)
            mock_cmux.assert_called_once_with(
                "notify", "--title", "Agent Complete", "--body", "Agent unknown finished"
            )


class TestHandleStop:
    """Tests for _handle_stop() event handler."""

    def test_stop_hook_active_returns_early(self):
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {"stop_hook_active": True}
            cmux_hook._handle_stop(data)
            mock_cmux.assert_not_called()

    def test_stop_notifies(self):
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {"stop_hook_active": False}
            cmux_hook._handle_stop(data)
            mock_cmux.assert_called_once_with(
                "notify", "--title", "Claude Code", "--body", "Work complete"
            )

    def test_stop_with_last_message(self):
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {
                "stop_hook_active": False,
                "last_assistant_message": "Task finished. Done.",
            }
            cmux_hook._handle_stop(data)
            mock_cmux.assert_called_once_with(
                "notify", "--title", "Claude Code", "--body", "Work complete: Task finished."
            )


class TestHandleSessionStart:
    """Tests for _handle_session_start() event handler."""

    def test_no_legacy_hook(self):
        with (
            mock.patch.object(cmux_hook, "_cmux") as mock_cmux,
            mock.patch("pathlib.Path.exists", return_value=False),
            mock.patch("sys.stderr"),
        ):
            cmux_hook._handle_session_start({})
            mock_cmux.assert_not_called()

    def test_legacy_hook_exists_prints_warning(self):
        with (
            mock.patch.object(cmux_hook, "_cmux"),
            mock.patch("pathlib.Path.exists", return_value=True),
            mock.patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
        ):
            cmux_hook._handle_session_start({})
            stderr_value = mock_stderr.getvalue()
            assert "cmux-notify.sh" in stderr_value
            assert "duplicate" in stderr_value


class TestHandleSessionEnd:
    """Tests for _handle_session_end() — no-op until sidebar commands ship."""

    def test_session_end_is_noop(self):
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            cmux_hook._handle_session_end({})
            mock_cmux.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# main() integration tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestMain:
    """Integration tests for main() dispatcher."""

    def setup_method(self):
        cmux_hook._CMUX_AVAILABLE = None

    def test_unknown_event_name_no_handler(self):
        with (
            mock.patch.object(cmux_hook, "_cmux_available", return_value=True),
            mock.patch.object(cmux_hook, "_parse_hook_input") as mock_parse,
            mock.patch.object(cmux_hook, "_cmux") as mock_cmux,
        ):
            mock_parse.return_value = {"hook_event_name": "UnknownEvent"}
            with pytest.raises(SystemExit) as exc_info:
                cmux_hook.main()
            assert exc_info.value.code == 0
            mock_cmux.assert_not_called()

    def test_cmux_unavailable_exits_early(self):
        with (
            mock.patch.object(cmux_hook, "_cmux_available", return_value=False),
            mock.patch.object(cmux_hook, "_parse_hook_input") as mock_parse,
            mock.patch.object(cmux_hook, "_cmux") as mock_cmux,
        ):
            mock_parse.return_value = {"hook_event_name": "Stop"}
            with pytest.raises(SystemExit) as exc_info:
                cmux_hook.main()
            assert exc_info.value.code == 0
            mock_cmux.assert_not_called()

    def test_notification_event_dispatches(self):
        with (
            mock.patch.object(cmux_hook, "_cmux_available", return_value=True),
            mock.patch.object(cmux_hook, "_parse_hook_input") as mock_parse,
            mock.patch.object(cmux_hook, "_cmux") as mock_cmux,
        ):
            mock_parse.return_value = {
                "hook_event_name": "Notification",
                "notification_type": "permission_prompt",
                "message": "Test",
            }
            with pytest.raises(SystemExit) as exc_info:
                cmux_hook.main()
            assert exc_info.value.code == 0
            assert mock_cmux.called

    def test_stop_event_dispatches(self):
        with (
            mock.patch.object(cmux_hook, "_cmux_available", return_value=True),
            mock.patch.object(cmux_hook, "_parse_hook_input") as mock_parse,
            mock.patch.object(cmux_hook, "_cmux") as mock_cmux,
        ):
            mock_parse.return_value = {"hook_event_name": "Stop", "stop_hook_active": False}
            with pytest.raises(SystemExit) as exc_info:
                cmux_hook.main()
            assert exc_info.value.code == 0
            assert mock_cmux.called

    def test_subagent_stop_event_dispatches(self):
        with (
            mock.patch.object(cmux_hook, "_cmux_available", return_value=True),
            mock.patch.object(cmux_hook, "_parse_hook_input") as mock_parse,
            mock.patch.object(cmux_hook, "_cmux") as mock_cmux,
        ):
            mock_parse.return_value = {
                "hook_event_name": "SubagentStop",
                "agent_type": "architect",
            }
            with pytest.raises(SystemExit) as exc_info:
                cmux_hook.main()
            assert exc_info.value.code == 0
            assert mock_cmux.called

    def test_session_start_event_dispatches(self):
        with (
            mock.patch.object(cmux_hook, "_cmux_available", return_value=True),
            mock.patch.object(cmux_hook, "_parse_hook_input") as mock_parse,
            mock.patch("pathlib.Path.exists", return_value=False),
            mock.patch("sys.stderr"),
        ):
            mock_parse.return_value = {"hook_event_name": "SessionStart"}
            with pytest.raises(SystemExit) as exc_info:
                cmux_hook.main()
            assert exc_info.value.code == 0
