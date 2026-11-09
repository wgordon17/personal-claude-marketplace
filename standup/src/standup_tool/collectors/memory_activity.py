"""Collect project-memory plans and research files modified in the date range."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

from standup_tool.memory import detect_memory_dir
from standup_tool.workspace import RepoInfo

logger = logging.getLogger(__name__)

_CATEGORIES: tuple[str, ...] = ("plans", "research")
_EXCLUDED_SEGMENTS: frozenset[str] = frozenset({"done", "archive"})


def _extract_title(md_file: Path) -> str:
    """Return the first `# ` heading in *md_file*, or its filename if none exists."""
    try:
        text = md_file.read_text(encoding="utf-8", errors="replace")
    # tolerant: fall back to filename when memory markdown file cannot be read
    except OSError as exc:
        logger.debug("Failed to read memory file %s: %s", md_file, exc)
        return md_file.name

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            heading = stripped[2:].strip()
            if heading:
                return heading
    return md_file.name


def _iter_category_files(memory_dir: Path, category: str) -> list[Path]:
    """Yield markdown files in `<memory_dir>/<category>` and `<memory_dir>/*/<category>`."""
    candidates: list[Path] = []
    top_dir = memory_dir / category
    if top_dir.is_dir():
        candidates.extend(sorted(top_dir.glob("*.md")))

    for child in sorted(memory_dir.iterdir()):
        if not child.is_dir() or child.name in _EXCLUDED_SEGMENTS or child.name in _CATEGORIES:
            continue
        nested_dir = child / category
        if nested_dir.is_dir():
            candidates.extend(sorted(nested_dir.glob("*.md")))

    return candidates


def collect(
    repos: Sequence[RepoInfo],
    since: date,
    until: date,
) -> list[dict]:
    """Scan validated project-memory directories across *repos* for recent plans/research.

    Returns a list of dicts:
    `{repo, memory_dir, category, path, modified, title}`
    """
    items: list[dict] = []
    seen_files: set[Path] = set()

    for repo in repos:
        mem_dir = detect_memory_dir(repo.path)
        if mem_dir is None or not mem_dir.is_dir():
            continue

        for category in _CATEGORIES:
            for md_file in _iter_category_files(mem_dir, category):
                rel_parts = md_file.relative_to(mem_dir).parts
                if any(part in _EXCLUDED_SEGMENTS for part in rel_parts):
                    continue
                resolved_file = md_file.resolve()
                if resolved_file in seen_files:
                    continue
                try:
                    mtime = md_file.stat().st_mtime
                # tolerant: skip single memory markdown file whose stat call fails
                except OSError as exc:
                    logger.debug("Skipping unreadable memory file %s: %s", md_file, exc)
                    continue

                mod_date = datetime.fromtimestamp(mtime).date()
                if not (since <= mod_date <= until):
                    continue

                seen_files.add(resolved_file)
                items.append(
                    {
                        "repo": repo.name,
                        "memory_dir": str(mem_dir),
                        "category": category,
                        "path": str(md_file.relative_to(mem_dir)),
                        "modified": mod_date.isoformat(),
                        "title": _extract_title(md_file),
                    }
                )

    return items
