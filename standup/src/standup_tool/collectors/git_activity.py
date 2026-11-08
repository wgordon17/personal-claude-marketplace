"""Collect recent git commits across workspace repos."""

from __future__ import annotations

import logging
import subprocess
from datetime import date, datetime, timedelta

from standup_tool.workspace import RepoInfo

logger = logging.getLogger(__name__)

# Leading NUL (%x00) acts as a commit boundary marker. No -z flag — filenames are
# newline-separated after a blank line. Splitting on \x00 yields one chunk per commit:
# "format_fields\n\nfile1\nfile2\n".
_FIELD_SEP = "\x1f"
_LOG_FORMAT = f"--format=%x00%H{_FIELD_SEP}%s{_FIELD_SEP}%aI{_FIELD_SEP}%ae"


def collect(
    repos: list[RepoInfo],
    since_date: date,
    until_date: date,
    author_email: str = "",
) -> list[dict]:
    """Return list of git commit dicts across all repos since since_date until until_date.

    Uses a single git log call per repo with --name-only to get both metadata and
    changed file paths in one pass, eliminating the N+1 diff-tree subprocess pattern.
    Commits that only touch .dev/ paths are filtered out.

    Each dict: {repo, hash, subject, date, author_email}
    """
    since_str = since_date.isoformat()
    before_str = (until_date + timedelta(days=1)).isoformat()
    commits: list[dict] = []
    repo_errors = 0

    for repo in repos:
        cmd = [
            "git",
            "log",
            "--branches",
            f"--after={since_str}",
            f"--before={before_str}",
            _LOG_FORMAT,
            "--name-only",
            "--fixed-strings",
        ]
        if author_email:
            cmd += [f"--author={author_email}"]

        try:
            result = subprocess.run(
                cmd,
                cwd=repo.path,
                capture_output=True,
                text=True,
                timeout=30,
            )
        # tolerant: skip single timed-out repository and continue unless all repositories fail
        except subprocess.TimeoutExpired:
            logger.debug("Skipping timed-out git log in %s", repo.path)
            logger.warning("git log timed out in %s", repo.path)
            repo_errors += 1
            continue
        if result.returncode != 0:
            logger.debug("Skipping failed git log in %s (exit %d)", repo.path, result.returncode)
            logger.warning("git log failed in %s (exit %d)", repo.path, result.returncode)
            repo_errors += 1
            continue

        # Leading %x00 in format string makes each commit a NUL-delimited chunk:
        #   \x00<format>\n\n<file1>\n<file2>\n
        for chunk in result.stdout.split("\x00"):
            chunk = chunk.strip()
            if not chunk:
                continue

            halves = chunk.split("\n\n", 1)
            meta = halves[0].strip()
            files_block = halves[1].strip() if len(halves) > 1 else ""

            parts = meta.split(_FIELD_SEP, 3)
            if len(parts) < 4:
                continue
            commit_hash, subject, commit_date, commit_email = parts

            # git's --after/--before only prune the traversal; branches with merge
            # commits from periodic upstream rebases can still leak older commits.
            try:
                commit_day = datetime.fromisoformat(commit_date).date()
            # tolerant: skip commit chunk with malformed ISO-8601 date
            except ValueError:
                logger.debug("Skipping commit %s with invalid date %r", commit_hash, commit_date)
                continue
            if not (since_date <= commit_day <= until_date):
                continue

            files = [f for f in files_block.splitlines() if f.strip()]
            if files and all(f.startswith(".dev/") for f in files):
                continue

            commits.append(
                {
                    "repo": repo.name,
                    "hash": commit_hash,
                    "subject": subject,
                    "date": commit_date,
                    "author_email": commit_email,
                }
            )

    if repos and repo_errors == len(repos):
        raise RuntimeError(f"git log failed across all {len(repos)} configured repositories")

    return commits
