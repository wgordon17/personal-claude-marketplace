"""Hardened argv-only subprocess runner, GET-only gh wrapper, git wrapper, and ReplayProcs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import config
from .errors import CommandDenied, UnsafeArg

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]

ALLOWED_BINARIES: frozenset[str] = frozenset(
    {
        "gh",
        "git",
        "brew",
        "mise",
        "uv",
        "security",
        "osascript",
        "terminal-notifier",
        "launchctl",
        "sqlite3",
        "osv-scanner",
        "defaults",
        "mas",
        "cargo",
        "npm",
        "bun",
        "go",
        "sw_vers",
        "xcode-select",
        "pkgutil",
        "claude",
    }
)

_SYSTEM_DIRS: tuple[str, ...] = ("/opt/homebrew/bin", "/usr/bin", "/bin", "/usr/sbin")
_USER_BINARIES: frozenset[str] = frozenset({"claude", "bun", "cargo", "go"})
_USER_DIRS: tuple[str, ...] = ("~/.local/bin", "~/.cargo/bin", "~/.bun/bin", "~/go/bin")

SAFE_ARG_RE = re.compile(r"^[A-Za-z0-9@._/:+=-]{1,214}$")

GH_ENDPOINT_RE = re.compile(
    r"^(repos/[^/]+/[^/]+(/(releases|contents|commits|readme|discussions|tags|forks|contributors|compare)(/[^?\s]*)?|/issues(/\d+/comments)?)?|search/(repositories|code|issues)|users/[^/]+/starred|user/(starred|subscriptions)|advisories|graphql)(\?[A-Za-z0-9_=&%.,:+*-]*)?$"
)

_GUI_UID_RE = re.compile(r"\bgui/\d+")
_GQL_TRIPLE_STR_RE = re.compile(r'"""[\s\S]*?"""')
_GQL_STR_RE = re.compile(r'"(?:\\.|[^"\\])*"')
_GQL_COMMENT_RE = re.compile(r"#[^\n]*")
_GQL_FORBIDDEN_OP_RE = re.compile(r"\b(mutation|subscription)\b", re.IGNORECASE)

_ALLOWED_FIXED_FLAGS: frozenset[str] = frozenset(
    {
        "--",
        "-X",
        "GET",
        "POST",
        "--paginate",
        "-f",
        "-F",
        "-H",
        "--header",
        "-q",
        "--jq",
        "-c",
        "-C",
        "--no-textconv",
        "--no-ext-diff",
        "--global",
        "--list",
        "--name-only",
        "--porcelain",
        "--is-inside-work-tree",
        "--git-common-dir",
        "--show-toplevel",
        "--verify",
        "-r",
        "-n",
        "-1",
        "--json",
        "--json=v2",
        "--installed",
        "--show-paths",
        "--depth=0",
        "-g",
        "-m",
        "-p",
        "-s",
        "-a",
        "-w",
        "-U",
        "-e",
        "-title",
        "-subtitle",
        "-message",
        "-group",
        "-readonly",
        "--frozen",
        "--output-format",
        "--script",
        "--locked",
        "--isolated",
        "--quiet",
        "--subs",
        "--t",
        "--limit",
        "--version",
        "--no-lockfile",
        "--format=json",
    }
)

_ALLOWED_OPTION_PREFIXES: tuple[str, ...] = (
    "-S",
    "--since=",
    "--diff-filter=",
    "--format=",
    "--pretty=",
    "--pkg-info=",
    "--lockfile=",
)

_ACTIVE_REPLAY_STACK: list[ReplayProcs] = []


@dataclass(frozen=True)
class ProcResult:
    """Completed subprocess result."""

    returncode: int
    stdout: str
    stderr: str
    argv: tuple[str, ...]


class ProcsTransport(Protocol):
    """Pluggable subprocess execution interface."""

    def execute(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = 60.0,
    ) -> ProcResult:
        """Execute or replay an argv command."""
        ...


def is_replay_mode() -> bool:
    """Return True if a ReplayProcs is active or TECH_RADAR_REPLAY_DIR is set."""
    return bool(_ACTIVE_REPLAY_STACK or os.environ.get("TECH_RADAR_REPLAY_DIR"))


def _resolve_live(name: str) -> str | None:
    if name not in ALLOWED_BINARIES:
        return None
    search_dirs = list(_SYSTEM_DIRS)
    if name in _USER_BINARIES:
        search_dirs.extend(_USER_DIRS)
    for raw_dir in search_dirs:
        candidate = Path(raw_dir).expanduser() / name
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        except OSError:
            continue
    return None


def resolve(name: str, *, force_live: bool = False) -> str | None:
    """Resolve an allowlisted binary name to its absolute path.

    In replay/test mode (Spec 11 §1, Spec 12 A30), returns `/opt/homebrew/bin/<name>`
    for every allowlisted binary without searching the filesystem.
    """
    if name not in ALLOWED_BINARIES:
        return None
    if not force_live and is_replay_mode():
        return f"/opt/homebrew/bin/{name}"
    return _resolve_live(name)


def is_safe_arg(arg: str) -> bool:
    """Validate that a standalone data argument matches SAFE_ARG_RE and is not an option or URL."""
    if not arg or arg.startswith("-"):
        return False
    if "://" in arg or ".." in arg.split("/"):
        return False
    return bool(SAFE_ARG_RE.match(arg))


_GIT_FORMAT_RE = re.compile(r"^[A-Za-z0-9@._/:+=%\x00\x1f\n -]{1,120}$")


def validate_arg(
    arg: str,
    *,
    allowed_flags: Collection[str] = (),
    allowed_prefixes: Collection[str] = (),
) -> str:
    """Validate an argument as either an allowed flag, an allowed prefix+safe_arg, or a safe_arg."""
    if arg in _ALLOWED_FIXED_FLAGS or arg in allowed_flags:
        return arg
    for prefix in (*_ALLOWED_OPTION_PREFIXES, *allowed_prefixes):
        if arg.startswith(prefix):
            remainder = arg[len(prefix) :]
            if prefix in ("--format=", "--pretty="):
                if remainder and "://" not in remainder and _GIT_FORMAT_RE.match(remainder):
                    return arg
                raise UnsafeArg(f"unsafe format argument: {arg!r}")
            if remainder and is_safe_arg(remainder):
                return arg
            raise UnsafeArg(f"unsafe value in prefixed argument: {arg!r}")
    if arg.startswith("-"):
        raise UnsafeArg(f"disallowed option argument: {arg!r}")
    if not is_safe_arg(arg):
        raise UnsafeArg(f"unsafe argument: {arg!r}")
    return arg


def _validate_executable(prog: str) -> tuple[str, str]:
    base = os.path.basename(prog)
    if base not in ALLOWED_BINARIES:
        raise CommandDenied(f"binary {prog!r} is not in ALLOWED_BINARIES")
    if os.path.isabs(prog):
        parent = str(Path(prog).parent)
        allowed_dirs = {str(Path(d).expanduser()) for d in (*_SYSTEM_DIRS, *_USER_DIRS)}
        allowed_dirs.update(_SYSTEM_DIRS)
        if parent not in allowed_dirs:
            raise CommandDenied(f"binary directory {parent!r} is not allowlisted")
        return prog, base
    resolved = resolve(base)
    if resolved is None:
        raise CommandDenied(f"binary {base!r} could not be resolved in allowlisted directories")
    return resolved, base


def normalize_str(value: str) -> str:
    """Normalize dynamic test paths and launchctl UID domains for cross-OS replay keys."""
    p = config.paths(ensure_dirs=False)
    out = value
    replacements: list[tuple[str, str]] = []
    for candidate_path, token in (
        (p.run_root, "{RUN}"),
        (p.state, "{STATE}"),
        (p.sync, "{SYNC}"),
        (_SCRIPTS_DIR, "{S}"),
        (p.home, "{HOME}"),
    ):
        raw_s = str(candidate_path)
        if raw_s:
            replacements.append((raw_s, token))
        try:
            res_s = str(candidate_path.resolve())
            if res_s and res_s != raw_s:
                replacements.append((res_s, token))
        except OSError:
            pass

    replacements.sort(key=lambda item: len(item[0]), reverse=True)
    for src, tok in replacements:
        if src in out:
            out = out.replace(src, tok)

    return _GUI_UID_RE.sub("gui/{UID}", out)


def normalize_argv(argv: Sequence[str]) -> list[str]:
    """Normalize argv for ReplayProcs: basename argv[0] and replace path/UID tokens."""
    if not argv:
        return []
    out = [os.path.basename(argv[0])]
    for elem in argv[1:]:
        out.append(normalize_str(str(elem)))
    return out


def procs_key(cwd: Path | str | None, argv: Sequence[str]) -> str:
    """Compute the deterministic Spec 11 §1 ReplayProcs SHA-1 key."""
    norm_cwd = normalize_str(str(cwd)) if cwd is not None else None
    norm_argv = normalize_argv(argv)
    payload = json.dumps([norm_cwd, norm_argv], separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


class LiveProcs:
    """Live subprocess execution transport."""

    def execute(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = 60.0,
    ) -> ProcResult:
        res = subprocess.run(
            list(argv),
            cwd=str(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
        return ProcResult(
            returncode=int(res.returncode),
            stdout=res.stdout,
            stderr=res.stderr,
            argv=tuple(argv),
        )


@dataclass
class ReplayProcs:
    """Offline replay subprocess transport with optional real-git passthrough (Spec 12 A17)."""

    roots: tuple[Path, ...]
    passthrough: frozenset[str] = field(default_factory=lambda: frozenset({"git"}))
    calls: list[tuple[list[str], frozenset[str]]] = field(default_factory=list)

    def __init__(
        self,
        roots: Sequence[Path | str] = (),
        *,
        passthrough: Collection[str] = ("git",),
    ) -> None:
        self.roots = tuple(Path(r) for r in roots)
        self.passthrough = frozenset(passthrough)
        self.calls = []

    @contextmanager
    def activate(self) -> Iterator[ReplayProcs]:
        """Activate this ReplayProcs instance so procs.resolve/run/gh/git use it by default."""
        _ACTIVE_REPLAY_STACK.append(self)
        try:
            yield self
        finally:
            if _ACTIVE_REPLAY_STACK and _ACTIVE_REPLAY_STACK[-1] is self:
                _ACTIVE_REPLAY_STACK.pop()
            elif self in _ACTIVE_REPLAY_STACK:
                _ACTIVE_REPLAY_STACK.remove(self)

    def _find_fixture(
        self,
        key: str,
        norm_cwd: str | None,
        norm_argv: list[str],
    ) -> dict[str, Any] | None:
        target_name = f"{key}.json"
        for root in self.roots:
            if not root.exists():
                continue
            direct = root / target_name
            if direct.is_file():
                return json.loads(direct.read_text(encoding="utf-8"))
            for cand in sorted(root.rglob(target_name)):
                if cand.is_file():
                    return json.loads(cand.read_text(encoding="utf-8"))

        for root in self.roots:
            if not root.exists():
                continue
            for meta_path in sorted(root.rglob("*.json")):
                try:
                    data = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(data, dict) or "argv" not in data:
                    continue
                if data.get("argv") == norm_argv:
                    if "cwd" in data and data["cwd"] != norm_cwd:
                        continue
                    return data
        return None

    def execute(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float = 60.0,
    ) -> ProcResult:
        norm_argv = normalize_argv(argv)
        norm_cwd = normalize_str(str(cwd)) if cwd is not None else None
        env_keys = frozenset(env.keys()) if env is not None else frozenset()
        self.calls.append((norm_argv, env_keys))

        key = procs_key(cwd, argv)
        fixture = self._find_fixture(key, norm_cwd, norm_argv)
        if fixture is not None:
            stdout_val = fixture.get("stdout", "")
            if not isinstance(stdout_val, str):
                stdout_val = json.dumps(stdout_val)
            return ProcResult(
                returncode=int(fixture.get("returncode", 0)),
                stdout=stdout_val,
                stderr=str(fixture.get("stderr", "")),
                argv=tuple(argv),
            )

        base = os.path.basename(argv[0]) if argv else ""
        if base in self.passthrough:
            real_bin = _resolve_live(base)
            if real_bin is None:
                raise CommandDenied(f"passthrough binary {base!r} not found on system")
            real_argv = [real_bin, *argv[1:]]
            res = LiveProcs().execute(real_argv, cwd=cwd, env=env, timeout=timeout)
            return ProcResult(
                returncode=res.returncode,
                stdout=res.stdout,
                stderr=res.stderr,
                argv=tuple(argv),
            )

        raise CommandDenied(f"missing procs replay fixture {key} for {norm_argv!r}")


def _default_transport(transport: ProcsTransport | None) -> ProcsTransport:
    if transport is not None:
        return transport
    if _ACTIVE_REPLAY_STACK:
        return _ACTIVE_REPLAY_STACK[-1]
    raw_replay = os.environ.get("TECH_RADAR_REPLAY_DIR")
    if raw_replay:
        roots = [Path(p) for p in raw_replay.split(os.pathsep) if p]
        return ReplayProcs(roots)
    return LiveProcs()


def run(
    argv: Sequence[str],
    *,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = 60.0,
    transport: ProcsTransport | None = None,
    free_text_indices: Collection[int] = (),
    allowed_flags: Collection[str] = (),
    allowed_prefixes: Collection[str] = (),
) -> ProcResult:
    """Validate and execute an argv command."""
    if not argv:
        raise CommandDenied("empty argv")

    resolved_bin, _base = _validate_executable(str(argv[0]))
    validated_argv: list[str] = [resolved_bin]
    free_idx = set(free_text_indices)

    for idx, raw_arg in enumerate(argv[1:], start=1):
        arg = str(raw_arg)
        if idx in free_idx:
            validated_argv.append(arg)
        else:
            validated_argv.append(
                validate_arg(
                    arg,
                    allowed_flags=allowed_flags,
                    allowed_prefixes=allowed_prefixes,
                )
            )

    active = _default_transport(transport)
    return active.execute(validated_argv, cwd=cwd, env=env, timeout=timeout)


def _validate_graphql_query(query_text: str) -> None:
    stripped = _GQL_TRIPLE_STR_RE.sub('""', query_text)
    stripped = _GQL_STR_RE.sub('""', stripped)
    stripped = _GQL_COMMENT_RE.sub("", stripped).strip()
    if not stripped:
        raise CommandDenied("empty GraphQL query")
    if _GQL_FORBIDDEN_OP_RE.search(stripped):
        raise CommandDenied("GraphQL mutations and subscriptions are denied")
    low = stripped.lower()
    if not (low.startswith("query") or low.startswith("{")):
        raise CommandDenied("GraphQL operation must start with 'query' or '{'")


def gh(
    args: Sequence[str],
    *,
    transport: ProcsTransport | None = None,
    timeout: float = 60.0,
) -> ProcResult:
    """Execute a read-only `gh api` request enforced by the Spec 12 A33 endpoint allowlist."""
    if not args or args[0] != "api":
        raise CommandDenied("gh wrapper only permits 'gh api'")

    gh_bin = resolve("gh")
    if gh_bin is None:
        raise CommandDenied("gh binary not found in allowlisted directories")

    method = "GET"
    explicit_method = False
    endpoint: str | None = None
    fields: list[str] = []
    validated_rest: list[str] = ["api"]

    i = 1
    n = len(args)
    while i < n:
        tok = str(args[i])
        if tok == "--paginate":
            validated_rest.append(tok)
            i += 1
            continue
        if tok == "-X":
            if i + 1 >= n:
                raise CommandDenied("missing argument after -X")
            method = str(args[i + 1]).upper()
            explicit_method = True
            if method not in ("GET", "POST"):
                raise CommandDenied(f"disallowed gh api method: {method}")
            validated_rest.extend(["-X", method])
            i += 2
            continue
        if tok in ("-f", "-F"):
            if i + 1 >= n:
                raise CommandDenied(f"missing argument after {tok}")
            field_val = str(args[i + 1])
            if "=" not in field_val:
                raise UnsafeArg(f"malformed gh field {field_val!r}")
            fields.append(field_val)
            validated_rest.extend([tok, field_val])
            i += 2
            continue
        if tok in ("-H", "--header", "-q", "--jq"):
            if i + 1 >= n:
                raise CommandDenied(f"missing argument after {tok}")
            validated_rest.extend([tok, str(args[i + 1])])
            i += 2
            continue
        if tok.startswith("-"):
            if "://" in tok or "=" in tok:
                raise UnsafeArg(f"unsafe gh option {tok!r}")
            raise CommandDenied(f"disallowed gh option {tok!r}")

        if endpoint is not None:
            raise CommandDenied(f"multiple gh api endpoints provided: {endpoint!r} and {tok!r}")
        endpoint = tok.lstrip("/")
        validated_rest.append(tok)
        i += 1

    if endpoint is None:
        raise CommandDenied("missing gh api endpoint")

    if ".." in endpoint.split("/"):
        raise CommandDenied(f"path traversal in gh api endpoint: {endpoint!r}")

    if not GH_ENDPOINT_RE.match(endpoint):
        raise CommandDenied(f"gh api endpoint not allowlisted: {endpoint!r}")

    if endpoint.startswith("graphql"):
        queries: list[str] = []
        for fval in fields:
            k, v = fval.split("=", 1)
            if k == "query":
                queries.append(v)
            elif k == "variables":
                try:
                    parsed_vars = json.loads(v)
                    if not isinstance(parsed_vars, dict):
                        raise CommandDenied("GraphQL variables must be a JSON object")
                except json.JSONDecodeError as exc:
                    raise CommandDenied("invalid GraphQL variables JSON") from exc
        if not queries:
            raise CommandDenied("gh api graphql requires a query field")
        for q in queries:
            _validate_graphql_query(q)
    else:
        if explicit_method and method != "GET":
            raise CommandDenied("gh api non-graphql endpoints only allow GET")
        if method != "GET":
            raise CommandDenied("gh api non-graphql endpoints only allow GET")

    active = _default_transport(transport)
    return active.execute([gh_bin, *validated_rest], timeout=timeout)


def git(
    args: Sequence[str],
    repo: Path | str | None = None,
    *,
    transport: ProcsTransport | None = None,
    timeout: float = 60.0,
) -> ProcResult:
    """Execute a hardened read-only git command with disabled hooks/fsmonitor/ext-diff."""
    if not args:
        raise CommandDenied("empty git args")

    git_bin = resolve("git")
    if git_bin is None:
        raise CommandDenied("git binary not found in allowlisted directories")

    base_flags = [
        git_bin,
        "-c",
        "core.fsmonitor=",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "protocol.file.allow=never",
        "-c",
        "diff.external=",
    ]

    subcmd = str(args[0])
    rest = [str(a) for a in args[1:]]

    if subcmd == "config":
        if repo is not None or rest != ["--global", "--list"]:
            raise CommandDenied("only 'git config --global --list' (repo=None) is allowlisted")
        full_argv = [*base_flags, "config", "--global", "--list"]
    else:
        if repo is None:
            raise CommandDenied(f"git {subcmd!r} requires a repo path")
        repo_str = str(repo)
        if not is_safe_arg(repo_str):
            raise UnsafeArg(f"unsafe repo path: {repo_str!r}")

        if subcmd in ("log", "show"):
            for arg in rest:
                validate_arg(arg)
            deduped_rest = [a for a in rest if a not in ("--no-textconv", "--no-ext-diff")]
            full_argv = [
                *base_flags,
                "-C",
                repo_str,
                subcmd,
                "--no-textconv",
                "--no-ext-diff",
                *deduped_rest,
            ]
        elif subcmd == "remote":
            if len(rest) != 2 or rest[0] != "get-url" or not is_safe_arg(rest[1]):
                raise CommandDenied("only 'git remote get-url <name>' is allowlisted")
            full_argv = [*base_flags, "-C", repo_str, "remote", "get-url", rest[1]]
        elif subcmd == "rev-parse":
            for arg in rest:
                validate_arg(arg)
            full_argv = [*base_flags, "-C", repo_str, "rev-parse", *rest]
        elif subcmd == "worktree":
            if not rest or rest[0] != "list":
                raise CommandDenied("only 'git worktree list' is allowlisted")
            for arg in rest[1:]:
                validate_arg(arg)
            full_argv = [*base_flags, "-C", repo_str, "worktree", *rest]
        elif subcmd == "ls-tree":
            if len(rest) < 4 or rest[0] != "-r" or "--" not in rest:
                raise CommandDenied("only 'git ls-tree -r <sha> -- <path>' is allowlisted")
            for arg in rest:
                validate_arg(arg)
            full_argv = [*base_flags, "-C", repo_str, "ls-tree", *rest]
        else:
            raise CommandDenied(f"git subcommand {subcmd!r} is not allowlisted")

    git_env = dict(os.environ)
    git_env["GIT_OPTIONAL_LOCKS"] = "0"
    git_env["GIT_TERMINAL_PROMPT"] = "0"
    git_env["GIT_CONFIG_NOSYSTEM"] = "1"

    active = _default_transport(transport)
    return active.execute(full_argv, env=git_env, timeout=timeout)
