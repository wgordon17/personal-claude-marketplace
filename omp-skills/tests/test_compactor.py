import json
import os
import sys
from unittest.mock import MagicMock, mock_open, patch

# Add scripts to path to import compactor
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../scripts")))
import compactor


def test_detect_memory_dir(tmp_path):
    # Test priority 1: hack/ with 2 core files
    hack_dir = tmp_path / "hack"
    hack_dir.mkdir()
    (hack_dir / "PROJECT.md").write_text("test")
    (hack_dir / "TODO.md").write_text("test")

    # Test priority 3: scratch/ with no core files (should fail)
    scratch_dir = tmp_path / "scratch"
    scratch_dir.mkdir()

    with patch("os.path.isdir", side_effect=lambda d: d in [str(hack_dir), str(scratch_dir)]):
        with patch(
            "os.path.exists",
            side_effect=lambda f: f in [str(hack_dir / "PROJECT.md"), str(hack_dir / "TODO.md")],
        ):
            # We mock the working directory behavior or just pass base paths if we adapt the function
            # Since compactor uses relative paths, we mock the specific path checks
            def mock_isdir(path):
                return path in ["hack", "scratch"]

            def mock_exists(path):
                return path in ["hack/PROJECT.md", "hack/TODO.md"]

            with patch("os.path.isdir", side_effect=mock_isdir):
                with patch("os.path.exists", side_effect=mock_exists):
                    assert compactor.detect_memory_dir() == "hack"


def test_update_project_memory(tmp_path):
    mem_dir = tmp_path / "hack"
    mem_dir.mkdir()
    project_md = mem_dir / "PROJECT.md"
    project_md.write_text("# Initial Project\n")

    compactor.update_project_memory(str(mem_dir), ["Constraint 1"], ["Rationale 1"])

    content = project_md.read_text()
    assert "## Newly Discovered Context" in content
    assert "### Unbreakable Constraints" in content
    assert "- Constraint 1" in content
    assert "### Architectural Rationale" in content
    assert "- Rationale 1" in content


@patch("urllib.request.urlopen")
@patch("os.path.exists")
def test_main_success(mock_exists, mock_urlopen, tmp_path):
    session_id = "test_123"

    # Mock history file existence
    def mock_exists_impl(path):
        if path == f"/tmp/history_{session_id}.json":
            return True
        if path == "hack":
            return True
        if path == "hack/PROJECT.md":
            return True
        if path == "hack/TODO.md":
            return True
        return False

    mock_exists.side_effect = mock_exists_impl

    mock_history = [
        {"role": "user", "content": "Update the API"},
        {
            "role": "assistant",
            "tool_calls": [{"name": "edit", "arguments": '{"path": "src/api.ts"}'}],
        },
    ]

    mock_llm_response = {
        "choices": [
            {
                "message": {
                    "content": '{"discovered_constraints": ["Must use proxy"], "architectural_decisions": ["LiteLLM"]}'
                }
            }
        ]
    }

    mock_response_obj = MagicMock()
    mock_response_obj.read.return_value = json.dumps(mock_llm_response).encode("utf-8")
    mock_urlopen.return_value.__enter__.return_value = mock_response_obj

    with patch("builtins.open", mock_open(read_data=json.dumps(mock_history))):
        with patch("compactor.detect_memory_dir", return_value="hack"):
            with patch("compactor.update_project_memory") as mock_update:
                with patch("sys.argv", ["compactor.py", session_id]):
                    with patch("os.makedirs"):
                        compactor.main()
                        mock_update.assert_called_once_with("hack", ["Must use proxy"], ["LiteLLM"])
