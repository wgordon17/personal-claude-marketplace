#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# ///
"""Test suite for tool-selection-guard.py

33 rules across 5 categories, plus chain/newline splitting, subshell
extraction, env-var prefix stripping, andon cord, and cross-tool /tmp/ blocking.
"""
import json
import os
import subprocess
import sys

SCRIPT = os.path.join(
    os.path.dirname(__file__), os.pardir, "hooks", "tool-selection-guard.py"
)


def test(name, command, expect_exit, expect_msg=None):
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    result = subprocess.run(
        ["uv", "run", SCRIPT], input=payload, capture_output=True, text=True
    )
    status = "PASS" if result.returncode == expect_exit else "FAIL"
    if expect_msg:
        found = expect_msg in result.stderr or expect_msg in result.stdout
        if not found:
            status = "FAIL"
    icon = "v" if status == "PASS" else "X"
    detail = ""
    if status == "FAIL":
        detail = f" -- got exit={result.returncode}, stderr={result.stderr.strip()!r}"
    print(f"  [{icon}] {name}{detail}")
    return status == "PASS"


def test_non_bash(
    name, tool_name, file_path="/some/file.py", expect_exit=0,
    expect_msg=None, path_key="file_path",
):
    payload = json.dumps({"tool_name": tool_name, "tool_input": {path_key: file_path}})
    result = subprocess.run(
        ["uv", "run", SCRIPT], input=payload, capture_output=True, text=True
    )
    status = "PASS" if result.returncode == expect_exit else "FAIL"
    if expect_msg:
        found = expect_msg in result.stderr or expect_msg in result.stdout
        if not found:
            status = "FAIL"
    icon = "v" if status == "PASS" else "X"
    detail = ""
    if status == "FAIL":
        detail = f" -- got exit={result.returncode}, stderr={result.stderr.strip()!r}"
    print(f"  [{icon}] {name}{detail}")
    return status == "PASS"


passed = failed = 0


def tally(result):
    global passed, failed
    if result:
        passed += 1
    else:
        failed += 1


# ── Category A: Native tool redirections (12 rules) ──

print("Category A: Native tool redirections (12 rules)")
for args in [
    ("cat file -> Read",              "cat file.py",                2, "Read tool"),
    ("cat file|grep -> allow",        "cat file.py | grep pattern", 0),
    ("head file -> Read",             "head -20 file.py",           2, "Read tool"),
    ("head in pipe -> allow",         "git log | head -5",          0),
    ("tail file -> Read",             "tail -50 file.py",           2, "Read tool"),
    ("tail in pipe -> allow",         "ps aux | tail -5",           0),
    ("grep -> Grep tool",             "grep -rn pattern src/",      2, "Grep tool"),
    ("grep in pipe -> allow",         "git log | grep fix",         0),
    ("rg -> Grep tool",               "rg pattern src/",            2, "Grep tool"),
    ("find -name -> Glob",            'find . -name "*.py"',        2, "Glob tool"),
    ("sed -i -> Edit",                "sed -i s/old/new/g file",    2, "Edit tool"),
    ("awk redirect -> Edit",          "awk '{print $1}' f > out",   2, "Edit tool"),
    ("echo > file -> Write",          "echo hello > output.txt",    2, "Write tool"),
    ("echo > /dev/null -> allow",     "echo test > /dev/null",      0),
    ("cat heredoc -> Write",          "cat <<EOF > file.py",        2, "Write tool"),
    ("cat heredoc pipe -> blocked",   "cat <<EOF | grep pattern",   2, "Write tool"),
    ("printf > file -> Write",        "printf '%s' x > out.txt",    2, "Write tool"),
    ("less -> Read",                  "less file.py",               2, "Pagers"),
    ("more -> Read",                  "more file.py",               2, "Pagers"),
    ("nano -> Edit",                  "nano file.py",               2, "Interactive editors"),
    ("vim -> Edit",                   "vim file.py",                2, "Interactive editors"),
    ("vi -> Edit",                    "vi file.py",                 2, "Interactive editors"),
    ("emacs -> Edit",                 "emacs file.py",              2, "Interactive editors"),
]:
    tally(test(*args))

# ── Category B: Python tooling (14 rules) ──

print("\nCategory B: Python tooling (14 rules)")
for args in [
    ("python -> uv run",              "python script.py",           2, "uv run"),
    ("python3 -> uv run",             "python3 -c 'print(1)'",     2, "uv run"),
    ("uv run -> allow",               "uv run script.py",           0),
    ("uv run python -> allow",        "uv run python -c 'x=1'",    0),
    ("pip install -> uv add",         "pip install requests",       2, "uv add"),
    ("pip3 install -> uv add",        "pip3 install flask",         2, "uv add"),
    ("pip freeze -> uv pip",          "pip freeze",                 2, "uv pip"),
    ("pip list -> uv pip",            "pip list",                   2, "uv pip"),
    ("pip show -> uv pip",            "pip show requests",          2, "uv pip"),
    ("pip uninstall -> uv remove",    "pip uninstall requests",     2, "uv remove"),
    ("pytest -> make/uv run",         "pytest tests/",              2, "make py-test"),
    ("uvx pytest -> allow",           "uvx pytest tests/",          0),
    ("uv run pytest -> allow",        "uv run pytest tests/",       0),
    ("black -> ruff",                 "black src/",                 2, "ruff"),
    ("ruff -> make/uv run",           "ruff check .",               2, "make py-lint"),
    ("uvx ruff -> allow",             "uvx ruff check .",           0),
    ("mypy -> pyright",               "mypy src/",                  2, "pyright"),
    ("pyright -> make/uv run",        "pyright src/",               2, "make py-lint"),
    ("uvx pyright -> allow",          "uvx pyright src/",           0),
    ("pre-commit -> prek",            "pre-commit run --all-files", 2, "prek"),
    ("uvx pre-commit -> prek",        "uvx pre-commit run",         2, "prek"),
    ("uv run pre-commit -> prek",     "uv run pre-commit run",      2, "prek"),
    ("prek direct -> make",           "prek run --all-files",       2, "make"),
    ("uvx prek -> make",              "uvx prek run --all-files",   2, "make"),
    ("make prek -> allow",            "make prek",                  0),
    ("ipython -> uv run",             "ipython",                    2, "uv run ipython"),
    ("uv run ipython -> allow",       "uv run ipython",             0),
    ("tox -> uvx",                    "tox -e py312",               2, "uvx tox"),
    ("uvx tox -> allow",              "uvx tox -e py312",           0),
    ("isort -> ruff",                 "isort src/",                 2, "ruff"),
    ("flake8 -> ruff",                "flake8 src/",                2, "ruff"),
]:
    tally(test(*args))

# ── Category C: Project conventions (5 rules) ──

print("\nCategory C: Project conventions (5 rules)")
for args in [
    ("bash script.sh -> make",        "bash scripts/ci-check.sh",      2, "make"),
    ("sh script.sh -> make",          "sh install.sh",                  2, "make"),
    ("bash -c cat -> blocked",        "bash -c 'cat file.py'",          2, "Read tool"),
    ("bash -c grep -> blocked",       "bash -c 'grep -rn pat src/'",   2, "Grep tool"),
    ("bash -c python -> blocked",     "bash -c 'python script.py'",    2, "uv run"),
    ("bash -c safe -> still blocked", "bash -c 'git status'",          2, "directly"),
    ("bash -e flag -> allow",         "bash -e script.sh",             0),
    ("./script.sh -> make",           "./scripts/build.sh",             2, "make"),
    ("scripts/foo.sh -> make",        "scripts/deploy.sh prod",         2, "make"),
    ("/abs/path.sh -> make",          "/usr/local/bin/setup.sh",        2, "make"),
    ("not a script -> allow",         "git commit -m 'fix foo.sh'",    0),
    ("chmod script -> allow",         "chmod +x script.sh",            0),
    ("uv run /tmp/ -> blocked",       "uv run /tmp/test.py",           2, "hack/tmp/"),
    ("/tmp/ in cmd -> blocked",       "cp results.json /tmp/out.json", 2, "hack/tmp/"),
    ("rm /tmp/ -> blocked",           "rm /tmp/test-guard.py",          2, "hack/tmp/"),
    ("rm -rf /tmp/ -> blocked",       "rm -rf /tmp/test-output/",       2, "hack/tmp/"),
    ("hack/tmp/ -> allow",            "uv run hack/tmp/test.py",       0),
    ("mkdir hack/tmp -> allow",       "mkdir -p hack/tmp",             0),
    ("no /tmp -> allow",              "uv run test.py",                0),
]:
    tally(test(*args))

# ── Category D: Simpler patterns (2 rules) ──

print("\nCategory D: Simpler patterns (2 rules)")
for args in [
    ("echo noop -> direct output",    'echo "hello world"',        2, "directly"),
    ("echo with pipe -> allow",       'echo "hello" | grep hello', 0),
    ("printf noop -> direct output",  'printf "hello world"',      2, "directly"),
    ("printf with pipe -> allow",     'printf "%s" x | wc -c',    0),
]:
    tally(test(*args))

# ── Category E: Interactive commands (2 rules) ──

print("\nCategory E: Interactive commands (2 rules)")
for args in [
    ("git rebase -i -> branchless",   "git rebase -i HEAD~3",         2, "git-branchless"),
    ("git rebase --interactive",      "git rebase --interactive H~3", 2, "Interactive rebase"),
    ("git rebase (no -i) -> allow",   "git rebase main",              0),
    ("git add -p -> specific files",  "git add -p",                   2, "specific file"),
    ("git add --patch -> hang",       "git add --patch file.py",      2, "Interactive git add"),
    ("git add -i -> hang",            "git add -i",                   2, "Interactive git add"),
    ("git add file -> allow",         "git add file.py",              0),
]:
    tally(test(*args))

# ── Passthrough: Commands that should never be blocked ──

print("\nPassthrough: Commands not blocked")
for args in [
    ("git status",       "git status",         0),
    ("ls -la",           "ls -la",             0),
    ("make build",       "make build",         0),
    ("uv sync",          "uv sync",            0),
    ("docker ps",        "docker ps",          0),
    ("npm run test",     "npm run test",        0),
    ("curl url",         "curl https://x.com", 0),
]:
    tally(test(*args))

# ── Chained command splitting ──

print("\nChained command splitting")
for args in [
    ("echo label in && chain",
     'echo "=== label ===" && cat file.py',                          2, "directly"),
    ("cat in && chain",
     'cd /app && cat file.py',                                       2, "Read tool"),
    ("grep in && chain",
     'cd /app && grep -rn pattern src/',                             2, "Grep tool"),
    ("echo in && chain",
     'git status && echo "done"',                                    2, "directly"),
    ("python in ; chain",
     'cd /app ; python script.py',                                   2, "uv run"),
    ("cat in || chain",
     'test -f x || cat fallback.py',                                 2, "Read tool"),
    ("all-safe chain -> allow",
     'git status && git log --oneline -3',                           0),
    ("quoted && not split",
     'echo "foo && bar" | grep foo',                                 0),
    ("single-quoted && not split",
     "echo 'foo && bar' | wc",                                      0),
    ("triple chain first bad",
     'cat file.py && git status && ls',                              2, "Read tool"),
    ("triple chain middle bad",
     'git status && cat file.py && ls',                              2, "Read tool"),
    ("triple chain last bad",
     'git status && ls && cat file.py',                              2, "Read tool"),
    ("mixed && || ;",
     'git status && git log || cat f.py ; echo done',               2),
    ("real-world offender",
     'echo "=== Source ===" && cd /app && git log --oneline -3 && echo "" && cat plugin.json',
                                                                     2),
]:
    tally(test(*args))

# ── Newline-separated commands ──

print("\nNewline-separated commands")
for args in [
    ("cat on line 2",
     "git status\ncat file.py",                                      2, "Read tool"),
    ("grep on line 3",
     "git status\nls -la\ngrep pattern src/",                        2, "Grep tool"),
    ("continuation line -> allow",
     "docker run \\\n  -v /app:/app \\\n  image",                    0),
    ("all safe lines -> allow",
     "git status\nls -la\nmake build",                               0),
    ("echo on newline",
     'git log\necho "done"',                                         2, "directly"),
    ("comment then cat",
     "# comment\ncat file.py",                                      2, "Read tool"),
]:
    tally(test(*args))

# ── Env var prefix stripping ──

print("\nEnv var prefix stripping")
for args in [
    ("FOO=bar python -> blocked",     "FOO=bar python script.py",        2, "uv run"),
    ("FOO=bar cat -> blocked",        "FOO=bar cat file.py",             2, "Read tool"),
    ("FOO=bar grep -> blocked",       "FOO=bar grep pattern src/",       2, "Grep tool"),
    ("ENV=1 bash script -> blocked",  "ENV=1 bash deploy.sh",            2, "make"),
    ("A=1 B=2 python -> blocked",     "A=1 B=2 python script.py",       2, "uv run"),
    ("FOO=bar make -> allow",         "FOO=bar make build",              0),
    ("FOO=bar uv run -> allow",       "FOO=bar uv run script.py",       0),
]:
    tally(test(*args))

# ── Subshell / backtick extraction ──

print("\nSubshell and backtick extraction")
for args in [
    ("echo $(cat file) -> blocked",   "echo $(cat file.py)",             2, "Read tool"),
    ("echo `cat file` -> blocked",    "echo `cat file.py`",              2, "Read tool"),
    ("result=$(grep) -> blocked",     "result=$(grep -rn pat src/)",     2, "Grep tool"),
    ("$(uv run ok) -> allow",         "echo $(uv run script.py)",        0),
    ("nested $($()) -> blocked",      "echo $(echo $(cat f.py))",        2, "Read tool"),
]:
    tally(test(*args))

# ── Andon cord: GUARD_BYPASS=1 ──

print("\nAndon cord: GUARD_BYPASS=1 prefix")
for args in [
    ("bypass cat -> allow",           "GUARD_BYPASS=1 cat file.py",      0),
    ("bypass bash script -> allow",   "GUARD_BYPASS=1 bash setup.sh",    0),
    ("bypass /tmp -> allow",          "GUARD_BYPASS=1 uv run /tmp/t.py", 0),
    ("bypass covers full chain",      "GUARD_BYPASS=1 cat f.py && grep p", 0),
    ("no bypass -> still blocked",    "cat file.py",                     2, "Read tool"),
]:
    tally(test(*args))

# ── Non-Bash tools: pass through on normal paths ──

print("\nNon-Bash tools: pass through on normal paths")
for name, tool in [
    ("Read tool", "Read"), ("Write tool", "Write"),
    ("Grep tool", "Grep"), ("Glob tool", "Glob"),
]:
    tally(test_non_bash(name, tool))

# ── Non-Bash /tmp/ blocking ──

print("\nNon-Bash /tmp/ blocking (all tools)")
for args in [
    ("Read /tmp/ -> blocked",     "Read",  "/tmp/scratch.py",    2, "hack/tmp/"),
    ("Write /tmp/ -> blocked",    "Write", "/tmp/output.json",   2, "hack/tmp/"),
    ("Edit /tmp/ -> blocked",     "Edit",  "/tmp/config.yaml",   2, "hack/tmp/"),
    ("Grep /tmp/ -> blocked",     "Grep",  "/tmp/logs/",         2, "hack/tmp/", "path"),
    ("Glob /tmp/ -> blocked",     "Glob",  "/tmp/scratch/",      2, "hack/tmp/", "path"),
    ("Read hack/tmp/ -> allow",   "Read",  "hack/tmp/test.py",   0),
    ("Write hack/tmp/ -> allow",  "Write", "hack/tmp/out.json",  0),
    ("Read normal path -> allow", "Read",  "src/main.py",        0),
]:
    tally(test_non_bash(*args))

# ── Whitespace edge cases ──

print("\nWhitespace edge cases")
for args in [
    ("leading spaces",            "   cat file.py",              2, "Read tool"),
    ("leading tab",               "\tcat file.py",               2, "Read tool"),
    ("trailing semicolon",        "git status ;",                0),
    ("double semicolon",          "git status ;; cat file.py",   2, "Read tool"),
    ("empty after semicolon",     "cat file.py ;",               2, "Read tool"),
]:
    tally(test(*args))

# ── Results ──

print(f"\n{'=' * 50}")
print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
if failed > 0:
    sys.exit(1)
