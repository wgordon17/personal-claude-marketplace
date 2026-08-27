"""Tests for check-availability.sh's git-remote org-matching gate (Step 1-3).

Black-box subprocess tests against the real script -- a fake `git` shim on
PATH lets us control the "remote URL" the script sees without touching a
real repo, and CHAI_BOT_BASE_URL is left UNSET so that a matching remote
reaches step 4 and exits 2 (env var missing, treated as unreachable) rather
than attempting any network call. This distinguishes "the gate matched" (exit
2) from "the gate rejected" (exit 1) with zero network I/O either way -- the
network-call assertion (Step 2's job) belongs to test_availability_probe.py.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
SCRIPT = REPO_ROOT / "chai-bot" / "hooks" / "check-availability.sh"

_GIT_SHIM_TEMPLATE = """#!/usr/bin/env bash
if [[ "$1" == "rev-parse" ]]; then
    {rev_parse_body}
elif [[ "$1" == "remote" && "$2" == "get-url" ]]; then
    case "$3" in
        origin) {origin_body} ;;
        upstream) {upstream_body} ;;
        *) exit 1 ;;
    esac
else
    exit 1
fi
"""


def _make_git_shim(
    bin_dir: Path,
    *,
    is_repo: bool = True,
    origin: str | None = None,
    upstream: str | None = None,
) -> None:
    rev_parse_body = 'echo ".git"; exit 0' if is_repo else "exit 1"
    origin_body = f'echo "{origin}"; exit 0' if origin else "exit 1"
    upstream_body = f'echo "{upstream}"; exit 0' if upstream else "exit 1"
    shim = _GIT_SHIM_TEMPLATE.format(
        rev_parse_body=rev_parse_body,
        origin_body=origin_body,
        upstream_body=upstream_body,
    )
    git_path = bin_dir / "git"
    git_path.write_text(shim)
    git_path.chmod(git_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _make_curl_call_counting_shim(bin_dir: Path, call_log: Path) -> None:
    """QA-7: a curl shim that only records that it was called -- used to pin
    "exit 1 with zero network" for the gate's early-exit branches, matching
    test_availability_probe.py's _make_curl_shim pattern."""
    shim = f"""#!/usr/bin/env bash
echo "called" >> "{call_log}"
exit 0
"""
    curl_path = bin_dir / "curl"
    curl_path.write_text(shim)
    curl_path.chmod(curl_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _bin_dir_with_real(tmp_path: Path, *tools: str) -> Path:
    """Build a PATH dir containing real symlinks for the given tools (e.g. bash, curl)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for tool in tools:
        real = shutil.which(tool)
        if real:
            target = bin_dir / tool
            if not target.exists():
                target.symlink_to(real)
    return bin_dir


def _run_script(bin_dir: Path, *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PATH": str(bin_dir)}
    env.pop("CHAI_BOT_BASE_URL", None)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd) if cwd else None,
    )


class TestGitRemoteGateMatches:
    """A matching osac-project remote reaches step 4 -- exit 2 (env var unset), not exit 1."""

    def test_ssh_form_matches(self, tmp_path):
        bin_dir = _bin_dir_with_real(tmp_path, "bash", "curl")
        _make_git_shim(bin_dir, origin="git@github.com:osac-project/x.git")
        result = _run_script(bin_dir)
        assert result.returncode == 2, result.stderr

    def test_https_form_matches(self, tmp_path):
        bin_dir = _bin_dir_with_real(tmp_path, "bash", "curl")
        _make_git_shim(bin_dir, origin="https://github.com/osac-project/x.git")
        result = _run_script(bin_dir)
        assert result.returncode == 2, result.stderr

    def test_upstream_fallback_matches(self, tmp_path):
        """origin missing/fails; upstream is osac-project -- gate still matches."""
        bin_dir = _bin_dir_with_real(tmp_path, "bash", "curl")
        _make_git_shim(bin_dir, origin=None, upstream="git@github.com:osac-project/y.git")
        result = _run_script(bin_dir)
        assert result.returncode == 2, result.stderr

    @pytest.mark.parametrize(
        "remote_url",
        [
            "git@github.com:OSAC-Project/x.git",
            "https://github.com/OSAC-Project/x.git",
        ],
    )
    def test_differently_cased_org_still_matches(self, tmp_path, remote_url):
        """SEC-1: the real osac-project org, but differently cased -- must
        still be treated as eligible (proceed past the gate to exit 2, not
        wrongly rejected with exit 1) now that the match is case-insensitive."""
        bin_dir = _bin_dir_with_real(tmp_path, "bash", "curl")
        _make_git_shim(bin_dir, origin=remote_url)
        result = _run_script(bin_dir)
        assert result.returncode == 2, result.stderr

    @pytest.mark.parametrize(
        "remote_url",
        [
            "ssh://git@github.com/osac-project/x.git",
            "https://user@github.com/osac-project/x.git",
            "https://x-access-token:TOKEN@github.com/osac-project/x.git",
        ],
    )
    def test_scheme_and_userinfo_forms_match(self, tmp_path, remote_url):
        """ADV-1: legitimate osac-project remotes using the ssh:// scheme or an
        embedded userinfo/token must still be treated as eligible (exit 2, not a
        wrong exit-1 rejection). The SEC-1 anchoring must not over-tighten past the
        real forms git accepts, while the host stays boundary-anchored (see the
        lookalike/path-injection rejects below, which still fail)."""
        bin_dir = _bin_dir_with_real(tmp_path, "bash", "curl")
        _make_git_shim(bin_dir, origin=remote_url)
        result = _run_script(bin_dir)
        assert result.returncode == 2, result.stderr


class TestGitRemoteGateRejects:
    """Lookalikes and non-matches exit 1 -- no network call reachable."""

    @pytest.mark.parametrize(
        "remote_url",
        [
            "git@github.com:osac-project-fork/x.git",
            "https://github.com/osac-project-fork/x.git",
            "git@github.com:not-osac-project/x.git",
            "https://github.com/not-osac-project/x.git",
            "git@github.com:some-org/osac-project.git",
            "https://gitlab.com/osac-project/x.git",
            # SEC-1: lookalike host -- "github.com" is only a substring of the
            # actual host, which must not satisfy the anchored scheme/host match.
            "https://faux-github.com/osac-project/x.git",
            # SEC-1: "github.com/osac-project/" appears, but only as a path
            # segment on a completely different host -- must not match.
            "https://evil.example.com/github.com/osac-project/x",
            # ADV-1: "github.com" as userinfo of a different host (the real host
            # is evil.com) -- the userinfo group [^/@]*@ must not let this match.
            "https://github.com@evil.com/osac-project/x.git",
        ],
    )
    def test_lookalike_rejected(self, tmp_path, remote_url):
        bin_dir = _bin_dir_with_real(tmp_path, "bash", "curl")
        _make_git_shim(bin_dir, origin=remote_url)
        result = _run_script(bin_dir)
        assert result.returncode == 1, result.stderr

    def test_neither_remote_exists(self, tmp_path):
        bin_dir = _bin_dir_with_real(tmp_path, "bash", "curl")
        _make_git_shim(bin_dir, origin=None, upstream=None)
        result = _run_script(bin_dir)
        assert result.returncode == 1, result.stderr

    def test_not_a_git_repo(self, tmp_path):
        bin_dir = _bin_dir_with_real(tmp_path, "bash", "curl")
        _make_git_shim(bin_dir, is_repo=False)
        result = _run_script(bin_dir)
        assert result.returncode == 1, result.stderr

    def test_not_a_git_repo_makes_zero_curl_calls(self, tmp_path):
        """QA-7: pins 'exit 1 with zero network' for the not-a-repo branch --
        Step 1's `git rev-parse` failure must short-circuit before any curl
        invocation is even reachable."""
        bin_dir = _bin_dir_with_real(tmp_path, "bash")
        _make_git_shim(bin_dir, is_repo=False)
        call_log = tmp_path / "curl-calls.log"
        _make_curl_call_counting_shim(bin_dir, call_log)
        result = _run_script(bin_dir)
        assert result.returncode == 1, result.stderr
        assert not call_log.exists(), "curl must never be invoked when not in a git repo"

    def test_git_not_installed(self, tmp_path):
        """No git on PATH at all -- `git rev-parse` fails with command-not-found."""
        bin_dir = _bin_dir_with_real(tmp_path, "bash", "curl")
        result = _run_script(bin_dir)
        assert result.returncode == 1, result.stderr

    def test_git_not_installed_makes_zero_curl_calls(self, tmp_path):
        """QA-7: pins 'exit 1 with zero network' when git itself is missing --
        curl is present on PATH but must never be invoked."""
        bin_dir = _bin_dir_with_real(tmp_path, "bash")
        call_log = tmp_path / "curl-calls.log"
        _make_curl_call_counting_shim(bin_dir, call_log)
        result = _run_script(bin_dir)
        assert result.returncode == 1, result.stderr
        assert not call_log.exists(), "curl must never be invoked when git is not installed"

    def test_real_non_git_directory(self, tmp_path):
        """Using the REAL git binary against a directory with no .git at all."""
        bin_dir = _bin_dir_with_real(tmp_path, "bash", "curl", "git")
        empty_dir = tmp_path / "not-a-repo"
        empty_dir.mkdir()
        result = _run_script(bin_dir, cwd=empty_dir)
        assert result.returncode == 1, result.stderr

    def test_real_non_git_directory_makes_zero_curl_calls(self, tmp_path):
        """QA-7: pins 'exit 1 with zero network' using the REAL git binary
        against a genuine non-repo directory -- curl (shimmed only to count
        calls, not to fake success/failure) must never be invoked."""
        bin_dir = _bin_dir_with_real(tmp_path, "bash", "git")
        call_log = tmp_path / "curl-calls.log"
        _make_curl_call_counting_shim(bin_dir, call_log)
        empty_dir = tmp_path / "not-a-repo-2"
        empty_dir.mkdir()
        result = _run_script(bin_dir, cwd=empty_dir)
        assert result.returncode == 1, result.stderr
        assert not call_log.exists(), "curl must never be invoked for a real non-git directory"
