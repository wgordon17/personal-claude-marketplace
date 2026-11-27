"""Stdlib-only secret, known-value, PII, paste, and entropy redaction for review_rules.

This module intentionally imports ONLY from the Python standard library and has zero
imports from ``review_rules`` so hooks can load it directly via
``importlib.util.spec_from_file_location``.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Literal, NamedTuple

PRE_SLICE_LIMIT = 69_632  # 64 KB + 4 KB boundary window
MAX_OUTPUT_CHARS = 65_536
PASTE_GATE_MIN_CHARS = 2_048
PASTE_KEEP_LINES = 20
PASTE_MIN_UNFENCED_LINES = 5
PASTE_MIN_MARKER_RATIO = 0.60
PEM_END_WINDOW = 16_384
SETTINGS_MAX_BYTES = 1_048_576
ENTROPY_MIN_TOKEN_LEN = 32
ENTROPY_MIN_URL_SEG_LEN = 20
ENTROPY_THRESHOLD = 4.3
MIN_KNOWN_SECRET_LEN = 16


class Redacted(NamedTuple):
    text: str
    action: Literal["keep", "truncate", "drop"]
    dropped_reason: str | None = None


PatternValidator = Callable[[re.Match[str]], bool]

_KNOWN_SECRETS_CACHE: tuple[str, ...] | None = None

_SECRET_ENV_KEY_RE = re.compile(
    r"(?i)(?:(?:^|_)(?:TOKEN|KEY|SECRET|PASSWORD|PASSWD|PWD|PAT|AUTH|COOKIE|CREDENTIALS?)(?:_|$)"
    r"|(?:APIKEY|AUTHTOKEN|PGPASSWORD|PRIVATEKEY)$)"
)
_EXCLUDED_EXACT_ENV_KEYS = frozenset(
    {"PATH", "PWD", "OLDPWD", "REVIEW_RULES_ANTHROPIC_KEY_FILE", "SSH_AUTH_SOCK"}
)
_EXCLUDED_ENV_KEY_SUFFIXES = ("_PATH", "_DIR", "_HOME", "_URL")

_PEM_BEGIN_RE = re.compile(r"-----BEGIN ([A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?)-----")
_REDACTED_SPAN_RE = re.compile(r"\[REDACTED:[a-z0-9_]+\]")
_AUTH_KW_RE = re.compile(r"(?i)\b(?:bearer|basic)\b")
_AUTH_CHUNK_SPLIT_RE = re.compile(r"[ \t;,]+")
_AUTH_KV_CHUNK_RE = re.compile(r"^[A-Za-z0-9_.-]+=(.*)$")
_KEBAB_SNAKE_RE = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)+$")
_SUBSCRIPT_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*\[[A-Za-z0-9_\"'-]+\]$")


def _is_token_like(val: str, *, quoted: bool = False, allow_parens: bool = False) -> bool:
    if _REDACTED_SPAN_RE.fullmatch(val) is not None:
        return False
    cleaned = _REDACTED_SPAN_RE.sub("", val)
    if not cleaned or "[REDACTED:" in cleaned:
        return False
    if not quoted and _SUBSCRIPT_IDENT_RE.fullmatch(cleaned) is not None:
        return False
    if not allow_parens and ("(" in cleaned or ")" in cleaned):
        return False
    if _KEBAB_SNAKE_RE.fullmatch(cleaned):
        return False
    if (
        len(cleaned) >= 12
        and any(c.isalpha() for c in cleaned)
        and any(c.isdigit() for c in cleaned)
    ):
        return True
    return bool(quoted and len(cleaned) >= 16 and any(not c.isalnum() for c in cleaned))


def _validate_auth_header(m: re.Match[str]) -> bool:
    cleaned = _REDACTED_SPAN_RE.sub(" ", m.group(2))
    cleaned = _AUTH_KW_RE.sub(" ", cleaned).strip()
    if not cleaned:
        return False
    for raw_chunk in _AUTH_CHUNK_SPLIT_RE.split(cleaned):
        chunk = raw_chunk.strip("\"'")
        if not chunk:
            continue
        kv_match = _AUTH_KV_CHUNK_RE.fullmatch(chunk)
        if kv_match is not None:
            rhs = kv_match.group(1)
            if not (rhs and set(rhs) <= {"="}):
                cand = rhs.strip("\"'")
                if cand and _is_token_like(cand, quoted=False, allow_parens=True):
                    return True
                continue
        if _is_token_like(chunk, quoted=False, allow_parens=True):
            return True
    return False


def _validate_bearer(m: re.Match[str]) -> bool:
    return _is_token_like(m.group(1), quoted=False, allow_parens=False)


def _validate_kv_secret(m: re.Match[str]) -> bool:
    if m.group(2) is not None:
        return _is_token_like(m.group(3), quoted=True, allow_parens=False)
    return _is_token_like(m.group(4), quoted=False, allow_parens=False)


def _validate_url_userinfo(m: re.Match[str]) -> bool:
    return bool(_REDACTED_SPAN_RE.sub("", m.group(2)) or _REDACTED_SPAN_RE.sub("", m.group(3)))


def _validate_query_secret(m: re.Match[str]) -> bool:
    return bool(_REDACTED_SPAN_RE.sub("", m.group(2)))


def _validate_kubeconfig(m: re.Match[str]) -> bool:
    return bool(_REDACTED_SPAN_RE.sub("", m.group(2)))


PATTERNS: tuple[tuple[str, re.Pattern[str], PatternValidator | None], ...] = (
    ("anthropic", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,200}"), None),
    (
        "github",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b|\bgithub_pat_[A-Za-z0-9_]{22,255}"),
        None,
    ),
    ("gitlab", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,64}\b"), None),
    (
        "slack",
        re.compile(
            r"\bxox[abcdeprs]-[A-Za-z0-9%_./+=-]{10,2000}"
            r"|\bxapp-[A-Za-z0-9-]{10,200}"
            r"|\bxoxe\.xox[bp]-[A-Za-z0-9-]{10,200}"
            r"|https://hooks\.slack\.com/services/T[A-Za-z0-9_]+/B[A-Za-z0-9_]+/[A-Za-z0-9_]+"
        ),
        None,
    ),
    ("aws_key_id", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), None),
    ("google_api", re.compile(r"\bAIza[0-9A-Za-z_-]{35}"), None),
    ("google_oauth", re.compile(r"\bya29\.[0-9A-Za-z_-]{20,400}"), None),
    ("atlassian", re.compile(r"\bATATT[A-Za-z0-9_=-]{20,400}"), None),
    ("stripe", re.compile(r"\b[rs]k_live_[0-9A-Za-z]{16,200}\b"), None),
    ("npm", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"), None),
    ("pypi", re.compile(r"\bpypi-[A-Za-z0-9_-]{50,300}"), None),
    ("huggingface", re.compile(r"\bhf_[A-Za-z0-9]{30,60}\b"), None),
    ("vault", re.compile(r"\bhvs\.[A-Za-z0-9_-]{20,120}\b"), None),
    ("age_secret", re.compile(r"\bAGE-SECRET-KEY-1[A-Z0-9]{58}\b"), None),
    ("openshift", re.compile(r"\bsha256~[A-Za-z0-9_-]{40,60}"), None),
    ("generic_sk", re.compile(r"\bsk-[A-Za-z0-9_-]{20,200}"), None),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,2000}\.eyJ[A-Za-z0-9_-]{8,4000}\.[A-Za-z0-9_-]{8,2000}"),
        None,
    ),
    (
        "auth_header",
        re.compile(
            r"(?i)\b((?:authorization|proxy-authorization|cookie|set-cookie|x-api-key)"
            r"[ \t]{0,5}:[ \t]{0,5})([^\r\n]+)"
        ),
        _validate_auth_header,
    ),
    (
        "bearer",
        re.compile(r"(?i)\b(?:bearer|basic)[ \t]{1,5}([A-Za-z0-9._~+/=-]{16,})"),
        _validate_bearer,
    ),
    (
        "url_userinfo",
        re.compile(r"\b([a-z][a-z0-9+.-]{1,20}://)([^\s/:@]{1,200}):([^\s/@]{1,200})@"),
        _validate_url_userinfo,
    ),
    (
        "query_secret",
        re.compile(
            r"(?i)([?&][\w.-]{0,40}?(?:token|key|sig|signature|secret|password|credential|code)=)"
            r"([^&\s#]{6,})"
        ),
        _validate_query_secret,
    ),
    (
        "docker_auth",
        re.compile(r'("auth"[ \t]{0,5}:[ \t]{0,5}")[A-Za-z0-9+/=]{16,}"'),
        None,
    ),
    (
        "kubeconfig",
        re.compile(
            r"(?m)^([ \t]{0,40}(?:client-key-data|client-certificate-data|token|password):"
            r"[ \t]{1,5})(\S{16,})"
        ),
        _validate_kubeconfig,
    ),
    (
        "kv_secret",
        re.compile(
            r"(?i)\b([A-Za-z0-9_]{0,60}"
            r"(?:password|passwd|secret|token|api_?key|private_?key|access_?key)"
            r"""[A-Za-z0-9_]{0,60}["']?[ \t]{0,5}[:=][ \t]{0,5})"""
            r"""(?:(["'])([^"'\r\n]{12,500})\2|([^\s"',;]{12,500}))"""
        ),
        _validate_kv_secret,
    ),
)

_EMAIL_RE = re.compile(
    r"(?<![\w.+-])(?!git@)[\w.+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){0,8}"
    r"\.[A-Za-z]{2,24}(?![\w-])(?!\.[\w-])(?!:\w)"
)
_INTERNAL_HOST_RE = re.compile(
    r"(?i)\b(?:[a-z0-9-]{1,63}\.){1,6}(?:internal|corp|local)\b(?![\w.-])"
)
_RFC1918_RE = re.compile(
    r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"
)

_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_PASTE_TRUNC_LINE_RE = re.compile(r"^\[PASTE: \d+ more lines truncated\]\r?$")
_MULTI_SPACE_RE = re.compile(r" {2,}")
_PASTE_MARKER_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s{0,4}\[?(?:\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}|\d{2}:\d{2}:\d{2})"),
    re.compile(r"^\s{0,4}\[?(?:TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL)\]?\b"),
    re.compile(r'^(?:Traceback \(most recent|\s{2}File "|\s+at [\w$.]+\(|\w+(?:Error|Exception):)'),
    re.compile(r"^(?:@@ |diff --git|[+-]{3} |\+(?!\+)|-(?!-| [^ \t]))"),
    re.compile(r"^[A-Z_][A-Z0-9_]{2,}="),
    re.compile(r"^(?:PASSED|FAILED|ERROR|={3,} |_{3,} )"),
)
_TRACEBACK_HEAD_RE = re.compile(r"^Traceback \(most recent")

_TOKEN_SPLIT_RE = re.compile(r"""[\s"'`,;()\[\]{}<>]+""")
_URL_QF_START_RE = re.compile(r"[?#]")
_URL_QUERY_SPLIT_RE = re.compile(r"[?&#=]")
_AUTH_LABEL_SPLIT_RE = re.compile(r"[.:]")
_DATA_URI_RE = re.compile(r"""(?i)(?<![\w.+-])data:[^\s"'`()\[\]{}<>]+""")
_DATA_URI_PART_SPLIT_RE = re.compile(r"[,;=?&]")
_URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_DOTTED_DOMAIN_PATH_RE = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+[:/]")
_SAFE_SEG_RE = re.compile(r"^[\w.@+-]{1,40}$")
_HEX_SHA_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
_SHA256_PREFIXED_RE = re.compile(r"^sha256:[0-9a-fA-F]{64}$")
_SRI_RE = re.compile(r"^sha(?:384|512)-[A-Za-z0-9+/=]{40,128}$")
_GO_H1_RE = re.compile(r"^h1:[A-Za-z0-9+/=]{40,64}$")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_REDACTED_TOKEN_RE = re.compile(r"^REDACTED:[a-z0-9_]+$")

_SYS_REMINDER_OPEN = "<" + "system-reminder>"
_SYS_REMINDER_CLOSE = "</" + "system-reminder>"
_PASTED_OPEN_HEAD_RE = re.compile(r"<pasted_content(?=[\s>])", re.IGNORECASE)
_PASTED_CLOSE_HEAD_RE = re.compile(r"</pasted_content(?=[\s>])", re.IGNORECASE)
_WS_RUN_RE = re.compile(r"\s+")


def reset_known_secrets_cache() -> None:
    """Clear the cached known-secrets tuple (for unit tests)."""
    global _KNOWN_SECRETS_CACHE
    _KNOWN_SECRETS_CACHE = None


def _is_secret_env_key(key: str) -> bool:
    upper = key.upper()
    if upper in _EXCLUDED_EXACT_ENV_KEYS or upper.endswith(_EXCLUDED_ENV_KEY_SUFFIXES):
        return False
    return _SECRET_ENV_KEY_RE.search(key) is not None


def _read_settings_env() -> dict[str, str]:
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    settings_path = Path(config_dir).expanduser() / "settings.json"
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(settings_path, flags)
    except OSError:
        return {}
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > SETTINGS_MAX_BYTES:
            return {}
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 65_536)
            if not chunk:
                break
            total += len(chunk)
            if total > SETTINGS_MAX_BYTES:
                return {}
            chunks.append(chunk)
    except OSError:
        return {}
    finally:
        os.close(fd)

    try:
        parsed = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    env_obj = parsed.get("env")
    if not isinstance(env_obj, dict):
        return {}
    return {k: v for k, v in env_obj.items() if isinstance(k, str) and isinstance(v, str)}


def load_known_secrets() -> tuple[str, ...]:
    """Load secret env values (>=16 chars) from Claude settings.json env + os.environ."""
    global _KNOWN_SECRETS_CACHE
    if _KNOWN_SECRETS_CACHE is not None:
        return _KNOWN_SECRETS_CACHE

    values: set[str] = set()
    for source in (_read_settings_env(), os.environ):
        for key, val in source.items():
            if len(val) >= MIN_KNOWN_SECRET_LEN and _is_secret_env_key(key):
                values.add(val)

    _KNOWN_SECRETS_CACHE = tuple(sorted(values, key=len, reverse=True))
    return _KNOWN_SECRETS_CACHE


def _sub_with_cap(
    pattern: re.Pattern[str],
    repl_fn: Callable[[re.Match[str]], str],
    text: str,
    cap_idx: int,
) -> tuple[str, int]:
    parts: list[str] = []
    pos = 0
    delta = 0
    straddled_new_cap: int | None = None
    for m in pattern.finditer(text):
        start, end = m.span()
        parts.append(text[pos:start])
        rep = repl_fn(m)
        parts.append(rep)
        pos = end
        if rep != m.group(0):
            rep_len = len(rep)
            if end <= cap_idx:
                delta += rep_len - (end - start)
            elif start < cap_idx:
                straddled_new_cap = start + delta + rep_len
                delta += rep_len - (end - start)
    if pos == 0:
        return text, cap_idx
    parts.append(text[pos:])
    new_cap = straddled_new_cap if straddled_new_cap is not None else cap_idx + delta
    return "".join(parts), max(0, new_cap)


def _scrub_known_secrets(text: str, cap_idx: int) -> tuple[str, int]:
    secrets = load_known_secrets()
    if not secrets:
        return text, cap_idx
    pattern = re.compile("|".join(re.escape(s) for s in secrets))
    return _sub_with_cap(pattern, lambda _m: "[REDACTED:known]", text, cap_idx)


def _redact_private_keys(text: str, cap_idx: int) -> tuple[str, int]:
    parts: list[str] = []
    pos = 0
    delta = 0
    straddled_new_cap: int | None = None
    rep = "[REDACTED:private_key]"
    rep_len = len(rep)
    while True:
        match = _PEM_BEGIN_RE.search(text, pos)
        if match is None:
            if not parts:
                return text, cap_idx
            parts.append(text[pos:])
            break
        start = match.start()
        parts.append(text[pos:start])
        parts.append(rep)
        end_marker = f"-----END {match.group(1)}-----"
        end_idx = text.find(end_marker, match.end(), match.end() + PEM_END_WINDOW)
        end = len(text) if end_idx == -1 else end_idx + len(end_marker)
        if end <= cap_idx:
            delta += rep_len - (end - start)
        elif start < cap_idx:
            straddled_new_cap = start + delta + rep_len
            delta += rep_len - (end - start)
        if end_idx == -1:
            break
        pos = end
    new_cap = straddled_new_cap if straddled_new_cap is not None else cap_idx + delta
    return "".join(parts), max(0, new_cap)


def _apply_patterns(text: str, cap_idx: int) -> tuple[str, int]:
    text, cap_idx = _redact_private_keys(text, cap_idx)
    for kind, pattern, validator in PATTERNS:

        def _replace(
            m: re.Match[str],
            k: str = kind,
            v: PatternValidator | None = validator,
        ) -> str:
            if v is not None and not v(m):
                return m.group(0)
            if k == "url_userinfo":
                return f"{m.group(1)}[REDACTED:url_userinfo]@"
            if k == "docker_auth":
                return f'{m.group(1)}[REDACTED:docker_auth]"'
            if k == "kv_secret":
                if m.group(2) is not None:
                    return f"{m.group(1)}{m.group(2)}[REDACTED:kv_secret]{m.group(2)}"
                return f"{m.group(1)}[REDACTED:kv_secret]"
            if k in {"query_secret", "auth_header", "kubeconfig"}:
                return f"{m.group(1)}[REDACTED:{k}]"
            return f"[REDACTED:{k}]"

        text, cap_idx = _sub_with_cap(pattern, _replace, text, cap_idx)
    return text, cap_idx


def _cap_output(text: str, extra_pre_sliced: int, cap_idx: int) -> tuple[str, bool]:
    effective_limit = min(cap_idx, MAX_OUTPUT_CHARS) if extra_pre_sliced > 0 else MAX_OUTPUT_CHARS
    if len(text) <= effective_limit and extra_pre_sliced == 0:
        return text, False

    redacted_spans = [m.span() for m in _REDACTED_SPAN_RE.finditer(text)]
    cut = min(len(text), effective_limit)
    for _ in range(4):
        n_truncated = (len(text) - cut) + extra_pre_sliced
        suffix = f"[TRUNCATED:{n_truncated} chars]"
        budget = MAX_OUTPUT_CHARS - len(suffix)
        if extra_pre_sliced > 0:
            budget = min(budget, cap_idx)
        candidate_cut = min(len(text), budget)
        for start, end in redacted_spans:
            if start < candidate_cut < end:
                candidate_cut = start
                break
        candidate_cut = max(0, candidate_cut)
        if candidate_cut == cut:
            break
        cut = candidate_cut

    n_truncated = (len(text) - cut) + extra_pre_sliced
    suffix = f"[TRUNCATED:{n_truncated} chars]"
    return text[:cut] + suffix, True


def _scrub_pii(text: str) -> str:
    text = _EMAIL_RE.sub("[EMAIL]", text)
    text = _INTERNAL_HOST_RE.sub("[HOST]", text)
    return _RFC1918_RE.sub("[HOST]", text)


def _is_paste_marker(line: str) -> bool:
    if any(pat.search(line) for pat in _PASTE_MARKER_RES):
        return True
    return len(_MULTI_SPACE_RE.findall(line.strip())) >= 3


def _is_indented_cont(line: str) -> bool:
    return line.startswith(("  ", "\t"))


def _truncate_pastes(text: str) -> tuple[str, bool]:
    if len(text) <= PASTE_GATE_MIN_CHARS:
        return text, False

    had_trailing_nl = text.endswith("\n")
    raw = text[:-1] if had_trailing_nl else text
    lines = raw.split("\n")
    out: list[str] = []
    truncated_any = False
    i = 0
    n = len(lines)

    while i < n:
        fence_match = _FENCE_OPEN_RE.match(lines[i])
        if fence_match is not None:
            fence, info = fence_match.group(1), fence_match.group(2)
            if not (fence.startswith("`") and "`" in info):
                close_re = re.compile(rf"^ {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*\r?$")
                open_line = lines[i]
                i += 1
                inner: list[str] = []
                close_line: str | None = None
                while i < n:
                    if close_re.match(lines[i]):
                        close_line = lines[i]
                        i += 1
                        break
                    inner.append(lines[i])
                    i += 1

                countable = [ln for ln in inner if not _PASTE_TRUNC_LINE_RE.match(ln)]
                out.append(open_line)
                if len(countable) > PASTE_KEEP_LINES:
                    out.extend(countable[:PASTE_KEEP_LINES])
                    out.append(f"[PASTE: {len(countable) - PASTE_KEEP_LINES} more lines truncated]")
                    truncated_any = True
                else:
                    out.extend(inner)
                if close_line is not None:
                    out.append(close_line)
                continue

        if _is_paste_marker(lines[i]) or _PASTE_TRUNC_LINE_RE.match(lines[i]):
            run_start = i
            i += 1
            while i < n:
                fm = _FENCE_OPEN_RE.match(lines[i])
                if fm is not None and not (fm.group(1).startswith("`") and "`" in fm.group(2)):
                    break
                if (
                    _is_paste_marker(lines[i])
                    or _PASTE_TRUNC_LINE_RE.match(lines[i])
                    or not lines[i].strip()
                    or _is_indented_cont(lines[i])
                ):
                    i += 1
                else:
                    break

            run_end = i
            while run_end > run_start and not lines[run_end - 1].strip():
                run_end -= 1
            run = lines[run_start:run_end]

            countable = [ln for ln in run if ln.strip() and not _PASTE_TRUNC_LINE_RE.match(ln)]
            markers = [ln for ln in countable if _is_paste_marker(ln)]
            has_traceback = any(_TRACEBACK_HEAD_RE.match(ln) for ln in countable)
            is_paste_run = False
            if len(countable) >= PASTE_MIN_UNFENCED_LINES:
                if len(markers) / len(countable) >= PASTE_MIN_MARKER_RATIO:
                    is_paste_run = True
                elif has_traceback and len(markers) >= 3:
                    non_cont = [
                        ln for ln in countable if _is_paste_marker(ln) or not _is_indented_cont(ln)
                    ]
                    if non_cont and len(markers) / len(non_cont) >= PASTE_MIN_MARKER_RATIO:
                        is_paste_run = True

            if is_paste_run:
                countable_all = [ln for ln in run if not _PASTE_TRUNC_LINE_RE.match(ln)]
                if len(countable_all) > PASTE_KEEP_LINES:
                    out.extend(countable_all[:PASTE_KEEP_LINES])
                    out.append(
                        f"[PASTE: {len(countable_all) - PASTE_KEEP_LINES} more lines truncated]"
                    )
                    truncated_any = True
                else:
                    out.extend(run)
                out.extend(lines[run_end:i])
                continue
            else:
                out.extend(lines[run_start:i])
                continue

        out.append(lines[i])
        i += 1

    result = "\n".join(out)
    if had_trailing_nl:
        result += "\n"
    return result, truncated_any


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = Counter(s)
    length = len(s)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


def _is_mixed_alnum(s: str) -> bool:
    return (
        any(c.isupper() for c in s) and any(c.islower() for c in s) and any(c.isdigit() for c in s)
    )


def _is_high_entropy_secret(s: str, *, min_len: int) -> bool:
    return len(s) >= min_len and _is_mixed_alnum(s) and _shannon_entropy(s) >= ENTROPY_THRESHOLD


def _is_safe_dotted_domain_path(tok: str) -> bool:
    if _DOTTED_DOMAIN_PATH_RE.match(tok) is None:
        return False
    segs = [s for s in re.split(r"[/:]", tok) if s]
    if not segs:
        return False
    for idx, seg in enumerate(segs):
        if idx > 0 and segs[idx - 1].endswith("@sha256") and re.fullmatch(r"[0-9a-fA-F]{64}", seg):
            continue
        if _SAFE_SEG_RE.fullmatch(seg) is None:
            return False
        if len(seg) >= ENTROPY_MIN_URL_SEG_LEN and _is_mixed_alnum(seg):
            return False
    return True


def _is_allowlisted_non_url(tok: str) -> bool:
    if (
        _HEX_SHA_RE.fullmatch(tok)
        or _SHA256_PREFIXED_RE.fullmatch(tok)
        or _SRI_RE.fullmatch(tok)
        or _GO_H1_RE.fullmatch(tok)
        or _UUID_RE.fullmatch(tok)
        or _REDACTED_TOKEN_RE.fullmatch(tok)
    ):
        return True
    if _is_safe_dotted_domain_path(tok):
        return True
    if tok.startswith(("/", "./", "../", "~/")):
        segs = [s for s in tok.lstrip("~/.").split("/") if s]
        if all(
            _SAFE_SEG_RE.fullmatch(seg) is not None
            and not (len(seg) >= ENTROPY_MIN_URL_SEG_LEN and _is_mixed_alnum(seg))
            for seg in segs
        ):
            return True
    return False


def _check_url_token(tok: str) -> bool:
    """Return True if the URL token (or any query/fragment/path/authority) trips entropy."""
    if tok.lower().startswith("data:"):
        return _is_high_entropy_secret(tok, min_len=ENTROPY_MIN_TOKEN_LEN)

    qf_match = _URL_QF_START_RE.search(tok)
    if qf_match is not None:
        base_url = tok[: qf_match.start()]
        qf_part = tok[qf_match.start() + 1 :]
        for q_tok in _URL_QUERY_SPLIT_RE.split(qf_part):
            q_tok = q_tok.rstrip(".:!?")
            if (
                q_tok
                and not _is_allowlisted_non_url(q_tok)
                and _is_high_entropy_secret(q_tok, min_len=ENTROPY_MIN_TOKEN_LEN)
            ):
                return True
    else:
        base_url = tok

    _, rest = base_url.split("://", 1)
    parts = rest.split("/")
    authority = parts[0]
    path_segs = [s for s in parts[1:] if s]
    has_userinfo = "@" in authority
    authority_secret = any(
        _is_high_entropy_secret(label, min_len=ENTROPY_MIN_URL_SEG_LEN)
        for label in _AUTH_LABEL_SPLIT_RE.split(authority)
        if label
    )
    authority_ok = bool(authority) and not authority_secret

    segments_safe = all(_SAFE_SEG_RE.fullmatch(seg) is not None for seg in path_segs)
    no_long_mixed_seg = not any(
        len(seg) >= ENTROPY_MIN_URL_SEG_LEN and _is_mixed_alnum(seg) for seg in path_segs
    )
    if authority_ok and not has_userinfo and segments_safe and no_long_mixed_seg:
        return False

    if authority_secret:
        return True

    for seg in path_segs:
        if _is_high_entropy_secret(seg, min_len=ENTROPY_MIN_URL_SEG_LEN):
            return True

    return _is_high_entropy_secret(tok, min_len=ENTROPY_MIN_TOKEN_LEN)


def _passes_entropy_gate(text: str) -> bool:
    """Return False if any non-allowlisted token/segment trips the entropy threshold."""
    # Strip "[REDACTED:url_userinfo]@" cleanly so URLs retain their scheme+host,
    # replace "[HOST]" with a safe hostname so [HOST] URLs stay intact URL tokens,
    # and replace remaining [REDACTED:...] markers with spaces.
    cleaned = text.replace("[REDACTED:url_userinfo]@", "")
    cleaned = cleaned.replace("[HOST]", "host.invalid")
    cleaned = _REDACTED_SPAN_RE.sub(" ", cleaned)

    for match in _DATA_URI_RE.finditer(cleaned):
        data_tok = match.group(0).rstrip(".:!?")
        if _is_high_entropy_secret(data_tok, min_len=ENTROPY_MIN_TOKEN_LEN):
            return False
        for part in _DATA_URI_PART_SPLIT_RE.split(data_tok):
            part = part.rstrip(".:!?")
            if part and _is_high_entropy_secret(part, min_len=ENTROPY_MIN_TOKEN_LEN):
                return False
    cleaned = _DATA_URI_RE.sub(" ", cleaned)

    for raw_tok in _TOKEN_SPLIT_RE.split(cleaned):
        tok = raw_tok.rstrip(".:!?")
        if not tok:
            continue
        if _URL_SCHEME_RE.match(tok):
            if _check_url_token(tok):
                return False
            continue
        if _is_allowlisted_non_url(tok):
            continue
        if _is_high_entropy_secret(tok, min_len=ENTROPY_MIN_TOKEN_LEN):
            return False
    return True


def redact_event(text: str) -> Redacted:
    """Redact secrets, PII, oversized pastes, and high-entropy tokens (fail-closed)."""
    if not text or not text.strip():
        return Redacted(text="", action="keep", dropped_reason=None)

    extra_pre_sliced = 0
    if len(text) > PRE_SLICE_LIMIT:
        extra_pre_sliced = len(text) - PRE_SLICE_LIMIT
        text = text[:PRE_SLICE_LIMIT]
        if not text.strip():
            return Redacted(text="", action="keep", dropped_reason=None)

    cap_idx = MAX_OUTPUT_CHARS
    text, cap_idx = _scrub_known_secrets(text, cap_idx)
    text, cap_idx = _apply_patterns(text, cap_idx)
    text, cap_truncated = _cap_output(text, extra_pre_sliced, cap_idx)
    text = _scrub_pii(text)
    text, paste_truncated = _truncate_pastes(text)

    if not _passes_entropy_gate(text):
        return Redacted(text="", action="drop", dropped_reason="entropy")

    action: Literal["keep", "truncate", "drop"] = (
        "truncate" if (cap_truncated or paste_truncated) else "keep"
    )
    return Redacted(text=text, action=action, dropped_reason=None)


def _strip_system_reminders(text: str) -> str:
    parts: list[str] = []
    pos = 0
    open_len = len(_SYS_REMINDER_OPEN)
    close_len = len(_SYS_REMINDER_CLOSE)
    while True:
        start = text.find(_SYS_REMINDER_OPEN, pos)
        if start == -1:
            parts.append(text[pos:])
            break
        end = text.find(_SYS_REMINDER_CLOSE, start + open_len)
        if end == -1:
            parts.append(text[pos:])
            break
        parts.append(text[pos:start])
        pos = end + close_len
    return "".join(parts)


def _convert_pasted_content(text: str) -> str:
    parts: list[str] = []
    pos = 0
    while True:
        open_m = _PASTED_OPEN_HEAD_RE.search(text, pos)
        if open_m is None:
            parts.append(text[pos:])
            break
        open_gt = text.find(">", open_m.end())
        if open_gt == -1:
            parts.append(text[pos:])
            break
        close_m = _PASTED_CLOSE_HEAD_RE.search(text, open_gt + 1)
        if close_m is None:
            parts.append(text[pos:])
            break
        close_gt = text.find(">", close_m.end())
        if close_gt == -1:
            parts.append(text[pos:])
            break
        parts.append(text[pos : open_m.start()])
        inner = text[open_gt + 1 : close_m.start()].strip("\n")
        parts.append(f"\n```\n{inner}\n```\n")
        pos = close_gt + 1
    return "".join(parts)


def hash_form(text: str) -> str:
    """Normalize text for cross-source deduplication; returns '' if dropped by redaction."""
    stripped = _strip_system_reminders(text)
    stripped = _convert_pasted_content(stripped)
    res = redact_event(stripped)
    if res.action == "drop":
        return ""
    return _WS_RUN_RE.sub(" ", res.text).strip()
