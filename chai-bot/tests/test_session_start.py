"""Tests for chai-bot/hooks/session-start.sh's SessionStart hook.

QA-A: session-start.sh previously had no test coverage. Black-box subprocess
tests against the REAL session-start.sh -- CLAUDE_PLUGIN_ROOT points at a
temp plugin-root directory containing a shimmed hooks/check-availability.sh
(so the outcome is deterministic with zero git/network I/O) and a
references/chai-guidance.md copied verbatim from the real one. This mirrors
test_git_remote_gate.py's / test_availability_probe.py's shim pattern.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
SCRIPT = REPO_ROOT / "chai-bot" / "hooks" / "session-start.sh"
REAL_GUIDANCE_FILE = REPO_ROOT / "chai-bot" / "references" / "chai-guidance.md"


def _make_plugin_root(
    tmp_path: Path, *, availability_exit_code: int, with_guidance: bool = True
) -> Path:
    """Build a fake CLAUDE_PLUGIN_ROOT: hooks/check-availability.sh shimmed to
    exit with the given code (no git/network involved at all) plus, when
    with_guidance, references/chai-guidance.md copied verbatim from the real
    file so a passing test also pins the exact real content."""
    plugin_root = tmp_path / "plugin-root"
    hooks_dir = plugin_root / "hooks"
    hooks_dir.mkdir(parents=True)
    shim = f"#!/usr/bin/env bash\nexit {availability_exit_code}\n"
    shim_path = hooks_dir / "check-availability.sh"
    shim_path.write_text(shim)
    shim_path.chmod(shim_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    if with_guidance:
        references_dir = plugin_root / "references"
        references_dir.mkdir(parents=True)
        shutil.copyfile(REAL_GUIDANCE_FILE, references_dir / "chai-guidance.md")

    return plugin_root


def _run_script(*, plugin_root: Path | None) -> subprocess.CompletedProcess:
    env = {**os.environ}
    if plugin_root is None:
        env.pop("CLAUDE_PLUGIN_ROOT", None)
    else:
        env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)
    return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)


class TestPluginRootUnset:
    def test_unset_plugin_root_exits_zero_no_stdout(self):
        """CLAUDE_PLUGIN_ROOT unset entirely -- exit 0, no stdout, no attempt
        to even invoke check-availability.sh."""
        result = _run_script(plugin_root=None)
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""


class TestUnavailable:
    @pytest.mark.parametrize("availability_exit_code", [1, 2])
    def test_unavailable_exits_zero_no_stdout(self, tmp_path, availability_exit_code):
        """check-availability.sh exits 1 (not eligible) or 2 (unreachable) --
        either way, the SessionStart nudge stays silent and exits 0."""
        plugin_root = _make_plugin_root(tmp_path, availability_exit_code=availability_exit_code)
        result = _run_script(plugin_root=plugin_root)
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""


class TestAvailable:
    def test_available_exits_zero_and_prints_guidance_verbatim(self, tmp_path):
        """check-availability.sh exits 0 -- exit 0 AND stdout equals the
        exact contents of references/chai-guidance.md."""
        plugin_root = _make_plugin_root(tmp_path, availability_exit_code=0)
        result = _run_script(plugin_root=plugin_root)
        assert result.returncode == 0, result.stderr
        assert result.stdout == REAL_GUIDANCE_FILE.read_text()
