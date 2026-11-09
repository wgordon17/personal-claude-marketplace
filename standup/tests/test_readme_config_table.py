"""Guard test ensuring README Configuration table matches config.example.yaml leaf keys."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

STANDUP_ROOT = Path(__file__).resolve().parents[1]


def _walk_yaml_leaf_keys(node: Any, prefix: str = "") -> set[str]:
    leaves: set[str] = set()
    if isinstance(node, dict):
        for k, v in node.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            leaves.update(_walk_yaml_leaf_keys(v, path))
    elif isinstance(node, list) and node and all(isinstance(el, dict) for el in node):
        for el in node:
            leaves.update(_walk_yaml_leaf_keys(el, f"{prefix}[]"))
    else:
        leaves.add(prefix)
    return leaves


def _extract_configuration_table_first_column(readme_text: str) -> list[str]:
    lines = readme_text.splitlines()
    in_section = False
    section_lines: list[str] = []
    for line in lines:
        if line.strip() == "## Configuration":
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section:
            section_lines.append(line)

    table_rows: list[str] = []
    for line in section_lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            table_rows.append(stripped)

    assert len(table_rows) >= 3, "Expected a markdown table under ## Configuration"
    keys: list[str] = []
    for row in table_rows[2:]:
        cells = [c.strip() for c in row.strip("|").split("|")]
        if not cells:
            continue
        first_col = cells[0].strip("`").strip()
        if first_col:
            keys.append(first_col)
    return keys


def test_readme_configuration_table_matches_config_example_leaf_keys() -> None:
    """Every leaf key in config.example.yaml appears in the ## Configuration table in README.md."""
    raw = yaml.safe_load((STANDUP_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    expected = _walk_yaml_leaf_keys(raw)
    readme_text = (STANDUP_ROOT / "README.md").read_text(encoding="utf-8")
    actual_list = _extract_configuration_table_first_column(readme_text)
    assert len(actual_list) == len(set(actual_list)), "Duplicate keys in README Configuration table"
    assert set(actual_list) == expected
