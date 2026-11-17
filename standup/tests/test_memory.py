"""Tests for standup_tool.memory."""

from __future__ import annotations

import subprocess
from pathlib import Path

from standup_tool.memory import detect_memory_dir


def _init_git_repo(repo_dir: Path) -> Path:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "ada@example.com"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Ada Example"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    return repo_dir


def test_detect_memory_dir_returns_none_when_no_candidates(tmp_path: Path):
    repo = _init_git_repo(tmp_path / "repo")
    assert detect_memory_dir(repo) is None


def test_detect_memory_dir_rejects_candidate_with_one_core_file(tmp_path: Path):
    repo = _init_git_repo(tmp_path / "repo")
    hack = repo / "hack"
    hack.mkdir()
    (hack / "PROJECT.md").write_text("# Project\n", encoding="utf-8")
    assert detect_memory_dir(repo) is None


def test_detect_memory_dir_rejects_hack_with_build_scripts_only(tmp_path: Path):
    repo = _init_git_repo(tmp_path / "repo")
    hack = repo / "hack"
    hack.mkdir()
    (hack / "build.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (hack / "release.py").write_text("pass\n", encoding="utf-8")
    assert detect_memory_dir(repo) is None


def test_detect_memory_dir_accepts_dev_symlink_to_external_dir(tmp_path: Path):
    repo = _init_git_repo(tmp_path / "repo")
    external = tmp_path / "external_memory"
    external.mkdir()
    (external / "PROJECT.md").write_text("# Project\n", encoding="utf-8")
    (external / "TODO.md").write_text("# TODO\n", encoding="utf-8")

    (repo / ".dev").symlink_to(external)
    result = detect_memory_dir(repo)
    assert result == external.resolve()


def test_detect_memory_dir_resolves_linked_worktree_to_main_worktree(tmp_path: Path):
    main_repo = _init_git_repo(tmp_path / "main-repo")
    (main_repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=main_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=main_repo,
        check=True,
        capture_output=True,
    )

    hack = main_repo / "hack"
    hack.mkdir()
    (hack / "PROJECT.md").write_text("# Project\n", encoding="utf-8")
    (hack / "SESSIONS.md").write_text("# Sessions\n", encoding="utf-8")

    linked_wt = tmp_path / "linked-wt"
    subprocess.run(
        ["git", "worktree", "add", "-b", "feat/branch", str(linked_wt)],
        cwd=main_repo,
        check=True,
        capture_output=True,
    )

    assert detect_memory_dir(linked_wt) == hack.resolve()


def test_detect_memory_dir_in_bare_clone_layout_uses_the_checkout_not_the_bare_dir(
    tmp_path: Path,
):
    source = _init_git_repo(tmp_path / "source")
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "initial"],
        cwd=source,
        check=True,
        capture_output=True,
    )
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    bare = tmp_path / "proj" / ".bare"
    subprocess.run(
        ["git", "clone", "--bare", str(source), str(bare)], check=True, capture_output=True
    )
    checkout = tmp_path / "proj" / "main"
    subprocess.run(
        ["git", "worktree", "add", str(checkout), branch],
        cwd=bare,
        check=True,
        capture_output=True,
    )
    hack = checkout / "hack"
    hack.mkdir()
    (hack / "PROJECT.md").write_text("# Project\n", encoding="utf-8")
    (hack / "TODO.md").write_text("# TODO\n", encoding="utf-8")

    assert detect_memory_dir(checkout) == hack.resolve()


def test_detect_memory_dir_respects_candidate_priority(tmp_path: Path):
    repo = _init_git_repo(tmp_path / "repo")
    for cand in ("hack", ".dev"):
        d = repo / cand
        d.mkdir()
        (d / "PROJECT.md").write_text("# Project\n", encoding="utf-8")
        (d / "NEXT.md").write_text("# Next\n", encoding="utf-8")

    assert detect_memory_dir(repo) == (repo / "hack").resolve()


def test_detect_memory_dir_falls_back_to_repo_path_when_git_fails(tmp_path: Path):
    plain_dir = tmp_path / "not-a-git-repo"
    scratch = plain_dir / "scratch"
    scratch.mkdir(parents=True)
    (scratch / "LESSONS.md").write_text("# Lessons\n", encoding="utf-8")
    (scratch / "NEXT.md").write_text("# Next\n", encoding="utf-8")

    assert detect_memory_dir(plain_dir) == scratch.resolve()
