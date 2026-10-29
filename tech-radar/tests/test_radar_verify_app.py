"""Tests for verify_app.py pinned inline verifier (Task 6 Step 4, Spec 07 §7, §9, A25, A30)."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import verify_app
from tech_radar import appsync, context, http, procs

FIXED_NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)


def _sync_clean_app(
    fixture_home: Any,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> tuple[context.Ctx, str]:
    _ = fixture_home
    ctx = context.build(
        transports={
            "http": replay_http(),
            "gh": replay_gh(),
            "procs": replay_procs(),
        },
        now=FIXED_NOW,
    )
    res = appsync.sync(ctx)
    assert res.ok is True
    assert res.manifest_sha is not None
    return ctx, res.manifest_sha


@pytest.mark.parametrize("uv_bin", ["/opt/homebrew/bin/uv", "/usr/local/bin/uv-custom"])
def test_verify_app_clean_tree_execve_args_and_env(
    fixture_home: Any,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
    uv_bin: str,
) -> None:
    """Clean tree invokes execve with exact argv and hardened environment."""
    ctx, manifest_sha = _sync_clean_app(fixture_home, replay_http, replay_gh, replay_procs)
    app_dir = ctx.paths.app

    recorded_exec: list[tuple[str, list[str], dict[str, str]]] = []
    recorded_notify: list[str] = []

    def _fake_execve(path: str, argv: list[str], env: dict[str, str]) -> None:
        recorded_exec.append((path, list(argv), dict(env)))

    code = verify_app.main(
        ["-c", manifest_sha, str(app_dir), uv_bin],
        execve=_fake_execve,
        notify=recorded_notify.append,
    )
    assert code == 0
    assert not recorded_notify
    assert len(recorded_exec) == 1

    exec_path, exec_argv, exec_env = recorded_exec[0]
    assert exec_path == uv_bin
    assert exec_argv == [
        uv_bin,
        "run",
        "--script",
        "--locked",
        "--isolated",
        "--quiet",
        str(app_dir / "radar.py"),
        "hourly",
    ]
    assert exec_env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert exec_env["UV_MALWARE_CHECK"] == "1"
    assert exec_env["TECH_RADAR_STATE"] == str(ctx.paths.state)


def test_verify_app_tampered_byte_alerts_once_and_exits_4(
    fixture_home: Any,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    """Tampering one byte in app/tech_radar/security.py exits 4 and notifies once per marker."""
    ctx, manifest_sha = _sync_clean_app(fixture_home, replay_http, replay_gh, replay_procs)
    app_dir = ctx.paths.app

    sec_py = app_dir / "tech_radar" / "security.py"
    raw = sec_py.read_bytes()
    sec_py.write_bytes(raw + b"\n# tampered\n")

    exec_calls: list[Any] = []
    notify_calls: list[str] = []

    rc1 = verify_app.main(
        ["-c", manifest_sha, str(app_dir), "/opt/homebrew/bin/uv"],
        execve=lambda p, a, e: exec_calls.append((p, a, e)),
        notify=notify_calls.append,
    )
    assert rc1 == 4
    assert not exec_calls
    assert len(notify_calls) == 1
    assert "app integrity check failed" in notify_calls[0]

    marker = ctx.paths.state / f"integrity-alerted-{manifest_sha[:16]}"
    assert marker.is_file()

    # Second invocation with the same manifest_sha -> no second notification
    rc2 = verify_app.main(
        ["-c", manifest_sha, str(app_dir), "/opt/homebrew/bin/uv"],
        execve=lambda p, a, e: exec_calls.append((p, a, e)),
        notify=notify_calls.append,
    )
    assert rc2 == 4
    assert len(notify_calls) == 1


def test_verify_app_rejects_unlisted_file_and_pycache_and_symlink_and_wrong_manifest_sha(
    fixture_home: Any,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
    tmp_path: Path,
) -> None:
    """Unlisted file, planted __pycache__/.pyc, symlink, or manifest hash mismatch all exit 4."""
    ctx, manifest_sha = _sync_clean_app(fixture_home, replay_http, replay_gh, replay_procs)
    app_dir = ctx.paths.app

    # 1. Unlisted file in app/tech_radar/evil.py -> exit 4
    evil_py = app_dir / "tech_radar" / "evil.py"
    evil_py.write_text("raise RuntimeError('pwn')\n", encoding="utf-8")
    assert (
        verify_app.main(
            ["-c", manifest_sha, str(app_dir), "/opt/homebrew/bin/uv"],
            execve=lambda *_a: None,
            notify=lambda _m: None,
        )
        == 4
    )
    evil_py.unlink()

    # 2. Planted __pycache__/radar.cpython-313.pyc -> exit 4
    pycache = app_dir / "__pycache__"
    pycache.mkdir()
    (pycache / "radar.cpython-313.pyc").write_bytes(b"pyc")
    assert (
        verify_app.main(
            ["-c", manifest_sha, str(app_dir), "/opt/homebrew/bin/uv"],
            execve=lambda *_a: None,
            notify=lambda _m: None,
        )
        == 4
    )
    (pycache / "radar.cpython-313.pyc").unlink()
    pycache.rmdir()

    # 3. Symlink planted in app/ -> exit 4
    outside = tmp_path / "outside.py"
    outside.write_text("# outside\n", encoding="utf-8")
    sym = app_dir / "symlink.py"
    sym.symlink_to(outside)
    assert (
        verify_app.main(
            ["-c", manifest_sha, str(app_dir), "/opt/homebrew/bin/uv"],
            execve=lambda *_a: None,
            notify=lambda _m: None,
        )
        == 4
    )
    sym.unlink()

    # 4. Manifest sha256 != argv[1] -> exit 4
    wrong_sha = hashlib.sha256(b"wrong").hexdigest()
    assert (
        verify_app.main(
            ["-c", wrong_sha, str(app_dir), "/opt/homebrew/bin/uv"],
            execve=lambda *_a: None,
            notify=lambda _m: None,
        )
        == 4
    )

    # 5. Missing listed file -> exit 4
    init_py = app_dir / "tech_radar" / "__init__.py"
    saved_bytes = init_py.read_bytes()
    init_py.unlink()
    assert (
        verify_app.main(
            ["-c", manifest_sha, str(app_dir), "/opt/homebrew/bin/uv"],
            execve=lambda *_a: None,
            notify=lambda _m: None,
        )
        == 4
    )
    init_py.write_bytes(saved_bytes)

    # 6. Wrong argument count -> exit 4
    assert (
        verify_app.main(
            ["only-one-arg"],
            execve=lambda *_a: None,
            notify=lambda _m: None,
        )
        == 4
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS /usr/bin/python3 CLT check (A30)")
def test_verify_app_real_usr_bin_python3_execve_fake_uv(
    fixture_home: Any,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
    tmp_path: Path,
) -> None:
    """Running /usr/bin/python3 -I -c <verify_app.py> execs the fake uv binary with exact argv."""
    ctx, manifest_sha = _sync_clean_app(fixture_home, replay_http, replay_gh, replay_procs)
    app_dir = ctx.paths.app

    argv_out = tmp_path / "recorded_uv_argv.txt"
    fake_uv = tmp_path / "fake_uv.sh"
    fake_uv.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$@" > "{argv_out}"\n',
        encoding="utf-8",
    )
    os.chmod(fake_uv, 0o755)

    verify_code = (app_dir / "verify_app.py").read_text(encoding="utf-8")
    res = subprocess.run(
        [
            "/usr/bin/python3",
            "-I",
            "-c",
            verify_code,
            manifest_sha,
            str(app_dir),
            str(fake_uv),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert argv_out.is_file()
    lines = argv_out.read_text(encoding="utf-8").splitlines()
    assert lines == [
        "run",
        "--script",
        "--locked",
        "--isolated",
        "--quiet",
        str(app_dir / "radar.py"),
        "hourly",
    ]
