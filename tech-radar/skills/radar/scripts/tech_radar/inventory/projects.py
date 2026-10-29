"""Project discovery, worktree dedupe, manifest parsing (A27), visibility/alias (A8), and intent."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import tomllib
import urllib.parse
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from .. import fsguard, redact
from ..errors import RadarError
from . import fingerprint as fp
from .system import DiscoveredRoot, EntityRecord, UsageRecord

if TYPE_CHECKING:
    from ..context import Ctx

Usage = UsageRecord

PRUNED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "target",
        "dist",
        "build",
        "__pycache__",
        ".tox",
        ".mypy_cache",
        ".ruff_cache",
    }
)

NEVER_READ_SUFFIXES: tuple[str, ...] = (".pem", ".key", ".p12")

AI_SDK_EXACT: frozenset[str] = frozenset(
    {
        "anthropic",
        "openai",
        "litellm",
        "pydantic-ai",
        "langgraph",
        "google-genai",
        "claude-agent-sdk",
        "mcp",
        "@anthropic-ai/sdk",
        "@modelcontextprotocol/sdk",
    }
)

_PEP508_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_PEP508_VER_RE = re.compile(r"(?:==|~=|\^|>=|<=|>|<)\s*([0-9][A-Za-z0-9.*+-]*)")
_DOCKER_FROM_RE = re.compile(
    r"^\s*FROM\s+(?:--platform=\S+\s+)?([^\s#]+)(?:\s+AS\s+\S+)?",
    re.IGNORECASE,
)
_GO_REQUIRE_LINE_RE = re.compile(
    r"^\s*([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)\s+v([0-9][^\s/]*)(.*?)$"
)
_SHA40_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_SCP_GIT_RE = re.compile(r"^(?:[^@]+@)?([A-Za-z0-9.-]+):(.+)$")
_GITHUB_KEY_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)$")
_TF_SOURCE_RE = re.compile(r'source\s*=\s*"([^"]+)"')
_TF_VERSION_RE = re.compile(r'version\s*=\s*"([^"]+)"')

MANIFEST_BASENAMES: frozenset[str] = frozenset(
    {
        "pyproject.toml",
        "uv.lock",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "bun.lock",
        "Cargo.toml",
        "Cargo.lock",
        "go.mod",
        "Dockerfile",
        "Containerfile",
        ".pre-commit-config.yaml",
        ".pre-commit-config.yml",
        "prek.toml",
        "mise.toml",
        ".mise.toml",
        ".tool-versions",
        "Chart.yaml",
        "uv-receipt.toml",
    }
)


@dataclass
class ProjectRecord:
    """Discovered project metadata ready for SQLite upsert."""

    project_key: str
    display_path: str
    main_worktree: str
    last_commit_at: str | None
    active: int
    renovate_json: str | None
    excluded: int
    root_source: str
    visibility: str = "unknown"
    alias: str | None = None
    manifest_files_json: str = "[]"


@dataclass
class ProjectsInventoryOutput:
    """Aggregated output from projects.collect(ctx)."""

    projects: list[ProjectRecord] = field(default_factory=list)
    entities: list[EntityRecord] = field(default_factory=list)
    usages: list[UsageRecord] = field(default_factory=list)
    fingerprints: list[fp.FingerprintRecord] = field(default_factory=list)
    dataless_skipped: int = 0


def is_ai_sdk(name: str) -> bool:
    """Return True if a package name matches Spec 04 [AIH-12] AI SDK patterns."""
    norm = re.sub(r"[-_.]+", "-", name.strip().lower())
    if norm in AI_SDK_EXACT or name.strip().lower() in AI_SDK_EXACT:
        return True
    return norm.startswith("langchain") or norm.startswith("@langchain/")


def normalize_pypi_name(name: str) -> str:
    """Normalize a PyPI distribution name per PEP 503."""
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def is_never_read(path: Path | str) -> bool:
    """Return True if path matches Spec 04's never-read secret file patterns."""
    p = Path(path)
    name = p.name
    low = name.lower()
    if ".git" in p.parts:
        return True
    if low == ".env" or low.startswith(".env"):
        return True
    if low == ".netrc":
        return True
    if low.endswith(NEVER_READ_SUFFIXES):
        return True
    if low.startswith("id_"):
        return True
    return low.startswith("credentials")


def _stat_file(path: Path) -> tuple[os.stat_result | None, bool]:
    """Stat a file using os.stat and check SF_DATALESS."""
    if is_never_read(path):
        return None, False
    try:
        st = os.stat(path)
    except OSError:
        return None, False
    if fsguard.is_dataless(st):
        return None, True
    return st, False


def _read_text_safe(path: Path) -> tuple[str | None, bool]:
    """Read file text only after verifying never-read exclusion and SF_DATALESS."""
    if is_never_read(path):
        return None, False
    st, dataless = _stat_file(path)
    if dataless:
        return None, True
    if st is None:
        return None, False
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return fh.read(), False
    except OSError:
        return None, False


def normalize_origin_url(raw_url: str | None, fallback_path: Path | None = None) -> str:
    """Normalize a git remote URL to `https://host/owner/repo` with userinfo stripped."""
    cleaned = (raw_url or "").strip()
    if cleaned:
        scp_match = _SCP_GIT_RE.match(cleaned)
        if scp_match and "://" not in cleaned:
            host = scp_match.group(1).lower()
            repo_path = scp_match.group(2).lstrip("/")
            if repo_path.endswith(".git"):
                repo_path = repo_path[:-4]
            repo_path = repo_path.rstrip("/").lower()
            return f"https://{host}/{repo_path}"

        try:
            parsed = urllib.parse.urlsplit(cleaned)
            if parsed.hostname and parsed.path:
                host = parsed.hostname.lower()
                repo_path = parsed.path.lstrip("/")
                if repo_path.endswith(".git"):
                    repo_path = repo_path[:-4]
                repo_path = repo_path.rstrip("/").lower()
                if repo_path:
                    return f"https://{host}/{repo_path}"
        except ValueError:
            pass

    target = fallback_path.resolve() if fallback_path is not None else Path(".").resolve()
    digest = hashlib.sha256(str(target).encode("utf-8")).hexdigest()
    return f"local:{digest}"


def entity_for_usage(entity_id: str) -> EntityRecord:
    """Build an EntityRecord from a canonical entity_id prefix per Spec 04."""
    if entity_id.startswith("pkg:"):
        rest = entity_id[len("pkg:") :]
        eco, name = rest.split("/", 1) if "/" in rest else ("unknown", rest)
        return EntityRecord(
            entity_id=entity_id,
            kind="package",
            ecosystem=eco,
            name=name,
        )
    if entity_id.startswith("gha:"):
        slug = entity_id[len("gha:") :]
        parts = slug.split("/")
        repo_slug = "/".join(parts[:2]).lower() if len(parts) >= 2 else slug.lower()
        return EntityRecord(
            entity_id=entity_id,
            kind="action",
            ecosystem="github-actions",
            name=slug,
            repo=repo_slug,
            visibility="public",
        )
    if entity_id.startswith("gh:"):
        slug = entity_id[len("gh:") :].lower()
        return EntityRecord(
            entity_id=entity_id,
            kind="repo",
            ecosystem="github",
            name=slug,
            repo=slug,
        )
    if entity_id.startswith("oci:"):
        name = entity_id[len("oci:") :]
        return EntityRecord(
            entity_id=entity_id,
            kind="package",
            ecosystem="docker",
            name=name,
            visibility="public",
        )
    if entity_id.startswith("tf:"):
        name = entity_id[len("tf:") :]
        return EntityRecord(
            entity_id=entity_id,
            kind="package",
            ecosystem="terraform",
            name=name,
            visibility="public",
        )
    if entity_id.startswith("helm:"):
        name = entity_id[len("helm:") :]
        return EntityRecord(
            entity_id=entity_id,
            kind="package",
            ecosystem="helm",
            name=name,
            visibility="public",
        )
    if entity_id.startswith("nix:"):
        name = entity_id[len("nix:") :]
        return EntityRecord(
            entity_id=entity_id,
            kind="package",
            ecosystem="nix",
            name=name,
            visibility="public",
        )
    if entity_id.startswith("brew:formula/"):
        name = entity_id[len("brew:formula/") :]
        return EntityRecord(
            entity_id=entity_id,
            kind="cli",
            ecosystem="homebrew",
            name=name,
            visibility="public",
        )
    if entity_id.startswith("brew:cask/"):
        name = entity_id[len("brew:cask/") :]
        return EntityRecord(
            entity_id=entity_id,
            kind="app",
            ecosystem="homebrew",
            name=name,
            visibility="public",
        )
    if entity_id.startswith("app:"):
        name = entity_id[len("app:") :]
        return EntityRecord(
            entity_id=entity_id,
            kind="app",
            ecosystem="macos",
            name=name,
            visibility="public",
        )
    if entity_id.startswith("raycast:"):
        name = entity_id[len("raycast:") :]
        return EntityRecord(
            entity_id=entity_id,
            kind="plugin",
            ecosystem="raycast",
            name=name,
            visibility="public",
        )
    if entity_id.startswith("mcp:"):
        name = entity_id[len("mcp:") :]
        return EntityRecord(
            entity_id=entity_id,
            kind="mcp",
            ecosystem="mcp",
            name=name,
            visibility="public",
        )
    if entity_id.startswith("skill:"):
        name = entity_id[len("skill:") :]
        return EntityRecord(
            entity_id=entity_id,
            kind="skill",
            ecosystem="agent-skills",
            name=name,
            visibility="public",
        )
    if entity_id.startswith("plugin:"):
        name = entity_id[len("plugin:") :]
        return EntityRecord(
            entity_id=entity_id,
            kind="plugin",
            ecosystem="claude-code",
            name=name,
            visibility="public",
        )
    if entity_id.startswith("model:"):
        rest = entity_id[len("model:") :]
        prov, name = rest.split("/", 1) if "/" in rest else ("unknown", rest)
        return EntityRecord(
            entity_id=entity_id,
            kind="model",
            ecosystem=prov,
            name=name,
            visibility="public",
        )
    if entity_id.startswith("svc:"):
        name = entity_id.split("/", 1)[-1]
        return EntityRecord(
            entity_id=entity_id,
            kind="service",
            ecosystem="launchd",
            name=name,
            visibility="private",
        )
    if entity_id.startswith("runtime:"):
        name = entity_id[len("runtime:") :]
        return EntityRecord(
            entity_id=entity_id,
            kind="runtime",
            ecosystem="os",
            name=name,
            visibility="public",
        )
    if entity_id.startswith("technique:"):
        name = entity_id[len("technique:") :]
        return EntityRecord(
            entity_id=entity_id,
            kind="technique",
            ecosystem=None,
            name=name,
            visibility="public",
        )
    if entity_id.startswith("bin:"):
        name = entity_id[len("bin:") :]
        return EntityRecord(
            entity_id=entity_id,
            kind="cli",
            ecosystem="manual",
            name=name,
        )
    if entity_id.startswith("harness:tool/"):
        name = entity_id[len("harness:tool/") :]
        return EntityRecord(
            entity_id=entity_id,
            kind="cli",
            ecosystem="claude-code",
            name=name,
            visibility="public",
        )
    return EntityRecord(
        entity_id=entity_id,
        kind="package",
        ecosystem=None,
        name=entity_id,
    )


def _extract_pyproject_direct_and_private_index(
    pyproject_data: dict[str, Any],
) -> tuple[dict[str, str | None], set[str]]:
    """Return (direct_dep_name -> version_spec_or_None, custom_index_dep_names)."""
    direct: dict[str, str | None] = {}
    custom_index_deps: set[str] = set()

    proj = pyproject_data.get("project")
    if isinstance(proj, dict):
        for raw_dep in proj.get("dependencies") or []:
            if not isinstance(raw_dep, str):
                continue
            m = _PEP508_NAME_RE.match(raw_dep)
            if m:
                name = normalize_pypi_name(m.group(1))
                vm = _PEP508_VER_RE.search(raw_dep)
                direct[name] = vm.group(1) if vm else None

        opt_deps = proj.get("optional-dependencies")
        if isinstance(opt_deps, dict):
            for group_list in opt_deps.values():
                if not isinstance(group_list, list):
                    continue
                for raw_dep in group_list:
                    if not isinstance(raw_dep, str):
                        continue
                    m = _PEP508_NAME_RE.match(raw_dep)
                    if m:
                        name = normalize_pypi_name(m.group(1))
                        vm = _PEP508_VER_RE.search(raw_dep)
                        direct.setdefault(name, vm.group(1) if vm else None)

    dep_groups = pyproject_data.get("dependency-groups")
    if isinstance(dep_groups, dict):
        for group_list in dep_groups.values():
            if not isinstance(group_list, list):
                continue
            for raw_dep in group_list:
                if not isinstance(raw_dep, str):
                    continue
                m = _PEP508_NAME_RE.match(raw_dep)
                if m:
                    name = normalize_pypi_name(m.group(1))
                    vm = _PEP508_VER_RE.search(raw_dep)
                    direct.setdefault(name, vm.group(1) if vm else None)

    tool_tbl = pyproject_data.get("tool")
    if isinstance(tool_tbl, dict):
        uv_tbl = tool_tbl.get("uv")
        if isinstance(uv_tbl, dict):
            indexes = uv_tbl.get("index")
            has_custom_index = isinstance(indexes, list) and len(indexes) > 0
            sources = uv_tbl.get("sources")
            if isinstance(sources, dict):
                for pkg_key, src_cfg in sources.items():
                    norm_pkg = normalize_pypi_name(str(pkg_key))
                    if isinstance(src_cfg, dict) and (src_cfg.get("index") or has_custom_index):
                        custom_index_deps.add(norm_pkg)
            elif has_custom_index:
                custom_index_deps.update(direct.keys())

    return direct, custom_index_deps


def _make_usage(
    *,
    entity_id: str,
    context: str,
    version: str | None,
    direct: int,
    source_file: str,
    extra_attrs: dict[str, Any] | None = None,
) -> UsageRecord:
    attrs: dict[str, Any] = {"source_file": source_file}
    if extra_attrs:
        attrs.update(extra_attrs)
    if entity_id.startswith(("pkg:pypi/", "pkg:npm/")):
        pkg_name = entity_id.split("/", 1)[1]
        if is_ai_sdk(pkg_name):
            attrs["ai_sdk"] = True
    return UsageRecord(
        entity_id=entity_id,
        context=context,
        version=version,
        direct=direct,
        attrs=attrs,
    )


def _context_for_project_key(project_key: str) -> str:
    if project_key.startswith(("project:", "launchd:", "system:")):
        return project_key
    return f"project:{project_key}"


def parse_manifest(path: Path | str, project_key: str) -> list[UsageRecord]:
    """Single per-file manifest/lockfile parser used by both collect and hourly (Spec 12 A27)."""
    manifest_path = Path(path).expanduser()
    if is_never_read(manifest_path):
        return []
    abs_source = str(manifest_path.resolve())
    context = _context_for_project_key(project_key)
    text, _dataless = _read_text_safe(manifest_path)
    if text is None:
        return []

    fname = manifest_path.name
    usages: list[UsageRecord] = []

    if fname == "uv.lock":
        try:
            lock_data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return []
        if not isinstance(lock_data, dict):
            return []

        direct_names: set[str] = set()
        custom_index_deps: set[str] = set()
        root_pkg_names: set[str] = set()

        sibling_pyproject = manifest_path.parent / "pyproject.toml"
        if sibling_pyproject.is_file() and not is_never_read(sibling_pyproject):
            py_text, _ = _read_text_safe(sibling_pyproject)
            if py_text:
                try:
                    py_data = tomllib.loads(py_text)
                    if isinstance(py_data, dict):
                        d_map, c_idx = _extract_pyproject_direct_and_private_index(py_data)
                        direct_names.update(d_map.keys())
                        custom_index_deps.update(c_idx)
                        proj_tbl = py_data.get("project")
                        if isinstance(proj_tbl, dict) and proj_tbl.get("name"):
                            root_pkg_names.add(normalize_pypi_name(str(proj_tbl["name"])))
                except tomllib.TOMLDecodeError:
                    pass

        packages = lock_data.get("package")
        if not isinstance(packages, list):
            return []

        for pkg in packages:
            if not isinstance(pkg, dict):
                continue
            raw_name = str(pkg.get("name") or "").strip()
            if not raw_name:
                continue
            norm_name = normalize_pypi_name(raw_name)
            src = pkg.get("source")
            is_virtual_or_editable = isinstance(src, dict) and (
                "virtual" in src or "editable" in src
            )
            if is_virtual_or_editable or norm_name in root_pkg_names:
                for dep_item in pkg.get("dependencies") or []:
                    if isinstance(dep_item, dict) and dep_item.get("name"):
                        direct_names.add(normalize_pypi_name(str(dep_item["name"])))
                dev_deps = pkg.get("dev-dependencies")
                if isinstance(dev_deps, dict):
                    for grp_list in dev_deps.values():
                        if isinstance(grp_list, list):
                            for dep_item in grp_list:
                                if isinstance(dep_item, dict) and dep_item.get("name"):
                                    direct_names.add(normalize_pypi_name(str(dep_item["name"])))

        for pkg in packages:
            if not isinstance(pkg, dict):
                continue
            raw_name = str(pkg.get("name") or "").strip()
            if not raw_name:
                continue
            norm_name = normalize_pypi_name(raw_name)
            src = pkg.get("source")
            is_virtual_or_editable = isinstance(src, dict) and (
                "virtual" in src or "editable" in src
            )
            if is_virtual_or_editable or norm_name in root_pkg_names:
                continue

            ver = str(pkg.get("version") or "").strip() or None
            is_direct = 1 if (not direct_names or norm_name in direct_names) else 0
            extra: dict[str, Any] = {}
            if norm_name in custom_index_deps:
                extra["private_index"] = True
            if isinstance(src, dict):
                reg = str(src.get("registry") or "")
                if reg and "pypi.org" not in reg and "files.pythonhosted.org" not in reg:
                    extra["private_index"] = True

            usages.append(
                _make_usage(
                    entity_id=f"pkg:pypi/{norm_name}",
                    context=context,
                    version=ver,
                    direct=is_direct,
                    source_file=abs_source,
                    extra_attrs=extra,
                )
            )
        return usages

    if fname == "uv-receipt.toml":
        try:
            rec_data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return []
        if not isinstance(rec_data, dict):
            return []
        tool_tbl = rec_data.get("tool")
        reqs = tool_tbl.get("requirements") if isinstance(tool_tbl, dict) else None
        if isinstance(reqs, list):
            for req in reqs:
                if not isinstance(req, dict) or not req.get("name"):
                    continue
                norm_name = normalize_pypi_name(str(req["name"]))
                ver: str | None = None
                if req.get("version"):
                    ver = str(req["version"]).strip()
                elif req.get("specifier"):
                    vm = _PEP508_VER_RE.search(str(req["specifier"]))
                    ver = vm.group(1) if vm else str(req["specifier"]).strip().lstrip("=~^><")
                usages.append(
                    _make_usage(
                        entity_id=f"pkg:pypi/{norm_name}",
                        context="system:uv-tool" if project_key == "system:uv-tool" else context,
                        version=ver,
                        direct=1,
                        source_file=abs_source,
                    )
                )
        return usages

    if fname == "pyproject.toml":
        try:
            py_data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return []
        if not isinstance(py_data, dict):
            return []
        direct_map, custom_index_deps = _extract_pyproject_direct_and_private_index(py_data)
        for dep_name, ver in sorted(direct_map.items()):
            extra = {"private_index": True} if dep_name in custom_index_deps else {}
            usages.append(
                _make_usage(
                    entity_id=f"pkg:pypi/{dep_name}",
                    context=context,
                    version=ver,
                    direct=1,
                    source_file=abs_source,
                    extra_attrs=extra,
                )
            )
        return usages

    if fname == "package.json":
        try:
            pkg_data = json.loads(text)
        except json.JSONDecodeError:
            return []
        if not isinstance(pkg_data, dict):
            return []
        is_priv_manifest = bool(pkg_data.get("private") is True)
        for sec in ("dependencies", "devDependencies", "optionalDependencies"):
            deps = pkg_data.get(sec)
            if not isinstance(deps, dict):
                continue
            for dep_name, ver_spec in sorted(deps.items()):
                name = str(dep_name).strip()
                if not name:
                    continue
                ver = str(ver_spec).strip().lstrip("^~>=<") if ver_spec else None
                extra: dict[str, Any] = {}
                if is_priv_manifest and (
                    str(ver_spec).startswith(("workspace:", "file:", "link:"))
                ):
                    extra["private"] = True
                usages.append(
                    _make_usage(
                        entity_id=f"pkg:npm/{name}",
                        context=context,
                        version=ver,
                        direct=1,
                        source_file=abs_source,
                        extra_attrs=extra,
                    )
                )
        return usages

    if fname == "package-lock.json":
        try:
            lock_data = json.loads(text)
        except json.JSONDecodeError:
            return []
        if not isinstance(lock_data, dict):
            return []
        direct_npm: set[str] = set()
        sibling_pkg = manifest_path.parent / "package.json"
        if sibling_pkg.is_file() and not is_never_read(sibling_pkg):
            p_text, _ = _read_text_safe(sibling_pkg)
            if p_text:
                try:
                    p_obj = json.loads(p_text)
                    if isinstance(p_obj, dict):
                        for sec in ("dependencies", "devDependencies", "optionalDependencies"):
                            if isinstance(p_obj.get(sec), dict):
                                direct_npm.update(str(k) for k in p_obj[sec])
                except json.JSONDecodeError:
                    pass
        packages_tbl = lock_data.get("packages")
        if isinstance(packages_tbl, dict):
            for pkg_path, meta in packages_tbl.items():
                if not pkg_path or not isinstance(meta, dict):
                    continue
                if "node_modules/" not in pkg_path:
                    continue
                name = pkg_path.split("node_modules/")[-1].strip()
                if not name:
                    continue
                ver = str(meta.get("version") or "").strip() or None
                is_direct = 1 if (not direct_npm or name in direct_npm) else 0
                usages.append(
                    _make_usage(
                        entity_id=f"pkg:npm/{name}",
                        context=context,
                        version=ver,
                        direct=is_direct,
                        source_file=abs_source,
                    )
                )
        return usages

    if fname in ("pnpm-lock.yaml", "yarn.lock", "bun.lock"):
        for m in re.finditer(r"['\"]?([@A-Za-z0-9_./-]+)@(\d+\.\d+\.\d+[^'\"\s:]*)['\"]?", text):
            name = m.group(1).strip()
            ver = m.group(2).strip()
            if name and not name.startswith("."):
                usages.append(
                    _make_usage(
                        entity_id=f"pkg:npm/{name}",
                        context=context,
                        version=ver,
                        direct=0,
                        source_file=abs_source,
                    )
                )
        return usages

    if fname == "Cargo.toml":
        try:
            cargo_data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return []
        if not isinstance(cargo_data, dict):
            return []
        for sec in ("dependencies", "dev-dependencies", "build-dependencies"):
            deps = cargo_data.get(sec)
            if not isinstance(deps, dict):
                continue
            for crate, spec in sorted(deps.items()):
                ver: str | None = None
                if isinstance(spec, str):
                    ver = spec.strip()
                elif isinstance(spec, dict) and spec.get("version"):
                    ver = str(spec["version"]).strip()
                usages.append(
                    _make_usage(
                        entity_id=f"pkg:cargo/{crate}",
                        context=context,
                        version=ver,
                        direct=1,
                        source_file=abs_source,
                    )
                )
        return usages

    if fname == "Cargo.lock":
        try:
            lock_data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return []
        if not isinstance(lock_data, dict):
            return []
        for pkg in lock_data.get("package") or []:
            if not isinstance(pkg, dict):
                continue
            crate = str(pkg.get("name") or "").strip()
            ver = str(pkg.get("version") or "").strip() or None
            if crate:
                usages.append(
                    _make_usage(
                        entity_id=f"pkg:cargo/{crate}",
                        context=context,
                        version=ver,
                        direct=0,
                        source_file=abs_source,
                    )
                )
        return usages

    if fname == "go.mod":
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("module ", "go ", "//", ")")) or not stripped:
                continue
            if stripped.startswith("require ") and "(" not in stripped:
                stripped = stripped[len("require ") :].strip()
            m = _GO_REQUIRE_LINE_RE.match(stripped)
            if m:
                mod_path = m.group(1).strip()
                ver = m.group(2).strip()
                trailing = m.group(3)
                is_direct = 0 if "indirect" in trailing else 1
                usages.append(
                    _make_usage(
                        entity_id=f"pkg:golang/{mod_path}",
                        context=context,
                        version=ver,
                        direct=is_direct,
                        source_file=abs_source,
                    )
                )
        return usages

    if fname in ("Dockerfile", "Containerfile") or fname.startswith("Dockerfile."):
        for line in text.splitlines():
            m = _DOCKER_FROM_RE.match(line)
            if not m:
                continue
            image_ref = m.group(1).strip()
            if image_ref.lower() == "scratch" or image_ref.startswith("$"):
                continue
            img_name = image_ref
            img_ver: str | None = None
            if "@" in image_ref:
                img_name, img_ver = image_ref.split("@", 1)
            elif ":" in image_ref.split("/")[-1]:
                img_name, img_ver = image_ref.rsplit(":", 1)
            usages.append(
                _make_usage(
                    entity_id=f"oci:{img_name}",
                    context=context,
                    version=img_ver,
                    direct=1,
                    source_file=abs_source,
                )
            )
        return usages

    if fname in (".pre-commit-config.yaml", ".pre-commit-config.yml"):
        try:
            pc_data = yaml.safe_load(text)
        except yaml.YAMLError:
            return []
        if not isinstance(pc_data, dict):
            return []
        for repo_item in pc_data.get("repos") or []:
            if not isinstance(repo_item, dict):
                continue
            repo_url = str(repo_item.get("repo") or "").strip()
            if repo_url in ("local", "meta") or "github.com/" not in repo_url:
                continue
            slug_part = repo_url.split("github.com/", 1)[1].strip("/")
            if slug_part.endswith(".git"):
                slug_part = slug_part[:-4]
            slug = slug_part.lower()
            rev = str(repo_item.get("rev") or "").strip() or None
            if "/" in slug:
                usages.append(
                    _make_usage(
                        entity_id=f"gh:{slug}",
                        context=context,
                        version=rev,
                        direct=1,
                        source_file=abs_source,
                    )
                )
        return usages

    if fname == "prek.toml":
        try:
            prek_data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return []
        if not isinstance(prek_data, dict):
            return []
        for repo_item in prek_data.get("repos") or []:
            if not isinstance(repo_item, dict):
                continue
            repo_url = str(repo_item.get("repo") or "").strip()
            if repo_url in ("local", "meta") or "github.com/" not in repo_url:
                continue
            slug_part = repo_url.split("github.com/", 1)[1].strip("/")
            if slug_part.endswith(".git"):
                slug_part = slug_part[:-4]
            slug = slug_part.lower()
            rev = str(repo_item.get("rev") or "").strip() or None
            if "/" in slug:
                usages.append(
                    _make_usage(
                        entity_id=f"gh:{slug}",
                        context=context,
                        version=rev,
                        direct=1,
                        source_file=abs_source,
                    )
                )
        return usages

    if fname in ("mise.toml", ".mise.toml"):
        try:
            mise_data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return []
        if isinstance(mise_data, dict) and isinstance(mise_data.get("tools"), dict):
            for tool_name, ver_val in sorted(mise_data["tools"].items()):
                ver = str(ver_val) if isinstance(ver_val, str | int | float) else None
                usages.append(
                    _make_usage(
                        entity_id=f"pkg:mise/{tool_name}",
                        context=context,
                        version=ver,
                        direct=1,
                        source_file=abs_source,
                    )
                )
        return usages

    if fname == ".tool-versions":
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if len(parts) >= 2:
                usages.append(
                    _make_usage(
                        entity_id=f"pkg:mise/{parts[0]}",
                        context=context,
                        version=parts[1],
                        direct=1,
                        source_file=abs_source,
                    )
                )
        return usages

    if fname == "Chart.yaml":
        try:
            chart_data = yaml.safe_load(text)
        except yaml.YAMLError:
            return []
        if isinstance(chart_data, dict) and isinstance(chart_data.get("dependencies"), list):
            for dep in chart_data["dependencies"]:
                if isinstance(dep, dict) and dep.get("name"):
                    usages.append(
                        _make_usage(
                            entity_id=f"helm:{dep['name']}",
                            context=context,
                            version=str(dep.get("version") or "") or None,
                            direct=1,
                            source_file=abs_source,
                        )
                    )
        return usages

    if fname.endswith(".tf"):
        for sm in _TF_SOURCE_RE.finditer(text):
            src = sm.group(1).strip()
            if "/" in src:
                vm = _TF_VERSION_RE.search(text[sm.end() : sm.end() + 200])
                ver = vm.group(1).strip() if vm else None
                usages.append(
                    _make_usage(
                        entity_id=f"tf:{src}",
                        context=context,
                        version=ver,
                        direct=1,
                        source_file=abs_source,
                    )
                )
        return usages

    if fname.endswith((".yml", ".yaml")):
        try:
            wf_data = yaml.safe_load(text)
        except yaml.YAMLError:
            return []
        if not isinstance(wf_data, dict):
            return []
        jobs = wf_data.get("jobs")
        if isinstance(jobs, dict):
            for job_cfg in jobs.values():
                if not isinstance(job_cfg, dict):
                    continue
                for step in job_cfg.get("steps") or []:
                    if not isinstance(step, dict) or not step.get("uses"):
                        continue
                    uses_val = str(step["uses"]).strip()
                    if uses_val.startswith(("./", "docker://")) or "@" not in uses_val:
                        continue
                    action_part, ref = uses_val.rsplit("@", 1)
                    segs = action_part.split("/")
                    if len(segs) < 2:
                        continue
                    action_slug = f"{segs[0].lower()}/{segs[1].lower()}"
                    sha_pinned = bool(_SHA40_RE.match(ref))
                    usages.append(
                        _make_usage(
                            entity_id=f"gha:{action_slug}",
                            context=context,
                            version=ref,
                            direct=1,
                            source_file=abs_source,
                            extra_attrs={
                                "ref": ref,
                                "sha_pinned": sha_pinned,
                            },
                        )
                    )
        elif wf_data.get("apiVersion") and wf_data.get("kind"):
            for m in re.finditer(r"^\s*-?\s*image:\s*['\"]?([^\s'\"]+)['\"]?", text, re.MULTILINE):
                img_ref = m.group(1).strip()
                img_name = img_ref.split(":")[0].split("@")[0]
                img_ver = img_ref.split(":", 1)[1] if ":" in img_ref else None
                usages.append(
                    _make_usage(
                        entity_id=f"oci:{img_name}",
                        context=context,
                        version=img_ver,
                        direct=1,
                        source_file=abs_source,
                    )
                )
        return usages

    return []


def _extract_workflow_fingerprints(
    wf_path: Path,
    context: str,
    known_clis: Collection[str],
) -> tuple[list[fp.FingerprintRecord], int]:
    text, dataless = _read_text_safe(wf_path)
    if dataless:
        return [], 1
    if not text:
        return [], 0
    try:
        wf_data = yaml.safe_load(text)
    except yaml.YAMLError:
        return [], 0
    if not isinstance(wf_data, dict) or not isinstance(wf_data.get("jobs"), dict):
        return [], 0

    out: list[fp.FingerprintRecord] = []
    for job_cfg in wf_data["jobs"].values():
        if not isinstance(job_cfg, dict):
            continue
        for step in job_cfg.get("steps") or []:
            if not isinstance(step, dict):
                continue
            uses_val = str(step.get("uses") or "").strip()
            if uses_val and "@" in uses_val and isinstance(step.get("with"), dict):
                action_part = uses_val.rsplit("@", 1)[0]
                segs = action_part.split("/")
                if len(segs) >= 2:
                    action_slug = f"{segs[0].lower()}/{segs[1].lower()}"
                    for wk in step["with"]:
                        out.append(
                            fp.FingerprintRecord(
                                context=context,
                                kind="config_key",
                                token=f"{action_slug}.{wk}",
                            )
                        )
            run_val = step.get("run")
            if isinstance(run_val, str) and run_val.strip():
                out.extend(
                    fp.extract_cli_fingerprints(
                        run_val,
                        context,
                        known_clis,
                        emit_cmd=True,
                    )
                )
    return fp.merge_fingerprints(out), 0


def _discover_git_dirs(root: Path, max_depth: int = 4) -> tuple[list[Path], int]:
    """Walk root up to max_depth levels looking for directories containing .git."""
    found: list[Path] = []
    dataless_count = 0
    if not root.is_dir():
        return found, 0

    def _walk(cur: Path, depth: int) -> None:
        nonlocal dataless_count
        if depth > max_depth:
            return
        try:
            entries = sorted(cur.iterdir(), key=lambda p: p.name)
        except OSError:
            return

        has_git = any(e.name == ".git" for e in entries)
        if has_git:
            found.append(cur)

        if depth == max_depth:
            return
        for entry in entries:
            if entry.name in PRUNED_DIRS or entry.name.startswith("."):
                continue
            if entry.is_symlink():
                continue
            if entry.is_dir():
                _walk(entry, depth + 1)

    _walk(root, 0)
    return found, dataless_count


def _resolve_main_worktree(ctx: Ctx, repo_dir: Path) -> Path:
    try:
        res = ctx.procs.git(["worktree", "list", "--porcelain"], repo_dir)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if line.startswith("worktree "):
                    wt_path = line[len("worktree ") :].strip()
                    if wt_path:
                        return Path(wt_path).resolve()
    except RadarError:
        pass
    return repo_dir.resolve()


def _resolve_last_commit_at(ctx: Ctx, repo_dir: Path) -> str | None:
    try:
        res = ctx.procs.git(["log", "-1", "--format=%cI"], repo_dir)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip().splitlines()[0].strip()
    except RadarError:
        pass
    return None


def _resolve_origin_url(ctx: Ctx, repo_dir: Path) -> str | None:
    try:
        res = ctx.procs.git(["remote", "get-url", "origin"], repo_dir)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip().splitlines()[0].strip()
    except RadarError:
        pass
    return None


def _is_commit_within_days(commit_iso: str | None, now: datetime, days: int = 180) -> bool:
    if not commit_iso:
        return False
    try:
        dt = datetime.fromisoformat(commit_iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return (now - dt) <= timedelta(days=days)
    except ValueError:
        return False


def _matches_private_scope(slug_or_name: str, private_scopes: Sequence[str]) -> bool:
    low = slug_or_name.lower().lstrip("@")
    for raw_scope in private_scopes:
        scope = raw_scope.strip().lower().lstrip("@").rstrip("/*")
        if not scope:
            continue
        if low == scope or low.startswith(f"{scope}/") or low.startswith(f"{scope}-"):
            return True
    return False


def _classify_project_visibility_and_alias(
    ctx: Ctx,
    project_key: str,
    existing_aliases: dict[str, str],
    used_private_numbers: set[int],
) -> tuple[str, str]:
    """Determine project visibility via gh api repos/o/r and assign stable alias (Spec 12 A8)."""
    gh_match = _GITHUB_KEY_RE.match(project_key)
    visibility = "private"
    public_slug: str | None = None

    if gh_match:
        owner = gh_match.group(1).lower()
        repo = gh_match.group(2).lower()
        slug = f"{owner}/{repo}"
        if not _matches_private_scope(
            slug, ctx.profile.private_scopes
        ) and not _matches_private_scope(owner, ctx.profile.private_scopes):
            try:
                res = ctx.gh(["api", f"repos/{owner}/{repo}"])
                if res.returncode == 0 and res.stdout.strip():
                    data = json.loads(res.stdout)
                    if isinstance(data, dict) and data.get("private") is False:
                        visibility = "public"
                        public_slug = slug
            except (RadarError, json.JSONDecodeError):
                visibility = "private"

    if visibility == "public" and public_slug:
        return "public", public_slug

    existing = existing_aliases.get(project_key)
    if existing and existing.startswith("private-project-"):
        return "private", existing

    next_n = (max(used_private_numbers) + 1) if used_private_numbers else 1
    used_private_numbers.add(next_n)
    alias = f"private-project-{next_n}"
    existing_aliases[project_key] = alias
    return "private", alias


def _load_existing_project_aliases(conn: sqlite3.Connection) -> tuple[dict[str, str], set[int]]:
    rows = conn.execute(
        "SELECT project_key, alias FROM projects WHERE alias IS NOT NULL"
    ).fetchall()
    by_key: dict[str, str] = {}
    nums: set[int] = set()
    for row in rows:
        pkey = str(row["project_key"])
        alias = str(row["alias"])
        by_key[pkey] = alias
        if alias.startswith("private-project-"):
            suffix = alias[len("private-project-") :]
            if suffix.isdigit():
                nums.add(int(suffix))
    return by_key, nums


def _scan_single_project(
    project_dir: Path,
    project_key: str,
    known_clis: Collection[str],
    out: ProjectsInventoryOutput,
) -> tuple[str | None, str]:
    """Scan manifests, code fingerprints, recipes, and intent inside a single project."""
    context = f"project:{project_key}"
    manifest_meta: list[dict[str, Any]] = []
    manifest_paths: list[Path] = []
    renovate_json: str | None = None

    def _walk_project(cur: Path, rel_depth: int) -> None:
        nonlocal renovate_json
        if rel_depth > 4:
            return
        try:
            entries = sorted(cur.iterdir(), key=lambda p: p.name)
        except OSError:
            return

        for entry in entries:
            name = entry.name
            if name in PRUNED_DIRS:
                continue
            if entry.is_symlink():
                continue
            if is_never_read(entry):
                continue

            if entry.is_dir():
                if name == ".github":
                    wf_dir = entry / "workflows"
                    if wf_dir.is_dir():
                        try:
                            wf_entries = sorted(wf_dir.iterdir(), key=lambda p: p.name)
                        except OSError:
                            wf_entries = []
                        for wf_file in wf_entries:
                            if wf_file.is_symlink() or is_never_read(wf_file):
                                continue
                            if wf_file.name.endswith((".yml", ".yaml")):
                                st, dataless = _stat_file(wf_file)
                                if dataless:
                                    out.dataless_skipped += 1
                                    continue
                                if st is not None:
                                    wf_resolved = wf_file.resolve()
                                    wf_sha = ""
                                    with contextlib.suppress(OSError):
                                        wf_sha = hashlib.sha256(wf_file.read_bytes()).hexdigest()
                                    manifest_paths.append(wf_file)
                                    manifest_meta.append(
                                        {
                                            "path": str(wf_resolved),
                                            "mtime": st.st_mtime,
                                            "size": st.st_size,
                                            "sha256": wf_sha,
                                        }
                                    )
                    ren5 = entry / "renovate.json5"
                    if ren5.is_file() and not is_never_read(ren5) and renovate_json is None:
                        r_text, d_skip = _read_text_safe(ren5)
                        if d_skip:
                            out.dataless_skipped += 1
                        elif r_text is not None:
                            renovate_json = redact.scrub(r_text)
                    continue
                if name.startswith(".") and name not in (".claude",):
                    continue
                _walk_project(entry, rel_depth + 1)
                continue

            st, dataless = _stat_file(entry)
            if dataless:
                out.dataless_skipped += 1
                continue
            if st is None:
                continue

            if name == "renovate.json":
                r_text, d_skip = _read_text_safe(entry)
                if d_skip:
                    out.dataless_skipped += 1
                elif r_text is not None:
                    renovate_json = redact.scrub(r_text)
                continue

            if name in MANIFEST_BASENAMES or name.startswith("Dockerfile.") or name.endswith(".tf"):
                entry_sha = ""
                with contextlib.suppress(OSError):
                    entry_sha = hashlib.sha256(entry.read_bytes()).hexdigest()
                manifest_paths.append(entry)
                manifest_meta.append(
                    {
                        "path": str(entry.resolve()),
                        "mtime": st.st_mtime,
                        "size": st.st_size,
                        "sha256": entry_sha,
                    }
                )

            if name in (
                "ruff.toml",
                ".ruff.toml",
                "tsconfig.json",
                ".pre-commit-config.yaml",
                ".pre-commit-config.yml",
            ):
                cfg_fps, d_skip = fp.extract_config_file_fingerprints(entry, context)
                out.dataless_skipped += d_skip
                out.fingerprints.extend(cfg_fps)

            if name in (
                "Makefile",
                "justfile",
                "Justfile",
                "Taskfile.yml",
                "Taskfile.yaml",
            ) or name.endswith(".sh"):
                script_text, d_skip = _read_text_safe(entry)
                if d_skip:
                    out.dataless_skipped += 1
                elif script_text:
                    out.fingerprints.extend(
                        fp.extract_cli_fingerprints(
                            script_text,
                            context,
                            known_clis,
                            emit_cmd=True,
                        )
                    )

            if name.endswith(".py"):
                py_fps, d_skip = fp.extract_python_ast_fingerprints(entry, context)
                out.dataless_skipped += d_skip
                out.fingerprints.extend(py_fps)
            elif name.endswith((".js", ".ts", ".jsx", ".tsx")):
                js_fps, d_skip = fp.extract_jsts_fingerprints(entry, context)
                out.dataless_skipped += d_skip
                out.fingerprints.extend(js_fps)

    _walk_project(project_dir, 0)

    has_uv_lock = any(p.name == "uv.lock" for p in manifest_paths)
    has_pkg_lock = any(p.name == "package-lock.json" for p in manifest_paths)
    has_cargo_lock = any(p.name == "Cargo.lock" for p in manifest_paths)

    seen_usage_ids: set[str] = set()
    ordered_manifests = sorted(
        manifest_paths,
        key=lambda p: (
            0 if p.name in ("uv.lock", "package-lock.json", "Cargo.lock") else 1,
            str(p),
        ),
    )

    for mpath in ordered_manifests:
        if mpath.name == "pyproject.toml":
            py_text, _ = _read_text_safe(mpath)
            if py_text:
                try:
                    py_data = tomllib.loads(py_text)
                    if isinstance(py_data, dict):
                        out.fingerprints.extend(fp.extract_pyproject_config_keys(py_data, context))
                except tomllib.TOMLDecodeError:
                    pass
            if has_uv_lock:
                for u in parse_manifest(mpath, project_key):
                    if u.entity_id not in seen_usage_ids:
                        seen_usage_ids.add(u.entity_id)
                        out.usages.append(u)
                        out.entities.append(entity_for_usage(u.entity_id))
                continue

        if mpath.name == "package.json" and has_pkg_lock:
            for u in parse_manifest(mpath, project_key):
                if u.entity_id not in seen_usage_ids:
                    seen_usage_ids.add(u.entity_id)
                    out.usages.append(u)
                    out.entities.append(entity_for_usage(u.entity_id))
            continue

        if mpath.name == "Cargo.toml" and has_cargo_lock:
            for u in parse_manifest(mpath, project_key):
                if u.entity_id not in seen_usage_ids:
                    seen_usage_ids.add(u.entity_id)
                    out.usages.append(u)
                    out.entities.append(entity_for_usage(u.entity_id))
            continue

        if mpath.name.endswith((".yml", ".yaml")) and ".github" in mpath.parts:
            wf_fps, d_skip = _extract_workflow_fingerprints(mpath, context, known_clis)
            out.dataless_skipped += d_skip
            out.fingerprints.extend(wf_fps)

        for u in parse_manifest(mpath, project_key):
            seen_usage_ids.add(u.entity_id)
            out.usages.append(u)
            out.entities.append(entity_for_usage(u.entity_id))

    intent_fps, d_skip = fp.extract_project_intent_fingerprints(project_dir, context)
    out.dataless_skipped += d_skip
    out.fingerprints.extend(intent_fps)

    manifest_meta.sort(key=lambda item: item["path"])
    return renovate_json, json.dumps(manifest_meta, separators=(",", ":"), sort_keys=True)


def collect(
    ctx: Ctx,
    *,
    extra_roots: Sequence[DiscoveredRoot] = (),
    known_clis: Collection[str] = fp.DEFAULT_KNOWN_CLIS,
) -> ProjectsInventoryOutput:
    """Walk project roots + extra roots and collect projects, usages, and fingerprints."""
    out = ProjectsInventoryOutput()
    candidate_dirs: list[tuple[Path, str]] = []

    for root in ctx.paths.project_roots:
        git_dirs, d_skip = _discover_git_dirs(root, max_depth=4)
        out.dataless_skipped += d_skip
        for gdir in git_dirs:
            candidate_dirs.append((gdir, "active_projects"))

    for extra in extra_roots:
        if extra.path.is_dir():
            candidate_dirs.append((extra.path, extra.root_source))

    seen_worktrees: dict[str, tuple[Path, str]] = {}
    for cand_path, root_source in candidate_dirs:
        has_git = (cand_path / ".git").exists()
        main_wt = _resolve_main_worktree(ctx, cand_path) if has_git else cand_path.resolve()
        wt_key = str(main_wt)
        if wt_key not in seen_worktrees:
            seen_worktrees[wt_key] = (main_wt, root_source)

    existing_aliases, used_priv_nums = _load_existing_project_aliases(ctx.db)

    discovered_items: list[tuple[str, Path, str, str | None, int]] = []
    for main_wt, root_source in seen_worktrees.values():
        has_git = (main_wt / ".git").exists()
        origin_url = _resolve_origin_url(ctx, main_wt) if has_git else None
        last_commit = _resolve_last_commit_at(ctx, main_wt) if has_git else None
        project_key = normalize_origin_url(origin_url, fallback_path=main_wt)

        is_active = (
            1
            if root_source in ("launchd", "dotfiles")
            or _is_commit_within_days(last_commit, ctx.now, days=180)
            else 0
        )
        discovered_items.append((project_key, main_wt, root_source, last_commit, is_active))

    discovered_items.sort(key=lambda item: (0 if item[2] == "active_projects" else 1, item[0]))

    for project_key, main_wt, root_source, last_commit, is_active in discovered_items:
        visibility, alias = _classify_project_visibility_and_alias(
            ctx,
            project_key,
            existing_aliases,
            used_priv_nums,
        )
        renovate_json: str | None = None
        manifest_files_json = "[]"
        if is_active:
            renovate_json, manifest_files_json = _scan_single_project(
                main_wt,
                project_key,
                known_clis,
                out,
            )

        out.projects.append(
            ProjectRecord(
                project_key=project_key,
                display_path=str(main_wt),
                main_worktree=str(main_wt),
                last_commit_at=last_commit,
                active=is_active,
                renovate_json=renovate_json,
                excluded=0,
                root_source=root_source,
                visibility=visibility,
                alias=alias,
                manifest_files_json=manifest_files_json,
            )
        )

    out.fingerprints = fp.merge_fingerprints(out.fingerprints)
    return out


def refresh_manifests(ctx: Ctx) -> list[UsageRecord]:
    """Refresh changed manifests and uv tool receipts for `hourly` (Spec 07 §6, Spec 12 A27).

    Stats recorded `manifest_files_json` paths, `usages.attrs_json.source_file` paths,
    and `uv tool dir` receipts; re-parses changed files via `parse_manifest`; upserts
    updated and newly added usages; and runs inline `classify.visibility` on new entities.
    """
    from .. import db
    from . import classify

    refreshed: list[UsageRecord] = []
    now_iso = ctx.now_iso
    checked_paths: set[str] = set()

    def _apply_parsed_usages(parsed: list[UsageRecord]) -> None:
        for u in parsed:
            refreshed.append(u)
            ent_row = ctx.db.execute(
                "SELECT visibility FROM entities WHERE entity_id = ?",
                (u.entity_id,),
            ).fetchone()
            needs_classify = False
            if ent_row is None:
                ent = entity_for_usage(u.entity_id)
                safe_name = db.scrub_text(ent.name) or ent.entity_id
                safe_repo = db.scrub_text(ent.repo)
                safe_home = db.scrub_text(ent.homepage)
                ctx.db.execute(
                    "INSERT INTO entities("
                    "entity_id, kind, ecosystem, name, repo, homepage, quadrant, "
                    "visibility, created_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        ent.entity_id,
                        ent.kind,
                        ent.ecosystem,
                        safe_name,
                        safe_repo,
                        safe_home,
                        ent.quadrant,
                        ent.visibility,
                        now_iso,
                    ),
                )
                needs_classify = ent.visibility == "unknown"
            elif str(ent_row["visibility"] or "unknown") == "unknown":
                needs_classify = True

            safe_ctx = db.scrub_text(u.context) or u.context
            safe_ver = db.scrub_text(u.version)
            safe_attrs = db.scrub_text(u.attrs_json)
            ctx.db.execute(
                "INSERT INTO usages("
                "entity_id, context, version, direct, first_seen, last_seen, attrs_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(entity_id, context) DO UPDATE SET "
                "version = excluded.version, "
                "direct = excluded.direct, "
                "last_seen = excluded.last_seen, "
                "attrs_json = excluded.attrs_json",
                (
                    u.entity_id,
                    safe_ctx,
                    safe_ver,
                    u.direct,
                    now_iso,
                    now_iso,
                    safe_attrs,
                ),
            )
            ctx.db.commit()

            if needs_classify:
                classify.visibility(ctx, u.entity_id)

    proj_rows = ctx.db.execute(
        "SELECT project_key, manifest_files_json FROM projects WHERE active = 1"
    ).fetchall()

    for prow in proj_rows:
        proj_key = str(prow["project_key"])
        raw_json = str(prow["manifest_files_json"] or "[]")
        try:
            entries = json.loads(raw_json)
        except json.JSONDecodeError:
            entries = []
        if not isinstance(entries, list):
            entries = []

        updated_entries: list[dict[str, Any]] = []
        changed_any = False
        for item in entries:
            if not isinstance(item, dict) or not item.get("path"):
                continue
            mpath = Path(str(item["path"]))
            checked_paths.add(str(mpath))
            st, dataless = _stat_file(mpath)
            if dataless or st is None:
                updated_entries.append(item)
                continue
            try:
                cur_sha = hashlib.sha256(mpath.read_bytes()).hexdigest()
            except OSError:
                updated_entries.append(item)
                continue

            prev_mtime = item.get("mtime")
            prev_size = item.get("size")
            prev_sha = item.get("sha256")
            is_changed = (
                prev_mtime != st.st_mtime
                or prev_size != st.st_size
                or (bool(prev_sha) and prev_sha != cur_sha)
            )
            if is_changed:
                changed_any = True
                _apply_parsed_usages(parse_manifest(mpath, proj_key))
            updated_entries.append(
                {
                    "path": str(mpath),
                    "mtime": st.st_mtime,
                    "size": st.st_size,
                    "sha256": cur_sha,
                }
            )

        if changed_any:
            ctx.db.execute(
                "UPDATE projects SET manifest_files_json = ? WHERE project_key = ?",
                (json.dumps(updated_entries, separators=(",", ":"), sort_keys=True), proj_key),
            )
            ctx.db.commit()

    # Check any source_file recorded on usages not already covered by projects.manifest_files_json
    usage_sf_rows = ctx.db.execute(
        "SELECT DISTINCT context, attrs_json FROM usages WHERE attrs_json IS NOT NULL"
    ).fetchall()
    extra_sf_state_raw = db.kv_get(ctx.db, "hourly_source_files_json") or "{}"
    try:
        extra_sf_state: dict[str, dict[str, Any]] = json.loads(extra_sf_state_raw)
        if not isinstance(extra_sf_state, dict):
            extra_sf_state = {}
    except json.JSONDecodeError:
        extra_sf_state = {}

    extra_changed = False
    for urow in usage_sf_rows:
        uctx = str(urow["context"])
        try:
            uattrs = json.loads(str(urow["attrs_json"]))
        except json.JSONDecodeError:
            continue
        if not isinstance(uattrs, dict) or not uattrs.get("source_file"):
            continue
        sf_str = str(uattrs["source_file"])
        if sf_str in checked_paths:
            continue
        checked_paths.add(sf_str)
        sf_path = Path(sf_str)
        st, dataless = _stat_file(sf_path)
        if dataless or st is None:
            continue
        try:
            cur_sha = hashlib.sha256(sf_path.read_bytes()).hexdigest()
        except OSError:
            continue
        prev = extra_sf_state.get(sf_str)
        if (
            prev is None
            or prev.get("mtime") != st.st_mtime
            or prev.get("size") != st.st_size
            or prev.get("sha256") != cur_sha
        ):
            extra_changed = True
            pkey = uctx[len("project:") :] if uctx.startswith("project:") else uctx
            _apply_parsed_usages(parse_manifest(sf_path, pkey))
            extra_sf_state[sf_str] = {
                "mtime": st.st_mtime,
                "size": st.st_size,
                "sha256": cur_sha,
            }

    # Check uv tool dir receipts (`{home}/.local/share/uv/tools/*/uv-receipt.toml`)
    uv_tools_dir = ctx.paths.home / ".local" / "share" / "uv" / "tools"
    if uv_tools_dir.is_dir():
        for receipt in sorted(uv_tools_dir.glob("*/uv-receipt.toml")):
            rec_str = str(receipt.resolve())
            if rec_str in checked_paths:
                continue
            checked_paths.add(rec_str)
            st, dataless = _stat_file(receipt)
            if dataless or st is None:
                continue
            try:
                cur_sha = hashlib.sha256(receipt.read_bytes()).hexdigest()
            except OSError:
                continue
            prev = extra_sf_state.get(rec_str)
            if (
                prev is None
                or prev.get("mtime") != st.st_mtime
                or prev.get("size") != st.st_size
                or prev.get("sha256") != cur_sha
            ):
                extra_changed = True
                _apply_parsed_usages(parse_manifest(receipt, "system:uv-tool"))
                extra_sf_state[rec_str] = {
                    "mtime": st.st_mtime,
                    "size": st.st_size,
                    "sha256": cur_sha,
                }

    if extra_changed:
        db.kv_set(
            ctx.db,
            "hourly_source_files_json",
            json.dumps(extra_sf_state, separators=(",", ":"), sort_keys=True),
        )

    return refreshed
