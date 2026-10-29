"""Tests for hardened subprocess execution, gh/git wrappers, and ReplayProcs (Task 3 Step 5)."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from tech_radar import config, context, procs
from tech_radar.errors import CommandDenied, UnsafeArg

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG_DIR = REPO_ROOT / "tech-radar" / "skills" / "radar" / "scripts" / "tech_radar"


def _write_proc_fixture(
    root: Path,
    argv: list[str],
    stdout: str = "{}",
    *,
    cwd: str | None = None,
    returncode: int = 0,
) -> str:
    root.mkdir(parents=True, exist_ok=True)
    key = procs.procs_key(cwd, argv)
    (root / f"{key}.json").write_text(
        json.dumps({"returncode": returncode, "stdout": stdout, "stderr": ""}),
        encoding="utf-8",
    )
    return key


def test_gh_wrapper_endpoint_and_graphql_rules(tmp_path: Path) -> None:
    """Verify Spec 12 A33 gh endpoint regex, GET-only rule, GraphQL rules, and --paginate."""
    fix_dir = tmp_path / "procs_fix"
    replay = procs.ReplayProcs([fix_dir])

    # 1. gh api -X POST repos/a/b/issues -> CommandDenied
    with pytest.raises(CommandDenied, match="GET"):
        procs.gh(["api", "-X", "POST", "repos/a/b/issues"], transport=replay)

    # 2. gh issue create -> CommandDenied
    with pytest.raises(CommandDenied, match="gh api"):
        procs.gh(["issue", "create"], transport=replay)

    # 3. GraphQL mutation -> CommandDenied (even with comment/string tricks)
    with pytest.raises(CommandDenied, match="mutation"):
        procs.gh(
            [
                "api",
                "graphql",
                "-f",
                'query=mutation { deleteIssue(input: {issueId: "1"}) { clientMutationId } }',
            ],
            transport=replay,
        )
    with pytest.raises(CommandDenied, match="mutation"):
        procs.gh(
            [
                "api",
                "-X",
                "POST",
                "graphql",
                "-f",
                "query=# comment\nmutation CreateThing { __typename }",
            ],
            transport=replay,
        )

    # 4. Allowed endpoints: GraphQL query, advisories, issues/12/comments, starred --paginate
    gql_args = [
        "api",
        "graphql",
        "-f",
        "query=query { viewer { login } }",
    ]
    _write_proc_fixture(fix_dir, ["gh", *gql_args], '{"data":{"viewer":{"login":"example-user"}}}')

    adv_args = ["api", "advisories?type=malware&updated=2026-10-21..*"]
    _write_proc_fixture(fix_dir, ["gh", *adv_args], "[]")

    comments_args = ["api", "repos/a/b/issues/12/comments"]
    _write_proc_fixture(fix_dir, ["gh", *comments_args], "[]")

    starred_args = ["api", "user/starred", "--paginate"]
    _write_proc_fixture(fix_dir, ["gh", *starred_args], "[]")

    with replay.activate():
        r_gql = procs.gh(gql_args)
        assert json.loads(r_gql.stdout)["data"]["viewer"]["login"] == "example-user"

        r_adv = procs.gh(adv_args)
        assert r_adv.stdout == "[]"

        r_com = procs.gh(comments_args)
        assert r_com.stdout == "[]"

        r_star = procs.gh(starred_args)
        assert r_star.stdout == "[]"

    # 5. repos/a/b/issues/12/labels -> CommandDenied
    with pytest.raises(CommandDenied):
        procs.gh(["api", "repos/a/b/issues/12/labels"], transport=replay)

    # 6. Non-graphql endpoint with -f/-F without explicit -X GET -> CommandDenied (SEC-001)
    with pytest.raises(CommandDenied):
        procs.gh(["api", "search/issues", "-f", "q=repo:a/b"], transport=replay)

    # 7. -f/-F value starting with '@' -> UnsafeArg (SEC-001)
    with pytest.raises(UnsafeArg):
        procs.gh(["api", "-X", "GET", "search/issues", "-F", "q=@/etc/passwd"], transport=replay)


def test_unsafe_arg_and_prefixed_option_validation(tmp_path: Path) -> None:
    """--registry=http://evil raises UnsafeArg; literal prefix + validated value is permitted."""
    fix_dir = tmp_path / "procs_fix"
    replay = procs.ReplayProcs([fix_dir])

    with pytest.raises(UnsafeArg):
        procs.run(["npm", "ls", "--registry=http://evil"], transport=replay)

    with pytest.raises(UnsafeArg):
        procs.run(["uv", "run", "$(touch /tmp/pwn)"], transport=replay)

    # Literal prefix -S joined to a validated version value is allowed (Spec 11 §5)
    _write_proc_fixture(
        fix_dir,
        [
            "git",
            "-c",
            "core.fsmonitor=",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "protocol.file.allow=never",
            "-c",
            "diff.external=",
            "-C",
            "repo_a",
            "log",
            "--no-textconv",
            "--no-ext-diff",
            "-S1.107.0",
        ],
        "commit123",
    )
    res = procs.git(["log", "-S1.107.0"], repo="repo_a", transport=replay)
    assert res.stdout == "commit123"


def test_resolve_live_vs_replay_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live resolve('definitely-missing') -> None; replay resolve('uv') -> placeholder."""
    assert procs.resolve("definitely-missing") is None

    replay = procs.ReplayProcs([])
    with replay.activate():
        assert procs.resolve("uv") == "/opt/homebrew/bin/uv"
        assert procs.resolve("gh") == "/opt/homebrew/bin/gh"
        assert procs.resolve("definitely-missing") is None

    # Also via TECH_RADAR_REPLAY_DIR env var
    monkeypatch.setenv("TECH_RADAR_REPLAY_DIR", "/some/fixture/root")
    assert procs.resolve("uv") == "/opt/homebrew/bin/uv"
    monkeypatch.delenv("TECH_RADAR_REPLAY_DIR", raising=False)

    # Explicit transport=ReplayProcs resolves even when live _resolve_live returns None
    monkeypatch.setattr(procs, "_resolve_live", lambda _name: None)
    assert procs.resolve("gh", replay=True) == "/opt/homebrew/bin/gh"
    runner = context.Procs(replay)
    assert runner.resolve("gh") == "/opt/homebrew/bin/gh"


def test_replay_procs_normalization_and_call_recording(
    tmp_path: Path,
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    """launchctl bootout gui/501/x and gui/1001/x share replay key; calls recorded in order."""
    k501 = procs.procs_key(None, ["/bin/launchctl", "bootout", "gui/501/com.tech-radar.hourly"])
    k1001 = procs.procs_key(
        None,
        ["/opt/homebrew/bin/launchctl", "bootout", "gui/1001/com.tech-radar.hourly"],
    )
    assert k501 == k1001

    p = config.paths(ensure_dirs=True)
    run_file = p.run_root / "runs" / "r1"
    norm = procs.normalize_argv(["/opt/homebrew/bin/uv", "run", str(run_file), "gui/501/x"])
    assert norm == ["uv", "run", "{RUN}/runs/r1", "gui/{UID}/x"]

    fix_dir = tmp_path / "procs_fix"
    _write_proc_fixture(
        fix_dir,
        ["launchctl", "bootout", "gui/501/com.tech-radar.hourly"],
        "ok",
    )
    rp = replay_procs(extra_roots=[fix_dir])
    with rp.activate():
        procs.run(["launchctl", "bootout", "gui/501/com.tech-radar.hourly"])
        procs.run(["launchctl", "bootout", "gui/1001/com.tech-radar.hourly"])

    assert len(rp.calls) == 2
    assert rp.calls[0][0] == ["launchctl", "bootout", "gui/{UID}/com.tech-radar.hourly"]
    assert rp.calls[1][0] == ["launchctl", "bootout", "gui/{UID}/com.tech-radar.hourly"]

    # Verify cwd is part of procs_key so identical argv in different projects have distinct keys
    key_proj_a = procs.procs_key(
        p.home / "SynologyDrive" / "ActiveProjects" / "repo_a",
        ["uv", "audit", "--frozen", "--output-format", "json"],
    )
    key_proj_b = procs.procs_key(
        p.home / "SynologyDrive" / "ActiveProjects" / "repo_b",
        ["uv", "audit", "--frozen", "--output-format", "json"],
    )
    assert key_proj_a != key_proj_b


def test_git_wrapper_passthrough_and_allowlist(tmp_path: Path) -> None:
    """git passthrough runs against temp repo, records calls, and enforces subcommand allowlist."""
    repo_dir = tmp_path / "temp_repo"
    repo_dir.mkdir()
    env = {
        "HOME": str(tmp_path),
        "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "example-user",
        "GIT_AUTHOR_EMAIL": "user@example.com",
        "GIT_COMMITTER_NAME": "example-user",
        "GIT_COMMITTER_EMAIL": "user@example.com",
    }
    subprocess.run(["git", "init", str(repo_dir)], env=env, check=True, capture_output=True)
    (repo_dir / "README.md").write_text("hello radar\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_dir), "add", "README.md"], env=env, check=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "commit", "-m", "initial"],
        env=env,
        check=True,
        capture_output=True,
    )

    rp = procs.ReplayProcs([], passthrough=("git",))
    with rp.activate():
        rev_res = procs.git(["rev-parse", "HEAD"], repo=repo_dir)
        sha = rev_res.stdout.strip()
        assert len(sha) == 40

        log_res = procs.git(["log", "-1", "--format=%H"], repo=repo_dir)
        assert log_res.stdout.strip() == sha
        assert "-c" in log_res.argv
        assert "diff.external=" in log_res.argv
        assert "--no-textconv" in log_res.argv
        assert "--no-ext-diff" in log_res.argv

        show_res = procs.git(["show", f"{sha}:README.md"], repo=repo_dir)
        assert show_res.stdout == "hello radar\n"
        assert "diff.external=" in show_res.argv
        assert "--no-textconv" in show_res.argv
        assert "--no-ext-diff" in show_res.argv

        tree_res = procs.git(["ls-tree", "-r", sha, "--", "README.md"], repo=repo_dir)
        assert "README.md" in tree_res.stdout

    assert len(rp.calls) == 4
    assert all(call[0][0] == "git" for call in rp.calls)

    # Disallowed git subcommands raise CommandDenied
    for forbidden in (
        ["status"],
        ["cat-file", "-t", "HEAD"],
        ["checkout", "HEAD"],
    ):
        with pytest.raises(CommandDenied):
            procs.git(forbidden, repo=repo_dir, transport=rp)


def test_no_shell_true_os_system_or_popen_in_package() -> None:
    """Grep test: no shell=True, os.system, or os.popen under P/."""
    py_files = sorted(PKG_DIR.rglob("*.py"))
    assert py_files
    forbidden_re = re.compile(r"\bshell\s*=\s*True\b|\bos\s*\.\s*(?:system|popen)\s*\(")
    violations: list[str] = []
    for path in py_files:
        text = path.read_text(encoding="utf-8")
        if forbidden_re.search(text):
            violations.append(str(path.relative_to(REPO_ROOT)))
    assert not violations, f"Found forbidden shell execution in: {violations}"
