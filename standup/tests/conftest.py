"""Shared pytest fixtures for standup-tool tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def cfg_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an isolated 0700 config directory and point STANDUP_CONFIG_DIR at it."""
    d = tmp_path / "cfg"
    d.mkdir(mode=0o700, exist_ok=True)
    d.chmod(0o700)
    monkeypatch.setenv("STANDUP_CONFIG_DIR", str(d))
    return d
