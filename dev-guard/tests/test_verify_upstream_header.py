"""Black-box subprocess tests for .github/scripts/verify-upstream-header.sh
— the header-strip + safety-check logic invoked by sync-token-efficiency.yml
before an upstream fetch is trusted. Previously inline YAML with zero test
coverage; the Unicode bidi-control/zero-width guard here is a genuine
Trojan-Source-style (CVE-2021-42574 pattern) security control that was only
ever exercised live, by the real weekly cron against real upstream content.

BSD grep (macOS's default `grep`) does not support `-P` at all -- running
these tests uncovered that the script's original `if grep -P ...; then`
form couldn't distinguish "no match" from "grep itself failed to run,"
which would silently skip the security check on any platform/runner
without GNU grep. The Unicode-detection tests below prefer a `-P`-capable
grep (GNU grep, commonly available as `ggrep` via Homebrew on macOS) on
PATH so they verify the real character-class regex, not just the
fail-safe fallback; if none is available, they verify the fail-safe
behavior instead of silently passing.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
VERIFY_SCRIPT = REPO_ROOT / ".github" / "scripts" / "verify-upstream-header.sh"

_VALID_RAW = "BENJAMIN-PLUS MODE ACTIVE\n\n# Benjamin-Plus\n\nSome rule content here.\n"


def _grep_supports_dash_p() -> bool:
    result = subprocess.run(["grep", "-P", "x"], input="x", capture_output=True, text=True)
    return result.returncode in (0, 1)


def _run_script(*args: str, path: str | None = None) -> subprocess.CompletedProcess:
    import os

    env = {**os.environ}
    if path is not None:
        env["PATH"] = path
    return subprocess.run(
        ["bash", str(VERIFY_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def _path_with_dash_p_grep(tmp_path: Path) -> str:
    """Build a PATH where `grep` resolves to a -P-capable binary: the
    system grep if it already supports -P, otherwise `ggrep` (GNU grep,
    typically installed via `brew install grep`) symlinked as `grep`."""
    import os

    if _grep_supports_dash_p():
        return os.environ["PATH"]
    ggrep = shutil.which("ggrep")
    if ggrep is None:
        return os.environ["PATH"]
    bin_dir = tmp_path / "grep-shim"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "grep").symlink_to(ggrep)
    return f"{bin_dir}:{os.environ['PATH']}"


def _has_dash_p_grep_available() -> bool:
    return _grep_supports_dash_p() or shutil.which("ggrep") is not None


class TestVerifyUpstreamHeaderScript:
    """Black-box subprocess tests for verify-upstream-header.sh."""

    @pytest.mark.skipif(
        not _has_dash_p_grep_available(),
        reason="no -P-capable grep (GNU grep / ggrep) available to verify real detection",
    )
    def test_valid_header_strips_and_passes(self, tmp_path):
        raw = tmp_path / "raw.md"
        raw.write_text(_VALID_RAW)
        out = tmp_path / "stripped.md"
        result = _run_script(str(raw), str(out), path=_path_with_dash_p_grep(tmp_path))
        assert result.returncode == 0, result.stderr
        stripped = out.read_text()
        assert stripped.startswith("# Benjamin-Plus")
        assert "BENJAMIN-PLUS MODE ACTIVE" not in stripped

    def test_missing_benjamin_plus_heading_exits_1(self, tmp_path):
        raw = tmp_path / "raw.md"
        raw.write_text("BENJAMIN-PLUS MODE ACTIVE\n\nSomething else entirely\n")
        out = tmp_path / "stripped.md"
        result = _run_script(str(raw), str(out))
        assert result.returncode == 1
        assert "does not start with '# Benjamin-Plus'" in result.stderr

    def test_residual_mode_active_text_exits_1(self, tmp_path):
        """A duplicated/misplaced header marker surviving the strip must be
        caught even when the first-line check otherwise passes."""
        raw = tmp_path / "raw.md"
        raw.write_text(
            "BENJAMIN-PLUS MODE ACTIVE\n\n"
            "# Benjamin-Plus\n\n"
            "Some text mentioning BENJAMIN-PLUS MODE ACTIVE later in the doc.\n"
        )
        out = tmp_path / "stripped.md"
        result = _run_script(str(raw), str(out))
        assert result.returncode == 1
        assert "still contains 'BENJAMIN-PLUS MODE ACTIVE'" in result.stderr

    @pytest.mark.skipif(
        not _has_dash_p_grep_available(),
        reason="no -P-capable grep (GNU grep / ggrep) available to verify real detection",
    )
    def test_bidi_control_character_exits_1(self, tmp_path):
        raw = tmp_path / "raw.md"
        raw.write_text(
            "BENJAMIN-PLUS MODE ACTIVE\n\n# Benjamin-Plus\n\nInnocuous text‮hidden reversed text\n"
        )
        out = tmp_path / "stripped.md"
        result = _run_script(str(raw), str(out), path=_path_with_dash_p_grep(tmp_path))
        assert result.returncode == 1
        assert "Fetched content contains Unicode bidi-control or zero-width characters" in (
            result.stderr
        )

    @pytest.mark.skipif(
        not _has_dash_p_grep_available(),
        reason="no -P-capable grep (GNU grep / ggrep) available to verify real detection",
    )
    def test_zero_width_character_exits_1(self, tmp_path):
        raw = tmp_path / "raw.md"
        raw.write_text(
            "BENJAMIN-PLUS MODE ACTIVE\n\n# Benjamin-Plus\n\n"
            "Split​word to evade a literal-text scan\n"
        )
        out = tmp_path / "stripped.md"
        result = _run_script(str(raw), str(out), path=_path_with_dash_p_grep(tmp_path))
        assert result.returncode == 1
        assert "Fetched content contains Unicode bidi-control or zero-width characters" in (
            result.stderr
        )

    def test_unicode_check_fails_safe_when_dash_p_unsupported(self, tmp_path):
        """If the runtime grep doesn't support -P at all (e.g. BSD grep on
        macOS, unlike the GNU grep on GitHub Actions runners), the script
        must refuse to sync rather than silently treating the unusable
        check as 'no dangerous characters found.'"""
        bin_dir = tmp_path / "no-dash-p-grep"
        bin_dir.mkdir()
        fake_grep = bin_dir / "grep"
        fake_grep.write_text(
            "#!/usr/bin/env bash\n"
            'for arg in "$@"; do\n'
            '  if [[ "$arg" == "-P" ]]; then\n'
            '    echo "grep: invalid option -- P" >&2\n'
            "    exit 2\n"
            "  fi\n"
            "done\n"
            'exec /usr/bin/grep "$@"\n'
        )
        fake_grep.chmod(0o755)

        raw = tmp_path / "raw.md"
        raw.write_text(_VALID_RAW)
        out = tmp_path / "stripped.md"
        result = _run_script(str(raw), str(out), path=f"{bin_dir}:/usr/bin:/bin")
        assert result.returncode == 1
        assert "check itself failed to run" in result.stderr

    def test_missing_arguments_exits_1(self, tmp_path):
        raw = tmp_path / "raw.md"
        raw.write_text(_VALID_RAW)
        result = _run_script(str(raw))
        assert result.returncode == 1
        assert "requires <raw-file>" in result.stderr

    def test_nonexistent_raw_file_exits_1(self, tmp_path):
        result = _run_script(str(tmp_path / "missing.md"), str(tmp_path / "out.md"))
        assert result.returncode == 1
        assert "requires <raw-file>" in result.stderr
