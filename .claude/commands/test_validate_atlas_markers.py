"""Tests for validate-atlas-markers.py."""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent / "validate-atlas-markers.py"
ATLAS_MD = Path(__file__).parent.parent.parent / "ATLAS.md"

ALL_SECTIONS = [
    "skill-artifacts",
    "swarm-phases",
    "quality-gate-layers",
    "code-quality-agents",
    "code-quality-skills",
    "code-quality-commands",
    "dev-guard-hooks",
    "dev-guard-commands",
    "git-tools",
    "github-mcp",
    "lsp-plugins",
    "reference-docs",
    "mcp-integrations",
    "marketplace-registry",
]


def run(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["uv", "run", str(SCRIPT), str(path)],
        capture_output=True,
        text=True,
    )


def make_atlas(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "ATLAS.md"
    p.write_text(content, encoding="utf-8")
    return p


def valid_markers(sections: list[str]) -> str:
    """Build a minimal valid ATLAS.md with the given sections."""
    lines = []
    for s in sections:
        lines.append(f"<!-- BEGIN:AUTO {s} -->")
        lines.append(f"content for {s}")
        lines.append(f"<!-- END:AUTO {s} -->")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Check 1: File existence
# ---------------------------------------------------------------------------


def test_missing_file_exits_1(tmp_path):
    result = run(tmp_path / "nonexistent.md")
    assert result.returncode == 1
    assert "not found" in result.stderr


def test_existing_file_proceeds(tmp_path):
    path = make_atlas(tmp_path, valid_markers(ALL_SECTIONS))
    result = run(path)
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# Check 2: Marker syntax
# ---------------------------------------------------------------------------


def test_well_formed_markers_pass(tmp_path):
    path = make_atlas(tmp_path, valid_markers(ALL_SECTIONS))
    result = run(path)
    assert result.returncode == 0


def test_malformed_marker_missing_name_exits_1(tmp_path):
    # BEGIN:AUTO with no name after it — valid regex requires a non-whitespace token
    content = "<!-- BEGIN:AUTO -->\ncontent\n<!-- END:AUTO -->\n"
    path = make_atlas(tmp_path, content)
    result = run(path)
    assert result.returncode == 1
    assert "Malformed" in result.stderr


def test_malformed_marker_reports_line_number(tmp_path):
    content = "line one\n<!-- BEGIN:AUTO -->\ncontent\n"
    path = make_atlas(tmp_path, content)
    result = run(path)
    assert "Line 2" in result.stderr


def test_backtick_quoted_marker_in_prose_is_skipped(tmp_path):
    # A backtick before <!-- makes _is_marker_line return False — should not be flagged
    # Use all valid sections so Check 4 does not fail for a different reason.
    # We embed the backtick prose inside a valid section body.
    lines = []
    for s in ALL_SECTIONS:
        lines.append(f"<!-- BEGIN:AUTO {s} -->")
        if s == ALL_SECTIONS[0]:
            lines.append("`<!-- BEGIN:AUTO -->`")  # backtick before comment
        lines.append(f"<!-- END:AUTO {s} -->")
    path = make_atlas(tmp_path, "\n".join(lines) + "\n")
    result = run(path)
    assert result.returncode == 0


# ---------------------------------------------------------------------------
# Check 3: Pairing and ordering
# ---------------------------------------------------------------------------


def test_properly_paired_markers_pass(tmp_path):
    path = make_atlas(tmp_path, valid_markers(ALL_SECTIONS))
    result = run(path)
    assert result.returncode == 0


def test_nested_markers_exits_1(tmp_path):
    # BEGIN:AUTO foo then BEGIN:AUTO bar without END:AUTO foo first
    content = (
        "<!-- BEGIN:AUTO foo -->\n"
        "<!-- BEGIN:AUTO bar -->\n"
        "<!-- END:AUTO bar -->\n"
        "<!-- END:AUTO foo -->\n"
    )
    path = make_atlas(tmp_path, content)
    result = run(path)
    assert result.returncode == 1
    assert "Nested" in result.stderr


def test_unmatched_begin_exits_1(tmp_path):
    content = "<!-- BEGIN:AUTO foo -->\ncontent\n"
    path = make_atlas(tmp_path, content)
    result = run(path)
    assert result.returncode == 1
    assert "Unmatched BEGIN" in result.stderr


def test_unmatched_end_exits_1(tmp_path):
    content = "<!-- END:AUTO foo -->\ncontent\n"
    path = make_atlas(tmp_path, content)
    result = run(path)
    assert result.returncode == 1
    assert "Unmatched END" in result.stderr


def test_mismatched_names_exits_1(tmp_path):
    content = "<!-- BEGIN:AUTO foo -->\ncontent\n<!-- END:AUTO bar -->\n"
    path = make_atlas(tmp_path, content)
    result = run(path)
    assert result.returncode == 1
    assert "Mismatched" in result.stderr


# ---------------------------------------------------------------------------
# Check 4: Expected section coverage
# ---------------------------------------------------------------------------


def test_all_14_sections_present_exits_0(tmp_path):
    path = make_atlas(tmp_path, valid_markers(ALL_SECTIONS))
    result = run(path)
    assert result.returncode == 0
    assert "14/14" in result.stdout


def test_missing_sections_exits_1(tmp_path):
    # Omit the first two sections
    path = make_atlas(tmp_path, valid_markers(ALL_SECTIONS[2:]))
    result = run(path)
    assert result.returncode == 1
    assert "Missing" in result.stderr


def test_missing_sections_lists_names(tmp_path):
    omitted = ALL_SECTIONS[:2]  # ["skill-artifacts", "swarm-phases"]
    path = make_atlas(tmp_path, valid_markers(ALL_SECTIONS[2:]))
    result = run(path)
    for name in omitted:
        assert name in result.stderr


def test_extra_sections_exits_0_with_warning(tmp_path):
    extra = ALL_SECTIONS + ["unknown-extra"]
    path = make_atlas(tmp_path, valid_markers(extra))
    result = run(path)
    assert result.returncode == 0
    assert "WARNING" in result.stdout or "Extra" in result.stdout


# ---------------------------------------------------------------------------
# Sync check: test ALL_SECTIONS must match validator EXPECTED_SECTIONS
# ---------------------------------------------------------------------------


def test_sections_match_validator():
    """Ensure test ALL_SECTIONS stays in sync with validator EXPECTED_SECTIONS."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("validate_atlas_markers", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert set(ALL_SECTIONS) == module.EXPECTED_SECTIONS, (
        f"Test ALL_SECTIONS is out of sync with validator EXPECTED_SECTIONS.\n"
        f"In test but not validator: {set(ALL_SECTIONS) - module.EXPECTED_SECTIONS}\n"
        f"In validator but not test: {module.EXPECTED_SECTIONS - set(ALL_SECTIONS)}"
    )


# ---------------------------------------------------------------------------
# Integration: run against the real ATLAS.md
# ---------------------------------------------------------------------------


def test_real_atlas_md_passes():
    assert ATLAS_MD.exists(), f"ATLAS.md not found at {ATLAS_MD}"
    result = run(ATLAS_MD)
    assert result.returncode == 0
    assert "14/14" in result.stdout
