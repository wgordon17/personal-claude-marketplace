#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["python-frontmatter>=1.1.0"]
# ///
"""generate-atlas.py — Deterministic ATLAS.md inventory generator.

Parses marketplace.json, plugin frontmatter (skills, agents, commands),
hooks.json, .mcp.json, and reference docs to produce a complete ATLAS.md.

Usage:
    uv run .claude/commands/generate-atlas.py              # write ATLAS.md
    uv run .claude/commands/generate-atlas.py --check      # exit 1 if stale
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _atlas_lib  # noqa: E402
from _atlas_lib import (  # noqa: E402
    Agent,
    Command,
    Plugin,
    Skill,
    parse_agents,
    parse_commands,
    parse_marketplace,
    parse_skills,
)
from _atlas_lib import (
    list_reference_docs as _lib_list_reference_docs,
)
from _atlas_lib import (
    repo_root_from_git as _repo_root_from_git,
)

# ---------------------------------------------------------------------------
# Additional data classes (not shared with atlas-health-llm.py)
# ---------------------------------------------------------------------------


@dataclass
class Hook:
    plugin: str
    event: str
    matcher: str
    command: str


@dataclass
class McpServer:
    plugin: str
    server_name: str
    server_type: str
    url: str


@dataclass
class HealthFinding:
    severity: str  # "WARN" | "INFO"
    category: str
    message: str
    file_path: str = ""


# ---------------------------------------------------------------------------
# LSP plugin names (from shared lib)
# ---------------------------------------------------------------------------

_LSP_PLUGINS = _atlas_lib.LSP_PLUGINS

# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _validate_repo_root(repo_root: Path) -> None:
    """Validate that repo_root is a git root with marketplace.json."""
    if not repo_root.is_dir():
        print(f"ERROR: --repo-root {repo_root} is not a directory.", file=sys.stderr)
        sys.exit(1)
    mp_path = repo_root / ".claude-plugin" / "marketplace.json"
    if not mp_path.exists():
        print(
            f"ERROR: --repo-root {repo_root} does not contain .claude-plugin/marketplace.json.",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
        )
        git_root = Path(result.stdout.strip()).resolve()
        resolved = repo_root.resolve()
        if git_root != resolved:
            print(
                f"ERROR: --repo-root {repo_root} is not the git root (git says {git_root}).",
                file=sys.stderr,
            )
            sys.exit(1)
    except subprocess.CalledProcessError:
        print(
            f"ERROR: --repo-root {repo_root} is not inside a git repository.",
            file=sys.stderr,
        )
        sys.exit(1)


def _parse_skills(plugins: list[Plugin]) -> list[Skill]:
    """Collect all SKILL.md files across all plugins via _atlas_lib."""
    skills: list[Skill] = []
    for plugin in plugins:
        skills.extend(parse_skills(plugin.source_path, plugin.name))
    return skills


def _parse_agents(plugins: list[Plugin]) -> list[Agent]:
    """Collect all agent .md files across all plugins via _atlas_lib."""
    agents: list[Agent] = []
    for plugin in plugins:
        agents.extend(parse_agents(plugin.source_path, plugin.name))
    return agents


def _parse_commands(plugins: list[Plugin]) -> list[Command]:
    """Collect all command .md files across all plugins via _atlas_lib."""
    commands: list[Command] = []
    for plugin in plugins:
        commands.extend(parse_commands(plugin.source_path, plugin.name))
    return commands


def _parse_hooks(plugins: list[Plugin]) -> list[Hook]:
    """Collect all hooks from hooks.json files."""
    hooks: list[Hook] = []
    for plugin in plugins:
        hooks_file = plugin.source_path / "hooks" / "hooks.json"
        if not hooks_file.exists():
            continue
        try:
            data = json.loads(hooks_file.read_text())
        except Exception as exc:
            print(f"WARNING: failed to parse {hooks_file}: {exc}", file=sys.stderr)
            continue
        for event, entries in data.get("hooks", {}).items():
            for entry in entries:
                matcher = entry.get("matcher", "")
                for h in entry.get("hooks", []):
                    if h.get("type") == "command":
                        hooks.append(
                            Hook(
                                plugin=plugin.name,
                                event=event,
                                matcher=matcher,
                                command=h.get("command", ""),
                            )
                        )
    return hooks


def _parse_mcp_servers(plugins: list[Plugin]) -> list[McpServer]:
    """Collect MCP servers from .mcp.json files."""
    servers: list[McpServer] = []
    for plugin in plugins:
        mcp_file = plugin.source_path / ".mcp.json"
        if not mcp_file.exists():
            continue
        try:
            data = json.loads(mcp_file.read_text())
        except Exception as exc:
            print(f"WARNING: failed to parse {mcp_file}: {exc}", file=sys.stderr)
            continue
        for server_name, cfg in data.get("mcpServers", {}).items():
            servers.append(
                McpServer(
                    plugin=plugin.name,
                    server_name=server_name,
                    server_type=cfg.get("type", "unknown"),
                    url=cfg.get("url", ""),
                )
            )
    return servers


def _list_reference_docs(plugins: list[Plugin]) -> dict[str, list[Path]]:
    """Return {plugin_name: [Path, ...]} for references/ directories via _atlas_lib."""
    refs: dict[str, list[Path]] = {}
    for plugin in plugins:
        paths = _lib_list_reference_docs(plugin.source_path)
        if paths:
            refs[plugin.name] = paths
    return refs


# ---------------------------------------------------------------------------
# Cross-reference generation
# ---------------------------------------------------------------------------

# Regex to match subagent_type = "value" or subagent_type: "value"
_SUBAGENT_TYPE_RE = re.compile(r'subagent_type\s*[=:]\s*["\']([^"\']+)["\']')


def _build_spawn_graph(
    agents: list[Agent], skills: list[Skill], ref_docs: dict[str, list[Path]] | None = None
) -> dict[str, list[str]]:
    """Return {agent_name: [skill_name, ...]} — which skills spawn each agent.

    Matches via subagent_type= patterns and backtick-quoted agent names in skill bodies.
    Handles both bare names ('architect') and plugin-qualified names ('code-quality:architect').
    Also scans reference doc content and attributes matches to the owning skill.
    """
    spawn_graph: dict[str, list[str]] = {a.name: [] for a in agents}

    # Build a lookup: ref_doc_path -> skill_name (owner)
    # A ref doc is owned by a skill if it lives under skills/{skill_name}/references/
    ref_doc_owner: dict[Path, str] = {}
    if ref_docs:
        for _plugin_name, paths in ref_docs.items():
            for ref_path in paths:
                # Check if path is under skills/{skill_name}/references/
                parts = ref_path.parts
                for i, part in enumerate(parts):
                    if part == "skills" and i + 2 < len(parts):
                        skill_name = parts[i + 1]
                        ref_doc_owner[ref_path] = skill_name
                        break

    for agent in agents:
        bare = agent.name.split(":", 1)[-1]

        for skill in skills:
            body = skill.body
            if not body:
                try:
                    body = skill.path.read_text()
                except Exception:
                    continue

            for match in _SUBAGENT_TYPE_RE.finditer(body):
                captured = match.group(1)
                captured_bare = captured.split(":", 1)[-1]
                if captured_bare == bare and skill.name not in spawn_graph[agent.name]:
                    spawn_graph[agent.name].append(skill.name)

        # Second pass: scan reference docs owned by each skill
        if ref_docs:
            for ref_path, owning_skill_name in ref_doc_owner.items():
                if owning_skill_name not in spawn_graph[agent.name]:
                    try:
                        ref_body = ref_path.read_text()
                    except Exception:
                        continue
                    for match in _SUBAGENT_TYPE_RE.finditer(ref_body):
                        captured = match.group(1)
                        captured_bare = captured.split(":", 1)[-1]
                        if captured_bare == bare:
                            spawn_graph[agent.name].append(owning_skill_name)
                            break

    return spawn_graph


def _build_reference_consumption(
    skills: list[Skill], agents: list[Agent], ref_docs: dict[str, list[Path]]
) -> dict[str, list[str]]:
    """Return {ref_filename: [skill_or_agent_name, ...]} — who references each doc.

    Uses the 'references/{filename}' suffix pattern for matching.
    """
    # Collect all bodies (skills + agents)
    bodies: list[tuple[str, str]] = []  # (name, body_text)
    for skill in skills:
        body = skill.body or ""
        if not body:
            try:
                body = skill.path.read_text()
            except Exception:
                body = ""
        bodies.append((skill.name, body))
    for agent in agents:
        body = agent.body or ""
        if not body:
            try:
                body = agent.path.read_text()
            except Exception:
                body = ""
        bodies.append((agent.name, body))

    consumption: dict[str, list[str]] = {}
    for _plugin_name, paths in ref_docs.items():
        for ref_path in paths:
            fn = ref_path.name
            pattern = r"references/" + re.escape(fn)
            consumers: list[str] = []
            for name, body in bodies:
                if re.search(pattern, body):
                    consumers.append(name)
            if consumers:
                consumption[fn] = consumers

    return consumption


def _skill_spawns(
    skill: Skill, agents: list[Agent], spawn_graph: dict[str, list[str]]
) -> list[str]:
    """Return agent names spawned by this skill."""
    result = []
    for agent in agents:
        if skill.name in spawn_graph.get(agent.name, []):
            result.append(agent.name)
    return result


# Regex to match Agent( calls and extract model and subagent_type
_AGENT_CALL_RE = re.compile(r"Agent\([^)]*?model\s*=\s*[\"'](\w+)[\"']", re.DOTALL)
_AGENT_SUBTYPE_IN_CALL_RE = re.compile(
    r"Agent\([^)]*?subagent_type\s*=\s*[\"']([^\"']+)[\"']", re.DOTALL
)


def _count_agent_spawns(skill: Skill, ref_docs: dict[str, list[Path]]) -> str:
    """Count anonymous and named agent spawns, return display string.

    Returns strings like: "architect, Sonnet(2), Opus(1)" or "—"
    Named agents (non-general-purpose subagent_type) are excluded since they
    appear in the spawn graph. Only general-purpose agents are counted by model.
    """
    # Collect all text bodies to scan (skill body + owned reference docs)
    texts: list[str] = []
    body = skill.body or ""
    if not body:
        try:
            body = skill.path.read_text()
        except Exception:
            body = ""
    texts.append(body)

    # Add reference doc content owned by this skill
    for _plugin_name, paths in ref_docs.items():
        for ref_path in paths:
            parts = ref_path.parts
            for i, part in enumerate(parts):
                if part == "skills" and i + 1 < len(parts) and parts[i + 1] == skill.name:
                    with contextlib.suppress(Exception):
                        texts.append(ref_path.read_text())
                    break

    full_text = "\n".join(texts)

    # Find all Agent( calls with model=
    model_counts: dict[str, int] = {}
    for match in _AGENT_CALL_RE.finditer(full_text):
        model = match.group(1)
        # Check if this same Agent( call has a non-general-purpose subagent_type
        call_text = match.group(0)
        # Expand to capture full Agent(...) call for subtype check
        start = match.start()
        paren_end = full_text.find(")", start)
        if paren_end > 0:
            call_text = full_text[start : paren_end + 1]
        subtype_match = _AGENT_SUBTYPE_IN_CALL_RE.search(call_text)
        if subtype_match:
            subtype = subtype_match.group(1)
            if subtype != "general-purpose":
                continue  # Named agent — already in spawn graph
        # Count this anonymous/general-purpose agent by model
        model_label = model.capitalize()
        model_counts[model_label] = model_counts.get(model_label, 0) + 1

    if not model_counts:
        return ""
    parts = [f"{model}({count})" for model, count in sorted(model_counts.items())]
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------


def _run_health_checks(
    plugins: list[Plugin],
    skills: list[Skill],
    agents: list[Agent],
    commands: list[Command],
    hooks: list[Hook],
    ref_docs: dict[str, list[Path]],
    ref_consumption: dict[str, list[str]],
) -> list[HealthFinding]:
    """Return structural health findings."""
    findings: list[HealthFinding] = []

    # Version mismatches: plugin.json vs marketplace.json
    for plugin in plugins:
        plugin_json = plugin.source_path / ".claude-plugin" / "plugin.json"
        if plugin_json.exists():
            try:
                pdata = json.loads(plugin_json.read_text())
                plugin_version = pdata.get("version", "")
                if plugin_version and plugin_version != plugin.version:
                    findings.append(
                        HealthFinding(
                            severity="WARN",
                            category="version-mismatch",
                            message=(
                                f"{plugin.name}: plugin.json={plugin_version} "
                                f"vs marketplace.json={plugin.version}"
                            ),
                            file_path=str(plugin_json),
                        )
                    )
            except Exception:
                pass

    # Orphaned reference docs (not referenced by any skill or agent body)
    for _plugin_name, paths in ref_docs.items():
        for ref_path in paths:
            fn = ref_path.name
            if fn not in ref_consumption:
                findings.append(
                    HealthFinding(
                        severity="WARN",
                        category="orphan",
                        message="Not referenced by any skill or agent",
                        file_path=str(ref_path),
                    )
                )

    # Empty non-LSP plugins
    for plugin in plugins:
        if plugin.is_lsp:
            continue
        has_content = (
            any(s.plugin == plugin.name for s in skills)
            or any(a.plugin == plugin.name for a in agents)
            or any(c.plugin == plugin.name for c in commands)
            or any(h.plugin == plugin.name for h in hooks)
        )
        if not has_content:
            findings.append(
                HealthFinding(
                    severity="WARN",
                    category="empty-plugin",
                    message=f"{plugin.name} has no skills, agents, commands, or hooks",
                    file_path=str(plugin.source_path),
                )
            )

    # Skills with empty descriptions
    for skill in skills:
        if not skill.description:
            findings.append(
                HealthFinding(
                    severity="INFO",
                    category="missing-description",
                    message=f"skill {skill.name} has no description",
                    file_path=str(skill.path),
                )
            )

    # Agents with empty descriptions
    for agent in agents:
        if not agent.description:
            findings.append(
                HealthFinding(
                    severity="INFO",
                    category="missing-description",
                    message=f"agent {agent.name} has no description",
                    file_path=str(agent.path),
                )
            )

    # Build spawn graphs: body-only (for consistency check) and full (for orphan/mismatch)
    spawn_graph = _build_spawn_graph(agents, skills)
    spawn_graph_with_refs = _build_spawn_graph(agents, skills, ref_docs)

    # Orphaned agents: not spawned by any skill even with ref doc scanning
    for agent in agents:
        if not spawn_graph_with_refs.get(agent.name):
            findings.append(
                HealthFinding(
                    severity="INFO",
                    category="orphan-agent",
                    message=f"agent {agent.name} has no 'Spawned By' entries in spawn graph",
                    file_path=str(agent.path),
                )
            )

    # subagent_type consistency: flag skills that spawn agents (per ref doc scan)
    # but don't have subagent_type in their SKILL.md body
    for skill in skills:
        body = skill.body or ""
        if not body:
            try:
                body = skill.path.read_text()
            except Exception:
                body = ""
        has_subagent_in_body = bool(_SUBAGENT_TYPE_RE.search(body))
        # Check if this skill spawns agents via ref doc scanning
        spawns_via_refs = []
        for agent in agents:
            if skill.name in spawn_graph_with_refs.get(agent.name, []):
                spawns_via_refs.append(agent.name)
        # Only spawns via reference docs (not in body)
        spawns_via_body = []
        for agent in agents:
            if skill.name in spawn_graph.get(agent.name, []):
                spawns_via_body.append(agent.name)
        only_in_refs = set(spawns_via_refs) - set(spawns_via_body)
        if only_in_refs and not has_subagent_in_body:
            findings.append(
                HealthFinding(
                    severity="INFO",
                    category="subagent-consistency",
                    message=(
                        f"skill {skill.name} spawns agents via reference docs "
                        f"({', '.join(sorted(only_in_refs))}) but has no subagent_type "
                        f"in SKILL.md body — add subagent_type for consistency"
                    ),
                    file_path=str(skill.path),
                )
            )

    # Bidirectional spawn validation: compare spawn graph against agent spawned-by frontmatter
    for agent in agents:
        graph_spawners = set(spawn_graph_with_refs.get(agent.name, []))
        declared_spawners = set(agent.spawned_by)
        if not declared_spawners and not graph_spawners:
            continue  # no data either way — skip
        if declared_spawners:
            # Spawners in frontmatter but not detected by scanner
            for skill_name in sorted(declared_spawners - graph_spawners):
                findings.append(
                    HealthFinding(
                        severity="WARN",
                        category="spawn-mismatch",
                        message=(
                            f"agent {agent.name} declares spawned-by: {skill_name} "
                            f"but no subagent_type match found in that skill"
                        ),
                        file_path=str(agent.path),
                    )
                )
            # Spawners detected by scanner but not in frontmatter
            for skill_name in sorted(graph_spawners - declared_spawners):
                findings.append(
                    HealthFinding(
                        severity="INFO",
                        category="spawn-mismatch",
                        message=(
                            f"agent {agent.name} is spawned by {skill_name} "
                            f"but not listed in spawned-by frontmatter"
                        ),
                        file_path=str(agent.path),
                    )
                )
        elif graph_spawners:
            # No spawned-by frontmatter at all — suggest adding it
            findings.append(
                HealthFinding(
                    severity="INFO",
                    category="spawn-mismatch",
                    message=(
                        f"agent {agent.name} has no spawned-by frontmatter — "
                        f"add spawned-by: [{', '.join(sorted(graph_spawners))}]"
                    ),
                    file_path=str(agent.path),
                )
            )

    # Tool frontmatter validation: compare allowed-tools vs tools mentioned in skill body
    _KNOWN_TOOLS = {
        "Read",
        "Write",
        "Edit",
        "Glob",
        "Grep",
        "Bash",
        "Agent",
        "LSP",
        "AskUserQuestion",
        "SendMessage",
        "WebSearch",
        "WebFetch",
        "Skill",
        "ToolSearch",
        "TaskCreate",
        "TaskUpdate",
        "TaskList",
        "TaskGet",
        "CronCreate",
        "CronDelete",
        "NotebookEdit",
        "TeamCreate",
        "TeamDelete",
    }
    for skill in skills:
        body = skill.body or ""
        if not body:
            try:
                body = skill.path.read_text()
            except Exception:
                continue
        # Normalize allowed_tools: shorten MCP names for comparison
        allowed_set = {_shorten_mcp_tool(t) for t in skill.allowed_tools}
        for tool in sorted(_KNOWN_TOOLS):
            in_body = bool(re.search(r"\b" + re.escape(tool) + r"\b", body))
            in_allowed = tool in allowed_set
            if in_body and not in_allowed:
                findings.append(
                    HealthFinding(
                        severity="INFO",
                        category="tool-frontmatter",
                        message=f"tool {tool} used in body but not in allowed-tools",
                        file_path=str(skill.path),
                    )
                )
            elif in_allowed and not in_body:
                findings.append(
                    HealthFinding(
                        severity="INFO",
                        category="tool-frontmatter",
                        message=f"tool {tool} in allowed-tools but not found in body",
                        file_path=str(skill.path),
                    )
                )

    return findings


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _shorten_mcp_tool(name: str) -> str:
    """Shorten mcp__server__function to server:function."""
    if not name.startswith("mcp__"):
        return name
    # mcp__server__func or mcp__plugin_X_server__func
    parts = name.split("__")
    if len(parts) >= 3:
        server = parts[1]
        func = "__".join(parts[2:])
        # Plugin-qualified servers: "plugin_jira_mcp-atlassian-prod" → just use last segment
        if server.startswith("plugin_"):
            # mcp__plugin_jira_mcp-atlassian-prod__func → extract server from middle
            # Pattern: plugin_{plugin}_{server-name}
            seg = server.split("_", 2)
            server = seg[-1] if len(seg) >= 3 else server
        return f"{server}:{func}"
    return name


def _format_tools(tools: list[str], wrap_after: int = 3) -> str:
    """Return tools in <small> text, shortening MCP names, with <br> every N items."""
    if not tools:
        return "—"
    display_tools = [_shorten_mcp_tool(t) for t in tools]
    if len(display_tools) <= wrap_after:
        return "<small>" + ", ".join(display_tools) + "</small>"
    chunks = []
    for i in range(0, len(display_tools), wrap_after):
        chunks.append(", ".join(display_tools[i : i + wrap_after]))
    return "<small>" + ",<br>".join(chunks) + "</small>"


def _has_mcp_tools(tools: list[str]) -> bool:
    """Return True if any tool is an MCP tool (starts with mcp__)."""
    return any(t.startswith("mcp__") for t in tools)


def _compact_description(desc: str) -> str:
    """Truncate description to first sentence or 100 chars, whichever is shorter."""
    if not desc:
        return desc
    # First sentence: split on '. ' or end with '.'
    first_sentence = desc.strip()
    dot_idx = first_sentence.find(". ")
    if dot_idx != -1:
        first_sentence = first_sentence[: dot_idx + 1]
    # Apply 100-char cap
    if len(first_sentence) > 100:
        first_sentence = first_sentence[:100].rstrip() + "..."
    return first_sentence


def _description_first_line(desc: str) -> str:
    """Return the first non-empty line of a (possibly multi-line) description."""
    for line in desc.splitlines():
        line = line.strip()
        if line:
            return line
    return desc.strip()


def _short_path(path: str, repo_root: Path) -> str:
    """Return path relative to repo_root if possible."""
    try:
        return str(Path(path).relative_to(repo_root))
    except ValueError:
        return path


# ---------------------------------------------------------------------------
# ATLAS.md sections
# ---------------------------------------------------------------------------


def _render_lsp_table(plugins: list[Plugin]) -> str:
    """Render all LSP plugins in a single compact table."""
    lines: list[str] = []
    lines.append("### LSP Plugins")
    lines.append("")
    lines.append("| Plugin | Version | Description |")
    lines.append("|--------|---------|-------------|")
    for plugin in plugins:
        desc = _compact_description(plugin.description)
        lines.append(f"| {plugin.name} | {plugin.version} | {desc} |")
    lines.append("")
    return "\n".join(lines)


def _render_hooks_aggregated(hooks: list[Hook]) -> str:
    """Render hooks aggregated by script, sorted by event then matcher."""
    # Sort hooks by event then matcher for deterministic output
    sorted_hooks = sorted(hooks, key=lambda h: (h.event, h.matcher))

    # Group by script (command stripped of ${CLAUDE_PLUGIN_ROOT}/)
    script_matchers: dict[str, list[str]] = {}
    for hook in sorted_hooks:
        script = hook.command.replace("${CLAUDE_PLUGIN_ROOT}/", "")
        if script not in script_matchers:
            script_matchers[script] = []
        event_matcher = f"{hook.event}(`{hook.matcher}`)" if hook.matcher else hook.event
        if event_matcher not in script_matchers[script]:
            script_matchers[script].append(event_matcher)

    lines: list[str] = []
    lines.append("| Script | Events |")
    lines.append("|--------|--------|")
    for script in sorted(script_matchers):
        events_str = ", ".join(script_matchers[script])
        lines.append(f"| `{script}` | {events_str} |")
    return "\n".join(lines)


def _render_plugin_section(
    plugin: Plugin,
    skills: list[Skill],
    agents: list[Agent],
    commands: list[Command],
    hooks: list[Hook],
    mcp_servers: list[McpServer],
    spawn_graph: dict[str, list[str]],
    all_ref_docs: dict[str, list[Path]],
    ref_docs: list[Path] | None = None,
    ref_consumption: dict[str, list[str]] | None = None,
) -> str:
    """Render the full markdown section for one non-LSP plugin."""
    lines: list[str] = []
    lines.append(f"### {plugin.name} (v{plugin.version})")
    lines.append("")
    lines.append(_compact_description(plugin.description))
    lines.append("")

    # Skills table
    if skills:
        lines.append(f"**Skills ({len(skills)})**")
        lines.append("")
        lines.append("| Skill | Tools | Agents |")
        lines.append("|-------|-------|--------|")
        for skill in skills:
            tools_str = _format_tools(skill.allowed_tools)
            spawned = _skill_spawns(skill, agents, spawn_graph)
            anon = _count_agent_spawns(skill, all_ref_docs)
            parts = list(spawned)
            if anon:
                parts.append(anon)
            spawns_str = ", ".join(parts) if parts else "—"
            lines.append(f"| {skill.name} | {tools_str} | {spawns_str} |")
        if any(_has_mcp_tools(s.allowed_tools) for s in skills):
            lines.append("")
            lines.append("_MCP tools shown as `server:function` (full: `mcp__server__function`)._")
        lines.append("")

    # Agents table
    if agents:
        lines.append(f"**Agents ({len(agents)})**")
        lines.append("")
        mcp_note = any(_has_mcp_tools(a.tools) for a in agents)
        lines.append("| Agent | Model | Tools | Spawned By |")
        lines.append("|-------|-------|-------|------------|")
        for agent in agents:
            model_str = agent.model if agent.model else "—"
            tools_str = _format_tools(agent.tools)
            spawned_by = spawn_graph.get(agent.name, [])
            spawned_by_str = ", ".join(spawned_by) if spawned_by else "—"
            lines.append(f"| {agent.name} | {model_str} | {tools_str} | {spawned_by_str} |")
        if mcp_note:
            lines.append("")
            lines.append("_MCP tools shown as `server:function` (full: `mcp__server__function`)._")
        lines.append("")

    # Hooks table
    if hooks:
        lines.append("**Hooks**")
        lines.append("")
        lines.append(_render_hooks_aggregated(hooks))
        lines.append("")

    # Commands table
    if commands:
        lines.append(f"**Commands ({len(commands)})**")
        lines.append("")
        lines.append("| Command | Description |")
        lines.append("|---------|-------------|")
        for cmd in commands:
            desc = _description_first_line(cmd.description)
            lines.append(f"| {cmd.name} | {desc} |")
        lines.append("")

    # MCP servers table
    if mcp_servers:
        lines.append("**MCP Servers**")
        lines.append("")
        lines.append("| Server | Type | URL |")
        lines.append("|--------|------|-----|")
        for mcp in mcp_servers:
            lines.append(f"| `{mcp.server_name}` | {mcp.server_type} | `{mcp.url}` |")
        lines.append("")

    # References table (per-plugin, deduplicated by filename)
    if ref_docs:
        seen: set[str] = set()
        unique_refs: list[Path] = []
        for ref_path in sorted(ref_docs, key=lambda p: p.name):
            if ref_path.name not in seen:
                seen.add(ref_path.name)
                unique_refs.append(ref_path)
        lines.append(f"**References ({len(unique_refs)})**")
        lines.append("")
        lines.append("| Reference | Consumed By |")
        lines.append("|-----------|-------------|")
        for ref_path in unique_refs:
            fn = ref_path.name
            consumers = (ref_consumption or {}).get(fn, [])
            consumers_str = ", ".join(consumers) if consumers else "—"
            lines.append(f"| {fn} | {consumers_str} |")
        lines.append("")

    return "\n".join(lines)


def _render_cross_references(
    agents: list[Agent],
    spawn_graph: dict[str, list[str]],
) -> str:
    """Render the cross-reference section (Agent Spawn Graph only)."""
    lines: list[str] = []
    lines.append("## Cross-References")
    lines.append("")

    # Agent spawn graph (agent -> spawned by skills)
    lines.append("### Agent Spawn Graph")
    lines.append("")
    lines.append("| Agent | Spawned By |")
    lines.append("|-------|------------|")
    for agent in agents:
        spawned_by = spawn_graph.get(agent.name, [])
        spawned_by_str = ", ".join(spawned_by) if spawned_by else "—"
        lines.append(f"| {agent.name} | {spawned_by_str} |")
    lines.append("")

    return "\n".join(lines)


def _render_health_report(findings: list[HealthFinding], repo_root: Path) -> str:
    """Render the structural health section."""
    lines: list[str] = []
    lines.append("## Health Report")
    lines.append("")
    lines.append("### Structural Findings")
    lines.append("")

    if not findings:
        lines.append("No structural findings.")
        lines.append("")
        return "\n".join(lines)

    lines.append("| Severity | Category | Finding | File |")
    lines.append("|----------|----------|---------|------|")
    for f in findings:
        short = _short_path(f.file_path, repo_root) if f.file_path else "—"
        lines.append(f"| {f.severity} | {f.category} | {f.message} | {short} |")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------


def generate(repo_root: Path, today: str) -> tuple[str, dict[str, int]]:
    """Generate the full ATLAS.md content and return it with summary stats.

    Returns:
        (content, stats) where stats has keys: plugins, skills, agents, refs.
    """
    plugins = parse_marketplace(repo_root)
    skills = _parse_skills(plugins)
    agents = _parse_agents(plugins)
    commands = _parse_commands(plugins)
    hooks = _parse_hooks(plugins)
    mcp_servers = _parse_mcp_servers(plugins)
    ref_docs = _list_reference_docs(plugins)

    spawn_graph = _build_spawn_graph(agents, skills, ref_docs)
    ref_consumption = _build_reference_consumption(skills, agents, ref_docs)
    health_findings = _run_health_checks(
        plugins, skills, agents, commands, hooks, ref_docs, ref_consumption
    )

    # Read docs/WORKFLOW.md verbatim
    workflow_path = repo_root / "docs" / "WORKFLOW.md"
    if not workflow_path.exists():
        print(
            "ERROR: docs/WORKFLOW.md not found. Create it first (Task 1).",
            file=sys.stderr,
        )
        sys.exit(1)
    workflow_content = workflow_path.read_text()

    parts: list[str] = []

    # Header
    parts.append("# ATLAS — Plugin Inventory and Health Report")
    parts.append("<!-- Generated by generate-atlas.py — do not edit manually -->")
    parts.append(f"<!-- Last generated: {today} -->")
    parts.append("")

    # Verbatim WORKFLOW.md content
    parts.append(workflow_content.rstrip())
    parts.append("")
    parts.append("---")
    parts.append("")

    # Plugin Inventory
    parts.append("## Plugin Inventory")
    parts.append("")

    # Render all LSP plugins in one compact table
    lsp_plugins = [p for p in plugins if p.is_lsp]
    if lsp_plugins:
        parts.append(_render_lsp_table(lsp_plugins))

    # Render non-LSP plugins individually
    for plugin in plugins:
        if plugin.is_lsp:
            continue
        section = _render_plugin_section(
            plugin,
            [s for s in skills if s.plugin == plugin.name],
            [a for a in agents if a.plugin == plugin.name],
            [c for c in commands if c.plugin == plugin.name],
            [h for h in hooks if h.plugin == plugin.name],
            [m for m in mcp_servers if m.plugin == plugin.name],
            spawn_graph,
            all_ref_docs=ref_docs,
            ref_docs=ref_docs.get(plugin.name),
            ref_consumption=ref_consumption,
        )
        parts.append(section)

    parts.append("---")
    parts.append("")

    # Cross-references
    parts.append(_render_cross_references(agents, spawn_graph))

    parts.append("---")
    parts.append("")

    # Health report
    parts.append(_render_health_report(health_findings, repo_root))

    total_refs = sum(len(v) for v in ref_docs.values())
    stats = {
        "plugins": len(plugins),
        "skills": len(skills),
        "agents": len(agents),
        "refs": total_refs,
    }
    return "\n".join(parts) + "\n", stats


# ---------------------------------------------------------------------------
# --check mode
# ---------------------------------------------------------------------------

_TIMESTAMP_RE = re.compile(r"^<!-- Last generated: \d{4}-\d{2}-\d{2} -->\n", re.MULTILINE)


def _strip_timestamp(content: str) -> str:
    """Remove the <!-- Last generated: ... --> line for comparison."""
    return _TIMESTAMP_RE.sub("", content, count=1)


def check_staleness(atlas_path: Path, content: str) -> bool:
    """Return True if atlas_path matches content (ignoring timestamp)."""
    if not atlas_path.exists():
        return False
    on_disk = atlas_path.read_text()
    return _strip_timestamp(on_disk) == _strip_timestamp(content)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate or check ATLAS.md plugin inventory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if ATLAS.md is stale; exit 0 if current",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repository root (default: git rev-parse --show-toplevel)",
    )
    parser.add_argument(
        "--atlas-path",
        type=Path,
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--date",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    # Track which args were explicitly provided
    atlas_path_explicit = hasattr(args, "atlas_path")
    repo_root_explicit = args.repo_root is not None

    # Resolve repo root
    if repo_root_explicit:
        repo_root = args.repo_root.resolve()
        _validate_repo_root(repo_root)
    else:
        try:
            repo_root = _repo_root_from_git(Path.cwd())
        except subprocess.CalledProcessError:
            print(
                "ERROR: Not in a git repository. Use --repo-root to specify.",
                file=sys.stderr,
            )
            sys.exit(1)

    # Resolve atlas path
    if atlas_path_explicit:
        atlas_path = Path(args.atlas_path).resolve()
    else:
        atlas_path = (repo_root / "ATLAS.md").resolve()

    # Path boundary validation: atlas_path must be within repo_root
    # Skip only when BOTH --atlas-path and --repo-root are explicitly provided (test mode)
    if not (atlas_path_explicit and repo_root_explicit) and not atlas_path.is_relative_to(
        repo_root
    ):
        print(
            f"Error: --atlas-path must be within --repo-root ({repo_root})",
            file=sys.stderr,
        )
        sys.exit(1)

    # Date
    today: str = getattr(args, "date", date.today().isoformat())

    # Generate
    try:
        content, stats = generate(repo_root, today)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.check:
        if not atlas_path.exists():
            print(
                "ATLAS.md not found. Generate with: uv run .claude/commands/generate-atlas.py",
                file=sys.stderr,
            )
            sys.exit(1)
        if check_staleness(atlas_path, content):
            sys.exit(0)
        else:
            print(
                "ATLAS.md is stale. Regenerate with: uv run .claude/commands/generate-atlas.py",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        atlas_path.write_text(content)
        print(
            f"ATLAS.md generated ({stats['plugins']} plugins, "
            f"{stats['skills']} skills, {stats['agents']} agents, "
            f"{stats['refs']} references)"
        )


if __name__ == "__main__":
    main()
