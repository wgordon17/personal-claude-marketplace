"""Unit and integration tests for .github/scripts/skillsaw_custom_rules.py."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = REPO_ROOT / ".github" / "scripts" / "skillsaw_custom_rules.py"
SKILLSAW_YAML = REPO_ROOT / ".skillsaw.yaml"


def _load_rules_module() -> Any:
    spec = importlib.util.spec_from_file_location("skillsaw_custom_rules", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules["skillsaw_custom_rules"] = module
    spec.loader.exec_module(module)
    return module


RULES_MOD = _load_rules_module()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test User",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test User",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


def _write_plugin_manifests(
    repo: Path,
    plugin_name: str,
    *,
    version: str = "1.0.0",
    description: str | None = None,
    author: dict[str, str] | None = None,
) -> None:
    desc = description or f"Description for {plugin_name}, trailing clause"
    auth = author or {"name": "wgordon17"}

    claude_dir = repo / plugin_name / ".claude-plugin"
    claude_dir.mkdir(parents=True, exist_ok=True)
    claude_payload = {
        "name": plugin_name,
        "description": desc,
        "version": version,
        "author": auth,
    }
    (claude_dir / "plugin.json").write_text(
        json.dumps(claude_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    agent_payload = {
        "$schema": RULES_MOD.AGENT_PLUGIN_SCHEMA,
        "name": plugin_name,
        "version": version,
        "description": desc,
        "author": auth,
    }
    (repo / plugin_name / "plugin.json").write_text(
        json.dumps(agent_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _format_marketplace_json(plugins: list[dict[str, Any]]) -> str:
    """Format marketplace.json with single-line author and tags to mirror repo style."""
    entries_text: list[str] = []
    for p in plugins:
        name = json.dumps(p["name"], ensure_ascii=False)
        desc = json.dumps(p["description"], ensure_ascii=False)
        ver = json.dumps(p["version"], ensure_ascii=False)
        auth_name = json.dumps(p.get("author", {"name": "wgordon17"})["name"], ensure_ascii=False)
        tag_items = p.get("tags", ["tag-a", "tag-b"])
        tags = ", ".join(json.dumps(t, ensure_ascii=False) for t in tag_items)
        entry = (
            "    {\n"
            f'      "name": {name},\n'
            f'      "description": {desc},\n'
            f'      "version": {ver},\n'
            f'      "author": {{"name": {auth_name}}},\n'
            f'      "source": "./{p["name"]}",\n'
            '      "category": "development",\n'
            f'      "tags": [{tags}]\n'
            "    }"
        )
        entries_text.append(entry)
    joined = ",\n".join(entries_text)
    return (
        "{\n"
        '  "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",\n'
        '  "name": "test-marketplace",\n'
        '  "plugins": [\n'
        f"{joined}\n"
        "  ]\n"
        "}\n"
    )


def _init_repo(
    tmp_path: Path,
    plugins: dict[str, tuple[str, str]] | None = None,
) -> Path:
    """Initialize a tmp_path git repo with main and origin/main pointing to initial commit."""
    plugin_defs = plugins or {
        "drawio": ("1.0.0", "Draw.io diagram generation skill, synced weekly"),
        "dev-guard": ("1.0.0", "Development guardrails, PreToolUse and Stop hooks"),
    }
    _git(tmp_path, "init", "-b", "main")

    mkt_entries: list[dict[str, Any]] = []
    for name, (ver, desc) in plugin_defs.items():
        _write_plugin_manifests(tmp_path, name, version=ver, description=desc)
        mkt_entries.append(
            {
                "name": name,
                "version": ver,
                "description": desc,
                "author": {"name": "wgordon17"},
                "tags": ["lint", "test"],
            }
        )

    mkt_dir = tmp_path / ".claude-plugin"
    mkt_dir.mkdir(parents=True, exist_ok=True)
    (mkt_dir / "marketplace.json").write_text(
        _format_marketplace_json(mkt_entries),
        encoding="utf-8",
    )

    if "dev-guard" in plugin_defs:
        hooks_dir = tmp_path / "dev-guard" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        _, dg_desc = plugin_defs["dev-guard"]
        suffix = dg_desc.rsplit(",", 1)[-1].strip()
        (hooks_dir / "hooks.json").write_text(
            json.dumps(
                {"description": f"Guard hooks prefix, {suffix}", "hooks": {}},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "initial commit")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    return tmp_path


def _get_skillsaw_version() -> str:
    content = SKILLSAW_YAML.read_text(encoding="utf-8")
    match = re.search(r'^version:\s*"(\d+\.\d+\.\d+)"', content, re.MULTILINE)
    assert match is not None, "Could not extract skillsaw version from .skillsaw.yaml"
    return match.group(1)


class TestSkillsawCustomRules:
    """Unit and integration tests for PluginManifestSyncRule and CLI."""

    def test_clean_repo_passes_check_with_zero_modifications(self, tmp_path: Path) -> None:
        """1. Clean 3-manifest repo passes check() and --check with 0 violations and 0 fixes."""
        repo = _init_repo(tmp_path)
        orig_mkt = (repo / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
        assert '"tags": ["lint", "test"]' in orig_mkt
        assert '"author": {"name": "wgordon17"}' in orig_mkt

        rule = RULES_MOD.PluginManifestSyncRule()
        ctx = SimpleNamespace(root_path=repo)
        violations = rule.check(ctx)
        assert violations == []
        assert rule.fix(ctx, violations) == []

        v_list, fixes = RULES_MOD.compute_manifest_fixes(repo)
        assert v_list == []
        assert fixes == {}

        rc = RULES_MOD.main(["--check", "--repo-root", str(repo)])
        assert rc == 0
        current_mkt = (repo / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
        assert current_mkt == orig_mkt

    def test_drift_detection_and_fix_identity_and_auto_bump_on_desc_change(
        self, tmp_path: Path
    ) -> None:
        """2. Drift in plugin.json or marketplace.json is flagged; fix() syncs & bumps version."""
        repo = _init_repo(tmp_path)
        orig_dev_guard_block = (repo / ".claude-plugin" / "marketplace.json").read_text(
            encoding="utf-8"
        )

        # Edit description only in .claude-plugin/marketplace.json for drawio while version == 1.0.0
        mkt_path = repo / ".claude-plugin" / "marketplace.json"
        mkt_text = mkt_path.read_text(encoding="utf-8").replace(
            "Draw.io diagram generation skill, synced weekly",
            "Draw.io diagram generation skill, updated description",
        )
        mkt_path.write_text(mkt_text, encoding="utf-8")

        rule = RULES_MOD.PluginManifestSyncRule()
        ctx = SimpleNamespace(root_path=repo)
        violations = rule.check(ctx)
        assert len(violations) >= 2

        autofix_results = rule.fix(ctx, violations)
        assert len(autofix_results) > 0

        resolved_root = repo.resolve()
        for res in autofix_results:
            assert res.file_path.is_absolute()
            assert res.file_path.is_relative_to(resolved_root)

        fixed_violation_ids = {id(v) for res in autofix_results for v in res.violations_fixed}
        expected_violation_ids = {id(v) for v in violations}
        assert fixed_violation_ids == expected_violation_ids

        # Apply via CLI --fix and verify single pass syncs all 3 manifests and bumps 1.0.0 -> 1.0.1
        rc = RULES_MOD.main(["--fix", "--repo-root", str(repo)])
        assert rc == 0

        for manifest_rel in (
            Path("drawio/.claude-plugin/plugin.json"),
            Path("drawio/plugin.json"),
        ):
            data = json.loads((repo / manifest_rel).read_text(encoding="utf-8"))
            assert data["version"] == "1.0.1"
            assert data["description"] == "Draw.io diagram generation skill, updated description"

        updated_mkt_text = mkt_path.read_text(encoding="utf-8")
        mkt_data = json.loads(updated_mkt_text)
        drawio_mkt = next(p for p in mkt_data["plugins"] if p["name"] == "drawio")
        assert drawio_mkt["version"] == "1.0.1"
        assert drawio_mkt["description"] == "Draw.io diagram generation skill, updated description"
        # Untouched single-line tags and author formatting preserved
        assert '"tags": ["lint", "test"]' in updated_mkt_text
        assert '"author": {"name": "wgordon17"}' in updated_mkt_text
        assert orig_dev_guard_block.splitlines()[-10:] == updated_mkt_text.splitlines()[-10:]

        assert RULES_MOD.main(["--check", "--repo-root", str(repo)]) == 0

    def test_end_to_end_uvx_skillsaw_fix_and_lint(self, tmp_path: Path) -> None:
        """3. End-to-end uvx skillsaw@<version> fix and lint with plugin-manifest-sync rule."""
        repo = _init_repo(tmp_path)
        scripts_dir = repo / ".github" / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SCRIPT_PATH, scripts_dir / "skillsaw_custom_rules.py")

        skillsaw_ver = _get_skillsaw_version()
        (repo / ".skillsaw.yaml").write_text(
            f'version: "{skillsaw_ver}"\n'
            "strict: true\n"
            "custom-rules:\n"
            '  - ".github/scripts/skillsaw_custom_rules.py"\n'
            "rules:\n"
            "  plugin-manifest-sync:\n"
            "    enabled: true\n"
            "    severity: error\n",
            encoding="utf-8",
        )
        _git(repo, "add", ".github/scripts/skillsaw_custom_rules.py", ".skillsaw.yaml")
        _git(repo, "commit", "-m", "add skillsaw config")
        _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")

        # Introduce drift and non-test plugin modification on drawio
        skill_dir = repo / "drawio" / "skills" / "drawio"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text("# Drawio Skill\n", encoding="utf-8")

        fix_proc = subprocess.run(
            [
                "uvx",
                f"skillsaw@{skillsaw_ver}",
                "fix",
                "--no-plugins",
                "--rule",
                "plugin-manifest-sync",
                str(repo),
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        assert fix_proc.returncode == 0, (
            f"skillsaw fix failed: {fix_proc.stdout}\n{fix_proc.stderr}"
        )

        lint_proc = subprocess.run(
            [
                "uvx",
                f"skillsaw@{skillsaw_ver}",
                "lint",
                "--strict",
                "--no-plugins",
                "--rule",
                "plugin-manifest-sync",
                str(repo),
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        assert lint_proc.returncode == 0, (
            f"skillsaw lint failed after fix: {lint_proc.stdout}\n{lint_proc.stderr}"
        )

        drawio_claude = json.loads(
            (repo / "drawio" / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        assert drawio_claude["version"] == "1.0.1"

    @pytest.mark.parametrize("change_mode", ["committed", "unstaged", "untracked"])
    def test_modifying_non_test_file_requires_bump_and_fix_is_idempotent(
        self, tmp_path: Path, change_mode: str
    ) -> None:
        """4. Modifying non-test plugin file triggers patch bump; 2nd --fix pass is a no-op."""
        repo = _init_repo(tmp_path)
        hook_file = repo / "dev-guard" / "hooks" / "hook.py"
        hook_file.write_text("# initial\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "add hook.py")
        _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")

        if change_mode == "committed":
            hook_file.write_text("# modified\n", encoding="utf-8")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "modify hook.py")
        elif change_mode == "unstaged":
            hook_file.write_text("# modified unstaged\n", encoding="utf-8")
        else:
            skill_file = repo / "dev-guard" / "skills" / "foo" / "SKILL.md"
            skill_file.parent.mkdir(parents=True, exist_ok=True)
            skill_file.write_text("# new untracked skill\n", encoding="utf-8")

        assert RULES_MOD.main(["--check", "--repo-root", str(repo)]) == 1
        assert RULES_MOD.main(["--fix", "--repo-root", str(repo)]) == 0
        assert RULES_MOD.main(["--check", "--repo-root", str(repo)]) == 0

        for rel in (
            "dev-guard/.claude-plugin/plugin.json",
            "dev-guard/plugin.json",
        ):
            assert json.loads((repo / rel).read_text(encoding="utf-8"))["version"] == "1.0.1"
        mkt = json.loads((repo / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        dg_mkt = next(p for p in mkt["plugins"] if p["name"] == "dev-guard")
        assert dg_mkt["version"] == "1.0.1"

        # Second --fix pass is an idempotent no-op
        _, second_fixes = RULES_MOD.compute_manifest_fixes(repo)
        assert second_fixes == {}
        assert RULES_MOD.main(["--fix", "--repo-root", str(repo)]) == 0
        assert (
            json.loads((repo / "dev-guard/.claude-plugin/plugin.json").read_text(encoding="utf-8"))[
                "version"
            ]
            == "1.0.1"
        )

    def test_modifying_only_tests_directory_does_not_require_version_bump(
        self, tmp_path: Path
    ) -> None:
        """5. Modifying only <plugin>/tests/test_foo.py does NOT trigger a bump violation."""
        repo = _init_repo(tmp_path)
        test_file = repo / "dev-guard" / "tests" / "test_foo.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("def test_ok():\n    assert True\n", encoding="utf-8")

        assert RULES_MOD.get_modified_plugins(repo) == set()
        assert RULES_MOD.main(["--check", "--repo-root", str(repo)]) == 0

        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "test: add unit test")
        assert RULES_MOD.get_modified_plugins(repo) == set()
        assert RULES_MOD.main(["--check", "--repo-root", str(repo)]) == 0

    def test_rebase_absorption_scenario_flags_and_bumps_again(self, tmp_path: Path) -> None:
        """6. Rebase absorption: origin/main and branch both at 1.0.1 with changes -> 1.0.2."""
        repo = _init_repo(tmp_path)

        # Advance origin/main to 1.0.1 for drawio
        _write_plugin_manifests(
            repo,
            "drawio",
            version="1.0.1",
            description="Draw.io diagram generation skill, synced weekly",
        )
        mkt_path = repo / ".claude-plugin" / "marketplace.json"
        mkt_path.write_text(
            RULES_MOD._update_marketplace_plugin_in_place(
                mkt_path.read_text(encoding="utf-8"),
                "drawio",
                target_version="1.0.1",
            ),
            encoding="utf-8",
        )
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "chore: bump drawio to 1.0.1 on main")
        _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")

        # Branch now modifies a non-test file in drawio while still at 1.0.1
        (repo / "drawio" / "README.md").write_text("# Updated Drawio\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "feat(drawio): update readme")

        assert RULES_MOD.main(["--check", "--repo-root", str(repo)]) == 1
        assert RULES_MOD.main(["--fix", "--repo-root", str(repo)]) == 0
        assert RULES_MOD.main(["--check", "--repo-root", str(repo)]) == 0

        assert (
            json.loads((repo / "drawio/.claude-plugin/plugin.json").read_text(encoding="utf-8"))[
                "version"
            ]
            == "1.0.2"
        )

    def test_advancing_origin_main_does_not_flag_untouched_plugin(self, tmp_path: Path) -> None:
        """7. origin/main advances bumping Plugin A; branch modifies only Plugin B."""
        repo = _init_repo(tmp_path)
        fork_point = _git(repo, "rev-parse", "HEAD").stdout.strip()

        # Advance origin/main (M2): bump drawio 1.0.0 -> 1.0.1 and update drawio description
        _write_plugin_manifests(
            repo,
            "drawio",
            version="1.0.1",
            description="Draw.io diagram generation skill, updated on origin/main",
        )
        mkt_path = repo / ".claude-plugin" / "marketplace.json"
        mkt_path.write_text(
            RULES_MOD._update_marketplace_plugin_in_place(
                mkt_path.read_text(encoding="utf-8"),
                "drawio",
                target_version="1.0.1",
                target_description="Draw.io diagram generation skill, updated on origin/main",
            ),
            encoding="utf-8",
        )
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "origin/main: bump drawio to 1.0.1")
        _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")

        # Reset branch back to fork_point (M) and modify only dev-guard (Plugin B)
        _git(repo, "reset", "--hard", fork_point)
        (repo / "dev-guard" / "hooks" / "new_hook.py").write_text("x = 1\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "feat(dev-guard): add new_hook.py")

        violations, fixes = RULES_MOD.compute_manifest_fixes(repo)
        # Untouched Plugin A (drawio) must have 0 violations and 0 file fixes
        assert all("drawio" not in v.message for v in violations)
        assert all("drawio" not in str(p) for p in fixes)
        # Modified Plugin B (dev-guard) is flagged
        assert any("dev-guard" in v.message for v in violations)

        assert RULES_MOD.main(["--fix", "--repo-root", str(repo)]) == 0
        assert RULES_MOD.main(["--check", "--repo-root", str(repo)]) == 0

        # Drawio stays at 1.0.0 from fork_point; dev-guard bumped to 1.0.1
        assert (
            json.loads((repo / "drawio/.claude-plugin/plugin.json").read_text(encoding="utf-8"))[
                "version"
            ]
            == "1.0.0"
        )
        assert (
            json.loads((repo / "dev-guard/.claude-plugin/plugin.json").read_text(encoding="utf-8"))[
                "version"
            ]
            == "1.0.1"
        )

    def test_dev_guard_hooks_json_suffix_drift_is_detected_and_synced(self, tmp_path: Path) -> None:
        """8. dev-guard/hooks/hooks.json trailing description phrase drift is synced."""
        repo = _init_repo(tmp_path)
        hooks_path = repo / "dev-guard" / "hooks" / "hooks.json"
        hooks_path.write_text(
            json.dumps(
                {"description": "Guard hooks prefix, outdated trailing suffix", "hooks": {}},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        violations, fixes = RULES_MOD.compute_manifest_fixes(repo)
        assert any(v.file_path == hooks_path for v in violations)
        assert hooks_path in fixes

        assert RULES_MOD.main(["--fix", "--repo-root", str(repo)]) == 0
        synced_hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        assert synced_hooks["description"] == "Guard hooks prefix, PreToolUse and Stop hooks"
        assert RULES_MOD.main(["--check", "--repo-root", str(repo)]) == 0

    def test_mcp_json_sync_preserves_streamable_http_and_stdio(self, tmp_path: Path) -> None:
        """9. <plugin>/.mcp.json args/env/headers change is synced into <plugin>/mcp.json."""
        repo = _init_repo(
            tmp_path,
            plugins={
                "fetchaller-mcp": ("1.0.0", "Fetchaller MCP plugin, stdio server"),
                "github-mcp": ("1.0.0", "GitHub MCP plugin, http server"),
            },
        )
        # Seed stdio .mcp.json and http .mcp.json
        (repo / "fetchaller-mcp" / ".mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "fetchaller": {
                            "type": "stdio",
                            "command": "uvx",
                            "args": ["fetchaller@0.2.0"],
                            "env": {"FOO": "bar"},
                        }
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (repo / "github-mcp" / ".mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "github": {
                            "type": "http",
                            "url": "https://api.githubcopilot.com/mcp/",
                            "headers": {"X-MCP-Readonly": "true"},
                        }
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        # Outdated root mcp.json files
        (repo / "fetchaller-mcp" / "mcp.json").write_text(
            json.dumps(
                {
                    "$schema": RULES_MOD.AGENT_MCP_SCHEMA,
                    "mcpServers": {
                        "fetchaller": {
                            "type": "stdio",
                            "command": "uvx",
                            "args": ["fetchaller@0.1.0"],
                        }
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (repo / "github-mcp" / "mcp.json").write_text(
            json.dumps(
                {
                    "$schema": RULES_MOD.AGENT_MCP_SCHEMA,
                    "mcpServers": {
                        "github": {
                            "type": "stdio",
                            "url": "https://api.githubcopilot.com/mcp/",
                        }
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        assert RULES_MOD.main(["--check", "--repo-root", str(repo)]) == 1
        assert RULES_MOD.main(["--fix", "--repo-root", str(repo)]) == 0
        assert RULES_MOD.main(["--check", "--repo-root", str(repo)]) == 0

        fetchaller_mcp = json.loads(
            (repo / "fetchaller-mcp" / "mcp.json").read_text(encoding="utf-8")
        )
        assert fetchaller_mcp["$schema"] == RULES_MOD.AGENT_MCP_SCHEMA
        assert fetchaller_mcp["mcpServers"]["fetchaller"] == {
            "type": "stdio",
            "command": "uvx",
            "args": ["fetchaller@0.2.0"],
            "env": {"FOO": "bar"},
        }

        github_mcp = json.loads((repo / "github-mcp" / "mcp.json").read_text(encoding="utf-8"))
        assert github_mcp["$schema"] == RULES_MOD.AGENT_MCP_SCHEMA
        assert github_mcp["mcpServers"]["github"] == {
            "type": "streamable-http",
            "url": "https://api.githubcopilot.com/mcp/",
            "headers": {"X-MCP-Readonly": "true"},
        }

    def test_bump_plugin_single_and_multi_without_origin_main(self, tmp_path: Path) -> None:
        """10. --bump-plugin increments single and multiple plugins even without origin/main."""
        repo = _init_repo(tmp_path)
        _git(repo, "update-ref", "-d", "refs/remotes/origin/main")

        # Single plugin bump
        assert RULES_MOD.main(["--bump-plugin", "drawio", "--repo-root", str(repo)]) == 0
        assert (
            json.loads((repo / "drawio/.claude-plugin/plugin.json").read_text(encoding="utf-8"))[
                "version"
            ]
            == "1.0.1"
        )
        assert (
            json.loads((repo / "dev-guard/.claude-plugin/plugin.json").read_text(encoding="utf-8"))[
                "version"
            ]
            == "1.0.0"
        )

        # Multi-plugin bump in a single pass accumulates both in marketplace.json
        assert (
            RULES_MOD.main(
                [
                    "--bump-plugin",
                    "drawio",
                    "--bump-plugin",
                    "dev-guard",
                    "--repo-root",
                    str(repo),
                ]
            )
            == 0
        )
        for plugin, expected_ver in (("drawio", "1.0.2"), ("dev-guard", "1.0.1")):
            assert (
                json.loads(
                    (repo / plugin / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
                )["version"]
                == expected_ver
            )
            assert (
                json.loads((repo / plugin / "plugin.json").read_text(encoding="utf-8"))["version"]
                == expected_ver
            )

        mkt = json.loads((repo / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        versions_by_name = {p["name"]: p["version"] for p in mkt["plugins"]}
        assert versions_by_name == {"drawio": "1.0.2", "dev-guard": "1.0.1"}

    def test_supply_chain_blocked_marker_aborts_fix_with_exit_1(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """11. .khepri-supply-chain-blocked causes --fix to print abort msg to stderr and exit 1."""
        repo = _init_repo(tmp_path)
        # Create both a modified file and the supply-chain block marker
        (repo / "drawio" / "README.md").write_text("# Modified\n", encoding="utf-8")
        (repo / ".khepri-supply-chain-blocked").write_text("blocked\n", encoding="utf-8")

        before_claude = (repo / "drawio" / ".claude-plugin" / "plugin.json").read_text(
            encoding="utf-8"
        )
        before_agent = (repo / "drawio" / "plugin.json").read_text(encoding="utf-8")
        before_mkt = (repo / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")

        rc = RULES_MOD.main(["--fix", "--repo-root", str(repo)])
        captured = capsys.readouterr()

        assert rc == 1
        assert (
            "[SUPPLY_CHAIN_BLOCK] Aborting manifest fix: .khepri-supply-chain-blocked present"
            in captured.err
        )
        assert (repo / "drawio" / ".claude-plugin" / "plugin.json").read_text(
            encoding="utf-8"
        ) == before_claude
        assert (repo / "drawio" / "plugin.json").read_text(encoding="utf-8") == before_agent
        assert (repo / ".claude-plugin" / "marketplace.json").read_text(
            encoding="utf-8"
        ) == before_mkt

    def test_security_rejects_flag_base_ref_unknown_plugin_and_symlinks(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Security boundary checks: invalid base_ref, unknown plugin, and symlink escape."""
        repo = _init_repo(tmp_path)

        # Flag injection in --base-ref
        assert RULES_MOD.main(["--check", "--base-ref=-evil", "--repo-root", str(repo)]) == 1
        assert "Invalid git base_ref" in capsys.readouterr().err

        # Unknown plugin in --bump-plugin
        assert RULES_MOD.main(["--bump-plugin", "nonexistent", "--repo-root", str(repo)]) == 1
        assert "Unknown plugin(s) in --bump-plugin" in capsys.readouterr().err

        # Symlinked manifest is rejected
        outside = tmp_path.parent / f"outside_{tmp_path.name}.json"
        outside.write_text("{}\n", encoding="utf-8")
        try:
            agent_manifest = repo / "drawio" / "plugin.json"
            agent_manifest.unlink()
            agent_manifest.symlink_to(outside)
            assert RULES_MOD.main(["--fix", "--repo-root", str(repo)]) == 1
            assert "Refusing symlinked manifest path" in capsys.readouterr().err
        finally:
            outside.unlink(missing_ok=True)
