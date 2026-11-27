"""Pinned, strengthen-only rule loader, YAML/schema validator, and closed-form checks."""

from __future__ import annotations

import contextlib
import errno
import fcntl
import functools
import hashlib
import json
import os
import re
import stat
import subprocess
import time
import unicodedata
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from review_rules.paths import (
    StoreInsecure,
    get_repo_path,
    get_store_dir,
    read_regular_nofollow,
    verify_private_path,
)

__all__ = [
    "ALLOWED_IMPERATIVES",
    "CATEGORY_DEFAULT_REVIEWERS",
    "FETCH_STAMP_TTL_SECONDS",
    "MAX_RULE_BYTES",
    "LoadResult",
    "Rejection",
    "Rule",
    "RuleApproved",
    "RuleCheck",
    "RuleEvidence",
    "RuleProvenance",
    "RuleSafeLoader",
    "RuleScope",
    "RulesUnavailable",
    "closed_form_violations",
    "default_rule_metadata",
    "lint_directory",
    "load_rules",
    "run_git",
    "validate_rule",
    "verify_worktree_schema",
]

MAX_RULE_BYTES = 8192
FETCH_STAMP_TTL_SECONDS = 60.0

_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_RULE_TREE_PATH_RE = re.compile(r"^rules/[a-z0-9][a-z0-9-]*\.yaml$")
_RULE_FILENAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.yaml$")
_VENDORED_SCHEMA_PATH = Path(__file__).resolve().parent / "schema" / "rule.schema.json"

ALLOWED_IMPERATIVES: tuple[str, ...] = (
    "Flag",
    "Require",
    "Check",
    "Verify",
    "Ensure",
    "Prefer",
    "Avoid",
    "Use",
    "Keep",
    "Always",
    "Never",
    "Do not",
)

_IMPERATIVE_RE = re.compile(
    r"^(?:Flag|Require|Check|Verify|Ensure|Prefer|Avoid|Use|Keep|Always|Never|Do not)(?:[ \t]|$)"
)
_URL_RE = re.compile(r"https?://", re.IGNORECASE)
_SHELL_OP_RE = re.compile(r"\$\(|&&|\|\|")
_HSPACE_RE = re.compile(r"[ \t]+")

_W = r"[^.;,\n]"
_SUPPRESSION_CLAUSES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "suppression_clause_1",
        re.compile(
            rf"\b(?:ignor|skip|suppress|omit|overlook|bypass|downgrad|waiv|exempt|"
            rf"whitelist|allowlist|silenc|mute|hid|drop|disregard|dismiss|exclud)\w{{0,4}}\b"
            rf"{_W}{{0,80}}"
            rf"\b(?:finding|issue|warning|review|reviewer|severity|report|rule|nit|check)s?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "suppression_clause_2",
        re.compile(
            rf"\b(?:do not|don't|never|avoid|prefer not to|no need to|stop|refrain from)\b"
            rf"{_W}{{0,20}}"
            rf"\b(?:flag|report|mention|comment|warn|review)\w{{0,4}}\b",
            re.IGNORECASE,
        ),
    ),
    (
        "suppression_clause_3",
        re.compile(
            rf"\b(?:lower|reduc|downgrad|relax|loosen|soften|lenient)\w{{0,4}}\b"
            rf"{_W}{{0,40}}"
            rf"\b(?:severity|priority|rule|review|check)s?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "suppression_clause_4",
        re.compile(
            rf"\b(?:treat|mark)\w{{0,2}}\b"
            rf"{_W}{{0,20}}"
            rf"\bas\b{_W}{{0,10}}\b(?:fine|ok|acceptable)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "suppression_clause_5",
        re.compile(
            rf"\b(?:approv(?:e|es|ed|ing)|lgtm|auto-?merg(?:e|es|ed|ing)|"
            rf"accept(?:s|ed|ing)?|pass(?:es|ed|ing)?)\b"
            rf"{_W}{{0,40}}"
            rf"\b(?:change|pr|pull request|diff|commit)s?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "suppression_clause_6",
        re.compile(
            r"\b(?:false[_ -]?positives?|user-deferred|needs[_ -]?context|"
            r"no[ \t]+[a-z-]{0,25}[ \t]*findings)\b",
            re.IGNORECASE,
        ),
    ),
)

CATEGORY_DEFAULT_REVIEWERS: dict[str, tuple[str, ...]] = {
    "security": ("security",),
    "testing": ("qa",),
    "performance": ("performance",),
    "correctness": ("correctness",),
    "architecture": ("code-quality",),
    "docs": ("code-quality",),
    "style": ("code-quality",),
    "process": ("code-quality",),
}


class RulesUnavailable(Exception):
    """Raised when the rules repository or pinned schema cannot be loaded safely."""


@dataclass(frozen=True)
class Rejection:
    path: str
    reason: str


@dataclass(frozen=True)
class RuleScope:
    repos: tuple[str, ...]
    globs: tuple[str, ...]
    languages: tuple[str, ...]
    reviewers: tuple[str, ...]
    skills: tuple[str, ...]


@dataclass(frozen=True)
class RuleEvidence:
    event_id: str
    kind: str
    strength: str
    author_class: str


@dataclass(frozen=True)
class RuleProvenance:
    created_by: str
    created_at: str
    model: str | None = None
    prompt_version: str | None = None


@dataclass(frozen=True)
class RuleApproved:
    by: str
    at: str


@dataclass(frozen=True)
class RuleCheck:
    tool: str
    ref: str


@dataclass(frozen=True)
class Rule:
    id: str
    status: str
    trial: bool
    category: str
    statement: str
    rationale: str
    do_not_apply_when: tuple[str, ...]
    scope: RuleScope
    evidence: tuple[RuleEvidence, ...]
    provenance: RuleProvenance
    review_by: str
    approved: RuleApproved | None = None
    check: RuleCheck | None = None


@dataclass(frozen=True)
class LoadResult:
    sha: str
    fetched: bool
    fetch_error: str | None
    active: tuple[Rule, ...]
    candidates: tuple[Rule, ...]
    retired: tuple[Rule, ...]
    rejected: tuple[Rejection, ...]


class RuleSafeLoader(yaml.SafeLoader):
    """Hardened YAML SafeLoader that preserves dates as strings and blocks aliases/merges/dups."""

    yaml_implicit_resolvers = {
        key: [(tag, rx) for tag, rx in entries if tag != "tag:yaml.org,2002:timestamp"]
        for key, entries in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    yaml_constructors = {
        key: ctor
        for key, ctor in yaml.SafeLoader.yaml_constructors.items()
        if key not in {"tag:yaml.org,2002:merge", "tag:yaml.org,2002:timestamp"}
    }

    def construct_mapping(self, node: yaml.Node, deep: bool = False) -> dict[Any, Any]:
        if not isinstance(node, yaml.MappingNode):
            node_kind = getattr(node, "id", type(node).__name__)
            raise yaml.constructor.ConstructorError(
                None,
                None,
                f"expected a mapping node, but found {node_kind}",
                node.start_mark,
            )
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "YAML merge keys are not allowed",
                    key_node.start_mark,
                )
            key = self.construct_object(key_node, deep=deep)
            try:
                hash(key)
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found unhashable key",
                    key_node.start_mark,
                ) from exc
            if key in mapping:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


_GIT_REPO_ENV_VARS: tuple[str, ...] = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


def _run_git_bytes(
    repo_path: Path | os.PathLike[str],
    args: Sequence[str],
    *,
    timeout: float = 10.0,
    env_overrides: Mapping[str, str] | None = None,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run git against repo_path in bytes mode with isolated env and ceiling dir."""
    resolved_repo = Path(repo_path).resolve()
    env = os.environ.copy()
    for var in _GIT_REPO_ENV_VARS:
        env.pop(var, None)
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=5",
            "GIT_CEILING_DIRECTORIES": str(resolved_repo.parent),
        }
    )
    if env_overrides:
        env.update(env_overrides)
    cmd = [
        "git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "protocol.ext.allow=never",
        "-C",
        str(resolved_repo),
        *args,
    ]
    return subprocess.run(
        cmd,
        input=input_bytes,
        capture_output=True,
        text=False,
        timeout=timeout,
        check=False,
        env=env,
    )


def run_git(
    repo_path: Path | os.PathLike[str],
    args: Sequence[str],
    *,
    timeout: float = 10.0,
    env_overrides: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run git against repo_path with fsmonitor/ext disabled, isolated env, and strict UTF-8."""
    proc = _run_git_bytes(
        repo_path,
        args,
        timeout=timeout,
        env_overrides=env_overrides,
    )
    stdout = proc.stdout.decode("utf-8", errors="strict")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)


@functools.lru_cache(maxsize=1)
def _vendored_schema_info() -> tuple[str, dict[str, Any], jsonschema.Draft202012Validator]:
    raw = _VENDORED_SCHEMA_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    schema = json.loads(raw.decode("utf-8"))
    validator = jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.FormatChecker(),
    )
    return digest, schema, validator


def _normalize_text(text: str) -> str:
    nfkc = unicodedata.normalize("NFKC", text)
    folded = (
        nfkc.replace(chr(0x2018), "'")
        .replace(chr(0x2019), "'")
        .replace(chr(0x201C), '"')
        .replace(chr(0x201D), '"')
    )
    return "\n".join(_HSPACE_RE.sub(" ", line).strip(" \t") for line in folded.split("\n"))


def _normalize_data(obj: Any) -> Any:
    if isinstance(obj, str):
        return _normalize_text(obj)
    if isinstance(obj, list):
        return [_normalize_data(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _normalize_data(v) for k, v in obj.items()}
    return obj


def _iter_rule_strings(obj: Any, path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            yield from _iter_rule_strings(item, f"{path}[{idx}]")
    elif isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str):
                key_path = f"{path}.{key}" if path else key
                yield key_path, key
                yield from _iter_rule_strings(value, key_path)


def closed_form_violations(rule: dict[str, Any]) -> list[str]:
    """Return all closed-form and strengthen-only violations on a rule mapping."""
    violations: list[str] = []

    for field_path, raw_val in _iter_rule_strings(rule):
        norm_val = _normalize_text(raw_val)
        if _URL_RE.search(norm_val):
            violations.append(f"forbidden_url ({field_path}): URLs are not allowed")
        if _SHELL_OP_RE.search(norm_val):
            violations.append(
                f"forbidden_shell_syntax ({field_path}): "
                "shell operators ($(, &&, ||) are not allowed"
            )
        for ch in norm_val:
            cat = unicodedata.category(ch)
            if (cat == "Cc" and not (field_path == "rationale" and ch == "\n")) or cat in {
                "Cf",
                "Co",
                "Cn",
                "Zl",
                "Zp",
            }:
                violations.append(
                    f"forbidden_unicode ({field_path}): disallowed U+{ord(ch):04X} ({cat})"
                )
                break

    raw_stmt = rule.get("statement")
    if isinstance(raw_stmt, str):
        stmt = _normalize_text(raw_stmt)
        if "\n" in stmt or "\r" in stmt or chr(0x2028) in stmt or chr(0x2029) in stmt:
            violations.append("statement_multiline: statement must be a single line")
        if not _IMPERATIVE_RE.match(stmt):
            violations.append(
                "statement_imperative: statement must start with one of "
                + ", ".join(ALLOWED_IMPERATIVES)
            )
        for clause_id, pattern in _SUPPRESSION_CLAUSES:
            if pattern.search(stmt):
                violations.append(
                    f"{clause_id} (statement): disallowed review-suppression phrasing"
                )

    raw_dnaw = rule.get("do_not_apply_when")
    if isinstance(raw_dnaw, list):
        if len(raw_dnaw) > 5:
            violations.append(
                f"do_not_apply_when_limit: at most 5 entries allowed (got {len(raw_dnaw)})"
            )
        for idx, entry in enumerate(raw_dnaw):
            if isinstance(entry, str):
                norm_entry = _normalize_text(entry)
                for clause_id, pattern in _SUPPRESSION_CLAUSES:
                    if pattern.search(norm_entry):
                        violations.append(
                            f"{clause_id} (do_not_apply_when[{idx}]): "
                            "disallowed review-suppression phrasing"
                        )

    status = rule.get("status")
    raw_rationale = rule.get("rationale")
    if status != "retired" and isinstance(raw_rationale, str):
        norm_rat = _normalize_text(raw_rationale)
        for clause_id, pattern in _SUPPRESSION_CLAUSES:
            if pattern.search(norm_rat):
                violations.append(
                    f"{clause_id} (rationale): disallowed review-suppression phrasing"
                )

    return violations


def _raw_unicode_violations(data: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    for field_path, raw_val in _iter_rule_strings(data):
        for ch in raw_val:
            cat = unicodedata.category(ch)
            allowed_cc = ch == "\t" or (field_path == "rationale" and ch == "\n")
            if (cat == "Cc" and not allowed_cc) or cat in {"Cf", "Co", "Cn", "Zl", "Zp"}:
                violations.append(
                    f"forbidden_unicode ({field_path}): disallowed U+{ord(ch):04X} ({cat})"
                )
                break
    return violations


def validate_rule(data: dict[str, Any], stem: str) -> list[str]:
    """Validate a parsed rule mapping against the vendored schema, stem, and closed-form checks."""
    _, _, validator = _vendored_schema_info()
    errors: list[str] = []
    seen: set[str] = set()

    def _add(msg: str) -> None:
        if msg not in seen:
            seen.add(msg)
            errors.append(msg)

    raw_schema_errs = sorted(
        validator.iter_errors(data),
        key=lambda err: (list(err.path), err.message),
    )
    for err in raw_schema_errs:
        loc = ".".join(str(part) for part in err.path) or "<root>"
        _add(f"schema[{loc}]: {err.message}")

    raw_id = data.get("id")
    if raw_id != stem:
        _add(f"id_mismatch: rule id {raw_id!r} must match filename stem {stem!r}")

    normalized = _normalize_data(data)
    if isinstance(normalized, dict):
        norm_schema_errs = sorted(
            validator.iter_errors(normalized),
            key=lambda err: (list(err.path), err.message),
        )
        for err in norm_schema_errs:
            loc = ".".join(str(part) for part in err.path) or "<root>"
            _add(f"schema[{loc}]: {err.message}")

        for v in _raw_unicode_violations(data):
            _add(v)
        for v in closed_form_violations(normalized):
            _add(v)

    return errors


def default_rule_metadata(category: str, created_at: str) -> dict[str, Any]:
    """Return default trial/scope/review_by metadata for newly synthesized or seeded rules."""
    reviewers = CATEGORY_DEFAULT_REVIEWERS.get(category)
    if reviewers is None:
        raise ValueError(f"Unknown rule category: {category!r}")
    review_by = (date.fromisoformat(created_at) + timedelta(days=90)).isoformat()
    return {
        "trial": True,
        "scope": {
            "skills": ["*"],
            "reviewers": list(reviewers),
        },
        "review_by": review_by,
    }


def _parse_yaml_mapping(text: str) -> dict[str, Any]:
    ev_loader = RuleSafeLoader(text)
    doc_count = 0
    try:
        while ev_loader.peek_event() is not None:
            event = ev_loader.get_event()
            if isinstance(event, yaml.AliasEvent):
                raise yaml.YAMLError("YAML aliases are not allowed")
            if getattr(event, "anchor", None) is not None:
                raise yaml.YAMLError("YAML anchors are not allowed")
            if isinstance(event, yaml.DocumentStartEvent):
                doc_count += 1
                if doc_count > 1:
                    raise yaml.YAMLError("multi-document YAML streams are not allowed")
    finally:
        ev_loader.dispose()

    data_loader = RuleSafeLoader(text)
    try:
        data = data_loader.get_single_data()
    finally:
        data_loader.dispose()

    if not isinstance(data, dict):
        raise yaml.YAMLError("rule YAML root must be a mapping")
    return data


def _build_rule(norm: dict[str, Any]) -> Rule:
    scope_raw = norm["scope"]
    scope = RuleScope(
        repos=tuple(scope_raw["repos"]),
        globs=tuple(scope_raw["globs"]),
        languages=tuple(scope_raw["languages"]),
        reviewers=tuple(scope_raw["reviewers"]),
        skills=tuple(scope_raw["skills"]),
    )
    evidence = tuple(
        RuleEvidence(
            event_id=item["event_id"],
            kind=item["kind"],
            strength=item["strength"],
            author_class=item["author_class"],
        )
        for item in norm["evidence"]
    )
    prov_raw = norm["provenance"]
    provenance = RuleProvenance(
        created_by=prov_raw["created_by"],
        created_at=prov_raw["created_at"],
        model=prov_raw.get("model"),
        prompt_version=prov_raw.get("prompt_version"),
    )
    approved_raw = norm.get("approved")
    approved = (
        RuleApproved(by=approved_raw["by"], at=approved_raw["at"])
        if approved_raw is not None
        else None
    )
    check_raw = norm.get("check")
    check = (
        RuleCheck(tool=check_raw["tool"], ref=check_raw["ref"]) if check_raw is not None else None
    )
    return Rule(
        id=norm["id"],
        status=norm["status"],
        trial=norm["trial"],
        category=norm["category"],
        statement=norm["statement"],
        rationale=norm["rationale"],
        do_not_apply_when=tuple(norm["do_not_apply_when"]),
        scope=scope,
        evidence=evidence,
        provenance=provenance,
        review_by=norm["review_by"],
        approved=approved,
        check=check,
    )


def _parse_and_validate_rule(
    text: str,
    *,
    stem: str,
    display_path: str,
) -> tuple[Rule | None, Rejection | None]:
    try:
        raw_data = _parse_yaml_mapping(text)
    except (yaml.YAMLError, UnicodeDecodeError, RecursionError, TypeError) as exc:
        detail = _one_line(str(exc)) or type(exc).__name__
        return None, Rejection(display_path, f"yaml_parse: {detail}")

    errors = validate_rule(raw_data, stem)
    if errors:
        return None, Rejection(display_path, "; ".join(errors))

    norm = _normalize_data(raw_data)
    if not isinstance(norm, dict):
        return None, Rejection(display_path, "yaml_parse: rule YAML root must be a mapping")
    return _build_rule(norm), None


def _one_line(text: str, max_len: int = 200) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:max_len]
    return ""


def _get_fetch_stamp_path(resolved_repo: Path, store_dir: Path) -> Path | None:
    try:
        verify_private_path(store_dir, is_dir=True)
    except FileNotFoundError:
        return None
    digest = hashlib.sha256(str(resolved_repo).encode("utf-8")).hexdigest()[:16]
    return store_dir / f"fetch-stamp-{digest}"


def _is_fetch_stamp_fresh(stamp_path: Path) -> bool:
    try:
        raw = read_regular_nofollow(stamp_path, max_bytes=128, require_private=True)
        ts = float(raw.decode("utf-8").strip())
    except (FileNotFoundError, StoreInsecure, OSError, ValueError, UnicodeDecodeError):
        return False
    age = time.time() - ts
    return 0.0 <= age < FETCH_STAMP_TTL_SECONDS


def _write_fetch_stamp(stamp_path: Path) -> None:
    tmp_path = stamp_path.parent / f".{stamp_path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(tmp_path, flags, 0o600)
    except OSError:
        return
    try:
        payload = f"{time.time():.6f}\n".encode()
        view = memoryview(payload)
        written = 0
        while written < len(payload):
            written += os.write(fd, view[written:])
    except OSError:
        os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        return
    os.close(fd)
    try:
        os.replace(tmp_path, stamp_path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)


def _open_fetch_lock(lock_path: Path) -> int:
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR, getattr(errno, "EMLINK", -1)}:
            raise StoreInsecure(f"Refusing symlink or non-file lock path: {lock_path}") from exc
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise StoreInsecure(f"Expected regular lock file, got non-regular: {lock_path}")
        uid = os.getuid()
        if st.st_uid != uid:
            raise StoreInsecure(f"Untrusted owner uid {st.st_uid} on {lock_path} (expected {uid})")
        mode = stat.S_IMODE(st.st_mode)
        if mode & 0o077:
            raise StoreInsecure(f"Insecure permissions {oct(mode)} on {lock_path}; expected 0600")
    except Exception:
        os.close(fd)
        raise
    return fd


def _run_fetch_origin_main(resolved_repo: Path) -> tuple[bool, str | None]:
    try:
        proc = run_git(resolved_repo, ["fetch", "--quiet", "origin", "main"], timeout=10.0)
    except subprocess.TimeoutExpired:
        return False, "git fetch timed out after 10s"
    except (OSError, UnicodeDecodeError) as exc:
        return False, _one_line(str(exc)) or "git fetch failed"

    if proc.returncode == 0:
        return True, None

    err = _one_line(proc.stderr) or _one_line(proc.stdout) or f"git fetch exited {proc.returncode}"
    return False, err


def _maybe_fetch_origin_main(
    resolved_repo: Path,
    *,
    fetch: bool,
) -> tuple[bool, str | None]:
    if not fetch or os.environ.get("REVIEW_RULES_NO_FETCH") == "1":
        return False, None

    stamp_path = _get_fetch_stamp_path(resolved_repo, get_store_dir())
    if stamp_path is None:
        return _run_fetch_origin_main(resolved_repo)

    if _is_fetch_stamp_fresh(stamp_path):
        return False, None

    lock_path = stamp_path.parent / "rules-repo.lock"
    lock_fd = _open_fetch_lock(lock_path)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if _is_fetch_stamp_fresh(stamp_path):
            return False, None
        fetched, fetch_error = _run_fetch_origin_main(resolved_repo)
        if fetched:
            _write_fetch_stamp(stamp_path)
        return fetched, fetch_error
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def _partition_rule(
    rule: Rule,
    active: list[Rule],
    candidates: list[Rule],
    retired: list[Rule],
) -> None:
    if rule.status == "active":
        active.append(rule)
    elif rule.status == "candidate":
        candidates.append(rule)
    elif rule.status == "retired":
        retired.append(rule)


def verify_worktree_schema(repo_path: Path | os.PathLike[str]) -> None:
    """Verify <repo>/schema/rule.schema.json matches the vendored schema SHA-256."""
    schema_path = Path(repo_path) / "schema" / "rule.schema.json"
    vendored_digest, _, _ = _vendored_schema_info()
    try:
        raw = read_regular_nofollow(schema_path, max_bytes=1_048_576, require_private=False)
    except FileNotFoundError as exc:
        raise RulesUnavailable("schema drift: schema/rule.schema.json missing in worktree") from exc
    except (OSError, ValueError, StoreInsecure) as exc:
        raise RulesUnavailable(f"schema drift: cannot read {schema_path}: {exc}") from exc
    if hashlib.sha256(raw).hexdigest() != vendored_digest:
        raise RulesUnavailable("schema drift: schema/rule.schema.json differs from vendored schema")


def _batch_read_blobs(
    resolved_repo: Path,
    entries: list[tuple[str, str]],
) -> list[tuple[str, bytes | None, str | None]]:
    """Fetch git blobs for (rel_path, oid) pairs via a single git cat-file --batch call."""
    if not entries:
        return []
    stdin_payload = "".join(f"{oid}\n" for _rel, oid in entries).encode("ascii")
    try:
        batch_proc = _run_git_bytes(
            resolved_repo,
            ["cat-file", "--batch"],
            input_bytes=stdin_payload,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RulesUnavailable(f"git cat-file --batch failed: {exc}") from exc

    if batch_proc.returncode != 0:
        detail = _one_line(batch_proc.stderr.decode("utf-8", errors="replace"))
        raise RulesUnavailable(
            f"git cat-file --batch failed: {detail or f'exited {batch_proc.returncode}'}"
        )

    raw = batch_proc.stdout
    pos = 0
    total = len(raw)
    results: list[tuple[str, bytes | None, str | None]] = []

    for rel_path, expected_oid in entries:
        nl_idx = raw.find(b"\n", pos)
        if nl_idx == -1:
            raise RulesUnavailable(f"git cat-file --batch output truncated before {rel_path}")
        header = raw[pos:nl_idx].decode("utf-8", errors="replace")
        pos = nl_idx + 1
        if header.endswith(" missing"):
            results.append((rel_path, None, "git_cat_file_error: blob missing"))
            continue
        hdr_parts = header.split()
        if len(hdr_parts) != 3 or hdr_parts[0] != expected_oid or hdr_parts[1] != "blob":
            raise RulesUnavailable(
                f"Unexpected git cat-file --batch header for {rel_path}: {header!r}"
            )
        try:
            blob_size = int(hdr_parts[2])
        except ValueError as exc:
            raise RulesUnavailable(
                f"Invalid git cat-file --batch size for {rel_path}: {hdr_parts[2]!r}"
            ) from exc
        if blob_size < 0:
            raise RulesUnavailable(
                f"Negative git cat-file --batch size for {rel_path}: {blob_size}"
            )
        content_end = pos + blob_size
        if content_end > total:
            raise RulesUnavailable(f"git cat-file --batch output truncated reading {rel_path}")
        blob_bytes = raw[pos:content_end]
        pos = content_end
        if pos < total and raw[pos : pos + 1] == b"\n":
            pos += 1
        results.append((rel_path, blob_bytes, None))

    return results


def load_rules(
    repo_path: Path | os.PathLike[str] | None = None,
    fetch: bool = True,
) -> LoadResult:
    """Load and validate rules from pinned refs/remotes/origin/main^{commit}."""
    target = Path(repo_path) if repo_path is not None else get_repo_path()
    resolved_repo = target.resolve()
    if not resolved_repo.exists() or not resolved_repo.is_dir():
        raise RulesUnavailable(f"Rules repository not found: {resolved_repo}")

    fetched, fetch_error = _maybe_fetch_origin_main(resolved_repo, fetch=fetch)

    try:
        sha_proc = run_git(
            resolved_repo,
            ["rev-parse", "--verify", "refs/remotes/origin/main^{commit}"],
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as exc:
        raise RulesUnavailable(f"Failed to resolve origin/main: {exc}") from exc

    if sha_proc.returncode != 0:
        detail = _one_line(sha_proc.stderr) or "refs/remotes/origin/main not found"
        raise RulesUnavailable(f"Cannot resolve refs/remotes/origin/main^{{commit}}: {detail}")

    sha = sha_proc.stdout.strip()
    if not _GIT_SHA_RE.fullmatch(sha):
        raise RulesUnavailable(f"Invalid commit SHA from rev-parse: {sha!r}")

    vendored_digest, _, _ = _vendored_schema_info()
    try:
        schema_proc = _run_git_bytes(
            resolved_repo,
            ["show", "--no-textconv", "--no-ext-diff", f"{sha}:schema/rule.schema.json"],
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RulesUnavailable(f"Failed to read schema/rule.schema.json at {sha}: {exc}") from exc

    if schema_proc.returncode != 0:
        raise RulesUnavailable("schema drift: schema/rule.schema.json missing at pinned commit")

    remote_digest = hashlib.sha256(schema_proc.stdout).hexdigest()
    if remote_digest != vendored_digest:
        raise RulesUnavailable("schema drift: schema/rule.schema.json differs from vendored schema")

    try:
        tree_proc = run_git(resolved_repo, ["ls-tree", "-l", "-z", sha, "rules/"])
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as exc:
        raise RulesUnavailable(f"Failed to list rules/ at {sha}: {exc}") from exc

    if tree_proc.returncode != 0:
        detail = _one_line(tree_proc.stderr) or f"git ls-tree exited {tree_proc.returncode}"
        raise RulesUnavailable(f"Failed to list rules/ at {sha}: {detail}")

    active: list[Rule] = []
    candidates: list[Rule] = []
    retired: list[Rule] = []
    rejected: list[Rejection] = []
    pending_blobs: list[tuple[str, str]] = []

    for record in tree_proc.stdout.split("\0"):
        if not record:
            continue
        meta_part, sep, rel_path = record.partition("\t")
        if not sep:
            continue
        parts = meta_part.split()
        if len(parts) != 4:
            rejected.append(Rejection(rel_path, f"malformed ls-tree entry: {meta_part!r}"))
            continue
        mode, obj_type, oid, size_str = parts

        if rel_path == "rules/.gitkeep" and mode == "100644" and obj_type == "blob":
            continue
        if mode != "100644" or obj_type != "blob":
            rejected.append(
                Rejection(rel_path, f"disallowed git object mode={mode} type={obj_type}")
            )
            continue
        if not _RULE_TREE_PATH_RE.fullmatch(rel_path):
            rejected.append(
                Rejection(
                    rel_path,
                    "invalid rule path (must match ^rules/[a-z0-9][a-z0-9-]*\\.yaml$)",
                )
            )
            continue
        if not _GIT_SHA_RE.fullmatch(oid):
            rejected.append(Rejection(rel_path, f"invalid git blob oid {oid!r}"))
            continue
        try:
            size = int(size_str)
        except ValueError:
            rejected.append(Rejection(rel_path, f"invalid blob size {size_str!r}"))
            continue
        if size > MAX_RULE_BYTES:
            rejected.append(
                Rejection(rel_path, f"file exceeds {MAX_RULE_BYTES} byte limit ({size} bytes)")
            )
            continue
        pending_blobs.append((rel_path, oid))

    for rel_path, blob_bytes, batch_err in _batch_read_blobs(resolved_repo, pending_blobs):
        if batch_err is not None or blob_bytes is None:
            rejected.append(Rejection(rel_path, batch_err or "git_cat_file_error: missing blob"))
            continue
        if len(blob_bytes) > MAX_RULE_BYTES:
            rejected.append(Rejection(rel_path, f"file exceeds {MAX_RULE_BYTES} byte limit"))
            continue
        try:
            text = blob_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            rejected.append(Rejection(rel_path, f"utf8_decode: {exc}"))
            continue

        rule, rejection = _parse_and_validate_rule(
            text,
            stem=Path(rel_path).stem,
            display_path=rel_path,
        )
        if rejection is not None:
            rejected.append(rejection)
        elif rule is not None:
            _partition_rule(rule, active, candidates, retired)

    return LoadResult(
        sha=sha,
        fetched=fetched,
        fetch_error=fetch_error,
        active=tuple(active),
        candidates=tuple(candidates),
        retired=tuple(retired),
        rejected=tuple(rejected),
    )


def lint_directory(dir_path: Path | os.PathLike[str]) -> LoadResult:
    """Validate rule YAML files in a local directory using lstat/O_NOFOLLOW and 8 KB cap."""
    target = Path(dir_path)
    try:
        st = os.lstat(target)
    except FileNotFoundError as exc:
        raise RulesUnavailable(f"Rules directory not found: {target}") from exc
    except OSError as exc:
        raise RulesUnavailable(f"Cannot inspect rules directory {target}: {exc}") from exc

    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise RulesUnavailable(f"Rules path must be a non-symlink directory: {target}")

    active: list[Rule] = []
    candidates: list[Rule] = []
    retired: list[Rule] = []
    rejected: list[Rejection] = []

    with os.scandir(target) as it:
        entries = sorted(it, key=lambda entry: entry.name)

    for entry in entries:
        name = entry.name
        if entry.is_symlink():
            rejected.append(Rejection(name, "symlinks are not allowed"))
            continue
        if not entry.is_file(follow_symlinks=False):
            rejected.append(
                Rejection(name, "expected regular file (directories/specials disallowed)")
            )
            continue
        if name in {".gitkeep", ".DS_Store"}:
            continue
        if not _RULE_FILENAME_RE.fullmatch(name):
            rejected.append(
                Rejection(name, "invalid rule filename (must match ^[a-z0-9][a-z0-9-]*\\.yaml$)")
            )
            continue

        try:
            raw = read_regular_nofollow(
                Path(entry.path),
                max_bytes=MAX_RULE_BYTES,
                require_private=False,
            )
            text = raw.decode("utf-8")
        except (OSError, ValueError, StoreInsecure, UnicodeDecodeError) as exc:
            rejected.append(Rejection(name, str(exc)))
            continue

        rule, rejection = _parse_and_validate_rule(
            text,
            stem=Path(name).stem,
            display_path=name,
        )
        if rejection is not None:
            rejected.append(rejection)
        elif rule is not None:
            _partition_rule(rule, active, candidates, retired)

    return LoadResult(
        sha="",
        fetched=False,
        fetch_error=None,
        active=tuple(active),
        candidates=tuple(candidates),
        retired=tuple(retired),
        rejected=tuple(rejected),
    )
