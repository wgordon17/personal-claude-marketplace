"""Standup template loader and section header parser."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from standup_tool.config import ConfigError

_COMMENT_LINE_RE = re.compile(r"^\s*<!--.*-->\s*$")
_HEADER_LINE_RE = re.compile(r"^(:[a-z0-9_+-]+:) (.+)$")


@dataclass(frozen=True)
class Header:
    """Parsed standup section header."""

    emoji: str
    text: str


@dataclass(frozen=True)
class Template:
    """Loaded standup template with raw text and parsed section headers."""

    raw: str
    headers: list[Header]


def load_template(path: Path) -> Template:
    """Load *path* and parse its `:emoji: Header` section headers."""
    if path.is_symlink():
        raise ConfigError(f"Refusing symlink at {path}")
    if not path.is_file():
        raise ConfigError(f"Template file not found: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Failed to read template {path}: {exc}") from exc

    headers: list[Header] = []
    for line in raw.splitlines():
        if _COMMENT_LINE_RE.match(line):
            continue
        m = _HEADER_LINE_RE.match(line.strip())
        if m:
            headers.append(Header(emoji=m.group(1), text=m.group(2).strip()))

    if len(headers) < 2:
        raise ConfigError(
            f"Template {path} must define at least 2 ':emoji: Header' lines (found {len(headers)})"
        )

    return Template(raw=raw, headers=headers)
