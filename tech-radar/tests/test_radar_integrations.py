"""Integration tests for session-start alerts and dev-guard compatibility."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SESSION_START_MD = REPO_ROOT / "code-quality" / "commands" / "session-start.md"
CODE_QUALITY_CLAUDE_MANIFEST = REPO_ROOT / "code-quality" / ".claude-plugin" / "plugin.json"
CODE_QUALITY_ROOT_MANIFEST = REPO_ROOT / "code-quality" / "plugin.json"
DEV_GUARD_CLAUDE_MANIFEST = REPO_ROOT / "dev-guard" / ".claude-plugin" / "plugin.json"
DEV_GUARD_ROOT_MANIFEST = REPO_ROOT / "dev-guard" / "plugin.json"
MARKETPLACE_MANIFEST = REPO_ROOT / ".claude-plugin" / "marketplace.json"
DEV_GUARD_TEST_FILE = REPO_ROOT / "dev-guard" / "tests" / "test_tool_selection_guard.py"


def _load_devguard_test_helpers() -> Any:
    spec = importlib.util.spec_from_file_location("_devguard_test_helpers", DEV_GUARD_TEST_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_DG_HELPERS = _load_devguard_test_helpers()
run_guard = _DG_HELPERS.run_guard
assert_not_ask_decision = _DG_HELPERS.assert_not_ask_decision


@pytest.fixture(autouse=True)
def _isolate_guard_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect GUARD_DB_PATH and DEV_GUARD_CONFIG to tmp_path so tests never touch user state."""
    monkeypatch.setenv("GUARD_DB_PATH", str(tmp_path / "guard-test.db"))
    monkeypatch.setenv("DEV_GUARD_CONFIG", str(tmp_path / "nonexistent-dev-guard.json"))


def build_radar_agent_payloads(home: str = "/Users/example-user") -> list[tuple[str, str, dict]]:
    """Return `(label, tool_name, tool_input)` for every tool call the radar agent makes."""
    radar_cmd = (
        f"/opt/homebrew/bin/uv run --script --locked --isolated "
        f"{home}/.local/share/tech-radar/app/radar.py"
    )
    run_root = f"{home}/.local/share/tech-radar-run"
    sync_dir = f"{home}/SynologyDrive/ActiveProjects/personal/tech-radar-data"
    run_id = "20261029T070000Z-daily"
    weekly_run_id = "20261029T070000Z-weekly"

    allowed_subcommands = [
        ("bash:plan-run", f"{radar_cmd} plan-run --requested daily"),
        ("bash:collect", f"{radar_cmd} collect --run {run_id}"),
        ("bash:enrich", f"{radar_cmd} enrich --run {weekly_run_id}"),
        ("bash:verdict-apply", f"{radar_cmd} verdict apply --run {run_id}"),
        ("bash:render", f"{radar_cmd} render --run {run_id}"),
        ("bash:search", f"{radar_cmd} search R-0042"),
        ("bash:doctor", f"{radar_cmd} doctor"),
        ("bash:decide-pending", f"{radar_cmd} decide-pending R-0042 trial --ttl 90d"),
        ("bash:feedback", f"{radar_cmd} feedback R-0042 useful"),
        ("bash:taste-diff", f"{radar_cmd} taste diff"),
        ("bash:taste-propose", f"{radar_cmd} taste propose"),
        ("bash:request-confirm-bare", f"{radar_cmd} request-confirm"),
        ("bash:request-confirm-run", f"{radar_cmd} request-confirm --run {run_id}"),
        ("bash:confirm", f"{radar_cmd} confirm ABC234"),
    ]

    payloads: list[tuple[str, str, dict]] = [
        (label, "Bash", {"command": cmd}) for label, cmd in allowed_subcommands
    ]

    ev_url = "https://github.com/astral-sh/uv/releases/tag/0.13.5"
    narrative_why = (
        f"See {ev_url} — Supersedes the legacy lock resolver, "
        "replaces the old uv cache index, and ships Phase 2 workspace locking."
    )
    narrative_sup = (
        f"Benchmark at {ev_url} shows it Supersedes pip-tools and "
        "replaces the old resolver in Phase 2."
    )
    verdict_obj = {
        "schema": 1,
        "run_id": run_id,
        "items": [
            {
                "ref": "R-0001",
                "section": "contenders",
                "action": "surface",
                "fit": 85,
                "quality_adj": 5,
                "quality_adj_reason": f"Verified release notes at {ev_url}",
                "why": narrative_why,
                "replaces": "pkg:pypi/pip-tools",
                "superiority": narrative_sup,
                "relevant_to": ["private-project-1"],
                "benefit": "10x faster lockfile resolution",
                "suggested_action": "Trial on private-project-1",
                "effort": "S",
                "confidence": "high",
                "evidence_urls": [ev_url],
                "llm_fields": {
                    "cloud_code_upload": False,
                    "arbitrary_shell": False,
                    "broad_credentials": False,
                    "telemetry_no_optout": False,
                },
            }
        ],
    }
    shortlist_obj = {
        "run_id": weekly_run_id,
        "item_ids": ["R-0001"],
        "proposals": [
            {
                "name": "uv",
                "ecosystem": "pypi",
                "repo": "astral-sh/uv",
                "source_url": ev_url,
                "replaces": "pkg:pypi/pip-tools",
                "why": narrative_why,
            }
        ],
    }
    answers_obj = {
        "languages": ["python", "typescript"],
        "categories": ["Python tooling", "MCP servers"],
        "anti_interests": ["Rust tooling"],
        "preferences": ["MIT/Apache-2.0 preferred"],
        "taste_makers": ["simonw"],
        "watchlist": ["astral-sh/uv"],
        "initial_rings": [{"entity_id": "pkg:pypi/uv", "ring": "adopt", "reason": "primary tool"}],
        "subscriptions": ["claude-max"],
    }

    payloads.extend(
        [
            (
                "write:verdicts-single-line",
                "Write",
                {
                    "file_path": f"{run_root}/runs/{run_id}/verdicts.json",
                    "content": json.dumps(verdict_obj),
                },
            ),
            (
                "write:verdicts-multi-line",
                "Write",
                {
                    "file_path": f"{run_root}/runs/{run_id}/verdicts.json",
                    "content": json.dumps(verdict_obj, indent=2),
                },
            ),
            (
                "write:shortlist-single-line",
                "Write",
                {
                    "file_path": f"{run_root}/runs/{weekly_run_id}/shortlist.json",
                    "content": json.dumps(shortlist_obj),
                },
            ),
            (
                "write:shortlist-multi-line",
                "Write",
                {
                    "file_path": f"{run_root}/runs/{weekly_run_id}/shortlist.json",
                    "content": json.dumps(shortlist_obj, indent=2),
                },
            ),
            (
                "write:bootstrap-answers",
                "Write",
                {
                    "file_path": f"{run_root}/runs/bootstrap/answers.json",
                    "content": json.dumps(answers_obj, indent=2),
                },
            ),
            (
                "write:taste-profile-proposed",
                "Write",
                {
                    "file_path": f"{sync_dir}/TASTE_PROFILE.proposed.md",
                    "content": (
                        "# Taste Profile\n\n## Preferences\n"
                        f"- Prefer tools documented at {ev_url} that Supersedes legacy runners "
                        "and replaces the old workflow in Phase 2.\n"
                    ),
                },
            ),
            (
                "read:radar-cmd",
                "Read",
                {"file_path": f"{run_root}/RADAR_CMD"},
            ),
            (
                "read:brief-index",
                "Read",
                {"file_path": f"{run_root}/runs/{run_id}/brief/index.json"},
            ),
            (
                "read:brief-context",
                "Read",
                {"file_path": f"{run_root}/runs/{run_id}/brief/context.json"},
            ),
            (
                "read:plugin-reference",
                "Read",
                {
                    "file_path": (
                        f"{home}/.claude/plugins/cache/personal-claude-marketplace/"
                        "tech-radar/0.1.0/skills/radar/references/run-procedure.md"
                    )
                },
            ),
            (
                "websearch:alternatives",
                "WebSearch",
                {"query": "alternatives to pip-tools python lockfile"},
            ),
            (
                "webfetch:docs-url",
                "WebFetch",
                {
                    "url": "https://docs.astral.sh/uv/",
                    "prompt": "Summarize workspace lock features",
                },
            ),
            (
                "mcp:fetchaller-browse-reddit",
                "mcp__plugin_fetchaller-mcp_fetchaller__browse_reddit",
                {"subreddit": "Python", "sort": "top", "time": "week", "limit": 10},
            ),
            (
                "mcp:fetchaller-search-reddit",
                "mcp__plugin_fetchaller-mcp_fetchaller__search_reddit",
                {"query": "uv workspace lock", "subreddit": "Python", "limit": 10},
            ),
            (
                "mcp:t3-html-render",
                "mcp__t3-code__html_render",
                {"file_path": f"{run_root}/runs/{weekly_run_id}/radar.html"},
            ),
            (
                "mcp:t3-html-preview",
                "mcp__t3-code__html_preview",
                {"file_path": f"{run_root}/runs/{weekly_run_id}/radar.html"},
            ),
            (
                "mcp:t3-schedule-task",
                "mcp__t3-code__schedule_task",
                {
                    "title": "Tech Radar",
                    "prompt": "/tech-radar:radar run",
                    "schedule": {"type": "fixed_time", "timeOfDay": "07:00"},
                    "bindToCurrentThread": False,
                },
            ),
            (
                "mcp:t3-project-create",
                "mcp__t3-code__t3_project_create",
                {"title": "Tech Radar", "workspaceRoot": run_root},
            ),
            (
                "mcp:t3-project-update",
                "mcp__t3-code__t3_project_update",
                {
                    "projectId": "proj-1",
                    "defaultProvider": "claudeAgent_bifrost",
                    "defaultModel": "gemini-4-argon",
                },
            ),
            (
                "mcp:t3-thread-launch",
                "mcp__t3-code__t3_thread_launch",
                {"projectId": "proj-1", "prompt": "Create the daily 07:00 schedule"},
            ),
            (
                "mcp:t3-thread-wait",
                "mcp__t3-code__t3_thread_wait",
                {"threadId": "thread-1"},
            ),
            (
                "mcp:t3-thread-read",
                "mcp__t3-code__t3_thread_read",
                {"threadId": "thread-1"},
            ),
            (
                "mcp:t3-run-scheduled-task-now",
                "mcp__t3-code__run_scheduled_task_now",
                {"taskId": "task-1"},
            ),
            (
                "ask-user-question",
                "AskUserQuestion",
                {
                    "questions": [
                        {
                            "header": "Languages",
                            "question": "Confirm detected languages to track on your radar?",
                            "options": [
                                {"label": "Python + TypeScript", "description": "Default stack"},
                                {"label": "Python only", "description": "Narrow focus"},
                            ],
                        }
                    ]
                },
            ),
        ]
    )
    return payloads


def test_session_start_contains_step_3b_and_manifests_synced() -> None:
    """Verify Step 3b is positioned between Step 3 and Step 4 and code-quality is 3.32.0."""
    text = SESSION_START_MD.read_text(encoding="utf-8")

    existing_idx = text.index("## Existing Project Flow")
    new_flow_idx = text.index("## New Project Flow")
    existing_section = text[existing_idx:new_flow_idx]

    step1_idx = existing_section.index("### Step 1: Load Project Memory (Selective)")
    step2_idx = existing_section.index("### Step 2: Check Project Standards")
    step3_idx = existing_section.index("### Step 3: Check Git State")
    step3b_idx = existing_section.index("### Step 3b: Tech radar alerts")
    step4_idx = existing_section.index("### Step 4: Present Project Orientation")
    assert step1_idx < step2_idx < step3_idx < step3b_idx < step4_idx
    assert "### Step 5:" not in existing_section

    step3b_body = existing_section[step3b_idx:step4_idx]
    assert "~/.local/share/tech-radar/project-alerts.json" in step3b_body
    assert "git remote get-url origin" in step3b_body
    assert "git@host:o/r" in step3b_body
    assert "ssh://git@host/o/r" in step3b_body
    assert "https://host/o/r" in step3b_body
    assert ".git" in step3b_body
    assert "userinfo" in step3b_body.lower()
    assert "lowercase" in step3b_body.lower()
    assert "skip silently" in step3b_body.lower()
    assert "**Tech radar alerts:**" in step3b_body
    assert "- [tier] title (id)" in step3b_body

    cq_claude = json.loads(CODE_QUALITY_CLAUDE_MANIFEST.read_text(encoding="utf-8"))
    cq_root = json.loads(CODE_QUALITY_ROOT_MANIFEST.read_text(encoding="utf-8"))
    marketplace = json.loads(MARKETPLACE_MANIFEST.read_text(encoding="utf-8"))
    cq_market = next(p for p in marketplace["plugins"] if p["name"] == "code-quality")

    assert cq_claude["version"] == "3.32.0"
    assert cq_root["version"] == "3.32.0"
    assert cq_market["version"] == "3.32.0"
    assert cq_claude["description"] == cq_root["description"] == cq_market["description"]


@pytest.mark.parametrize(
    ("label", "tool_name", "tool_input"),
    build_radar_agent_payloads(),
    ids=[p[0] for p in build_radar_agent_payloads()],
)
def test_devguard_allows_all_radar_agent_payloads(
    label: str, tool_name: str, tool_input: dict
) -> None:
    """Every tool call made by the radar agent exits 0 and never returns ask or block."""
    result: subprocess.CompletedProcess[str] = run_guard(
        tool_name,
        tool_input,
        env=os.environ.copy(),
    )
    assert result.returncode == 0, (
        f"[{label}] expected exit 0, got {result.returncode}: stderr={result.stderr!r}"
    )
    assert isinstance(tool_input, dict | list)
    assert_not_ask_decision(result, test_id=label)
    if result.stdout.strip():
        parsed = json.loads(result.stdout)
        decision = parsed.get("hookSpecificOutput", {}).get("permissionDecision")
        assert isinstance(decision, str | None)
        assert decision not in ("ask", "block", "deny"), (
            f"[{label}] unexpected permissionDecision={decision!r}"
        )


def test_devguard_manifests_synced() -> None:
    """Verify dev-guard version is bumped and synchronized across all three manifests."""
    dg_claude = json.loads(DEV_GUARD_CLAUDE_MANIFEST.read_text(encoding="utf-8"))
    dg_root = json.loads(DEV_GUARD_ROOT_MANIFEST.read_text(encoding="utf-8"))
    marketplace = json.loads(MARKETPLACE_MANIFEST.read_text(encoding="utf-8"))
    dg_market = next(p for p in marketplace["plugins"] if p["name"] == "dev-guard")

    assert dg_claude["version"] in ("1.71.1", "1.72.0")
    assert dg_claude["version"] == dg_root["version"] == dg_market["version"]
    assert dg_claude["description"] == dg_root["description"] == dg_market["description"]
    assert len(build_radar_agent_payloads()) == 38


def _format_decision(proc: subprocess.CompletedProcess[str]) -> str:
    if proc.stdout.strip():
        data = json.loads(proc.stdout)
        return str(data.get("hookSpecificOutput", {}).get("permissionDecision") or "passthrough")
    if proc.returncode != 0:
        first_line = proc.stderr.strip().splitlines()[0] if proc.stderr.strip() else "error"
        return f"block ({first_line})"
    return "passthrough (none)"


def print_dry_run_report() -> None:
    """Print repo vs cached installed dev-guard evaluation for all radar agent payloads."""
    import tempfile

    installed_json = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    installed_data = json.loads(installed_json.read_text(encoding="utf-8"))
    dg_record = installed_data["plugins"]["dev-guard@personal-claude-marketplace"][0]
    cached_script = Path(dg_record["installPath"]) / "hooks" / "tool-selection-guard.py"
    repo_script = REPO_ROOT / "dev-guard" / "hooks" / "tool-selection-guard.py"

    with tempfile.TemporaryDirectory() as tmp_dir:
        env = {
            **os.environ,
            "GUARD_DB_PATH": str(Path(tmp_dir) / "guard.db"),
            "DEV_GUARD_CONFIG": str(Path(tmp_dir) / "nonexistent.json"),
        }
        print(f"- repo_guard: `{repo_script}` (v1.71.1)")
        print(f"- cached_guard: `{cached_script}` (v{dg_record['version']})")
        for label, tool_name, tool_input in build_radar_agent_payloads():
            payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
            r_repo = subprocess.run(
                ["uv", "run", str(repo_script)],
                input=payload,
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            r_cached = subprocess.run(
                ["uv", "run", str(cached_script)],
                input=payload,
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            d_repo = _format_decision(r_repo)
            d_cached = _format_decision(r_cached)
            diff_note = (
                " **(DIFFERS until plugin update)**"
                if (r_repo.returncode != r_cached.returncode or d_repo != d_cached)
                else ""
            )
            print(
                f"- `{label}` (`{tool_name}`): "
                f"repo exit=`{r_repo.returncode}` decision=`{d_repo}` | "
                f"cached exit=`{r_cached.returncode}` decision=`{d_cached}`{diff_note}"
            )


if __name__ == "__main__":
    print_dry_run_report()
