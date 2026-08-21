"""Black-box subprocess tests for .github/scripts/notify-sync-failure.sh —
the shared dedup-search-then-create-or-comment logic invoked by both
sync-token-efficiency.yml's and sync-drawio-skill.yml's failure-notification
steps.

Stubs `gh` with a fake shell script on PATH rather than hitting the live
GitHub API: the fake logs each invocation's arguments with per-argument
boundary markers (`<<arg>>`, one per line, `===` between invocations) so
tests can distinguish "one argument containing a quote" from "split into
multiple arguments" -- `echo "$*"` would flatten that distinction away.
Returns a canned `gh issue list` result controlled by an env var, and can
be told to make a specific subcommand fail, letting tests assert which
branch (comment vs. create) fired, with what arguments, and that a `gh`
failure aborts the script rather than being silently swallowed.
"""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
NOTIFY_SCRIPT = REPO_ROOT / ".github" / "scripts" / "notify-sync-failure.sh"

_FAKE_GH = """#!/usr/bin/env bash
printf '<<%s>>\\n' "$@" >> "$FAKE_GH_LOG"
printf '===\\n' >> "$FAKE_GH_LOG"
if [[ "${FAKE_GH_FAIL_SUBCOMMAND:-}" == "$1 $2" ]]; then
  exit 1
fi
if [[ "$1 $2" == "issue list" ]]; then
  echo "${FAKE_GH_EXISTING_ISSUE:-}"
fi
exit 0
"""


def _run_script(
    title: str,
    body: str,
    tmp_path: Path,
    existing_issue: str = "",
    fail_subcommand: str = "",
) -> tuple[subprocess.CompletedProcess, list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake_gh = bin_dir / "gh"
    fake_gh.write_text(_FAKE_GH)
    fake_gh.chmod(0o755)

    log_file = tmp_path / "gh_calls.log"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_GH_LOG": str(log_file),
        "FAKE_GH_EXISTING_ISSUE": existing_issue,
        "FAKE_GH_FAIL_SUBCOMMAND": fail_subcommand,
    }
    result = subprocess.run(
        ["bash", str(NOTIFY_SCRIPT), title, body],
        capture_output=True,
        text=True,
        env=env,
    )
    invocations = log_file.read_text().split("===\n") if log_file.exists() else []
    return result, [inv for inv in invocations if inv]


class TestNotifySyncFailureScript:
    """Black-box subprocess tests for notify-sync-failure.sh."""

    def test_creates_issue_when_none_exists(self, tmp_path):
        result, invocations = _run_script("Some Sync Failed", "body text here", tmp_path)
        assert result.returncode == 0, result.stderr
        assert len(invocations) == 2
        assert "<<issue>>" in invocations[0] and "<<list>>" in invocations[0]
        assert "<<create>>" in invocations[1]
        assert "<<Some Sync Failed>>" in invocations[1]
        assert "<<body text here>>" in invocations[1]

    def test_comments_on_existing_open_issue_instead_of_creating(self, tmp_path):
        result, invocations = _run_script(
            "Some Sync Failed", "body text here", tmp_path, existing_issue="42"
        )
        assert result.returncode == 0, result.stderr
        assert len(invocations) == 2
        assert "<<comment>>" in invocations[1]
        assert "<<42>>" in invocations[1]
        assert "<<create>>" not in invocations[1]

    def test_embedded_quote_in_title_does_not_corrupt_search_but_is_preserved_in_create(
        self, tmp_path
    ):
        """A title containing a literal double quote must not break out of
        GitHub's in:title "..." quoted-phrase search syntax -- the quote is
        stripped for the search query only, while the full original title
        (quote included) is still used for the created issue's --title."""
        result, invocations = _run_script('Sync "broke" again', "body text here", tmp_path)
        assert result.returncode == 0, result.stderr
        assert '<<in:title "Sync broke again">>' in invocations[0]
        assert "<<--title>>" in invocations[1]
        assert '<<Sync "broke" again>>' in invocations[1]

    def test_gh_list_failure_aborts_script_without_creating_or_commenting(self, tmp_path):
        """If gh issue list itself fails (auth, rate limit, network), the
        script must abort rather than silently treating the failure as
        'no existing issue found' and proceeding to create a duplicate."""
        result, invocations = _run_script(
            "Some Sync Failed", "body text here", tmp_path, fail_subcommand="issue list"
        )
        assert result.returncode != 0
        assert len(invocations) == 1
        assert "<<issue>>" in invocations[0] and "<<list>>" in invocations[0]

    def test_missing_title_exits_1(self, tmp_path):
        result, _ = _run_script("", "body text here", tmp_path)
        assert result.returncode == 1
        assert "requires <title> and <body>" in result.stderr

    def test_missing_body_exits_1(self, tmp_path):
        result, _ = _run_script("Some Sync Failed", "", tmp_path)
        assert result.returncode == 1
        assert "requires <title> and <body>" in result.stderr
