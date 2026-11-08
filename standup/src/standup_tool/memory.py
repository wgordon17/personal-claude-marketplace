"""Project-memory directory detection across main and linked git worktrees."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

MEMORY_CANDIDATES: tuple[str, ...] = ("hack", ".local", "scratch", ".dev")
CORE_MEMORY_FILES: tuple[str, ...] = (
    "PROJECT.md",
    "TODO.md",
    "SESSIONS.md",
    "NEXT.md",
    "LESSONS.md",
)


def _resolve_main_worktree(repo_path: Path) -> Path:
    """Resolve main worktree root via `git worktree list --porcelain` or fall back."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    # tolerant: fall back to repo_path when git worktree inspection fails
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("git worktree list failed for %s: %s", repo_path, exc)
        return repo_path

    if result.returncode == 0:
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                wt_str = line.removeprefix("worktree ").strip()
                if wt_str:
                    return Path(wt_str)
    return repo_path


def detect_memory_dir(repo_path: Path) -> Path | None:
    """Detect a validated project-memory directory for *repo_path*.

    Checks `hack/`, `.local/`, `scratch/`, and `.dev/` in priority order under the
    repository's main worktree, following symlinks, and returns the resolved path of
    the first directory containing at least 2 of the 5 core memory files.
    """
    main_wt = _resolve_main_worktree(repo_path)
    for candidate_name in MEMORY_CANDIDATES:
        candidate = main_wt / candidate_name
        if not candidate.is_dir():
            continue
        core_count = sum(1 for fname in CORE_MEMORY_FILES if (candidate / fname).is_file())
        if core_count >= 2:
            return candidate.resolve()
    return None
