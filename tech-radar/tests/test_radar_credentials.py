"""Tests for Keychain credentials wrapper, status/guide CLI, and non-leakage on fetch errors."""

from __future__ import annotations

import json
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tech_radar import cli, context, credentials, http, procs, sources
from tech_radar.errors import FetchError
from tech_radar.sources.base import run_source

FIXED_NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)


def _write_security_fixture(
    directory: Path,
    account: str,
    *,
    returncode: int,
    secret_val: str = "",
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "argv": [
            "security",
            "find-generic-password",
            "-s",
            "tech-radar",
            "-a",
            account,
            "-w",
        ],
        "returncode": returncode,
        "stderr": "" if returncode == 0 else "security: SecKeychainSearchCopyNext: item not found",
        "stdout": f"{secret_val}\n" if secret_val else "",
    }
    (directory / f"security_{account}_{returncode}.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def test_credentials_status_reflects_replayed_security_exit_codes(
    tmp_path: Path,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    """status reflects replayed security exit codes (missing vs present) without exposing token."""
    token_val = "ph_dev_" + ("K9m8N7b6V5c4" * 2)

    # 1. Missing (returncode=44)
    missing_dir = tmp_path / "sec_missing"
    _write_security_fixture(missing_dir, "producthunt", returncode=44)
    ctx_missing = context.build(
        transports={
            "http": replay_http(),
            "gh": replay_gh(),
            "procs": replay_procs(extra_roots=[missing_dir]),
        },
        now=FIXED_NOW,
    )
    st_missing = credentials.status(ctx_missing)
    assert st_missing["accounts"]["producthunt"] == "missing"
    assert "producthunt" in st_missing["missing"]
    assert st_missing["present"] == []

    # 2. Present (returncode=0)
    present_dir = tmp_path / "sec_present"
    _write_security_fixture(present_dir, "producthunt", returncode=0, secret_val=token_val)
    ctx_present = context.build(
        transports={
            "http": replay_http(),
            "gh": replay_gh(),
            "procs": replay_procs(extra_roots=[present_dir]),
        },
        now=FIXED_NOW,
    )
    st_present = credentials.status(ctx_present)
    assert st_present["accounts"]["producthunt"] == "present"
    assert "producthunt" in st_present["present"]
    assert st_present["missing"] == []
    assert token_val not in json.dumps(st_present)


def test_forced_exception_in_credentialed_fetch_contains_no_token_value(
    tmp_path: Path,
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    """Forced exception inside a credentialed fetch produces a traceback/DB log with no token."""
    token_val = "ph_tok_" + ("X1y2Z3w4Q5r6" * 3)
    present_dir = tmp_path / "sec_tok"
    _write_security_fixture(present_dir, "producthunt", returncode=0, secret_val=token_val)

    class _ExplodingTransport:
        def request(
            self,
            method: str,
            url: str,
            body: bytes | None,
            headers: Any,
        ) -> tuple[int, dict[str, str], bytes]:
            _ = (method, body)
            auth = dict(headers).get("Authorization", "")
            raise FetchError(f"boom while calling {url} with {auth}")

    ctx = context.build(
        transports={
            "http": _ExplodingTransport(),
            "gh": replay_gh(),
            "procs": replay_procs(extra_roots=[present_dir]),
        },
        now=FIXED_NOW,
    )

    spec = sources.get_source("producthunt")
    res = run_source(spec, ctx)
    assert res.ok is False
    assert res.error is not None
    assert token_val not in res.error

    row = ctx.db.execute(
        "SELECT last_error FROM source_health WHERE source_id = 'producthunt'"
    ).fetchone()
    assert row is not None
    assert token_val not in str(row["last_error"] or "")

    # Also test direct runner exception traceback formatting
    assert spec.runner is not None
    try:
        spec.runner(spec, ctx)
    except Exception as exc:
        tb_text = "".join(traceback.format_exception(exc))
        assert token_val not in tb_text
        assert token_val not in str(exc)


def test_credentials_guide_contains_exact_command_and_no_secret_value(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    """guide output contains no secret value and includes the security add-generic-password cmd."""
    token_val = "ph_tok_" + ("M9n8B7v6C5x4" * 3)
    present_dir = tmp_path / "sec_cli"
    _write_security_fixture(present_dir, "producthunt", returncode=0, secret_val=token_val)
    monkeypatch.setenv("TECH_RADAR_REPLAY_DIR", str(present_dir))

    exit_code = cli.main(["credentials", "guide", "producthunt"])
    assert exit_code == 0
    stdout_text = capsys.readouterr().out.strip()
    assert token_val not in stdout_text

    parsed = json.loads(stdout_text)
    assert parsed["ok"] is True
    expected_cmd = "security add-generic-password -U -s tech-radar -a producthunt -w"
    assert parsed["command"] == expected_cmd
    assert any(expected_cmd in step for step in parsed["steps"])

    status_code = cli.main(["credentials", "status"])
    assert status_code == 0
    status_out = capsys.readouterr().out.strip()
    assert token_val not in status_out
    status_parsed = json.loads(status_out)
    assert status_parsed["ok"] is True
    assert status_parsed["accounts"]["producthunt"] == "present"


def test_credentials_rejects_unsafe_account_and_skips_missing_producthunt_cleanly(
    tmp_path: Path,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    """Unsafe account names never invoke `security`, and missing credential skips Product Hunt."""
    missing_dir = tmp_path / "sec_none"
    _write_security_fixture(missing_dir, "producthunt", returncode=44)
    p_tr = replay_procs(extra_roots=[missing_dir])
    ctx = context.build(
        transports={"http": replay_http(), "gh": replay_gh(), "procs": p_tr},
        now=FIXED_NOW,
    )

    assert credentials.get(ctx, "-s") is None
    assert credentials.get(ctx, "bad account") is None
    assert credentials.get(ctx, "") is None
    assert len(p_tr.calls) == 0

    spec = sources.get_source("producthunt")
    res = run_source(spec, ctx)
    assert res.ok is True
    assert res.signals == []
    assert res.skipped_reason == "no credential"
