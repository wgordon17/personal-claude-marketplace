"""Tests for tool-selection-guard.py

Black-box tests: each test invokes the guard script via subprocess,
feeding it JSON on stdin and asserting exit code + stderr content.
"""

import json
import os
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
# Category A: Native tool redirections (12 rules)
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
            ("git log | grep fix", 0, None),
            ("rg pattern src/", 2, "Grep tool"),
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
        ],
        ids=[
            "cat-file",
            "cat-pipe-allow",
            "head-file",
            "head-pipe-allow",
            "tail-file",
            "tail-pipe-allow",
            "grep-blocked",
            "grep-pipe-allow",
            "rg-blocked",
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
            "ls -la",
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
# Newline-separated commands
# ═══════════════════════════════════════════════════════════════════════════════


class TestNewlineSeparation:
    @pytest.mark.parametrize(
        "command, expected_exit, expected_msg",
        [
            ("git status\ncat file.py", 2, "Read tool"),
            ("git status\nls -la\ngrep pattern src/", 2, "Grep tool"),
            ("docker run \\\n  -v /app:/app \\\n  image", 0, None),
            ("git status\nls -la\nmake build", 0, None),
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
# Non-Bash /tmp/ blocking (all tools)
# ═══════════════════════════════════════════════════════════════════════════════


class TestNonBashTmpBlocking:
    @pytest.mark.parametrize(
        "tool_name, file_path, expected_exit, expected_msg, path_key",
        [
            ("Read", "/tmp/scratch.py", 2, "hack/tmp/", "file_path"),
            ("Write", "/tmp/output.json", 2, "hack/tmp/", "file_path"),
            ("Edit", "/tmp/config.yaml", 2, "hack/tmp/", "file_path"),
            ("Grep", "/tmp/logs/", 2, "hack/tmp/", "path"),
            ("Glob", "/tmp/scratch/", 2, "hack/tmp/", "path"),
            ("Read", "hack/tmp/test.py", 0, None, "file_path"),
            ("Write", "hack/tmp/out.json", 0, None, "file_path"),
            ("Read", "src/main.py", 0, None, "file_path"),
        ],
        ids=[
            "read-tmp",
            "write-tmp",
            "edit-tmp",
            "grep-tmp",
            "glob-tmp",
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
