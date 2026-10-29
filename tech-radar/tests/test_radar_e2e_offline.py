"""Offline end-to-end subprocess test (no LLM) and secrets-canary negative control."""

from __future__ import annotations

import json
import os
import pty
import select
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest
import secret_samples
from tech_radar import context, procs, redact, render, runlog

REPO_ROOT = Path(__file__).resolve().parents[2]
RADAR_SCRIPT = REPO_ROOT / "tech-radar" / "skills" / "radar" / "scripts" / "radar.py"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
E2E_SCENARIO_DIR = FIXTURES_DIR / "scenarios" / "e2e"


def _run_pty_subprocess(cmd: list[str], env: dict[str, str]) -> tuple[int, str]:
    master_fd, slave_fd = pty.openpty()
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=subprocess.PIPE,
            env=env,
            close_fds=True,
        )
    finally:
        os.close(slave_fd)

    chunks: list[bytes] = []
    try:
        while True:
            rlist, _, _ = select.select([master_fd], [], [], 0.1)
            if rlist:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                chunks.append(data)
            elif proc.poll() is not None:
                break
        proc.wait(timeout=60)
    finally:
        os.close(master_fd)
        if proc.stderr is not None:
            proc.stderr.close()

    return proc.returncode, b"".join(chunks).decode("utf-8", errors="replace")


def _run_pipe_subprocess(cmd: list[str], env: dict[str, str]) -> tuple[int, str, str]:
    res = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return res.returncode, res.stdout, res.stderr


def _extract_latest_confirm_code(sink_file: Path) -> str:
    lines = [ln.strip() for ln in sink_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    code_lines = [ln for ln in lines if "tech-radar code " in ln]
    assert code_lines, f"No confirmation code notification found in sink: {lines}"
    latest = code_lines[-1]
    return latest.split("tech-radar code ", 1)[1].split(":", 1)[0].strip()


def _byte_scan_paths(
    needles: list[bytes],
    roots: list[Path],
    *,
    exclude_files: set[Path] | None = None,
) -> list[tuple[Path, bytes]]:
    excluded = {p.resolve() for p in (exclude_files or set())}
    hits: list[tuple[Path, bytes]] = []
    for root in roots:
        if not root.exists():
            continue
        files = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
        for fpath in files:
            if fpath.resolve() in excluded:
                continue
            data = fpath.read_bytes()
            for needle in needles:
                if needle and needle in data:
                    hits.append((fpath, needle))
    return hits


def test_offline_e2e_subprocess_pipeline_and_canary_negative_control(
    fixture_home: Any,
    warm_uv_script_cache: bool,
    isolated_radar_env: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Offline end-to-end subprocess pipeline (`UV_OFFLINE=1`, `TECH_RADAR_REPLAY_DIR`,
    `TECH_RADAR_NOTIFY_SINK`, `fixture_home(scenario='compromised')`):
      bootstrap init --channel cli (PTY)
      -> bootstrap apply --channel cli (PTY)
      -> bootstrap lockdown --channel cli (PTY)
      -> plan-run (pipe)
      -> collect (pipe)
      -> write verdicts.json -> verdict apply (pipe)
      -> render (pipe)
      -> decide-pending R-0001 hold (pipe)
      -> request-confirm (pipe)
      -> confirm <code from sink> (pipe)
    Asserts:
      - digest has Security with litellm compromise alert
      - DECISIONS.md written, snapshot present, lock released
      - confirmed decision applied with channel='thread'
      - every stdout is one JSON line and none contains the confirmation code
      - Python byte scan of state, run_root, sync, logs, and digests table finds zero
        injected secrets, zero confirmation codes, and zero private names under run_root
      - negative control with redact.scrub monkeypatched to identity DOES find injected secrets
    """
    if not warm_uv_script_cache:
        pytest.skip("uv script cache warm-up unavailable on cold machine")

    fixture_home(scenario="compromised")
    state_dir = isolated_radar_env["state"]
    run_root = isolated_radar_env["run_root"]
    sync_dir = isolated_radar_env["sync"]
    logs_dir = isolated_radar_env["logs"]
    sink_file = tmp_path / "e2e_notify_sink.txt"

    replay_dir_val = f"{E2E_SCENARIO_DIR}{os.pathsep}{FIXTURES_DIR}"
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["UV_OFFLINE"] = "1"
    env["UV_MALWARE_CHECK"] = "0"
    env["TECH_RADAR_REPLAY_DIR"] = replay_dir_val
    env["TECH_RADAR_NOTIFY_SINK"] = str(sink_file)

    uv_bin = procs.resolve("uv", force_live=True) or "uv"
    base_cmd = [uv_bin, "run", "--script", "--locked", "--isolated", str(RADAR_SCRIPT)]

    all_stdouts: list[str] = []

    def _assert_single_json_line(raw_out: str, step_name: str) -> dict[str, Any]:
        lines = [ln.strip() for ln in raw_out.splitlines() if ln.strip()]
        assert len(lines) == 1, f"{step_name}: expected 1 JSON line, got {raw_out!r}"
        all_stdouts.append(lines[0])
        return json.loads(lines[0])

    # 1. bootstrap init --channel cli (PTY)
    rc, out = _run_pty_subprocess([*base_cmd, "bootstrap", "init", "--channel", "cli"], env)
    assert rc == 0, f"bootstrap init failed ({rc}): {out}"
    init_res = _assert_single_json_line(out, "bootstrap init")
    assert init_res["ok"] is True

    # 2. bootstrap apply --channel cli (PTY)
    answers_path = run_root / "runs" / "bootstrap" / "answers.json"
    answers_obj = {
        "languages": ["python", "typescript"],
        "categories": ["Python tooling", "MCP servers", "cli-utilities"],
        "anti_interests": ["agent observability and cost tracking", "local inference"],
        "preferences": ["MIT or Apache-2.0 license", "deterministic JSON CLI"],
        "taste_makers": ["simonw"],
        "watchlist": ["astral-sh/ruff"],
        "initial_rings": [
            {"entity_id": "brew:formula/jq", "ring": "adopt", "reason": "Core JSON CLI"}
        ],
        "subscriptions": ["claude-max"],
    }
    answers_path.write_text(json.dumps(answers_obj, indent=2), encoding="utf-8")
    rc, out = _run_pty_subprocess(
        [*base_cmd, "bootstrap", "apply", "--answers", str(answers_path), "--channel", "cli"],
        env,
    )
    assert rc == 0, f"bootstrap apply failed ({rc}): {out}"
    apply_res = _assert_single_json_line(out, "bootstrap apply")
    assert apply_res["ok"] is True
    assert not answers_path.exists()

    # 3. bootstrap lockdown --channel cli (PTY)
    rc, out = _run_pty_subprocess([*base_cmd, "bootstrap", "lockdown", "--channel", "cli"], env)
    assert rc == 0, f"bootstrap lockdown failed ({rc}): {out}"
    lock_res = _assert_single_json_line(out, "bootstrap lockdown")
    assert lock_res["ok"] is True

    # 4. plan-run (pipe)
    rc, out, err = _run_pipe_subprocess([*base_cmd, "plan-run", "--requested", "daily"], env)
    assert rc == 0, f"plan-run failed ({rc}): {out} / {err}"
    plan_res = _assert_single_json_line(out, "plan-run")
    assert plan_res["ok"] is True
    assert isinstance(plan_res["run_id"], str)
    run_id = str(plan_res["run_id"])
    assert (state_dir / "run.lock").is_file()

    # 5. collect --run <run_id> (pipe; exit 0 or 3 degraded)
    rc, out, err = _run_pipe_subprocess([*base_cmd, "collect", "--run", run_id], env)
    assert rc in (0, 3), f"collect failed ({rc}): {out} / {err}"
    collect_res = _assert_single_json_line(out, "collect")
    assert collect_res["ok"] is True
    assert isinstance(collect_res["security_alerts"], int | float)
    assert collect_res["security_alerts"] >= 1

    # 6. Write fixture verdicts.json -> verdict apply --run <run_id> (pipe)
    run_dir = run_root / "runs" / run_id
    verdicts_payload = {
        "run_id": run_id,
        "items": [
            {
                "ref": "R-0001",
                "action": "watch",
                "ring": "adopt",
                "quadrant": "cli-dev-tools",
                "fit": 82,
                "quality_adj": 5,
                "confidence": "high",
                "why": "Rotation review of active tool in context.json.",
                "superiority": None,
                "benefit": "Reliable CLI processing.",
                "replaces": None,
                "relevant_to": ["brew:formula/jq"],
                "technique_slug": None,
                "citations": ["https://github.com/jqlang/jq/releases/tag/jq-1.7.1"],
                "adoption_snippet": None,
                "effort": "S",
                "llm_fields": {
                    "cloud_code_upload": "no",
                    "arbitrary_shell": "no",
                    "broad_credentials": "no",
                    "telemetry_no_optout": "no",
                },
                "section": "rotation",
            }
        ],
    }
    (run_dir / "verdicts.json").write_text(
        json.dumps(verdicts_payload, indent=2) + "\n",
        encoding="utf-8",
    )
    rc, out, err = _run_pipe_subprocess([*base_cmd, "verdict", "apply", "--run", run_id], env)
    assert rc == 0, f"verdict apply failed ({rc}): {out} / {err}"
    verdict_res = _assert_single_json_line(out, "verdict apply")
    assert verdict_res["ok"] is True

    # 7. render --run <run_id> (pipe)
    rc, out, err = _run_pipe_subprocess([*base_cmd, "render", "--run", run_id], env)
    assert rc == 0, f"render failed ({rc}): {out} / {err}"
    render_res = _assert_single_json_line(out, "render")
    assert render_res["ok"] is True
    assert not (state_dir / "run.lock").exists(), "run.lock must be released by render"

    digest_path = run_dir / "digest.md"
    assert digest_path.is_file()
    digest_text = digest_path.read_text(encoding="utf-8")
    assert "## Security" in digest_text
    assert "litellm" in digest_text
    assert "compromise" in digest_text.lower() or "MAL-2026-9999" in digest_text
    assert (sync_dir / "DECISIONS.md").is_file()
    assert (sync_dir / "TECH_RADAR.md").is_file()
    assert (sync_dir / "INVENTORY.md").is_file()
    assert (sync_dir / "project-alerts.json").is_file()
    assert (sync_dir / "radar.snapshot.db").is_file()
    tech_radar_md = (sync_dir / "TECH_RADAR.md").read_text(encoding="utf-8")
    assert "secret-widget" not in tech_radar_md
    assert "example-org/secret-widget" not in tech_radar_md

    # 8. decide-pending R-0001 hold -> request-confirm -> confirm <code> (pipe)
    rc, out, err = _run_pipe_subprocess(
        [*base_cmd, "decide-pending", "R-0001", "hold", "--reason", "E2E hold test"],
        env,
    )
    assert rc == 0, f"decide-pending failed ({rc}): {out} / {err}"
    _assert_single_json_line(out, "decide-pending")

    rc, out, err = _run_pipe_subprocess([*base_cmd, "request-confirm"], env)
    assert rc == 0, f"request-confirm failed ({rc}): {out} / {err}"
    _assert_single_json_line(out, "request-confirm")

    confirm_code = _extract_latest_confirm_code(sink_file)
    assert len(confirm_code) == 6

    rc, out, err = _run_pipe_subprocess([*base_cmd, "confirm", confirm_code], env)
    assert rc == 0, f"confirm failed ({rc}): {out} / {err}"
    confirm_res = _assert_single_json_line(out, "confirm")
    assert confirm_res["ok"] is True

    # Verify confirmed decision applied in DB with channel='thread' and actor='user'
    conn = sqlite3.connect(str(state_dir / "radar.db"))
    conn.row_factory = sqlite3.Row
    try:
        dec_row = conn.execute(
            "SELECT verb, channel, actor FROM decisions "
            "WHERE item_id = 'R-0001' AND superseded_by IS NULL"
        ).fetchone()
        assert dec_row is not None
        assert dec_row["verb"] == "hold"
        assert dec_row["channel"] == "thread"
        assert dec_row["actor"] == "user"

        run_row = conn.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        assert run_row is not None and run_row["status"] == "ok"

        batch_row = conn.execute(
            "SELECT status FROM confirm_batches ORDER BY batch_id DESC LIMIT 1"
        ).fetchone()
        assert batch_row is not None and batch_row["status"] == "confirmed"

        digests_blobs = [
            str(r["markdown"]).encode("utf-8")
            for r in conn.execute("SELECT markdown FROM digests").fetchall()
        ]
    finally:
        conn.close()

    # Verify none of the stdout lines contain the confirmation code
    for stdout_line in all_stdouts:
        assert confirm_code not in stdout_line, f"Confirmation code leaked in stdout: {stdout_line}"

    # 9. Python byte scan across state_dir, run_root, sync_dir, logs_dir, and digests table:
    #    - zero injected secrets
    #    - zero confirmation codes
    #    - zero private project/package names under run_root
    samples = secret_samples.build_samples()
    secret_bytes = [val.encode("utf-8") for val in samples.values()]
    code_bytes = [confirm_code.encode("utf-8")]

    scan_roots = [state_dir, run_root, sync_dir, logs_dir]
    secret_hits = _byte_scan_paths(secret_bytes, scan_roots)
    assert secret_hits == [], f"Secret canary leaked in files: {secret_hits}"

    for d_blob in digests_blobs:
        for s_needle in secret_bytes:
            assert s_needle not in d_blob, "Secret canary leaked in digests table"
        assert confirm_code.encode("utf-8") not in d_blob, "Confirm code leaked in digests table"

    code_hits = _byte_scan_paths(code_bytes, scan_roots)
    assert code_hits == [], f"Confirmation code leaked in files: {code_hits}"

    private_hits_in_run_root = _byte_scan_paths(
        [b"secret-widget", b"example-org/secret-widget"],
        [run_root],
    )
    assert private_hits_in_run_root == [], (
        f"Private project/package name leaked under run_root: {private_hits_in_run_root}"
    )

    # 10. Negative control: with redact.scrub monkeypatched to identity, secrets ARE found
    neg_base = tmp_path / "neg_control"
    monkeypatch.setenv("TECH_RADAR_HOME", str(neg_base / "home"))
    monkeypatch.setenv("TECH_RADAR_STATE", str(neg_base / "state"))
    monkeypatch.setenv("TECH_RADAR_RUN_ROOT", str(neg_base / "run_root"))
    monkeypatch.setenv("TECH_RADAR_LOGS", str(neg_base / "logs"))
    monkeypatch.setenv("TECH_RADAR_SYNC", str(neg_base / "sync"))
    monkeypatch.delenv("TECH_RADAR_NOTIFY_SINK", raising=False)

    monkeypatch.setattr(redact, "scrub", lambda text: text)
    # Seed a signal carrying one of the secret samples and render with identity scrub
    neg_ctx = context.build(now="2026-10-28T12:00:00Z")
    neg_run_id = runlog.start_run(neg_ctx, mode="daily", requested_mode="daily")
    neg_ctx.db.execute(
        "INSERT INTO entities(entity_id, kind, ecosystem, name, visibility, created_at) "
        "VALUES ('pkg:pypi/litellm', 'package', 'pypi', 'litellm', 'public', ?)",
        (neg_ctx.now_iso,),
    )
    neg_ctx.db.execute(
        "INSERT INTO signals(source_id, type, entity_id, title, url, published_at, fetched_at, "
        "confidence, untrusted_excerpt, dedupe_key) "
        "VALUES ('claude_code_changelog', 'feature', 'pkg:pypi/litellm', 'Feat', "
        "'https://github.com/BerriAI/litellm', ?, ?, 'confirmed', ?, 'neg|1')",
        (neg_ctx.now_iso, neg_ctx.now_iso, f"Leaked key {samples['anthropic_key']}"),
    )
    neg_sig_id = int(neg_ctx.db.execute("SELECT last_insert_rowid()").fetchone()[0])
    neg_ctx.db.execute(
        "INSERT INTO digest_items(run_id, ref, section, rank) VALUES (?, ?, 'features', 1)",
        (neg_run_id, f"S-{neg_sig_id}"),
    )
    neg_ctx.db.commit()
    render.render_run(neg_ctx, neg_run_id)

    neg_hits = _byte_scan_paths(
        secret_bytes,
        [neg_base / "state", neg_base / "run_root", neg_base / "sync"],
    )
    assert len(neg_hits) >= 1, "Negative control with identity redact.scrub MUST find secret canary"
