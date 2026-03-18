"""Tests for tool-selection-guard.py

Black-box tests: each test invokes the guard script via subprocess,
feeding it JSON on stdin and asserting exit code + stderr content.
"""

import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), os.pardir, "hooks", "tool-selection-guard.py")


def run_guard(
    tool_name: str,
    tool_input: dict,
    *,
    env: dict | None = None,
    payload_extra: dict | None = None,
) -> subprocess.CompletedProcess:
    """Invoke the guard script with the given tool_name and tool_input.

    payload_extra: additional top-level keys merged into the JSON payload
    (e.g. hook_event_name, tool_response, session_id).
    """
    payload_data: dict = {"tool_name": tool_name, "tool_input": tool_input}
    if payload_extra:
        payload_data.update(payload_extra)
    payload = json.dumps(payload_data)
    return subprocess.run(
        ["uv", "run", SCRIPT],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
    )


def run_bash(command: str) -> subprocess.CompletedProcess:
    """Shorthand: invoke guard as Bash tool."""
    return run_guard("Bash", {"command": command})


def assert_guard(result, expected_exit, expected_msg=None, test_id=""):
    """Common assertion helper."""
    assert result.returncode == expected_exit, (
        f"[{test_id}] Expected exit {expected_exit}, got {result.returncode}. "
        f"stderr: {result.stderr.strip()!r}"
    )
    if expected_msg:
        output = result.stderr + result.stdout
        assert expected_msg in output, (
            f"[{test_id}] Expected '{expected_msg}' in output. stderr: {result.stderr.strip()!r}"
        )


def assert_ask_decision(result, expected_reason_fragment=None, test_id=""):
    """Assert that the hook returned a permissionDecision: ask JSON response.

    Ask decisions exit 0 and output hookSpecificOutput JSON to stdout.
    """
    assert result.returncode == 0, (
        f"[{test_id}] Expected exit 0 for ask decision, got {result.returncode}. "
        f"stderr: {result.stderr.strip()!r}"
    )
    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"[{test_id}] Expected JSON stdout for ask decision, got: {result.stdout.strip()!r}"
        ) from exc
    hook_output = output.get("hookSpecificOutput", {})
    assert hook_output.get("permissionDecision") == "ask", (
        f"[{test_id}] Expected permissionDecision 'ask', got: {hook_output}"
    )
    if expected_reason_fragment:
        reason = hook_output.get("permissionDecisionReason", "")
        assert expected_reason_fragment in reason, (
            f"[{test_id}] Expected '{expected_reason_fragment}' in reason, got: {reason!r}"
        )


def assert_allow_decision(result, expected_reason_fragment=None, test_id=""):
    """Assert that the hook returned a permissionDecision: allow JSON response."""
    assert result.returncode == 0, (
        f"[{test_id}] Expected exit 0 for allow decision, got {result.returncode}. "
        f"stderr: {result.stderr.strip()!r}"
    )
    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"[{test_id}] Expected JSON stdout for allow decision, got: {result.stdout.strip()!r}"
        ) from exc
    hook_output = output.get("hookSpecificOutput", {})
    assert hook_output.get("permissionDecision") == "allow", (
        f"[{test_id}] Expected permissionDecision 'allow', got: {hook_output}"
    )
    if expected_reason_fragment:
        reason = hook_output.get("permissionDecisionReason", "")
        assert expected_reason_fragment in reason, (
            f"[{test_id}] Expected '{expected_reason_fragment}' in reason, got: {reason!r}"
        )


def _run_with_extra_url_rules(tool_name, tool_input, rules_file):
    """Run the guard with a custom extra URL rules file (URL_GUARD_EXTRA_RULES)."""
    env = os.environ.copy()
    env["URL_GUARD_EXTRA_RULES"] = str(rules_file)
    return run_guard(tool_name, tool_input, env=env)


def _run_with_extra_cmd_rules(command, rules_file):
    """Run the guard with a custom extra command rules file (COMMAND_GUARD_EXTRA_RULES)."""
    env = os.environ.copy()
    env["COMMAND_GUARD_EXTRA_RULES"] = str(rules_file)
    return run_guard("Bash", {"command": command}, env=env)


# ═══════════════════════════════════════════════════════════════════════════════
# Category A: Native tool redirections (13 rules)
# ═══════════════════════════════════════════════════════════════════════════════


class TestNativeToolRedirections:
    @pytest.mark.parametrize(
        "command, expected_exit, expected_msg",
        [
            ("cat file.py", 2, "Read tool"),
            ("cat file.py | grep pattern", 0, None),
            ("head -20 file.py", 2, "Read tool"),
            ("git log | head -5", 0, None),
            ("tail -50 file.py", 2, "Read tool"),
            ("ps aux | tail -5", 0, None),
            ("grep -rn pattern src/", 2, "Grep tool"),
            ("grep -r pattern src/ | wc -l", 2, "Grep tool"),
            ("grep -rn pattern src/ 2>/dev/null | head -5", 2, "Grep tool"),
            ("git log | grep fix", 0, None),
            ("rg pattern src/", 2, "Grep tool"),
            ("rg pattern src/ | wc -l", 2, "Grep tool"),
            ('find . -name "*.py"', 2, "Glob tool"),
            ("sed -i s/old/new/g file", 2, "Edit tool"),
            ("awk '{print $1}' f > out", 2, "Edit tool"),
            ("echo hello > output.txt", 2, "Write tool"),
            ("echo test > /dev/null", 0, None),
            ("cat <<EOF > file.py", 2, "Write tool"),
            ("cat <<EOF | grep pattern", 2, "Write tool"),
            ("printf '%s' x > out.txt", 2, "Write tool"),
            ("less file.py", 2, "Pagers"),
            ("more file.py", 2, "Pagers"),
            ("nano file.py", 2, "Interactive editors"),
            ("vim file.py", 2, "Interactive editors"),
            ("vi file.py", 2, "Interactive editors"),
            ("emacs file.py", 2, "Interactive editors"),
            ("ls -la /path", 2, "Glob tool"),
            ("ls /path | grep pattern", 0, None),
        ],
        ids=[
            "cat-file",
            "cat-pipe-allow",
            "head-file",
            "head-pipe-allow",
            "tail-file",
            "tail-pipe-allow",
            "grep-blocked",
            "grep-pipe-wc-blocked",
            "grep-pipe-head-blocked",
            "grep-pipe-allow",
            "rg-blocked",
            "rg-pipe-wc-blocked",
            "find-name",
            "sed-i",
            "awk-redirect",
            "echo-redirect",
            "echo-devnull-allow",
            "cat-heredoc",
            "cat-heredoc-pipe",
            "printf-redirect",
            "less-blocked",
            "more-blocked",
            "nano-blocked",
            "vim-blocked",
            "vi-blocked",
            "emacs-blocked",
            "ls-dir-blocked",
            "ls-pipe-allow",
        ],
    )
    def test_redirections(self, command, expected_exit, expected_msg):
        result = run_bash(command)
        assert_guard(result, expected_exit, expected_msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Category B: Python tooling (14 rules)
# ═══════════════════════════════════════════════════════════════════════════════


class TestPythonTooling:
    @pytest.mark.parametrize(
        "command, expected_exit, expected_msg",
        [
            ("python script.py", 2, "uv run"),
            ("python3 -c 'print(1)'", 2, "uv run"),
            ("python3 -c 'import json; print(json.dumps({}))'", 2, "jq"),
            ("python -c 'import sys,json; json.loads(x)'", 2, "jq"),
            ("uv run python -c 'import json; ...'", 0, None),
            ("uv run script.py", 0, None),
            ("uv run python -c 'x=1'", 0, None),
            ("pip install requests", 2, "uv add"),
            ("pip3 install flask", 2, "uv add"),
            ("pip freeze", 2, "uv pip"),
            ("pip list", 2, "uv pip"),
            ("pip show requests", 2, "uv pip"),
            ("pip uninstall requests", 2, "uv remove"),
            ("pytest tests/", 2, "make py-test"),
            ("uvx pytest tests/", 0, None),
            ("uv run pytest tests/", 0, None),
            ("black src/", 2, "ruff"),
            ("ruff check .", 2, "make py-lint"),
            ("uvx ruff check .", 0, None),
            ("mypy src/", 2, "pyright"),
            ("pyright src/", 2, "make py-lint"),
            ("uvx pyright src/", 0, None),
            ("pre-commit run --all-files", 2, "prek"),
            ("uvx pre-commit run", 2, "prek"),
            ("uv run pre-commit run", 2, "prek"),
            ("prek run --all-files", 2, "make"),
            ("uvx prek run --all-files", 2, "make"),
            ("make prek", 0, None),
            ("ipython", 2, "uv run ipython"),
            ("uv run ipython", 0, None),
            ("tox -e py312", 2, "uvx tox"),
            ("uvx tox -e py312", 0, None),
            ("isort src/", 2, "ruff"),
            ("flake8 src/", 2, "ruff"),
        ],
        ids=[
            "python",
            "python3",
            "python-json-standalone",
            "python-json-sys",
            "uv-run-python-json-allow",
            "uv-run-allow",
            "uv-run-python-allow",
            "pip-install",
            "pip3-install",
            "pip-freeze",
            "pip-list",
            "pip-show",
            "pip-uninstall",
            "pytest",
            "uvx-pytest-allow",
            "uv-run-pytest-allow",
            "black",
            "ruff",
            "uvx-ruff-allow",
            "mypy",
            "pyright",
            "uvx-pyright-allow",
            "pre-commit",
            "uvx-pre-commit",
            "uv-run-pre-commit",
            "prek-direct",
            "uvx-prek",
            "make-prek-allow",
            "ipython",
            "uv-run-ipython-allow",
            "tox",
            "uvx-tox-allow",
            "isort",
            "flake8",
        ],
    )
    def test_python_tooling(self, command, expected_exit, expected_msg):
        result = run_bash(command)
        assert_guard(result, expected_exit, expected_msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Category B2: Rust tooling (3 rules)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRustTooling:
    @pytest.mark.parametrize(
        "command, expected_exit, expected_msg",
        [
            # cargo-lint: check, clippy, fmt
            ("cargo check", 2, "make"),
            ("cargo clippy --all-targets", 2, "make"),
            ("cargo fmt --all --check", 2, "make"),
            ("cargo check 2>&1", 2, "make"),
            # cargo-test: test, nextest
            ("cargo test --all-features", 2, "make"),
            ("cargo nextest run --all-features", 2, "make"),
            # cargo-build
            ("cargo build --release", 2, "make"),
            # chained with cd (real-world pattern)
            ("cd lib/rust && cargo check 2>&1", 2, "make"),
            ("cd lib/rust && cargo clippy -- -D warnings", 2, "make"),
            # env prefix
            ("RUSTFLAGS=-Dwarnings cargo check", 2, "make"),
            # passthrough: non-build cargo subcommands
            ("cargo add serde", 0, None),
            ("cargo install cargo-nextest", 0, None),
            ("cargo update", 0, None),
            ("cargo doc --open", 0, None),
            ("cargo run --release", 0, None),
            ("cargo new my-project", 0, None),
            ("cargo init", 0, None),
        ],
        ids=[
            "cargo-check",
            "cargo-clippy",
            "cargo-fmt",
            "cargo-check-stderr",
            "cargo-test",
            "cargo-nextest",
            "cargo-build",
            "cd-cargo-check",
            "cd-cargo-clippy",
            "env-cargo-check",
            "cargo-add-allow",
            "cargo-install-allow",
            "cargo-update-allow",
            "cargo-doc-allow",
            "cargo-run-allow",
            "cargo-new-allow",
            "cargo-init-allow",
        ],
    )
    def test_rust_tooling(self, command, expected_exit, expected_msg):
        result = run_bash(command)
        assert_guard(result, expected_exit, expected_msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Category C: Project conventions (5 rules)
# ═══════════════════════════════════════════════════════════════════════════════


class TestProjectConventions:
    @pytest.mark.parametrize(
        "command, expected_exit, expected_msg",
        [
            ("bash scripts/ci-check.sh", 2, "make"),
            ("sh install.sh", 2, "make"),
            ("bash -c 'cat file.py'", 2, "Read tool"),
            ("bash -c 'grep -rn pat src/'", 2, "Grep tool"),
            ("bash -c 'python script.py'", 2, "uv run"),
            ("bash -c 'git status'", 2, "directly"),
            ("bash -e script.sh", 0, None),
            ("./scripts/build.sh", 2, "make"),
            ("scripts/deploy.sh prod", 2, "make"),
            ("/usr/local/bin/setup.sh", 2, "make"),
            ("git diff file.sh", 0, None),
            ("chmod +x script.sh", 0, None),
            ("uv run /tmp/test.py", 2, "hack/tmp/"),
            ("cp results.json /tmp/out.json", 2, "hack/tmp/"),
            ("rm /tmp/test-guard.py", 2, "hack/tmp/"),
            ("rm -rf /tmp/test-output/", 2, "hack/tmp/"),
            ("uv run hack/tmp/test.py", 0, None),
            ("mkdir -p hack/tmp", 0, None),
            ("uv run test.py", 0, None),
        ],
        ids=[
            "bash-script",
            "sh-script",
            "bash-c-cat",
            "bash-c-grep",
            "bash-c-python",
            "bash-c-safe-still-blocked",
            "bash-e-allow",
            "dot-script",
            "scripts-dir",
            "abs-path-script",
            "sh-in-arg-not-script-allow",
            "chmod-allow",
            "uv-run-tmp-blocked",
            "tmp-in-cmd-blocked",
            "rm-tmp-blocked",
            "rm-rf-tmp-blocked",
            "hack-tmp-allow",
            "mkdir-hack-tmp-allow",
            "no-tmp-allow",
        ],
    )
    def test_conventions(self, command, expected_exit, expected_msg):
        result = run_bash(command)
        assert_guard(result, expected_exit, expected_msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Claude Code built-in false-positive prevention
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuiltinFalsePositivePrevention:
    """Block commands that trigger Claude Code's built-in permission heuristics.

    These checks run in _check_subcmd before rule matching, similar to the
    bash -c wrapper detection.  The goal is to give actionable guidance
    instead of the cryptic built-in messages like
    "Command contains empty quotes before dash (potential bypass)".
    """

    @pytest.mark.parametrize(
        "command, expected_exit, expected_msg",
        [
            # Process substitution <(...)
            (
                "diff <(grep -n 'a' file1) <(grep -n 'a' file2)",
                2,
                "Process substitution",
            ),
            ("sort <(cat file.txt)", 2, "Process substitution"),
            ("comm -3 <(sort a.txt) <(sort b.txt)", 2, "Process substitution"),
            # Multiline python -c
            (
                'uv run python3 -c "\nimport sys\nprint(sys.version)\n"',
                2,
                "uv run python3",
            ),
            (
                'python3 -c "\nimport argparse\nparser = argparse.ArgumentParser()\n"',
                2,
                "uv run python3",
            ),
            # Single-line python -c should NOT trigger this check
            # (it may be caught by other rules, but not by multiline-python-c)
            ('uv run python3 -c "print(1)"', 0, None),
        ],
        ids=[
            "diff-process-sub",
            "sort-process-sub",
            "comm-process-sub",
            "uv-multiline-python-c",
            "bare-multiline-python-c",
            "single-line-python-c-allow",
        ],
    )
    def test_false_positive_prevention(self, command, expected_exit, expected_msg):
        result = run_bash(command)
        assert_guard(result, expected_exit, expected_msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Category D: Simpler patterns (2 rules)
# ═══════════════════════════════════════════════════════════════════════════════


class TestSimplerPatterns:
    @pytest.mark.parametrize(
        "command, expected_exit, expected_msg",
        [
            ('echo "hello world"', 2, "directly"),
            ('echo "hello" | grep hello', 0, None),
            ('printf "hello world"', 2, "directly"),
            ('printf "%s" x | wc -c', 0, None),
        ],
        ids=["echo-noop", "echo-pipe-allow", "printf-noop", "printf-pipe-allow"],
    )
    def test_simpler_patterns(self, command, expected_exit, expected_msg):
        result = run_bash(command)
        assert_guard(result, expected_exit, expected_msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Category E: Interactive commands (2 rules)
# ═══════════════════════════════════════════════════════════════════════════════


class TestInteractiveCommands:
    @pytest.mark.parametrize(
        "command, expected_exit, expected_msg",
        [
            ("git rebase -i HEAD~3", 2, "git-branchless"),
            ("git rebase --interactive H~3", 2, "Interactive rebase"),
            ("git rebase main", 0, None),
            ("git add -p", 2, "specific file"),
            ("git add --patch file.py", 2, "Interactive git add"),
            ("git add -i", 2, "Interactive git add"),
            ("git add file.py", 0, None),
        ],
        ids=[
            "rebase-i",
            "rebase-interactive",
            "rebase-no-i-allow",
            "add-p",
            "add-patch",
            "add-i",
            "add-file-allow",
        ],
    )
    def test_interactive(self, command, expected_exit, expected_msg):
        result = run_bash(command)
        assert_guard(result, expected_exit, expected_msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Passthrough: Commands that should never be blocked
# ═══════════════════════════════════════════════════════════════════════════════


class TestPassthrough:
    @pytest.mark.parametrize(
        "command",
        [
            "git status",
            "ls",
            "make build",
            "uv sync",
            "docker ps",
            "npm run test",
            "curl https://x.com",
        ],
        ids=[
            "git-status",
            "ls",
            "make",
            "uv-sync",
            "docker",
            "npm",
            "curl",
        ],
    )
    def test_passthrough(self, command):
        result = run_bash(command)
        assert result.returncode == 0, (
            f"Expected passthrough (exit 0), got {result.returncode}. "
            f"stderr: {result.stderr.strip()!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Category F: URL fetch guard (curl/wget + WebFetch)
# ═══════════════════════════════════════════════════════════════════════════════


def run_webfetch(url: str) -> subprocess.CompletedProcess:
    """Shorthand: invoke guard as WebFetch tool."""
    return run_guard("WebFetch", {"url": url, "prompt": "test"})


class TestURLFetchGuard:
    """Bash curl/wget commands against authenticated service URLs."""

    @pytest.mark.parametrize(
        "command, expected_exit, expected_msg",
        [
            # BLOCKED: GitHub API
            ("curl https://api.github.com/repos/org/repo", 2, "mcp__github__"),
            ("curl -s https://api.github.com/repos/org/repo/pulls", 2, "mcp__github__"),
            ("wget https://api.github.com/user", 2, "mcp__github__"),
            # BLOCKED: GitHub auth-gated content
            ("curl https://github.com/org/repo/settings", 2, "mcp__github__"),
            ("curl https://github.com/org/repo/pulls", 2, "mcp__github__"),
            ("curl https://github.com/org/repo/issues", 2, "mcp__github__"),
            ("curl https://github.com/org/repo/actions", 2, "mcp__github__"),
            ("curl https://github.com/org/repo/security", 2, "mcp__github__"),
            # BLOCKED: GitLab public API/raw
            ("curl https://gitlab.com/api/v4/projects", 2, "glab"),
            ("curl https://gitlab.com/org/repo/-/raw/main/f.py", 2, "glab"),
            ("curl https://gitlab.com/org/repo/-/blob/main/f.py", 2, "glab"),
            # BLOCKED: Google
            ("curl https://docs.google.com/document/d/abc123/edit", 2, "Google"),
            ("curl https://drive.google.com/file/d/abc/view", 2, "Google"),
            ("curl https://sheets.google.com/spreadsheets/d/abc", 2, "Google"),
            # BLOCKED: Atlassian/Jira
            (
                "curl https://myorg.atlassian.net/rest/api/3/issue/PROJ-123",
                2,
                "Atlassian",
            ),
            ("curl https://myorg.atlassian.net/wiki/spaces/TEAM", 2, "Atlassian"),
            ("curl https://jira.example.com/rest/api/latest", 2, "jira"),
            # BLOCKED: Slack
            ("curl https://hooks.slack.com/services/T00/B00/xxx", 2, "Slack"),
            ("curl https://api.slack.com/api/chat.postMessage", 2, "Slack"),
            # ALLOWED: public pages / non-guarded domains
            ("curl https://example.com/api/data", 0, None),
            ("curl https://github.com/org/repo", 0, None),
            ("curl https://raw.githubusercontent.com/org/repo/main/README.md", 0, None),
            ("curl https://httpbin.org/get", 0, None),
            ("curl -s https://jsonplaceholder.typicode.com/posts", 0, None),
            ("curl https://gitlab.com/org/repo", 0, None),
            ("curl https://pypi.org/simple/requests", 0, None),
            # ALLOWED: non-curl/wget commands with URLs pass through
            ("git clone https://github.com/org/repo.git", 0, None),
            # BYPASS: ALLOW_FETCH=1
            ("ALLOW_FETCH=1 curl https://api.github.com/repos/org/repo", 0, None),
            # BLOCKED in pipe (curl is first segment)
            (
                "curl https://api.github.com/repos/org/repo | jq .",
                2,
                "mcp__github__",
            ),
        ],
        ids=[
            # Blocked
            "github-api",
            "github-api-pulls",
            "github-api-wget",
            "github-settings",
            "github-pulls",
            "github-issues",
            "github-actions",
            "github-security",
            "gitlab-api",
            "gitlab-raw",
            "gitlab-blob",
            "google-docs",
            "google-drive",
            "google-sheets",
            "atlassian-api",
            "atlassian-wiki",
            "jira-server",
            "slack-hooks",
            "slack-api",
            # Allowed
            "example-com",
            "github-public-repo",
            "github-raw-content",
            "httpbin",
            "jsonplaceholder",
            "gitlab-public-page",
            "pypi",
            "git-clone-not-curl",
            # Bypass
            "bypass-github-api",
            # Pipe
            "curl-pipe-jq",
        ],
    )
    def test_url_fetch_guard(self, command, expected_exit, expected_msg):
        result = run_bash(command)
        assert_guard(result, expected_exit, expected_msg)


class TestWebFetchGuard:
    """WebFetch tool calls against authenticated service URLs."""

    @pytest.mark.parametrize(
        "url, expected_exit, expected_msg",
        [
            # BLOCKED
            ("https://api.github.com/repos/org/repo", 2, "mcp__github__"),
            ("https://docs.google.com/document/d/abc/edit", 2, "Google"),
            ("https://myorg.atlassian.net/rest/api/3/issue/KEY-1", 2, "Atlassian"),
            ("https://myorg.atlassian.net/wiki/spaces/TEAM/page", 2, "Atlassian"),
            ("https://hooks.slack.com/services/T00/B00/xxx", 2, "Slack"),
            ("https://api.slack.com/api/test", 2, "Slack"),
            ("https://github.com/org/repo/settings", 2, "mcp__github__"),
            ("https://gitlab.com/api/v4/projects/123", 2, "glab"),
            ("https://drive.google.com/file/d/abc/view", 2, "Google"),
            # ALLOWED
            ("https://example.com", 0, None),
            ("https://github.com/org/repo", 0, None),
            ("https://docs.python.org/3/library/json.html", 0, None),
            ("https://gitlab.com/org/repo", 0, None),
            ("https://httpbin.org/get", 0, None),
        ],
        ids=[
            "github-api",
            "google-docs",
            "atlassian-api",
            "atlassian-wiki",
            "slack-hooks",
            "slack-api",
            "github-settings",
            "gitlab-api",
            "google-drive",
            "example-com",
            "github-public",
            "python-docs",
            "gitlab-public",
            "httpbin",
        ],
    )
    def test_webfetch_guard(self, url, expected_exit, expected_msg):
        result = run_webfetch(url)
        assert_guard(result, expected_exit, expected_msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Chained command splitting
# ═══════════════════════════════════════════════════════════════════════════════


class TestChainedCommands:
    @pytest.mark.parametrize(
        "command, expected_exit, expected_msg",
        [
            ('echo "=== label ===" && cat file.py', 2, "directly"),
            ("cd /app && cat file.py", 2, "Read tool"),
            ("cd /app && grep -rn pattern src/", 2, "Grep tool"),
            ('git status && echo "done"', 2, "directly"),
            ("cd /app ; python script.py", 2, "uv run"),
            ("test -f x || cat fallback.py", 2, "Read tool"),
            ("git status && git log --oneline -3", 0, None),
            ('echo "foo && bar" | grep foo', 0, None),
            ("echo 'foo && bar' | wc", 0, None),
            ("cat file.py && git status && ls", 2, "Read tool"),
            ("git status && cat file.py && ls", 2, "Read tool"),
            ("git status && ls && cat file.py", 2, "Read tool"),
            ("git status && git log || cat f.py ; echo done", 2, None),
            (
                'echo "=== Source ===" && cd /app && git log --oneline -3 '
                '&& echo "" && cat plugin.json',
                2,
                None,
            ),
        ],
        ids=[
            "echo-in-chain",
            "cat-in-chain",
            "grep-in-chain",
            "echo-after-safe",
            "python-semicolon",
            "cat-in-or",
            "all-safe-allow",
            "quoted-and-not-split",
            "single-quoted-and-not-split",
            "triple-first-bad",
            "triple-middle-bad",
            "triple-last-bad",
            "mixed-operators",
            "real-world-offender",
        ],
    )
    def test_chains(self, command, expected_exit, expected_msg):
        result = run_bash(command)
        assert_guard(result, expected_exit, expected_msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Pipe segment checking
# ═══════════════════════════════════════════════════════════════════════════════


class TestPipeSegments:
    """Commands after | are checked for guarded tools."""

    @pytest.mark.parametrize(
        "command, expected_exit, expected_msg",
        [
            # python-json in pipe → suggest jq
            (
                "oc get deploy -o json | python3 -c "
                "'\"import sys,json; [print(a) for a in json.loads(sys.stdin.read())]\"'",
                2,
                "jq",
            ),
            (
                "curl -s https://api.example.com | python -c "
                "'\"import json,sys; print(json.load(sys.stdin))\"'",
                2,
                "jq",
            ),
            # plain python in pipe → suggest uv run
            ("oc get deploy | python3 -c 'print(1)'", 2, "uv run"),
            # multi-pipe: python in last segment
            ("cmd1 | cmd2 | python3 -c 'print(1)'", 2, "uv run"),
            # pager in pipe → still caught (interactive)
            ("git log | less", 2, "Pagers"),
            # editor in pipe → still caught (interactive)
            ("cat file | vim -", 2, "Interactive editors"),
            # pipe-exception rules skipped: these are legitimate pipeline usage
            ("cat file | wc -l", 0, None),  # existing behavior preserved
            ("git log | head -5", 0, None),  # existing behavior preserved
            ("ps aux | tail -5", 0, None),  # existing behavior preserved
            ("ls /path | sort", 0, None),  # existing behavior preserved
            ("git log | grep fix", 0, None),  # grep after pipe filters output
            ("oc get pods | grep Running", 0, None),  # grep after pipe filters output
            # safe passthrough
            ("oc get deploy | jq '.items[]'", 0, None),
            ("curl -s url | jq '.data'", 0, None),
            ("echo hello | wc -c", 0, None),
            # python tooling rules still fire in pipe segments
            ("cmd | pytest tests/", 2, "make py-test"),
            # git safety in pipe segment
            ("echo y | git reset --hard", 2, "FORBIDDEN"),
            # chain + pipe combo
            (
                "cd /app && oc get deploy -o json | python3 -c "
                "'\"import json,sys; print(json.load(sys.stdin))\"'",
                2,
                "jq",
            ),
            # env prefix in pipe segment
            ("cmd | FOO=bar python3 -c 'import json; x'", 2, "jq"),
            # subshell with pipe
            ("echo $(cmd | python3 -c 'import json; x')", 2, "jq"),
            ("result=$(oc get pods | python3 -c 'print(1)')", 2, "uv run"),
            # |& variant
            ("oc get pods |& python3 -c 'import json; x'", 2, "jq"),
            # pipe in for loop
            (
                "for ns in a b; do oc get pods -n $ns | python3 -c 'print(1)'; done",
                2,
                "uv run",
            ),
        ],
        ids=[
            "python-json-oc-pipe",
            "python-json-curl-pipe",
            "python-plain-pipe",
            "python-multi-pipe",
            "less-in-pipe",
            "vim-in-pipe",
            "cat-pipe-wc-allow",
            "head-pipe-allow",
            "tail-pipe-allow",
            "ls-pipe-sort-allow",
            "grep-after-pipe-allow",
            "grep-after-pipe-oc-allow",
            "jq-pipe-allow",
            "jq-curl-allow",
            "echo-pipe-wc-allow",
            "pytest-in-pipe",
            "git-deny-in-pipe",
            "chain-then-pipe",
            "env-prefix-in-pipe",
            "subshell-with-pipe-json",
            "subshell-with-pipe-plain",
            "pipe-and-variant",
            "pipe-in-for-loop",
        ],
    )
    def test_pipe_segments(self, command, expected_exit, expected_msg):
        result = run_bash(command)
        assert_guard(result, expected_exit, expected_msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Newline-separated commands
# ═══════════════════════════════════════════════════════════════════════════════


class TestNewlineSeparation:
    """Newline-separated commands: blocked subcmds still block, but when all
    subcmds pass we emit an explicit permissionDecision 'allow' so Claude
    Code's built-in newline detector doesn't reject the command."""

    @pytest.mark.parametrize(
        "command, expected_exit, expected_msg",
        [
            ("git status\ncat file.py", 2, "Read tool"),
            ("git status\npwd\ngrep pattern src/", 2, "Grep tool"),
            ('git log\necho "done"', 2, "directly"),
            ("# comment\ncat file.py", 2, "Read tool"),
        ],
        ids=[
            "cat-on-line-2",
            "grep-on-line-3",
            "echo-on-newline",
            "comment-then-cat",
        ],
    )
    def test_newlines_blocked(self, command, expected_exit, expected_msg):
        result = run_bash(command)
        assert_guard(result, expected_exit, expected_msg)

    @pytest.mark.parametrize(
        "command",
        [
            "docker run \\\n  -v /app:/app \\\n  image",
            "git status\npwd\nmake build",
        ],
        ids=[
            "continuation-allow",
            "all-safe-allow",
        ],
    )
    def test_newlines_explicit_allow(self, command):
        """Multiline commands where all subcmds pass get explicit allow."""
        result = run_bash(command)
        assert_allow_decision(result, "Multiline command reviewed")

    @pytest.mark.parametrize(
        "command, expected_exit, expected_msg",
        [
            # Backslash continuation joins lines — guard sees the full command
            ("git push \\\n--force", 2, "FORBIDDEN"),
            ("git reset \\\n--hard", 2, "FORBIDDEN"),
            ("cat \\\nfile.py", 2, "Read tool"),
        ],
        ids=[
            "continuation-push-force",
            "continuation-reset-hard",
            "continuation-cat-file",
        ],
    )
    def test_backslash_continuation_still_blocked(self, command, expected_exit, expected_msg):
        """Backslash continuation can't split dangerous commands across lines."""
        result = run_bash(command)
        assert_guard(result, expected_exit, expected_msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Env var prefix stripping
# ═══════════════════════════════════════════════════════════════════════════════


class TestEnvVarPrefix:
    @pytest.mark.parametrize(
        "command, expected_exit, expected_msg",
        [
            ("FOO=bar python script.py", 2, "uv run"),
            ("FOO=bar cat file.py", 2, "Read tool"),
            ("FOO=bar grep pattern src/", 2, "Grep tool"),
            ("ENV=1 bash deploy.sh", 2, "make"),
            ("A=1 B=2 python script.py", 2, "uv run"),
            ("FOO=bar make build", 0, None),
            ("FOO=bar uv run script.py", 0, None),
        ],
        ids=[
            "python",
            "cat",
            "grep",
            "bash-script",
            "multi-prefix",
            "make-allow",
            "uv-run-allow",
        ],
    )
    def test_env_prefix(self, command, expected_exit, expected_msg):
        result = run_bash(command)
        assert_guard(result, expected_exit, expected_msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Subshell / backtick extraction
# ═══════════════════════════════════════════════════════════════════════════════


class TestSubshells:
    @pytest.mark.parametrize(
        "command, expected_exit, expected_msg",
        [
            ("echo $(cat file.py)", 2, "Read tool"),
            ("echo `cat file.py`", 2, "Read tool"),
            ("result=$(grep -rn pat src/)", 2, "Grep tool"),
            ("echo $(uv run script.py)", 0, None),
            ("echo $(echo $(cat f.py))", 2, "Read tool"),
        ],
        ids=[
            "dollar-paren-cat",
            "backtick-cat",
            "dollar-paren-grep",
            "safe-subshell-allow",
            "nested-subshell",
        ],
    )
    def test_subshells(self, command, expected_exit, expected_msg):
        result = run_bash(command)
        assert_guard(result, expected_exit, expected_msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Andon cord: GUARD_BYPASS=1
# ═══════════════════════════════════════════════════════════════════════════════


class TestAndonCord:
    @pytest.mark.parametrize(
        "command, expected_exit, expected_msg",
        [
            ("GUARD_BYPASS=1 cat file.py", 0, None),
            ("GUARD_BYPASS=1 bash setup.sh", 0, None),
            ("GUARD_BYPASS=1 uv run /tmp/t.py", 0, None),
            ("GUARD_BYPASS=1 cat f.py && grep p", 0, None),
            ("cat file.py", 2, "Read tool"),
        ],
        ids=[
            "bypass-cat",
            "bypass-bash-script",
            "bypass-tmp",
            "bypass-full-chain",
            "no-bypass-blocked",
        ],
    )
    def test_andon_cord(self, command, expected_exit, expected_msg):
        result = run_bash(command)
        assert_guard(result, expected_exit, expected_msg)

    def test_andon_cord_multiline_allow(self):
        """GUARD_BYPASS with multiline emits explicit allow decision."""
        result = run_bash("GUARD_BYPASS=1 git status\npwd")
        assert_allow_decision(result, "Multiline bypass command reviewed")


# ═══════════════════════════════════════════════════════════════════════════════
# Non-Bash tools: passthrough on normal paths
# ═══════════════════════════════════════════════════════════════════════════════


class TestNonBashPassthrough:
    @pytest.mark.parametrize(
        "tool_name",
        ["Read", "Write", "Grep", "Glob"],
    )
    def test_non_bash_passthrough(self, tool_name):
        path_key = "path" if tool_name in ("Grep", "Glob") else "file_path"
        result = run_guard(tool_name, {path_key: "/some/file.py"})
        assert result.returncode == 0


# ═══════════════════════════════════════════════════════════════════════════════
# EnterPlanMode redirect (exit 2)
# ═══════════════════════════════════════════════════════════════════════════════


class TestEnterPlanModeRedirect:
    def test_enter_plan_mode_denied(self):
        result = run_guard("EnterPlanMode", {})
        assert_guard(result, 2, "incremental-planning")

    def test_enter_plan_mode_message_content(self):
        result = run_guard("EnterPlanMode", {})
        assert "Native plan mode" in result.stderr
        assert "Skill tool" in result.stderr

    def test_exit_plan_mode_passthrough(self):
        """ExitPlanMode is not intercepted — only EnterPlanMode is."""
        result = run_guard("ExitPlanMode", {})
        assert result.returncode == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Non-Bash /tmp/ blocking (write-oriented tools only)
# Read/Grep/Glob are allowed — Claude Code stores task output files in /tmp/.
# ═══════════════════════════════════════════════════════════════════════════════


class TestNonBashTmpBlocking:
    @pytest.mark.parametrize(
        "tool_name, file_path, expected_exit, expected_msg, path_key",
        [
            # Write-oriented tools are blocked on /tmp/
            ("Write", "/tmp/output.json", 2, "hack/tmp/", "file_path"),
            ("Edit", "/tmp/config.yaml", 2, "hack/tmp/", "file_path"),
            ("NotebookEdit", "/tmp/notebook.ipynb", 2, "hack/tmp/", "file_path"),
            # Read-oriented tools are allowed on /tmp/ (task output files live there)
            ("Read", "/tmp/scratch.py", 0, None, "file_path"),
            ("Grep", "/tmp/logs/", 0, None, "path"),
            ("Glob", "/tmp/scratch/", 0, None, "path"),
            ("Read", "/private/tmp/claude-501/tasks/abc.output", 0, None, "file_path"),
            # hack/tmp/ always allowed
            ("Read", "hack/tmp/test.py", 0, None, "file_path"),
            ("Write", "hack/tmp/out.json", 0, None, "file_path"),
            # Normal paths always allowed
            ("Read", "src/main.py", 0, None, "file_path"),
        ],
        ids=[
            "write-tmp-blocked",
            "edit-tmp-blocked",
            "notebook-tmp-blocked",
            "read-tmp-allowed",
            "grep-tmp-allowed",
            "glob-tmp-allowed",
            "read-task-output-allowed",
            "read-hack-tmp-allow",
            "write-hack-tmp-allow",
            "read-normal-allow",
        ],
    )
    def test_tmp_blocking(self, tool_name, file_path, expected_exit, expected_msg, path_key):
        result = run_guard(tool_name, {path_key: file_path})
        assert_guard(result, expected_exit, expected_msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Whitespace edge cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestWhitespaceEdgeCases:
    @pytest.mark.parametrize(
        "command, expected_exit, expected_msg",
        [
            ("   cat file.py", 2, "Read tool"),
            ("\tcat file.py", 2, "Read tool"),
            ("git status ;", 0, None),
            ("git status ;; cat file.py", 2, "Read tool"),
            ("cat file.py ;", 2, "Read tool"),
        ],
        ids=[
            "leading-spaces",
            "leading-tab",
            "trailing-semicolon",
            "double-semicolon",
            "empty-after-semicolon",
        ],
    )
    def test_whitespace(self, command, expected_exit, expected_msg):
        result = run_bash(command)
        assert_guard(result, expected_exit, expected_msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Git safety: DENY rules (exit 2)
# ═══════════════════════════════════════════════════════════════════════════════


class TestGitSafetyDeny:
    @pytest.mark.parametrize(
        "command, expected_exit, expected_msg",
        [
            # reset --hard
            ("git reset --hard", 2, "FORBIDDEN"),
            ("git reset --hard HEAD~3", 2, "FORBIDDEN"),
            ("git reset --mixed HEAD~1", 0, None),
            # force push
            ("git push --force origin feature", 2, "FORBIDDEN"),
            ("git push -f origin feature", 2, "FORBIDDEN"),
            ("git push origin feature", 0, None),
            # push upstream
            ("git push upstream main", 2, "FORBIDDEN"),
            ("git push upstream feature/x", 2, "FORBIDDEN"),
            # force-with-lease to main
            ("git push --force-with-lease origin main", 2, "FORBIDDEN"),
            ("git push --force-with-lease origin master", 2, "FORBIDDEN"),
            ("git push --force-with-lease origin feature", 0, None),
            # branch -D
            ("git branch -D feature", 2, "FORBIDDEN"),
            ("git branch -d feature", 0, None),
            # branch --force
            ("git branch --force feature HEAD~1", 2, "FORBIDDEN"),
            # push origin main
            ("git push origin main", 2, "FORBIDDEN"),
            ("git push origin master", 2, "FORBIDDEN"),
            # --no-verify
            ("git commit -m 'test' --no-verify", 2, "FORBIDDEN"),
            ("git push --no-verify origin feature", 2, "FORBIDDEN"),
            # add --force
            ("git add --force secret.env", 2, "FORBIDDEN"),
            ("git add -f .env", 2, "FORBIDDEN"),
            ("git add file.py", 0, None),
            # git rm
            ("git rm important.py", 2, "FORBIDDEN"),
            ("git rm --cached file.py", 0, None),
            ("git rm --cached --force file.py", 2, "FORBIDDEN"),
            # git clean -x/-X
            ("git clean -xdf", 2, "FORBIDDEN"),
            ("git clean -Xdf", 2, "FORBIDDEN"),
            ("git clean -df", 0, None),
            # chain awareness
            ("git status && git reset --hard", 2, "FORBIDDEN"),
            ("git add . && git push -f origin main", 2, "FORBIDDEN"),
        ],
        ids=[
            "reset-hard",
            "reset-hard-ref",
            "reset-mixed-allow",
            "push-force",
            "push-f-short",
            "push-origin-feature-allow",
            "push-upstream-main",
            "push-upstream-feature",
            "fwl-main",
            "fwl-master",
            "fwl-feature-allow",
            "branch-D",
            "branch-d-allow",
            "branch-force",
            "push-origin-main",
            "push-origin-master",
            "commit-no-verify",
            "push-no-verify",
            "add-force",
            "add-f-short",
            "add-file-allow",
            "rm-file",
            "rm-cached-allow",
            "rm-cached-force",
            "clean-x",
            "clean-X",
            "clean-df-allow",
            "reset-hard-in-chain",
            "force-push-in-chain",
        ],
    )
    def test_git_deny(self, command, expected_exit, expected_msg):
        result = run_bash(command)
        assert_guard(result, expected_exit, expected_msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Git safety: ASK rules (permissionDecision: ask via JSON)
# ═══════════════════════════════════════════════════════════════════════════════


class TestGitSafetyAsk:
    @pytest.mark.parametrize(
        "command, expected_ask, expected_msg",
        [
            ("git stash drop", True, "permanently deletes"),
            ("git checkout -- file.py", True, "destructive"),
            ("git filter-branch --tree-filter 'rm -f x'", True, "deprecated"),
            ("git reflog delete HEAD@{2}", True, "recovery points"),
            ("git reflog expire --expire=now", True, "recovery points"),
            ("git remote remove upstream", True, "break workflows"),
            ("git remote rm upstream", True, "break workflows"),
            ("git config --global user.name foo", True, "permission"),
            ("git config --global --get user.name", False, None),
            ("git config --global --list", False, None),
        ],
        ids=[
            "stash-drop",
            "checkout-dash-dash",
            "filter-branch",
            "reflog-delete",
            "reflog-expire",
            "remote-remove",
            "remote-rm",
            "config-global-write",
            "config-global-get-allow",
            "config-global-list-allow",
        ],
    )
    def test_git_ask(self, command, expected_ask, expected_msg):
        result = run_bash(command)
        if expected_ask:
            assert_ask_decision(result, expected_msg)
        else:
            assert_guard(result, 0, expected_msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Git safety: commit-to-main (requires being on main branch)
# ═══════════════════════════════════════════════════════════════════════════════


class TestGitSafetyCommitToMain:
    def test_commit_to_main(self):
        env = os.environ.copy()
        env["_GUARD_TEST_BRANCH"] = "main"
        result = run_guard("Bash", {"command": "git commit -m 'test'"}, env=env)
        assert_guard(result, 2, "FORBIDDEN", "commit-to-main")

    def test_commit_to_master(self):
        env = os.environ.copy()
        env["_GUARD_TEST_BRANCH"] = "master"
        result = run_guard("Bash", {"command": "git commit -m 'test'"}, env=env)
        assert_guard(result, 2, "FORBIDDEN", "commit-to-main")

    def test_commit_to_feature_branch_allowed(self):
        env = os.environ.copy()
        env["_GUARD_TEST_BRANCH"] = "feat/something"
        result = run_guard("Bash", {"command": "git commit -m 'test'"}, env=env)
        assert result.returncode == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Git safety: branch creation enforcement
# ═══════════════════════════════════════════════════════════════════════════════


class TestGitBranchEnforcement:
    """Tests for branch-no-base (DENY), branch-from-local-main (ASK),
    branch-from-non-upstream (ASK), and branch-needs-fetch (ASK) rules."""

    @pytest.mark.parametrize(
        "command, expected_exit, expected_msg",
        [
            # ── DENY: branch-no-base (exit 2) ──
            ("git switch -c feat/new", 2, "start-point"),
            ("git checkout -b feat/new", 2, "start-point"),
            ("git checkout -B feat/new", 2, "start-point"),
            ("git worktree add ../wt -b feat/new", 2, "start-point"),
            ("git switch --create feat/new", 2, "start-point"),
            ("git switch --create=feat/new", 2, "start-point"),
            ("git switch --track -c feat/new", 2, "start-point"),
            ("git checkout --track -b feat/new", 2, "start-point"),
            # ── ASK: branch-from-local-main (JSON ask) ──
            ("git switch -c feat/new main", "ask", "stale"),
            ("git checkout -b feat/new main", "ask", "stale"),
            ("git switch -c feat/new master", "ask", "stale"),
            ("git worktree add ../wt -b feat/new main", "ask", "stale"),
            # ── ASK: branch-from-non-upstream (JSON ask) ──
            ("git switch -c feat/new feat/other", "ask", "stacking"),
            ("git checkout -b feat/new develop", "ask", "stacking"),
            ("git worktree add ../wt -b feat/new feat/old", "ask", "stacking"),
            ("git switch -c feat/new origin/feat/x", "ask", "stacking"),
            # ── ASK: branch-needs-fetch (JSON ask) ──
            ("git switch -c feat/new upstream/main", "ask", "No git fetch"),
            ("git checkout -b feat/new origin/main", "ask", "No git fetch"),
            # ── PASS: safe with fetch in chain (exit 0) ──
            (
                "git fetch upstream main && git switch -c feat/new upstream/main",
                0,
                None,
            ),
            (
                "git fetch upstream && git checkout -b feat/new upstream/main",
                0,
                None,
            ),
            (
                "git fetch origin && git switch -c feat/new origin/main",
                0,
                None,
            ),
            # ── PASS: safe start points, non-remote (exit 0, no fetch needed) ──
            ("git switch -c feat/new HEAD", 0, None),
            ("git switch -c feat/new HEAD~3", 0, None),
            ("git switch -c feat/new HEAD^2", 0, None),
            ("git switch -c feat/new abc1234def", 0, None),
            ("git switch --create=feat/new HEAD", 0, None),
            # ── PASS: not branch creation (exit 0) ──
            ("git switch existing-branch", 0, None),
            ("git branch -d old-branch", 0, None),
            ("git checkout main", 0, None),
            ("git checkout -", 0, None),
            ("git worktree add ../wt", 0, None),
            ("git worktree list", 0, None),
            # ── Chain awareness ──
            # fetch seen but still no start point → DENY
            ("git fetch upstream && git switch -c feat/x", 2, "start-point"),
            # no fetch in chain → ASK
            (
                "git status && git switch -c feat/x upstream/main",
                "ask",
                "No git fetch",
            ),
            # fetch + safe start → PASS
            (
                "git fetch upstream main && git switch -c feat/x upstream/main",
                0,
                None,
            ),
        ],
        ids=[
            # DENY
            "deny-switch-c-no-start",
            "deny-checkout-b-no-start",
            "deny-checkout-B-no-start",
            "deny-worktree-b-no-start",
            "deny-switch-create-no-start",
            "deny-switch-create-eq-no-start",
            "deny-switch-track-c-no-start",
            "deny-checkout-track-b-no-start",
            # ASK: local main
            "ask-switch-c-main",
            "ask-checkout-b-main",
            "ask-switch-c-master",
            "ask-worktree-b-main",
            # ASK: non-upstream
            "ask-switch-c-feature",
            "ask-checkout-b-develop",
            "ask-worktree-b-feature",
            "ask-switch-c-remote-feature",
            # ASK: needs fetch
            "ask-upstream-no-fetch",
            "ask-origin-no-fetch",
            # PASS: safe with fetch
            "pass-fetch-then-switch",
            "pass-fetch-then-checkout",
            "pass-fetch-origin-then-switch",
            # PASS: non-remote safe
            "pass-HEAD",
            "pass-HEAD-tilde",
            "pass-HEAD-caret",
            "pass-sha",
            "pass-create-eq-HEAD",
            # PASS: not creation
            "pass-switch-existing",
            "pass-branch-delete",
            "pass-checkout-main",
            "pass-checkout-dash",
            "pass-worktree-no-b",
            "pass-worktree-list",
            # Chain awareness
            "chain-fetch-but-no-start",
            "chain-no-fetch-upstream",
            "chain-fetch-and-upstream",
        ],
    )
    def test_branch_enforcement(self, command, expected_exit, expected_msg):
        result = run_bash(command)
        if expected_exit == "ask":
            assert_ask_decision(result, expected_msg)
        else:
            assert_guard(result, expected_exit, expected_msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Unit tests: _parse_branch_creation and _is_safe_start_point
# ═══════════════════════════════════════════════════════════════════════════════

_spec = importlib.util.spec_from_file_location("tool_selection_guard", SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_parse_branch_creation = _mod._parse_branch_creation
_is_safe_start_point = _mod._is_safe_start_point
_strip_shell_keyword = _mod.strip_shell_keyword
_split_pipes = _mod.split_pipes


class TestParseBranchCreation:
    """Unit tests for _parse_branch_creation parser."""

    @pytest.mark.parametrize(
        "cmd, expected",
        [
            # switch -c
            ("git switch -c feat/x upstream/main", ("feat/x", "upstream/main")),
            ("git switch -c feat/x", ("feat/x", None)),
            ("git switch --create feat/x upstream/main", ("feat/x", "upstream/main")),
            ("git switch --create=feat/x upstream/main", ("feat/x", "upstream/main")),
            ("git switch --create=feat/x", ("feat/x", None)),
            # switch -c with interspersed flags
            (
                "git switch --track -c feat/x upstream/main",
                ("feat/x", "upstream/main"),
            ),
            (
                "git switch -c feat/x --no-track upstream/main",
                ("feat/x", "upstream/main"),
            ),
            # checkout -b/-B
            ("git checkout -b feat/x upstream/main", ("feat/x", "upstream/main")),
            ("git checkout -B feat/x upstream/main", ("feat/x", "upstream/main")),
            ("git checkout -b feat/x", ("feat/x", None)),
            # worktree add -b
            (
                "git worktree add ../wt -b feat/x upstream/main",
                ("feat/x", "upstream/main"),
            ),
            ("git worktree add ../wt -b feat/x", ("feat/x", None)),
            # -- separator skipped
            (
                "git checkout -b feat/x -- upstream/main",
                ("feat/x", "upstream/main"),
            ),
            # names with slashes and dots
            (
                "git switch -c feat/sub/deep upstream/main",
                ("feat/sub/deep", "upstream/main"),
            ),
            (
                "git switch -c fix/1.2.3 upstream/main",
                ("fix/1.2.3", "upstream/main"),
            ),
            # not branch creation → None
            ("git switch existing-branch", None),
            ("git checkout main", None),
            ("git branch -d old", None),
            ("git worktree add ../wt", None),
            ("git worktree list", None),
            ("git status", None),
            ("ls -la", None),
        ],
        ids=[
            "switch-c-with-start",
            "switch-c-no-start",
            "switch-create-with-start",
            "switch-create-eq-with-start",
            "switch-create-eq-no-start",
            "switch-track-c-with-start",
            "switch-c-notrack-with-start",
            "checkout-b-with-start",
            "checkout-B-with-start",
            "checkout-b-no-start",
            "worktree-b-with-start",
            "worktree-b-no-start",
            "checkout-b-dash-dash-start",
            "slashes-in-name",
            "dots-in-name",
            "switch-existing",
            "checkout-ref",
            "branch-delete",
            "worktree-no-b",
            "worktree-list",
            "git-status",
            "non-git",
        ],
    )
    def test_parse(self, cmd, expected):
        assert _parse_branch_creation(cmd) == expected


class TestIsSafeStartPoint:
    """Unit tests for _is_safe_start_point classifier."""

    @pytest.mark.parametrize(
        "ref, expected",
        [
            # safe remote refs
            ("upstream/main", True),
            ("origin/main", True),
            ("upstream/master", True),
            ("origin/master", True),
            # HEAD variants
            ("HEAD", True),
            ("HEAD~3", True),
            ("HEAD^2", True),
            ("HEAD^^", True),
            ("HEAD~1^2", True),
            # SHA-like
            ("abc1234def", True),
            ("abcdef1", True),
            ("abcdef1234567890abcdef1234567890abcdef12", True),
            # not safe
            ("main", False),
            ("master", False),
            ("develop", False),
            ("feat/other", False),
            ("origin/feat/x", False),
            ("abc123", False),  # 6 chars, below 7-char minimum
            ("ABCDEF1", False),  # uppercase
            ("HEAD~abc", False),  # non-numeric after ~
            ("HEADER", False),  # not HEAD
        ],
        ids=[
            "upstream-main",
            "origin-main",
            "upstream-master",
            "origin-master",
            "HEAD",
            "HEAD-tilde",
            "HEAD-caret",
            "HEAD-double-caret",
            "HEAD-combined",
            "sha-10",
            "sha-7-min",
            "sha-40-full",
            "local-main",
            "local-master",
            "develop",
            "feature-branch",
            "remote-feature",
            "sha-too-short",
            "sha-uppercase",
            "HEAD-non-numeric",
            "HEADER-not-HEAD",
        ],
    )
    def test_safe_start_point(self, ref, expected):
        assert _is_safe_start_point(ref) == expected


class TestSplitPipes:
    """Unit tests for split_pipes parser."""

    @pytest.mark.parametrize(
        "cmd, expected",
        [
            ("a | b", ["a", "b"]),
            ("a", ["a"]),
            ("a | b | c", ["a", "b", "c"]),
            ("echo 'a | b'", ["echo 'a | b'"]),
            ('echo "a | b"', ['echo "a | b"']),
            ("a |& b", ["a", "b"]),
            ("a | b |& c", ["a", "b", "c"]),
            ("", []),
            ("  a  |  b  ", ["a", "b"]),
            (
                "oc get deploy -o json 2>&1 | python3 -c 'import json'",
                ["oc get deploy -o json 2>&1", "python3 -c 'import json'"],
            ),
        ],
        ids=[
            "simple-pipe",
            "no-pipe",
            "multi-pipe",
            "single-quoted-pipe",
            "double-quoted-pipe",
            "pipe-and",
            "mixed-pipes",
            "empty",
            "whitespace",
            "real-world",
        ],
    )
    def test_split(self, cmd, expected):
        assert _split_pipes(cmd) == expected


class TestStripShellKeyword:
    """Unit tests for strip_shell_keyword helper."""

    @pytest.mark.parametrize(
        "cmd, expected",
        [
            ("do echo hi", "echo hi"),
            ("then cat file", "cat file"),
            ("else git status", "git status"),
            ("elif test -f x", "test -f x"),
            ("if true", "true"),
            ("while true", "true"),
            ("until false", "false"),
            # recursive stripping
            ("do if git reset --hard", "git reset --hard"),
            ("do then echo hi", "echo hi"),
            # keywords that don't prefix commands — left alone
            ("done", "done"),
            ("fi", "fi"),
            ("for x in a b", "for x in a b"),
            ("case x in", "case x in"),
            ("esac", "esac"),
            # 'do' inside 'docker' — not a keyword prefix
            ("docker run image", "docker run image"),
            # 'do' not at start
            ("echo do something", "echo do something"),
            # leading whitespace
            ("  do echo hi", "echo hi"),
            ("\tdo echo hi", "echo hi"),
        ],
        ids=[
            "do-echo",
            "then-cat",
            "else-git",
            "elif-test",
            "if-true",
            "while-true",
            "until-false",
            "recursive-do-if",
            "recursive-do-then",
            "done-passthrough",
            "fi-passthrough",
            "for-passthrough",
            "case-passthrough",
            "esac-passthrough",
            "docker-not-keyword",
            "do-not-at-start",
            "leading-spaces",
            "leading-tab",
        ],
    )
    def test_strip(self, cmd, expected):
        assert _strip_shell_keyword(cmd) == expected


# ═══════════════════════════════════════════════════════════════════════════════
# hooks.json configuration validation
# ═══════════════════════════════════════════════════════════════════════════════


HOOKS_FILE = os.path.join(os.path.dirname(__file__), os.pardir, "hooks", "hooks.json")


class TestHooksJsonConfiguration:
    """Validate hooks.json matchers are valid regex on tool names."""

    def test_hooks_json_is_valid(self):
        with open(HOOKS_FILE) as f:
            data = json.load(f)
        assert "hooks" in data

    def test_matchers_have_no_parenthesized_patterns(self):
        """Matchers are regex on tool names only — Bash(command*) is invalid."""
        with open(HOOKS_FILE) as f:
            data = json.load(f)
        for event_name, event_hooks in data["hooks"].items():
            for hook_group in event_hooks:
                matcher = hook_group.get("matcher", "")
                assert "(" not in matcher, (
                    f"Invalid matcher '{matcher}' in {event_name}: "
                    f"matchers are regex on tool names, not Bash(command) patterns"
                )

    def test_matchers_are_valid_regex(self):
        """All matchers should compile as valid regex."""
        with open(HOOKS_FILE) as f:
            data = json.load(f)
        for event_name, event_hooks in data["hooks"].items():
            for hook_group in event_hooks:
                matcher = hook_group.get("matcher", "")
                if matcher:
                    try:
                        re.compile(matcher)
                    except re.error as e:
                        pytest.fail(f"Invalid regex '{matcher}' in {event_name}: {e}")

    def test_all_hook_commands_reference_existing_scripts(self):
        """All command hooks should reference scripts that exist relative to hooks dir."""
        hooks_dir = os.path.join(os.path.dirname(__file__), os.pardir, "hooks")
        with open(HOOKS_FILE) as f:
            data = json.load(f)
        for event_name, event_hooks in data["hooks"].items():
            for hook_group in event_hooks:
                for hook in hook_group.get("hooks", []):
                    cmd = hook.get("command", "")
                    # Extract script path from command (after "uv run " or direct)
                    script = cmd.replace("${CLAUDE_PLUGIN_ROOT}/hooks/", "")
                    if script.startswith("uv run "):
                        script = script.replace("uv run ", "")
                        script = script.replace("${CLAUDE_PLUGIN_ROOT}/hooks/", "")
                    # Strip any arguments (e.g. "--validate") from the script name
                    script = script.split()[0] if script.strip() else script
                    script_path = os.path.join(hooks_dir, script)
                    assert os.path.exists(script_path), (
                        f"Script '{script}' referenced in {event_name} does not exist "
                        f"at {script_path}"
                    )


class TestShellControlStructures:
    """Integration tests: commands inside for/while/if blocks are caught."""

    @pytest.mark.parametrize(
        "command, expected_exit, expected_msg",
        [
            # for loop with cat
            ("for x in a b c; do cat file.txt; done", 2, "Read tool"),
            # if/then with cat
            ("if true; then cat file.py; fi", 2, "Read tool"),
            # while/do with grep
            ("while true; do grep pattern src/; done", 2, "Grep tool"),
            # for loop with ls (the original motivating example)
            (
                'for dir in /path/*; do ls -la "$dir"; done',
                2,
                "Glob tool",
            ),
            # nested: if inside do
            ("for x in a; do if true; then cat f.py; fi; done", 2, "Read tool"),
            # git safety inside control structure
            ("if true; then git reset --hard; fi", 2, "FORBIDDEN"),
            # safe command inside control structure — passes
            ("for x in a b; do git status; done", 0, None),
            ("if true; then make build; fi", 0, None),
        ],
        ids=[
            "for-do-cat",
            "if-then-cat",
            "while-do-grep",
            "for-do-ls",
            "nested-if-in-do",
            "git-deny-in-if",
            "safe-in-for",
            "safe-in-if",
        ],
    )
    def test_control_structures(self, command, expected_exit, expected_msg):
        result = run_bash(command)
        assert_guard(result, expected_exit, expected_msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Audit logging: URL guard events logged to JSONL file
# ═══════════════════════════════════════════════════════════════════════════════


class TestURLGuardAuditLog:
    """Verify URL guard events are logged to SQLite database."""

    def test_blocked_url_logged(self, tmp_path):
        _run_guard_with_db(
            "Bash",
            {"command": "curl https://api.github.com/repos/org/repo"},
            tmp_path,
        )
        entries = _read_url_events(tmp_path)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["action"] == "blocked"
        assert entry["rule"] == "github-api"
        assert entry["tool"] == "Bash"
        assert entry["phase"] == "pre"
        assert "api.github.com" in entry["url"]
        assert "ts" in entry

    def test_allowed_url_logged(self, tmp_path):
        _run_guard_with_db(
            "Bash",
            {"command": "curl https://example.com/data"},
            tmp_path,
        )
        entries = _read_url_events(tmp_path)
        assert len(entries) == 1
        assert entries[0]["action"] == "allowed"
        assert entries[0]["rule"] is None

    def test_bypassed_url_logged(self, tmp_path):
        _run_guard_with_db(
            "Bash",
            {"command": "ALLOW_FETCH=1 curl https://api.github.com/repos/org/repo"},
            tmp_path,
        )
        entries = _read_url_events(tmp_path)
        assert len(entries) == 1
        assert entries[0]["action"] == "bypassed"

    def test_webfetch_blocked_logged(self, tmp_path):
        _run_guard_with_db(
            "WebFetch",
            {"url": "https://api.github.com/repos/org/repo", "prompt": "test"},
            tmp_path,
        )
        entries = _read_url_events(tmp_path)
        assert len(entries) == 1
        assert entries[0]["action"] == "blocked"
        assert entries[0]["tool"] == "WebFetch"

    def test_webfetch_allowed_logged(self, tmp_path):
        _run_guard_with_db(
            "WebFetch",
            {"url": "https://example.com", "prompt": "test"},
            tmp_path,
        )
        entries = _read_url_events(tmp_path)
        assert len(entries) == 1
        assert entries[0]["action"] == "allowed"
        assert entries[0]["tool"] == "WebFetch"

    def test_non_fetch_command_not_logged(self, tmp_path):
        """Non-curl/wget bash commands should not produce URL log entries."""
        _run_guard_with_db("Bash", {"command": "git status"}, tmp_path)
        entries = _read_url_events(tmp_path)
        assert len(entries) == 0

    def test_multiple_events_stored(self, tmp_path):
        """Multiple events are stored in the SQLite database."""
        _run_guard_with_db(
            "Bash",
            {"command": "curl https://api.github.com/repos/o/r"},
            tmp_path,
        )
        _run_guard_with_db(
            "WebFetch",
            {"url": "https://example.com", "prompt": "t"},
            tmp_path,
        )
        entries = _read_url_events(tmp_path)
        assert len(entries) == 2
        for entry in entries:
            assert "ts" in entry
            assert "url" in entry
            assert "action" in entry


# ═══════════════════════════════════════════════════════════════════════════════
# PostToolUse: response code logging
# ═══════════════════════════════════════════════════════════════════════════════


class TestPostToolUseResponseLogging:
    """PostToolUse hooks log HTTP response codes for fetch commands."""

    def _run_post_hook(self, tool_name, tool_input, tool_response, tmp_path):
        """Simulate a PostToolUse hook invocation."""
        env = os.environ.copy()
        env["GUARD_DB_PATH"] = str(tmp_path / "test.db")
        env["GUARD_LOG_LEVEL"] = "all"
        return run_guard(
            tool_name,
            tool_input,
            env=env,
            payload_extra={
                "hook_event_name": "PostToolUse",
                "tool_response": tool_response,
            },
        )

    def test_bash_curl_403_logged(self, tmp_path):
        """HTTP 403 from curl is logged as auth_failed."""
        self._run_post_hook(
            "Bash",
            {"command": "curl https://example.com/api/data"},
            {"stdout": "HTTP/1.1 403 Forbidden\n<html>Access Denied</html>", "stderr": ""},
            tmp_path,
        )
        entries = _read_url_events(tmp_path)
        assert len(entries) == 1
        assert entries[0]["phase"] == "post"
        assert entries[0]["auth_failed"] is True
        assert entries[0]["response_code"] == 403
        assert entries[0]["action"] == "auth_failed"

    def test_bash_curl_401_logged(self, tmp_path):
        """HTTP 401 from curl is logged as auth_failed."""
        self._run_post_hook(
            "Bash",
            {"command": "curl https://example.com/api"},
            {"stdout": "HTTP/2 401 Unauthorized", "stderr": ""},
            tmp_path,
        )
        entries = _read_url_events(tmp_path)
        assert len(entries) == 1
        assert entries[0]["auth_failed"] is True
        assert entries[0]["response_code"] == 401

    def test_bash_curl_200_logged(self, tmp_path):
        """HTTP 200 from curl is logged as success."""
        self._run_post_hook(
            "Bash",
            {"command": "curl https://example.com/data"},
            {"stdout": 'HTTP/1.1 200 OK\n{"data": 1}', "stderr": ""},
            tmp_path,
        )
        entries = _read_url_events(tmp_path)
        assert len(entries) == 1
        assert entries[0]["auth_failed"] is False
        assert entries[0]["response_code"] == 200
        assert entries[0]["action"] == "success"

    def test_webfetch_auth_failed(self, tmp_path):
        """WebFetch response with auth failure patterns is detected."""
        self._run_post_hook(
            "WebFetch",
            {"url": "https://example.com/page", "prompt": "test"},
            "Login Required - Please sign in to continue",
            tmp_path,
        )
        entries = _read_url_events(tmp_path)
        assert len(entries) == 1
        assert entries[0]["tool"] == "WebFetch"
        assert entries[0]["auth_failed"] is True

    def test_webfetch_success(self, tmp_path):
        """WebFetch response without auth failure is logged as success."""
        self._run_post_hook(
            "WebFetch",
            {"url": "https://example.com", "prompt": "test"},
            "Welcome to Example.com! Here is the page content.",
            tmp_path,
        )
        entries = _read_url_events(tmp_path)
        assert len(entries) == 1
        assert entries[0]["auth_failed"] is False
        assert entries[0]["action"] == "success"

    def test_non_fetch_bash_not_logged(self, tmp_path):
        """Non-curl/wget bash commands produce no PostToolUse log entries."""
        self._run_post_hook(
            "Bash",
            {"command": "git status"},
            {"stdout": "On branch main", "stderr": ""},
            tmp_path,
        )
        entries = _read_url_events(tmp_path)
        assert len(entries) == 0

    def test_post_hook_always_exits_0(self, tmp_path):
        """PostToolUse hooks are observational — always exit 0."""
        result = self._run_post_hook(
            "Bash",
            {"command": "curl https://api.github.com/repos/org/repo"},
            {"stdout": "HTTP/1.1 403 Forbidden", "stderr": ""},
            tmp_path,
        )
        assert result.returncode == 0

    def test_sso_redirect_detected(self, tmp_path):
        """SSO redirect pattern is detected as auth failure."""
        self._run_post_hook(
            "WebFetch",
            {"url": "https://internal.example.com/page", "prompt": "test"},
            "Redirecting to SSO... SSO redirect detected for authentication",
            tmp_path,
        )
        entries = _read_url_events(tmp_path)
        assert len(entries) == 1
        assert entries[0]["auth_failed"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# Custom URL guard rules: URL_GUARD_EXTRA_RULES env var
# ═══════════════════════════════════════════════════════════════════════════════


class TestExtraURLRules:
    """URL_GUARD_EXTRA_RULES env var loads custom rules from a JSON file."""

    def test_extra_rule_blocks_custom_domain(self, tmp_path):
        """Custom rules loaded from JSON file block matching URLs."""
        rules = [
            {
                "name": "custom-internal",
                "pattern": r"internal\.example\.corp",
                "message": "This is an internal URL. Use the CLI instead.",
            }
        ]
        rules_file = tmp_path / "extra-rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_with_extra_url_rules(
            "Bash",
            {"command": "curl https://internal.example.corp/api/data"},
            rules_file,
        )
        assert result.returncode == 2
        assert "internal URL" in result.stderr

    def test_extra_rule_allows_non_matching(self, tmp_path):
        """Custom rules do not block URLs that don't match."""
        rules = [
            {
                "name": "custom-internal",
                "pattern": r"internal\.example\.corp",
                "message": "Blocked.",
            }
        ]
        rules_file = tmp_path / "extra-rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_with_extra_url_rules(
            "Bash",
            {"command": "curl https://example.com/api/data"},
            rules_file,
        )
        assert result.returncode == 0

    def test_extra_rule_webfetch(self, tmp_path):
        """Custom rules also apply to WebFetch tool."""
        rules = [
            {
                "name": "custom-internal",
                "pattern": r"internal\.example\.corp",
                "message": "Blocked internal URL.",
            }
        ]
        rules_file = tmp_path / "extra-rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_with_extra_url_rules(
            "WebFetch",
            {"url": "https://internal.example.corp/page", "prompt": "test"},
            rules_file,
        )
        assert result.returncode == 2

    def test_missing_file_does_not_break(self, tmp_path):
        """Non-existent rules file is silently ignored."""
        result = _run_with_extra_url_rules(
            "Bash",
            {"command": "curl https://example.com/data"},
            tmp_path / "nonexistent.json",
        )
        assert result.returncode == 0

    def test_invalid_json_does_not_break(self, tmp_path):
        """Malformed JSON rules file is silently ignored."""
        rules_file = tmp_path / "bad-rules.json"
        rules_file.write_text("not valid json {{{")
        result = _run_with_extra_url_rules(
            "Bash",
            {"command": "curl https://example.com/data"},
            rules_file,
        )
        assert result.returncode == 0

    def test_invalid_regex_does_not_break(self, tmp_path):
        """Invalid regex in rules file is silently ignored."""
        rules = [{"name": "bad", "pattern": "[invalid(", "message": "msg"}]
        rules_file = tmp_path / "bad-regex.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_with_extra_url_rules(
            "Bash",
            {"command": "curl https://example.com/data"},
            rules_file,
        )
        assert result.returncode == 0

    def test_non_string_pattern_does_not_break(self, tmp_path):
        """Non-string pattern (e.g. integer) is silently ignored."""
        rules = [{"name": "bad", "pattern": 123, "message": "msg"}]
        rules_file = tmp_path / "bad-type.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_with_extra_url_rules(
            "Bash",
            {"command": "curl https://example.com/data"},
            rules_file,
        )
        assert result.returncode == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Custom command guard rules: COMMAND_GUARD_EXTRA_RULES env var
# ═══════════════════════════════════════════════════════════════════════════════


class TestExtraCommandRules:
    """COMMAND_GUARD_EXTRA_RULES env var loads custom command rules from a JSON file."""

    def test_extra_rule_blocks_matching_command(self, tmp_path):
        """Custom rules loaded from JSON file block matching commands."""
        rules = [
            {
                "name": "oc-delete",
                "pattern": r"^\s*oc\s+delete\b",
                "message": "oc delete is blocked. Use the OpenShift console instead.",
            }
        ]
        rules_file = tmp_path / "extra-cmd-rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_with_extra_cmd_rules("oc delete pod my-pod", rules_file)
        assert result.returncode == 2
        assert "OpenShift console" in result.stderr

    def test_extra_rule_allows_non_matching(self, tmp_path):
        """Custom rules do not block commands that don't match."""
        rules = [
            {
                "name": "oc-delete",
                "pattern": r"^\s*oc\s+delete\b",
                "message": "Blocked.",
            }
        ]
        rules_file = tmp_path / "extra-cmd-rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_with_extra_cmd_rules("oc get pods", rules_file)
        assert result.returncode == 0

    def test_extra_rule_with_exception(self, tmp_path):
        """Custom rules with exception pattern allow exception matches."""
        rules = [
            {
                "name": "oc-delete",
                "pattern": r"^\s*oc\s+delete\b",
                "message": "oc delete is blocked.",
                "exception": "--dry-run",
            }
        ]
        rules_file = tmp_path / "extra-cmd-rules.json"
        rules_file.write_text(json.dumps(rules))
        # Without exception → blocked
        result = _run_with_extra_cmd_rules("oc delete pod my-pod", rules_file)
        assert result.returncode == 2
        # With exception → allowed
        result = _run_with_extra_cmd_rules("oc delete pod my-pod --dry-run", rules_file)
        assert result.returncode == 0

    def test_extra_rule_in_chained_commands(self, tmp_path):
        """Custom rules are checked in chained commands (&&, ||, ;)."""
        rules = [
            {
                "name": "gh-repo-delete",
                "pattern": r"^\s*gh\s+repo\s+delete\b",
                "message": "gh repo delete is blocked.",
            }
        ]
        rules_file = tmp_path / "extra-cmd-rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_with_extra_cmd_rules("git status && gh repo delete my-repo", rules_file)
        assert result.returncode == 2

    def test_extra_rule_in_pipe_segment(self, tmp_path):
        """Custom rules are checked in pipe segments."""
        rules = [
            {
                "name": "dangerous-cmd",
                "pattern": r"^\s*dangerous\b",
                "message": "dangerous command blocked.",
            }
        ]
        rules_file = tmp_path / "extra-cmd-rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_with_extra_cmd_rules("echo data | dangerous --flag", rules_file)
        assert result.returncode == 2

    def test_extra_rule_with_env_prefix(self, tmp_path):
        """Custom rules work with environment variable prefixes."""
        rules = [
            {
                "name": "oc-delete",
                "pattern": r"^\s*oc\s+delete\b",
                "message": "oc delete blocked.",
            }
        ]
        rules_file = tmp_path / "extra-cmd-rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_with_extra_cmd_rules("KUBECONFIG=x oc delete pod p", rules_file)
        assert result.returncode == 2

    def test_guard_bypass_overrides_extra_rules(self, tmp_path):
        """GUARD_BYPASS=1 andon cord overrides custom command rules."""
        rules = [
            {
                "name": "oc-delete",
                "pattern": r"^\s*oc\s+delete\b",
                "message": "Blocked.",
            }
        ]
        rules_file = tmp_path / "extra-cmd-rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_with_extra_cmd_rules("GUARD_BYPASS=1 oc delete pod my-pod", rules_file)
        assert result.returncode == 0

    def test_missing_file_does_not_break(self, tmp_path):
        """Non-existent rules file is silently ignored."""
        result = _run_with_extra_cmd_rules("oc delete pod my-pod", tmp_path / "nonexistent.json")
        assert result.returncode == 0

    def test_invalid_json_does_not_break(self, tmp_path):
        """Malformed JSON rules file is silently ignored."""
        rules_file = tmp_path / "bad-rules.json"
        rules_file.write_text("not valid json {{{")
        result = _run_with_extra_cmd_rules("oc delete pod my-pod", rules_file)
        assert result.returncode == 0

    def test_invalid_regex_does_not_break(self, tmp_path):
        """Invalid regex in rules file is silently ignored."""
        rules = [{"name": "bad", "pattern": "[invalid(", "message": "msg"}]
        rules_file = tmp_path / "bad-regex.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_with_extra_cmd_rules("some command", rules_file)
        assert result.returncode == 0

    def test_non_string_pattern_does_not_break(self, tmp_path):
        """Non-string pattern (e.g. integer) is silently ignored."""
        rules = [{"name": "bad", "pattern": 123, "message": "msg"}]
        rules_file = tmp_path / "bad-type.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_with_extra_cmd_rules("some command", rules_file)
        assert result.returncode == 0

    def test_multiple_extra_rules(self, tmp_path):
        """Multiple custom rules are all checked."""
        rules = [
            {
                "name": "oc-delete",
                "pattern": r"^\s*oc\s+delete\b",
                "message": "oc delete blocked.",
            },
            {
                "name": "gh-repo-delete",
                "pattern": r"^\s*gh\s+repo\s+delete\b",
                "message": "gh repo delete blocked.",
            },
        ]
        rules_file = tmp_path / "extra-cmd-rules.json"
        rules_file.write_text(json.dumps(rules))
        # First rule matches
        result = _run_with_extra_cmd_rules("oc delete pod my-pod", rules_file)
        assert result.returncode == 2
        assert "oc delete" in result.stderr
        # Second rule matches
        result = _run_with_extra_cmd_rules("gh repo delete my-repo", rules_file)
        assert result.returncode == 2
        assert "gh repo delete" in result.stderr
        # Neither rule matches
        result = _run_with_extra_cmd_rules("oc get pods", rules_file)
        assert result.returncode == 0

    def test_action_ask_prompts_user(self, tmp_path):
        """action: ask outputs JSON permissionDecision instead of blocking."""
        rules = [
            {
                "name": "oc-scale-zero",
                "pattern": r"^\s*oc\s+scale\b.*--replicas=0",
                "message": "Scaling to zero — confirm this is intentional.",
                "action": "ask",
            }
        ]
        rules_file = tmp_path / "extra-cmd-rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_with_extra_cmd_rules("oc scale deployment/app --replicas=0", rules_file)
        assert_ask_decision(result, "confirm")

    def test_action_block_exits_2(self, tmp_path):
        """action: block (explicit) causes exit 2."""
        rules = [
            {
                "name": "oc-delete",
                "pattern": r"^\s*oc\s+delete\b",
                "message": "oc delete blocked.",
                "action": "block",
            }
        ]
        rules_file = tmp_path / "extra-cmd-rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_with_extra_cmd_rules("oc delete pod my-pod", rules_file)
        assert result.returncode == 2

    def test_action_default_is_block(self, tmp_path):
        """Omitting action defaults to block (exit 2)."""
        rules = [
            {
                "name": "oc-delete",
                "pattern": r"^\s*oc\s+delete\b",
                "message": "oc delete blocked.",
            }
        ]
        rules_file = tmp_path / "extra-cmd-rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_with_extra_cmd_rules("oc delete pod my-pod", rules_file)
        assert result.returncode == 2

    def test_action_ask_with_exception(self, tmp_path):
        """action: ask works with exception patterns."""
        rules = [
            {
                "name": "oc-scale-zero",
                "pattern": r"^\s*oc\s+scale\b.*--replicas=0",
                "message": "Scaling to zero — confirm.",
                "action": "ask",
                "exception": "--dry-run",
            }
        ]
        rules_file = tmp_path / "extra-cmd-rules.json"
        rules_file.write_text(json.dumps(rules))
        # Without exception → ask (JSON decision)
        result = _run_with_extra_cmd_rules("oc scale deployment/app --replicas=0", rules_file)
        assert_ask_decision(result, "confirm")
        # With exception → allowed (exit 0, no JSON)
        result = _run_with_extra_cmd_rules(
            "oc scale deployment/app --replicas=0 --dry-run", rules_file
        )
        assert result.returncode == 0

    def test_mixed_actions(self, tmp_path):
        """Rules with different actions coexist correctly."""
        rules = [
            {
                "name": "oc-delete",
                "pattern": r"^\s*oc\s+delete\b",
                "message": "oc delete blocked.",
                "action": "block",
            },
            {
                "name": "oc-scale-zero",
                "pattern": r"^\s*oc\s+scale\b.*--replicas=0",
                "message": "Scaling to zero — confirm.",
                "action": "ask",
            },
        ]
        rules_file = tmp_path / "extra-cmd-rules.json"
        rules_file.write_text(json.dumps(rules))
        # Block rule → exit 2
        result = _run_with_extra_cmd_rules("oc delete pod my-pod", rules_file)
        assert result.returncode == 2
        # Ask rule → JSON decision
        result = _run_with_extra_cmd_rules("oc scale deployment/app --replicas=0", rules_file)
        assert_ask_decision(result, "confirm")
        # Neither → exit 0
        result = _run_with_extra_cmd_rules("oc get pods", rules_file)
        assert result.returncode == 0

    def test_invalid_action_defaults_to_block(self, tmp_path):
        """Unknown action value defaults to block (exit 2)."""
        rules = [
            {
                "name": "oc-delete",
                "pattern": r"^\s*oc\s+delete\b",
                "message": "oc delete blocked.",
                "action": "invalid-value",
            }
        ]
        rules_file = tmp_path / "extra-cmd-rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_with_extra_cmd_rules("oc delete pod my-pod", rules_file)
        assert result.returncode == 2


# ═══════════════════════════════════════════════════════════════════════════════
# Custom URL guard rules: action field support
# ═══════════════════════════════════════════════════════════════════════════════


class TestExtraURLRulesAction:
    """URL_GUARD_EXTRA_RULES action field: ask vs block."""

    def test_url_action_ask_prompts_user(self, tmp_path):
        """URL rule with action: ask outputs JSON permissionDecision."""
        rules = [
            {
                "name": "staging-api",
                "pattern": r"staging\.api\.example\.com",
                "message": "Staging API — confirm this is intentional.",
                "action": "ask",
            }
        ]
        rules_file = tmp_path / "extra-url-rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_with_extra_url_rules(
            "WebFetch",
            {"url": "https://staging.api.example.com/data", "prompt": "test"},
            rules_file,
        )
        assert_ask_decision(result, "confirm")

    def test_url_action_block_exits_2(self, tmp_path):
        """URL rule with action: block (explicit) causes exit 2."""
        rules = [
            {
                "name": "internal-api",
                "pattern": r"internal\.example\.com",
                "message": "Blocked.",
                "action": "block",
            }
        ]
        rules_file = tmp_path / "extra-url-rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_with_extra_url_rules(
            "WebFetch",
            {"url": "https://internal.example.com/api", "prompt": "test"},
            rules_file,
        )
        assert result.returncode == 2

    def test_url_action_default_is_block(self, tmp_path):
        """Omitting action on URL rule defaults to block (exit 2)."""
        rules = [
            {
                "name": "internal-api",
                "pattern": r"internal\.example\.com",
                "message": "Blocked.",
            }
        ]
        rules_file = tmp_path / "extra-url-rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_with_extra_url_rules(
            "WebFetch",
            {"url": "https://internal.example.com/api", "prompt": "test"},
            rules_file,
        )
        assert result.returncode == 2

    def test_url_action_ask_curl(self, tmp_path):
        """URL rule with action: ask also works for curl commands."""
        rules = [
            {
                "name": "staging-api",
                "pattern": r"staging\.api\.example\.com",
                "message": "Staging API — confirm.",
                "action": "ask",
            }
        ]
        rules_file = tmp_path / "extra-url-rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_with_extra_url_rules(
            "Bash",
            {"command": "curl https://staging.api.example.com/data"},
            rules_file,
        )
        assert_ask_decision(result, "confirm")


# ═══════════════════════════════════════════════════════════════════════════════
# --validate mode: config file validation
# ═══════════════════════════════════════════════════════════════════════════════


def _run_validate(**env_overrides) -> subprocess.CompletedProcess:
    """Run the guard with --validate flag and optional env var overrides."""
    env = os.environ.copy()
    # Clear any existing config env vars
    env.pop("URL_GUARD_EXTRA_RULES", None)
    env.pop("COMMAND_GUARD_EXTRA_RULES", None)
    env.update(env_overrides)
    return subprocess.run(
        ["uv", "run", SCRIPT, "--validate"],
        capture_output=True,
        text=True,
        env=env,
    )


class TestValidateMode:
    """--validate flag checks config files and reports issues."""

    def test_no_env_vars_exits_0_silently(self):
        """No env vars set → exit 0, no output."""
        result = _run_validate()
        assert result.returncode == 0
        assert result.stderr.strip() == ""

    def test_valid_url_rules(self, tmp_path):
        """Valid URL rules file → exit 0, success message."""
        rules = [
            {
                "name": "internal",
                "pattern": r"internal\.example\.com",
                "message": "Blocked.",
            }
        ]
        rules_file = tmp_path / "url-rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_validate(URL_GUARD_EXTRA_RULES=str(rules_file))
        assert result.returncode == 0
        assert "1 rule(s)" in result.stdout

    def test_valid_command_rules(self, tmp_path):
        """Valid command rules file → exit 0, success message."""
        rules = [
            {
                "name": "oc-delete",
                "pattern": r"^\s*oc\s+delete\b",
                "message": "Blocked.",
            },
            {
                "name": "gh-delete",
                "pattern": r"^\s*gh\s+repo\s+delete\b",
                "message": "Blocked.",
            },
        ]
        rules_file = tmp_path / "cmd-rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_validate(COMMAND_GUARD_EXTRA_RULES=str(rules_file))
        assert result.returncode == 0
        assert "2 rule(s)" in result.stdout

    def test_missing_file(self, tmp_path):
        """Missing file → exit 2, error fed to Claude."""
        result = _run_validate(COMMAND_GUARD_EXTRA_RULES=str(tmp_path / "nonexistent.json"))
        assert result.returncode == 2
        assert "file not found" in result.stderr

    def test_invalid_json(self, tmp_path):
        """Invalid JSON → exit 2, error fed to Claude."""
        rules_file = tmp_path / "bad.json"
        rules_file.write_text("not json {{{")
        result = _run_validate(COMMAND_GUARD_EXTRA_RULES=str(rules_file))
        assert result.returncode == 2
        assert "invalid JSON" in result.stderr

    def test_not_array(self, tmp_path):
        """JSON object instead of array → exit 2, error fed to Claude."""
        rules_file = tmp_path / "obj.json"
        rules_file.write_text('{"name": "x"}')
        result = _run_validate(COMMAND_GUARD_EXTRA_RULES=str(rules_file))
        assert result.returncode == 2
        assert "expected JSON array" in result.stderr

    def test_empty_array(self, tmp_path):
        """Empty array → exit 2, warning fed to Claude."""
        rules_file = tmp_path / "empty.json"
        rules_file.write_text("[]")
        result = _run_validate(COMMAND_GUARD_EXTRA_RULES=str(rules_file))
        assert result.returncode == 2
        assert "empty array" in result.stderr

    def test_missing_required_fields(self, tmp_path):
        """Missing name/pattern/message → exit 2, lists missing fields."""
        rules = [{"name": "x"}]
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_validate(COMMAND_GUARD_EXTRA_RULES=str(rules_file))
        assert result.returncode == 2
        assert "missing required field 'pattern'" in result.stderr
        assert "missing required field 'message'" in result.stderr

    def test_non_string_pattern(self, tmp_path):
        """Non-string pattern → exit 2, type error."""
        rules = [{"name": "x", "pattern": 123, "message": "msg"}]
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_validate(COMMAND_GUARD_EXTRA_RULES=str(rules_file))
        assert result.returncode == 2
        assert "must be a string" in result.stderr

    def test_invalid_regex_pattern(self, tmp_path):
        """Invalid regex → exit 2, error fed to Claude."""
        rules = [{"name": "x", "pattern": "[invalid(", "message": "msg"}]
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_validate(COMMAND_GUARD_EXTRA_RULES=str(rules_file))
        assert result.returncode == 2
        assert "invalid regex" in result.stderr

    def test_empty_pattern_warning(self, tmp_path):
        """Empty pattern string → exit 2, warns about matching everything."""
        rules = [{"name": "x", "pattern": "", "message": "msg"}]
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_validate(COMMAND_GUARD_EXTRA_RULES=str(rules_file))
        assert result.returncode == 2
        assert "match ALL" in result.stderr

    def test_empty_exception_warning(self, tmp_path):
        """Empty exception string → exit 2, warns about disabling rule."""
        rules = [{"name": "x", "pattern": r"^\s*oc\b", "message": "msg", "exception": ""}]
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_validate(COMMAND_GUARD_EXTRA_RULES=str(rules_file))
        assert result.returncode == 2
        assert "disabling this rule" in result.stderr

    def test_invalid_exception_regex(self, tmp_path):
        """Invalid exception regex → exit 1."""
        rules = [
            {
                "name": "x",
                "pattern": r"^\s*oc\b",
                "message": "msg",
                "exception": "[bad(",
            }
        ]
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_validate(COMMAND_GUARD_EXTRA_RULES=str(rules_file))
        assert result.returncode == 2
        assert "invalid regex in 'exception'" in result.stderr

    def test_invalid_action_value(self, tmp_path):
        """Unknown action value → exit 2."""
        rules = [{"name": "x", "pattern": r"oc", "message": "msg", "action": "warn"}]
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_validate(COMMAND_GUARD_EXTRA_RULES=str(rules_file))
        assert result.returncode == 2
        assert "'block', 'ask', or 'allow'" in result.stderr

    def test_non_string_action(self, tmp_path):
        """Non-string action → exit 2."""
        rules = [{"name": "x", "pattern": r"oc", "message": "msg", "action": 1}]
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_validate(COMMAND_GUARD_EXTRA_RULES=str(rules_file))
        assert result.returncode == 2
        assert "'action' must be a string" in result.stderr

    def test_entry_not_object(self, tmp_path):
        """Array entry that isn't an object → exit 2."""
        rules_file = tmp_path / "rules.json"
        rules_file.write_text('["not an object"]')
        result = _run_validate(COMMAND_GUARD_EXTRA_RULES=str(rules_file))
        assert result.returncode == 2
        assert "expected object" in result.stderr

    def test_valid_with_action_ask(self, tmp_path):
        """Valid rule with action: ask → exit 0."""
        rules = [
            {
                "name": "x",
                "pattern": r"^\s*oc\s+scale\b",
                "message": "Confirm.",
                "action": "ask",
            }
        ]
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_validate(COMMAND_GUARD_EXTRA_RULES=str(rules_file))
        assert result.returncode == 0

    def test_mixed_valid_and_invalid(self, tmp_path):
        """One valid + one invalid file → exit 2, shows both."""
        url_rules = [{"name": "x", "pattern": r"example\.com", "message": "Blocked."}]
        url_file = tmp_path / "url.json"
        url_file.write_text(json.dumps(url_rules))
        cmd_file = tmp_path / "cmd.json"
        cmd_file.write_text("bad json")
        result = _run_validate(
            URL_GUARD_EXTRA_RULES=str(url_file),
            COMMAND_GUARD_EXTRA_RULES=str(cmd_file),
        )
        assert result.returncode == 2
        # URL rules valid
        assert "1 rule(s)" in result.stderr
        # Command rules invalid
        assert "invalid JSON" in result.stderr

    def test_multiple_issues_in_one_file(self, tmp_path):
        """Multiple entries with different issues → all reported."""
        rules = [
            {"name": "a", "pattern": "[bad(", "message": "msg"},
            {"name": "b", "pattern": r"ok", "action": "invalid"},
        ]
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_validate(COMMAND_GUARD_EXTRA_RULES=str(rules_file))
        assert result.returncode == 2
        assert "invalid regex" in result.stderr
        assert "'block', 'ask', or 'allow'" in result.stderr


# ═══════════════════════════════════════════════════════════════════════════════
# Kill command guard
# ═══════════════════════════════════════════════════════════════════════════════


class TestKillCommandGuard:
    """Tests for _extract_kill_targets, process tree utilities, and kill guard integration."""

    # ── Unit tests: _extract_kill_targets ──

    @pytest.mark.parametrize(
        "cmd, expected",
        [
            # Basic numeric PID
            ("kill 1234", ("kill", [1234], [])),
            # Signal flag -9
            ("kill -9 1234", ("kill", [1234], [])),
            # Signal flag -SIGTERM, multiple PIDs
            ("kill -SIGTERM 1234 5678", ("kill", [1234, 5678], [])),
            # Signal via -s TERM
            ("kill -s TERM 1234", ("kill", [1234], [])),
            # Job reference %1 — no numeric PIDs
            ("kill %1", ("kill", [], ["%1"])),
            # kill -- separator, numeric PID
            ("kill -- 1234", ("kill", [1234], [])),
            # kill -9 with -- separator and multiple PIDs
            ("kill -9 -- 1234 5678", ("kill", [1234, 5678], [])),
            # xargs pipe — dynamic
            ("echo foo | xargs kill", ("kill", [], ["dynamic"])),
        ],
        ids=[
            "basic-pid",
            "signal-9",
            "sigterm-multi-pid",
            "s-term",
            "job-ref",
            "double-dash",
            "signal-double-dash-multi",
            "xargs-kill",
        ],
    )
    def test_extract_kill_targets_kill(self, cmd, expected):
        result = _mod._extract_kill_targets(cmd)
        assert result is not None
        cmd_type, pids, names = result
        exp_type, exp_pids, exp_names = expected
        assert cmd_type == exp_type
        assert pids == exp_pids
        assert names == exp_names

    @pytest.mark.parametrize(
        "cmd",
        [
            "kill -l",
            "kill -L",
        ],
        ids=["kill-l", "kill-L"],
    )
    def test_extract_kill_targets_informational_returns_none(self, cmd):
        assert _mod._extract_kill_targets(cmd) is None

    def test_extract_kill_targets_killall_command_type(self):
        result = _mod._extract_kill_targets("killall python3")
        assert result is not None
        cmd_type, _pids, _names = result
        assert cmd_type == "killall"

    def test_extract_kill_targets_pkill_command_type(self):
        result = _mod._extract_kill_targets('pkill -f "some pattern"')
        assert result is not None
        cmd_type, _pids, _names = result
        assert cmd_type == "pkill"

    def test_extract_kill_targets_not_kill_command(self):
        assert _mod._extract_kill_targets("echo hello") is None

    # ── Unit tests: process tree utilities ──

    def test_get_parent_info_current_process(self):
        """_get_parent_info returns (ppid, comm) for a live PID."""
        result = _mod._get_parent_info(os.getpid())
        assert result is not None
        ppid, comm = result
        assert ppid > 0
        assert isinstance(comm, str)
        assert len(comm) > 0

    def test_get_parent_info_nonexistent_pid(self):
        """_get_parent_info returns None for a nonexistent PID."""
        assert _mod._get_parent_info(999999999) is None

    def test_is_descendant_of_current_process_descends_from_parent(self):
        """A live process is a descendant of its own parent PID."""
        parent_info = _mod._get_parent_info(os.getpid())
        assert parent_info is not None, "Could not get parent info for current process"
        ppid = parent_info[0]
        assert _mod._is_descendant_of(os.getpid(), ppid) is True

    def test_is_descendant_of_nonexistent_target(self):
        """Nonexistent target PID returns False."""
        assert _mod._is_descendant_of(999999999, 1) is False

    def test_is_descendant_of_nonexistent_ancestor(self):
        """A live PID is not a descendant of a nonexistent ancestor."""
        assert _mod._is_descendant_of(os.getpid(), 999999999) is False

    # ── Integration tests: black-box via run_bash ──

    def test_kill_l_passes_through(self):
        """`kill -l` is informational — guard allows it (exit 0, no ask)."""
        result = run_bash("kill -l")
        assert_guard(result, 0)

    def test_kill_L_passes_through(self):
        """`kill -L` is informational — guard allows it (exit 0, no ask)."""
        result = run_bash("kill -L")
        assert_guard(result, 0)

    def test_kill_nonexistent_pid_triggers_ask(self):
        """`kill 999999999` targets an unknown PID — guard asks for confirmation."""
        result = run_bash("kill 999999999")
        assert_ask_decision(result, "kill-non-claude-process")

    def test_kill_zero_nonexistent_pid_triggers_ask(self):
        """`kill -0 999999999` (existence check) still triggers ask."""
        result = run_bash("kill -0 999999999")
        assert_ask_decision(result, "kill-non-claude-process")

    def test_kill_with_guard_bypass_allowed(self):
        """GUARD_BYPASS=1 command prefix overrides the kill guard — command passes through."""
        result = run_bash("GUARD_BYPASS=1 kill 999999999")
        assert_guard(result, 0)


# ═══════════════════════════════════════════════════════════════════════════════
# Unified logging: SQLite audit database
# ═══════════════════════════════════════════════════════════════════════════════


def _run_guard_with_db(tool_name, tool_input, tmp_path, *, env_extra=None, session_id=None):
    """Run the guard with a custom SQLite database path."""
    env = os.environ.copy()
    env["GUARD_DB_PATH"] = str(tmp_path / "test.db")
    env["GUARD_LOG_LEVEL"] = "all"
    if env_extra:
        env.update(env_extra)
    extra = {}
    if session_id:
        extra["session_id"] = session_id
    return run_guard(tool_name, tool_input, env=env, payload_extra=extra or None)


def _read_all_events(tmp_path):
    """Read all events from the SQLite database."""
    db_path = tmp_path / "test.db"
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM events ORDER BY id").fetchall()
    conn.close()
    return [dict(row) for row in rows]


def _read_url_events(tmp_path):
    """Read URL guard events from the SQLite database."""
    db_path = tmp_path / "test.db"
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM events WHERE category='url' ORDER BY id").fetchall()
    conn.close()
    entries = []
    for row in rows:
        entry = {
            "action": row["action"],
            "rule": row["rule"],
            "url": row["command"],
            "ts": row["ts"],
        }
        if row["detail"]:
            detail = json.loads(row["detail"])
            entry["tool"] = detail.get("tool")
            entry["phase"] = detail.get("phase")
            entry["response_code"] = detail.get("response_code")
            entry["auth_failed"] = detail.get("auth_failed")
        entries.append(entry)
    return entries


class TestUnifiedLogging:
    """Tests for the unified SQLite audit logging system."""

    def test_db_created_on_first_event(self, tmp_path):
        """Database file is created when the first event is logged."""
        db_path = tmp_path / "test.db"
        assert not db_path.exists()
        _run_guard_with_db("Bash", {"command": "cat file.py"}, tmp_path)
        assert db_path.exists()

    def test_db_uses_wal_mode(self, tmp_path):
        """Database uses WAL journal mode."""
        _run_guard_with_db("Bash", {"command": "cat file.py"}, tmp_path)
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"

    def test_event_inserted_on_block(self, tmp_path):
        """Blocked commands produce events in the database."""
        _run_guard_with_db("Bash", {"command": "cat file.py"}, tmp_path)
        events = _read_all_events(tmp_path)
        assert len(events) >= 1
        guard_events = [e for e in events if e["category"] == "guard"]
        assert len(guard_events) >= 1
        assert guard_events[0]["action"] == "blocked"

    def test_log_level_off_no_events(self, tmp_path):
        """GUARD_LOG_LEVEL=off produces no events."""
        _run_guard_with_db(
            "Bash", {"command": "cat file.py"}, tmp_path, env_extra={"GUARD_LOG_LEVEL": "off"}
        )
        db_path = tmp_path / "test.db"
        if db_path.exists():
            events = _read_all_events(tmp_path)
            assert len(events) == 0

    def test_log_level_actions_skips_allowed(self, tmp_path):
        """GUARD_LOG_LEVEL=actions skips allowed events."""
        _run_guard_with_db(
            "Bash",
            {"command": "git status"},
            tmp_path,
            env_extra={"GUARD_LOG_LEVEL": "actions"},
        )
        events = _read_all_events(tmp_path)
        allowed_events = [e for e in events if e["action"] == "allowed"]
        assert len(allowed_events) == 0

    def test_log_level_all_includes_allowed(self, tmp_path):
        """GUARD_LOG_LEVEL=all includes allowed events."""
        _run_guard_with_db("Bash", {"command": "git status"}, tmp_path)
        events = _read_all_events(tmp_path)
        allowed_events = [e for e in events if e["action"] == "allowed"]
        assert len(allowed_events) >= 1

    def test_session_id_stored(self, tmp_path):
        """Session ID from payload is stored in events."""
        _run_guard_with_db(
            "Bash", {"command": "cat file.py"}, tmp_path, session_id="test-session-123"
        )
        events = _read_all_events(tmp_path)
        guard_events = [e for e in events if e["category"] == "guard"]
        assert len(guard_events) >= 1
        assert guard_events[0]["session_id"] == "test-session-123"


# ═══════════════════════════════════════════════════════════════════════════════
# Extra command rules: action=allow
# ═══════════════════════════════════════════════════════════════════════════════


class TestExtraCommandRulesAllow:
    """Tests for the allow action in extra command rules."""

    def test_allow_bypasses_permission_system(self, tmp_path):
        """action: allow exits 0 and outputs permissionDecision allow."""
        rules = [
            {
                "name": "oc-get-allow",
                "pattern": r"^\s*oc\s+get\b",
                "message": "oc get is always allowed.",
                "action": "allow",
            }
        ]
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_with_extra_cmd_rules("oc get pods", rules_file)
        assert result.returncode == 0
        output = json.loads(result.stdout)
        hook_output = output.get("hookSpecificOutput", {})
        assert hook_output.get("permissionDecision") == "allow"

    def test_allow_with_exception(self, tmp_path):
        """Exception pattern skips the allow rule."""
        rules = [
            {
                "name": "oc-get-allow",
                "pattern": r"^\s*oc\s+get\b",
                "message": "oc get allowed.",
                "action": "allow",
                "exception": "--all-namespaces",
            }
        ]
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))
        # Without exception -> allow decision
        result = _run_with_extra_cmd_rules("oc get pods", rules_file)
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output.get("hookSpecificOutput", {}).get("permissionDecision") == "allow"
        # With exception -> passes through (no allow decision from this rule)
        result = _run_with_extra_cmd_rules("oc get pods --all-namespaces", rules_file)
        assert result.returncode == 0

    def test_allow_before_ask(self, tmp_path):
        """First matching rule wins: allow before ask."""
        rules = [
            {
                "name": "oc-get-allow",
                "pattern": r"^\s*oc\s+get\b",
                "message": "oc get allowed.",
                "action": "allow",
            },
            {
                "name": "oc-any-ask",
                "pattern": r"^\s*oc\b",
                "message": "Confirm oc command.",
                "action": "ask",
            },
        ]
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_with_extra_cmd_rules("oc get pods", rules_file)
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output.get("hookSpecificOutput", {}).get("permissionDecision") == "allow"

    def test_allow_does_not_skip_dangerous_pipe_segment(self, tmp_path):
        """Allow on first pipe segment must NOT skip checking later segments."""
        rules = [
            {
                "name": "gh-read-allow",
                "pattern": r"^\s*gh\s+pr\s+view\b",
                "message": "Read-only gh commands are safe.",
                "action": "allow",
            },
            {
                "name": "danger-block",
                "pattern": r"^\s*dangerous-cmd\b",
                "message": "This command is blocked.",
                "action": "block",
            },
        ]
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))
        # gh pr view is allowed, but dangerous-cmd in the pipe must still be blocked
        result = _run_with_extra_cmd_rules(
            "gh pr view 172 --json title | dangerous-cmd", rules_file
        )
        assert result.returncode == 2
        assert "blocked" in result.stderr.lower() or "danger" in result.stderr.lower()

    def test_allow_passes_when_pipe_segments_clean(self, tmp_path):
        """Allow rule passes when all pipe segments are also clean."""
        rules = [
            {
                "name": "gh-read-allow",
                "pattern": r"^\s*gh\s+pr\s+view\b",
                "message": "Read-only gh commands are safe.",
                "action": "allow",
            }
        ]
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_with_extra_cmd_rules("gh pr view 172 --json title | jq '.title'", rules_file)
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output.get("hookSpecificOutput", {}).get("permissionDecision") == "allow"

    def test_validate_accepts_allow_action(self, tmp_path):
        """--validate accepts action: allow as valid."""
        rules = [
            {
                "name": "oc-get-allow",
                "pattern": r"^\s*oc\s+get\b",
                "message": "Allowed.",
                "action": "allow",
            }
        ]
        rules_file = tmp_path / "rules.json"
        rules_file.write_text(json.dumps(rules))
        result = _run_validate(COMMAND_GUARD_EXTRA_RULES=str(rules_file))
        assert result.returncode == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Trust database: unit tests
# ═══════════════════════════════════════════════════════════════════════════════


def _load_guard_module(tmp_path):
    """Load the guard module with a fresh DB path. Returns the module."""
    spec = importlib.util.spec_from_file_location("guard_trust", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Post-exec override needed because module-level _DB_PATH is computed at import time
    mod._DB_PATH = tmp_path / "trust-test.db"
    mod._db_conn = None
    mod._GUARD_LOG_LEVEL = "all"
    return mod


class TestTrustDatabase:
    """Unit tests for _add_trust, _check_trust, _remove_trust, _list_trust."""

    def test_add_and_check_trust(self, tmp_path):
        """Adding a trust rule makes it checkable."""
        mod = _load_guard_module(tmp_path)
        ok, msg = mod._add_trust("test-rule", None, "always", None)
        assert ok is True
        assert "Trusted" in msg
        assert mod._check_trust("test-rule", "some command", "any-session") is True

    def test_check_trust_not_found(self, tmp_path):
        """Non-existent rule returns False."""
        mod = _load_guard_module(tmp_path)
        assert mod._check_trust("nonexistent", "cmd", "session") is False

    def test_session_scoped_trust(self, tmp_path):
        """Session-scoped trust only matches the correct session."""
        mod = _load_guard_module(tmp_path)
        mod._add_trust("test-rule", None, "session", "session-A")
        assert mod._check_trust("test-rule", "cmd", "session-A") is True
        assert mod._check_trust("test-rule", "cmd", "session-B") is False

    def test_match_pattern_filter(self, tmp_path):
        """Match pattern does case-insensitive substring filtering."""
        mod = _load_guard_module(tmp_path)
        mod._add_trust("test-rule", "deploy", "always", None)
        assert mod._check_trust("test-rule", "oc get deploy -n prod", "s") is True
        assert mod._check_trust("test-rule", "oc get DEPLOY -n prod", "s") is True
        assert mod._check_trust("test-rule", "oc get pods", "s") is False

    def test_remove_trust(self, tmp_path):
        """Removing a trust rule makes it no longer checkable."""
        mod = _load_guard_module(tmp_path)
        mod._add_trust("test-rule", None, "always", None)
        assert mod._check_trust("test-rule", "cmd", "s") is True
        ok, count = mod._remove_trust("test-rule")
        assert ok is True
        assert count == 1
        assert mod._check_trust("test-rule", "cmd", "s") is False

    def test_remove_with_match_pattern(self, tmp_path):
        """Remove only the trust entry with matching pattern."""
        mod = _load_guard_module(tmp_path)
        mod._add_trust("test-rule", "deploy", "always", None)
        mod._add_trust("test-rule", "pod", "always", None)
        ok, count = mod._remove_trust("test-rule", "deploy")
        assert ok is True
        assert count == 1
        # "pod" pattern still exists
        assert mod._check_trust("test-rule", "oc get pod", "s") is True
        assert mod._check_trust("test-rule", "oc get deploy", "s") is False

    def test_list_trust(self, tmp_path):
        """Listing trust rules returns all entries."""
        mod = _load_guard_module(tmp_path)
        mod._add_trust("rule-a", None, "always", None)
        mod._add_trust("rule-b", "pattern", "session", "sid")
        rules = mod._list_trust()
        assert len(rules) == 2
        assert rules[0]["rule_name"] == "rule-a"
        assert rules[1]["rule_name"] == "rule-b"
        assert rules[1]["match_pattern"] == "pattern"
        assert rules[1]["scope"] == "session"

    def test_add_trust_replace_on_conflict(self, tmp_path):
        """Adding the same rule+pattern+scope replaces (INSERT OR REPLACE)."""
        mod = _load_guard_module(tmp_path)
        mod._add_trust("test-rule", None, "always", None)
        # Same rule, pattern, scope -> replaces
        mod._add_trust("test-rule", None, "always", None)
        rules = mod._list_trust()
        assert len(rules) == 1

    def test_different_scope_creates_separate_entries(self, tmp_path):
        """Different scopes for same rule+pattern create separate entries."""
        mod = _load_guard_module(tmp_path)
        mod._add_trust("test-rule", None, "always", None)
        mod._add_trust("test-rule", None, "session", "sid")
        rules = mod._list_trust()
        assert len(rules) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# Trust integration: ask rules with trust entries
# ═══════════════════════════════════════════════════════════════════════════════


class TestTrustIntegration:
    """Integration tests: trusted ask rules auto-allow silently."""

    def _setup_trust(self, tmp_path, rule_name, *, match_pattern=None, scope="always", sid=None):
        """Insert a trust entry directly into the database.

        Uses a guard invocation to initialize the DB schema, then inserts
        the trust entry via direct SQL (avoids duplicating DDL).
        """
        import datetime

        # Run a no-op guard invocation to create the DB with the canonical schema
        _run_guard_with_db("Bash", {"command": "git status"}, tmp_path)

        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO trusted_rules "
            "(rule_name, match_pattern, scope, session_id, created_ts) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                rule_name,
                match_pattern,
                scope,
                sid,
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()

    def _run(self, command, tmp_path, *, session_id=None):
        """Run the guard with trust DB."""
        return _run_guard_with_db("Bash", {"command": command}, tmp_path, session_id=session_id)

    def test_trusted_rule_allows_silently(self, tmp_path):
        """An ask rule with a trust entry auto-allows with trusted reason."""
        # git stash drop triggers the "stash-drop" ask rule
        self._setup_trust(tmp_path, "stash-drop")
        result = self._run("git stash drop", tmp_path)
        assert result.returncode == 0
        output = json.loads(result.stdout)
        hook_output = output.get("hookSpecificOutput", {})
        assert hook_output.get("permissionDecision") == "allow"
        assert "[trusted]" in hook_output.get("permissionDecisionReason", "")

    def test_block_rule_never_trusted(self, tmp_path):
        """Block rules (exit 2) are never bypassed by trust."""
        # "reset-hard" is a deny (block) rule, trust should not help
        self._setup_trust(tmp_path, "reset-hard")
        result = self._run("git reset --hard", tmp_path)
        assert result.returncode == 2

    def test_session_trust_expires(self, tmp_path):
        """Session-scoped trust only works for the matching session."""
        self._setup_trust(tmp_path, "stash-drop", scope="session", sid="session-A")
        # Right session -> trusted
        result = self._run("git stash drop", tmp_path, session_id="session-A")
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert "[trusted]" in output.get("hookSpecificOutput", {}).get(
            "permissionDecisionReason", ""
        )
        # Wrong session -> ask
        result = self._run("git stash drop", tmp_path, session_id="session-B")
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output.get("hookSpecificOutput", {}).get("permissionDecision") == "ask"

    def test_match_pattern_filters(self, tmp_path):
        """Trust with match pattern only matches commands containing the pattern."""
        self._setup_trust(tmp_path, "stash-drop", match_pattern="stash@{0}")
        result = self._run("git stash drop stash@{0}", tmp_path)
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert "[trusted]" in output.get("hookSpecificOutput", {}).get(
            "permissionDecisionReason", ""
        )
        # Different stash ref -> not trusted, falls through to ask
        result = self._run("git stash drop stash@{1}", tmp_path)
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output.get("hookSpecificOutput", {}).get("permissionDecision") == "ask"

    def test_untrusted_rule_still_asks(self, tmp_path):
        """Without trust, ask rules still prompt."""
        result = self._run("git stash drop", tmp_path)
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output.get("hookSpecificOutput", {}).get("permissionDecision") == "ask"


# ═══════════════════════════════════════════════════════════════════════════════
# Trust CLI: --trust add/remove/list
# ═══════════════════════════════════════════════════════════════════════════════


class TestTrustCommand:
    """CLI tests for --trust add/remove/list."""

    def _run_trust(self, args, tmp_path):
        """Run the guard with --trust and given args."""
        env = os.environ.copy()
        env["GUARD_DB_PATH"] = str(tmp_path / "test.db")
        return subprocess.run(
            ["uv", "run", SCRIPT, "--trust"] + args,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_list_empty(self, tmp_path):
        """--trust list with no rules shows 'No trusted rules'."""
        result = self._run_trust(["list"], tmp_path)
        assert result.returncode == 0
        assert "No trusted rules" in result.stdout

    def test_add_and_list(self, tmp_path):
        """--trust add creates a rule visible in --trust list."""
        result = self._run_trust(["add", "stash-drop", "--scope", "always"], tmp_path)
        assert result.returncode == 0
        assert "Trusted" in result.stdout
        result = self._run_trust(["list"], tmp_path)
        assert result.returncode == 0
        assert "stash-drop" in result.stdout

    def test_add_with_match(self, tmp_path):
        """--trust add with --match stores the pattern."""
        self._run_trust(["add", "stash-drop", "--match", "deploy"], tmp_path)
        result = self._run_trust(["list"], tmp_path)
        assert "deploy" in result.stdout

    def test_remove(self, tmp_path):
        """--trust remove deletes a rule."""
        self._run_trust(["add", "stash-drop"], tmp_path)
        result = self._run_trust(["remove", "stash-drop"], tmp_path)
        assert result.returncode == 0
        assert "Removed" in result.stdout
        result = self._run_trust(["list"], tmp_path)
        assert "No trusted rules" in result.stdout

    def test_remove_with_match(self, tmp_path):
        """--trust remove with --match only removes the matching entry."""
        self._run_trust(["add", "stash-drop", "--match", "deploy"], tmp_path)
        self._run_trust(["add", "stash-drop", "--match", "pod"], tmp_path)
        self._run_trust(["remove", "stash-drop", "--match", "deploy"], tmp_path)
        result = self._run_trust(["list"], tmp_path)
        assert "pod" in result.stdout
        assert "deploy" not in result.stdout

    def test_add_no_rule_name(self, tmp_path):
        """--trust add without rule name shows usage."""
        result = self._run_trust(["add"], tmp_path)
        assert result.returncode == 2
        assert "Usage" in result.stderr

    def test_add_unknown_rule_rejected(self, tmp_path):
        """--trust add with unknown rule name is rejected."""
        result = self._run_trust(["add", "nonexistent-rule"], tmp_path)
        assert result.returncode == 2
        assert "not a known ask-type rule" in result.stderr
        assert "Trustable rules:" in result.stderr

    def test_unknown_action(self, tmp_path):
        """--trust with unknown action shows error."""
        result = self._run_trust(["invalid"], tmp_path)
        assert result.returncode == 2
        assert "Unknown trust action" in result.stderr

    def test_no_args(self, tmp_path):
        """--trust with no args shows usage."""
        result = self._run_trust([], tmp_path)
        assert result.returncode == 2
        assert "Usage" in result.stderr

    def test_session_scope_without_session(self, tmp_path):
        """--trust add --session without prior session ID fails."""
        result = self._run_trust(["add", "stash-drop", "--session"], tmp_path)
        assert result.returncode == 2
        assert "No session ID" in result.stderr

    def test_session_scope_with_prior_session(self, tmp_path):
        """--trust add --session works after a guard check sets the session."""
        # First, run a guard check to set the session ID
        _run_guard_with_db(
            "Bash", {"command": "git status"}, tmp_path, session_id="test-session-xyz"
        )
        # Now add a session trust
        result = self._run_trust(["add", "stash-drop", "--session"], tmp_path)
        assert result.returncode == 0
        assert "Trusted" in result.stdout

    def test_unknown_flag_rejected_bug003(self, tmp_path):
        """BUG-003: Unknown flags are rejected (not silently ignored)."""
        result = self._run_trust(["add", "stash-drop", "--unknown-flag", "value"], tmp_path)
        assert result.returncode == 2
        assert "Usage error" in result.stderr or "unrecognized" in result.stderr.lower()

    def test_remove_rejects_scope_flag_bug003(self, tmp_path):
        """BUG-003: remove action doesn't accept --scope flag."""
        result = self._run_trust(["remove", "stash-drop", "--scope", "session"], tmp_path)
        assert result.returncode == 2
        assert "unrecognized" in result.stderr.lower()

    def test_session_shorthand_bug003(self, tmp_path):
        """BUG-003: --session shorthand works as alias for --scope session."""
        # First set session ID via guard run
        _run_guard_with_db(
            "Bash", {"command": "git status"}, tmp_path, session_id="test-session-xyz"
        )
        # Now use --session shorthand (no --scope)
        result = self._run_trust(["add", "stash-drop", "--session"], tmp_path)
        assert result.returncode == 0
        assert "Trusted" in result.stdout
        # Verify it was added with session scope
        result = self._run_trust(["list"], tmp_path)
        assert result.returncode == 0
        assert "session" in result.stdout

    def test_always_shorthand_bug003(self, tmp_path):
        """BUG-003: --always shorthand works as alias for --scope always."""
        result = self._run_trust(["add", "stash-drop", "--always"], tmp_path)
        assert result.returncode == 0
        assert "Trusted" in result.stdout
        # Verify it was added with always scope
        result = self._run_trust(["list"], tmp_path)
        assert result.returncode == 0
        assert "always" in result.stdout

    def test_explicit_session_id_bug004(self, tmp_path):
        """BUG-004: --session-id <id> allows explicit session ID specification."""
        result = self._run_trust(
            ["add", "stash-drop", "--scope", "session", "--session-id", "explicit-id-123"],
            tmp_path,
        )
        assert result.returncode == 0
        assert "Trusted" in result.stdout
        # Verify the rule was added and is session-scoped
        result = self._run_trust(["list"], tmp_path)
        assert result.returncode == 0
        assert "stash-drop" in result.stdout
        assert "session" in result.stdout

    def test_explicit_session_id_with_shorthand_bug004(self, tmp_path):
        """BUG-004: --session-id works with --session shorthand."""
        result = self._run_trust(
            ["add", "stash-drop", "--session", "--session-id", "custom-session"],
            tmp_path,
        )
        assert result.returncode == 0
        assert "Trusted" in result.stdout
        # Verify the rule was added
        result = self._run_trust(["list"], tmp_path)
        assert result.returncode == 0
        assert "stash-drop" in result.stdout

    def test_trust_hint_includes_session_id_bug004(self, tmp_path):
        """BUG-004: Ask prompt trust hint includes --session-id when available."""
        # Set up a guard check with a session ID and capture the ask output
        result = _run_guard_with_db(
            "Bash", {"command": "git stash drop"}, tmp_path, session_id="test-session-abc"
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        hook_output = output.get("hookSpecificOutput", {})
        reason = hook_output.get("permissionDecisionReason", "")
        # The reason should include the trust hint with --session-id
        assert "--session-id test-session-abc" in reason or "--session-id" in reason


# ═══════════════════════════════════════════════════════════════════════════════
# oc/kubectl command parsing: unit tests
# ═══════════════════════════════════════════════════════════════════════════════


_parse_oc_command = _mod._parse_oc_command
_classify_oc_risk = _mod._classify_oc_risk


class TestOcCommandParsing:
    """Unit tests for _parse_oc_command and _classify_oc_risk."""

    @pytest.mark.parametrize(
        "cmd, expected_verb, expected_resource",
        [
            ("oc get pods", "get", "pods"),
            ("oc get deploy -n prod", "get", "deploy"),
            ("kubectl get pods -o json", "get", "pods"),
            ("oc delete pod my-pod", "delete", "pod"),
            ("oc apply -f manifest.yaml", "apply", None),
            ("oc create deployment my-dep", "create", "deployment"),
            ("oc scale deployment/app --replicas=3", "scale", "deployment"),
            ("oc exec pod/my-pod -- ls", "exec", "pod"),
            ("oc logs my-pod", "logs", "my-pod"),
            ("oc describe pod my-pod", "describe", "pod"),
            ("oc get pods --all-namespaces", "get", "pods"),
            ("oc patch configmap cm-name -p '{}'", "patch", "configmap"),
        ],
        ids=[
            "get-pods",
            "get-deploy-namespace",
            "kubectl-get",
            "delete-pod",
            "apply-file",
            "create-deployment",
            "scale-deployment",
            "exec-pod",
            "logs",
            "describe",
            "get-all-ns",
            "patch-configmap",
        ],
    )
    def test_parse_verb_and_resource(self, cmd, expected_verb, expected_resource):
        parsed = _parse_oc_command(cmd)
        assert parsed is not None
        assert parsed["verb"] == expected_verb
        assert parsed["resource_type"] == expected_resource

    def test_parse_namespace(self):
        parsed = _parse_oc_command("oc get pods -n my-namespace")
        assert parsed["namespace"] == "my-namespace"

    def test_parse_namespace_long(self):
        parsed = _parse_oc_command("oc get pods --namespace=kube-system")
        assert parsed["namespace"] == "kube-system"

    def test_parse_filename(self):
        parsed = _parse_oc_command("oc apply -f deployment.yaml")
        assert parsed["filename"] == "deployment.yaml"

    def test_parse_filename_long(self):
        parsed = _parse_oc_command("oc apply --filename=deployment.yaml")
        assert parsed["filename"] == "deployment.yaml"

    def test_parse_flags(self):
        parsed = _parse_oc_command("oc get pods -o json --show-labels")
        assert "-o" in parsed["flags"]
        assert "--show-labels" in parsed["flags"]

    def test_parse_non_oc_returns_none(self):
        assert _parse_oc_command("git status") is None
        assert _parse_oc_command("") is None
        assert _parse_oc_command("ls -la") is None

    def test_parse_no_verb(self):
        parsed = _parse_oc_command("oc")
        assert parsed is not None
        assert parsed["verb"] is None

    @pytest.mark.parametrize(
        "cmd, expected_risk",
        [
            ("oc get pods", "safe"),
            ("oc describe pod my-pod", "safe"),
            ("oc logs my-pod", "safe"),
            ("oc delete pod my-pod", "high"),
            ("oc delete namespace prod", "critical"),
            ("oc apply -f deployment.yaml", "medium"),
            ("oc apply -f deployment.yaml --dry-run=client", "safe"),
            ("oc exec pod/my-pod -- ls", "high"),
            ("oc rsh my-pod", "high"),
            ("oc create configmap cm --from-literal=k=v", "high"),
            ("oc patch pod my-pod -p '{}'", "medium"),
            ("oc scale deployment/app --replicas=3", "high"),
            ("oc create clusterrole admin", "critical"),
            ("oc delete clusterrolebinding admin", "critical"),
            ("oc create build my-build", "low"),
        ],
        ids=[
            "get-safe",
            "describe-safe",
            "logs-safe",
            "delete-pod-high",
            "delete-ns-critical",
            "apply-high",
            "apply-dry-run-safe",
            "exec-high",
            "rsh-high",
            "create-configmap-high",
            "patch-pod-medium",
            "scale-high",
            "create-clusterrole-critical",
            "delete-clusterrolebinding-critical",
            "create-build-low",
        ],
    )
    def test_classify_risk(self, cmd, expected_risk):
        parsed = _parse_oc_command(cmd)
        risk, _reason = _classify_oc_risk(parsed)
        assert risk == expected_risk

    def test_classify_none_parsed(self):
        risk, reason = _classify_oc_risk(None)
        assert risk == "safe"
        assert reason is None

    def test_classify_no_verb(self):
        parsed = _parse_oc_command("oc")
        risk, reason = _classify_oc_risk(parsed)
        assert risk == "safe"

    def test_classify_mutating_unknown_resource(self):
        """Mutating verb with unrecognized resource is medium risk."""
        parsed = _parse_oc_command("oc create customthing my-thing")
        risk, _reason = _classify_oc_risk(parsed)
        assert risk == "medium"


# ═══════════════════════════════════════════════════════════════════════════════
# Manifest inspection: unit tests
# ═══════════════════════════════════════════════════════════════════════════════


_inspect_manifest = _mod._inspect_manifest
_inspect_pipe_source = _mod._inspect_pipe_source


class TestManifestInspection:
    """Unit tests for _inspect_manifest and _inspect_pipe_source."""

    def test_yaml_manifest(self, tmp_path):
        """Parse a simple YAML manifest."""
        manifest = tmp_path / "deploy.yaml"
        manifest.write_text(
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: my-app\n"
            "  namespace: prod\n"
            "spec:\n"
            "  replicas: 3\n"
        )
        result = _inspect_manifest(str(manifest))
        assert len(result) == 1
        assert result[0]["kind"] == "Deployment"
        assert result[0]["name"] == "my-app"
        assert result[0]["namespace"] == "prod"

    def test_multi_document_yaml(self, tmp_path):
        """Parse multi-document YAML with --- separators."""
        manifest = tmp_path / "multi.yaml"
        manifest.write_text(
            "apiVersion: v1\n"
            "kind: Service\n"
            "metadata:\n"
            "  name: my-svc\n"
            "---\n"
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: my-app\n"
        )
        result = _inspect_manifest(str(manifest))
        assert len(result) == 2
        assert result[0]["kind"] == "Service"
        assert result[1]["kind"] == "Deployment"

    def test_security_fields_detected(self, tmp_path):
        """Security-relevant fields are collected."""
        manifest = tmp_path / "sec.yaml"
        manifest.write_text(
            "apiVersion: v1\n"
            "kind: Pod\n"
            "metadata:\n"
            "  name: priv-pod\n"
            "spec:\n"
            "  containers:\n"
            "  - name: main\n"
            "    securityContext:\n"
            "      privileged: true\n"
            "      capabilities:\n"
            "        add: [NET_ADMIN]\n"
        )
        result = _inspect_manifest(str(manifest))
        assert len(result) == 1
        sec = result[0]["security_fields"]
        assert "privileged" in sec
        assert "securityContext" in sec
        assert "capabilities" in sec

    def test_json_manifest(self, tmp_path):
        """Parse a JSON manifest."""
        manifest = tmp_path / "deploy.json"
        manifest.write_text(
            json.dumps(
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "metadata": {"name": "my-app", "namespace": "prod"},
                }
            )
        )
        result = _inspect_manifest(str(manifest))
        assert len(result) == 1
        assert result[0]["kind"] == "Deployment"
        assert result[0]["name"] == "my-app"

    def test_json_list_manifest(self, tmp_path):
        """Parse a JSON List kind manifest."""
        manifest = tmp_path / "list.json"
        manifest.write_text(
            json.dumps(
                {
                    "kind": "List",
                    "items": [
                        {"kind": "Service", "metadata": {"name": "svc1"}},
                        {"kind": "Deployment", "metadata": {"name": "dep1"}},
                    ],
                }
            )
        )
        result = _inspect_manifest(str(manifest))
        assert len(result) == 2
        assert result[0]["kind"] == "Service"
        assert result[1]["kind"] == "Deployment"

    def test_oversized_file(self, tmp_path):
        """Files over 1MB return error info."""
        manifest = tmp_path / "big.yaml"
        manifest.write_text("x" * (1_048_577))
        result = _inspect_manifest(str(manifest))
        assert len(result) == 1
        assert result[0].get("error") == "file too large"

    def test_binary_file(self, tmp_path):
        """Binary files return error info."""
        manifest = tmp_path / "binary.yaml"
        manifest.write_bytes(b"kind: Pod\n\x00\x01\x02binary content")
        result = _inspect_manifest(str(manifest))
        assert len(result) == 1
        assert result[0].get("error") == "binary file"

    def test_missing_file(self, tmp_path):
        """Missing file returns empty list."""
        result = _inspect_manifest(str(tmp_path / "nonexistent.yaml"))
        assert result == []

    def test_inspect_pipe_source_cat(self):
        """Extract filename from 'cat file | ...'."""
        assert _inspect_pipe_source("cat deploy.yaml | oc apply -f -") == "deploy.yaml"

    def test_inspect_pipe_source_redirect(self):
        """Extract filename from '< file ...'."""
        assert _inspect_pipe_source("oc apply -f - < deploy.yaml") == "deploy.yaml"

    def test_inspect_pipe_source_none(self):
        """No pipe source returns None."""
        assert _inspect_pipe_source("oc apply -f deploy.yaml") is None


# ═══════════════════════════════════════════════════════════════════════════════
# oc/kubectl introspection: black-box integration tests
# ═══════════════════════════════════════════════════════════════════════════════


def _run_oc_guard(command):
    """Run the guard for oc commands without extra command rules (tests introspection only)."""
    env = os.environ.copy()
    env.pop("COMMAND_GUARD_EXTRA_RULES", None)
    env.pop("URL_GUARD_EXTRA_RULES", None)
    return run_guard("Bash", {"command": command}, env=env)


class TestOcIntrospection:
    """Black-box subprocess tests: run guard with oc commands, check behavior."""

    def test_oc_get_safe(self):
        """oc get (read-only) passes through."""
        result = _run_oc_guard("oc get pods")
        assert result.returncode == 0

    def test_oc_describe_safe(self):
        """oc describe (read-only) passes through."""
        result = _run_oc_guard("oc describe pod my-pod")
        assert result.returncode == 0

    def test_oc_delete_asks(self):
        """oc delete (mutating, high risk) triggers ask."""
        result = _run_oc_guard("oc delete pod my-pod")
        assert_ask_decision(result, "high-risk")

    def test_oc_delete_namespace_asks(self):
        """oc delete namespace (critical risk) triggers ask."""
        result = _run_oc_guard("oc delete namespace prod")
        assert_ask_decision(result, "critical-risk")

    def test_oc_dry_run_safe(self):
        """oc with --dry-run passes through."""
        result = _run_oc_guard("oc delete pod my-pod --dry-run=client")
        assert result.returncode == 0

    def test_oc_exec_asks(self):
        """oc exec (high risk) triggers ask."""
        result = _run_oc_guard("oc exec pod/my-pod -- ls")
        assert_ask_decision(result, "high-risk")

    def test_oc_apply_with_manifest(self):
        """oc apply -f with manifest file inspects the manifest."""
        manifest = Path(SCRIPT).parent / "_test_deploy.yaml"
        try:
            manifest.write_text(
                "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: my-app\n"
            )
            result = _run_oc_guard(f"oc apply -f {manifest}")
            assert_ask_decision(result)
        finally:
            manifest.unlink(missing_ok=True)

    def test_oc_apply_with_security_fields(self):
        """oc apply with security fields in manifest triggers ask."""
        manifest = Path(SCRIPT).parent / "_test_priv.yaml"
        try:
            manifest.write_text(
                "apiVersion: v1\n"
                "kind: Pod\n"
                "metadata:\n"
                "  name: priv-pod\n"
                "spec:\n"
                "  containers:\n"
                "  - name: main\n"
                "    securityContext:\n"
                "      privileged: true\n"
            )
            result = _run_oc_guard(f"oc apply -f {manifest}")
            assert_ask_decision(result)
        finally:
            manifest.unlink(missing_ok=True)

    def test_oc_create_low_risk_passes(self):
        """oc create for low-risk resources passes through."""
        result = _run_oc_guard("oc create build my-build")
        # low risk passes through
        assert result.returncode == 0

    def test_pipe_source_inspection(self):
        """cat file | oc apply -f - passes through (introspection checks subcmd start)."""
        manifest = Path(SCRIPT).parent / "_test_svc.yaml"
        try:
            manifest.write_text("apiVersion: v1\nkind: Service\nmetadata:\n  name: my-svc\n")
            result = _run_oc_guard(f"cat {manifest} | oc apply -f -")
            # cat-file rule has pipe exception for |, so cat passes;
            # oc introspection only runs when subcmd starts with oc/kubectl,
            # but the subcmd here starts with cat, so introspection doesn't trigger
            assert result.returncode == 0
        finally:
            manifest.unlink(missing_ok=True)

    def test_kubectl_also_introspected(self):
        """kubectl commands are also introspected."""
        result = _run_oc_guard("kubectl delete pod my-pod")
        assert_ask_decision(result, "high-risk")


# ═══════════════════════════════════════════════════════════════════════════════
# BUG-006: NamedTuple rules with default action="block"
# ═══════════════════════════════════════════════════════════════════════════════


class TestNamedTupleRules:
    """Verify NamedTuple rule types have correct defaults and attributes."""

    def test_command_rule_default_action(self):
        """CommandRule defaults to action='block' when not specified."""
        assert (
            _mod.CommandRule(
                name="test", pattern=re.compile("test"), exception=None, guidance="test"
            ).action
            == "block"
        )

    def test_url_rule_default_action(self):
        """URLRule defaults to action='block' when not specified."""
        assert (
            _mod.URLRule(name="test", pattern=re.compile("test"), guidance="test").action == "block"
        )

    def test_command_rule_with_explicit_action(self):
        """CommandRule accepts explicit action value."""
        rule = _mod.CommandRule(
            name="test", pattern=re.compile("test"), exception=None, guidance="test", action="ask"
        )
        assert rule.action == "ask"

    def test_url_rule_with_explicit_action(self):
        """URLRule accepts explicit action value."""
        rule = _mod.URLRule(
            name="test", pattern=re.compile("test"), guidance="test", action="allow"
        )
        assert rule.action == "allow"

    def test_git_rule_attributes(self):
        """GitRule has name, check_fn, message attributes."""
        check_fn = lambda cmd: "test" in cmd  # noqa: E731
        rule = _mod.GitRule(name="test-rule", check_fn=check_fn, message="test message")
        assert rule.name == "test-rule"
        assert rule.check_fn("test") is True
        assert rule.message == "test message"

    def test_command_rule_unpacking_backward_compat(self):
        """NamedTuple unpacking still works for backward compatibility."""
        rule = _mod.CommandRule(
            name="test", pattern=re.compile("test"), exception=None, guidance="msg"
        )
        # Tuple unpacking should work
        name, pattern, exception, guidance, action = rule
        assert name == "test"
        assert guidance == "msg"
        assert action == "block"


# ═══════════════════════════════════════════════════════════════════════════════
# BUG-007 Issue F: _hook_output helper
# ═══════════════════════════════════════════════════════════════════════════════


class TestHookOutputHelper:
    """Verify _hook_output produces correct JSON structure."""

    def test_hook_output_allow(self):
        """_hook_output('allow', 'reason') produces correct JSON."""
        output = _mod._hook_output("allow", "test reason")
        parsed = json.loads(output)
        assert "hookSpecificOutput" in parsed
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert parsed["hookSpecificOutput"]["permissionDecisionReason"] == "test reason"
        assert parsed["hookSpecificOutput"]["hookEventName"] == "PreToolUse"

    def test_hook_output_ask(self):
        """_hook_output('ask', 'reason') produces correct JSON."""
        output = _mod._hook_output("ask", "confirm this action")
        parsed = json.loads(output)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "ask"
        assert parsed["hookSpecificOutput"]["permissionDecisionReason"] == "confirm this action"

    def test_hook_output_with_special_characters(self):
        """_hook_output correctly escapes special characters in reason."""
        reason = 'Test "quoted" reason with\nnewline'
        output = _mod._hook_output("allow", reason)
        parsed = json.loads(output)
        assert parsed["hookSpecificOutput"]["permissionDecisionReason"] == reason


# ═══════════════════════════════════════════════════════════════════════════════
# Feature: cd && git compound command blocking
# ═══════════════════════════════════════════════════════════════════════════════


class TestCdGitCompound:
    """Block compound commands that cd into a directory then run git."""

    def test_cd_and_git_push_blocked(self):
        """cd /path && git push is blocked."""
        result = run_bash("cd /some/repo && git push origin HEAD:main")
        assert_guard(result, 2, "cd-git-compound", "cd-and-push")

    def test_cd_and_git_commit_blocked(self):
        """cd /path && git commit is blocked."""
        result = run_bash("cd /some/repo && git commit -m 'test'")
        assert_guard(result, 2, "cd-git-compound", "cd-and-commit")

    def test_cd_semicolon_git_blocked(self):
        """cd /path ; git push is blocked (semicolon separator)."""
        result = run_bash("cd /some/repo ; git push origin main")
        assert_guard(result, 2, "cd-git-compound", "cd-semicolon-git")

    def test_cd_and_non_git_allowed(self):
        """cd /path && make all is allowed (no git involved)."""
        result = run_bash("cd /some/repo && make all")
        assert result.returncode == 0, f"Expected pass, stderr: {result.stderr}"

    def test_git_without_cd_allowed(self):
        """Plain git -C push is allowed (no cd prefix, cd-git-compound doesn't fire)."""
        # Use CWD (which is trusted) to avoid git-untrusted-dir blocking
        cwd = os.getcwd()
        result = run_guard(
            "Bash",
            {"command": f"git -C {cwd} push origin HEAD:main"},
            env={**os.environ, "DEV_GUARD_CONFIG": "/nonexistent"},
        )
        assert result.returncode == 0, f"Expected pass, stderr: {result.stderr}"

    def test_cd_and_intervening_cmd_then_git_blocked(self):
        """cd /path && ls && git push is blocked (cd still in effect)."""
        result = run_bash("cd /some/repo && ls && git push origin main")
        assert_guard(result, 2, "cd-git-compound", "cd-ls-git")

    def test_guidance_includes_git_c_suggestion(self):
        """Guidance message includes git -C alternative."""
        result = run_bash("cd /my/project && git push origin main")
        output = result.stderr + result.stdout
        assert "git -C" in output, f"Expected 'git -C' in output: {output}"
        assert "/my/project" in output, f"Expected path in output: {output}"

    def test_cd_quoted_path_and_git_blocked(self):
        """cd with quoted path followed by git is blocked."""
        result = run_bash('cd "/path with spaces" && git status')
        assert_guard(result, 2, "cd-git-compound", "cd-quoted-git")


# ═══════════════════════════════════════════════════════════════════════════════
# Feature: git trusted directories
# ═══════════════════════════════════════════════════════════════════════════════


def _run_with_trusted_dirs(command, dirs_file):
    """Run the guard with a custom trusted dirs file (GIT_TRUSTED_DIRS).

    Clears the unified config so only the env var file is used.
    """
    env = os.environ.copy()
    env["GIT_TRUSTED_DIRS"] = str(dirs_file)
    # Override unified config to prevent ~/.claude/dev-guard.json from interfering
    env["DEV_GUARD_CONFIG"] = str(dirs_file.parent / "nonexistent-config.json")
    return run_guard("Bash", {"command": command}, env=env)


class TestGitTrustedDirs:
    """Block git commands operating outside configured trusted directories."""

    def test_git_c_trusted_dir_allowed(self, tmp_path):
        """git -C pointing to a trusted directory is allowed."""
        # Use CWD to avoid /tmp/ triggering the tmp-path rule on Linux CI
        cwd = os.getcwd()
        dirs_file = tmp_path / "trusted.json"
        dirs_file.write_text(json.dumps([cwd]))
        result = _run_with_trusted_dirs(f"git -C {cwd}/subdir push origin main", dirs_file)
        assert result.returncode == 0, f"Expected pass, stderr: {result.stderr}"

    def test_git_c_untrusted_dir_blocked(self, tmp_path):
        """git -C pointing outside trusted directories is blocked."""
        cwd = os.getcwd()
        dirs_file = tmp_path / "trusted.json"
        dirs_file.write_text(json.dumps([cwd]))
        result = _run_with_trusted_dirs("git -C /nonexistent/untrusted push origin main", dirs_file)
        assert_guard(result, 2, "git-untrusted-dir", "untrusted-git-c")

    def test_no_trusted_dirs_allows_all(self):
        """Without GIT_TRUSTED_DIRS or unified config, all git commands are allowed."""
        env = {**os.environ, "DEV_GUARD_CONFIG": "/nonexistent"}
        env.pop("GIT_TRUSTED_DIRS", None)
        result = run_guard("Bash", {"command": "git -C /any/path status"}, env=env)
        assert result.returncode == 0, f"Expected pass, stderr: {result.stderr}"

    def test_git_version_always_allowed(self, tmp_path):
        """git --version is informational and always allowed."""
        dirs_file = tmp_path / "trusted.json"
        dirs_file.write_text(json.dumps(["/nonexistent"]))
        result = _run_with_trusted_dirs("git --version", dirs_file)
        assert result.returncode == 0, f"Expected pass, stderr: {result.stderr}"

    def test_git_help_always_allowed(self, tmp_path):
        """git help is informational and always allowed."""
        dirs_file = tmp_path / "trusted.json"
        dirs_file.write_text(json.dumps(["/nonexistent"]))
        result = _run_with_trusted_dirs("git help push", dirs_file)
        assert result.returncode == 0, f"Expected pass, stderr: {result.stderr}"

    def test_git_config_global_always_allowed(self, tmp_path):
        """git config --global is allowed (operates on global config, not repo)."""
        dirs_file = tmp_path / "trusted.json"
        dirs_file.write_text(json.dumps(["/nonexistent"]))
        result = _run_with_trusted_dirs("git config --global user.name", dirs_file)
        assert result.returncode == 0, f"Expected pass, stderr: {result.stderr}"

    def test_git_cmd_with_help_flag_allowed(self, tmp_path):
        """git push --help is informational and always allowed."""
        dirs_file = tmp_path / "trusted.json"
        dirs_file.write_text(json.dumps(["/nonexistent"]))
        result = _run_with_trusted_dirs("git push --help", dirs_file)
        assert result.returncode == 0, f"Expected pass, stderr: {result.stderr}"

    def test_multiple_trusted_dirs(self, tmp_path):
        """Multiple trusted directories — command in any one is allowed."""
        # Use CWD to avoid /tmp/ triggering the tmp-path rule on Linux CI
        cwd = os.getcwd()
        dirs_file = tmp_path / "trusted.json"
        dirs_file.write_text(json.dumps(["/nonexistent/dir1", cwd]))
        # In second trusted dir — allowed
        result = _run_with_trusted_dirs(f"git -C {cwd}/repo status", dirs_file)
        assert result.returncode == 0, f"Expected pass, stderr: {result.stderr}"

    def test_blocked_message_shows_trusted_dirs(self, tmp_path):
        """Block message shows the configured trusted directories."""
        cwd = os.getcwd()
        dirs_file = tmp_path / "trusted.json"
        dirs_file.write_text(json.dumps([cwd]))
        result = _run_with_trusted_dirs("git -C /nonexistent/evil push origin main", dirs_file)
        output = result.stderr + result.stdout
        assert cwd in output, f"Expected trusted dir in output: {output}"

    def test_tilde_expansion(self, tmp_path):
        """Paths with ~ are expanded correctly."""
        dirs_file = tmp_path / "trusted.json"
        dirs_file.write_text(json.dumps(["~"]))
        # CWD is under home dir — should be allowed
        result = _run_with_trusted_dirs("git status", dirs_file)
        assert result.returncode == 0, f"Expected pass, stderr: {result.stderr}"

    def test_git_c_quoted_path(self, tmp_path):
        """git -C with a quoted path is handled correctly."""
        # Use CWD to avoid /tmp/ triggering the tmp-path rule on Linux CI
        cwd = os.getcwd()
        dirs_file = tmp_path / "trusted.json"
        dirs_file.write_text(json.dumps([cwd]))
        result = _run_with_trusted_dirs(f'git -C "{cwd}/my repo" status', dirs_file)
        assert result.returncode == 0, f"Expected pass, stderr: {result.stderr}"

    def test_malformed_json_fails_open(self, tmp_path):
        """Malformed JSON trusted dirs file fails open (no blocking)."""
        dirs_file = tmp_path / "bad.json"
        dirs_file.write_text("not json")
        result = _run_with_trusted_dirs("git -C /any/path status", dirs_file)
        assert result.returncode == 0, f"Expected pass (fail-open), stderr: {result.stderr}"

    def test_empty_array_fails_open(self, tmp_path):
        """Empty array in trusted dirs file fails open (no blocking)."""
        dirs_file = tmp_path / "empty.json"
        dirs_file.write_text("[]")
        result = _run_with_trusted_dirs("git -C /any/path status", dirs_file)
        assert result.returncode == 0, f"Expected pass (fail-open), stderr: {result.stderr}"


class TestGitTrustedDirsValidation:
    """Validation of GIT_TRUSTED_DIRS config file."""

    def test_validate_valid_dirs(self, tmp_path):
        """Valid trusted dirs file passes validation."""
        trusted = tmp_path / "projects"
        trusted.mkdir()
        dirs_file = tmp_path / "trusted.json"
        dirs_file.write_text(json.dumps([str(trusted)]))
        env = os.environ.copy()
        env["GIT_TRUSTED_DIRS"] = str(dirs_file)
        result = subprocess.run(
            ["uv", "run", SCRIPT, "--validate"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "Git trusted dirs" in result.stdout

    def test_validate_missing_file(self, tmp_path):
        """Missing trusted dirs file fails validation."""
        env = os.environ.copy()
        env["GIT_TRUSTED_DIRS"] = str(tmp_path / "nonexistent.json")
        result = subprocess.run(
            ["uv", "run", SCRIPT, "--validate"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 2
        assert "file not found" in result.stderr

    def test_validate_nonexistent_dir(self, tmp_path):
        """Trusted dirs file with nonexistent directory fails validation."""
        dirs_file = tmp_path / "trusted.json"
        dirs_file.write_text(json.dumps(["/this/dir/does/not/exist"]))
        env = os.environ.copy()
        env["GIT_TRUSTED_DIRS"] = str(dirs_file)
        result = subprocess.run(
            ["uv", "run", SCRIPT, "--validate"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 2
        assert "does not exist" in result.stderr

    def test_validate_non_string_entry(self, tmp_path):
        """Trusted dirs file with non-string entry fails validation."""
        dirs_file = tmp_path / "trusted.json"
        dirs_file.write_text(json.dumps([123, "/tmp"]))
        env = os.environ.copy()
        env["GIT_TRUSTED_DIRS"] = str(dirs_file)
        result = subprocess.run(
            ["uv", "run", SCRIPT, "--validate"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 2
        assert "expected string" in result.stderr


# ═══════════════════════════════════════════════════════════════════════════════
# Feature: unified dev-guard.json config
# ═══════════════════════════════════════════════════════════════════════════════


def _run_with_unified_config(command, config_file, *, extra_env=None):
    """Run the guard with a unified config file (DEV_GUARD_CONFIG)."""
    env = os.environ.copy()
    env["DEV_GUARD_CONFIG"] = str(config_file)
    # Clear individual env vars to isolate unified config testing
    env.pop("COMMAND_GUARD_EXTRA_RULES", None)
    env.pop("URL_GUARD_EXTRA_RULES", None)
    env.pop("GIT_TRUSTED_DIRS", None)
    if extra_env:
        env.update(extra_env)
    return run_guard("Bash", {"command": command}, env=env)


class TestUnifiedConfig:
    """Unified ~/.claude/dev-guard.json config file."""

    def test_command_rules_from_unified_config(self, tmp_path):
        """command_rules section loads and blocks matching commands."""
        config = {
            "command_rules": [
                {
                    "name": "test-block",
                    "pattern": r"^\s*dangerous-cmd\b",
                    "message": "Use safe-cmd instead.",
                }
            ]
        }
        config_file = tmp_path / "dev-guard.json"
        config_file.write_text(json.dumps(config))
        result = _run_with_unified_config("dangerous-cmd --flag", config_file)
        assert result.returncode == 2
        assert "safe-cmd" in result.stderr

    def test_url_rules_from_unified_config(self, tmp_path):
        """url_rules section loads and blocks matching URLs."""
        config = {
            "url_rules": [
                {
                    "name": "test-url",
                    "pattern": r"internal\.corp\.com",
                    "message": "Use VPN tool instead.",
                }
            ]
        }
        config_file = tmp_path / "dev-guard.json"
        config_file.write_text(json.dumps(config))
        result = _run_with_unified_config("curl https://internal.corp.com/api", config_file)
        assert result.returncode == 2
        assert "VPN tool" in result.stderr

    def test_git_trusted_dirs_from_unified_config(self, tmp_path):
        """git_trusted_dirs section loads and blocks untrusted git paths."""
        cwd = os.getcwd()
        config = {"git_trusted_dirs": [cwd]}
        config_file = tmp_path / "dev-guard.json"
        config_file.write_text(json.dumps(config))
        # Trusted path — allowed
        result = _run_with_unified_config(f"git -C {cwd}/sub status", config_file)
        assert result.returncode == 0, f"Expected pass, stderr: {result.stderr}"
        # Untrusted path — blocked
        result = _run_with_unified_config("git -C /nonexistent/evil status", config_file)
        assert result.returncode == 2
        assert "git-untrusted-dir" in (result.stderr + result.stdout)

    def test_env_var_overrides_stacks(self, tmp_path):
        """Env var rules stack on top of unified config (additive)."""
        # Unified config blocks dangerous-cmd
        config = {
            "command_rules": [
                {
                    "name": "unified-block",
                    "pattern": r"^\s*unified-cmd\b",
                    "message": "Blocked by unified config.",
                }
            ]
        }
        config_file = tmp_path / "dev-guard.json"
        config_file.write_text(json.dumps(config))
        # Env var adds another rule blocking extra-cmd
        extra_rules = [
            {
                "name": "env-block",
                "pattern": r"^\s*env-cmd\b",
                "message": "Blocked by env var.",
            }
        ]
        extra_file = tmp_path / "extra-rules.json"
        extra_file.write_text(json.dumps(extra_rules))
        # Both should be blocked
        result = _run_with_unified_config(
            "unified-cmd", config_file, extra_env={"COMMAND_GUARD_EXTRA_RULES": str(extra_file)}
        )
        assert result.returncode == 2
        assert "unified config" in result.stderr
        result = _run_with_unified_config(
            "env-cmd", config_file, extra_env={"COMMAND_GUARD_EXTRA_RULES": str(extra_file)}
        )
        assert result.returncode == 2
        assert "env var" in result.stderr

    def test_missing_unified_config_is_fine(self, tmp_path):
        """Missing unified config file is silently ignored."""
        config_file = tmp_path / "nonexistent.json"
        result = _run_with_unified_config("git status", config_file)
        assert result.returncode == 0, f"Expected pass, stderr: {result.stderr}"

    def test_malformed_unified_config_fails_open(self, tmp_path):
        """Malformed unified config file is silently ignored."""
        config_file = tmp_path / "bad.json"
        config_file.write_text("not json {{{")
        result = _run_with_unified_config("git status", config_file)
        assert result.returncode == 0, f"Expected pass (fail-open), stderr: {result.stderr}"

    def test_unknown_keys_ignored_at_runtime(self, tmp_path):
        """Unknown keys in unified config don't break loading."""
        config = {"future_feature": True, "git_trusted_dirs": ["~"]}
        config_file = tmp_path / "dev-guard.json"
        config_file.write_text(json.dumps(config))
        result = _run_with_unified_config("git status", config_file)
        assert result.returncode == 0, f"Expected pass, stderr: {result.stderr}"


class TestUnifiedConfigValidation:
    """Validation of unified dev-guard.json config."""

    def test_validate_valid_unified_config(self, tmp_path):
        """Valid unified config passes validation."""
        cwd = os.getcwd()
        config = {
            "command_rules": [{"name": "test", "pattern": r"^\s*test\b", "message": "Test rule."}],
            "git_trusted_dirs": [cwd],
        }
        config_file = tmp_path / "dev-guard.json"
        config_file.write_text(json.dumps(config))
        env = os.environ.copy()
        env["DEV_GUARD_CONFIG"] = str(config_file)
        result = subprocess.run(
            ["uv", "run", SCRIPT, "--validate"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "Command rules" in result.stdout
        assert "Git trusted dirs" in result.stdout

    def test_validate_unknown_keys_warns(self, tmp_path):
        """Unknown keys in unified config are flagged during validation."""
        config = {"bogus_key": []}
        config_file = tmp_path / "dev-guard.json"
        config_file.write_text(json.dumps(config))
        env = os.environ.copy()
        env["DEV_GUARD_CONFIG"] = str(config_file)
        result = subprocess.run(
            ["uv", "run", SCRIPT, "--validate"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 2
        assert "unknown keys" in result.stderr

    def test_validate_bad_rule_in_unified(self, tmp_path):
        """Invalid rule entry in unified config is caught by validation."""
        config = {"command_rules": [{"name": "test"}]}  # missing pattern, message
        config_file = tmp_path / "dev-guard.json"
        config_file.write_text(json.dumps(config))
        env = os.environ.copy()
        env["DEV_GUARD_CONFIG"] = str(config_file)
        result = subprocess.run(
            ["uv", "run", SCRIPT, "--validate"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 2


# ═══════════════════════════════════════════════════════════════════════════════
# RTK Integration: fixtures
# ═══════════════════════════════════════════════════════════════════════════════

# Dummy rtk script that mimics rtk behavior:
# - `rtk rewrite <cmd>`: echoes "rtk <cmd>" and exits 0 (supported)
# - `rtk rewrite` with unsupported cmd: exits 1
# - `rtk config`: prints "Config: $RTK_CONFIG_PATH" (env var must be set)
# - `rtk config --create`: creates a default config.toml at $RTK_CONFIG_PATH
_RTK_STUB_SCRIPT = """\
#!/bin/sh
if [ "$1" = "rewrite" ]; then
    shift
    CMD="$*"
    case "$CMD" in
        cargo\\ *) echo "rtk cargo ${CMD#cargo }" ;;
        git\\ *) echo "rtk git ${CMD#git }" ;;
        docker\\ *) echo "rtk docker ${CMD#docker }" ;;
        kubectl\\ *) echo "rtk kubectl ${CMD#kubectl }" ;;
        go\\ *) echo "rtk go ${CMD#go }" ;;
        pytest*) echo "rtk pytest ${CMD#pytest}" ;;
        ls*) echo "rtk ls ${CMD#ls}" ;;
        curl\\ *) echo "rtk curl ${CMD#curl }" ;;
        *) exit 1 ;;
    esac
    exit 0
fi
if [ "$1" = "config" ]; then
    if [ -z "$RTK_CONFIG_PATH" ]; then
        echo "Config: unknown"
        exit 0
    fi
    if [ "$2" = "--create" ]; then
        mkdir -p "$(dirname "$RTK_CONFIG_PATH")"
        cat > "$RTK_CONFIG_PATH" << 'TOML'
[telemetry]
enabled = true

[tee]
mode = "failures"
TOML
        exit 0
    fi
    echo "Config: $RTK_CONFIG_PATH"
    exit 0
fi
exit 1
"""


@pytest.fixture
def rtk_env(tmp_path):
    """Create env with dummy rtk binary on PATH."""
    rtk_script = tmp_path / "rtk"
    rtk_script.write_text(_RTK_STUB_SCRIPT)
    rtk_script.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    env.pop("RTK_DISABLED", None)
    return env


@pytest.fixture
def rtk_disabled_env():
    """Env with RTK explicitly disabled."""
    return {**os.environ, "RTK_DISABLED": "1"}


@pytest.fixture
def rtk_db_env(tmp_path):
    """Env with dummy rtk and isolated DB."""
    rtk_script = tmp_path / "rtk"
    rtk_script.write_text(_RTK_STUB_SCRIPT)
    rtk_script.chmod(0o755)
    db_path = tmp_path / "test-guard.db"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "GUARD_DB_PATH": str(db_path),
    }
    env.pop("RTK_DISABLED", None)
    return env, db_path


# ═══════════════════════════════════════════════════════════════════════════════
# RTK Integration: rewrite behaviour (PreToolUse Bash)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRTKIntegration:
    """RTK rewrite functionality via `rtk rewrite` subprocess delegation."""

    def _assert_rtk_rewrite(self, result):
        """Assert the guard emitted an allow + updatedInput for RTK."""
        assert result.returncode == 0, (
            f"Expected exit 0 for RTK rewrite, got {result.returncode}. "
            f"stderr: {result.stderr.strip()!r}"
        )
        output = json.loads(result.stdout)
        hook = output["hookSpecificOutput"]
        assert hook["permissionDecision"] == "allow", f"Expected allow, got: {hook}"
        assert "updatedInput" in hook, f"Expected updatedInput in hook output: {hook}"
        return hook["updatedInput"]["command"]

    # ── Commands that pass deny rules → RTK rewrites them ────────────

    def test_rtk_rewrite_git_log(self, rtk_env):
        """git log passes deny rules, then RTK rewrites it."""
        result = run_guard("Bash", {"command": "git log --oneline"}, env=rtk_env)
        cmd = self._assert_rtk_rewrite(result)
        assert "rtk" in cmd

    def test_rtk_rewrite_git_status(self, rtk_env):
        """git status passes deny rules, then RTK rewrites it."""
        result = run_guard("Bash", {"command": "git status"}, env=rtk_env)
        cmd = self._assert_rtk_rewrite(result)
        assert "rtk" in cmd

    def test_rtk_rewrite_docker_ps(self, rtk_env):
        """docker ps passes deny rules, then RTK rewrites it."""
        result = run_guard("Bash", {"command": "docker ps"}, env=rtk_env)
        cmd = self._assert_rtk_rewrite(result)
        assert "rtk" in cmd

    # ── Commands blocked by deny rules → RTK never fires ─────────────

    def test_rtk_cargo_test_blocked_by_deny(self, rtk_env):
        """cargo test is blocked by deny rule; RTK never fires."""
        result = run_guard("Bash", {"command": "cargo test"}, env=rtk_env)
        assert result.returncode == 2

    def test_rtk_bare_pytest_blocked_by_deny(self, rtk_env):
        """bare pytest is blocked by deny rule; RTK never fires."""
        result = run_guard("Bash", {"command": "pytest tests/"}, env=rtk_env)
        assert result.returncode == 2

    # ── Commands with no RTK equivalent → pass through ───────────────

    def test_rtk_no_rewrite_date(self, rtk_env):
        """date has no RTK equivalent; passes through normally."""
        result = run_guard("Bash", {"command": "date"}, env=rtk_env)
        assert result.returncode == 0
        if result.stdout.strip():
            output = json.loads(result.stdout)
            hook = output.get("hookSpecificOutput", {})
            assert "updatedInput" not in hook

    def test_rtk_no_rewrite_make(self, rtk_env):
        """make test has no RTK equivalent."""
        result = run_guard("Bash", {"command": "make test"}, env=rtk_env)
        assert result.returncode == 0
        if result.stdout.strip():
            output = json.loads(result.stdout)
            hook = output.get("hookSpecificOutput", {})
            assert "updatedInput" not in hook

    # ── RTK_DISABLED escape hatch ────────────────────────────────────

    def test_rtk_disabled_no_rewrite(self, rtk_disabled_env):
        """With RTK_DISABLED=1, git log passes through without rewrite."""
        result = run_guard("Bash", {"command": "git log --oneline"}, env=rtk_disabled_env)
        assert result.returncode == 0
        if result.stdout.strip():
            output = json.loads(result.stdout)
            hook = output.get("hookSpecificOutput", {})
            assert "updatedInput" not in hook

    # ── Safety: deny rules still enforced ────────────────────────────

    def test_rtk_git_push_force_blocked(self, rtk_env):
        """git push --force is blocked by git safety."""
        result = run_guard("Bash", {"command": "git push --force"}, env=rtk_env)
        assert result.returncode == 2

    def test_rtk_compound_command_not_rewritten(self, rtk_env):
        """Compound commands skip RTK (only single commands eligible)."""
        result = run_guard("Bash", {"command": "git log --oneline && git status"}, env=rtk_env)
        assert result.returncode == 0
        if result.stdout.strip():
            output = json.loads(result.stdout)
            hook = output.get("hookSpecificOutput", {})
            assert "updatedInput" not in hook

    def test_rtk_piped_command_not_rewritten(self, rtk_env):
        """Piped commands skip RTK."""
        result = run_guard("Bash", {"command": "git log | head -5"}, env=rtk_env)
        assert result.returncode == 0
        if result.stdout.strip():
            output = json.loads(result.stdout)
            hook = output.get("hookSpecificOutput", {})
            assert "updatedInput" not in hook

    def test_rtk_subshell_command_not_rewritten(self, rtk_env):
        """Subshell commands skip RTK."""
        result = run_guard("Bash", {"command": "git log $(date)"}, env=rtk_env)
        assert result.returncode == 0
        if result.stdout.strip():
            output = json.loads(result.stdout)
            hook = output.get("hookSpecificOutput", {})
            assert "updatedInput" not in hook


# ═══════════════════════════════════════════════════════════════════════════════
# RTK Integration: rtk_events DB logging
# ═══════════════════════════════════════════════════════════════════════════════


class TestRTKEventLogging:
    """RTK event logging into the rtk_events SQLite table."""

    def test_rtk_compressed_event_logged(self, rtk_db_env):
        """RTK rewrite creates an rtk_events row with event_type='compressed'."""
        env, db_path = rtk_db_env
        result = run_guard("Bash", {"command": "git log --oneline"}, env=env)
        assert result.returncode == 0, f"Expected exit 0, stderr: {result.stderr!r}"
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT event_type, command, rtk_command FROM rtk_events").fetchall()
        conn.close()
        assert len(rows) == 1, f"Expected 1 rtk_events row, got {len(rows)}: {rows}"
        assert rows[0][0] == "compressed"
        assert "git log" in (rows[0][1] or "")
        assert "rtk" in (rows[0][2] or "")

    def test_rtk_full_read_event_logged(self, rtk_db_env):
        """PostToolUse Read of tee file creates event_type='full_read'."""
        env, db_path = rtk_db_env
        if sys.platform == "darwin":
            tee_dir = Path.home() / "Library" / "Application Support" / "rtk" / "tee"
        else:
            xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
            tee_dir = Path(xdg) / "rtk" / "tee"
        tee_path = str(tee_dir / "somefile.txt")
        run_guard(
            "Read",
            {"file_path": tee_path},
            env=env,
            payload_extra={"hook_event_name": "PostToolUse"},
        )
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT event_type FROM rtk_events").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "full_read"

    def test_rtk_non_tee_read_not_logged(self, rtk_db_env):
        """PostToolUse Read of a normal file does NOT create an rtk_events row."""
        env, db_path = rtk_db_env
        run_guard(
            "Read",
            {"file_path": "/some/normal/file.txt"},
            env=env,
            payload_extra={"hook_event_name": "PostToolUse"},
        )
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            rows = conn.execute("SELECT event_type FROM rtk_events").fetchall()
            conn.close()
            assert len(rows) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# RTK Config: _ensure_rtk_config (SessionStart)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRTKConfig:
    """_ensure_rtk_config patches telemetry and tee mode at session start."""

    @pytest.fixture
    def rtk_config_env(self, tmp_path):
        """Create env with dummy rtk binary and config path."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        rtk_script = bin_dir / "rtk"
        rtk_script.write_text(_RTK_STUB_SCRIPT)
        rtk_script.chmod(0o755)
        config_path = tmp_path / "config" / "rtk" / "config.toml"
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "RTK_CONFIG_PATH": str(config_path),
        }
        env.pop("RTK_DISABLED", None)
        return env, config_path

    def test_patches_telemetry_and_tee(self, rtk_config_env):
        """Config with telemetry=true and tee mode=failures is patched."""
        env, config_path = rtk_config_env
        config_path.parent.mkdir(parents=True)
        config_path.write_text('[telemetry]\nenabled = true\n\n[tee]\nmode = "failures"\n')
        result = subprocess.run(
            ["uv", "run", SCRIPT, "--validate"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "telemetry disabled" in result.stdout
        assert "tee mode" in result.stdout
        patched = config_path.read_text()
        assert "enabled = false" in patched
        assert 'mode = "always"' in patched

    def test_already_configured_reports_ok(self, rtk_config_env):
        """Config already correct → reports 'rtk config: OK'."""
        env, config_path = rtk_config_env
        config_path.parent.mkdir(parents=True)
        config_path.write_text('[telemetry]\nenabled = false\n\n[tee]\nmode = "always"\n')
        result = subprocess.run(
            ["uv", "run", SCRIPT, "--validate"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "rtk config: OK" in result.stdout

    def test_creates_config_if_absent(self, rtk_config_env):
        """When config doesn't exist, rtk config --create is called first."""
        env, config_path = rtk_config_env
        assert not config_path.exists()
        result = subprocess.run(
            ["uv", "run", SCRIPT, "--validate"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert config_path.exists(), "rtk config --create should have created the file"
        patched = config_path.read_text()
        assert "enabled = false" in patched
        assert 'mode = "always"' in patched

    def test_no_rtk_binary_skips_silently(self, tmp_path):
        """Without rtk on PATH, no config message is emitted."""
        # Create a minimal PATH with only a uv symlink, no rtk
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        uv_real = subprocess.run(["which", "uv"], capture_output=True, text=True).stdout.strip()
        (bin_dir / "uv").symlink_to(uv_real)
        env = {**os.environ, "PATH": str(bin_dir)}
        env.pop("RTK_DISABLED", None)
        result = subprocess.run(
            ["uv", "run", SCRIPT, "--validate"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "rtk config" not in result.stdout

    def test_preserves_other_sections(self, rtk_config_env):
        """Patching only touches telemetry and tee; other sections are preserved."""
        env, config_path = rtk_config_env
        config_path.parent.mkdir(parents=True)
        original = (
            "[tracking]\nenabled = true\nhistory_days = 90\n\n"
            "[telemetry]\nenabled = true\n\n"
            '[tee]\nmode = "failures"\nmax_files = 20\n\n'
            "[hooks]\nexclude_commands = []\n"
        )
        config_path.write_text(original)
        result = subprocess.run(
            ["uv", "run", SCRIPT, "--validate"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        patched = config_path.read_text()
        # Telemetry and tee patched
        assert "enabled = false" in patched
        assert 'mode = "always"' in patched
        # [tracking] section NOT touched (enabled still true there)
        tracking_section = patched.split("[tracking]")[1].split("\n[")[0]
        assert "enabled = true" in tracking_section
        # Other sections preserved
        assert "history_days = 90" in patched
        assert "max_files = 20" in patched
        assert "exclude_commands = []" in patched


# ═══════════════════════════════════════════════════════════════════════════════
# Session Tracking: SessionStart, SessionEnd, Tool Counter
# ═══════════════════════════════════════════════════════════════════════════════

GUARD_STATS_SCRIPT = os.path.join(os.path.dirname(__file__), os.pardir, "hooks", "guard-stats.py")


@pytest.fixture
def session_db(tmp_path):
    """Isolated DB + env for session tracking tests."""
    db_path = tmp_path / "test-guard.db"
    env = {
        **os.environ,
        "GUARD_DB_PATH": str(db_path),
        "DEV_GUARD_CONFIG": str(tmp_path / "nonexistent.json"),
    }
    env.pop("RTK_DISABLED", None)
    return env, db_path


def _run_session_start(env, session_id="test-session", cwd="/test/project"):
    """Run --validate (SessionStart) with session data on stdin."""
    payload = json.dumps({"session_id": session_id, "cwd": cwd})
    return subprocess.run(
        ["uv", "run", SCRIPT, "--validate"],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
    )


def _run_session_end(env, session_id="test-session"):
    """Run --session-end with session data on stdin."""
    payload = json.dumps({"session_id": session_id})
    return subprocess.run(
        ["uv", "run", SCRIPT, "--session-end"],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
    )


class TestSessionStart:
    """SessionStart hook stores CWD and initializes tool counter."""

    def test_stores_cwd(self, session_db):
        env, db_path = session_db
        _run_session_start(env, session_id="s1", cwd="/my/project")
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT value FROM session_state WHERE key = 'cwd:s1'").fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "/my/project"

    def test_initializes_tool_counter(self, session_db):
        env, db_path = session_db
        _run_session_start(env, session_id="s1")
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT value FROM session_state WHERE key = 'tools:s1'").fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "0"

    def test_updates_last_session_id(self, session_db):
        env, db_path = session_db
        _run_session_start(env, session_id="s1")
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT value FROM session_state WHERE key = 'last_session_id'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "s1"

    def test_tool_counter_not_reset_on_second_start(self, session_db):
        """INSERT OR IGNORE: counter survives plugin reload."""
        env, db_path = session_db
        _run_session_start(env, session_id="s1")
        # Simulate tool calls by manually setting counter
        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE session_state SET value = '42' WHERE key = 'tools:s1'")
        conn.commit()
        conn.close()
        # Second SessionStart (plugin reload) should NOT reset counter
        _run_session_start(env, session_id="s1")
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT value FROM session_state WHERE key = 'tools:s1'").fetchone()
        conn.close()
        assert row[0] == "42"


class TestToolCounter:
    """Tool call counter increments on PreToolUse, not PostToolUse."""

    def test_increments_on_pre_tool_use(self, session_db):
        env, db_path = session_db
        _run_session_start(env, session_id="s1")
        # Run a PreToolUse guard check
        run_guard(
            "Bash",
            {"command": "date"},
            env=env,
            payload_extra={"session_id": "s1"},
        )
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT value FROM session_state WHERE key = 'tools:s1'").fetchone()
        conn.close()
        assert int(row[0]) >= 1

    def test_no_increment_on_post_tool_use(self, session_db):
        env, db_path = session_db
        _run_session_start(env, session_id="s1")
        # Run a PostToolUse event
        run_guard(
            "Read",
            {"file_path": "/some/file.txt"},
            env=env,
            payload_extra={"session_id": "s1", "hook_event_name": "PostToolUse"},
        )
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT value FROM session_state WHERE key = 'tools:s1'").fetchone()
        conn.close()
        assert row[0] == "0"


class TestSessionEnd:
    """SessionEnd computes and stores session summary."""

    def test_stores_summary(self, session_db):
        env, db_path = session_db
        _run_session_start(env, session_id="s1", cwd="/my/project")
        # Simulate some tool calls
        for _ in range(3):
            run_guard(
                "Bash",
                {"command": "date"},
                env=env,
                payload_extra={"session_id": "s1"},
            )
        _run_session_end(env, session_id="s1")
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT value FROM session_state WHERE key = 'summary:s1'").fetchone()
        conn.close()
        assert row is not None
        summary = json.loads(row[0])
        assert summary["tool_calls"] >= 3
        assert summary["cwd"] == "/my/project"
        assert "blocked" in summary
        assert "asked" in summary

    def test_summary_counts_blocked_events(self, session_db):
        env, db_path = session_db
        _run_session_start(env, session_id="s1")
        # Trigger a blocked command (grep is blocked by deny rule)
        run_guard(
            "Bash",
            {"command": "grep foo bar.txt"},
            env=env,
            payload_extra={"session_id": "s1"},
        )
        _run_session_end(env, session_id="s1")
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT value FROM session_state WHERE key = 'summary:s1'").fetchone()
        conn.close()
        summary = json.loads(row[0])
        assert summary["blocked"] >= 1

    def test_no_session_id_is_noop(self, session_db):
        """SessionEnd with no session_id exits cleanly."""
        env, _db_path = session_db
        result = subprocess.run(
            ["uv", "run", SCRIPT, "--session-end"],
            input="{}",
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Guard Stats Script
# ═══════════════════════════════════════════════════════════════════════════════


class TestGuardStats:
    """guard-stats.py query and formatting tests."""

    def test_empty_db(self, session_db):
        """Stats on an empty DB produces header but no crash."""
        env, db_path = session_db
        # Create the DB with tables by running a guard check
        run_guard(
            "Bash",
            {"command": "date"},
            env=env,
            payload_extra={"session_id": "s1"},
        )
        result = subprocess.run(
            ["uv", "run", GUARD_STATS_SCRIPT, "1"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "Dev Guard Stats" in result.stdout

    def test_shows_guard_decisions(self, session_db):
        """Stats shows guard decisions after some blocked commands."""
        env, db_path = session_db
        _run_session_start(env, session_id="s1")
        # Generate some blocks
        for cmd in ["grep foo", "cat file.txt", "grep bar"]:
            run_guard(
                "Bash",
                {"command": cmd},
                env=env,
                payload_extra={"session_id": "s1"},
            )
        result = subprocess.run(
            ["uv", "run", GUARD_STATS_SCRIPT],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "Guard Decisions" in result.stdout
        assert "Blocked" in result.stdout

    def test_shows_session_summaries(self, session_db):
        """Stats shows session summaries after SessionEnd."""
        env, db_path = session_db
        _run_session_start(env, session_id="s1", cwd="/my/project")
        run_guard(
            "Bash",
            {"command": "date"},
            env=env,
            payload_extra={"session_id": "s1"},
        )
        _run_session_end(env, session_id="s1")
        result = subprocess.run(
            ["uv", "run", GUARD_STATS_SCRIPT],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "Session Summaries" in result.stdout
        assert "Guarded tool calls" in result.stdout

    def test_no_db_file(self, tmp_path):
        """Stats with no DB file exits gracefully."""
        env = {
            **os.environ,
            "GUARD_DB_PATH": str(tmp_path / "nonexistent.db"),
        }
        result = subprocess.run(
            ["uv", "run", GUARD_STATS_SCRIPT],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "No guard database" in result.stdout

    def test_negative_days_clamped(self, session_db):
        """Negative days argument is clamped to 1."""
        env, _db_path = session_db
        # Create DB by running a guard check
        run_guard("Bash", {"command": "date"}, env=env, payload_extra={"session_id": "s1"})
        result = subprocess.run(
            ["uv", "run", GUARD_STATS_SCRIPT, "-5"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0
        assert "last 1 days" in result.stdout


# ═══════════════════════════════════════════════════════════════════════════════
# MCP tool auto-approval guard
# ═══════════════════════════════════════════════════════════════════════════════


class TestMCPKeyFunction:
    """Tests for the mcp_key() extraction helper in mcp_constants.py."""

    def test_serena_tool(self):
        from mcp_constants import mcp_key

        assert mcp_key("mcp__serena__find_symbol") == "serena__find_symbol"

    def test_github_mcp_tool(self):
        from mcp_constants import mcp_key

        assert (
            mcp_key("mcp__plugin_github-mcp_github__actions_get")
            == "plugin_github-mcp_github__actions_get"
        )

    def test_claude_mem_tool(self):
        from mcp_constants import mcp_key

        assert (
            mcp_key("mcp__plugin_claude-mem_mcp-search__search")
            == "plugin_claude-mem_mcp-search__search"
        )

    def test_jira_tool(self):
        from mcp_constants import mcp_key

        assert (
            mcp_key("mcp__plugin_hcm-jira-administrator-agent_mcp-atlassian-prod__getJiraIssue")
            == "plugin_hcm-jira-administrator-agent_mcp-atlassian-prod__getJiraIssue"
        )

    def test_short_name_returns_empty(self):
        from mcp_constants import mcp_key

        assert mcp_key("mcp__serena") == ""

    def test_empty_returns_empty(self):
        from mcp_constants import mcp_key

        assert mcp_key("") == ""


class TestMCPReadOnlyFrozenset:
    """Spot-check that key tools are in the frozenset with correct server qualification."""

    def test_serena_read_tool_present(self):
        from mcp_constants import MCP_READ_ONLY

        assert "serena__find_symbol" in MCP_READ_ONLY

    def test_github_read_tool_present(self):
        from mcp_constants import MCP_READ_ONLY

        assert "plugin_github-mcp_github__actions_get" in MCP_READ_ONLY

    def test_jira_read_tool_present(self):
        from mcp_constants import MCP_READ_ONLY

        assert (
            "plugin_hcm-jira-administrator-agent_mcp-atlassian-prod__getJiraIssue" in MCP_READ_ONLY
        )

    def test_serena_write_tool_absent(self):
        from mcp_constants import MCP_READ_ONLY

        assert "serena__write_memory" not in MCP_READ_ONLY

    def test_jira_write_tool_absent(self):
        from mcp_constants import MCP_READ_ONLY

        assert (
            "plugin_hcm-jira-administrator-agent_mcp-atlassian-prod__createJiraIssue"
            not in MCP_READ_ONLY
        )

    def test_bare_name_not_present(self):
        """Bare function names should NOT match — must be server-qualified."""
        from mcp_constants import MCP_READ_ONLY

        assert "find_symbol" not in MCP_READ_ONLY
        assert "actions_get" not in MCP_READ_ONLY

    def test_cross_server_spoofing_blocked(self):
        """An evil server reusing a known function name should NOT match."""
        from mcp_constants import MCP_READ_ONLY, mcp_key

        evil_tool = "mcp__evil-server__find_symbol"
        key = mcp_key(evil_tool)
        assert key == "evil-server__find_symbol"
        assert key not in MCP_READ_ONLY


class TestMCPGuardIntegration:
    """Integration tests: run the guard with MCP tool names and verify output."""

    def test_known_readonly_mcp_tool_approved(self, session_db):
        """A known read-only GitHub MCP tool should get permissionDecision: allow."""
        env, _ = session_db
        result = run_guard(
            "mcp__plugin_github-mcp_github__get_me",
            {},
            env=env,
        )
        assert result.returncode == 0
        assert "permissionDecision" in result.stdout
        assert '"allow"' in result.stdout

    def test_serena_readonly_tool_approved(self, session_db):
        """A known read-only Serena tool should get permissionDecision: allow."""
        env, _ = session_db
        result = run_guard(
            "mcp__serena__find_symbol",
            {"name_path": "Foo"},
            env=env,
        )
        assert result.returncode == 0
        assert '"allow"' in result.stdout

    def test_serena_think_tool_approved(self, session_db):
        """think_about_* tools should get permissionDecision: allow."""
        env, _ = session_db
        result = run_guard(
            "mcp__serena__think_about_task_adherence",
            {"query": "Am I on track?"},
            env=env,
        )
        assert result.returncode == 0
        assert '"allow"' in result.stdout

    def test_unknown_mcp_tool_passthrough(self, session_db):
        """An unknown MCP tool should passthrough (exit 0, no permissionDecision)."""
        env, _ = session_db
        result = run_guard(
            "mcp__serena__write_memory",
            {"name": "test"},
            env=env,
        )
        assert result.returncode == 0
        assert "permissionDecision" not in result.stdout

    def test_evil_server_spoofing_passthrough(self, session_db):
        """A tool from an unknown server using a known name should NOT get allow."""
        env, _ = session_db
        result = run_guard(
            "mcp__evil-server__find_symbol",
            {},
            env=env,
        )
        assert result.returncode == 0
        # Should NOT get permissionDecision: allow (server not recognized)
        assert '"allow"' not in result.stdout
