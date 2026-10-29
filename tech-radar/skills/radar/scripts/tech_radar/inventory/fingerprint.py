"""Usage fingerprinting: CLI subcommands/flags (A12), Python AST, JS/TS, config keys, and intent."""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import tomllib
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .. import fsguard, redact, sanitize

MAX_SOURCE_FILE_BYTES = 512 * 1024
INTENT_README_BYTES = 2048
INTENT_MAX_CHARS = 80

_FLAG_RE = re.compile(r"^--?[a-z][a-z0-9-]*$")
_SUBCOMMAND_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_MODEL_LITERAL_RE = re.compile(r"^(?:claude|gpt|gemini|deepseek)-[A-Za-z0-9._-]+$")
_JS_IMPORT_RE = re.compile(
    r"""(?:import\s+(?:[^'"]+?\s+from\s+)?|require\s*\(\s*)['"]([^'"]+)['"]"""
)
_SHELL_SPLIT_RE = re.compile(r"(?:&&|\|\||;|\||\n)+")
_MAKEFILE_TARGET_RE = re.compile(r"^[A-Za-z0-9_.-]+\s*:")
_HEADING_LINE_RE = re.compile(r"^#{1,4}\s+(.+?)\s*$")
_UNCHECKED_TODO_RE = re.compile(r"^\s*[-*+]\s+\[\s\]\s+(.+?)\s*$")

DEFAULT_KNOWN_CLIS: frozenset[str] = frozenset(
    {
        "brew",
        "bun",
        "cargo",
        "claude",
        "docker",
        "fd",
        "fzf",
        "gh",
        "git",
        "go",
        "jq",
        "just",
        "kubectl",
        "lazygit",
        "make",
        "mise",
        "npm",
        "npx",
        "osv-scanner",
        "pnpm",
        "prek",
        "pytest",
        "rg",
        "rtk",
        "ruff",
        "terraform",
        "uv",
        "uvx",
        "yarn",
    }
)


@dataclass(frozen=True)
class FingerprintRecord:
    """Single usage fingerprint observation."""

    context: str
    kind: str
    token: str
    count: int = 1


def merge_fingerprints(records: Collection[FingerprintRecord]) -> list[FingerprintRecord]:
    """Combine duplicate (context, kind, token) records by summing their counts."""
    totals: dict[tuple[str, str, str], int] = {}
    for rec in records:
        if not rec.token:
            continue
        key = (rec.context, rec.kind, rec.token)
        totals[key] = totals.get(key, 0) + max(1, rec.count)
    return [
        FingerprintRecord(context=ctx, kind=kind, token=tok, count=cnt)
        for (ctx, kind, tok), cnt in sorted(totals.items())
    ]


def _strip_leading_wrappers(tokens: list[str]) -> list[str]:
    idx = 0
    n = len(tokens)
    while idx < n:
        tok = tokens[idx]
        if _ENV_ASSIGN_RE.match(tok):
            idx += 1
            continue
        base = os.path.basename(tok)
        if base in ("sudo", "env", "command", "nohup", "time", "exec"):
            idx += 1
            while idx < n and tokens[idx].startswith("-"):
                idx += 1
            continue
        break
    return tokens[idx:]


def extract_command_name(segment: str) -> str | None:
    """Extract only the command basename from a single shell segment (never arguments)."""
    stripped = segment.strip().lstrip("@-+")
    if not stripped or stripped.startswith("#"):
        return None
    try:
        raw_tokens = shlex.split(stripped, comments=True)
    except ValueError:
        raw_tokens = stripped.split()
    tokens = _strip_leading_wrappers(raw_tokens)
    if not tokens:
        return None
    first = os.path.basename(tokens[0]).strip()
    if not first or not re.match(r"^[A-Za-z0-9_.-]+$", first):
        return None
    return first


def extract_cli_tokens_from_argv(
    argv: Collection[str],
    context: str,
    known_clis: Collection[str] = DEFAULT_KNOWN_CLIS,
    *,
    emit_cmd: bool = True,
) -> list[FingerprintRecord]:
    """Extract cmd, subcommand, and flag tokens (never flag values) from an argv sequence (A12)."""
    tokens = _strip_leading_wrappers([str(x) for x in argv if str(x).strip()])
    if not tokens:
        return []

    cmd = os.path.basename(tokens[0])
    if not re.match(r"^[A-Za-z0-9_.-]+$", cmd):
        return []

    out: list[FingerprintRecord] = []
    if emit_cmd:
        out.append(FingerprintRecord(context=context, kind="cmd", token=cmd, count=1))

    known_set = set(known_clis) | DEFAULT_KNOWN_CLIS
    if cmd not in known_set:
        return out

    rest = tokens[1:]
    if rest:
        second = rest[0]
        if not second.startswith("-") and _SUBCOMMAND_RE.match(second):
            out.append(
                FingerprintRecord(
                    context=context,
                    kind="subcommand",
                    token=f"{cmd} {second}",
                    count=1,
                )
            )

    for tok in rest:
        if tok == "--":
            break
        if not tok.startswith("-"):
            continue
        flag_part = tok.split("=", 1)[0]
        if _FLAG_RE.match(flag_part):
            out.append(FingerprintRecord(context=context, kind="flag", token=flag_part, count=1))

    return out


def extract_cli_fingerprints(
    script_text: str,
    context: str,
    known_clis: Collection[str] = DEFAULT_KNOWN_CLIS,
    *,
    emit_cmd: bool = True,
) -> list[FingerprintRecord]:
    """Extract CLI cmd, subcommand, and flag tokens from non-history script/recipe text (A12)."""
    out: list[FingerprintRecord] = []
    for raw_line in script_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if _MAKEFILE_TARGET_RE.match(line) and not raw_line.startswith(("\t", "  ")):
            continue
        line = line.lstrip("@-+")
        if line.startswith(
            ("function ", "end", "if ", "else", "for ", "while ", "switch ", "case ")
        ):
            if line.startswith("function "):
                parts = line.split(";", 1)
                line = parts[1].strip() if len(parts) > 1 else ""
            else:
                continue
        if not line:
            continue

        for segment in _SHELL_SPLIT_RE.split(line):
            seg = segment.strip()
            if not seg or seg.startswith("#"):
                continue
            try:
                argv = shlex.split(seg, comments=True)
            except ValueError:
                argv = seg.split()
            if not argv:
                continue
            out.extend(
                extract_cli_tokens_from_argv(
                    argv,
                    context,
                    known_clis,
                    emit_cmd=emit_cmd,
                )
            )
    return merge_fingerprints(out)


def extract_python_ast_fingerprints(
    py_path: Path,
    context: str,
) -> tuple[list[FingerprintRecord], int]:
    """Parse a Python file via ast to extract imports, imported module API calls, and model IDs."""
    try:
        st = py_path.stat()
    except OSError:
        return [], 0
    if fsguard.is_dataless(st):
        return [], 1
    if st.st_size > MAX_SOURCE_FILE_BYTES:
        return [], 0
    try:
        source = py_path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source, filename=str(py_path))
    except (OSError, SyntaxError, ValueError):
        return [], 0

    out: list[FingerprintRecord] = []
    imported_aliases: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_mod = alias.name.split(".")[0]
                if top_mod:
                    out.append(FingerprintRecord(context=context, kind="import", token=top_mod))
                    bound = alias.asname or top_mod
                    imported_aliases[bound] = top_mod
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            top_mod = node.module.split(".")[0]
            if top_mod:
                out.append(FingerprintRecord(context=context, kind="import", token=top_mod))
            for alias in node.names:
                if alias.name and alias.name != "*":
                    out.append(
                        FingerprintRecord(
                            context=context,
                            kind="import",
                            token=f"{node.module}.{alias.name}",
                        )
                    )
                    bound = alias.asname or alias.name
                    imported_aliases[bound] = f"{node.module}.{alias.name}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            val = node.func.value
            if isinstance(val, ast.Name) and val.id in imported_aliases:
                target_mod = imported_aliases[val.id]
                out.append(
                    FingerprintRecord(
                        context=context,
                        kind="api",
                        token=f"{target_mod}.{node.func.attr}",
                    )
                )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            s_val = node.value.strip()
            if 6 <= len(s_val) <= 80 and _MODEL_LITERAL_RE.match(s_val):
                out.append(
                    FingerprintRecord(
                        context=context,
                        kind="model_id",
                        token=s_val,
                    )
                )

    return merge_fingerprints(out), 0


def extract_jsts_fingerprints(
    js_path: Path,
    context: str,
) -> tuple[list[FingerprintRecord], int]:
    """Extract import/require module tokens and model literals from a JS/TS file."""
    try:
        st = js_path.stat()
    except OSError:
        return [], 0
    if fsguard.is_dataless(st):
        return [], 1
    if st.st_size > MAX_SOURCE_FILE_BYTES:
        return [], 0
    try:
        text = js_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return [], 0

    out: list[FingerprintRecord] = []
    for match in _JS_IMPORT_RE.finditer(text):
        spec = match.group(1).strip()
        if not spec or spec.startswith(".") or spec.startswith("/"):
            continue
        if spec.startswith("@"):
            parts = spec.split("/")
            pkg = "/".join(parts[:2]) if len(parts) >= 2 else spec
        else:
            pkg = spec.split("/")[0]
        if pkg:
            out.append(FingerprintRecord(context=context, kind="import", token=pkg))
    return merge_fingerprints(out), 0


def extract_pyproject_config_keys(
    pyproject_data: dict[str, Any],
    context: str,
) -> list[FingerprintRecord]:
    """Extract [tool.<name>] sections and their top-level keys from pyproject.toml."""
    out: list[FingerprintRecord] = []
    tool_tbl = pyproject_data.get("tool")
    if not isinstance(tool_tbl, dict):
        return out
    for tool_name, tool_cfg in tool_tbl.items():
        out.append(
            FingerprintRecord(
                context=context,
                kind="config_key",
                token=f"tool.{tool_name}",
            )
        )
        if isinstance(tool_cfg, dict):
            for k in tool_cfg:
                out.append(
                    FingerprintRecord(
                        context=context,
                        kind="config_key",
                        token=f"tool.{tool_name}.{k}",
                    )
                )
    return merge_fingerprints(out)


def extract_config_file_fingerprints(
    path: Path,
    context: str,
) -> tuple[list[FingerprintRecord], int]:
    """Extract config_key tokens from ruff.toml, tsconfig.json, .pre-commit-config.yaml, GHA."""
    try:
        st = path.stat()
    except OSError:
        return [], 0
    if fsguard.is_dataless(st):
        return [], 1

    name = path.name
    out: list[FingerprintRecord] = []
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return [], 0

    if name in ("ruff.toml", ".ruff.toml"):
        try:
            data = tomllib.loads(raw)
            if isinstance(data, dict):
                for k in data:
                    out.append(
                        FingerprintRecord(context=context, kind="config_key", token=f"ruff.{k}")
                    )
        except tomllib.TOMLDecodeError:
            pass
    elif name == "tsconfig.json":
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and isinstance(data.get("compilerOptions"), dict):
                for k in data["compilerOptions"]:
                    out.append(
                        FingerprintRecord(
                            context=context,
                            kind="config_key",
                            token=f"tsconfig.{k}",
                        )
                    )
        except json.JSONDecodeError:
            pass
    elif name in (".pre-commit-config.yaml", ".pre-commit-config.yml"):
        try:
            data = yaml.safe_load(raw)
            if isinstance(data, dict) and isinstance(data.get("repos"), list):
                for repo_entry in data["repos"]:
                    if not isinstance(repo_entry, dict):
                        continue
                    hooks = repo_entry.get("hooks")
                    if isinstance(hooks, list):
                        for h in hooks:
                            if isinstance(h, dict) and h.get("id"):
                                out.append(
                                    FingerprintRecord(
                                        context=context,
                                        kind="config_key",
                                        token=f"pre-commit.{h['id']}",
                                    )
                                )
        except yaml.YAMLError:
            pass

    return merge_fingerprints(out), 0


def normalize_intent_phrase(raw: str) -> str:
    """Clean, scrub, and normalize an intent heading or TODO item to <= 80 chars."""
    cleaned = sanitize.clean(raw, limit=INTENT_MAX_CHARS)
    scrubbed = redact.scrub(cleaned).strip()
    if len(scrubbed) > INTENT_MAX_CHARS:
        scrubbed = scrubbed[: INTENT_MAX_CHARS - 1].rstrip() + "…"
    return scrubbed


def extract_project_intent_fingerprints(
    project_dir: Path,
    context: str,
) -> tuple[list[FingerprintRecord], int]:
    """Extract intent signals from README.md, CLAUDE.md, and hack/{PROJECT,TODO,LESSONS,BUGS}.md."""
    out: list[FingerprintRecord] = []
    dataless_count = 0

    readme = project_dir / "README.md"
    if readme.is_file():
        try:
            st = readme.stat()
            if fsguard.is_dataless(st):
                dataless_count += 1
            else:
                raw_bytes = readme.read_bytes()[:INTENT_README_BYTES]
                text = raw_bytes.decode("utf-8", errors="ignore")
                for line in text.splitlines():
                    m = _HEADING_LINE_RE.match(line)
                    if m:
                        phrase = normalize_intent_phrase(m.group(1))
                        if phrase:
                            out.append(
                                FingerprintRecord(
                                    context=context,
                                    kind="api",
                                    token=f"intent:{phrase}",
                                )
                            )
        except OSError:
            pass

    for rel in ("CLAUDE.md", "hack/PROJECT.md", "hack/LESSONS.md", "hack/BUGS.md"):
        doc_path = project_dir / rel
        if not doc_path.is_file():
            continue
        try:
            st = doc_path.stat()
            if fsguard.is_dataless(st):
                dataless_count += 1
                continue
            text = doc_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            m = _HEADING_LINE_RE.match(line)
            if m:
                phrase = normalize_intent_phrase(m.group(1))
                if phrase:
                    out.append(
                        FingerprintRecord(
                            context=context,
                            kind="api",
                            token=f"intent:{phrase}",
                        )
                    )

    todo_path = project_dir / "hack" / "TODO.md"
    if todo_path.is_file():
        try:
            st = todo_path.stat()
            if fsguard.is_dataless(st):
                dataless_count += 1
            else:
                text = todo_path.read_text(encoding="utf-8", errors="ignore")
                for line in text.splitlines():
                    hm = _HEADING_LINE_RE.match(line)
                    if hm:
                        phrase = normalize_intent_phrase(hm.group(1))
                        if phrase:
                            out.append(
                                FingerprintRecord(
                                    context=context,
                                    kind="api",
                                    token=f"intent:{phrase}",
                                )
                            )
                        continue
                    tm = _UNCHECKED_TODO_RE.match(line)
                    if tm:
                        phrase = normalize_intent_phrase(tm.group(1))
                        if phrase:
                            out.append(
                                FingerprintRecord(
                                    context=context,
                                    kind="api",
                                    token=f"intent:{phrase}",
                                )
                            )
        except OSError:
            pass

    return merge_fingerprints(out), dataless_count
