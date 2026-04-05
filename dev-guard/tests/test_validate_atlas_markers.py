"""Tests for validate-atlas-markers.py."""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent.parent / ".claude" / "commands" / "validate-atlas-markers.py"
ATLAS_MD = Path(__file__).parent.parent.parent / "ATLAS.md"

ALL_SECTIONS = [
    "skill-artifacts",
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


# ---------------------------------------------------------------------------
# Check 2: Marker syntax
# ---------------------------------------------------------------------------


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


def test_duplicate_section_exits_1(tmp_path):
    content = (
        "<!-- BEGIN:AUTO foo -->\ncontent\n<!-- END:AUTO foo -->\n"
        "<!-- BEGIN:AUTO foo -->\nmore\n<!-- END:AUTO foo -->\n"
    )
    path = make_atlas(tmp_path, content)
    result = run(path)
    assert result.returncode == 1
    assert "Duplicate" in result.stderr


def test_markers_inside_fenced_code_block_are_ignored(tmp_path):
    # Markers inside ``` fenced blocks should not be treated as real markers
    lines = []
    for s in ALL_SECTIONS:
        lines.append(f"<!-- BEGIN:AUTO {s} -->")
        if s == ALL_SECTIONS[0]:
            lines.append("```markdown")
            lines.append("<!-- BEGIN:AUTO fake -->")
            lines.append("<!-- END:AUTO fake -->")
            lines.append("```")
        lines.append(f"<!-- END:AUTO {s} -->")
    path = make_atlas(tmp_path, "\n".join(lines) + "\n")
    result = run(path)
    assert result.returncode == 0
    assert "12/12" in result.stdout


def test_markers_inside_tilde_fenced_block_are_ignored(tmp_path):
    # Markers inside ~~~ fenced blocks should not be treated as real markers
    lines = []
    for s in ALL_SECTIONS:
        lines.append(f"<!-- BEGIN:AUTO {s} -->")
        if s == ALL_SECTIONS[0]:
            lines.append("~~~markdown")
            lines.append("<!-- BEGIN:AUTO fake -->")
            lines.append("<!-- END:AUTO fake -->")
            lines.append("~~~")
        lines.append(f"<!-- END:AUTO {s} -->")
    path = make_atlas(tmp_path, "\n".join(lines) + "\n")
    result = run(path)
    assert result.returncode == 0
    assert "12/12" in result.stdout


def test_mismatched_fence_types_do_not_close(tmp_path):
    # A ~~~ fence should NOT be closed by ``` — markers inside should stay hidden
    lines = []
    for s in ALL_SECTIONS:
        lines.append(f"<!-- BEGIN:AUTO {s} -->")
        if s == ALL_SECTIONS[0]:
            lines.append("~~~markdown")
            lines.append("```")  # backtick inside tilde fence — not a closer
            lines.append("<!-- BEGIN:AUTO fake -->")
            lines.append("<!-- END:AUTO fake -->")
            lines.append("~~~")  # actual closer
        lines.append(f"<!-- END:AUTO {s} -->")
    path = make_atlas(tmp_path, "\n".join(lines) + "\n")
    result = run(path)
    assert result.returncode == 0
    assert "12/12" in result.stdout


def test_unclosed_fence_exits_1(tmp_path):
    content = valid_markers(ALL_SECTIONS) + "```\n<!-- BEGIN:AUTO extra -->\n"
    path = make_atlas(tmp_path, content)
    result = run(path)
    assert result.returncode == 1
    assert "Unclosed" in result.stderr


def test_unclosed_fence_reports_line_number(tmp_path):
    # Fence opens on line 2, never closed
    content = "preamble\n```\nsome code\n"
    path = make_atlas(tmp_path, content)
    result = run(path)
    assert result.returncode == 1
    assert "line 2" in result.stderr


def test_marker_after_closed_backtick_pair_is_real(tmp_path):
    # `code` <!-- BEGIN:AUTO foo --> — marker is AFTER the backtick pair, should be real
    lines = []
    for s in ALL_SECTIONS:
        lines.append(f"<!-- BEGIN:AUTO {s} -->")
        if s == ALL_SECTIONS[0]:
            lines.append("`code` <!-- BEGIN:AUTO fake -->")
        lines.append(f"<!-- END:AUTO {s} -->")
    path = make_atlas(tmp_path, "\n".join(lines) + "\n")
    result = run(path)
    # The "fake" marker after closed backticks is detected as a real nested marker
    assert result.returncode == 1
    assert "Nested" in result.stderr


def test_single_backtick_before_marker_treated_as_real(tmp_path):
    # Single unclosed backtick before <!-- — marker is not inside inline code
    lines = []
    for s in ALL_SECTIONS:
        lines.append(f"<!-- BEGIN:AUTO {s} -->")
        if s == ALL_SECTIONS[0]:
            lines.append("` <!-- BEGIN:AUTO fake -->")
        lines.append(f"<!-- END:AUTO {s} -->")
    path = make_atlas(tmp_path, "\n".join(lines) + "\n")
    result = run(path)
    # Unclosed backtick means comment is not in inline code — treated as real marker
    assert result.returncode == 1
    assert "Nested" in result.stderr


# ---------------------------------------------------------------------------
# Check 4: Expected section coverage
# ---------------------------------------------------------------------------


def test_all_12_sections_present_exits_0(tmp_path):
    path = make_atlas(tmp_path, valid_markers(ALL_SECTIONS))
    result = run(path)
    assert result.returncode == 0
    assert "12/12" in result.stdout


def test_missing_sections_exits_1(tmp_path):
    # Omit the first two sections
    path = make_atlas(tmp_path, valid_markers(ALL_SECTIONS[2:]))
    result = run(path)
    assert result.returncode == 1
    assert "Missing" in result.stderr


def test_missing_sections_lists_names(tmp_path):
    omitted = ALL_SECTIONS[:2]  # ["skill-artifacts", "code-quality-agents"]
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
    assert "12/12" in result.stdout
