"""Unit and integration tests for Betterleaks overlay compiler and Git 2.54+ hook."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
import tomli_w
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SYNC_SCRIPT_PATH = REPO_ROOT / "git-tools" / "scripts" / "sync-betterleaks-overlay.py"
HOOK_SCRIPT_PATH = REPO_ROOT / "git-tools" / "git-hooks" / "betterleaks-hook.sh"


def _load_sync_module():
    spec = importlib.util.spec_from_file_location("sync_betterleaks_overlay", SYNC_SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["sync_betterleaks_overlay"] = module
    spec.loader.exec_module(module)
    return module


sync_mod = _load_sync_module()


def _build_fixture_feeds() -> tuple[str, str]:
    """Create synthetic LeakTK TOML and Veles YAML containing all 22 + 14 curated rule IDs."""
    leaktk_rules: list[dict[str, Any]] = []
    for orig_id, readable_id in sync_mod.CURATED_LEAKTK_RULE_IDS.items():
        rule: dict[str, Any] = {
            "id": orig_id,
            "description": f"Desc for {readable_id}",
            "regex": r"(secret_[0-9]+)",
            "keywords": ["Secret", "TOKEN"],
            "tags": ["key", "leaktk"],
        }
        if orig_id == "KWxkfMbO7cM":
            rule["entropy"] = 3.5
            rule["allowlist"] = {
                "paths": [r"tests/.*\.py", r"docs/`special`\.md"],
                "regexTarget": "line",
                "regexes": [r"LTKF\{public\}"],
                "stopwords": ["example"],
                "condition": "OR",
            }
        leaktk_rules.append(rule)

    veles_rules: list[dict[str, Any]] = []
    for vid in sync_mod.CURATED_VELES_RULES:
        vrule: dict[str, Any] = {
            "id": vid,
            "name": f"Veles {vid}",
            "pattern": (
                r"(?P<TOKEN_ID>bw_id)_(secret)"
                if vid == "veles.secrets/bitwardenoauth2access"
                else r"(veles_secret_[0-9]+)"
            ),
        }
        if vid == "veles.secrets/hcpclientcredentials-client-id":
            vrule["visible"] = False
            vrule["path"] = r".*\.hcl$"
        if vid == "veles.secrets/hcpclientcredentials":
            vrule["depends_on_rule"] = [{"rule_id": "veles.secrets/hcpclientcredentials-client-id"}]
        veles_rules.append(vrule)

    lt_toml = tomli_w.dumps({"rules": leaktk_rules})
    veles_yaml = yaml.safe_dump({"rules": veles_rules})
    return lt_toml, veles_yaml


class TestSyncBetterleaksOverlayUnit:
    def test_quote_expr_regex(self):
        assert sync_mod._quote_expr_regex(r"foo\d+") == r"`foo\d+`"
        assert sync_mod._quote_expr_regex("has`backtick") == '"has`backtick"'

    def test_translate_allowlist_to_v2_expr_variants(self):
        assert sync_mod._translate_allowlist_to_v2_expr({"id": "r1"}) is None

        rule_or = {
            "id": "r2",
            "entropy": 4.25,
            "allowlist": {
                "paths": ["a/b"],
                "regexTarget": "invalid_target",
                "regexes": ["test_.*"],
                "stopwords": ["dummy"],
                "condition": "OR",
            },
        }
        expr_or = sync_mod._translate_allowlist_to_v2_expr(rule_or)
        assert expr_or is not None
        assert 'entropy(finding["secret"]) <= 4.25' in expr_or
        assert 'matchesAny(attributes["path"]' in expr_or
        assert 'matchesAny(finding["secret"]' in expr_or
        assert 'containsAny(finding["secret"]' in expr_or
        assert " || " in expr_or

        rule_and = {
            "id": "r3",
            "allowlists": [
                {
                    "paths": ["src/.*"],
                    "regexTarget": "match",
                    "regexes": ["abc"],
                    "condition": "AND",
                }
            ],
        }
        expr_and = sync_mod._translate_allowlist_to_v2_expr(rule_and)
        assert expr_and is not None
        assert 'matchesAny(finding["match"]' in expr_and
        assert " && " in expr_and

    @pytest.mark.parametrize("bad_entropy", ["inf", "-inf", "nan", float("inf"), float("nan")])
    def test_translate_allowlist_rejects_non_finite_entropy(self, bad_entropy):
        with pytest.raises(ValueError, match="Non-finite entropy"):
            sync_mod._translate_allowlist_to_v2_expr({"id": "bad", "entropy": bad_entropy})

    def test_write_secure_permissions_and_atomic_replace(self, tmp_path: Path):
        target = tmp_path / "sub" / "config.toml"
        sync_mod._write_secure(target, "title = 'test'\n")
        assert target.read_text(encoding="utf-8") == "title = 'test'\n"
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700

    def test_fetch_or_cache_modes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        cache_file = tmp_path / "cache" / "feed.toml"

        # Offline miss -> FileNotFoundError
        with pytest.raises(FileNotFoundError, match="Offline mode requested"):
            sync_mod._fetch_or_cache("https://example.invalid/feed", cache_file, offline=True)

        # Online success writes cache
        def fake_run_ok(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="rules = []\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run_ok)
        out = sync_mod._fetch_or_cache("https://example.invalid/feed", cache_file, offline=False)
        assert out == "rules = []\n"
        assert cache_file.read_text(encoding="utf-8") == "rules = []\n"

        # Offline hit reads cache without subprocess
        def fake_run_fail(cmd, **kwargs):
            raise subprocess.CalledProcessError(1, cmd)

        monkeypatch.setattr(subprocess, "run", fake_run_fail)
        assert (
            sync_mod._fetch_or_cache("https://example.invalid/feed", cache_file, offline=True)
            == "rules = []\n"
        )
        # Online failure falls back to existing cache
        assert (
            sync_mod._fetch_or_cache("https://example.invalid/feed", cache_file, offline=False)
            == "rules = []\n"
        )

    def test_build_v2_compatible_overlay_rules_and_missing_rule_guard(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("BETTERLEAKS_CONFIG_DIR", str(tmp_path))
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True)
        lt_toml, veles_yaml = _build_fixture_feeds()
        (cache_dir / "leaktk-public-8.27.0.toml").write_text(lt_toml, encoding="utf-8")
        (cache_dir / "veles.yml").write_text(veles_yaml, encoding="utf-8")

        rules = sync_mod.build_v2_compatible_overlay_rules(offline=True)
        assert len(rules) == 36
        by_id = {r["id"]: r for r in rules}
        # htpasswd regex override
        assert by_id["leaktk-htpasswd-file"]["regex"].startswith(r'(?:^|\n)[^:\s"/]{1,64}:')
        # bitwarden named group normalized to non-capturing group
        assert "(?P<TOKEN_ID>" not in by_id["veles.secrets/bitwardenoauth2access"]["regex"]
        assert by_id["veles.secrets/hcpclientcredentials-client-id"]["skipReport"] is True
        assert by_id["veles.secrets/hcpclientcredentials"]["components"] == [
            {"id": "veles.secrets/hcpclientcredentials-client-id"}
        ]

        # Dropping one curated rule must raise RuntimeError
        partial_lt = tomli_w.dumps({"rules": tomllib.loads(lt_toml)["rules"][1:]})
        (cache_dir / "leaktk-public-8.27.0.toml").write_text(partial_lt, encoding="utf-8")
        with pytest.raises(RuntimeError, match="Curated overlay rule verification failed"):
            sync_mod.build_v2_compatible_overlay_rules(offline=True)

    @pytest.mark.parametrize("is_v2", [False, True], ids=["v1-merged", "v2-extend"])
    def test_sync_all_end_to_end_isolated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, is_v2: bool
    ):
        monkeypatch.setenv("BETTERLEAKS_CONFIG_DIR", str(tmp_path))
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True)
        lt_toml, veles_yaml = _build_fixture_feeds()
        (cache_dir / "leaktk-public-8.27.0.toml").write_text(lt_toml, encoding="utf-8")
        (cache_dir / "veles.yml").write_text(veles_yaml, encoding="utf-8")

        ver_str = "betterleaks version 2.0.1" if is_v2 else "betterleaks version 1.9.0"
        builtin_cfg = tomli_w.dumps(
            {
                "title": "Built-in",
                "filter": 'matchesAny(finding["secret"], [`builtin`])',
                "rules": [
                    {"id": "builtin-1", "description": "b1", "regex": "(b1)"},
                    # Include an overlapping ID to verify built-in/overlay rule deduplication
                    {"id": "leaktk-flag", "description": "dup", "regex": "(dup)"},
                ],
            }
        )

        def fake_check_output(cmd, **kwargs):
            if cmd == ["betterleaks", "--version"]:
                return ver_str + "\n"
            if cmd == ["betterleaks", "config", "show"]:
                if is_v2:
                    raise AssertionError("betterleaks config show should not be called on v2")
                return builtin_cfg
            raise AssertionError(f"Unexpected check_output: {cmd}")

        def fake_run(cmd, **kwargs):
            if cmd[:3] in (["betterleaks", "config", "check"], ["betterleaks", "config", "show"]):
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if cmd[:2] == ["betterleaks", "stdin"]:
                out = (
                    json.dumps({"findings": [{"id": "1"}, {"id": "2"}]})
                    if is_v2
                    else json.dumps([{"id": "1"}, {"id": "2"}])
                )
                return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
            raise AssertionError(f"Unexpected run: {cmd}")

        monkeypatch.setattr(subprocess, "check_output", fake_check_output)
        monkeypatch.setattr(subprocess, "run", fake_run)

        summary = sync_mod.sync_all(offline=True)
        assert summary["installed_betterleaks"] == ver_str
        assert (tmp_path / "overlay-v2.toml").is_file()
        assert (tmp_path / "betterleaks-unified.toml").is_file()
        assert (tmp_path / ".compiled-for-version").read_text(encoding="utf-8").strip() == ver_str
        assert r"os\.environ/" in sync_mod.NOTSECRET_FILTER_EXPR

        if is_v2:
            assert summary["schema_mode"] == "v2_extend_overlay"
            assert summary["builtin_rules"] == 0
            assert summary["total_active_rules"] == 36
        else:
            assert summary["schema_mode"] == "v1_merged_v2_ready_rules"
            assert summary["builtin_rules"] == 2
            # 2 builtin + 36 overlay - 1 overlapping ("leaktk-flag") = 37
            assert summary["total_active_rules"] == 37

    def test_sync_all_smoke_test_failure_rolls_back_staging(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("BETTERLEAKS_CONFIG_DIR", str(tmp_path))
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True)
        lt_toml, veles_yaml = _build_fixture_feeds()
        (cache_dir / "leaktk-public-8.27.0.toml").write_text(lt_toml, encoding="utf-8")
        (cache_dir / "veles.yml").write_text(veles_yaml, encoding="utf-8")
        existing_active = tmp_path / "betterleaks-unified.toml"
        existing_active.write_text("title = 'existing'\n", encoding="utf-8")

        monkeypatch.setattr(
            subprocess,
            "check_output",
            lambda cmd, **kw: "betterleaks version 2.0.1\n",
        )

        def fake_run_bad_smoke(cmd, **kw):
            if cmd[:3] == ["betterleaks", "config", "check"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if cmd[:2] == ["betterleaks", "stdin"]:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=json.dumps({"findings": [{"id": "only-1"}]}), stderr=""
                )
            raise AssertionError(f"Unexpected run: {cmd}")

        monkeypatch.setattr(subprocess, "run", fake_run_bad_smoke)

        with pytest.raises(RuntimeError, match="Smoke test expected 2 findings"):
            sync_mod.sync_all(offline=True)

        assert existing_active.read_text(encoding="utf-8") == "title = 'existing'\n"
        assert list(tmp_path.glob("*.staging")) == []


class TestBetterleaksHookSubprocess:
    def _make_executable(self, path: Path, content: str) -> Path:
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)
        return path

    def test_missing_betterleaks_binary_fails_closed(self, tmp_path: Path):
        hook_copy = tmp_path / "betterleaks-hook.sh"
        # Strip Homebrew fallback PATH in isolated copy so host betterleaks is hidden
        text = HOOK_SCRIPT_PATH.read_text(encoding="utf-8").replace(
            ":/opt/homebrew/bin:/usr/local/bin", ""
        )
        self._make_executable(hook_copy, text)

        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "BETTERLEAKS_CONFIG_DIR": str(tmp_path / "cfg"),
        }
        proc = subprocess.run(["bash", str(hook_copy)], env=env, capture_output=True, text=True)
        assert proc.returncode == 1
        assert "betterleaks: binary not found in PATH; refusing commit" in proc.stderr

    def test_missing_active_config_when_sync_unavailable_fails_closed(self, tmp_path: Path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        self._make_executable(
            bin_dir / "betterleaks",
            "#!/bin/sh\n"
            'if [ "$1" = "--version" ]; then echo \'betterleaks 2.0.0\'; exit 0; fi\n'
            "exit 0\n",
        )
        # Copy hook into a directory with no ../scripts/sync-betterleaks-overlay.py and no uv
        hook_dir = tmp_path / "hooks"
        hook_dir.mkdir()
        hook_copy = self._make_executable(
            hook_dir / "betterleaks-hook.sh",
            HOOK_SCRIPT_PATH.read_text(encoding="utf-8").replace(
                ":/opt/homebrew/bin:/usr/local/bin", ""
            ),
        )
        env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            "BETTERLEAKS_CONFIG_DIR": str(tmp_path / "cfg"),
        }
        proc = subprocess.run(["bash", str(hook_copy)], env=env, capture_output=True, text=True)
        assert proc.returncode == 1
        assert "betterleaks: active config missing or out-of-date" in proc.stderr

    @pytest.mark.parametrize(
        "version_out, ignore_file, expected_flag",
        [
            ("betterleaks version 2.1.0", None, None),
            ("betterleaks version 1.9.0", None, None),
            ("betterleaks version 1.9.0", ".betterleaksignore", ".betterleaksignore"),
            ("betterleaks version 1.9.0", ".gitleaksignore", ".gitleaksignore"),
        ],
    )
    def test_hook_dispatches_v1_and_v2_cli_via_path_shim(
        self,
        tmp_path: Path,
        version_out: str,
        ignore_file: str | None,
        expected_flag: str | None,
    ):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        args_log = tmp_path / "args.json"
        self._make_executable(
            bin_dir / "betterleaks",
            f"""#!/bin/bash
if [ "$1" = "--version" ]; then
    echo "{version_out}"
    exit 0
fi
printf '%s\\n' "$@" > "{args_log}"
exit 0
""",
        )

        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        (cfg_dir / "betterleaks-unified.toml").write_text("rules = []\n", encoding="utf-8")
        (cfg_dir / ".compiled-for-version").write_text(version_out + "\n", encoding="utf-8")

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
        if ignore_file:
            (repo_dir / ignore_file).write_text("", encoding="utf-8")

        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '/usr/bin:/bin')}"
        env["BETTERLEAKS_CONFIG_DIR"] = str(cfg_dir)

        proc = subprocess.run(
            ["bash", str(HOOK_SCRIPT_PATH)],
            cwd=repo_dir,
            env=env,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        recorded = args_log.read_text(encoding="utf-8").splitlines()
        assert recorded[0] == "git"
        assert "--staged" in recorded
        assert "--redact" in recorded
        if "2.1.0" in version_out:
            assert "--pre-commit" not in recorded
            assert "--gitleaks-ignore-path" not in recorded
        elif expected_flag is None:
            assert "--pre-commit" in recorded
            assert "--gitleaks-ignore-path" not in recorded
        else:
            assert "--pre-commit" in recorded
            assert "--gitleaks-ignore-path" in recorded
            assert any(line.endswith(expected_flag) for line in recorded)

    def test_version_mismatch_triggers_sync_offline_fallback_and_prefers_repo_script(
        self, tmp_path: Path
    ):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        sync_log = tmp_path / "sync_invocations.log"
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()

        # Poisoned $CONFIG_DIR/sync_overlay.py must not shadow the repo script
        (cfg_dir / "sync_overlay.py").write_text("raise SystemExit(99)\n", encoding="utf-8")

        self._make_executable(
            bin_dir / "uv",
            f"""#!/bin/bash
echo "$*" >> "{sync_log}"
if [[ "$*" == *"--offline"* ]]; then
    exit 1
fi
mkdir -p "{cfg_dir}"
echo "rules = []" > "{cfg_dir}/betterleaks-unified.toml"
echo "betterleaks version 2.0.0" > "{cfg_dir}/.compiled-for-version"
exit 0
""",
        )
        self._make_executable(
            bin_dir / "betterleaks",
            """#!/bin/bash
if [ "$1" = "--version" ]; then
    echo "betterleaks version 2.0.0"
    exit 0
fi
exit 0
""",
        )

        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '/usr/bin:/bin')}"
        env["BETTERLEAKS_CONFIG_DIR"] = str(cfg_dir)

        proc = subprocess.run(
            ["bash", str(HOOK_SCRIPT_PATH)],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        invocations = sync_log.read_text(encoding="utf-8").splitlines()
        assert len(invocations) == 2
        assert invocations[0].endswith("sync-betterleaks-overlay.py --offline")
        assert invocations[1].endswith("sync-betterleaks-overlay.py")
        assert "sync_overlay.py" not in "\n".join(invocations)
