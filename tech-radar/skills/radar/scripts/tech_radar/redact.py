"""Secret detection and redaction for database and rendered outputs."""

from __future__ import annotations

import math
import re
from collections import Counter

_REDACTION_COUNTS: Counter[str] = Counter()

_PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN\s+[A-Z0-9 _-]*PRIVATE\s+KEY-----[\s\S]*?-----END\s+[A-Z0-9 _-]*PRIVATE\s+KEY-----"
)
_PRIVATE_KEY_HEADER_RE = re.compile(r"-----BEGIN\s+[A-Z0-9 _-]*PRIVATE\s+KEY-----")
_URL_USERINFO_RE = re.compile(r"(://)([^/\s:@]+:[^/\s@]+)(@)")
_AWS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_GITHUB_TOKEN_RE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{36,255}|github_pat_[A-Za-z0-9_]{22,255})\b"
)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_API_KEY_RE = re.compile(
    r"\b(?:sk-ant-[A-Za-z0-9_-]{16,}|sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{35})\b"
)
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}")
_SLACK_TOKEN_RE = re.compile(r"\bxox[abprs]-[0-9A-Za-z-]{10,}\b")
_CANDIDATE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9+/=._:\[\]-])[A-Za-z0-9+/=_-]{32,}(?![A-Za-z0-9+/=._:\]-])"
)

_ORDERED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", _PRIVATE_KEY_BLOCK_RE),
    ("private_key", _PRIVATE_KEY_HEADER_RE),
    ("jwt", _JWT_RE),
    ("github_token", _GITHUB_TOKEN_RE),
    ("aws_key", _AWS_KEY_RE),
    ("api_key", _API_KEY_RE),
    ("slack_token", _SLACK_TOKEN_RE),
    ("bearer", _BEARER_RE),
)


def shannon_entropy(token: str) -> float:
    """Compute Shannon entropy in bits per character."""
    n = len(token)
    if n == 0:
        return 0.0
    counts = Counter(token)
    return -sum((count / n) * math.log2(count / n) for count in counts.values())


def _is_high_entropy_secret(token: str) -> bool:
    if len(token) < 32:
        return False
    has_upper = any("A" <= ch <= "Z" for ch in token)
    has_lower = any("a" <= ch <= "z" for ch in token)
    has_digit = any("0" <= ch <= "9" for ch in token)
    if not (has_upper and has_lower and has_digit):
        return False
    return shannon_entropy(token) >= 4.0


def scrub(text: str) -> str:
    """Replace secret patterns and high-entropy tokens with [REDACTED:<kind>] markers."""
    if not text:
        return text

    out = text

    def _replace_userinfo(match: re.Match[str]) -> str:
        _REDACTION_COUNTS["url_userinfo"] += 1
        return f"{match.group(1)}[REDACTED:url_userinfo]{match.group(3)}"

    out = _URL_USERINFO_RE.sub(_replace_userinfo, out)

    for kind, pattern in _ORDERED_PATTERNS:

        def _repl(_match: re.Match[str], k: str = kind) -> str:
            _REDACTION_COUNTS[k] += 1
            return f"[REDACTED:{k}]"

        out = pattern.sub(_repl, out)

    def _replace_high_entropy(match: re.Match[str]) -> str:
        tok = match.group(0)
        if _is_high_entropy_secret(tok):
            _REDACTION_COUNTS["high_entropy"] += 1
            return "[REDACTED:high_entropy]"
        return tok

    return _CANDIDATE_TOKEN_RE.sub(_replace_high_entropy, out)


def get_redaction_count() -> int:
    """Return total redaction hits across all secret kinds."""
    return sum(_REDACTION_COUNTS.values())


def get_redaction_counts() -> dict[str, int]:
    """Return redaction hit counts keyed by secret kind."""
    return dict(_REDACTION_COUNTS)


def reset_redaction_count() -> None:
    """Reset the in-memory redaction counters."""
    _REDACTION_COUNTS.clear()
