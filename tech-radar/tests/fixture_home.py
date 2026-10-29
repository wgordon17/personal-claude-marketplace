"""Synthetic fixture home builder for tech-radar inventory and signal tests (Spec 11 §2, A25)."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import secret_samples

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[1]
SCRIPTS_DIR = REPO_ROOT / "tech-radar" / "skills" / "radar" / "scripts"
STATIC_FS_DIR = TESTS_DIR / "fixtures" / "fs" / "static"

FIXED_GIT_DATE = "2026-10-20T12:00:00Z"
EXCLUDED_SCRIPT_NAMES = frozenset({"__pycache__", ".DS_Store", ".pytest_cache"})


def _git_env(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_AUTHOR_NAME"] = "Example User"
    env["GIT_AUTHOR_EMAIL"] = "user@example.com"
    env["GIT_COMMITTER_NAME"] = "Example User"
    env["GIT_COMMITTER_EMAIL"] = "user@example.com"
    env["GIT_AUTHOR_DATE"] = FIXED_GIT_DATE
    env["GIT_COMMITTER_DATE"] = FIXED_GIT_DATE
    return env


def _run_git(args: list[str], cwd: Path, home: Path) -> subprocess.CompletedProcess[str]:
    res = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=_git_env(home),
        capture_output=True,
        text=True,
        check=True,
    )
    return res


def _copy_static_tree(home: Path, sync: Path) -> None:
    for src_path in sorted(STATIC_FS_DIR.rglob("*")):
        if src_path.is_dir():
            continue
        rel = src_path.relative_to(STATIC_FS_DIR)
        if rel.parts[0] == "sync":
            sync_rel = Path(*rel.parts[1:])
            for dest_base in (
                sync,
                home / "SynologyDrive" / "ActiveProjects" / "personal" / "tech-radar-data",
            ):
                dest = dest_base / sync_rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dest)
            continue

        dest = home / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src_path.suffix == ".plist":
            text = src_path.read_text(encoding="utf-8")
            text = text.replace("{HOME}", str(home))
            dest.write_text(text, encoding="utf-8")
        else:
            shutil.copy2(src_path, dest)

    mytool = home / ".local" / "bin" / "mytool"
    if mytool.is_file():
        os.chmod(mytool, 0o755)

    gopls_bin = home / "go" / "bin" / "gopls"
    gopls_bin.parent.mkdir(parents=True, exist_ok=True)
    gopls_bin.write_text("#!/bin/sh\necho gopls\n", encoding="utf-8")
    os.chmod(gopls_bin, 0o755)

    ruff_bin = home / ".local" / "bin" / "ruff"
    ruff_bin.parent.mkdir(parents=True, exist_ok=True)
    ruff_bin.write_text("#!/bin/sh\necho ruff\n", encoding="utf-8")
    os.chmod(ruff_bin, 0o755)


def _copy_scripts_clean(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    for item in sorted(src.rglob("*")):
        rel = item.relative_to(src)
        if any(part in EXCLUDED_SCRIPT_NAMES or part.endswith(".pyc") for part in rel.parts):
            continue
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def _build_installed_plugin_fixture(home: Path, marketplace: str = "present") -> str:
    """Create v2 installed_plugins.json + temp marketplace git repo (Spec 12 A25)."""
    mkt_repo = home / ".claude" / "plugins" / "marketplaces" / "personal-claude-marketplace"
    mkt_repo.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "-b", "main"], mkt_repo, home)

    manifest_dir = mkt_repo / "tech-radar" / ".claude-plugin"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "tech-radar",
                "version": "0.1.0",
                "description": "Synthetic tech-radar plugin fixture",
                "author": {"name": "example-user"},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    mkt_scripts = mkt_repo / "tech-radar" / "skills" / "radar" / "scripts"
    _copy_scripts_clean(SCRIPTS_DIR, mkt_scripts)

    _run_git(["add", "."], mkt_repo, home)
    _run_git(["commit", "-m", "chore: initial marketplace commit"], mkt_repo, home)
    head_sha = _run_git(["rev-parse", "HEAD"], mkt_repo, home).stdout.strip()

    install_path = (
        home
        / ".claude"
        / "plugins"
        / "cache"
        / "personal-claude-marketplace"
        / "tech-radar"
        / "0.1.0"
    )
    install_path.mkdir(parents=True, exist_ok=True)
    (install_path / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest_dir / "plugin.json", install_path / ".claude-plugin" / "plugin.json")
    _copy_scripts_clean(SCRIPTS_DIR, install_path / "skills" / "radar" / "scripts")

    recorded_sha = "0" * 40 if marketplace == "shallow-missing" else head_sha
    installed_plugins_json = {
        "version": 2,
        "plugins": {
            "tech-radar@personal-claude-marketplace": [
                {
                    "scope": "user",
                    "installPath": str(install_path),
                    "version": "0.1.0",
                    "installedAt": "2026-10-28T00:00:00.000Z",
                    "lastUpdated": "2026-10-28T00:00:00.000Z",
                    "gitCommitSha": recorded_sha,
                }
            ]
        },
    }
    plugins_dir = home / ".claude" / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    (plugins_dir / "installed_plugins.json").write_text(
        json.dumps(installed_plugins_json, indent=2) + "\n",
        encoding="utf-8",
    )
    (plugins_dir / "known_marketplaces.json").write_text(
        json.dumps(
            {
                "personal-claude-marketplace": {
                    "installLocation": str(mkt_repo),
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return head_sha


def _uv_lock_text(litellm_ver: str) -> str:
    return (
        "version = 1\n"
        'requires-python = ">=3.13"\n\n'
        "[[package]]\n"
        'name = "anthropic"\n'
        'version = "0.40.0"\n'
        'source = { registry = "https://pypi.org/simple" }\n\n'
        "[[package]]\n"
        'name = "fastapi"\n'
        'version = "0.115.0"\n'
        'source = { registry = "https://pypi.org/simple" }\n'
        "dependencies = [\n"
        '    { name = "starlette" },\n'
        "]\n\n"
        "[[package]]\n"
        'name = "litellm"\n'
        f'version = "{litellm_ver}"\n'
        'source = { registry = "https://pypi.org/simple" }\n\n'
        "[[package]]\n"
        'name = "pytest"\n'
        'version = "8.3.0"\n'
        'source = { registry = "https://pypi.org/simple" }\n\n'
        "[[package]]\n"
        'name = "ruff"\n'
        'version = "0.8.0"\n'
        'source = { registry = "https://pypi.org/simple" }\n\n'
        "[[package]]\n"
        'name = "starlette"\n'
        'version = "0.41.0"\n'
        'source = { registry = "https://pypi.org/simple" }\n\n'
        "[[package]]\n"
        'name = "public-app"\n'
        'version = "0.1.0"\n'
        'source = { virtual = "." }\n'
        "dependencies = [\n"
        '    { name = "anthropic" },\n'
        '    { name = "fastapi" },\n'
        '    { name = "litellm" },\n'
        "]\n"
        "[package.dev-dependencies]\n"
        "dev = [\n"
        '    { name = "pytest" },\n'
        '    { name = "ruff" },\n'
        "]\n"
    )


def _build_repos(
    home: Path,
    proj_root: Path,
    scenario: str,
    samples: dict[str, str],
) -> None:
    proj_root.mkdir(parents=True, exist_ok=True)

    # Repo A: public-app
    repo_a = proj_root / "public-app"
    repo_a.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "-b", "main"], repo_a, home)
    _run_git(
        ["remote", "add", "origin", "https://github.com/example-org/public-app.git"],
        repo_a,
        home,
    )

    (repo_a / "pyproject.toml").write_text(
        "[project]\n"
        'name = "public-app"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.13"\n'
        "dependencies = [\n"
        '    "fastapi>=0.115.0",\n'
        '    "litellm>=1.100.0",\n'
        '    "anthropic>=0.40.0",\n'
        "]\n\n"
        "[dependency-groups]\n"
        "dev = [\n"
        '    "pytest>=8.0.0",\n'
        '    "ruff>=0.8.0",\n'
        "]\n\n"
        "[tool.ruff]\n"
        "line-length = 100\n"
        'select = ["E", "F"]\n',
        encoding="utf-8",
    )
    (repo_a / "uv.lock").write_text(_uv_lock_text("1.106.2"), encoding="utf-8")

    wf_dir = repo_a / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / "ci.yml").write_text(
        "name: CI\n"
        "on: [push]\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683\n"
        "        with:\n"
        "          fetch-depth: 0\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        "          python-version: '3.13'\n"
        "      - run: |\n"
        "          uv sync --frozen\n"
        "          gh pr list --state open\n",
        encoding="utf-8",
    )
    (repo_a / ".pre-commit-config.yaml").write_text(
        "repos:\n"
        "  - repo: https://github.com/astral-sh/ruff-pre-commit\n"
        "    rev: v0.8.0\n"
        "    hooks:\n"
        "      - id: ruff\n",
        encoding="utf-8",
    )
    (repo_a / "prek.toml").write_text(
        '[[repos]]\nrepo = "https://github.com/gitleaks/gitleaks"\nrev = "v8.21.0"\n',
        encoding="utf-8",
    )
    (repo_a / "Dockerfile").write_text("FROM python:3.13-slim\nWORKDIR /app\n", encoding="utf-8")
    (repo_a / "Makefile").write_text(
        ".PHONY: test\ntest:\n\tuv sync --frozen\n\tgh pr list --state open\n",
        encoding="utf-8",
    )
    (repo_a / "renovate.json").write_text(
        json.dumps({"extends": ["config:recommended"]}, indent=2) + "\n",
        encoding="utf-8",
    )
    (repo_a / "README.md").write_text(
        "# Public App\n\n## Streaming Gateway\n\nSynthetic public repo.\n",
        encoding="utf-8",
    )
    (repo_a / "CLAUDE.md").write_text(
        "# CLAUDE.md\n\n## Fast Local Testing\n",
        encoding="utf-8",
    )
    hack_dir = repo_a / "hack"
    hack_dir.mkdir(parents=True, exist_ok=True)
    (hack_dir / "PROJECT.md").write_text("# Project\n\n## Async Pipeline\n", encoding="utf-8")
    (hack_dir / "TODO.md").write_text(
        "# TODO\n\n- [ ] Automate release notes\n- [x] Initial setup\n",
        encoding="utf-8",
    )
    (hack_dir / "LESSONS.md").write_text("# Lessons\n\n## Pin Actions By SHA\n", encoding="utf-8")
    (hack_dir / "BUGS.md").write_text("# Bugs\n\n## Retry Timeout Handling\n", encoding="utf-8")

    (repo_a / "app.py").write_text(
        "import litellm\n"
        "from fastapi import FastAPI\n\n"
        "app = FastAPI()\n\n"
        "def run_prompt() -> None:\n"
        '    litellm.completion(model="claude-sonnet-5-5", messages=[])\n',
        encoding="utf-8",
    )
    repo_skill = repo_a / ".claude" / "skills" / "repo-skill"
    repo_skill.mkdir(parents=True, exist_ok=True)
    (repo_skill / "SKILL.md").write_text(
        "---\nname: repo-skill\ndescription: Repo local skill.\n---\n# Repo Skill\n",
        encoding="utf-8",
    )

    _run_git(["add", "."], repo_a, home)
    _run_git(["commit", "-m", "feat: initial public-app"], repo_a, home)

    if scenario == "compromised":
        (repo_a / "uv.lock").write_text(_uv_lock_text("1.107.0"), encoding="utf-8")
        _run_git(["add", "uv.lock"], repo_a, home)
        _run_git(["commit", "-m", "chore: bump litellm to 1.107.0"], repo_a, home)

        gw_lock = home / ".config" / "llm-gateway" / "uv.lock"
        gw_lock.write_text(
            gw_lock.read_text(encoding="utf-8").replace("1.106.2", "1.107.0"),
            encoding="utf-8",
        )
        site_pkgs = repo_a / ".venv" / "lib" / "python3.13" / "site-packages"
        site_pkgs.mkdir(parents=True, exist_ok=True)
        cook_pth = site_pkgs / "cook.pth"
        cook_pth.write_text("import os; # synthetic malicious pth\n", encoding="utf-8")
        # Deterministic mtime (2026-10-27T12:00:00Z = 1793102400) within +/-7d of FIXED_NOW
        os.utime(cook_pth, (1793102400, 1793102400))

    # Never-read secret files inside repo_a (untracked, must never be opened)
    (repo_a / ".env").write_text(f"AWS_SECRET={samples['aws_key']}\n", encoding="utf-8")
    (repo_a / "id_rsa").write_text(f"{samples['private_key']}\n", encoding="utf-8")
    (repo_a / ".netrc").write_text(
        f"machine api.example.com password {samples['high_entropy']}\n", encoding="utf-8"
    )
    (repo_a / "x.pem").write_text(f"{samples['private_key']}\n", encoding="utf-8")

    # Repo B: worktree of Repo A
    repo_b = proj_root / "public-app-wt"
    _run_git(["worktree", "add", str(repo_b), "-b", "wt-branch"], repo_a, home)

    # Repo C: private repo secret-widget
    repo_c = proj_root / "secret-widget"
    repo_c.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "-b", "main"], repo_c, home)
    _run_git(
        ["remote", "add", "origin", "https://github.com/example-org/secret-widget.git"],
        repo_c,
        home,
    )
    (repo_c / "pyproject.toml").write_text(
        "[project]\n"
        'name = "secret-widget"\n'
        'version = "0.1.0"\n'
        'dependencies = ["secret-widget-core>=0.2.0"]\n',
        encoding="utf-8",
    )
    (repo_c / "uv.lock").write_text(
        "version = 1\n"
        'requires-python = ">=3.13"\n\n'
        "[[package]]\n"
        'name = "secret-widget-core"\n'
        'version = "0.2.0"\n'
        'source = { registry = "https://pypi.org/simple" }\n\n'
        "[[package]]\n"
        'name = "secret-widget"\n'
        'version = "0.1.0"\n'
        'source = { virtual = "." }\n'
        'dependencies = [{ name = "secret-widget-core" }]\n',
        encoding="utf-8",
    )
    _run_git(["add", "."], repo_c, home)
    _run_git(["commit", "-m", "feat: initial secret-widget"], repo_c, home)

    # Malicious repo: evil-repo
    evil_repo = proj_root / "evil-repo"
    evil_repo.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "-b", "main"], evil_repo, home)
    (evil_repo / "README.md").write_text("# Evil Repo\n", encoding="utf-8")
    (evil_repo / ".gitattributes").write_text("* diff=evil\n", encoding="utf-8")
    (evil_repo / "Makefile").write_text("all:\n\ttouch EVIL_CANARY\n", encoding="utf-8")
    (evil_repo / ".envrc").write_text(f"export TOKEN={samples['slack_token']}\n", encoding="utf-8")
    _run_git(["add", "."], evil_repo, home)
    _run_git(["commit", "-m", "feat: evil commit"], evil_repo, home)

    canary_file = home / "fsmonitor_canary_triggered"
    canary_script = home / "evil_canary.sh"
    canary_script.write_text(
        f"#!/bin/sh\ntouch {canary_file}\n",
        encoding="utf-8",
    )
    os.chmod(canary_script, 0o755)

    evil_git_config = evil_repo / ".git" / "config"
    cfg_text = evil_git_config.read_text(encoding="utf-8")
    tokenized_url = (
        f"https://devuser:{samples['github_token']}@github.com/example-org/evil-repo.git"
    )
    cfg_text += (
        f'\n[core]\n\tfsmonitor = "{canary_script}"\n'
        f'[diff "evil"]\n\ttextconv = "{canary_script}"\n'
        f'[remote "origin"]\n\turl = {tokenized_url}\n'
    )
    evil_git_config.write_text(cfg_text, encoding="utf-8")


def _build_t3_state_db(home: Path, run_root: Path) -> None:
    """Create statev2.sqlite using the spike-recorded schema (spike-results.md ## T3 state DB)."""
    t3_dir = home / ".t3" / "userdata"
    t3_dir.mkdir(parents=True, exist_ok=True)
    db_path = t3_dir / "statev2.sqlite"
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE projection_projects (
                project_id TEXT PRIMARY KEY,
                title TEXT,
                workspace_root TEXT,
                scripts_json TEXT,
                created_at TEXT,
                updated_at TEXT,
                deleted_at TEXT,
                default_model_selection_json TEXT,
                default_thread_env_mode TEXT,
                favicon_path TEXT,
                auto_pull INTEGER,
                project_icon_json TEXT
            );
            CREATE TABLE orchestration_v2_projection_threads (
                thread_id TEXT PRIMARY KEY,
                project_id TEXT,
                title TEXT,
                default_provider TEXT,
                runtime_mode TEXT,
                interaction_mode TEXT,
                active_provider_thread_id TEXT,
                created_at TEXT,
                updated_at TEXT,
                archived_at TEXT,
                deleted_at TEXT,
                payload_json TEXT,
                provider_instance_id TEXT
            );
            CREATE TABLE orchestration_v2_projection_runs (
                run_id TEXT PRIMARY KEY,
                thread_id TEXT,
                ordinal INTEGER,
                provider TEXT,
                provider_thread_id TEXT,
                status TEXT,
                requested_at TEXT,
                completed_at TEXT,
                payload_json TEXT,
                provider_instance_id TEXT
            );
            CREATE TABLE orchestration_v2_projection_provider_sessions (
                provider_session_id TEXT PRIMARY KEY,
                thread_id TEXT,
                provider TEXT,
                status TEXT,
                model TEXT,
                updated_at TEXT,
                payload_json TEXT,
                driver TEXT,
                provider_instance_id TEXT
            );
            CREATE TABLE orchestration_v2_projection_turn_items (
                turn_item_id TEXT PRIMARY KEY,
                thread_id TEXT,
                run_id TEXT,
                node_id TEXT,
                provider_thread_id TEXT,
                provider_turn_id TEXT,
                parent_item_id TEXT,
                ordinal INTEGER,
                type TEXT,
                status TEXT,
                updated_at TEXT,
                payload_json TEXT
            );
            CREATE TABLE orchestration_v2_projection_messages (
                message_id TEXT PRIMARY KEY,
                thread_id TEXT,
                run_id TEXT,
                node_id TEXT,
                role TEXT,
                streaming INTEGER,
                created_at TEXT,
                updated_at TEXT,
                payload_json TEXT
            );
            CREATE TABLE scheduled_tasks (
                task_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                prompt TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                schedule_json TEXT NOT NULL,
                project_id TEXT NOT NULL,
                thread_id TEXT,
                workspace_strategy_json TEXT NOT NULL,
                model_selection_json TEXT NOT NULL,
                runtime_mode TEXT NOT NULL,
                interaction_mode TEXT NOT NULL,
                created_by TEXT NOT NULL,
                creation_source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                next_run_at TEXT,
                last_run_at TEXT,
                last_run_status TEXT NOT NULL,
                last_run_error TEXT,
                run_count INTEGER NOT NULL,
                webhook_token TEXT,
                webhook_secret TEXT,
                last_run_webhook_delivery_id TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO projection_projects("
            "project_id, title, workspace_root, created_at, updated_at, deleted_at"
            ") VALUES (?, ?, ?, ?, ?, NULL)",
            ("proj-radar-1", "tech-radar-run", str(run_root), FIXED_GIT_DATE, FIXED_GIT_DATE),
        )
        payload = json.dumps({"modelSelection": {"model": "gemini-4-argon"}})
        for idx in (1, 2, 3):
            tid = f"thread:project:proj-radar-1:000{idx}"
            conn.execute(
                "INSERT INTO orchestration_v2_projection_threads("
                "thread_id, project_id, title, default_provider, runtime_mode, interaction_mode, "
                "created_at, updated_at, deleted_at, payload_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                (
                    tid,
                    "proj-radar-1",
                    f"Radar Run {idx}",
                    "claudeAgent_bifrost",
                    "auto",
                    "default",
                    f"2026-10-2{idx}T07:00:00Z",
                    f"2026-10-2{idx}T07:05:00Z",
                    payload,
                ),
            )
        conn.execute(
            "INSERT INTO orchestration_v2_projection_provider_sessions("
            "provider_session_id, thread_id, provider, status, model, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (
                "sess-1",
                "thread:project:proj-radar-1:0003",
                "claudeAgent_bifrost",
                "completed",
                "gemini-4-argon",
                "2026-10-23T07:05:00Z",
            ),
        )
        conn.execute(
            "INSERT INTO orchestration_v2_projection_turn_items("
            "turn_item_id, thread_id, run_id, ordinal, type, status, updated_at, payload_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "item-1",
                "thread:project:proj-radar-1:0003",
                "run-1",
                1,
                "command_execution",
                "completed",
                "2026-10-23T07:01:00Z",
                json.dumps({"toolName": "Bash", "status": "completed"}),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _inject_secrets(home: Path, samples: dict[str, str]) -> None:
    """Inject secrets from secret_samples.py into history, settings, MCP, and transcript."""
    fish_hist = home / ".local" / "share" / "fish" / "fish_history"
    hist_text = fish_hist.read_text(encoding="utf-8")
    all_secret_args = " ".join(f"--secret={val}" for val in samples.values())
    hist_text += f"- cmd: uv run script.py {all_secret_args}\n  when: 1793000600\n"
    fish_hist.write_text(hist_text, encoding="utf-8")

    settings_path = home / ".claude" / "settings.json"
    settings_data: dict[str, Any] = json.loads(settings_path.read_text(encoding="utf-8"))
    settings_data["env"] = {
        "ANTHROPIC_API_KEY": samples["anthropic_key"],
        "OPENAI_API_KEY": samples["openai_key"],
        "GOOGLE_API_KEY": samples["google_key"],
        "AWS_ACCESS_KEY_ID": samples["aws_key"],
        "GITHUB_PAT": samples["github_pat"],
    }
    settings_path.write_text(json.dumps(settings_data, indent=2) + "\n", encoding="utf-8")

    claude_json_path = home / ".claude.json"
    cj_data: dict[str, Any] = json.loads(claude_json_path.read_text(encoding="utf-8"))
    cj_data["mcpServers"]["github"]["headers"] = {
        "Authorization": samples["bearer"],
        "X-JWT": samples["jwt"],
        "X-Slack": samples["slack_token"],
    }
    cj_data["mcpServers"]["github"]["env"] = {
        "GITHUB_PERSONAL_ACCESS_TOKEN": samples["github_token"],
        "HIGH_ENTROPY": samples["high_entropy"],
    }
    claude_json_path.write_text(json.dumps(cj_data, indent=2) + "\n", encoding="utf-8")

    transcript_path = (
        home / ".claude" / "projects" / "-Users-example-user-project" / "session-1.jsonl"
    )
    t_lines = transcript_path.read_text(encoding="utf-8").splitlines()
    injected_tool_result = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_01",
                    "is_error": False,
                    "content": ("TOOL_RESULT_SECRET_CANARY " + " ".join(samples.values())),
                }
            ],
        },
    }
    t_lines.append(json.dumps(injected_tool_result))
    transcript_path.write_text("\n".join(t_lines) + "\n", encoding="utf-8")


def build(
    tmp_path: Path,
    scenario: str = "baseline",
    *,
    marketplace: str = "present",
) -> Path:
    """Materialise the synthetic fixture home directory and return its Path."""
    env_home = os.environ.get("TECH_RADAR_HOME")
    if env_home and Path(env_home).is_relative_to(tmp_path):
        home = Path(env_home)
    elif tmp_path.name == "home":
        home = tmp_path
    else:
        home = tmp_path / "home"

    env_sync = os.environ.get("TECH_RADAR_SYNC")
    sync = (
        Path(env_sync)
        if env_sync and Path(env_sync).is_relative_to(tmp_path)
        else tmp_path / "sync"
    )
    env_run_root = os.environ.get("TECH_RADAR_RUN_ROOT")
    run_root = (
        Path(env_run_root)
        if env_run_root and Path(env_run_root).is_relative_to(tmp_path)
        else tmp_path / "run_root"
    )

    if home.exists():
        shutil.rmtree(home)
    home.mkdir(parents=True, exist_ok=True)
    sync.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)

    proj_root = home / "SynologyDrive" / "ActiveProjects"
    os.environ["TECH_RADAR_HOME"] = str(home)
    os.environ["TECH_RADAR_SYNC"] = str(sync)
    os.environ["TECH_RADAR_RUN_ROOT"] = str(run_root)
    os.environ["TECH_RADAR_PROJECT_ROOTS"] = str(proj_root)

    samples = secret_samples.build_samples()
    _copy_static_tree(home, sync)
    _build_repos(home, proj_root, scenario, samples)
    _build_t3_state_db(home, run_root)
    _build_installed_plugin_fixture(home, marketplace=marketplace)
    _inject_secrets(home, samples)

    return home
