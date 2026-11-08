"""Shared project-memory directory resolution for dev-guard hooks.

Project memory directories (hack/, .local/, scratch/, .dev/) are gitignored, so linked
git worktrees do not contain them. Hooks resolve them against the main worktree.
"""

import subprocess
from pathlib import Path

MEMORY_DIR_CANDIDATES = ("hack", ".local", "scratch", ".dev")
CORE_MEMORY_FILES = ("PROJECT.md", "TODO.md", "SESSIONS.md", "NEXT.md", "LESSONS.md")


def find_main_worktree(start: Path | str) -> Path:
    """Return the first non-bare worktree from `git worktree list --porcelain`.

    Falls back to `start` when git is unavailable, fails, or reports no worktree
    (for example a directory that is not a git repository).
    """
    try:
        res = subprocess.run(
            ["git", "-C", str(start), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return Path(start)
    if res.returncode != 0:
        return Path(start)
    for block in res.stdout.split("\n\n"):
        lines = block.splitlines()
        if "bare" in lines:
            continue
        for line in lines:
            if line.startswith("worktree "):
                return Path(line[len("worktree ") :].strip())
    return Path(start)


def find_memory_dir(root: Path) -> Path | None:
    """Return the first candidate under `root` holding at least two core memory files."""
    for name in MEMORY_DIR_CANDIDATES:
        candidate = root / name
        if candidate.is_dir():
            core_count = sum(1 for f in CORE_MEMORY_FILES if (candidate / f).is_file())
            if core_count >= 2:
                return candidate
    return None
