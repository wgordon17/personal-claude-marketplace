"""Tests for sanitize.py and redact.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from tech_radar import redact, sanitize
from tech_radar.errors import FetchError

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import secret_samples  # noqa: E402

INJECTION_DIR = TESTS_DIR / "fixtures" / "injection"


@pytest.mark.parametrize(
    "fixture_name",
    ["readme.md", "reddit.json", "skills_entry.html", "rss.xml"],
)
def test_clean_on_every_injection_fixture(fixture_name: str) -> None:
    """clean() strips '<', U+202E/U+200B, ANSI escapes, and enforces length <= limit."""
    path = INJECTION_DIR / fixture_name
    raw = path.read_text(encoding="utf-8")
    if fixture_name == "reddit.json":
        parsed = json.loads(raw)
        child = parsed["data"]["children"][0]["data"]
        raw = f"{child['title']}\n{child['selftext']}"

    cleaned = sanitize.clean(raw, limit=600)
    assert "<" not in cleaned
    assert ">" not in cleaned
    assert chr(0x202E) not in cleaned
    assert chr(0x202C) not in cleaned
    assert chr(0x200B) not in cleaned
    assert "\x1b" not in cleaned
    assert len(cleaned) <= 600

    # Also verify smaller truncation limit
    short = sanitize.clean(raw, limit=40)
    assert len(short) <= 40
    assert short.endswith("…")


def test_fence_survives_inner_backtick_runs_and_strips_html() -> None:
    """fence() uses a fence longer than any inner backtick run and strips tags/controls."""
    payload = (
        "Here is a block:\n```python\nprint('hi')\n```\nand ````four```` plus <script>x</script>"
        + chr(0x202E)
    )
    fenced = sanitize.fence(payload)
    lines = fenced.splitlines()
    assert lines[0] == "`````text"
    assert lines[-1] == "`````"
    assert "<script>" not in fenced
    assert chr(0x202E) not in fenced
    assert "```python" in fenced
    assert "````four````" in fenced


def test_scrub_redacts_every_generated_sample_and_preserves_plain_text() -> None:
    """scrub() replaces every secret sample with [REDACTED:<kind>] and keeps plain text."""
    redact.reset_redaction_count()
    samples = secret_samples.build_samples_with_kinds()
    canaries = secret_samples.raw_canary_substrings()

    for name, (secret_val, expected_kind) in samples.items():
        text = f"prefix ({name}): {secret_val} -- suffix"
        scrubbed = redact.scrub(text)
        assert f"[REDACTED:{expected_kind}]" in scrubbed, (
            f"Expected [REDACTED:{expected_kind}] for sample {name}, got: {scrubbed!r}"
        )
        assert canaries[name] not in scrubbed, f"Canary leaked for sample {name}"

    assert redact.get_redaction_count() >= len(samples)
    counts = redact.get_redaction_counts()
    assert counts["github_token"] >= 2
    assert counts["api_key"] >= 3

    plain = (
        "The quick brown fox jumps over 42 lazy dogs in https://github.com/astral-sh/uv "
        "using package pkg:pypi/litellm version 1.106.2."
    )
    before = redact.get_redaction_count()
    assert redact.scrub(plain) == plain
    assert redact.get_redaction_count() == before


def test_scrub_negative_control_detects_canary_when_monkeypatched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control: canary is detected in output when redact.scrub is identity."""
    samples = secret_samples.build_samples()
    canaries = secret_samples.raw_canary_substrings()
    combined = "\n".join(f"{k}={v}" for k, v in samples.items())

    # Normal scrub removes all canaries
    normal_out = redact.scrub(combined)
    assert all(c not in normal_out for c in canaries.values())

    # Monkeypatch scrub to identity and verify the assertion WOULD fail (canary found)
    monkeypatch.setattr(redact, "scrub", lambda s: s)
    identity_out = redact.scrub(combined)
    leaked = [name for name, canary in canaries.items() if canary in identity_out]
    assert len(leaked) == len(canaries)


def test_parse_xml_valid_and_billion_laughs_rejected() -> None:
    """parse_xml parses valid RSS/Atom and raises FetchError on DTD / billion-laughs / XXE."""
    rss_bytes = (INJECTION_DIR / "rss.xml").read_bytes()
    root = sanitize.parse_xml(rss_bytes)
    assert root.tag == "rss"

    billion_laughs = (
        b'<?xml version="1.0"?>\n'
        b"<!DOCTYPE lolz [\n"
        b' <!ENTITY lol "lol">\n'
        b' <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">\n'
        b' <!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">\n'
        b"]>\n"
        b"<lolz>&lol2;</lolz>"
    )
    with pytest.raises(FetchError):
        sanitize.parse_xml(billion_laughs)

    xxe_doc = (
        b'<?xml version="1.0"?>\n'
        b'<!DOCTYPE foo [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>\n'
        b"<foo>&xxe;</foo>"
    )
    with pytest.raises(FetchError):
        sanitize.parse_xml(xxe_doc)

    with pytest.raises(FetchError):
        sanitize.parse_xml(b"<unclosed")
