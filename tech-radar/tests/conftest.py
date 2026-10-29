"""Pytest configuration and shared fixtures for tech-radar tests."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "tech-radar" / "skills" / "radar" / "scripts"
RADAR_SCRIPT = SCRIPTS_DIR / "radar.py"
TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"

for _p in (str(SCRIPTS_DIR), str(TESTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import fixture_home as fixture_home_mod  # noqa: E402
from tech_radar import http, procs  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def warm_uv_script_cache() -> bool:
    """Warm the uv script environment for S/radar.py once per test session (Spec 11 §1)."""
    env = {k: v for k, v in os.environ.items() if k != "UV_FROZEN"}
    res = subprocess.run(
        ["uv", "run", "--script", "--locked", "--isolated", str(RADAR_SCRIPT), "--help"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return res.returncode == 0


@pytest.fixture(autouse=True)
def isolated_radar_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Isolate all TECH_RADAR_* environment variables under tmp_path for every test."""
    home = tmp_path / "home"
    state = tmp_path / "state"
    run_root = tmp_path / "run_root"
    logs = tmp_path / "logs"
    sync = tmp_path / "sync"
    proj_root = home / "SynologyDrive" / "ActiveProjects"

    for directory in (home, proj_root, sync):
        directory.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("TECH_RADAR_HOME", str(home))
    monkeypatch.setenv("TECH_RADAR_STATE", str(state))
    monkeypatch.setenv("TECH_RADAR_RUN_ROOT", str(run_root))
    monkeypatch.setenv("TECH_RADAR_LOGS", str(logs))
    monkeypatch.setenv("TECH_RADAR_SYNC", str(sync))
    monkeypatch.setenv("TECH_RADAR_PROJECT_ROOTS", str(proj_root))
    monkeypatch.delenv("TECH_RADAR_REPLAY_DIR", raising=False)
    monkeypatch.delenv("TECH_RADAR_NOTIFY_SINK", raising=False)
    monkeypatch.delenv("UV_FROZEN", raising=False)

    return {
        "home": home,
        "state": state,
        "run_root": run_root,
        "logs": logs,
        "sync": sync,
        "project_root": proj_root,
    }


@pytest.fixture
def replay_http() -> Callable[..., http.ReplayHttp]:
    """Return a factory building ReplayHttp with optional source_id and scenario overlay."""

    def _build(
        source_id: str | None = None,
        *,
        scenario: str | None = None,
        extra_roots: list[Path] | None = None,
    ) -> http.ReplayHttp:
        roots: list[Path] = []
        if extra_roots:
            roots.extend(extra_roots)
        if scenario:
            roots.append(FIXTURES_DIR / "scenarios" / scenario / "http")
        if source_id:
            roots.append(FIXTURES_DIR / "http" / source_id)
        roots.append(FIXTURES_DIR / "http")
        return http.ReplayHttp(roots)

    return _build


@pytest.fixture
def replay_gh() -> Callable[..., procs.ReplayProcs]:
    """Return a factory building ReplayProcs scoped to tests/fixtures/procs/gh/."""

    def _build(
        *,
        scenario: str | None = None,
        extra_roots: list[Path] | None = None,
    ) -> procs.ReplayProcs:
        roots: list[Path] = []
        if extra_roots:
            roots.extend(extra_roots)
        if scenario:
            roots.append(FIXTURES_DIR / "scenarios" / scenario / "procs")
        roots.append(FIXTURES_DIR / "procs" / "gh")
        roots.append(FIXTURES_DIR / "procs")
        return procs.ReplayProcs(roots)

    return _build


@pytest.fixture
def replay_procs() -> Callable[..., procs.ReplayProcs]:
    """Return a factory building ReplayProcs with scenario overlays and git passthrough."""

    def _build(
        *,
        scenario: str | None = None,
        extra_roots: list[Path] | None = None,
        passthrough: tuple[str, ...] = ("git",),
    ) -> procs.ReplayProcs:
        roots: list[Path] = []
        if extra_roots:
            roots.extend(extra_roots)
        if scenario:
            roots.append(FIXTURES_DIR / "scenarios" / scenario / "procs")
        roots.append(FIXTURES_DIR / "procs")
        return procs.ReplayProcs(roots, passthrough=passthrough)

    return _build


class CallableFixtureHome(type(Path())):
    """Path subclass that can also be called as fixture_home(scenario=..., marketplace=...)."""

    _tmp_path: Path

    def __call__(
        self,
        scenario: str = "baseline",
        *,
        marketplace: str = "present",
    ) -> Path:
        return fixture_home_mod.build(
            self._tmp_path,
            scenario=scenario,
            marketplace=marketplace,
        )


@pytest.fixture
def fixture_home(tmp_path: Path) -> CallableFixtureHome:
    """Materialise the synthetic fixture home and return a Path (also callable for scenarios)."""
    built = fixture_home_mod.build(tmp_path, scenario="baseline")
    wrapped = CallableFixtureHome(built)
    wrapped._tmp_path = tmp_path
    return wrapped
