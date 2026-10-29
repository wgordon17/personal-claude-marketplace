"""Tests for exposure.py and `radar.py exposure` CLI handler (Task 5 Step 4, Spec 07 §2, A2)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tech_radar import cli, context, exposure, http, inventory, procs

FIXED_NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)


def _sha256_tree(directory: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(directory.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(directory))
        h.update(rel.encode("utf-8"))
        h.update(p.read_bytes())
    return h.hexdigest()


def test_exposure_check_lockfile_history_pth_and_confinement(
    fixture_home: Any,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
    capsys: Any,
) -> None:
    home = fixture_home(scenario="compromised")
    proj_root = home / "SynologyDrive" / "ActiveProjects"
    repo_a = proj_root / "public-app"
    before_checksum = _sha256_tree(repo_a)

    ctx = context.build(
        transports={
            "http": replay_http("classify"),
            "gh": replay_gh(),
            "procs": replay_procs(),
        },
        now=FIXED_NOW,
    )
    inventory.collect(ctx)

    rep = exposure.check(
        ctx,
        "pkg:pypi/litellm",
        versions=["1.107.0"],
        alert_id=42,
        advisory_id="MAL-2026-9999",
        published_at="2026-10-27T10:00:00Z",
    )

    # 1. Lockfile history containing litellm==1.107.0 -> commit listed
    assert len(rep.commits) >= 1
    assert any(c["version"] == "1.107.0" and c["lockfile"] == "uv.lock" for c in rep.commits)

    # 2. Fixture venv with fresh cook.pth -> listed with sha256
    assert len(rep.pth_files) == 1
    pth_item = rep.pth_files[0]
    assert pth_item["path"].endswith("cook.pth")
    expected_pth_sha = hashlib.sha256(
        (repo_a / ".venv" / "lib" / "python3.13" / "site-packages" / "cook.pth").read_bytes()
    ).hexdigest()
    assert pth_item["sha256"] == expected_pth_sha

    # 3. Report written under {state}/exposure/ and nothing under run_root
    assert rep.report_path == ctx.paths.state / "exposure" / "42.md"
    assert rep.report_path.is_file()
    report_text = rep.report_path.read_text(encoding="utf-8")
    assert "cook.pth" in report_text
    assert expected_pth_sha in report_text
    assert rep.commits[0]["commit"] in report_text

    run_root_files = [p for p in ctx.paths.run_root.rglob("*") if p.is_file()]
    assert run_root_files == []

    # 4. Project tree checksum unchanged (read-only)
    assert _sha256_tree(repo_a) == before_checksum

    # 5. CLI `exposure --show 42` prints the report path
    capsys.readouterr()
    rc = cli.main(["exposure", "--show", "42"])
    assert rc == 0
    out_obj = json.loads(capsys.readouterr().out.strip())
    assert out_obj["ok"] is True
    assert out_obj["report_path"] == str(rep.report_path)
    assert out_obj["exists"] is True
