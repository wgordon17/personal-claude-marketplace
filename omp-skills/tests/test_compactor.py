import json
import os
import sys
from unittest.mock import MagicMock, mock_open, patch

import pytest

# Add scripts to path to import compactor
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../scripts")))
import compactor


def test_detect_memory_dir(tmp_path):
    hack_dir = tmp_path / "hack"
    hack_dir.mkdir()
    (hack_dir / "PROJECT.md").write_text("test")
    (hack_dir / "TODO.md").write_text("test")

    scratch_dir = tmp_path / "scratch"
    scratch_dir.mkdir()

    def mock_isdir(path):
        return path in ["hack", "scratch"]

    def mock_exists(path):
        return path in ["hack/PROJECT.md", "hack/TODO.md"]

    with (
        patch("os.path.isdir", side_effect=mock_isdir),
        patch("os.path.exists", side_effect=mock_exists),
    ):
        assert compactor.detect_memory_dir() == "hack"


def test_update_project_memory(tmp_path):
    mem_dir = tmp_path / "hack"
    mem_dir.mkdir()
    project_md = mem_dir / "PROJECT.md"
    project_md.write_text("# Initial Project\n")

    compactor.update_project_memory(str(mem_dir), ["Constraint 1"], ["Rationale 1"])

    content = project_md.read_text()
    assert "## Session Compaction Inbox" in content
    assert "### Unbreakable Constraints" in content


@patch("urllib.request.urlopen")
@patch("os.path.exists")
def test_main_success(mock_exists, mock_urlopen, tmp_path):
    session_id = "test_123"

    def mock_exists_impl(path):
        return path in ["hack", "hack/PROJECT.md", "hack/TODO.md"]

    mock_exists.side_effect = mock_exists_impl

    mock_history = [
        {"role": "user", "content": "Update the API"},
        {
            "role": "assistant",
            "tool_calls": [{"name": "edit", "arguments": '{"path": "src/api.ts"}'}],
        },
    ]

    mock_llm_response = {
        "choices": [{"message": {"content": '{"discovered_constraints": ["Must use proxy"]}'}}]
    }

    mock_response_obj = MagicMock()
    mock_response_obj.read.return_value = json.dumps(mock_llm_response).encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_response_obj

    with (
        patch("sys.stdin.read", return_value="\n".join([json.dumps(m) for m in mock_history])),
        patch("compactor.detect_memory_dir", return_value="hack"),
        patch("compactor.update_project_memory") as mock_update,
        patch("sys.argv", ["compactor.py", session_id]),
        patch("os.makedirs"),
        patch("os.open"),
        patch("os.fdopen", mock_open()),
    ):
        compactor.main()
        mock_update.assert_called_once_with("hack", ["Must use proxy"], [])

        # Verify the actual history was passed to the LLM
        req_arg = mock_urlopen.call_args[0][0]
        payload_sent = json.loads(req_arg.data.decode("utf-8"))
        assert "Update the API" in payload_sent["messages"][1]["content"]


@patch("urllib.request.urlopen")
@patch("os.path.exists")
def test_main_api_failure_fallback(mock_exists, mock_urlopen, tmp_path):
    session_id = "test_123"

    def mock_exists_impl(path):
        return False

    mock_exists.side_effect = mock_exists_impl

    mock_history = [{"role": "user", "content": "Update"}]

    # Force an Exception in the urllib call
    mock_urlopen.side_effect = Exception("API Timeout")

    with (
        patch("sys.stdin.read", return_value="\n".join([json.dumps(m) for m in mock_history])),
        patch("sys.argv", ["compactor.py", session_id]),
        patch("os.makedirs"),
        patch("os.open"),
        patch("os.fdopen", mock_open()),
    ):
        # Should not raise exception, should use fallback semantic structure
        compactor.main()


def test_main_missing_args():
    with patch("sys.argv", ["compactor.py"]), pytest.raises(SystemExit):
        compactor.main()


def test_main_invalid_json_stdin():
    session_id = "test_123"
    with (
        patch("sys.argv", ["compactor.py", session_id]),
        patch("sys.stdin.read", return_value="invalid json"),
        pytest.raises(SystemExit),
    ):
        compactor.main()
