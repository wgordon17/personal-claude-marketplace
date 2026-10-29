"""Untrusted text sanitization, Markdown fencing, and defusedxml parsing."""

from __future__ import annotations

import re
import unicodedata
from xml.etree.ElementTree import Element, ParseError

from defusedxml import ElementTree as DefusedET
from defusedxml.common import DefusedXmlException

from .errors import FetchError

_HTML_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")
_HTML_TAG_RE = re.compile(r"<[^>]*>")
_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_BIDI_AND_ZW_CHARS = "".join(
    chr(c)
    for c in (
        0x00AD,
        0x061C,
        0x180E,
        *range(0x200B, 0x2010),
        *range(0x202A, 0x202F),
        *range(0x2060, 0x2070),
        0xFEFF,
        0xFFF9,
        0xFFFA,
        0xFFFB,
    )
)
_BIDI_AND_ZW_RE = re.compile("[" + re.escape(_BIDI_AND_ZW_CHARS) + "]")
_CONTROL_CHARS = "".join(
    chr(c) for c in (*range(0x00, 0x09), 0x0B, 0x0C, *range(0x0E, 0x20), *range(0x7F, 0xA0))
)
_CONTROL_CHARS_RE = re.compile("[" + re.escape(_CONTROL_CHARS) + "]")
_HORIZ_WS_RE = re.compile(r"[ \t\f\v]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_BACKTICK_RUN_RE = re.compile(r"`+")


def clean(text: str | None, limit: int = 600) -> str:
    """Normalize and strip HTML/XML tags, comments, ANSI escapes, bidi/zero-width, and controls."""
    if not text:
        return ""
    out = unicodedata.normalize("NFKC", text)
    out = out.replace("\r\n", "\n").replace("\r", "\n")
    out = _ANSI_ESCAPE_RE.sub("", out)
    out = _HTML_COMMENT_RE.sub(" ", out)
    out = _HTML_TAG_RE.sub(" ", out)
    # Remove any unclosed or stray angle brackets so raw HTML/tags cannot survive
    out = out.replace("<", "").replace(">", "")
    out = _BIDI_AND_ZW_RE.sub("", out)
    out = _CONTROL_CHARS_RE.sub("", out)
    out = _HORIZ_WS_RE.sub(" ", out)
    out = "\n".join(line.strip() for line in out.split("\n"))
    out = _MULTI_NEWLINE_RE.sub("\n\n", out).strip()

    if limit and limit > 0 and len(out) > limit:
        if limit == 1:
            return "…"
        out = out[: limit - 1].rstrip() + "…"
    return out


def fence(text: str | None) -> str:
    """Wrap untrusted text in a Markdown code fence longer than any internal backtick run."""
    raw = text or ""
    normalized = unicodedata.normalize("NFKC", raw)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _ANSI_ESCAPE_RE.sub("", normalized)
    normalized = _HTML_COMMENT_RE.sub(" ", normalized)
    normalized = _HTML_TAG_RE.sub(" ", normalized)
    normalized = normalized.replace("<", "").replace(">", "")
    normalized = _BIDI_AND_ZW_RE.sub("", normalized)
    normalized = _CONTROL_CHARS_RE.sub("", normalized)

    max_ticks = max((len(m.group(0)) for m in _BACKTICK_RUN_RE.finditer(normalized)), default=0)
    fence_str = "`" * max(3, max_ticks + 1)
    return f"{fence_str}text\n{normalized}\n{fence_str}"


def parse_xml(data: bytes | str) -> Element:
    """Parse XML safely via defusedxml, rejecting DTDs, entity expansion, and external refs."""
    if isinstance(data, str):
        raw_bytes = data.encode("utf-8", errors="strict")
    elif isinstance(data, bytes | bytearray | memoryview):
        raw_bytes = bytes(data)
    else:
        raise FetchError("XML input must be bytes or str")

    if not raw_bytes.strip():
        raise FetchError("XML input is empty")

    try:
        elem = DefusedET.fromstring(
            raw_bytes,
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    except (DefusedXmlException, ParseError, UnicodeDecodeError, ValueError) as exc:
        raise FetchError(f"XML parse rejected or failed: {exc}") from exc

    if elem is None:
        raise FetchError("XML parse returned no root element")
    return elem
