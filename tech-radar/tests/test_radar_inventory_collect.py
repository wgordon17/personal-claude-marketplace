"""Tests for fixture_home builder and end-to-end inventory.collect (Task 4 Steps 2 & 7)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import secret_samples
from tech_radar import cli, context, http, inventory, procs

FIXED_NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)


def test_fixture_home_builds(
    fixture_home: Any,
) -> None:
    home = Path(fixture_home)
    proj_root = home / "SynologyDrive" / "ActiveProjects"
    repo_a = proj_root / "public-app"
    repo_b = proj_root / "public-app-wt"
    repo_c = proj_root / "secret-widget"
    evil_repo = proj_root / "evil-repo"

    assert repo_a.is_dir()
    assert repo_b.is_dir()
    assert repo_c.is_dir()
    assert evil_repo.is_dir()

    # git -C <repoA> log -1 succeeds
    log_res = subprocess.run(
        ["git", "-C", str(repo_a), "log", "-1"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert log_res.returncode == 0
    assert "public-app" in log_res.stdout

    # Installed-plugin fixture whose gitCommitSha resolves in the temp marketplace repo (A25)
    ip_path = home / ".claude" / "plugins" / "installed_plugins.json"
    ip_data = json.loads(ip_path.read_text(encoding="utf-8"))
    assert ip_data["version"] == 2
    rec = ip_data["plugins"]["tech-radar@personal-claude-marketplace"][0]
    sha = rec["gitCommitSha"]
    mkt_repo = home / ".claude" / "plugins" / "marketplaces" / "personal-claude-marketplace"
    ls_res = subprocess.run(
        ["git", "-C", str(mkt_repo), "ls-tree", "-r", sha, "--", "tech-radar/skills/radar/scripts"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ls_res.returncode == 0
    assert "radar.py" in ls_res.stdout

    # Shallow-missing variant writes a gitCommitSha not in the temp marketplace repo
    home_shallow = fixture_home(marketplace="shallow-missing")
    ip_shallow = json.loads(
        (home_shallow / ".claude" / "plugins" / "installed_plugins.json").read_text(
            encoding="utf-8"
        )
    )
    sha_shallow = ip_shallow["plugins"]["tech-radar@personal-claude-marketplace"][0]["gitCommitSha"]
    ls_shallow = subprocess.run(
        [
            "git",
            "-C",
            str(
                home_shallow
                / ".claude"
                / "plugins"
                / "marketplaces"
                / "personal-claude-marketplace"
            ),
            "ls-tree",
            "-r",
            sha_shallow,
            "--",
            "tech-radar/skills/radar/scripts",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ls_shallow.returncode != 0


def test_inventory_collect_baseline_diff_and_state_byte_scan(
    fixture_home: Path,
    tmp_path: Path,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
    monkeypatch: Any,
) -> None:
    _ = fixture_home
    ctx1 = context.build(
        transports={
            "http": replay_http("classify"),
            "gh": replay_gh(),
            "procs": replay_procs(),
        },
        now=FIXED_NOW,
    )

    # 1. First collect over fixture home emits 0 inventory_change signals,
    # sets inventory_baseline=True, and does NOT set kv['baseline_done']
    res1 = inventory.collect(ctx1)
    assert res1.counts["inventory_baseline"] is True
    sig_count_1 = int(
        ctx1.db.execute("SELECT COUNT(*) FROM signals WHERE type = 'inventory_change'").fetchone()[
            0
        ]
    )
    assert sig_count_1 == 0
    baseline_done = ctx1.db.execute("SELECT value FROM kv WHERE key = 'baseline_done'").fetchone()
    assert baseline_done is None

    # 2. Unchanged re-collect emits 0 signals and sets inventory_baseline=False
    ctx2 = context.build(
        transports={
            "http": replay_http("classify"),
            "gh": replay_gh(),
            "procs": replay_procs(),
        },
        now=FIXED_NOW + timedelta(hours=1),
        db_conn=ctx1.db,
        cache_conn=ctx1.cache_db,
    )
    res2 = inventory.collect(ctx2)
    assert res2.counts["inventory_baseline"] is False
    assert res2.added == []
    assert res2.removed == []
    sig_count_2 = int(
        ctx2.db.execute("SELECT COUNT(*) FROM signals WHERE type = 'inventory_change'").fetchone()[
            0
        ]
    )
    assert sig_count_2 == 0

    # 3. Removing brew formula jq from replay and re-collecting emits 1 removed signal
    base_brew = json.loads(
        (Path(__file__).resolve().parent / "fixtures" / "procs" / "brew_info.json").read_text(
            encoding="utf-8"
        )
    )
    no_jq_brew = dict(base_brew)
    no_jq_stdout = dict(base_brew["stdout"])
    no_jq_stdout["formulae"] = [f for f in base_brew["stdout"]["formulae"] if f.get("name") != "jq"]
    no_jq_brew["stdout"] = no_jq_stdout

    overlay_procs = tmp_path / "no_jq_procs"
    overlay_procs.mkdir(parents=True, exist_ok=True)
    (overlay_procs / "brew_info.json").write_text(
        json.dumps(no_jq_brew, indent=2) + "\n",
        encoding="utf-8",
    )
    (overlay_procs / "brew_leaves.json").write_text(
        json.dumps(
            {
                "argv": ["brew", "leaves"],
                "returncode": 0,
                "stderr": "",
                "stdout": "ripgrep\ngh\nuv\n",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    ctx3 = context.build(
        transports={
            "http": replay_http("classify"),
            "gh": replay_gh(),
            "procs": replay_procs(extra_roots=[overlay_procs]),
        },
        now=FIXED_NOW + timedelta(hours=2),
        db_conn=ctx1.db,
        cache_conn=ctx1.cache_db,
    )
    res3 = inventory.collect(ctx3)
    assert res3.removed == [("brew:formula/jq", "system:brew")]
    removed_sigs = ctx3.db.execute(
        "SELECT entity_id, title, payload_json FROM signals WHERE type = 'inventory_change'"
    ).fetchall()
    assert len(removed_sigs) == 1
    assert str(removed_sigs[0]["entity_id"]) == "brew:formula/jq"

    # 4. CLI `collect --inventory-only` succeeds via replay hook
    fixtures_dir = Path(__file__).resolve().parent / "fixtures"
    monkeypatch.setenv("TECH_RADAR_REPLAY_DIR", str(fixtures_dir))
    rc = cli.main(["collect", "--inventory-only"])
    assert rc == 0

    # 5. Python byte scan (A30) over state dir finds none of the injected secrets
    ctx1.db.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    canaries = secret_samples.raw_canary_substrings()
    for state_file in sorted(ctx1.paths.state.rglob("*")):
        if not state_file.is_file():
            continue
        raw_bytes = state_file.read_bytes()
        for sample_name, canary in canaries.items():
            assert canary.encode("utf-8") not in raw_bytes, (
                f"secret canary {sample_name} found in {state_file}"
            )
