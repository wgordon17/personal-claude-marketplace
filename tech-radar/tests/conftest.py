"""Pytest configuration and shared fixtures for tech-radar tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "tech-radar" / "skills" / "radar" / "scripts"
RADAR_SCRIPT = SCRIPTS_DIR / "radar.py"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture(autouse=True)
def isolated_radar_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Isolate all TECH_RADAR_* environment variables under tmp_path for every test."""
    home = tmp_path / "home"
    state = tmp_path / "state"
    run_root = tmp_path / "run_root"
    logs = tmp_path / "logs"
    sync = tmp_path / "sync"
    proj_root = home / "SynologyDrive" / "ActiveProjects"

    for directory in (home, proj_root):
        directory.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("TECH_RADAR_HOME", str(home))
    monkeypatch.setenv("TECH_RADAR_STATE", str(state))
    monkeypatch.setenv("TECH_RADAR_RUN_ROOT", str(run_root))
    monkeypatch.setenv("TECH_RADAR_LOGS", str(logs))
    monkeypatch.setenv("TECH_RADAR_SYNC", str(sync))
    monkeypatch.setenv("TECH_RADAR_PROJECT_ROOTS", str(proj_root))
    monkeypatch.delenv("TECH_RADAR_REPLAY_DIR", raising=False)
    monkeypatch.delenv("TECH_RADAR_NOTIFY_SINK", raising=False)

    return {
        "home": home,
        "state": state,
        "run_root": run_root,
        "logs": logs,
        "sync": sync,
        "project_root": proj_root,
    }
