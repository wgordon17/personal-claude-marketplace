"""Tests for PostToolUse project-format mode in validate-commit-message.sh."""

import json
import subprocess
from pathlib import Path

VALIDATE_SCRIPT = Path(__file__).parent.parent / "hooks" / "validate-commit-message.sh"


def init_project_repo(
    repo_dir: Path,
    *,
    trailers_required: bool = True,
    subject_pattern: str | None = None,
) -> Path:
    """Create a git repository configured for project commit-format tests."""
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Dev Example"], cwd=repo_dir, check=True)
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


def make_commit(repo_dir: Path, message: str) -> None:
    """Create an empty commit with the given message in repo_dir."""
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", message],
        cwd=repo_dir,
        check=True,
    )


def run_validator(
    *,
    cwd: Path,
    command: str = "git commit -m 'msg'",
) -> subprocess.CompletedProcess[str]:
    """Run validate-commit-message.sh as a PostToolUse hook."""
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    return subprocess.run(
        ["bash", str(VALIDATE_SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )


class TestValidateCommitMessageProjectFormat:
    """Tests for project commit-format mode in validate-commit-message.sh."""

    def test_compliant_jira_commit_passes(self, tmp_path: Path) -> None:
        repo = init_project_repo(tmp_path / "repo")
        make_commit(
            repo,
            "OSAC-1: adds thing\n\n"
            "Assisted-by: Claude <noreply@anthropic.com>\n"
            "Signed-off-by: Dev Example <dev@example.com>",
        )
        res = run_validator(cwd=repo)
        assert res.returncode == 0, res.stderr

    def test_compliant_no_issue_commit_passes(self, tmp_path: Path) -> None:
        repo = init_project_repo(tmp_path / "repo")
        make_commit(
            repo,
            "NO-ISSUE: updates config\n\n"
            "Assisted-by: Claude <noreply@anthropic.com>\n"
            "Signed-off-by: Dev Example <dev@example.com>",
        )
        res = run_validator(cwd=repo)
        assert res.returncode == 0, res.stderr

    def test_long_imperative_subject_allowed_in_project_mode(self, tmp_path: Path) -> None:
        """No subject length or tense checks apply in project mode."""
        repo = init_project_repo(tmp_path / "repo")
        long_subject = (
            "OSAC-123: add a very long imperative commit subject line that exceeds "
            "seventy two characters easily"
        )
        make_commit(
            repo,
            f"{long_subject}\n\n"
            "Assisted-by: Claude <noreply@anthropic.com>\n"
            "Signed-off-by: Dev Example <dev@example.com>",
        )
        res = run_validator(cwd=repo)
        assert res.returncode == 0, res.stderr

    def test_missing_prefix_exits_2_with_spine_message(self, tmp_path: Path) -> None:
        repo = init_project_repo(tmp_path / "repo")
        make_commit(
            repo,
            "adds thing without prefix\n\n"
            "Assisted-by: Claude <noreply@anthropic.com>\n"
            "Signed-off-by: Dev Example <dev@example.com>",
        )
        res = run_validator(cwd=repo)
        assert res.returncode == 2
        assert "Missing Jira key or NO-ISSUE prefix (e.g., OSAC-123: or NO-ISSUE:)" in res.stderr

    def test_missing_assisted_by_exits_2(self, tmp_path: Path) -> None:
        repo = init_project_repo(tmp_path / "repo")
        make_commit(
            repo,
            "OSAC-1: adds thing\n\nSigned-off-by: Dev Example <dev@example.com>",
        )
        res = run_validator(cwd=repo)
        assert res.returncode == 2
        assert "Missing Assisted-by trailer" in res.stderr

    def test_missing_signed_off_by_exits_2(self, tmp_path: Path) -> None:
        repo = init_project_repo(tmp_path / "repo")
        make_commit(
            repo,
            "OSAC-1: adds thing\n\nAssisted-by: Claude <noreply@anthropic.com>",
        )
        res = run_validator(cwd=repo)
        assert res.returncode == 2
        assert "Missing Signed-off-by trailer" in res.stderr

    def test_custom_subject_pattern_override(self, tmp_path: Path) -> None:
        repo = init_project_repo(
            tmp_path / "repo",
            subject_pattern=r"^(CUSTOM-[0-9]+|HOTFIX): ",
        )
        make_commit(
            repo,
            "CUSTOM-42: custom prefix commit\n\n"
            "Assisted-by: Claude <noreply@anthropic.com>\n"
            "Signed-off-by: Dev Example <dev@example.com>",
        )
        res_ok = run_validator(cwd=repo)
        assert res_ok.returncode == 0, res_ok.stderr

        make_commit(
            repo,
            "OSAC-42: non-matching prefix\n\n"
            "Assisted-by: Claude <noreply@anthropic.com>\n"
            "Signed-off-by: Dev Example <dev@example.com>",
        )
        res_bad = run_validator(cwd=repo)
        assert res_bad.returncode == 2
        assert "Missing Jira key or NO-ISSUE prefix" in res_bad.stderr

    def test_co_authored_by_and_emoji_shortcode_still_blocked(self, tmp_path: Path) -> None:
        repo = init_project_repo(tmp_path / "repo")
        make_commit(
            repo,
            "OSAC-1: adds thing\n\n"
            "Assisted-by: Claude <noreply@anthropic.com>\n"
            "Signed-off-by: Dev Example <dev@example.com>\n"
            "Co-authored-by: Someone <s@example.com>",
        )
        res_co = run_validator(cwd=repo)
        assert res_co.returncode == 2
        assert "Co-authored-by" in res_co.stderr

        make_commit(
            repo,
            "OSAC-1: adds :sparkles: shortcode\n\n"
            "Assisted-by: Claude <noreply@anthropic.com>\n"
            "Signed-off-by: Dev Example <dev@example.com>",
        )
        res_emoji = run_validator(cwd=repo)
        assert res_emoji.returncode == 2
        assert "emoji shortcodes" in res_emoji.stderr

    def test_global_git_flags_resolve_target_repo_from_non_repo_cwd(self, tmp_path: Path) -> None:
        repo = init_project_repo(tmp_path / "target_repo")
        non_repo = tmp_path / "non_repo_cwd"
        non_repo.mkdir()

        make_commit(
            repo,
            "bad subject\n\n"
            "Assisted-by: Claude <noreply@anthropic.com>\n"
            "Signed-off-by: Dev Example <dev@example.com>",
        )

        for cmd in (
            f"git -C {repo} commit -m 'bad subject'",
            f"git -c user.name=x -C {repo} commit -m 'bad subject'",
            f"git --git-dir={repo}/.git --work-tree={repo} commit -m 'bad subject'",
        ):
            res = run_validator(cwd=non_repo, command=cmd)
            assert res.returncode == 2, f"Expected exit 2 for {cmd!r}, got {res.returncode}"
            assert "Missing Jira key or NO-ISSUE prefix" in res.stderr

        res_no_pager = run_validator(cwd=repo, command="git --no-pager commit -m 'bad subject'")
        assert res_no_pager.returncode == 2
        assert "Missing Jira key or NO-ISSUE prefix" in res_no_pager.stderr

    def test_unresolvable_dash_c_path_exits_2(self, tmp_path: Path) -> None:
        non_repo = tmp_path / "non_repo_cwd"
        non_repo.mkdir()
        missing = tmp_path / "does-not-exist"
        res = run_validator(cwd=non_repo, command=f"git -C {missing} commit -m 'OSAC-1: x'")
        assert res.returncode == 2
        assert "does not exist" in res.stderr

    def test_unset_key_preserves_conventional_commit_validation(self, tmp_path: Path) -> None:
        repo = init_project_repo(tmp_path / "conv_repo", trailers_required=False)
        make_commit(repo, "feat(scope): adds conventional commit")
        res_ok = run_validator(cwd=repo)
        assert res_ok.returncode == 0, res_ok.stderr

        make_commit(repo, "OSAC-1: non-conventional subject")
        res_bad = run_validator(cwd=repo)
        assert res_bad.returncode == 2
        assert "Conventional Commits format" in res_bad.stderr
