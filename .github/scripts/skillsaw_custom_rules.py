#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# ///
"""Skillsaw custom rule and standalone CLI for 3-manifest plugin metadata sync and version bumps."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

AGENT_PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
AGENT_MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
EXPECTED_AUTHOR: dict[str, str] = {"name": "wgordon17"}
MCP_PLUGINS: frozenset[str] = frozenset({"jira", "fetchaller-mcp", "github-mcp", "chai-bot"})
MCP_SERVER_FIELDS: tuple[str, ...] = ("command", "args", "env", "url", "headers")
SUPPLY_CHAIN_MARKER = ".khepri-supply-chain-blocked"
SUPPLY_CHAIN_ABORT_MSG = (
    "[SUPPLY_CHAIN_BLOCK] Aborting manifest fix: .khepri-supply-chain-blocked present"
)
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
PLUGIN_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class ManifestViolation:
    """Represents a manifest sync or version-bump violation."""

    file_path: Path
    message: str


def parse_semver(v: str) -> tuple[int, int, int]:
    """Parse a strict X.Y.Z semver string into an integer 3-tuple."""
    match = SEMVER_RE.match(v.strip())
    if not match:
        raise ValueError(f"Invalid semver version: {v!r}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def bump_patch(v: str) -> str:
    """Increment the patch segment of an X.Y.Z semver string."""
    major, minor, patch = parse_semver(v)
    return f"{major}.{minor}.{patch + 1}"


def _validate_base_ref(base_ref: str) -> None:
    """Ensure base_ref is a non-empty git revision that cannot inject git flags."""
    if not base_ref or base_ref.startswith("-") or any(ch.isspace() for ch in base_ref):
        raise ValueError(f"Invalid git base_ref: {base_ref!r}")


def _validate_plugin_name(plugin_name: str) -> None:
    """Validate that a plugin name is a safe single path segment."""
    if not PLUGIN_NAME_RE.match(plugin_name):
        raise ValueError(f"Invalid plugin name: {plugin_name!r}")


def _validate_repo_path(repo_root: Path, path: Path) -> Path:
    """Verify that path is not a symlink and resolves inside repo_root."""
    resolved_root = repo_root.resolve()
    if path.is_symlink():
        raise ValueError(f"Refusing symlinked manifest path: {path}")
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise ValueError(f"Path escapes repo_root ({resolved_root}): {path} -> {resolved_path}")
    return resolved_path


def _run_git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run git in repo_root with an explicit argument list (shell=False)."""
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )


def _load_registered_plugins(repo_root: Path) -> list[dict[str, Any]]:
    """Return the list of plugin entries from .claude-plugin/marketplace.json."""
    marketplace_path = _validate_repo_path(
        repo_root, repo_root / ".claude-plugin" / "marketplace.json"
    )
    if not marketplace_path.exists():
        return []
    data = json.loads(marketplace_path.read_text(encoding="utf-8"))
    plugins = data.get("plugins", [])
    return plugins if isinstance(plugins, list) else []


def get_modified_plugins(repo_root: Path, base_ref: str = "origin/main") -> set[str]:
    """Return set of marketplace plugins with non-test file changes relative to base_ref."""
    resolved_root = repo_root.resolve()
    _validate_base_ref(base_ref)

    registered = {
        str(entry["name"])
        for entry in _load_registered_plugins(resolved_root)
        if isinstance(entry, dict) and "name" in entry
    }
    if not registered:
        return set()

    verify_proc = _run_git(resolved_root, ["rev-parse", "--verify", base_ref])
    if verify_proc.returncode != 0:
        return set()

    changed_paths: set[str] = set()
    for git_args in (
        ["diff", "--name-only", f"{base_ref}...HEAD"],
        ["diff", "--name-only", "HEAD"],
        ["ls-files", "--others", "--exclude-standard"],
    ):
        proc = _run_git(resolved_root, git_args)
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                stripped = line.strip()
                if stripped:
                    changed_paths.add(stripped)

    modified: set[str] = set()
    for rel_path in changed_paths:
        parts = rel_path.split("/", 1)
        if len(parts) != 2:
            continue
        plugin_name, rest = parts
        if plugin_name in registered and rest != "tests" and not rest.startswith("tests/"):
            modified.add(plugin_name)

    return modified


def get_base_plugin_version(
    repo_root: Path,
    plugin_name: str,
    base_ref: str = "origin/main",
) -> str | None:
    """Read <plugin>/.claude-plugin/plugin.json version from base_ref."""
    resolved_root = repo_root.resolve()
    _validate_base_ref(base_ref)
    _validate_plugin_name(plugin_name)

    proc = _run_git(resolved_root, ["show", f"{base_ref}:{plugin_name}/.claude-plugin/plugin.json"])
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    version = data.get("version")
    return version if isinstance(version, str) else None


def get_base_plugin_description(
    repo_root: Path,
    plugin_name: str,
    base_ref: str = "origin/main",
) -> str | None:
    """Read <plugin>/.claude-plugin/plugin.json description from merge-base(base_ref, HEAD)."""
    resolved_root = repo_root.resolve()
    _validate_base_ref(base_ref)
    _validate_plugin_name(plugin_name)

    mb_proc = _run_git(resolved_root, ["merge-base", base_ref, "HEAD"])
    ref_to_use = (
        mb_proc.stdout.strip() if mb_proc.returncode == 0 and mb_proc.stdout.strip() else base_ref
    )
    _validate_base_ref(ref_to_use)

    proc = _run_git(
        resolved_root,
        ["show", f"{ref_to_use}:{plugin_name}/.claude-plugin/plugin.json"],
    )
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    desc = data.get("description")
    return desc if isinstance(desc, str) else None


def _batch_read_plugin_manifests(
    repo_root: Path,
    ref_sha: str,
    plugin_names: list[str],
) -> dict[str, dict[str, Any]]:
    """Read <plugin>/.claude-plugin/plugin.json for all plugin_names at ref_sha in one git call."""
    if not plugin_names:
        return {}
    _validate_base_ref(ref_sha)
    for name in plugin_names:
        _validate_plugin_name(name)

    stdin_payload = "".join(
        f"{ref_sha}:{name}/.claude-plugin/plugin.json\n" for name in plugin_names
    ).encode("utf-8")
    proc = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=repo_root,
        input=stdin_payload,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return {}

    raw = proc.stdout
    pos = 0
    total = len(raw)
    manifests: dict[str, dict[str, Any]] = {}

    for name in plugin_names:
        nl_idx = raw.find(b"\n", pos)
        if nl_idx == -1:
            break
        header = raw[pos:nl_idx]
        pos = nl_idx + 1
        if header.endswith(b" missing"):
            continue
        parts = header.split()
        if len(parts) < 3:
            break
        try:
            size = int(parts[2])
        except ValueError:
            break
        content_end = pos + size
        if content_end > total:
            break
        blob_bytes = raw[pos:content_end]
        pos = content_end
        if pos < total and raw[pos : pos + 1] == b"\n":
            pos += 1
        if parts[1] != b"blob":
            continue
        try:
            parsed = json.loads(blob_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            manifests[name] = parsed

    return manifests


def _load_base_plugin_metadata(
    repo_root: Path,
    plugin_names: list[str],
    base_ref: str = "origin/main",
) -> tuple[dict[str, str | None], dict[str, str | None]]:
    """Batch-load base versions (at base_ref) and base descriptions (at merge-base)."""
    resolved_root = repo_root.resolve()
    _validate_base_ref(base_ref)
    for name in plugin_names:
        _validate_plugin_name(name)

    if not plugin_names:
        return {}, {}

    verify_proc = _run_git(resolved_root, ["rev-parse", "--verify", "--quiet", base_ref])
    base_sha = verify_proc.stdout.strip()
    if verify_proc.returncode != 0 or not base_sha:
        empty: dict[str, str | None] = {name: None for name in plugin_names}
        return dict(empty), dict(empty)

    mb_proc = _run_git(resolved_root, ["merge-base", base_ref, "HEAD"])
    merge_base = (
        mb_proc.stdout.strip() if mb_proc.returncode == 0 and mb_proc.stdout.strip() else base_sha
    )

    base_manifests = _batch_read_plugin_manifests(resolved_root, base_sha, plugin_names)
    if merge_base == base_sha:
        mb_manifests = base_manifests
    else:
        mb_manifests = _batch_read_plugin_manifests(resolved_root, merge_base, plugin_names)

    base_versions: dict[str, str | None] = {}
    base_descriptions: dict[str, str | None] = {}
    for name in plugin_names:
        ver = base_manifests.get(name, {}).get("version")
        base_versions[name] = ver if isinstance(ver, str) else None
        desc = mb_manifests.get(name, {}).get("description")
        base_descriptions[name] = desc if isinstance(desc, str) else None

    return base_versions, base_descriptions


def _update_marketplace_plugin_in_place(
    marketplace_text: str,
    plugin_name: str,
    *,
    target_version: str | None = None,
    target_description: str | None = None,
    target_author: dict[str, str] | None = None,
) -> str:
    """Update a single plugin object's fields in marketplace_text while preserving formatting."""
    match = re.search(r'"plugins"\s*:\s*\[', marketplace_text)
    if not match:
        return marketplace_text

    idx = match.end()
    n = len(marketplace_text)
    in_string = False
    escape = False
    brace_depth = 0
    bracket_depth = 1
    obj_start: int | None = None

    while idx < n and bracket_depth > 0:
        ch = marketplace_text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                if brace_depth == 0:
                    obj_start = idx
                brace_depth += 1
            elif ch == "}":
                brace_depth -= 1
                if brace_depth == 0 and obj_start is not None:
                    obj_end = idx + 1
                    obj_text = marketplace_text[obj_start:obj_end]
                    obj_data = json.loads(obj_text)
                    if obj_data.get("name") == plugin_name:
                        new_obj_text = obj_text
                        if target_version is not None:
                            ver_json = json.dumps(target_version, ensure_ascii=False)
                            new_obj_text = re.sub(
                                r'("version"\s*:\s*)"(?:\\.|[^"\\])*"',
                                lambda m, val=ver_json: f"{m.group(1)}{val}",
                                new_obj_text,
                                count=1,
                            )
                        if target_description is not None:
                            desc_json = json.dumps(target_description, ensure_ascii=False)
                            new_obj_text = re.sub(
                                r'("description"\s*:\s*)"(?:\\.|[^"\\])*"',
                                lambda m, val=desc_json: f"{m.group(1)}{val}",
                                new_obj_text,
                                count=1,
                            )
                        if target_author is not None:
                            author_json = json.dumps(target_author, ensure_ascii=False)
                            if re.search(r'"author"\s*:', new_obj_text):
                                new_obj_text = re.sub(
                                    r'("author"\s*:\s*)(\{[^{}]*\}|"(?:\\.|[^"\\])*"|null)',
                                    lambda m, val=author_json: f"{m.group(1)}{val}",
                                    new_obj_text,
                                    count=1,
                                )
                            else:
                                new_obj_text = re.sub(
                                    r"\s*\}$",
                                    f', "author": {author_json}}}',
                                    new_obj_text,
                                    count=1,
                                )
                        return (
                            marketplace_text[:obj_start] + new_obj_text + marketplace_text[obj_end:]
                        )
                    obj_start = None
            elif ch == "[" and brace_depth == 0:
                bracket_depth += 1
            elif ch == "]" and brace_depth == 0:
                bracket_depth -= 1
        idx += 1

    return marketplace_text


def _build_expected_agent_mcp(dot_mcp_data: dict[str, Any]) -> dict[str, Any]:
    """Construct expected Agent Plugins v1 mcp.json payload from .mcp.json payload."""
    expected_servers: dict[str, Any] = {}
    dot_servers = dot_mcp_data.get("mcpServers", {})
    if isinstance(dot_servers, dict):
        for srv_name, dot_srv in dot_servers.items():
            if not isinstance(dot_srv, dict):
                continue
            expected_type = "streamable-http" if dot_srv.get("type") == "http" else "stdio"
            srv_entry: dict[str, Any] = {"type": expected_type}
            for field_name in MCP_SERVER_FIELDS:
                if field_name in dot_srv:
                    srv_entry[field_name] = dot_srv[field_name]
            expected_servers[srv_name] = srv_entry
    return {
        "$schema": AGENT_MCP_SCHEMA,
        "mcpServers": expected_servers,
    }


def _mcp_configs_match(dot_mcp_data: dict[str, Any], root_mcp_data: dict[str, Any]) -> bool:
    """Return True if root mcp.json semantically matches .mcp.json."""
    if root_mcp_data.get("$schema") != AGENT_MCP_SCHEMA:
        return False
    dot_servers = dot_mcp_data.get("mcpServers", {})
    root_servers = root_mcp_data.get("mcpServers", {})
    if not isinstance(dot_servers, dict) or not isinstance(root_servers, dict):
        return False
    if set(dot_servers.keys()) != set(root_servers.keys()):
        return False
    for srv_name, dot_srv in dot_servers.items():
        root_srv = root_servers.get(srv_name)
        if not isinstance(dot_srv, dict) or not isinstance(root_srv, dict):
            return False
        expected_type = "streamable-http" if dot_srv.get("type") == "http" else "stdio"
        if root_srv.get("type") != expected_type:
            return False
        for field_name in MCP_SERVER_FIELDS:
            if dot_srv.get(field_name) != root_srv.get(field_name):
                return False
    return True


def compute_manifest_fixes(
    repo_root: Path,
    *,
    base_ref: str = "origin/main",
    explicit_bump_plugins: set[str] | None = None,
) -> tuple[list[ManifestViolation], dict[Path, tuple[str, str, str]]]:
    """Compute manifest violations and semantic file fixes across all registered plugins."""
    resolved_root = repo_root.resolve()
    _validate_base_ref(base_ref)
    explicit_bumps = set(explicit_bump_plugins or ())
    for name in explicit_bumps:
        _validate_plugin_name(name)

    marketplace_path = _validate_repo_path(
        resolved_root, resolved_root / ".claude-plugin" / "marketplace.json"
    )
    if not marketplace_path.exists():
        if explicit_bumps:
            raise ValueError(f"Marketplace manifest not found: {marketplace_path}")
        return [], {}

    original_marketplace_text = marketplace_path.read_text(encoding="utf-8")
    current_marketplace_text = original_marketplace_text
    marketplace_data = json.loads(original_marketplace_text)
    marketplace_plugins: list[dict[str, Any]] = marketplace_data.get("plugins", [])

    plugin_names = [str(p["name"]) for p in marketplace_plugins if "name" in p]
    for name in plugin_names:
        _validate_plugin_name(name)
    registered_names = set(plugin_names)
    unknown_bumps = explicit_bumps - registered_names
    if unknown_bumps:
        raise ValueError(f"Unknown plugin(s) in --bump-plugin: {', '.join(sorted(unknown_bumps))}")

    modified_plugins = get_modified_plugins(resolved_root, base_ref=base_ref)
    base_versions, base_descriptions = _load_base_plugin_metadata(
        resolved_root, plugin_names, base_ref=base_ref
    )
    violations: list[ManifestViolation] = []
    fixes: dict[Path, tuple[str, str, str]] = {}
    marketplace_dirty_plugins: list[str] = []

    for mkt_entry in marketplace_plugins:
        plugin_name = str(mkt_entry["name"])

        claude_path = _validate_repo_path(
            resolved_root, resolved_root / plugin_name / ".claude-plugin" / "plugin.json"
        )
        agent_path = _validate_repo_path(resolved_root, resolved_root / plugin_name / "plugin.json")

        if not claude_path.exists():
            violations.append(
                ManifestViolation(
                    marketplace_path,
                    f"{plugin_name}: listed in marketplace.json but {claude_path} is missing",
                )
            )
            continue

        claude_orig_text = claude_path.read_text(encoding="utf-8")
        claude_data: dict[str, Any] = json.loads(claude_orig_text)

        if agent_path.exists():
            agent_orig_text = agent_path.read_text(encoding="utf-8")
            agent_data: dict[str, Any] = json.loads(agent_orig_text)
        else:
            agent_orig_text = ""
            agent_data = {}
            violations.append(
                ManifestViolation(agent_path, f"{plugin_name}: missing root plugin.json")
            )

        claude_desc = str(claude_data.get("description", ""))
        agent_desc = str(agent_data.get("description", ""))
        mkt_desc = str(mkt_entry.get("description", ""))
        base_desc = base_descriptions.get(plugin_name)

        if base_desc is not None:
            if claude_desc != base_desc:
                target_desc = claude_desc
            elif agent_desc != base_desc:
                target_desc = agent_desc
            elif mkt_desc != base_desc:
                target_desc = mkt_desc
            else:
                target_desc = claude_desc
        else:
            target_desc = claude_desc

        surface_dirty = False
        if base_desc is not None and target_desc != base_desc:
            surface_dirty = True

        if claude_desc != target_desc:
            violations.append(
                ManifestViolation(
                    claude_path,
                    f"{plugin_name}: .claude-plugin/plugin.json description mismatch",
                )
            )
            surface_dirty = True
        if agent_desc != target_desc:
            violations.append(
                ManifestViolation(
                    agent_path,
                    f"{plugin_name}: plugin.json description mismatch",
                )
            )
            surface_dirty = True
        if mkt_desc != target_desc:
            violations.append(
                ManifestViolation(
                    marketplace_path,
                    f"{plugin_name}: marketplace.json description mismatch",
                )
            )
            surface_dirty = True

        if claude_data.get("name") != plugin_name:
            violations.append(
                ManifestViolation(
                    claude_path,
                    f"{plugin_name}: .claude-plugin/plugin.json name={claude_data.get('name')!r}",
                )
            )
            surface_dirty = True
        if agent_data.get("name") != plugin_name:
            violations.append(
                ManifestViolation(
                    agent_path,
                    f"{plugin_name}: plugin.json name={agent_data.get('name')!r}",
                )
            )
            surface_dirty = True

        if claude_data.get("author") != EXPECTED_AUTHOR:
            violations.append(
                ManifestViolation(
                    claude_path,
                    f"{plugin_name}: .claude-plugin/plugin.json "
                    f"author={claude_data.get('author')!r}",
                )
            )
            surface_dirty = True
        if agent_data.get("author") != EXPECTED_AUTHOR:
            violations.append(
                ManifestViolation(
                    agent_path,
                    f"{plugin_name}: plugin.json author={agent_data.get('author')!r}",
                )
            )
            surface_dirty = True
        if mkt_entry.get("author") != EXPECTED_AUTHOR:
            violations.append(
                ManifestViolation(
                    marketplace_path,
                    f"{plugin_name}: marketplace.json author={mkt_entry.get('author')!r}",
                )
            )
            surface_dirty = True

        if agent_data.get("$schema") != AGENT_PLUGIN_SCHEMA:
            violations.append(
                ManifestViolation(
                    agent_path,
                    f"{plugin_name}: plugin.json $schema={agent_data.get('$schema')!r}",
                )
            )
            surface_dirty = True

        if plugin_name == "dev-guard":
            hooks_path = _validate_repo_path(
                resolved_root, resolved_root / "dev-guard" / "hooks" / "hooks.json"
            )
            if hooks_path.exists():
                hooks_orig_text = hooks_path.read_text(encoding="utf-8")
                hooks_data = json.loads(hooks_orig_text)
                hooks_desc = str(hooks_data.get("description", ""))
                hooks_suffix = hooks_desc.rsplit(",", 1)[-1].strip()
                target_suffix = target_desc.rsplit(",", 1)[-1].strip()
                if hooks_suffix != target_suffix:
                    violations.append(
                        ManifestViolation(
                            hooks_path,
                            f"dev-guard: hooks.json description suffix "
                            f"{hooks_suffix!r} != {target_suffix!r}",
                        )
                    )
                    surface_dirty = True
                    if "," in hooks_desc:
                        prefix, _ = hooks_desc.rsplit(",", 1)
                        new_hooks_desc = f"{prefix}, {target_suffix}"
                    else:
                        new_hooks_desc = target_suffix
                    hooks_data["description"] = new_hooks_desc
                    fixes[hooks_path] = (
                        hooks_orig_text,
                        json.dumps(hooks_data, indent=2, ensure_ascii=False) + "\n",
                        "Sync dev-guard/hooks/hooks.json description suffix",
                    )

        dot_mcp_path = _validate_repo_path(resolved_root, resolved_root / plugin_name / ".mcp.json")
        if plugin_name in MCP_PLUGINS or dot_mcp_path.exists():
            root_mcp_path = _validate_repo_path(
                resolved_root, resolved_root / plugin_name / "mcp.json"
            )
            if dot_mcp_path.exists():
                dot_mcp_data = json.loads(dot_mcp_path.read_text(encoding="utf-8"))
                root_mcp_orig = (
                    root_mcp_path.read_text(encoding="utf-8") if root_mcp_path.exists() else ""
                )
                root_mcp_data = json.loads(root_mcp_orig) if root_mcp_orig else {}
                if not _mcp_configs_match(dot_mcp_data, root_mcp_data):
                    violations.append(
                        ManifestViolation(
                            root_mcp_path,
                            f"{plugin_name}: mcp.json out of sync with .mcp.json",
                        )
                    )
                    surface_dirty = True
                    expected_mcp = _build_expected_agent_mcp(dot_mcp_data)
                    fixes[root_mcp_path] = (
                        root_mcp_orig,
                        json.dumps(expected_mcp, indent=2, ensure_ascii=False) + "\n",
                        f"Sync {plugin_name}/mcp.json with .mcp.json",
                    )

        claude_ver = str(claude_data.get("version", "0.0.0"))
        agent_ver = str(agent_data.get("version", claude_ver))
        mkt_ver = str(mkt_entry.get("version", claude_ver))
        target_version = max((claude_ver, agent_ver, mkt_ver), key=parse_semver)
        base_version = base_versions.get(plugin_name)

        reported_bump_violation = False
        if plugin_name in explicit_bumps:
            floor_ver = (
                max((target_version, base_version), key=parse_semver)
                if base_version is not None
                else target_version
            )
            target_version = bump_patch(floor_ver)
        elif (
            (plugin_name in modified_plugins or surface_dirty)
            and base_version is not None
            and parse_semver(target_version) <= parse_semver(base_version)
        ):
            target_version = bump_patch(base_version)
            violations.append(
                ManifestViolation(
                    claude_path,
                    f"{plugin_name}: modified relative to {base_ref} without version bump "
                    f"({claude_ver} <= {base_version})",
                )
            )
            reported_bump_violation = True

        if claude_ver != target_version and not reported_bump_violation:
            violations.append(
                ManifestViolation(
                    claude_path,
                    f"{plugin_name}: .claude-plugin/plugin.json "
                    f"version {claude_ver!r} != {target_version!r}",
                )
            )
        if agent_ver != target_version:
            violations.append(
                ManifestViolation(
                    agent_path,
                    f"{plugin_name}: plugin.json version {agent_ver!r} != {target_version!r}",
                )
            )
        if mkt_ver != target_version:
            violations.append(
                ManifestViolation(
                    marketplace_path,
                    f"{plugin_name}: marketplace.json version {mkt_ver!r} != {target_version!r}",
                )
            )

        claude_needs_update = (
            claude_data.get("name") != plugin_name
            or claude_data.get("version") != target_version
            or claude_data.get("description") != target_desc
            or claude_data.get("author") != EXPECTED_AUTHOR
        )
        if claude_needs_update:
            new_claude_data = dict(claude_data)
            new_claude_data["name"] = plugin_name
            new_claude_data["description"] = target_desc
            new_claude_data["version"] = target_version
            new_claude_data["author"] = dict(EXPECTED_AUTHOR)
            fixes[claude_path] = (
                claude_orig_text,
                json.dumps(new_claude_data, indent=2, ensure_ascii=False) + "\n",
                f"Sync {plugin_name}/.claude-plugin/plugin.json (v{target_version})",
            )

        agent_needs_update = (
            not agent_path.exists()
            or agent_data.get("$schema") != AGENT_PLUGIN_SCHEMA
            or agent_data.get("name") != plugin_name
            or agent_data.get("version") != target_version
            or agent_data.get("description") != target_desc
            or agent_data.get("author") != EXPECTED_AUTHOR
        )
        if agent_needs_update:
            new_agent_data: dict[str, Any] = {
                "$schema": AGENT_PLUGIN_SCHEMA,
                "name": plugin_name,
                "version": target_version,
                "description": target_desc,
                "author": dict(EXPECTED_AUTHOR),
            }
            for k, v in agent_data.items():
                if k not in new_agent_data:
                    new_agent_data[k] = v
            fixes[agent_path] = (
                agent_orig_text,
                json.dumps(new_agent_data, indent=2, ensure_ascii=False) + "\n",
                f"Sync {plugin_name}/plugin.json (v{target_version})",
            )

        mkt_ver_changed = mkt_entry.get("version") != target_version
        mkt_desc_changed = mkt_entry.get("description") != target_desc
        mkt_author_changed = mkt_entry.get("author") != EXPECTED_AUTHOR
        if mkt_ver_changed or mkt_desc_changed or mkt_author_changed:
            current_marketplace_text = _update_marketplace_plugin_in_place(
                current_marketplace_text,
                plugin_name,
                target_version=target_version if mkt_ver_changed else None,
                target_description=target_desc if mkt_desc_changed else None,
                target_author=EXPECTED_AUTHOR if mkt_author_changed else None,
            )
            marketplace_dirty_plugins.append(plugin_name)

    if current_marketplace_text != original_marketplace_text:
        fixes[marketplace_path] = (
            original_marketplace_text,
            current_marketplace_text,
            f"Sync .claude-plugin/marketplace.json ({', '.join(marketplace_dirty_plugins)})",
        )

    return violations, fixes


if TYPE_CHECKING:

    class Severity(Enum):
        ERROR = "error"
        WARNING = "warning"
        INFO = "info"

    class AutofixConfidence(Enum):
        SAFE = "safe"
        SUGGEST = "suggest"

    @dataclass
    class RuleViolation:
        rule_id: str
        severity: Severity
        message: str
        file_path: Path | None = None
        line: int | None = None
        block: Any = None
        source: str = "custom"
        value: float | None = None
        metric: str | None = None
        fixable: bool | None = None
        fix_confidence: AutofixConfidence | None = None
        fingerprint_discriminator: str | None = None
        constituents: tuple[RuleViolation, ...] = ()
        consolidated_into: RuleViolation | None = None
        fix_data: dict[str, str] | None = None

    @dataclass
    class AutofixResult:
        rule_id: str
        file_path: Path
        confidence: AutofixConfidence
        original_content: str
        fixed_content: str
        description: str
        violations_fixed: list[RuleViolation] = field(default_factory=list)
        rename_from: Path | None = None
        on_apply: Callable[[], None] | None = None

    class Rule:
        autofix_confidence: AutofixConfidence | None = None
        _source: str = "custom"

        def __init__(self, config: dict[str, Any] | None = None) -> None: ...

        @property
        def rule_id(self) -> str: ...

        @property
        def description(self) -> str: ...

        def default_severity(self) -> Severity: ...

        @property
        def severity(self) -> Severity: ...

        def violation(
            self,
            message: str,
            file_path: Path | None = None,
            line: int | None = None,
            severity: Severity | None = None,
            fixable: bool | None = None,
            fix_confidence: AutofixConfidence | None = None,
            **kwargs: Any,
        ) -> RuleViolation: ...

else:
    try:
        from skillsaw.rule import (
            AutofixConfidence,
            AutofixResult,
            Rule,
            RuleViolation,
            Severity,
        )
    except ImportError:

        class Severity(Enum):
            ERROR = "error"
            WARNING = "warning"
            INFO = "info"

        class AutofixConfidence(Enum):
            SAFE = "safe"
            SUGGEST = "suggest"

        @dataclass
        class RuleViolation:
            rule_id: str
            severity: Any
            message: str
            file_path: Path | None = None
            line: int | None = None
            block: Any = None
            source: str = "custom"
            value: float | None = None
            metric: str | None = None
            fixable: bool | None = None
            fix_confidence: Any = None
            fingerprint_discriminator: str | None = None
            constituents: tuple[Any, ...] = ()
            consolidated_into: Any = None
            fix_data: dict[str, str] | None = None

        @dataclass
        class AutofixResult:
            rule_id: str
            file_path: Path
            confidence: Any
            original_content: str
            fixed_content: str
            description: str
            violations_fixed: list[Any] = field(default_factory=list)
            rename_from: Path | None = None
            on_apply: Callable[[], None] | None = None

        class Rule:
            autofix_confidence: Any = None
            _source: str = "custom"

            def __init__(self, config: dict[str, Any] | None = None) -> None:
                self.config = config or {}
                self._enabled = self.config.get("enabled", True)
                sev_str = self.config.get("severity", self.default_severity().value)
                self._severity = (
                    Severity(sev_str) if sev_str is not None else self.default_severity()
                )

            @property
            def rule_id(self) -> str:
                raise NotImplementedError

            @property
            def description(self) -> str:
                raise NotImplementedError

            def default_severity(self) -> Any:
                return Severity.ERROR

            @property
            def severity(self) -> Any:
                return self._severity

            def violation(
                self,
                message: str,
                file_path: Path | None = None,
                line: int | None = None,
                severity: Any = None,
                fixable: bool | None = None,
                fix_confidence: Any = None,
                **kwargs: Any,
            ) -> Any:
                return RuleViolation(
                    rule_id=self.rule_id,
                    severity=severity if severity is not None else self.severity,
                    message=message,
                    file_path=file_path,
                    line=line,
                    source=self._source,
                    fixable=True if fixable is None else fixable,
                    fix_confidence=fix_confidence or self.autofix_confidence,
                )


class PluginManifestSyncRule(Rule):
    """Synchronize plugin version, description, author, and MCP metadata across manifests."""

    autofix_confidence = AutofixConfidence.SAFE
    _last_fix_cache: tuple[Path, dict[Path, tuple[str, str, str]]] | None = None

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._last_fix_cache: tuple[Path, dict[Path, tuple[str, str, str]]] | None = None

    @property
    def rule_id(self) -> str:
        return "plugin-manifest-sync"

    @property
    def description(self) -> str:
        return (
            "Synchronize plugin version, description, author, and MCP metadata "
            "across dual manifests and marketplace.json"
        )

    def default_severity(self) -> Severity:
        return Severity.ERROR

    def check(self, context: Any) -> list[RuleViolation]:
        repo_root = Path(context.root_path).resolve()
        manifest_violations, file_fixes = compute_manifest_fixes(repo_root)
        self._last_fix_cache = (repo_root, file_fixes)
        return [
            self.violation(v.message, file_path=v.file_path, fixable=True)
            for v in manifest_violations
        ]

    def fix(self, context: Any, violations: list[RuleViolation]) -> list[AutofixResult]:
        repo_root = Path(context.root_path).resolve()
        if self._last_fix_cache is not None and self._last_fix_cache[0] == repo_root:
            file_fixes = self._last_fix_cache[1]
            self._last_fix_cache = None
        else:
            self._last_fix_cache = None
            _, file_fixes = compute_manifest_fixes(repo_root)
        if not file_fixes:
            return []

        results: list[AutofixResult] = []
        claimed_ids: set[int] = set()

        for path, (orig, fixed, desc) in file_fixes.items():
            matched = [v for v in violations if v.file_path == path]
            for v in matched:
                claimed_ids.add(id(v))
            results.append(
                AutofixResult(
                    rule_id=self.rule_id,
                    file_path=path,
                    confidence=AutofixConfidence.SAFE,
                    original_content=orig,
                    fixed_content=fixed,
                    description=desc,
                    violations_fixed=matched,
                )
            )

        unclaimed = [v for v in violations if id(v) not in claimed_ids]
        if unclaimed and results:
            results[0].violations_fixed.extend(unclaimed)

        return results


def _atomic_write_text(repo_root: Path, target_path: Path, content: str) -> None:
    """Atomically write content to target_path inside repo_root."""
    validated_target = _validate_repo_path(repo_root, target_path)
    validated_target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=validated_target.parent,
        prefix=f".{validated_target.name}.",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_path, validated_target)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for standalone manifest checks, fixes, and explicit bumps."""
    parser = argparse.ArgumentParser(
        description="Check or synchronize 3-manifest plugin metadata and patch versions."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify manifests are synchronized and modified plugins have bumped versions.",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Synchronize manifests and auto-bump patch versions for modified plugins.",
    )
    parser.add_argument(
        "--bump-plugin",
        action="append",
        default=[],
        metavar="PLUGIN",
        help="Explicitly bump patch version for PLUGIN (repeatable) and sync manifests.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root directory (default: current working directory).",
    )
    parser.add_argument(
        "--base-ref",
        default="origin/main",
        help="Git base reference for modified-plugin comparison (default: origin/main).",
    )
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    explicit_bumps = set(args.bump_plugin)
    write_mode = bool(args.fix or explicit_bumps)

    if write_mode and (repo_root / SUPPLY_CHAIN_MARKER).exists():
        print(SUPPLY_CHAIN_ABORT_MSG, file=sys.stderr)
        return 1

    try:
        violations, fixes = compute_manifest_fixes(
            repo_root,
            base_ref=args.base_ref,
            explicit_bump_plugins=explicit_bumps,
        )
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    if write_mode:
        for path, (_orig, fixed_content, _desc) in fixes.items():
            _atomic_write_text(repo_root, path, fixed_content)
        return 0

    if violations:
        for v in violations:
            rel = (
                v.file_path.relative_to(repo_root)
                if v.file_path.is_relative_to(repo_root)
                else v.file_path
            )
            print(f"[plugin-manifest-sync] {rel}: {v.message}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
