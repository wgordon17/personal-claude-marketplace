"""Tests for the shared project-memory resolver in dev-guard/hooks/memory_paths.py."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from memory_paths import find_main_worktree, find_memory_dir  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(var, raising=False)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=d@example.com", "-c", "user.name=Dev", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _repo_with_commit(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "commit", "-q", "--allow-empty", "-m", "init")
    return path


def test_linked_worktree_resolves_to_main_checkout(tmp_path: Path) -> None:
    main = _repo_with_commit(tmp_path / "main")
    linked = tmp_path / "linked"
    _git(main, "worktree", "add", "-q", "-b", "feature", str(linked))
    assert find_main_worktree(linked).resolve() == main.resolve()


def test_bare_clone_layout_skips_the_bare_entry(tmp_path: Path) -> None:
    source = _repo_with_commit(tmp_path / "source")
    bare = tmp_path / "proj" / ".bare"
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(source), str(bare)], check=True, capture_output=True
    )
    checkout = tmp_path / "proj" / "main"
    _git(bare, "worktree", "add", "-q", str(checkout), "main")
    assert find_main_worktree(checkout).resolve() == checkout.resolve()


def test_non_git_directory_falls_back_to_start(tmp_path: Path) -> None:
    assert find_main_worktree(tmp_path) == tmp_path


def test_missing_git_binary_falls_back_to_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "no-bin"))
    assert find_main_worktree(tmp_path) == tmp_path


def test_find_memory_dir_requires_two_core_files(tmp_path: Path) -> None:
    hack = tmp_path / "hack"
    hack.mkdir()
    (hack / "PROJECT.md").write_text("x")
    assert find_memory_dir(tmp_path) is None
    (hack / "TODO.md").write_text("x")
    assert find_memory_dir(tmp_path) == hack


def test_find_memory_dir_honours_candidate_priority(tmp_path: Path) -> None:
    for name in (".dev", "hack"):
        d = tmp_path / name
        d.mkdir()
        (d / "PROJECT.md").write_text("x")
        (d / "NEXT.md").write_text("x")
    assert find_memory_dir(tmp_path) == tmp_path / "hack"
