"""Tests for standup_tool.collectors.memory_activity."""

from __future__ import annotations

import os
import subprocess
from datetime import date, datetime
from pathlib import Path

from standup_tool.collectors.memory_activity import collect
from standup_tool.workspace import RepoInfo


def _make_repo_with_memory(tmp_path: Path, name: str = "widgets") -> tuple[RepoInfo, Path]:
    repo_dir = tmp_path / name
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    hack = repo_dir / "hack"
    hack.mkdir(exist_ok=True)
    (hack / "PROJECT.md").write_text("# Project\n", encoding="utf-8")
    (hack / "TODO.md").write_text("# TODO\n", encoding="utf-8")
    return RepoInfo(name=name, path=repo_dir, upstream_slug=f"acme/{name}"), hack.resolve()


def _touch_at(path: Path, content: str, dt: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    ts = dt.timestamp()
    os.utime(path, (ts, ts))


def test_collect_returns_empty_when_repo_has_no_memory_dir(tmp_path: Path):
    repo_dir = tmp_path / "plain"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    repo = RepoInfo(name="plain", path=repo_dir, upstream_slug="acme/plain")

    assert collect([repo], date(2026, 5, 1), date(2026, 5, 5)) == []


def test_collect_scans_top_level_and_component_plans_and_research(tmp_path: Path):
    repo, mem_dir = _make_repo_with_memory(tmp_path, "widgets")
    in_window = datetime(2026, 5, 4, 12, 0, 0)

    _touch_at(
        mem_dir / "plans" / "plan-a.md",
        "Intro text\n# ACME-101 Widget Plan\nDetails\n",
        in_window,
    )
    _touch_at(
        mem_dir / "research" / "spike-b.md",
        "No heading here, only body text.\n",
        in_window,
    )
    _touch_at(
        mem_dir / "subcomp" / "plans" / "nested-plan.md",
        "# Nested Component Plan\n",
        in_window,
    )
    _touch_at(
        mem_dir / "subcomp" / "research" / "nested-research.md",
        "# Nested Research\n",
        in_window,
    )

    items = collect([repo], date(2026, 5, 4), date(2026, 5, 4))
    by_title = {item["title"]: item for item in items}

    assert set(by_title.keys()) == {
        "ACME-101 Widget Plan",
        "spike-b.md",
        "Nested Component Plan",
        "Nested Research",
    }

    plan_item = by_title["ACME-101 Widget Plan"]
    assert plan_item["repo"] == "widgets"
    assert plan_item["memory_dir"] == str(mem_dir)
    assert plan_item["category"] == "plans"
    assert plan_item["modified"] == "2026-05-04"
    assert "plan-a.md" in plan_item["path"]

    spike_item = by_title["spike-b.md"]
    assert spike_item["category"] == "research"


def test_collect_excludes_done_and_archive_segments_and_out_of_window_files(tmp_path: Path):
    repo, mem_dir = _make_repo_with_memory(tmp_path, "widgets")
    in_window = datetime(2026, 5, 4, 12, 0, 0)
    too_old = datetime(2026, 4, 20, 12, 0, 0)
    too_new = datetime(2026, 5, 10, 12, 0, 0)

    _touch_at(mem_dir / "plans" / "active.md", "# Active Plan\n", in_window)
    _touch_at(mem_dir / "done" / "plans" / "finished.md", "# Done Plan\n", in_window)
    _touch_at(mem_dir / "archive" / "research" / "archived.md", "# Archived Research\n", in_window)
    _touch_at(mem_dir / "plans" / "old.md", "# Old Plan\n", too_old)
    _touch_at(mem_dir / "plans" / "future.md", "# Future Plan\n", too_new)

    items = collect([repo], date(2026, 5, 1), date(2026, 5, 5))
    assert len(items) == 1
    assert items[0]["title"] == "Active Plan"


def test_collect_deduplicates_shared_memory_dir_and_skips_empty_h1(tmp_path: Path):
    repo_a, mem_dir = _make_repo_with_memory(tmp_path, "widgets")
    repo_b_dir = tmp_path / "widgets-wt"
    repo_b_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_b_dir, check=True, capture_output=True)
    (repo_b_dir / ".dev").symlink_to(mem_dir)
    repo_b = RepoInfo(name="widgets-wt", path=repo_b_dir, upstream_slug="acme/widgets-wt")

    in_window = datetime(2026, 5, 4, 12, 0, 0)
    _touch_at(
        mem_dir / "plans" / "shared.md",
        "# \n# Actual Shared Title\n",
        in_window,
    )

    items = collect([repo_a, repo_b], date(2026, 5, 1), date(2026, 5, 5))
    assert len(items) == 1
    assert items[0]["title"] == "Actual Shared Title"
    assert items[0]["repo"] == "widgets"
