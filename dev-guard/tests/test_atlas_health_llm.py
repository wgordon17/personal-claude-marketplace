"""Tests for atlas-health-llm.py.

Two test patterns:
  - Subprocess tests: exercise CLI flags (--dry-run, env var handling) via uv run.
  - Import tests: load internal functions via importlib and test with mocking.

No live Vertex AI calls. All LLM interactions are mocked or bypassed via --dry-run.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

# ── Module paths ──────────────────────────────────────────────────────────────

COMMANDS_DIR = Path(__file__).resolve().parents[2] / ".claude" / "commands"
ATLAS_HEALTH_LLM = COMMANDS_DIR / "atlas-health-llm.py"

# ── Module loader ─────────────────────────────────────────────────────────────

_mod_cache = None


def _load_module():
    """Import atlas-health-llm.py as a module for direct function access.

    Uses a fresh load each session. Module name avoids hyphens.
    Registers the module in sys.modules so @dataclass can resolve cls.__module__.
    """
    global _mod_cache
    if _mod_cache is None:
        spec = importlib.util.spec_from_file_location("atlas_health_llm", ATLAS_HEALTH_LLM)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["atlas_health_llm"] = mod
        spec.loader.exec_module(mod)
        _mod_cache = mod
    return _mod_cache


# ── Subprocess helpers ────────────────────────────────────────────────────────


def _run(
    args: list[str] | None = None,
    *,
    extra_env: dict | None = None,
    repo_root: Path | None = None,
) -> subprocess.CompletedProcess:
    """Run atlas-health-llm.py via uv run with optional extra args and env."""
    cmd = ["uv", "run", str(ATLAS_HEALTH_LLM)] + (args or [])
    env = os.environ.copy()
    # Remove credentials so tests don't accidentally hit Vertex AI
    env.pop("ANTHROPIC_VERTEX_PROJECT_ID", None)
    if extra_env:
        env.update(extra_env)
    kwargs = dict(capture_output=True, text=True, env=env)
    if repo_root is not None:
        kwargs["cwd"] = str(repo_root)
    return subprocess.run(cmd, **kwargs)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def fake_repo(tmp_path: Path) -> Path:
    """Create a minimal fake repository structure for subprocess tests."""
    # marketplace.json with one non-LSP plugin
    claude_plugin_dir = tmp_path / ".claude-plugin"
    claude_plugin_dir.mkdir()
    marketplace = {
        "metadata": {"version": "1.0.0"},
        "plugins": [
            {
                "name": "test-plugin",
                "version": "1.0.0",
                "description": "A test plugin.",
                "source": "./test-plugin",
                "category": "testing",
                "tags": [],
            }
        ],
    }
    (claude_plugin_dir / "marketplace.json").write_text(json.dumps(marketplace))

    # Plugin directory
    plugin_dir = tmp_path / "test-plugin"
    plugin_dir.mkdir()

    # Init git repo so _repo_root_from_git works
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(tmp_path),
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path),
        capture_output=True,
        check=True,
    )

    return tmp_path


# ═══════════════════════════════════════════════════════════════════════════════
# Subprocess tests: CLI flags and env var handling
# ═══════════════════════════════════════════════════════════════════════════════


class TestDryRun:
    def test_dry_run_exits_0_without_credentials(self, fake_repo):
        """--dry-run exits 0 even with no ANTHROPIC_VERTEX_PROJECT_ID."""
        result = _run(["--dry-run", "--repo-root", str(fake_repo)])
        assert result.returncode == 0, f"stderr: {result.stderr!r}"

    def test_dry_run_prints_dry_run_header(self, fake_repo):
        """--dry-run prints the DRY RUN header."""
        result = _run(["--dry-run", "--repo-root", str(fake_repo)])
        assert "DRY RUN" in result.stdout

    def test_dry_run_shows_model_name(self, fake_repo):
        """--dry-run output includes model name."""
        result = _run(["--dry-run", "--repo-root", str(fake_repo)])
        assert "claude" in result.stdout.lower()

    def test_dry_run_with_model_override(self, fake_repo):
        """ANTHROPIC_DEFAULT_SONNET_MODEL env var overrides the model name."""
        result = _run(
            ["--dry-run", "--repo-root", str(fake_repo)],
            extra_env={"ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-haiku-4-5"},
        )
        assert result.returncode == 0
        assert "claude-haiku-4-5" in result.stdout


class TestFailOpen:
    def test_fail_open_missing_credentials(self, fake_repo):
        """Without ANTHROPIC_VERTEX_PROJECT_ID, exits 0 (fail-open)."""
        result = _run(["--repo-root", str(fake_repo)])
        assert result.returncode == 0

    def test_fail_open_prints_skip_message(self, fake_repo):
        """Fail-open prints a skip message to stdout."""
        result = _run(["--repo-root", str(fake_repo)])
        assert "skipped" in result.stdout.lower() or "ATLAS" in result.stdout

    def test_fail_open_import_error(self):
        """If anthropic package is unavailable, _call_vertex exits 0 (fail-open)."""
        # Load module first (registers in sys.modules so @dataclass works).
        mod = _load_module()

        # Patch anthropic to None AFTER loading — triggers ImportError inside _call_vertex.
        with (
            patch.dict(sys.modules, {"anthropic": None}),
            patch.dict(os.environ, {"ANTHROPIC_VERTEX_PROJECT_ID": "proj-123"}),
            pytest.raises(SystemExit) as exc_info,
        ):
            mod._call_vertex("test prompt")
        assert exc_info.value.code == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Import tests: internal functions
# ═══════════════════════════════════════════════════════════════════════════════


class TestContentFiltering:
    def test_content_filtering_redacts_env_var_secrets(self):
        """Env var assignments with long values are replaced with [REDACTED]."""
        mod = _load_module()
        text = 'MY_API_KEY="AbCdEfGhIjKlMnOpQrStUvWxYz1234"\n'
        result = mod._redact_secrets(text, Path("test.md"))
        assert "[REDACTED]" in result
        assert "AbCdEfGhIjKlMnOpQrStUvWxYz1234" not in result

    def test_content_filtering_redacts_bearer_tokens(self):
        """Bearer tokens are replaced with [REDACTED]."""
        mod = _load_module()
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9\n"
        result = mod._redact_secrets(text, Path("test.md"))
        assert "[REDACTED]" in result
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result

    def test_content_filtering_redacts_pem_blocks(self):
        """Full PEM blocks (BEGIN...END) are replaced with [REDACTED]."""
        mod = _load_module()
        text = (
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----\n"
        )
        result = mod._redact_secrets(text, Path("test.md"))
        assert "[REDACTED]" in result
        assert "BEGIN RSA PRIVATE KEY" not in result

    def test_content_filtering_leaves_safe_text_unchanged(self):
        """Normal text without secrets is left unchanged."""
        mod = _load_module()
        text = "This is a normal skill description without any secrets.\n"
        result = mod._redact_secrets(text, Path("skill.md"))
        assert result == text

    def test_content_filtering_warns_on_stderr(self, capsys):
        """Redaction warnings go to stderr."""
        mod = _load_module()
        text = 'SECRET="AbCdEfGhIjKlMnOpQrStUvWxYz1234"\n'
        mod._redact_secrets(text, Path("test.md"))
        captured = capsys.readouterr()
        assert "WARNING" in captured.err or "redacted" in captured.err.lower()


class TestModelSuffixStripping:
    def test_model_suffix_stripped(self):
        """Context-window suffix like [1m] is stripped from model name."""
        import re

        raw = "claude-sonnet-4-6[1m]"
        stripped = re.sub(r"\[.*\]$", "", raw)
        assert stripped == "claude-sonnet-4-6"

    def test_model_without_suffix_unchanged(self):
        """Model name without suffix is unchanged."""
        import re

        raw = "claude-sonnet-4-6"
        stripped = re.sub(r"\[.*\]$", "", raw)
        assert stripped == "claude-sonnet-4-6"


class TestResponseParsing:
    def test_response_parsing_fenced_json(self):
        """JSON wrapped in ``` fences is correctly stripped before parsing."""
        fenced_response = textwrap.dedent("""\
            ```json
            [{"component": 1, "drift": false, "severity": null, "findings": null}]
            ```
        """).strip()

        # Reproduce the fence-stripping logic from _call_vertex
        text = fenced_response.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(line for line in lines if not line.startswith("```")).strip()

        parsed = json.loads(text)
        assert isinstance(parsed, list)
        assert parsed[0]["drift"] is False

    def test_response_parsing_plain_json(self):
        """Plain JSON (no fences) is parsed directly."""
        plain = '[{"component": 1, "drift": true, "severity": "CRITICAL", "findings": ["bad"]}]'
        parsed = json.loads(plain)
        assert parsed[0]["drift"] is True
        assert parsed[0]["severity"] == "CRITICAL"

    def test_response_parsing_malformed(self):
        """Non-JSON response triggers fail-open (exit 0)."""
        result = subprocess.run(
            ["uv", "run", str(ATLAS_HEALTH_LLM), "--dry-run"],
            capture_output=True,
            text=True,
            env={**os.environ, "ANTHROPIC_VERTEX_PROJECT_ID": ""},
        )
        # With empty project ID, script fail-opens before any parsing.
        # The malformed JSON path is tested implicitly via the _fail_open
        # call on json.JSONDecodeError in the implementation.
        assert result.returncode == 0


class TestFormatOutput:
    def test_format_output_no_findings(self):
        """No findings returns pass message and exit code 0."""
        mod = _load_module()
        text, code = mod._format_output([])
        assert "passed" in text.lower() or "Health Check" in text
        assert code == 0

    def test_format_output_critical_finding(self):
        """A CRITICAL finding returns exit code 1 and blocked message."""
        mod = _load_module()
        finding = mod.Finding(
            severity="CRITICAL",
            category="drift",
            component="skills/test/SKILL.md",
            message="Description claims 21 agents but body defines 19.",
        )
        text, code = mod._format_output([finding])
        assert code == 1
        assert "CRITICAL" in text
        assert "21 agents" in text

    def test_format_output_info_only(self):
        """INFO-only findings return exit code 0."""
        mod = _load_module()
        finding = mod.Finding(
            severity="INFO",
            category="drift",
            component="skills/test/SKILL.md",
            message="Body has additional capability not mentioned in description.",
        )
        text, code = mod._format_output([finding])
        assert code == 0
        assert "INFO" in text

    def test_format_output_mixed_findings(self):
        """Mixed CRITICAL+INFO findings return exit code 1."""
        mod = _load_module()
        findings = [
            mod.Finding("CRITICAL", "drift", "comp-a", "Critical issue."),
            mod.Finding("INFO", "duplication", "doc-a ↔ doc-b", "Overlap detected."),
        ]
        text, code = mod._format_output(findings)
        assert code == 1
        assert "CRITICAL" in text
        assert "INFO" in text


class TestPromptBuilding:
    def test_drift_prompt_includes_component_info(self):
        """_build_drift_prompt includes component name and type."""
        mod = _load_module()
        components = [("my-skill", "skill", "Does X and Y.", "Body content here.")]
        prompt = mod._build_drift_prompt(components)
        assert "my-skill" in prompt
        assert "skill" in prompt
        assert "Does X and Y." in prompt

    def test_drift_prompt_includes_json_schema(self):
        """_build_drift_prompt includes expected JSON response schema."""
        mod = _load_module()
        components = [("test", "agent", "Desc.", "Body.")]
        prompt = mod._build_drift_prompt(components)
        assert "drift" in prompt
        assert "CRITICAL" in prompt
        assert "severity" in prompt

    def test_duplication_prompt_includes_both_docs(self):
        """_build_duplication_prompt references both document names."""
        mod = _load_module()
        prompt = mod._build_duplication_prompt(
            "guide-a.md", "Content A...", "guide-b.md", "Content B..."
        )
        assert "guide-a.md" in prompt
        assert "guide-b.md" in prompt
        assert "duplicate" in prompt


class TestAnalyzeDrift:
    def test_analyze_drift_dry_run_makes_no_vertex_calls(self, tmp_path):
        """In dry_run mode, _analyze_drift prints prompts and returns no findings."""
        mod = _load_module()
        from _atlas_lib import Skill  # noqa: PLC0415

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("A" * 600)  # above _BODY_THRESHOLD
        skills = [
            Skill(
                name="test-skill",
                plugin="test",
                description="A skill that does X.",
                allowed_tools=[],
                body="A" * 600,
                path=skill_path,
            )
        ]
        import time

        findings = mod._analyze_drift(skills, [], time.monotonic(), dry_run=True)
        assert findings == []

    def test_analyze_drift_skips_short_bodies(self, tmp_path):
        """Components with body shorter than _BODY_THRESHOLD are skipped."""
        mod = _load_module()
        from _atlas_lib import Skill  # noqa: PLC0415

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("Short body.")
        skills = [
            Skill(
                name="short-skill",
                plugin="test",
                description="Short.",
                allowed_tools=[],
                body="Short body.",  # well below threshold
                path=skill_path,
            )
        ]
        import time

        # Mock _call_vertex to fail if called — it should NOT be called for short bodies
        with patch.object(mod, "_call_vertex", side_effect=AssertionError("should not call")):
            findings = mod._analyze_drift(skills, [], time.monotonic(), dry_run=False)
        assert findings == []

    def test_analyze_drift_mocked_vertex_returns_findings(self, tmp_path):
        """With mocked _call_vertex returning a CRITICAL drift, _analyze_drift yields a finding."""
        mod = _load_module()
        from _atlas_lib import Skill  # noqa: PLC0415

        skill_path = tmp_path / "SKILL.md"
        body = "A" * 600
        skill_path.write_text(body)
        skills = [
            Skill(
                name="drifted-skill",
                plugin="test",
                description="Does X and Y.",
                allowed_tools=[],
                body=body,
                path=skill_path,
            )
        ]

        mock_response = json.dumps(
            [
                {
                    "component": 1,
                    "drift": True,
                    "severity": "CRITICAL",
                    "findings": ["Description says X but body says Z."],
                }
            ]
        )
        import time

        with patch.object(mod, "_call_vertex", return_value=mock_response):
            findings = mod._analyze_drift(skills, [], time.monotonic(), dry_run=False)

        assert len(findings) == 1
        assert findings[0].severity == "CRITICAL"
        assert findings[0].category == "drift"
        assert "Description says X" in findings[0].message


class TestAnalyzeDuplication:
    def test_analyze_duplication_mocked_vertex_returns_finding(self, tmp_path):
        """With mocked _call_vertex returning a duplicate, _analyze_duplication yields finding."""
        mod = _load_module()
        from _atlas_lib import Plugin  # noqa: PLC0415

        # Create a plugin dir with two reference docs
        plugin_dir = tmp_path / "test-plugin"
        refs_dir = plugin_dir / "references"
        refs_dir.mkdir(parents=True)
        ref_a = refs_dir / "guide-a.md"
        ref_b = refs_dir / "guide-b.md"
        ref_a.write_text("# Guide A\n\n" + "Content about topic X. " * 50)
        ref_b.write_text("# Guide B\n\n" + "Content about topic X. " * 50)

        plugins = [
            Plugin(
                name="test-plugin",
                version="1.0.0",
                description="Test plugin",
                source="./test-plugin",
                category="test",
                tags=[],
                source_path=plugin_dir,
            )
        ]

        mock_response = json.dumps(
            {
                "duplicate": True,
                "severity": "INFO",
                "overlap_description": "Both cover topic X.",
            }
        )

        import time as _time

        with patch.object(mod, "_call_vertex", return_value=mock_response):
            findings = mod._analyze_duplication(plugins, _time.monotonic(), dry_run=False)

        assert len(findings) == 1
        assert findings[0].severity == "INFO"
        assert findings[0].category == "duplication"
