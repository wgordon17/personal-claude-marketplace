"""Repository discovery from validated standup configuration."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from standup_tool.config import Config

_SLUG_RE = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9_.-]*/[a-zA-Z0-9_][a-zA-Z0-9_.-]*$")


@dataclass
class RepoInfo:
    """Resolved repository metadata for collectors."""

    name: str
    path: Path
    upstream_slug: str


def _parse_slug_from_url(url: str) -> str:
    url = url.strip()
    if url.startswith("git@"):
        colon_part = url.split(":", 1)[-1]
        slug = colon_part.removesuffix(".git")
        if _SLUG_RE.match(slug):
            return slug
    elif url.startswith(("https://", "http://")):
        path_part = url.split("/", 3)[-1]
        slug = path_part.removesuffix(".git")
        if _SLUG_RE.match(slug):
            return slug
    return ""


def _parse_remote_slug(repo_path: Path, remote: str) -> str:
    """Return 'org/repo' slug parsed from `git remote get-url <remote>`, or ''."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", remote],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return _parse_slug_from_url(result.stdout)


def _parse_upstream_slug(repo_path: Path) -> str:
    """Return 'org/repo' slug trying `upstream` remote first, then `origin`, else ''."""
    slug = _parse_remote_slug(repo_path, "upstream")
    if slug:
        return slug
    return _parse_remote_slug(repo_path, "origin")


def list_repos(config: Config) -> list[RepoInfo]:
    """Return RepoInfo for configured repos plus 1-level child fork clones matching github.org."""
    repos: list[RepoInfo] = []
    seen: set[Path] = set()
    org_prefix = f"{config.github.org}/"

    for entry in config.repos:
        path = entry.path
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            upstream_slug = _parse_upstream_slug(path) if path.is_dir() else ""
            repos.append(RepoInfo(name=path.name, path=path, upstream_slug=upstream_slug))

        if not path.is_dir():
            continue

        for child in sorted(path.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if not (child / ".git").exists():
                continue
            child_resolved = child.resolve()
            if child_resolved in seen:
                continue
            child_slug = _parse_remote_slug(child, "upstream")
            if child_slug.startswith(org_prefix):
                seen.add(child_resolved)
                repos.append(RepoInfo(name=child.name, path=child, upstream_slug=child_slug))

    return repos
