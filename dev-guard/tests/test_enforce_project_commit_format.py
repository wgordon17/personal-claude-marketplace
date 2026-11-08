"""Tests for PreToolUse enforce-project-commit-format.sh in dev-guard."""

import json
import os
import subprocess
from pathlib import Path

ENFORCE_SCRIPT = Path(__file__).parent.parent / "hooks" / "enforce-project-commit-format.sh"


def init_repo(
    repo_dir: Path,
    *,
    trailers_required: bool = True,
    subject_pattern: str | None = None,
) -> Path:
    """Create a git repository for PreToolUse commit-format tests."""
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    if trailers_required:
        subprocess.run(
            ["git", "config", "claude-code.commit-trailers-required", "true"],
            cwd=repo_dir,
            check=True,
        )
    if subject_pattern is not None:
        subprocess.run(
            ["git", "config", "claude-code.commit-subject-pattern", subject_pattern],
            cwd=repo_dir,
            check=True,
        )
    return repo_dir


def run_enforce(
    command: str,
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run enforce-project-commit-format.sh with a PreToolUse Bash payload."""
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    return subprocess.run(
        ["bash", str(ENFORCE_SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        check=False,
    )


class TestEnforceProjectCommitFormat:
    """Tests for PreToolUse enforce-project-commit-format.sh."""

    def test_compliant_signoff_and_trailer_flag_passes(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        cmd = (
            'git commit -s -m "OSAC-1: adds thing" '
            '--trailer "Assisted-by: Claude <noreply@anthropic.com>"'
        )
        res = run_enforce(cmd, cwd=repo)
        assert res.returncode == 0, res.stderr

    def test_compliant_heredoc_commit_passes(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        cmd = (
            "git commit -m \"$(cat <<'EOF'\n"
            "NO-ISSUE: updates configuration\n\n"
            "Assisted-by: Claude <noreply@anthropic.com>\n"
            "Signed-off-by: Dev Example <dev@example.com>\n"
            "EOF\n"
            ')"'
        )
        res = run_enforce(cmd, cwd=repo)
        assert res.returncode == 0, res.stderr

    def test_blocks_missing_prefix(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        cmd = (
            'git commit -s -m "adds thing without prefix" '
            '--trailer "Assisted-by: Claude <noreply@anthropic.com>"'
        )
        res = run_enforce(cmd, cwd=repo)
        assert res.returncode == 2
        assert "Missing Jira key or NO-ISSUE prefix" in res.stderr

    def test_blocks_missing_assisted_by(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        cmd = 'git commit -s -m "OSAC-1: adds thing"'
        res = run_enforce(cmd, cwd=repo)
        assert res.returncode == 2
        assert "Missing Assisted-by trailer" in res.stderr
        assert "Missing Signed-off-by" not in res.stderr

    def test_blocks_missing_signed_off_by(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        cmd = (
            'git commit -m "OSAC-1: adds thing" '
            '--trailer "Assisted-by: Claude <noreply@anthropic.com>"'
        )
        res = run_enforce(cmd, cwd=repo)
        assert res.returncode == 2
        assert "Missing Signed-off-by trailer" in res.stderr
        assert "Missing Assisted-by" not in res.stderr

    def test_blocks_leading_git_dash_c_config_flag(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        res = run_enforce('git -c user.name=a commit -m "bad"', cwd=repo)
        assert res.returncode == 2
        assert "BLOCK: commit message missing required elements:" in res.stderr

    def test_blocks_comment_bypass(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        cmd = 'git commit -m "bad" # NO-ISSUE: Assisted-by: Signed-off-by:'
        res = run_enforce(cmd, cwd=repo)
        assert res.returncode == 2
        assert "Missing Jira key or NO-ISSUE prefix" in res.stderr
        assert "Missing Assisted-by trailer" in res.stderr
        assert "Missing Signed-off-by trailer" in res.stderr

    def test_blocks_trailer_subject_spoof(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        cmd = 'git commit -s -m "bad subject" --trailer "Assisted-by: NO-ISSUE: foo"'
        res = run_enforce(cmd, cwd=repo)
        assert res.returncode == 2
        assert "Missing Jira key or NO-ISSUE prefix" in res.stderr

    def test_defers_file_flag_and_arbitrary_subshell_to_post_tool_use(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        res_file = run_enforce("git commit -F msg.txt", cwd=repo)
        assert res_file.returncode == 0

        res_amend = run_enforce("git commit --amend --no-edit", cwd=repo)
        assert res_amend.returncode == 0

        res_subshell = run_enforce('git commit -m "$(generate_msg)"', cwd=repo)
        assert res_subshell.returncode == 0

    def test_fails_closed_on_unresolvable_dash_c_path(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        missing = tmp_path / "nonexistent"
        res = run_enforce(f'git -C {missing} commit -m "OSAC-1: adds thing"', cwd=repo)
        assert res.returncode == 2
        assert "does not exist" in res.stderr

    def test_exits_0_for_non_commit_and_repos_without_key(self, tmp_path: Path) -> None:
        project_repo = init_repo(tmp_path / "project_repo", trailers_required=True)
        other_repo = init_repo(tmp_path / "other_repo", trailers_required=False)

        assert run_enforce("git status", cwd=project_repo).returncode == 0
        assert run_enforce('git commit -m "feat: normal"', cwd=other_repo).returncode == 0
        assert (
            run_enforce(
                f'git -C {other_repo} commit -m "feat: normal"',
                cwd=project_repo,
            ).returncode
            == 0
        )

    def test_fails_closed_when_jq_missing(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        empty_bin = tmp_path / "empty_bin"
        empty_bin.mkdir()
        # Symlink only bash so jq is unavailable on PATH
        bash_path = Path("/bin/bash")
        if bash_path.exists():
            (empty_bin / "bash").symlink_to(bash_path)
        env = {**os.environ, "PATH": str(empty_bin)}
        env.pop("BASH_ENV", None)
        res = run_enforce('git commit -m "OSAC-1: test"', cwd=repo, env=env)
        assert res.returncode == 2
        assert "jq" in res.stderr
