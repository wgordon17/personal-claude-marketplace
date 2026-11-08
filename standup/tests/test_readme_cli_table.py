"""Guard test ensuring the README ## CLI table matches top-level subcommands on build_parser()."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from standup_tool.cli import build_parser, subcommand_names

STANDUP_ROOT = Path(__file__).resolve().parents[1]
_UNESCAPED_PIPE_RE = re.compile(r"(?<!\\)\|")


def _extract_cli_table_rows(readme_text: str) -> list[list[str]]:
    lines = readme_text.splitlines()
    in_section = False
    section_lines: list[str] = []
    for line in lines:
        if line.strip() == "## CLI":
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section:
            section_lines.append(line)

    raw_rows: list[str] = []
    for line in section_lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            raw_rows.append(stripped)

    assert len(raw_rows) >= 3, "Expected a markdown table under ## CLI"
    parsed_rows: list[list[str]] = []
    for row in raw_rows:
        inner = row[1:-1]
        cells = [c.replace(r"\|", "|").strip() for c in _UNESCAPED_PIPE_RE.split(inner)]
        parsed_rows.append(cells)
    return parsed_rows


def _extract_cli_table_first_column(readme_text: str) -> list[str]:
    parsed_rows = _extract_cli_table_rows(readme_text)
    commands: list[str] = []
    for cells in parsed_rows[2:]:
        if not cells:
            continue
        first_col = cells[0].strip("`").strip()
        if first_col:
            commands.append(first_col)
    return commands


def test_readme_cli_table_matches_parser_subcommands() -> None:
    """First column of the ## CLI table in README.md equals subcommand_names(build_parser())."""
    readme_text = (STANDUP_ROOT / "README.md").read_text(encoding="utf-8")
    actual_list = _extract_cli_table_first_column(readme_text)
    assert len(actual_list) == len(set(actual_list)), "Duplicate commands in README CLI table"
    assert set(actual_list) == subcommand_names(build_parser())


def test_readme_cli_table_headers_and_config_subcommands() -> None:
    """The ## CLI table has command|purpose|reads|writes columns and config subcommands."""
    readme_text = (STANDUP_ROOT / "README.md").read_text(encoding="utf-8")
    rows = _extract_cli_table_rows(readme_text)
    header = [c.lower() for c in rows[0]]
    assert header == ["command", "purpose", "reads", "writes"]

    rows_by_cmd: dict[str, list[str]] = {}
    for row in rows[2:]:
        assert len(row) == 4, f"Expected 4 columns in CLI table row, got {len(row)}: {row!r}"
        assert all(cell for cell in row), f"Empty cell in CLI table row: {row!r}"
        cmd = row[0].strip("`").strip()
        rows_by_cmd[cmd] = row

    assert "config" in rows_by_cmd
    config_purpose = rows_by_cmd["config"][1]
    for subcmd in ("show", "validate", "path"):
        assert subcmd in config_purpose, f"config row purpose missing subcommand {subcmd!r}"


def test_extract_cli_table_stops_at_next_section_and_handles_escaped_pipes() -> None:
    """Table extraction ignores tables in later sections and unescapes \\| inside cells."""
    sample = (
        "# Title\n\n"
        "## CLI\n\n"
        "| command | purpose | reads | writes |\n"
        "| --- | --- | --- | --- |\n"
        "| `collect` | Collect data | config | output |\n"
        "| `verify` | Check readiness | config | stdout (`check \\| status \\| hint`) |\n\n"
        "## Another Section\n\n"
        "| command | purpose | reads | writes |\n"
        "| --- | --- | --- | --- |\n"
        "| `ignored-cmd` | Should not be read | none | none |\n"
    )
    assert _extract_cli_table_first_column(sample) == ["collect", "verify"]
    rows = _extract_cli_table_rows(sample)
    assert rows[3] == [
        "`verify`",
        "Check readiness",
        "config",
        "stdout (`check | status | hint`)",
    ]


@pytest.mark.parametrize(
    "bad_markdown",
    [
        "# No CLI Section\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n",
        "## CLI\n\nNo table here.\n",
        "## CLI\n\n| command | purpose |\n| --- | --- |\n",
    ],
)
def test_extract_cli_table_rejects_missing_or_incomplete_table(bad_markdown: str) -> None:
    """Missing ## CLI section or table without data rows raises AssertionError."""
    with pytest.raises(AssertionError, match="Expected a markdown table under ## CLI"):
        _extract_cli_table_first_column(bad_markdown)
