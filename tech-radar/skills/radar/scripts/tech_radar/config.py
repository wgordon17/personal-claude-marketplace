"""Path configuration and environment overrides for tech-radar."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

os.umask(0o077)


@dataclass(frozen=True)
class Paths:
    """Resolved filesystem paths for tech-radar state, run project, logs, and sync."""

    home: Path
    state: Path
    db: Path
    cache_db: Path
    run_root: Path
    runs: Path
    app: Path
    exposure: Path
    logs: Path
    sync: Path
    launch_agents: Path
    project_roots: tuple[Path, ...]

    @property
    def cache(self) -> Path:
        """Alias for cache_db."""
        return self.cache_db

    @property
    def runs_dir(self) -> Path:
        """Alias for runs directory."""
        return self.runs

    @property
    def app_copy(self) -> Path:
        """Alias for verified app directory."""
        return self.app

    @property
    def exposure_reports(self) -> Path:
        """Alias for exposure reports directory."""
        return self.exposure

    @property
    def sync_dir(self) -> Path:
        """Alias for sync directory."""
        return self.sync

    @property
    def radar_cmd(self) -> Path:
        """Path to RADAR_CMD file inside run_root."""
        return self.run_root / "RADAR_CMD"

    def run_dir(self, run_id: str) -> Path:
        """Return the directory for a specific run_id under runs/."""
        return self.runs / run_id


def _ensure_dir_0700(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)


def default_user_paths() -> tuple[Path, Path]:
    """Return the default real user state and run_root paths (ignoring TECH_RADAR_HOME)."""
    real_home = Path(os.path.expanduser("~")).resolve()
    return (
        (real_home / ".local" / "share" / "tech-radar").resolve(),
        (real_home / ".local" / "share" / "tech-radar-run").resolve(),
    )


def test_hooks_safe(p: Paths | None = None) -> bool:
    """Verify test-only env hooks are not active against the default installation paths."""
    if not (os.environ.get("TECH_RADAR_REPLAY_DIR") or os.environ.get("TECH_RADAR_NOTIFY_SINK")):
        return True
    if not os.environ.get("TECH_RADAR_STATE") or not os.environ.get("TECH_RADAR_RUN_ROOT"):
        return False
    resolved = p if p is not None else paths(ensure_dirs=False)
    default_state, default_run_root = default_user_paths()
    state_resolved = resolved.state.resolve()
    run_root_resolved = resolved.run_root.resolve()
    if state_resolved == default_state or state_resolved.is_relative_to(default_state):
        return False
    return not (
        run_root_resolved == default_run_root or run_root_resolved.is_relative_to(default_run_root)
    )


def paths(*, ensure_dirs: bool = True) -> Paths:
    """Resolve all tech-radar paths from environment overrides and create 0700 dirs on first use."""
    os.umask(0o077)
    raw_home = os.environ.get("TECH_RADAR_HOME")
    home = Path(raw_home).expanduser() if raw_home else Path.home()

    raw_state = os.environ.get("TECH_RADAR_STATE")
    state = Path(raw_state).expanduser() if raw_state else home / ".local" / "share" / "tech-radar"

    raw_run_root = os.environ.get("TECH_RADAR_RUN_ROOT")
    run_root = (
        Path(raw_run_root).expanduser()
        if raw_run_root
        else home / ".local" / "share" / "tech-radar-run"
    )

    raw_logs = os.environ.get("TECH_RADAR_LOGS")
    logs = Path(raw_logs).expanduser() if raw_logs else home / ".local" / "state" / "tech-radar"

    raw_sync = os.environ.get("TECH_RADAR_SYNC")
    sync = (
        Path(raw_sync).expanduser()
        if raw_sync
        else home / "SynologyDrive" / "ActiveProjects" / "personal" / "tech-radar-data"
    )

    launch_agents = home / "Library" / "LaunchAgents"

    raw_roots = os.environ.get("TECH_RADAR_PROJECT_ROOTS")
    if raw_roots is not None:
        project_roots = tuple(
            Path(part).expanduser() for part in raw_roots.split(os.pathsep) if part
        )
    else:
        project_roots = (home / "SynologyDrive" / "ActiveProjects",)

    db = state / "radar.db"
    cache_db = state / "cache.db"
    runs = run_root / "runs"
    app = state / "app"
    exposure = state / "exposure"

    if ensure_dirs:
        for directory in (state, run_root, runs, exposure, logs):
            _ensure_dir_0700(directory)
        if launch_agents.parent.is_dir():
            _ensure_dir_0700(launch_agents)

    return Paths(
        home=home,
        state=state,
        db=db,
        cache_db=cache_db,
        run_root=run_root,
        runs=runs,
        app=app,
        exposure=exposure,
        logs=logs,
        sync=sync,
        launch_agents=launch_agents,
        project_roots=project_roots,
    )
