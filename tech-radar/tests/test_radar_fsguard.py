"""Tests for fsguard.safe_write confinement, symlink rejection, and dataless flags."""

from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from tech_radar import config, fsguard
from tech_radar.errors import WriteDenied


def test_safe_write_in_sync_dir_and_run_root_ok() -> None:
    """Writes inside sync dir and run_root/runs/x succeed and set 0600 permissions."""
    p = config.paths(ensure_dirs=True)

    sync_target = p.sync / "TECH_RADAR.md"
    written_sync = fsguard.safe_write(sync_target, "# Tech Radar\n")
    assert written_sync == sync_target.resolve()
    assert sync_target.read_text(encoding="utf-8") == "# Tech Radar\n"
    assert stat.S_IMODE(sync_target.stat().st_mode) == 0o600

    run_target = p.run_root / "runs" / "20261028T070000Z-daily" / "brief" / "context.json"
    written_run = fsguard.safe_write(run_target, b'{"ok":true}', root=p.run_root / "runs")
    assert written_run == run_target.resolve()
    assert run_target.read_bytes() == b'{"ok":true}'
    assert stat.S_IMODE(run_target.stat().st_mode) == 0o600


def test_safe_write_explicit_root_narrows_allowed_root() -> None:
    """With root=run_root/'runs', a write to {state}/app/x raises WriteDenied."""
    p = config.paths(ensure_dirs=True)
    with pytest.raises(WriteDenied):
        fsguard.safe_write(p.state / "app" / "x.txt", "denied", root=p.run_root / "runs")


def test_safe_write_rejects_traversal_and_outside_paths(tmp_path: Path) -> None:
    """../ escape or path outside allowed roots raises WriteDenied."""
    p = config.paths(ensure_dirs=True)
    outside = tmp_path / "outside.txt"

    with pytest.raises(WriteDenied):
        fsguard.safe_write(outside, "nope")

    traversal = p.run_root / "runs" / ".." / ".." / "outside_escape.txt"
    with pytest.raises(WriteDenied):
        fsguard.safe_write(traversal, "nope")


def test_safe_write_rejects_symlink_target_and_symlink_subdir(tmp_path: Path) -> None:
    """Symlink-to-outside target or symlinked subdirectory inside root raises WriteDenied."""
    p = config.paths(ensure_dirs=True)
    outside_file = tmp_path / "outside_secret.txt"
    outside_file.write_text("original", encoding="utf-8")

    # Symlinked file target inside sync dir pointing outside
    sym_target = p.sync / "evil_link.md"
    sym_target.symlink_to(outside_file)
    with pytest.raises(WriteDenied):
        fsguard.safe_write(sym_target, "overwritten")
    assert outside_file.read_text(encoding="utf-8") == "original"

    # Symlinked subdirectory inside state dir pointing outside
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    sym_dir = p.state / "evil_subdir"
    sym_dir.symlink_to(outside_dir)
    with pytest.raises(WriteDenied):
        fsguard.safe_write(sym_dir / "nested.txt", "overwritten")
    assert not (outside_dir / "nested.txt").exists()


def test_safe_write_accepts_sync_dir_reached_through_symlinked_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sync dir with a symlinked parent succeeds for safe_write and snapshot, while a symlink
    inside sync is still rejected with WriteDenied.
    """
    from tech_radar import context, snapshot

    real_active = tmp_path / "SynologyDrive" / "ActiveProjects" / "personal" / "tech-radar-data"
    real_active.mkdir(parents=True)
    projects_link = tmp_path / "Projects"
    projects_link.symlink_to(tmp_path / "SynologyDrive" / "ActiveProjects")

    via_symlink = projects_link / "personal" / "tech-radar-data"
    monkeypatch.setenv("TECH_RADAR_SYNC", str(via_symlink))

    written = fsguard.safe_write(via_symlink / "TECH_RADAR.md", "ok\n")
    assert written == (real_active / "TECH_RADAR.md").resolve()
    assert (real_active / "TECH_RADAR.md").read_text(encoding="utf-8") == "ok\n"

    # Also succeeds when writing into a new subdirectory under the symlink-parented sync root
    written_sub = fsguard.safe_write(via_symlink / "subdir" / "INVENTORY.md", "inventory content\n")
    assert written_sub == (real_active / "subdir" / "INVENTORY.md").resolve()

    # snapshot.create_snapshot succeeds when TECH_RADAR_SYNC has a symlinked parent
    ctx = context.build(now="2026-10-28T12:00:00Z")
    snap_path, snap_sha = snapshot.create_snapshot(ctx)
    assert snap_path == (real_active / "radar.snapshot.db").resolve()
    assert snap_path.is_file()
    assert len(snap_sha) == 64

    # A symlink inside sync (even pointing to another directory inside sync) is rejected
    inside_real = real_active / "inside_real"
    inside_real.mkdir()
    sublink = via_symlink / "sublink"
    sublink.symlink_to(inside_real)
    with pytest.raises(WriteDenied):
        fsguard.safe_write(via_symlink / "sublink" / "file.md", "denied")


def test_is_dataless_handles_missing_and_present_st_flags() -> None:
    """is_dataless uses getattr(st, 'st_flags', 0) for Linux compatibility (Spec 12 A30)."""
    linux_stat = SimpleNamespace(st_mode=0o100600)
    assert fsguard.is_dataless(linux_stat) is False  # type: ignore[arg-type]

    dataless_stat = SimpleNamespace(st_mode=0o100600, st_flags=fsguard.SF_DATALESS)
    assert fsguard.is_dataless(dataless_stat) is True  # type: ignore[arg-type]


def test_safe_replace_tree_rotates_prev_excludes_bytecode_and_rejects_symlinks(
    tmp_path: Path,
) -> None:
    """safe_replace_tree copies cleanly, rotates dst.prev, and rejects symlinks/out-of-root."""
    p = config.paths(ensure_dirs=True)
    src_dir = tmp_path / "src_scripts"
    (src_dir / "tech_radar").mkdir(parents=True)
    (src_dir / "__pycache__").mkdir(parents=True)
    (src_dir / "radar.py").write_text("# v1\n", encoding="utf-8")
    (src_dir / "tech_radar" / "__init__.py").write_text("# pkg v1\n", encoding="utf-8")
    (src_dir / "__pycache__" / "radar.cpython-313.pyc").write_bytes(b"bytecode")
    (src_dir / ".pytest_cache").mkdir(parents=True)
    (src_dir / ".pytest_cache" / "README.md").write_text("cache\n", encoding="utf-8")
    (src_dir / "stray.pyc").write_bytes(b"stray-pyc")
    (src_dir / ".DS_Store").write_bytes(b"dsstore")

    dst_app = p.app
    replaced = fsguard.safe_replace_tree(src_dir, dst_app, root=p.state)
    assert replaced == dst_app.resolve()
    assert (dst_app / "radar.py").read_text(encoding="utf-8") == "# v1\n"
    assert (dst_app / "tech_radar" / "__init__.py").read_text(encoding="utf-8") == "# pkg v1\n"
    assert not (dst_app / "__pycache__").exists()
    assert not (dst_app / ".pytest_cache").exists()
    assert not (dst_app / "stray.pyc").exists()
    assert not (dst_app / ".DS_Store").exists()

    # Second replace rotates app -> app.prev
    (src_dir / "radar.py").write_text("# v2\n", encoding="utf-8")
    fsguard.safe_replace_tree(src_dir, dst_app, root=p.state)
    assert (dst_app / "radar.py").read_text(encoding="utf-8") == "# v2\n"
    app_prev = p.state / "app.prev"
    assert (app_prev / "radar.py").read_text(encoding="utf-8") == "# v1\n"

    # Out-of-root destination -> WriteDenied
    with pytest.raises(WriteDenied):
        fsguard.safe_replace_tree(src_dir, tmp_path / "outside_app", root=p.state)

    # Symlink inside source tree -> WriteDenied
    outside_secret = tmp_path / "secret.txt"
    outside_secret.write_text("secret", encoding="utf-8")
    (src_dir / "evil_link.py").symlink_to(outside_secret)
    with pytest.raises(WriteDenied):
        fsguard.safe_replace_tree(src_dir, dst_app, root=p.state)
