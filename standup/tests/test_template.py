"""Tests for template.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from standup_tool.config import ConfigError
from standup_tool.template import Header, load_template


def test_load_template_parses_headers_and_preserves_raw(tmp_path: Path) -> None:
    tpl = tmp_path / "template.md"
    raw_content = (
        ":dart: Key Effort\n"
        "<!-- criteria: Primary active engineering effort -->\n"
        "• ACME-101: Shipped token validation\n\n"
        ":rotating_light: Blockers & Timelines\n"
        "• None\n\n"
        ":rocket: Progress & Wins\n"
        "Plain item line\n"
    )
    tpl.write_text(raw_content, encoding="utf-8")

    loaded = load_template(tpl)
    assert loaded.raw == raw_content
    assert loaded.headers == [
        Header(emoji=":dart:", text="Key Effort"),
        Header(emoji=":rotating_light:", text="Blockers & Timelines"),
        Header(emoji=":rocket:", text="Progress & Wins"),
    ]


def test_load_template_rejects_single_header(tmp_path: Path) -> None:
    tpl = tmp_path / "single.md"
    tpl.write_text(":dart: Only One\n• item\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="at least 2"):
        load_template(tpl)


def test_load_template_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_template(tmp_path / "missing.md")


def test_load_template_rejects_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real.md"
    real.write_text(":dart: One\n:rocket: Two\n", encoding="utf-8")
    link = tmp_path / "link.md"
    link.symlink_to(real)
    with pytest.raises(ConfigError, match="symlink"):
        load_template(link)


def test_load_template_skips_commented_out_header(tmp_path: Path) -> None:
    tpl = tmp_path / "commented.md"
    tpl.write_text(
        ":dart: Real Header\n<!-- :rocket: Commented Header -->\n• item\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="found 1"):
        load_template(tpl)


def test_load_template_rejects_zero_headers(tmp_path: Path) -> None:
    tpl = tmp_path / "empty.md"
    tpl.write_text("Plain text with no emoji headers\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="found 0"):
        load_template(tpl)
