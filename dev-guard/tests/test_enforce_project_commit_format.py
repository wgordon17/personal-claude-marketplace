"""Tests for PreToolUse enforce-project-commit-format.sh in dev-guard."""

import json
import os
import subprocess
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent / "hooks"
ENFORCE_SCRIPT = HOOKS_DIR / "enforce-project-commit-format.sh"
ASSISTED = '--trailer "Assisted-by: Claude <noreply@anthropic.com>"'


@pytest.fixture(autouse=True)
def _isolated_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's global git config and GIT_* variables out of these tests."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(var, raising=False)


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


class TestParserForms:
    """Command shapes the static parser must recognise (or deliberately defer)."""

    @pytest.mark.parametrize(
        "command",
        [
            f'git commit -am"bad subject" {ASSISTED}',
            f'git commit -am"Fix: no prefix" -s {ASSISTED}',
            f'/usr/bin/git commit -m "bad" {ASSISTED}',
            f'command git commit -m "bad" {ASSISTED}',
            f'env GIT_PAGER=cat git commit -m "bad" {ASSISTED}',
            f'time git commit -m "bad" {ASSISTED}',
            f'FOO=1 git commit -m "bad" {ASSISTED}',
            f'true | git commit -m "bad" {ASSISTED}',
            f'true & git commit -m "bad" {ASSISTED}',
            f'(git commit -m "bad" {ASSISTED})',
            f'{{ git commit -m "bad" {ASSISTED}; }}',
            f'git add x && git commit -m "bad" {ASSISTED}',
            f'git add x; git commit -m "bad" {ASSISTED}',
            f'git commit -m "bad" {ASSISTED} 2>&1',
        ],
    )
    def test_blocks_noncompliant_commit_in_any_recognised_form(
        self, tmp_path: Path, command: str
    ) -> None:
        repo = init_repo(tmp_path / "repo")
        res = run_enforce(command, cwd=repo)
        assert res.returncode == 2, command

    def test_attached_message_text_does_not_set_signoff(self, tmp_path: Path) -> None:
        """The 's' in 'issues' belongs to the message, not to a -s flag."""
        repo = init_repo(tmp_path / "repo")
        res = run_enforce(f'git commit -am"OSAC-1: fixes issues" {ASSISTED}', cwd=repo)
        assert res.returncode == 2
        assert "Missing Signed-off-by trailer" in res.stderr

    @pytest.mark.parametrize(
        "command",
        [
            f'git commit -sm "OSAC-1: adds thing" {ASSISTED}',
            f'git commit -asm "OSAC-1: adds thing" {ASSISTED}',
            f'git commit -am"OSAC-1: adds thing" -s {ASSISTED}',
            f'git commit --signoff --message="OSAC-1: adds thing" {ASSISTED}',
            f'git commit -s -m "OSAC-1: adds thing" -m "body" {ASSISTED}',
            'git commit -s --trailer="Assisted-by: Claude" -m "OSAC-1: adds thing"',
            f'git -C . commit -s -m "OSAC-1: adds thing" {ASSISTED}',
        ],
    )
    def test_allows_compliant_commit_in_any_recognised_form(
        self, tmp_path: Path, command: str
    ) -> None:
        repo = init_repo(tmp_path / "repo")
        res = run_enforce(command, cwd=repo)
        assert res.returncode == 0, res.stderr

    @pytest.mark.parametrize(
        "command",
        [
            "git commit",
            "git commit -C HEAD",
            "git commit -c HEAD",
            "git commit --reuse-message=HEAD",
            "git commit --fixup=HEAD",
            "git commit --squash HEAD",
            "git commit -F msg.txt",
            "git commit -aF msg.txt",
            "git commit --amend --no-edit",
        ],
    )
    def test_defers_commits_without_a_readable_message(self, tmp_path: Path, command: str) -> None:
        repo = init_repo(tmp_path / "repo")
        assert run_enforce(command, cwd=repo).returncode == 0, command

    @pytest.mark.parametrize(
        "command",
        [
            "git commit-tree abc123",
            'echo "git commit -m bad"',
            'git log --grep="git commit"',
            "git status && git diff",
        ],
    )
    def test_ignores_commands_that_only_mention_commit(self, tmp_path: Path, command: str) -> None:
        repo = init_repo(tmp_path / "repo")
        assert run_enforce(command, cwd=repo).returncode == 0, command

    def test_heredoc_body_with_quotes_and_hashes_is_read_intact(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        cmd = (
            "git commit -m \"$(cat <<'EOF'\n"
            'OSAC-1: handles (parens) and "quotes" # not a comment\n\n'
            "Assisted-by: Claude <noreply@anthropic.com>\n"
            "Signed-off-by: Dev Example <dev@example.com>\n"
            "EOF\n"
            ')"'
        )
        res = run_enforce(cmd, cwd=repo)
        assert res.returncode == 0, res.stderr

    def test_trailer_words_inside_subject_do_not_count(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        res = run_enforce('git commit -s -m "OSAC-1: fix Assisted-by: handling"', cwd=repo)
        assert res.returncode == 2
        assert "Missing Assisted-by trailer" in res.stderr

    def test_cd_changes_the_enforced_repository(self, tmp_path: Path) -> None:
        project = init_repo(tmp_path / "project")
        other = init_repo(tmp_path / "other", trailers_required=False)
        assert run_enforce(f'cd {project} && git commit -m "bad"', cwd=other).returncode == 2
        assert run_enforce(f'cd {other} && git commit -m "bad"', cwd=project).returncode == 0

    def test_git_dir_assignment_changes_the_enforced_repository(self, tmp_path: Path) -> None:
        project = init_repo(tmp_path / "project")
        other = init_repo(tmp_path / "other", trailers_required=False)
        res = run_enforce(f'GIT_DIR={project}/.git git commit -m "bad"', cwd=other)
        assert res.returncode == 2

    def test_git_dir_and_work_tree_options(self, tmp_path: Path) -> None:
        project = init_repo(tmp_path / "project")
        other = init_repo(tmp_path / "other", trailers_required=False)
        res = run_enforce(f'git --git-dir={project}/.git commit -m "bad"', cwd=other)
        assert res.returncode == 2
        missing = tmp_path / "nowhere"
        res = run_enforce(f'git --git-dir {missing} commit -m "bad"', cwd=project)
        assert res.returncode == 2
        assert "does not exist" in res.stderr
        res = run_enforce(f'git --work-tree={missing} commit -m "bad"', cwd=project)
        assert res.returncode == 2


class TestDynamicTargets:
    """Targets that depend on shell expansion cannot be resolved; they must not false-block."""

    @pytest.mark.parametrize(
        "command",
        [
            'git -C "$REPO" commit -m "bad"',
            'git -C "$(git rev-parse --show-toplevel)" commit -m "bad"',
            'git --git-dir="$GD" commit -m "bad"',
            'GIT_DIR="$GD" git commit -m "bad"',
            'cd "$WT" && git commit -m "bad"',
        ],
    )
    def test_defers_instead_of_failing_closed(self, tmp_path: Path, command: str) -> None:
        repo = init_repo(tmp_path / "repo")
        res = run_enforce(command, cwd=repo)
        assert res.returncode == 0, res.stderr
        assert "does not exist" not in res.stderr

    def test_relative_paths_resolve_against_the_target_directory(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        (repo / "sub").mkdir()
        res = run_enforce('git -C sub commit -m "bad"', cwd=repo)
        assert res.returncode == 2
        assert "Missing Jira key" in res.stderr
        res = run_enforce(f'git -C {repo} --git-dir=.git commit -m "bad"', cwd=tmp_path)
        assert res.returncode == 2
        assert "does not exist" not in res.stderr
        assert "Missing Jira key" in res.stderr
        res = run_enforce(f'cd {repo} && git --git-dir=.git commit -m "bad"', cwd=tmp_path)
        assert "does not exist" not in res.stderr
        assert res.returncode == 2


class TestParserRobustness:
    @pytest.mark.parametrize(
        "command",
        [
            'git -P commit -m "bad"',
            'git --no-optional-locks commit -m "bad"',
            'git --no-advice commit -m "bad"',
            'git --namespace ns commit -m "bad"',
        ],
    )
    def test_skips_valueless_and_valued_global_options(self, tmp_path: Path, command: str) -> None:
        repo = init_repo(tmp_path / "repo")
        assert run_enforce(command, cwd=repo).returncode == 2, command

    def test_heredoc_body_is_not_parsed_as_shell(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        body_with_commit = "cat > notes.md <<'EOF'\ngit commit -m \"WIP\"\nEOF"
        assert run_enforce(body_with_commit, cwd=repo).returncode == 0

    def test_apostrophe_in_heredoc_body_does_not_hide_a_later_commit(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        cmd = f"cat <<'EOF'\ndon't do this\nEOF\ngit commit -m \"bad\" {ASSISTED}"
        assert run_enforce(cmd, cwd=repo).returncode == 2

    def test_stripped_tab_heredoc_terminator(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        cmd = f'cat <<-EOF\n\tgit commit -m x\n\tEOF\ngit commit -m "bad" {ASSISTED}'
        assert run_enforce(cmd, cwd=repo).returncode == 2

    def test_here_string_does_not_swallow_the_following_commit(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        cmd = f'jq . <<< "$json"\ngit commit -m "bad" {ASSISTED}'
        assert run_enforce(cmd, cwd=repo).returncode == 2

    def test_absolute_cd_after_an_unresolved_cd_is_enforced(self, tmp_path: Path) -> None:
        project = init_repo(tmp_path / "project")
        other = init_repo(tmp_path / "other", trailers_required=False)
        res = run_enforce(f'cd "$X" && cd {project} && git commit -m "bad"', cwd=other)
        assert res.returncode == 2
        res = run_enforce('cd "$X" && cd sub && git commit -m "bad"', cwd=project)
        assert res.returncode == 0

    def test_every_commit_in_a_command_is_checked(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        good = f'git commit -s -m "OSAC-1: adds thing" {ASSISTED}'
        assert run_enforce(f"{good} && {good}", cwd=repo).returncode == 0
        res = run_enforce(f'{good} && git commit -m "bad" {ASSISTED}', cwd=repo)
        assert res.returncode == 2

    def test_ansi_c_quoted_message_defers(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        cmd = "git commit -m $'NO-ISSUE: x\\n\\nAssisted-by: c\\nSigned-off-by: d'"
        assert run_enforce(cmd, cwd=repo).returncode == 0

    def test_trailer_with_equals_separator(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        cmd = (
            'git commit -m "OSAC-1: adds thing" '
            '--trailer "Assisted-by=Claude" --trailer "Signed-off-by=Dev <d@example.com>"'
        )
        res = run_enforce(cmd, cwd=repo)
        assert res.returncode == 0, res.stderr


class TestSubjectPattern:
    def test_custom_pattern_accepts_and_rejects(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo", subject_pattern="CUSTOM-[0-9]+: ")
        ok = run_enforce(f'git commit -s -m "CUSTOM-1: adds thing" {ASSISTED}', cwd=repo)
        assert ok.returncode == 0, ok.stderr
        bad = run_enforce(f'git commit -s -m "OSAC-1: adds thing" {ASSISTED}', cwd=repo)
        assert bad.returncode == 2

    def test_unanchored_pattern_is_anchored_to_the_subject_start(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo", subject_pattern="CUSTOM-[0-9]+: ")
        res = run_enforce(f'git commit -s -m "xx CUSTOM-1: adds thing" {ASSISTED}', cwd=repo)
        assert res.returncode == 2


class TestFailClosed:
    @staticmethod
    def _env_without_tools(tmp_path: Path) -> dict[str, str]:
        """An environment whose PATH holds only bash, so jq and coreutils are unavailable."""
        empty_bin = tmp_path / "empty_bin"
        empty_bin.mkdir()
        bash_path = Path("/bin/bash")
        if bash_path.exists():
            (empty_bin / "bash").symlink_to(bash_path)
        env = {**os.environ, "PATH": str(empty_bin)}
        env.pop("BASH_ENV", None)
        return env

    def test_missing_jq_does_not_block_unrelated_commands(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        env = self._env_without_tools(tmp_path)
        assert run_enforce("ls -la", cwd=repo, env=env).returncode == 0

    def test_missing_jq_blocks_commit_commands(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        env = self._env_without_tools(tmp_path)
        res = run_enforce('git commit -m "OSAC-1: test"', cwd=repo, env=env)
        assert res.returncode == 2
        assert "jq" in res.stderr

    def test_missing_helper_library_blocks(self, tmp_path: Path) -> None:
        """A hook that cannot load its parser must not silently pass commits."""
        hooks = tmp_path / "hooks"
        hooks.mkdir()
        script = hooks / "enforce-project-commit-format.sh"
        script.write_text(ENFORCE_SCRIPT.read_text())
        repo = init_repo(tmp_path / "repo")
        payload = {"tool_name": "Bash", "tool_input": {"command": 'git commit -m "x"'}}
        res = subprocess.run(
            ["bash", str(script)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=repo,
            check=False,
        )
        assert res.returncode == 2
        assert "cannot load" in res.stderr


class TestWiring:
    """Tests that call the script directly still pass if nothing invokes it."""

    def test_hooks_json_registers_the_pre_tool_use_bash_hook(self) -> None:
        config = json.loads((HOOKS_DIR / "hooks.json").read_text())
        commands = [
            hook["command"]
            for entry in config["hooks"]["PreToolUse"]
            if entry.get("matcher") == "Bash"
            for hook in entry["hooks"]
        ]
        assert any("enforce-project-commit-format.sh" in c for c in commands)

    def test_omp_bridge_runs_the_hook_and_honours_exit_2(self) -> None:
        source = (HOOKS_DIR.parent / "omp-extension.ts").read_text()
        assert "enforce-project-commit-format.sh" in source
        assert "commitFormatResult.code === 2" in source
