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
        """Extract sentence ending with period."""
        result = cmux_hook._first_sentence("Hello world. More text.")
        assert result == "Hello world."

    def test_sentence_ending_with_exclamation(self):
        """Extract sentence ending with exclamation mark."""
        result = cmux_hook._first_sentence("Done! Next.")
        assert result == "Done!"

    def test_sentence_ending_with_question(self):
        """Extract sentence ending with question mark."""
        result = cmux_hook._first_sentence("Really? Yes.")
        assert result == "Really?"

    def test_multiline_with_newline(self):
        """Extract first sentence from multi-line text with newline."""
        result = cmux_hook._first_sentence("First line.\nSecond line.")
        assert result == "First line."

    def test_empty_string(self):
        """Empty string returns empty string."""
        result = cmux_hook._first_sentence("")
        assert result == ""

    def test_none_input(self):
        """None input returns empty string."""
        result = cmux_hook._first_sentence(None)
        assert result == ""

    def test_no_sentence_boundary(self):
        """Text without sentence boundary returns full text."""
        result = cmux_hook._first_sentence("No punctuation here")
        assert result == "No punctuation here"

    def test_very_long_text_truncated(self):
        """Text longer than 120 chars is truncated with '...'."""
        long_text = "A" * 130 + ". More text."
        result = cmux_hook._first_sentence(long_text)
        assert result == "A" * 117 + "..."
        assert len(result) == 120

    def test_very_long_first_sentence_truncated(self):
        """Very long first sentence is truncated with '...'."""
        long_sentence = "A" * 150 + ". Next."
        result = cmux_hook._first_sentence(long_sentence)
        assert result == "A" * 117 + "..."
        assert len(result) == 120

    def test_sentence_with_trailing_whitespace(self):
        """Whitespace is stripped from result."""
        result = cmux_hook._first_sentence("  Hello world.  More text.")
        assert result == "Hello world."

    def test_sentence_with_multiple_spaces_after_period(self):
        """Period followed by multiple spaces still marks sentence boundary."""
        result = cmux_hook._first_sentence("First.   Second.")
        assert result == "First."


# ═══════════════════════════════════════════════════════════════════════════════
# _parse_hook_input() tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestParseHookInput:
    """Tests for _parse_hook_input() JSON parsing."""

    def test_valid_json_on_stdin(self):
        """Valid JSON payload is parsed and returned."""
        payload = json.dumps({"hook_event_name": "Stop", "data": "test"})
        with mock.patch("sys.stdin.buffer.read", return_value=payload.encode()):
            result = cmux_hook._parse_hook_input()
            assert result == {"hook_event_name": "Stop", "data": "test"}

    def test_invalid_json_exits_zero(self):
        """Invalid JSON causes SystemExit with code 0 (fail-open)."""
        with (
            mock.patch("sys.stdin.buffer.read", return_value=b"not json"),
            mock.patch("sys.stderr"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                cmux_hook._parse_hook_input()
            assert exc_info.value.code == 0

    def test_empty_stdin_exits_zero(self):
        """Empty stdin causes SystemExit with code 0."""
        with mock.patch("sys.stdin.buffer.read", return_value=b""):
            with pytest.raises(SystemExit) as exc_info:
                cmux_hook._parse_hook_input()
            assert exc_info.value.code == 0

    def test_oversized_input_exits_zero(self):
        """Input larger than _MAX_INPUT causes SystemExit with code 0."""
        large_input = b"x" * (10 * 1024 * 1024 + 1)
        with mock.patch("sys.stdin.buffer.read", return_value=large_input):
            with pytest.raises(SystemExit) as exc_info:
                cmux_hook._parse_hook_input()
            assert exc_info.value.code == 0

    def test_error_printed_to_stderr(self):
        """Parse errors are printed to stderr."""
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
        """Reset global cache before each test."""
        cmux_hook._CMUX_AVAILABLE = None

    def test_cmux_ping_success(self):
        """Successful cmux ping returns True."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            result = cmux_hook._cmux_available()
            assert result is True
            mock_run.assert_called_once_with(["cmux", "ping"], capture_output=True, timeout=2)

    def test_cmux_ping_failure(self):
        """Failed cmux ping (returncode 1) returns False."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=1)
            result = cmux_hook._cmux_available()
            assert result is False

    def test_cmux_not_in_path(self):
        """FileNotFoundError when cmux not in PATH returns False."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            result = cmux_hook._cmux_available()
            assert result is False

    def test_cmux_timeout(self):
        """TimeoutExpired exception returns False."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("cmux", 2)
            result = cmux_hook._cmux_available()
            assert result is False

    def test_cmux_os_error(self):
        """OSError exception returns False."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = OSError()
            result = cmux_hook._cmux_available()
            assert result is False

    def test_result_cached(self):
        """Result is cached; second call doesn't run subprocess."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            result1 = cmux_hook._cmux_available()
            result2 = cmux_hook._cmux_available()
            assert result1 is True
            assert result2 is True
            # Should only be called once
            assert mock_run.call_count == 1


# ═══════════════════════════════════════════════════════════════════════════════
# _cmux() tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestCmuxCall:
    """Tests for _cmux() fire-and-forget subprocess calls."""

    def test_successful_call(self):
        """Successful subprocess call completes without error."""
        with mock.patch("subprocess.run") as mock_run:
            cmux_hook._cmux("notify", "--title", "Test", "--body", "Message")
            mock_run.assert_called_once_with(
                ["cmux", "notify", "--title", "Test", "--body", "Message"],
                capture_output=True,
                timeout=5,
            )

    def test_exception_suppressed(self):
        """Exceptions during call are swallowed."""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.side_effect = RuntimeError("Test error")
            # Should not raise
            cmux_hook._cmux("notify", "--title", "Test", "--body", "Message")


# ═══════════════════════════════════════════════════════════════════════════════
# Handler tests (with _cmux mocked)
# ═══════════════════════════════════════════════════════════════════════════════


class TestHandleNotification:
    """Tests for _handle_notification() event handler."""

    def test_permission_prompt_type(self):
        """permission_prompt notification gets 'Permission Needed' title."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {
                "notification_type": "permission_prompt",
                "message": "Need permission",
            }
            cmux_hook._handle_notification(data)
            mock_cmux.assert_called_once_with(
                "notify",
                "--title",
                "Permission Needed",
                "--body",
                "Need permission",
            )

    def test_idle_prompt_type(self):
        """idle_prompt notification gets 'Claude Idle' title."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {"notification_type": "idle_prompt", "message": "Claude is idle"}
            cmux_hook._handle_notification(data)
            mock_cmux.assert_called_once_with(
                "notify", "--title", "Claude Idle", "--body", "Claude is idle"
            )

    def test_auth_success_type(self):
        """auth_success notification gets 'Auth Success' title."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {
                "notification_type": "auth_success",
                "message": "Authenticated",
            }
            cmux_hook._handle_notification(data)
            mock_cmux.assert_called_once_with(
                "notify", "--title", "Auth Success", "--body", "Authenticated"
            )

    def test_elicitation_dialog_type(self):
        """elicitation_dialog notification gets 'Claude Code' title."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {
                "notification_type": "elicitation_dialog",
                "message": "Respond please",
            }
            cmux_hook._handle_notification(data)
            mock_cmux.assert_called_once_with(
                "notify",
                "--title",
                "Claude Code",
                "--body",
                "Respond please",
            )

    def test_unknown_type_uses_title_field(self):
        """Unknown type uses custom title field."""
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
        """Unknown type without title field falls back to 'Claude Code'."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {"notification_type": "unknown_type", "message": "Message"}
            cmux_hook._handle_notification(data)
            mock_cmux.assert_called_once_with(
                "notify", "--title", "Claude Code", "--body", "Message"
            )

    def test_empty_message_uses_empty_body(self):
        """Missing message field results in empty body."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {"notification_type": "permission_prompt"}
            cmux_hook._handle_notification(data)
            mock_cmux.assert_called_once_with(
                "notify", "--title", "Permission Needed", "--body", ""
            )


class TestHandleSubagentStop:
    """Tests for _handle_subagent_stop() event handler."""

    def test_with_last_message(self):
        """Subagent stop with last_assistant_message includes message in notification."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {
                "agent_type": "architect",
                "last_assistant_message": "Analysis complete. Summary here.",
            }
            cmux_hook._handle_subagent_stop(data)
            # Should call notify and log
            calls = mock_cmux.call_args_list
            assert len(calls) == 2
            notify_call = calls[0]
            log_call = calls[1]
            # Check notify call
            assert notify_call[0][0] == "notify"
            assert "Agent Complete" in notify_call[0]
            # Check log call
            assert log_call[0][0] == "log"
            assert "Agent architect finished: Analysis complete." in " ".join(
                str(x) for x in log_call[0]
            )

    def test_without_last_message(self):
        """Subagent stop without last_assistant_message uses simple text."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {"agent_type": "security"}
            cmux_hook._handle_subagent_stop(data)
            calls = mock_cmux.call_args_list
            assert len(calls) == 2
            # Check log call
            log_call = calls[1]
            assert "Agent security finished" in log_call[0]

    def test_unknown_agent_type(self):
        """Unknown agent type shows 'unknown' in message."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {}
            cmux_hook._handle_subagent_stop(data)
            calls = mock_cmux.call_args_list
            assert len(calls) == 2
            log_call = calls[1]
            assert "Agent unknown finished" in log_call[0]


class TestHandleStop:
    """Tests for _handle_stop() event handler."""

    def test_stop_hook_active_returns_early(self):
        """When stop_hook_active=True, handler returns early without cmux calls."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {"stop_hook_active": True}
            cmux_hook._handle_stop(data)
            # Should not call _cmux
            mock_cmux.assert_not_called()

    def test_stop_hook_inactive_sets_status(self):
        """When stop_hook_active=False, sets complete status."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {"stop_hook_active": False}
            cmux_hook._handle_stop(data)
            calls = mock_cmux.call_args_list
            # Should have set-status call
            set_status_call = calls[0]
            assert set_status_call[0][0] == "set-status"
            assert "claude-session" in set_status_call[0]
            assert "complete" in set_status_call[0]

    def test_stop_with_last_message(self):
        """Stop with last_assistant_message includes it in notification."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {
                "stop_hook_active": False,
                "last_assistant_message": "Task finished. Done.",
            }
            cmux_hook._handle_stop(data)
            calls = mock_cmux.call_args_list
            # Check notify call (should be 3rd call after set-status and clear-status)
            notify_call = calls[2]
            assert notify_call[0][0] == "notify"
            assert "Task finished." in " ".join(str(x) for x in notify_call[0])

    def test_stop_without_last_message(self):
        """Stop without last_assistant_message uses simple text."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {"stop_hook_active": False}
            cmux_hook._handle_stop(data)
            calls = mock_cmux.call_args_list
            notify_call = calls[2]
            assert "Work complete" in notify_call[0]


class TestHandleSessionStart:
    """Tests for _handle_session_start() event handler."""

    def test_no_legacy_hook(self):
        """Session start without legacy cmux-notify.sh skips warning."""
        with (
            mock.patch.object(cmux_hook, "_cmux") as mock_cmux,
            mock.patch("pathlib.Path.exists", return_value=False),
            mock.patch("sys.stderr"),
        ):
            data = {}
            cmux_hook._handle_session_start(data)
            # Should call _cmux but not print warning
            calls = mock_cmux.call_args_list
            assert len(calls) == 2
            assert calls[0][0][0] == "set-status"

    def test_legacy_hook_exists_prints_warning(self):
        """Session start with legacy cmux-notify.sh prints warning to stderr."""
        with (
            mock.patch.object(cmux_hook, "_cmux"),
            mock.patch("pathlib.Path.exists", return_value=True),
            mock.patch("sys.stderr", new_callable=io.StringIO) as mock_stderr,
        ):
            data = {}
            cmux_hook._handle_session_start(data)
            # Should print warning
            stderr_value = mock_stderr.getvalue()
            assert "cmux-notify.sh" in stderr_value
            assert "duplicate" in stderr_value

    def test_sets_session_active_status(self):
        """Session start sets active status."""
        with (
            mock.patch.object(cmux_hook, "_cmux") as mock_cmux,
            mock.patch("pathlib.Path.exists", return_value=False),
            mock.patch("sys.stderr"),
        ):
            data = {}
            cmux_hook._handle_session_start(data)
            set_status_call = mock_cmux.call_args_list[0]
            assert "claude-session" in set_status_call[0]
            assert "active" in set_status_call[0]

    def test_logs_session_started(self):
        """Session start logs 'Session started' message."""
        with (
            mock.patch.object(cmux_hook, "_cmux") as mock_cmux,
            mock.patch("pathlib.Path.exists", return_value=False),
            mock.patch("sys.stderr"),
        ):
            data = {}
            cmux_hook._handle_session_start(data)
            log_call = mock_cmux.call_args_list[1]
            assert log_call[0][0] == "log"
            assert "Session started" in log_call[0]


class TestHandleSessionEnd:
    """Tests for _handle_session_end() event handler."""

    def test_clears_session_status(self):
        """Session end clears claude-session status."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {}
            cmux_hook._handle_session_end(data)
            calls = mock_cmux.call_args_list
            clear_session_call = calls[0]
            assert clear_session_call[0][0] == "clear-status"
            assert "claude-session" in clear_session_call[0]

    def test_clears_activity_status(self):
        """Session end clears claude-activity status."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {}
            cmux_hook._handle_session_end(data)
            calls = mock_cmux.call_args_list
            clear_activity_call = calls[1]
            assert clear_activity_call[0][0] == "clear-status"
            assert "claude-activity" in clear_activity_call[0]

    def test_logs_session_ended(self):
        """Session end logs 'Session ended' message."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {}
            cmux_hook._handle_session_end(data)
            calls = mock_cmux.call_args_list
            log_call = calls[2]
            assert log_call[0][0] == "log"
            assert "Session ended" in log_call[0]


class TestHandlePreToolUse:
    """Tests for _handle_pre_tool_use() event handler."""

    def test_bash_tool_sets_running_status(self):
        """Bash tool sets 'Running' status with command."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {
                "tool_name": "Bash",
                "tool_input": {"command": "git status"},
            }
            cmux_hook._handle_pre_tool_use(data)
            mock_cmux.assert_called_once()
            call_args = mock_cmux.call_args[0]
            assert call_args[0] == "set-status"
            assert "Running: git status" in call_args
            assert "⏳" in call_args

    def test_bash_tool_truncates_long_command(self):
        """Bash tool truncates command to 60 chars."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            long_cmd = "x" * 100
            data = {
                "tool_name": "Bash",
                "tool_input": {"command": long_cmd},
            }
            cmux_hook._handle_pre_tool_use(data)
            call_args = mock_cmux.call_args[0]
            assert len(call_args[2]) <= 60 + len("Running: ")

    def test_task_tool_sets_spawning_status(self):
        """Task tool sets 'Spawning' status with description."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {
                "tool_name": "Task",
                "tool_input": {"description": "Running tests"},
            }
            cmux_hook._handle_pre_tool_use(data)
            mock_cmux.assert_called_once()
            call_args = mock_cmux.call_args[0]
            assert call_args[0] == "set-status"
            assert "Spawning: Running tests" in call_args
            assert "🚀" in call_args

    def test_task_tool_truncates_long_description(self):
        """Task tool truncates description to 60 chars."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            long_desc = "x" * 100
            data = {
                "tool_name": "Task",
                "tool_input": {"description": long_desc},
            }
            cmux_hook._handle_pre_tool_use(data)
            call_args = mock_cmux.call_args[0]
            assert len(call_args[2]) <= 60 + len("Spawning: ")

    def test_unknown_tool_no_call(self):
        """Unknown tool name results in no cmux call."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {"tool_name": "UnknownTool", "tool_input": {}}
            cmux_hook._handle_pre_tool_use(data)
            mock_cmux.assert_not_called()


class TestHandlePostToolUse:
    """Tests for _handle_post_tool_use() event handler."""

    def test_clears_activity_status(self):
        """Post tool use clears activity status."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {"tool_response": "Success"}
            cmux_hook._handle_post_tool_use(data)
            calls = mock_cmux.call_args_list
            clear_call = calls[0]
            assert clear_call[0][0] == "clear-status"
            assert "claude-activity" in clear_call[0]

    def test_string_response_extracts_first_sentence(self):
        """String response extracts first sentence for logging."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {
                "tool_response": "First sentence. Second sentence.",
            }
            cmux_hook._handle_post_tool_use(data)
            calls = mock_cmux.call_args_list
            log_call = calls[1]
            assert "First sentence." in " ".join(str(x) for x in log_call[0])

    def test_dict_response_extracts_stdout(self):
        """Dict response extracts first sentence from stdout."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {
                "tool_response": {
                    "stdout": "Output here. More output.",
                    "returncode": 0,
                }
            }
            cmux_hook._handle_post_tool_use(data)
            calls = mock_cmux.call_args_list
            log_call = calls[1]
            assert "Output here." in " ".join(str(x) for x in log_call[0])

    def test_no_response_uses_simple_message(self):
        """Empty response uses 'Task completed' without summary."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {"tool_response": ""}
            cmux_hook._handle_post_tool_use(data)
            calls = mock_cmux.call_args_list
            log_call = calls[1]
            assert "Task completed" in log_call[0]

    def test_dict_without_stdout_no_summary(self):
        """Dict without stdout uses simple message."""
        with mock.patch.object(cmux_hook, "_cmux") as mock_cmux:
            data = {"tool_response": {"returncode": 0}}
            cmux_hook._handle_post_tool_use(data)
            calls = mock_cmux.call_args_list
            log_call = calls[1]
            assert "Task completed" in log_call[0]


# ═══════════════════════════════════════════════════════════════════════════════
# main() integration tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestMain:
    """Integration tests for main() dispatcher."""

    def setup_method(self):
        """Reset global cache before each test."""
        cmux_hook._CMUX_AVAILABLE = None

    def test_unknown_event_name_no_handler(self):
        """Unknown event name doesn't call any handler."""
        with (
            mock.patch.object(cmux_hook, "_cmux_available", return_value=True),
            mock.patch.object(cmux_hook, "_parse_hook_input") as mock_parse,
            mock.patch.object(cmux_hook, "_cmux") as mock_cmux,
        ):
            mock_parse.return_value = {"hook_event_name": "UnknownEvent"}
            with pytest.raises(SystemExit) as exc_info:
                cmux_hook.main()
            assert exc_info.value.code == 0
            # Should not call _cmux
            mock_cmux.assert_not_called()

    def test_cmux_unavailable_exits_early(self):
        """If cmux unavailable, exits 0 without calling handlers."""
        with (
            mock.patch.object(cmux_hook, "_cmux_available", return_value=False),
            mock.patch.object(cmux_hook, "_parse_hook_input") as mock_parse,
            mock.patch.object(cmux_hook, "_cmux") as mock_cmux,
        ):
            mock_parse.return_value = {"hook_event_name": "Stop"}
            with pytest.raises(SystemExit) as exc_info:
                cmux_hook.main()
            assert exc_info.value.code == 0
            # Should not call _cmux
            mock_cmux.assert_not_called()

    def test_notification_event_dispatches_handler(self):
        """Notification event dispatches to _handle_notification."""
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
            # Should call _cmux
            assert mock_cmux.called

    def test_stop_event_dispatches_handler(self):
        """Stop event dispatches to _handle_stop."""
        with (
            mock.patch.object(cmux_hook, "_cmux_available", return_value=True),
            mock.patch.object(cmux_hook, "_parse_hook_input") as mock_parse,
            mock.patch.object(cmux_hook, "_cmux") as mock_cmux,
        ):
            mock_parse.return_value = {
                "hook_event_name": "Stop",
                "stop_hook_active": False,
            }
            with pytest.raises(SystemExit) as exc_info:
                cmux_hook.main()
            assert exc_info.value.code == 0
            # Should call _cmux
            assert mock_cmux.called

    def test_subagent_stop_event_dispatches_handler(self):
        """SubagentStop event dispatches to _handle_subagent_stop."""
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
            # Should call _cmux
            assert mock_cmux.called

    def test_session_start_event_dispatches_handler(self):
        """SessionStart event dispatches to _handle_session_start."""
        with (
            mock.patch.object(cmux_hook, "_cmux_available", return_value=True),
            mock.patch.object(cmux_hook, "_parse_hook_input") as mock_parse,
            mock.patch.object(cmux_hook, "_cmux") as mock_cmux,
            mock.patch("pathlib.Path.exists", return_value=False),
            mock.patch("sys.stderr"),
        ):
            mock_parse.return_value = {
                "hook_event_name": "SessionStart",
            }
            with pytest.raises(SystemExit) as exc_info:
                cmux_hook.main()
            assert exc_info.value.code == 0
            # Should call _cmux
            assert mock_cmux.called

    def test_session_end_event_dispatches_handler(self):
        """SessionEnd event dispatches to _handle_session_end."""
        with (
            mock.patch.object(cmux_hook, "_cmux_available", return_value=True),
            mock.patch.object(cmux_hook, "_parse_hook_input") as mock_parse,
            mock.patch.object(cmux_hook, "_cmux") as mock_cmux,
        ):
            mock_parse.return_value = {
                "hook_event_name": "SessionEnd",
            }
            with pytest.raises(SystemExit) as exc_info:
                cmux_hook.main()
            assert exc_info.value.code == 0
            # Should call _cmux
            assert mock_cmux.called

    def test_pre_tool_use_event_dispatches_handler(self):
        """PreToolUse event dispatches to _handle_pre_tool_use."""
        with (
            mock.patch.object(cmux_hook, "_cmux_available", return_value=True),
            mock.patch.object(cmux_hook, "_parse_hook_input") as mock_parse,
            mock.patch.object(cmux_hook, "_cmux") as mock_cmux,
        ):
            mock_parse.return_value = {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
            }
            with pytest.raises(SystemExit) as exc_info:
                cmux_hook.main()
            assert exc_info.value.code == 0
            # Should call _cmux
            assert mock_cmux.called

    def test_post_tool_use_event_dispatches_handler(self):
        """PostToolUse event dispatches to _handle_post_tool_use."""
        with (
            mock.patch.object(cmux_hook, "_cmux_available", return_value=True),
            mock.patch.object(cmux_hook, "_parse_hook_input") as mock_parse,
            mock.patch.object(cmux_hook, "_cmux") as mock_cmux,
        ):
            mock_parse.return_value = {
                "hook_event_name": "PostToolUse",
                "tool_response": "Success",
            }
            with pytest.raises(SystemExit) as exc_info:
                cmux_hook.main()
            assert exc_info.value.code == 0
            # Should call _cmux
            assert mock_cmux.called
