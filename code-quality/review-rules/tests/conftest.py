"""Shared pytest configuration and fixtures for review-rules tests."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

_VENDORED_SCHEMA = (
    Path(__file__).resolve().parent.parent / "src" / "review_rules" / "schema" / "rule.schema.json"
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip @pytest.mark.timing tests when CI is set."""
    _ = config
    if not os.environ.get("CI"):
        return
    skip_timing = pytest.mark.skip(reason="timing tests skipped when CI is set")
    for item in items:
        if item.get_closest_marker("timing") is not None:
            item.add_marker(skip_timing)


@pytest.fixture(autouse=True)
def _restore_umask() -> Iterator[None]:
    """Ensure every test starts with 0o022 umask and restores prior process umask."""
    prev = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(prev)


@pytest.fixture(autouse=True)
def _isolated_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate temp-repo git commands from global/system git config and inherited git env."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(var, raising=False)


@dataclass
class RulesRepo:
    """Helper wrapper around a temp rules git repository and its bare origin remote."""

    path: Path
    origin: Path

    def __fspath__(self) -> str:
        return str(self.path)

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-C", str(self.path), *args],
            check=check,
            capture_output=True,
            text=True,
        )

    def commit_file(
        self,
        rel_path: str,
        content: str | bytes,
        *,
        push: bool = True,
        message: str = "update rule",
    ) -> str:
        target = self.path / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
        self.git("add", rel_path)
        self.git("commit", "-m", message)
        if push:
            self.git("push", "origin", "main")
        return self.head_sha()

    def head_sha(self) -> str:
        return self.git("rev-parse", "HEAD").stdout.strip()

    def origin_sha(self) -> str:
        return self.git("rev-parse", "refs/remotes/origin/main").stdout.strip()


@pytest.fixture
def rules_repo(tmp_path: Path) -> RulesRepo:
    """Create a temp git repo with bare origin main and vendored schema/rule.schema.json."""
    origin = tmp_path / "origin.git"
    repo = tmp_path / "rules-repo"

    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)],
        check=True,
        capture_output=True,
        text=True,
    )
    helper = RulesRepo(path=repo, origin=origin)
    helper.git("config", "user.email", "test@example.com")
    helper.git("config", "user.name", "Test User")
    helper.git("remote", "add", "origin", str(origin))

    schema_dst = repo / "schema" / "rule.schema.json"
    schema_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_VENDORED_SCHEMA, schema_dst)

    gitkeep = repo / "rules" / ".gitkeep"
    gitkeep.parent.mkdir(parents=True, exist_ok=True)
    gitkeep.write_text("", encoding="utf-8")

    helper.git("add", "schema/rule.schema.json", "rules/.gitkeep")
    helper.git("commit", "-m", "init schema")
    helper.git("push", "-u", "origin", "main")
    return helper
