"""Tests for git-instructions.sh allowlist validation and URL parsing.

Exercises the UPSTREAM_REPO and ORIGIN_OWNER allowlist patterns, and
the parse_url_owner / parse_url_owner_repo extraction functions.
"""

import re
import subprocess
from pathlib import Path

UPSTREAM_REPO_PATTERN = r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$"
ORIGIN_OWNER_PATTERN = r"^[a-zA-Z0-9_.-]+$"

GIT_INSTRUCTIONS_SH = (
    Path(__file__).resolve().parents[2] / "git-tools" / "scripts" / "git-instructions.sh"
)


def _matches(pattern: str, value: str) -> bool:
    return bool(re.search(pattern, value))


class TestUpstreamRepoAllowlist:
    def test_accepts_simple_owner_repo(self):
        assert _matches(UPSTREAM_REPO_PATTERN, "owner/repo")

    def test_accepts_dotted_names(self):
        assert _matches(UPSTREAM_REPO_PATTERN, "my.org/my.repo")

    def test_accepts_hyphens_underscores(self):
        assert _matches(UPSTREAM_REPO_PATTERN, "my-org/my_repo")

    def test_rejects_empty(self):
        assert not _matches(UPSTREAM_REPO_PATTERN, "")

    def test_rejects_no_slash(self):
        assert not _matches(UPSTREAM_REPO_PATTERN, "ownerrepo")

    def test_rejects_triple_segment(self):
        assert not _matches(UPSTREAM_REPO_PATTERN, "a/b/c")

    def test_rejects_backtick(self):
        assert not _matches(UPSTREAM_REPO_PATTERN, "owner/repo`id`")

    def test_rejects_backslash(self):
        assert not _matches(UPSTREAM_REPO_PATTERN, r"owner/repo\ninjected")

    def test_rejects_semicolon(self):
        assert not _matches(UPSTREAM_REPO_PATTERN, "owner/repo;echo pwned")

    def test_rejects_space(self):
        assert not _matches(UPSTREAM_REPO_PATTERN, "owner/ repo")

    def test_rejects_hash(self):
        assert not _matches(UPSTREAM_REPO_PATTERN, "owner/repo#comment")

    def test_rejects_dollar(self):
        assert not _matches(UPSTREAM_REPO_PATTERN, "owner$HOME/repo")


class TestOriginOwnerAllowlist:
    def test_accepts_simple_owner(self):
        assert _matches(ORIGIN_OWNER_PATTERN, "myuser")

    def test_accepts_dotted(self):
        assert _matches(ORIGIN_OWNER_PATTERN, "my.user")

    def test_accepts_hyphens_underscores(self):
        assert _matches(ORIGIN_OWNER_PATTERN, "my-user_name")

    def test_rejects_empty(self):
        assert not _matches(ORIGIN_OWNER_PATTERN, "")

    def test_rejects_slash(self):
        assert not _matches(ORIGIN_OWNER_PATTERN, "owner/repo")

    def test_rejects_backtick(self):
        assert not _matches(ORIGIN_OWNER_PATTERN, "user`id`")

    def test_rejects_backslash(self):
        assert not _matches(ORIGIN_OWNER_PATTERN, r"user\ninjected")

    def test_rejects_semicolon(self):
        assert not _matches(ORIGIN_OWNER_PATTERN, "user;echo pwned")

    def test_rejects_space(self):
        assert not _matches(ORIGIN_OWNER_PATTERN, "my user")

    def test_rejects_dollar(self):
        assert not _matches(ORIGIN_OWNER_PATTERN, "user$HOME")


class TestPatternSync:
    """Verify test constants match the patterns in git-instructions.sh."""

    def test_upstream_repo_pattern_in_script(self):
        script = GIT_INSTRUCTIONS_SH.read_text()
        assert f"'{UPSTREAM_REPO_PATTERN}'" in script, (
            f"UPSTREAM_REPO_PATTERN {UPSTREAM_REPO_PATTERN!r} not found in script"
        )

    def test_origin_owner_pattern_in_script(self):
        script = GIT_INSTRUCTIONS_SH.read_text()
        assert f"'{ORIGIN_OWNER_PATTERN}'" in script, (
            f"ORIGIN_OWNER_PATTERN {ORIGIN_OWNER_PATTERN!r} not found in script"
        )


def _extract_bash_functions() -> str:
    """Extract parse_url_owner and parse_url_owner_repo from git-instructions.sh."""
    lines = GIT_INSTRUCTIONS_SH.read_text().splitlines()
    funcs: list[str] = []
    in_func = False
    for line in lines:
        if line.startswith(("parse_url_owner()", "parse_url_owner_repo()")):
            in_func = True
        if in_func:
            funcs.append(line)
            if line == "}":
                in_func = False
    return "\n".join(funcs)


def _call_bash_func(func_name: str, url: str) -> str:
    """Call an extracted bash function with the given URL argument."""
    funcs = _extract_bash_functions()
    result = subprocess.run(
        ["bash", "-c", f'{funcs}\n{func_name} "$1"', "_", url],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


class TestParseUrlOwner:
    def test_scp_style_ssh(self):
        assert _call_bash_func("parse_url_owner", "git@github.com:owner/repo.git") == "owner"

    def test_scp_style_ssh_no_dotgit(self):
        assert _call_bash_func("parse_url_owner", "git@github.com:owner/repo") == "owner"

    def test_https(self):
        assert _call_bash_func("parse_url_owner", "https://github.com/owner/repo.git") == "owner"

    def test_https_no_dotgit(self):
        assert _call_bash_func("parse_url_owner", "https://github.com/owner/repo") == "owner"

    def test_ssh_protocol(self):
        assert _call_bash_func("parse_url_owner", "ssh://git@github.com/owner/repo.git") == "owner"

    def test_ssh_with_port(self):
        assert (
            _call_bash_func("parse_url_owner", "ssh://git@github.com:22/owner/repo.git") == "owner"
        )

    def test_empty_url(self):
        assert _call_bash_func("parse_url_owner", "") == ""


class TestParseUrlOwnerRepo:
    def test_scp_style_ssh(self):
        assert (
            _call_bash_func("parse_url_owner_repo", "git@github.com:owner/repo.git") == "owner/repo"
        )

    def test_scp_style_ssh_no_dotgit(self):
        assert _call_bash_func("parse_url_owner_repo", "git@github.com:owner/repo") == "owner/repo"

    def test_https(self):
        assert (
            _call_bash_func("parse_url_owner_repo", "https://github.com/owner/repo.git")
            == "owner/repo"
        )

    def test_https_no_dotgit(self):
        assert (
            _call_bash_func("parse_url_owner_repo", "https://github.com/owner/repo") == "owner/repo"
        )

    def test_ssh_protocol(self):
        assert (
            _call_bash_func("parse_url_owner_repo", "ssh://git@github.com/owner/repo.git")
            == "owner/repo"
        )

    def test_ssh_with_port(self):
        assert (
            _call_bash_func("parse_url_owner_repo", "ssh://git@github.com:22/owner/repo.git")
            == "owner/repo"
        )

    def test_empty_url(self):
        assert _call_bash_func("parse_url_owner_repo", "") == ""
