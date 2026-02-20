"""Tests for tool-selection-guard.py

Black-box tests: each test invokes the guard script via subprocess,
feeding it JSON on stdin and asserting exit code + stderr content.
"""

import importlib.util
import json
import os
import re
import subprocess

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), os.pardir, "hooks", "tool-selection-guard.py")


def run_guard(tool_name: str, tool_input: dict) -> subprocess.CompletedProcess:
    """Invoke the guard script with the given tool_name and tool_input."""
    payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
    return subprocess.run(
        ["uv", "run", SCRIPT],
        input=payload,
        capture_output=True,
        text=True,
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
            ("curl https://api.github.com/repos/org/repo", 2, "gh"),
            ("curl -s https://api.github.com/repos/org/repo/pulls", 2, "gh"),
            ("wget https://api.github.com/user", 2, "gh"),
            # BLOCKED: GitHub auth-gated content
            ("curl https://github.com/org/repo/settings", 2, "gh"),
            ("curl https://github.com/org/repo/pulls", 2, "gh"),
            ("curl https://github.com/org/repo/issues", 2, "gh"),
            ("curl https://github.com/org/repo/actions", 2, "gh"),
            ("curl https://github.com/org/repo/security", 2, "gh"),
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
                "gh",
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
            ("https://api.github.com/repos/org/repo", 2, "gh"),
            ("https://docs.google.com/document/d/abc/edit", 2, "Google"),
            ("https://myorg.atlassian.net/rest/api/3/issue/KEY-1", 2, "Atlassian"),
            ("https://myorg.atlassian.net/wiki/spaces/TEAM/page", 2, "Atlassian"),
            ("https://hooks.slack.com/services/T00/B00/xxx", 2, "Slack"),
            ("https://api.slack.com/api/test", 2, "Slack"),
            ("https://github.com/org/repo/settings", 2, "gh"),
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
    @pytest.mark.parametrize(
        "command, expected_exit, expected_msg",
        [
            ("git status\ncat file.py", 2, "Read tool"),
            ("git status\npwd\ngrep pattern src/", 2, "Grep tool"),
            ("docker run \\\n  -v /app:/app \\\n  image", 0, None),
            ("git status\npwd\nmake build", 0, None),
            ('git log\necho "done"', 2, "directly"),
            ("# comment\ncat file.py", 2, "Read tool"),
        ],
        ids=[
            "cat-on-line-2",
            "grep-on-line-3",
            "continuation-allow",
            "all-safe-allow",
            "echo-on-newline",
            "comment-then-cat",
        ],
    )
    def test_newlines(self, command, expected_exit, expected_msg):
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
# Git safety: ASK rules (exit 1)
# ═══════════════════════════════════════════════════════════════════════════════


class TestGitSafetyAsk:
    @pytest.mark.parametrize(
        "command, expected_exit, expected_msg",
        [
            ("git stash drop", 1, "permanently deletes"),
            ("git checkout -- file.py", 1, "destructive"),
            ("git filter-branch --tree-filter 'rm -f x'", 1, "deprecated"),
            ("git reflog delete HEAD@{2}", 1, "recovery points"),
            ("git reflog expire --expire=now", 1, "recovery points"),
            ("git remote remove upstream", 1, "break workflows"),
            ("git remote rm upstream", 1, "break workflows"),
            ("git config --global user.name foo", 1, "permission"),
            ("git config --global --get user.name", 0, None),
            ("git config --global --list", 0, None),
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
    def test_git_ask(self, command, expected_exit, expected_msg):
        result = run_bash(command)
        assert_guard(result, expected_exit, expected_msg)


# ═══════════════════════════════════════════════════════════════════════════════
# Git safety: commit-to-main (requires being on main branch)
# ═══════════════════════════════════════════════════════════════════════════════


_current_branch = subprocess.run(
    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
    capture_output=True,
    text=True,
).stdout.strip()


class TestGitSafetyCommitToMain:
    @pytest.mark.skipif(
        _current_branch not in ("main", "master"),
        reason=f"Only runs on main/master (currently on {_current_branch!r})",
    )
    def test_commit_to_main(self):
        result = run_bash("git commit -m 'test'")
        assert_guard(result, 2, "FORBIDDEN", "commit-to-main")


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
            # ── ASK: branch-from-local-main (exit 1) ──
            ("git switch -c feat/new main", 1, "stale"),
            ("git checkout -b feat/new main", 1, "stale"),
            ("git switch -c feat/new master", 1, "stale"),
            ("git worktree add ../wt -b feat/new main", 1, "stale"),
            # ── ASK: branch-from-non-upstream (exit 1) ──
            ("git switch -c feat/new feat/other", 1, "stacking"),
            ("git checkout -b feat/new develop", 1, "stacking"),
            ("git worktree add ../wt -b feat/new feat/old", 1, "stacking"),
            ("git switch -c feat/new origin/feat/x", 1, "stacking"),
            # ── ASK: branch-needs-fetch (exit 1) ──
            ("git switch -c feat/new upstream/main", 1, "No git fetch"),
            ("git checkout -b feat/new origin/main", 1, "No git fetch"),
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
                1,
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
# Shell control structure integration tests
# ═══════════════════════════════════════════════════════════════════════════════


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
    """Verify URL guard events are logged to JSONL file."""

    def _run_with_log_dir(self, tool_name, tool_input, tmp_path):
        """Run the guard with a custom log directory."""
        payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
        env = os.environ.copy()
        env["URL_GUARD_LOG_DIR"] = str(tmp_path)
        return subprocess.run(
            ["uv", "run", SCRIPT],
            input=payload,
            capture_output=True,
            text=True,
            env=env,
        )

    def _read_log(self, tmp_path):
        """Read and parse all JSONL entries from the log file."""
        log_file = tmp_path / "url-guard.log"
        if not log_file.exists():
            return []
        lines = log_file.read_text().strip().splitlines()
        return [json.loads(line) for line in lines]

    def test_blocked_url_logged(self, tmp_path):
        self._run_with_log_dir(
            "Bash",
            {"command": "curl https://api.github.com/repos/org/repo"},
            tmp_path,
        )
        entries = self._read_log(tmp_path)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["action"] == "blocked"
        assert entry["rule"] == "github-api"
        assert entry["tool"] == "Bash"
        assert entry["phase"] == "pre"
        assert "api.github.com" in entry["url"]
        assert "timestamp" in entry

    def test_allowed_url_logged(self, tmp_path):
        self._run_with_log_dir(
            "Bash",
            {"command": "curl https://example.com/data"},
            tmp_path,
        )
        entries = self._read_log(tmp_path)
        assert len(entries) == 1
        assert entries[0]["action"] == "allowed"
        assert entries[0]["rule"] is None

    def test_bypassed_url_logged(self, tmp_path):
        self._run_with_log_dir(
            "Bash",
            {"command": "ALLOW_FETCH=1 curl https://api.github.com/repos/org/repo"},
            tmp_path,
        )
        entries = self._read_log(tmp_path)
        assert len(entries) == 1
        assert entries[0]["action"] == "bypassed"

    def test_webfetch_blocked_logged(self, tmp_path):
        self._run_with_log_dir(
            "WebFetch",
            {"url": "https://api.github.com/repos/org/repo", "prompt": "test"},
            tmp_path,
        )
        entries = self._read_log(tmp_path)
        assert len(entries) == 1
        assert entries[0]["action"] == "blocked"
        assert entries[0]["tool"] == "WebFetch"

    def test_webfetch_allowed_logged(self, tmp_path):
        self._run_with_log_dir(
            "WebFetch",
            {"url": "https://example.com", "prompt": "test"},
            tmp_path,
        )
        entries = self._read_log(tmp_path)
        assert len(entries) == 1
        assert entries[0]["action"] == "allowed"
        assert entries[0]["tool"] == "WebFetch"

    def test_non_fetch_command_not_logged(self, tmp_path):
        """Non-curl/wget bash commands should not produce log entries."""
        self._run_with_log_dir("Bash", {"command": "git status"}, tmp_path)
        entries = self._read_log(tmp_path)
        assert len(entries) == 0

    def test_jsonl_format(self, tmp_path):
        """Each line is valid JSON."""
        self._run_with_log_dir(
            "Bash",
            {"command": "curl https://api.github.com/repos/o/r"},
            tmp_path,
        )
        self._run_with_log_dir(
            "WebFetch",
            {"url": "https://example.com", "prompt": "t"},
            tmp_path,
        )
        log_file = tmp_path / "url-guard.log"
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            entry = json.loads(line)
            assert "timestamp" in entry
            assert "url" in entry
            assert "action" in entry


# ═══════════════════════════════════════════════════════════════════════════════
# PostToolUse: response code logging
# ═══════════════════════════════════════════════════════════════════════════════


class TestPostToolUseResponseLogging:
    """PostToolUse hooks log HTTP response codes for fetch commands."""

    def _run_post_hook(self, tool_name, tool_input, tool_response, tmp_path):
        """Simulate a PostToolUse hook invocation."""
        payload = json.dumps(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_response": tool_response,
            }
        )
        env = os.environ.copy()
        env["URL_GUARD_LOG_DIR"] = str(tmp_path)
        return subprocess.run(
            ["uv", "run", SCRIPT],
            input=payload,
            capture_output=True,
            text=True,
            env=env,
        )

    def _read_log(self, tmp_path):
        log_file = tmp_path / "url-guard.log"
        if not log_file.exists():
            return []
        return [json.loads(line) for line in log_file.read_text().strip().splitlines()]

    def test_bash_curl_403_logged(self, tmp_path):
        """HTTP 403 from curl is logged as auth_failed."""
        self._run_post_hook(
            "Bash",
            {"command": "curl https://example.com/api/data"},
            {"stdout": "HTTP/1.1 403 Forbidden\n<html>Access Denied</html>", "stderr": ""},
            tmp_path,
        )
        entries = self._read_log(tmp_path)
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
        entries = self._read_log(tmp_path)
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
        entries = self._read_log(tmp_path)
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
        entries = self._read_log(tmp_path)
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
        entries = self._read_log(tmp_path)
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
        entries = self._read_log(tmp_path)
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
        entries = self._read_log(tmp_path)
        assert len(entries) == 1
        assert entries[0]["auth_failed"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# Custom URL guard rules: URL_GUARD_EXTRA_RULES env var
# ═══════════════════════════════════════════════════════════════════════════════


class TestExtraURLRules:
    """URL_GUARD_EXTRA_RULES env var loads custom rules from a JSON file."""

    def _run_with_extra_rules(self, tool_name, tool_input, rules_file):
        """Run the guard with a custom extra rules file."""
        payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
        env = os.environ.copy()
        env["URL_GUARD_EXTRA_RULES"] = str(rules_file)
        return subprocess.run(
            ["uv", "run", SCRIPT],
            input=payload,
            capture_output=True,
            text=True,
            env=env,
        )

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
        result = self._run_with_extra_rules(
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
        result = self._run_with_extra_rules(
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
        result = self._run_with_extra_rules(
            "WebFetch",
            {"url": "https://internal.example.corp/page", "prompt": "test"},
            rules_file,
        )
        assert result.returncode == 2

    def test_missing_file_does_not_break(self, tmp_path):
        """Non-existent rules file is silently ignored."""
        result = self._run_with_extra_rules(
            "Bash",
            {"command": "curl https://example.com/data"},
            tmp_path / "nonexistent.json",
        )
        assert result.returncode == 0

    def test_invalid_json_does_not_break(self, tmp_path):
        """Malformed JSON rules file is silently ignored."""
        rules_file = tmp_path / "bad-rules.json"
        rules_file.write_text("not valid json {{{")
        result = self._run_with_extra_rules(
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
        result = self._run_with_extra_rules(
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

    def _run_with_extra_rules(self, command, rules_file):
        """Run the guard with a custom extra command rules file."""
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
        env = os.environ.copy()
        env["COMMAND_GUARD_EXTRA_RULES"] = str(rules_file)
        return subprocess.run(
            ["uv", "run", SCRIPT],
            input=payload,
            capture_output=True,
            text=True,
            env=env,
        )

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
        result = self._run_with_extra_rules("oc delete pod my-pod", rules_file)
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
        result = self._run_with_extra_rules("oc get pods", rules_file)
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
        result = self._run_with_extra_rules("oc delete pod my-pod", rules_file)
        assert result.returncode == 2
        # With exception → allowed
        result = self._run_with_extra_rules("oc delete pod my-pod --dry-run", rules_file)
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
        result = self._run_with_extra_rules("git status && gh repo delete my-repo", rules_file)
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
        result = self._run_with_extra_rules("echo data | dangerous --flag", rules_file)
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
        result = self._run_with_extra_rules("KUBECONFIG=x oc delete pod p", rules_file)
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
        result = self._run_with_extra_rules("GUARD_BYPASS=1 oc delete pod my-pod", rules_file)
        assert result.returncode == 0

    def test_missing_file_does_not_break(self, tmp_path):
        """Non-existent rules file is silently ignored."""
        result = self._run_with_extra_rules("oc delete pod my-pod", tmp_path / "nonexistent.json")
        assert result.returncode == 0

    def test_invalid_json_does_not_break(self, tmp_path):
        """Malformed JSON rules file is silently ignored."""
        rules_file = tmp_path / "bad-rules.json"
        rules_file.write_text("not valid json {{{")
        result = self._run_with_extra_rules("oc delete pod my-pod", rules_file)
        assert result.returncode == 0

    def test_invalid_regex_does_not_break(self, tmp_path):
        """Invalid regex in rules file is silently ignored."""
        rules = [{"name": "bad", "pattern": "[invalid(", "message": "msg"}]
        rules_file = tmp_path / "bad-regex.json"
        rules_file.write_text(json.dumps(rules))
        result = self._run_with_extra_rules("some command", rules_file)
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
        result = self._run_with_extra_rules("oc delete pod my-pod", rules_file)
        assert result.returncode == 2
        assert "oc delete" in result.stderr
        # Second rule matches
        result = self._run_with_extra_rules("gh repo delete my-repo", rules_file)
        assert result.returncode == 2
        assert "gh repo delete" in result.stderr
        # Neither rule matches
        result = self._run_with_extra_rules("oc get pods", rules_file)
        assert result.returncode == 0
