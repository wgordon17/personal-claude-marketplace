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


def _repo_root_from_git(cwd: Path) -> Path:
    """Return the worktree root via git rev-parse."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(result.stdout.strip())


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


def _build_spawn_graph(agents: list[Agent], skills: list[Skill]) -> dict[str, list[str]]:
    """Return {agent_name: [skill_name, ...]} — which skills spawn each agent.

    Matches via subagent_type= patterns and backtick-quoted agent names in skill bodies.
    Handles both bare names ('architect') and plugin-qualified names ('code-quality:architect').
    """
    spawn_graph: dict[str, list[str]] = {a.name: [] for a in agents}

    for skill in skills:
        body = skill.body
        if not body:
            try:
                body = skill.path.read_text()
            except Exception:
                continue

        for agent in agents:
            # Normalize agent name: strip plugin prefix
            bare = agent.name.split(":", 1)[-1]

            # Match subagent_type patterns
            for match in _SUBAGENT_TYPE_RE.finditer(body):
                captured = match.group(1)
                # Normalize captured value
                captured_bare = captured.split(":", 1)[-1]
                if captured_bare == bare and skill.name not in spawn_graph[agent.name]:
                    spawn_graph[agent.name].append(skill.name)

            # Match backtick-quoted agent names
            backtick_pattern = r"`" + re.escape(bare) + r"`"
            if re.search(backtick_pattern, body) and skill.name not in spawn_graph[agent.name]:
                spawn_graph[agent.name].append(skill.name)

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


def _skill_ref_count(skill: Skill, ref_docs: dict[str, list[Path]]) -> int:
    """Count how many reference docs this skill body mentions."""
    body = skill.body or ""
    if not body:
        try:
            body = skill.path.read_text()
        except Exception:
            return 0
    count = 0
    for paths in ref_docs.values():
        for ref_path in paths:
            pattern = r"references/" + re.escape(ref_path.name)
            if re.search(pattern, body):
                count += 1
    return count


def _skill_spawns(
    skill: Skill, agents: list[Agent], spawn_graph: dict[str, list[str]]
) -> list[str]:
    """Return agent names spawned by this skill."""
    result = []
    for agent in agents:
        if skill.name in spawn_graph.get(agent.name, []):
            result.append(agent.name)
    return result


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

    return findings


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _abbr_tools(tools: list[str], max_show: int = 2) -> str:
    """Abbreviate a long tool list with <abbr> hover."""
    if not tools:
        return "—"
    if len(tools) <= max_show:
        return ", ".join(tools)
    shown = ", ".join(tools[:max_show])
    full = ", ".join(tools)
    remaining = len(tools) - max_show
    return f'<abbr title="{full}">{shown} +{remaining}</abbr>'


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


def _render_plugin_section(
    plugin: Plugin,
    skills: list[Skill],
    agents: list[Agent],
    commands: list[Command],
    hooks: list[Hook],
    mcp_servers: list[McpServer],
    ref_docs: dict[str, list[Path]],
    spawn_graph: dict[str, list[str]],
    ref_consumption: dict[str, list[str]],
) -> str:
    """Render the full markdown section for one plugin."""
    lines: list[str] = []
    lines.append(f"### {plugin.name} (v{plugin.version})")
    lines.append("")
    lines.append(plugin.description)
    lines.append("")

    # LSP plugins: stop here (no sub-sections)
    if plugin.is_lsp:
        return "\n".join(lines)

    plugin_refs = ref_docs.get(plugin.name, [])

    # Skills table
    if skills:
        lines.append(f"#### Skills ({len(skills)})")
        lines.append("")
        lines.append("| Skill | Tools | Refs | Spawns |")
        lines.append("|-------|-------|------|--------|")
        for skill in skills:
            tools_str = _abbr_tools(skill.allowed_tools)
            ref_count = _skill_ref_count(skill, ref_docs)
            refs_str = str(ref_count) if ref_count else "—"
            spawned = _skill_spawns(skill, agents, spawn_graph)
            spawns_str = ", ".join(spawned) if spawned else "—"
            lines.append(f"| {skill.name} | {tools_str} | {refs_str} | {spawns_str} |")
        lines.append("")

    # Agents table
    if agents:
        lines.append(f"#### Agents ({len(agents)})")
        lines.append("")
        lines.append("| Agent | Model | Tools | Spawned By |")
        lines.append("|-------|-------|-------|------------|")
        for agent in agents:
            model_str = agent.model if agent.model else "—"
            tools_str = _abbr_tools(agent.tools)
            spawned_by = spawn_graph.get(agent.name, [])
            spawned_by_str = ", ".join(spawned_by) if spawned_by else "—"
            lines.append(f"| {agent.name} | {model_str} | {tools_str} | {spawned_by_str} |")
        lines.append("")

    # Hooks table
    if hooks:
        lines.append("#### Hooks")
        lines.append("")
        lines.append("| Event | Matcher | Script |")
        lines.append("|-------|---------|--------|")
        for hook in hooks:
            matcher_str = hook.matcher if hook.matcher else "(any)"
            cmd_display = hook.command.replace("${CLAUDE_PLUGIN_ROOT}/", "")
            lines.append(f"| {hook.event} | `{matcher_str}` | `{cmd_display}` |")
        lines.append("")

    # Commands table
    if commands:
        lines.append(f"#### Commands ({len(commands)})")
        lines.append("")
        lines.append("| Command | Description |")
        lines.append("|---------|-------------|")
        for cmd in commands:
            desc = _description_first_line(cmd.description)
            lines.append(f"| {cmd.name} | {desc} |")
        lines.append("")

    # MCP servers table
    if mcp_servers:
        lines.append("#### MCP Servers")
        lines.append("")
        lines.append("| Server | Type | URL |")
        lines.append("|--------|------|-----|")
        for mcp in mcp_servers:
            lines.append(f"| `{mcp.server_name}` | {mcp.server_type} | `{mcp.url}` |")
        lines.append("")

    # References table — deduplicate by filename for display
    if plugin_refs:
        seen: set[str] = set()
        unique_refs: list[Path] = []
        for ref_path in plugin_refs:
            if ref_path.name not in seen:
                seen.add(ref_path.name)
                unique_refs.append(ref_path)
        lines.append(f"#### References ({len(unique_refs)})")
        lines.append("")
        lines.append("| Reference | Consumed By |")
        lines.append("|-----------|-------------|")
        for ref_path in unique_refs:
            fn = ref_path.name
            consumers = ref_consumption.get(fn, [])
            consumers_str = ", ".join(consumers) if consumers else "—"
            lines.append(f"| {fn} | {consumers_str} |")
        lines.append("")

    return "\n".join(lines)


def _render_cross_references(
    agents: list[Agent],
    spawn_graph: dict[str, list[str]],
    ref_consumption: dict[str, list[str]],
) -> str:
    """Render the cross-reference section."""
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

    # Reference consumption
    if ref_consumption:
        lines.append("### Reference Consumption")
        lines.append("")
        lines.append("| Reference | Consumed By |")
        lines.append("|-----------|-------------|")
        for fn in sorted(ref_consumption):
            consumers = ref_consumption[fn]
            lines.append(f"| {fn} | {', '.join(consumers)} |")
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


def generate(repo_root: Path, today: str) -> str:
    """Generate the full ATLAS.md content and return it as a string."""
    plugins = parse_marketplace(repo_root)
    skills = _parse_skills(plugins)
    agents = _parse_agents(plugins)
    commands = _parse_commands(plugins)
    hooks = _parse_hooks(plugins)
    mcp_servers = _parse_mcp_servers(plugins)
    ref_docs = _list_reference_docs(plugins)

    spawn_graph = _build_spawn_graph(agents, skills)
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
    for plugin in plugins:
        section = _render_plugin_section(
            plugin,
            [s for s in skills if s.plugin == plugin.name],
            [a for a in agents if a.plugin == plugin.name],
            [c for c in commands if c.plugin == plugin.name],
            [h for h in hooks if h.plugin == plugin.name],
            [m for m in mcp_servers if m.plugin == plugin.name],
            ref_docs,
            spawn_graph,
            ref_consumption,
        )
        parts.append(section)

    parts.append("---")
    parts.append("")

    # Cross-references
    parts.append(_render_cross_references(agents, spawn_graph, ref_consumption))

    parts.append("---")
    parts.append("")

    # Health report
    parts.append(_render_health_report(health_findings, repo_root))

    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# --check mode
# ---------------------------------------------------------------------------

_TIMESTAMP_RE = re.compile(r"^<!-- Last generated: \d{4}-\d{2}-\d{2} -->\n", re.MULTILINE)


def _strip_timestamp(content: str) -> str:
    """Remove the <!-- Last generated: ... --> line for comparison."""
    return _TIMESTAMP_RE.sub("", content, count=1)


def check_staleness(atlas_path: Path, generated: str) -> bool:
    """Return True if atlas_path matches generated content (ignoring timestamp)."""
    if not atlas_path.exists():
        return False
    on_disk = atlas_path.read_text()
    return _strip_timestamp(on_disk) == _strip_timestamp(generated)


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
        content = generate(repo_root, today)
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
        # Parse once to get summary counts
        _plugins = parse_marketplace(repo_root)
        _skills = _parse_skills(_plugins)
        _agents = _parse_agents(_plugins)
        _refs = _list_reference_docs(_plugins)
        total_refs = sum(len(v) for v in _refs.values())
        print(
            f"ATLAS.md generated ({len(_plugins)} plugins, "
            f"{len(_skills)} skills, {len(_agents)} agents, "
            f"{total_refs} references)"
        )


if __name__ == "__main__":
    main()
