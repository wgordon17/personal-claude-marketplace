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
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import frontmatter

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Plugin:
    name: str
    version: str
    description: str
    source: str
    category: str
    tags: list[str]
    is_lsp: bool = False


@dataclass
class Skill:
    name: str
    plugin: str
    description: str
    allowed_tools: list[str]
    path: Path = field(default_factory=Path)


@dataclass
class Agent:
    name: str
    plugin: str
    description: str
    tools: list[str]
    model: str
    color: str
    path: Path = field(default_factory=Path)


@dataclass
class Command:
    name: str
    plugin: str
    description: str
    path: Path = field(default_factory=Path)


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
    severity: str  # "warning" | "info"
    category: str
    message: str


# ---------------------------------------------------------------------------
# LSP plugin names (render only header + description)
# ---------------------------------------------------------------------------

_LSP_PLUGINS = frozenset(
    ["pyright-uvx", "vtsls-npx", "gopls-go", "vscode-html-css-npx", "rust-analyzer-rustup"]
)

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


def _parse_marketplace(repo_root: Path) -> list[Plugin]:
    """Parse .claude-plugin/marketplace.json and return ordered Plugin list."""
    mp_path = repo_root / ".claude-plugin" / "marketplace.json"
    data = json.loads(mp_path.read_text())
    plugins: list[Plugin] = []
    for entry in data["plugins"]:
        plugins.append(
            Plugin(
                name=entry["name"],
                version=entry["version"],
                description=entry["description"],
                source=entry["source"],
                category=entry["category"],
                tags=entry.get("tags", []),
                is_lsp=entry["name"] in _LSP_PLUGINS,
            )
        )
    return plugins


def _parse_skills(repo_root: Path, plugins: list[Plugin]) -> list[Skill]:
    """Collect all SKILL.md files across plugins."""
    skills: list[Skill] = []
    for plugin in plugins:
        plugin_dir = repo_root / plugin.source.lstrip("./")
        skill_files = sorted(plugin_dir.glob("skills/*/SKILL.md"))
        for sf in skill_files:
            try:
                post = frontmatter.load(str(sf))
            except Exception:
                continue
            raw_tools = post.get("allowed-tools", [])
            if isinstance(raw_tools, str):
                tools = [t.strip() for t in raw_tools.split(",") if t.strip()]
            elif isinstance(raw_tools, list):
                tools = raw_tools
            else:
                tools = []
            skills.append(
                Skill(
                    name=post.get("name", sf.parent.name),
                    plugin=plugin.name,
                    description=str(post.get("description", "")).strip(),
                    allowed_tools=tools,
                    path=sf,
                )
            )
    return skills


def _parse_agents(repo_root: Path, plugins: list[Plugin]) -> list[Agent]:
    """Collect all agent .md files across plugins."""
    agents: list[Agent] = []
    for plugin in plugins:
        plugin_dir = repo_root / plugin.source.lstrip("./")
        agent_files = sorted(plugin_dir.glob("agents/*.md"))
        for af in agent_files:
            try:
                post = frontmatter.load(str(af))
            except Exception:
                continue
            raw_tools = post.get("tools", "")
            if isinstance(raw_tools, str):
                tools = [t.strip() for t in raw_tools.split(",") if t.strip()]
            elif isinstance(raw_tools, list):
                tools = raw_tools
            else:
                tools = []
            agents.append(
                Agent(
                    name=post.get("name", af.stem),
                    plugin=plugin.name,
                    description=str(post.get("description", "")).strip(),
                    tools=tools,
                    model=post.get("model", ""),
                    color=post.get("color", ""),
                    path=af,
                )
            )
    return agents


def _parse_commands(repo_root: Path, plugins: list[Plugin]) -> list[Command]:
    """Collect all command .md files across plugins (COMMAND.md and flat *.md)."""
    commands: list[Command] = []
    for plugin in plugins:
        plugin_dir = repo_root / plugin.source.lstrip("./")
        # Pattern 1: commands/<name>/COMMAND.md
        cmd_files = sorted(plugin_dir.glob("commands/*/COMMAND.md"))
        for cf in cmd_files:
            try:
                post = frontmatter.load(str(cf))
            except Exception:
                continue
            commands.append(
                Command(
                    name=post.get("name", cf.parent.name),
                    plugin=plugin.name,
                    description=str(post.get("description", "")).strip(),
                    path=cf,
                )
            )
        # Pattern 2: commands/*.md (flat)
        flat_files = sorted(plugin_dir.glob("commands/*.md"))
        for cf in flat_files:
            try:
                post = frontmatter.load(str(cf))
            except Exception:
                continue
            commands.append(
                Command(
                    name=post.get("name", cf.stem),
                    plugin=plugin.name,
                    description=str(post.get("description", "")).strip(),
                    path=cf,
                )
            )
    return commands


def _parse_hooks(repo_root: Path, plugins: list[Plugin]) -> list[Hook]:
    """Collect all hooks from hooks.json files."""
    hooks: list[Hook] = []
    for plugin in plugins:
        plugin_dir = repo_root / plugin.source.lstrip("./")
        hooks_file = plugin_dir / "hooks" / "hooks.json"
        if not hooks_file.exists():
            continue
        try:
            data = json.loads(hooks_file.read_text())
        except Exception:
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


def _parse_mcp_servers(repo_root: Path, plugins: list[Plugin]) -> list[McpServer]:
    """Collect MCP servers from .mcp.json files."""
    servers: list[McpServer] = []
    for plugin in plugins:
        plugin_dir = repo_root / plugin.source.lstrip("./")
        mcp_file = plugin_dir / ".mcp.json"
        if not mcp_file.exists():
            continue
        try:
            data = json.loads(mcp_file.read_text())
        except Exception:
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


def _list_reference_docs(repo_root: Path, plugins: list[Plugin]) -> dict[str, list[str]]:
    """Return {plugin_name: [filename, ...]} for references/ directories."""
    refs: dict[str, list[str]] = {}
    for plugin in plugins:
        plugin_dir = repo_root / plugin.source.lstrip("./")
        ref_dir = plugin_dir / "references"
        if ref_dir.exists():
            files = sorted(f.name for f in ref_dir.iterdir() if f.is_file())
            if files:
                refs[plugin.name] = files
    return refs


# ---------------------------------------------------------------------------
# Cross-reference generation
# ---------------------------------------------------------------------------


def _agent_spawn_graph(agents: list[Agent], skills: list[Skill]) -> list[tuple[str, str]]:
    """Find (skill_name, agent_name) pairs where skill body references agent subagent_type."""
    pairs: list[tuple[str, str]] = []
    for skill in skills:
        try:
            body = skill.path.read_text()
        except Exception:
            continue
        for agent in agents:
            # Normalize agent name: strip plugin prefix if present
            agent_short = agent.name.split(":")[-1]
            # Match subagent_type: "agent_name" or plugin:agent_name
            pattern = r"subagent_type[\"']?\s*[:=]\s*[\"']" + re.escape(agent_short) + r"[\"']"
            if re.search(pattern, body, re.IGNORECASE):
                pairs.append((skill.name, agent.name))
    return pairs


def _reference_consumption(
    skills: list[Skill], ref_docs: dict[str, list[str]]
) -> list[tuple[str, str, str]]:
    """Return (skill_name, plugin_name, ref_filename) where skill body mentions reference doc."""
    results: list[tuple[str, str, str]] = []
    for skill in skills:
        try:
            body = skill.path.read_text()
        except Exception:
            continue
        for plugin_name, filenames in ref_docs.items():
            for fn in filenames:
                # Use re.escape to safely match filename in body
                if re.search(re.escape(fn), body):
                    results.append((skill.name, plugin_name, fn))
    return results


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------


def _run_health_checks(
    plugins: list[Plugin],
    skills: list[Skill],
    agents: list[Agent],
    commands: list[Command],
    hooks: list[Hook],
) -> list[HealthFinding]:
    """Return structural health findings."""
    findings: list[HealthFinding] = []

    # Version consistency: compare plugin.json vs marketplace.json
    # (Both already parsed via Plugin dataclass — marketplace is canonical)
    # Check for plugins that have no skills, agents, commands, or hooks (non-LSP)
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
                    severity="warning",
                    category="empty-plugin",
                    message=f"{plugin.name} has no skills, agents, commands, or hooks",
                )
            )

    # Skills with empty descriptions
    for skill in skills:
        if not skill.description:
            findings.append(
                HealthFinding(
                    severity="info",
                    category="missing-description",
                    message=f"skill:{skill.plugin}:{skill.name} has no description",
                )
            )

    # Agents with empty descriptions
    for agent in agents:
        if not agent.description:
            findings.append(
                HealthFinding(
                    severity="info",
                    category="missing-description",
                    message=f"agent:{agent.plugin}:{agent.name} has no description",
                )
            )

    # Agents with no tools
    for agent in agents:
        if not agent.tools:
            findings.append(
                HealthFinding(
                    severity="info",
                    category="no-tools",
                    message=f"agent:{agent.plugin}:{agent.name} has no tools defined",
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _abbr_tools(tools: list[str], max_show: int = 3) -> str:
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


# ---------------------------------------------------------------------------
# ATLAS.md sections
# ---------------------------------------------------------------------------


_WORKFLOW_CONTENT = """\
# Plugin Workflow Guide

This guide describes the recommended skill workflow for working in this marketplace.

## Primary Workflow

```
[deep-research] → [swarm] → [pr-review] → [fix] → [quality-gate]
    (skill)        (skill)     (skill)     (skill)    (skill)
```

Each step:

1. **`/deep-research`** (skill) — Research the problem space thoroughly before designing a
   solution. Supports External mode (web) and Bridged mode (internal + external).

2. **`/swarm`** (skill) — Launch a full 21+ agent pipelined team for implementation.
   Covers architecture, security design, reduction, implementation, review, testing,
   QA, performance, plan adherence, simplification, docs, and verification.

3. **`/pr-review`** (skill) — Multi-agent PR review with 6 parallel specialized reviewers.
   Verifies findings by investigating source code before reporting.

4. **`/fix`** (skill) — Comprehensive finding fixer. Reads findings from the current session
   and applies targeted fixes with 8-bucket verification.

5. **`/quality-gate`** (skill) — Final checkpoint before claiming work is complete.
   Use after any significant deliverable.

## Side Workflows

```
Bug hunting:    [bug-investigation] → [fix]
Cleanup:        [unfuck] → [quality-gate]
Parallel impl:  [speculative] → [quality-gate]
Bulk analysis:  [map-reduce] → [fix]
Deep audit:     [file-audit] → [fix]
```

## Planning

Use **`/incremental-planning`** (skill) before any multi-file implementation task.
Native plan mode is disabled by hook — this skill replaces it.

Use **`/roadmap`** (skill) to sequence multiple implementation plans.

## Session Lifecycle

| Command | Purpose |
|---------|---------|
| `/session-start` (command) | Load project context or initialize new project |
| `/session-end` (command) | Sync project memory and update hack/ files |

## Quality Gate Callout

**Always run `/quality-gate` before claiming work is done.**
This applies after `/swarm`, `/fix`, subagent-driven development, or any significant deliverable.
"""


def _render_plugin_section(
    plugin: Plugin,
    skills: list[Skill],
    agents: list[Agent],
    commands: list[Command],
    hooks: list[Hook],
    mcp_servers: list[McpServer],
    ref_docs: dict[str, list[str]],
) -> str:
    """Render the full markdown section for one plugin."""
    lines: list[str] = []
    lines.append(f"## {plugin.name}")
    lines.append("")
    lines.append(
        f"**Version:** {plugin.version} | **Category:** {plugin.category} | "
        f"**Tags:** {', '.join(plugin.tags)}"
    )
    lines.append("")
    lines.append(plugin.description)
    lines.append("")

    # LSP plugins: stop here
    if plugin.is_lsp:
        return "\n".join(lines)

    plugin_skills = [s for s in skills if s.plugin == plugin.name]
    plugin_agents = [a for a in agents if a.plugin == plugin.name]
    plugin_commands = [c for c in commands if c.plugin == plugin.name]
    plugin_hooks = [h for h in hooks if h.plugin == plugin.name]
    plugin_mcp = [m for m in mcp_servers if m.plugin == plugin.name]
    plugin_refs = ref_docs.get(plugin.name, [])

    # Skills table
    if plugin_skills:
        lines.append("### Skills")
        lines.append("")
        lines.append("| Skill | Description | Tools |")
        lines.append("|-------|-------------|-------|")
        for skill in plugin_skills:
            desc = _description_first_line(skill.description)
            tools_str = _abbr_tools(skill.allowed_tools)
            lines.append(f"| `{skill.name}` | {desc} | {tools_str} |")
        lines.append("")

    # Agents table
    if plugin_agents:
        lines.append("### Agents")
        lines.append("")
        lines.append("| Agent | Description | Model | Tools |")
        lines.append("|-------|-------------|-------|-------|")
        for agent in plugin_agents:
            desc = _description_first_line(agent.description)
            model_str = agent.model if agent.model else "—"
            tools_str = _abbr_tools(agent.tools)
            lines.append(f"| `{agent.name}` | {desc} | {model_str} | {tools_str} |")
        lines.append("")

    # Commands table
    if plugin_commands:
        lines.append("### Commands")
        lines.append("")
        lines.append("| Command | Description |")
        lines.append("|---------|-------------|")
        for cmd in plugin_commands:
            desc = _description_first_line(cmd.description)
            lines.append(f"| `/{cmd.name}` | {desc} |")
        lines.append("")

    # Hooks table
    if plugin_hooks:
        lines.append("### Hooks")
        lines.append("")
        lines.append("| Event | Matcher | Command |")
        lines.append("|-------|---------|---------|")
        for hook in plugin_hooks:
            matcher_str = hook.matcher if hook.matcher else "(any)"
            # Abbreviate long command paths
            cmd_display = hook.command.replace("${CLAUDE_PLUGIN_ROOT}/", "")
            lines.append(f"| {hook.event} | `{matcher_str}` | `{cmd_display}` |")
        lines.append("")

    # MCP servers table
    if plugin_mcp:
        lines.append("### MCP Servers")
        lines.append("")
        lines.append("| Server | Type | URL |")
        lines.append("|--------|------|-----|")
        for mcp in plugin_mcp:
            lines.append(f"| `{mcp.server_name}` | {mcp.server_type} | `{mcp.url}` |")
        lines.append("")

    # Reference docs
    if plugin_refs:
        lines.append("### Reference Docs")
        lines.append("")
        for fn in plugin_refs:
            lines.append(f"- `{fn}`")
        lines.append("")

    return "\n".join(lines)


def _render_cross_references(
    spawn_graph: list[tuple[str, str]],
    ref_consumption: list[tuple[str, str, str]],
) -> str:
    """Render the cross-reference section."""
    lines: list[str] = []
    lines.append("## Cross-References")
    lines.append("")

    if spawn_graph:
        lines.append("### Agent Spawn Graph")
        lines.append("")
        lines.append("Skills that spawn agents via `subagent_type`:")
        lines.append("")
        lines.append("| Skill | Agent |")
        lines.append("|-------|-------|")
        for skill_name, agent_name in sorted(spawn_graph):
            lines.append(f"| `{skill_name}` | `{agent_name}` |")
        lines.append("")

    if ref_consumption:
        lines.append("### Reference Doc Consumption")
        lines.append("")
        lines.append("Skills that reference shared documentation:")
        lines.append("")
        lines.append("| Skill | Plugin | Reference |")
        lines.append("|-------|--------|-----------|")
        for skill_name, plugin_name, fn in sorted(ref_consumption):
            lines.append(f"| `{skill_name}` | {plugin_name} | `{fn}` |")
        lines.append("")

    return "\n".join(lines)


def _render_health_report(findings: list[HealthFinding]) -> str:
    """Render the structural health section."""
    lines: list[str] = []
    lines.append("## Structural Health")
    lines.append("")

    if not findings:
        lines.append("No structural issues found.")
        lines.append("")
        return "\n".join(lines)

    warnings = [f for f in findings if f.severity == "warning"]
    infos = [f for f in findings if f.severity == "info"]

    if warnings:
        lines.append("### Warnings")
        lines.append("")
        for f in warnings:
            lines.append(f"- **[{f.category}]** {f.message}")
        lines.append("")

    if infos:
        lines.append("### Info")
        lines.append("")
        for f in infos:
            lines.append(f"- [{f.category}] {f.message}")
        lines.append("")

    return "\n".join(lines)


def _render_summary_table(
    plugins: list[Plugin],
    skills: list[Skill],
    agents: list[Agent],
    commands: list[Command],
) -> str:
    """Render the top-level summary table."""
    non_lsp = [p for p in plugins if not p.is_lsp]
    lsp = [p for p in plugins if p.is_lsp]

    lines: list[str] = []
    lines.append("## Summary")
    lines.append("")
    lines.append("| Total Plugins | LSP Plugins | Non-LSP Plugins | Skills | Agents | Commands |")
    lines.append("|--------------|------------|-----------------|--------|--------|----------|")
    lines.append(
        f"| {len(plugins)} | {len(lsp)} | {len(non_lsp)} "
        f"| {len(skills)} | {len(agents)} | {len(commands)} |"
    )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------


def generate(repo_root: Path, today: str) -> str:
    """Generate the full ATLAS.md content and return it as a string."""
    plugins = _parse_marketplace(repo_root)
    skills = _parse_skills(repo_root, plugins)
    agents = _parse_agents(repo_root, plugins)
    commands = _parse_commands(repo_root, plugins)
    hooks = _parse_hooks(repo_root, plugins)
    mcp_servers = _parse_mcp_servers(repo_root, plugins)
    ref_docs = _list_reference_docs(repo_root, plugins)

    spawn_graph = _agent_spawn_graph(agents, skills)
    ref_consumption = _reference_consumption(skills, ref_docs)
    health_findings = _run_health_checks(plugins, skills, agents, commands, hooks)

    parts: list[str] = []

    # Header with timestamp
    parts.append(f"<!-- Last generated: {today} -->")
    parts.append("")
    parts.append("# ATLAS — Plugin Inventory")
    parts.append("")
    parts.append(
        "_Auto-generated by `generate-atlas.py`. Do not edit manually — run "
        "`uv run .claude/commands/generate-atlas.py` to regenerate._"
    )
    parts.append("")

    # Workflow guide (inlined)
    parts.append(_WORKFLOW_CONTENT)

    # Summary
    parts.append(_render_summary_table(plugins, skills, agents, commands))

    # Plugin sections in marketplace.json order
    parts.append("## Plugins")
    parts.append("")
    for plugin in plugins:
        plugin_skills = [s for s in skills if s.plugin == plugin.name]
        plugin_agents = [a for a in agents if a.plugin == plugin.name]
        plugin_commands = [c for c in commands if c.plugin == plugin.name]
        plugin_hooks = [h for h in hooks if h.plugin == plugin.name]
        plugin_mcp = [m for m in mcp_servers if m.plugin == plugin.name]
        section = _render_plugin_section(
            plugin,
            plugin_skills,
            plugin_agents,
            plugin_commands,
            plugin_hooks,
            plugin_mcp,
            ref_docs,
        )
        parts.append(section)

    # Cross-references
    if spawn_graph or ref_consumption:
        parts.append(_render_cross_references(spawn_graph, ref_consumption))

    # Health report
    parts.append(_render_health_report(health_findings))

    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# --check mode
# ---------------------------------------------------------------------------

_TIMESTAMP_RE = re.compile(r"^<!-- Last generated: \d{4}-\d{2}-\d{2} -->\n?", re.MULTILINE)


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
        help="Path to ATLAS.md (default: <repo-root>/ATLAS.md)",
    )
    parser.add_argument(
        "--date",
        default=argparse.SUPPRESS,
        help="Date string for timestamp (default: today in YYYY-MM-DD)",
    )
    args = parser.parse_args()

    # Resolve repo root
    if args.repo_root is not None:
        repo_root = args.repo_root.resolve()
    else:
        try:
            repo_root = _repo_root_from_git(Path.cwd())
        except subprocess.CalledProcessError:
            print("ERROR: Not in a git repository. Use --repo-root to specify.", file=sys.stderr)
            sys.exit(1)

    # Resolve atlas path
    atlas_path: Path = getattr(args, "atlas_path", repo_root / "ATLAS.md")
    atlas_path = atlas_path.resolve()

    # Path boundary validation: atlas_path must be within repo_root
    # (Skip only when both --atlas-path and --repo-root are explicitly provided and they
    # point outside each other — e.g., test mode with /tmp paths)
    explicit_both = args.repo_root is not None and hasattr(args, "atlas_path")
    if not explicit_both:
        try:
            atlas_path.relative_to(repo_root)
        except ValueError:
            print(
                f"ERROR: --atlas-path {atlas_path} is outside --repo-root {repo_root}",
                file=sys.stderr,
            )
            sys.exit(1)

    # Date
    today: str = getattr(args, "date", date.today().isoformat())

    # Generate
    try:
        content = generate(repo_root, today)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.check:
        if check_staleness(atlas_path, content):
            print("ATLAS.md is current.")
            sys.exit(0)
        else:
            if not atlas_path.exists():
                print("ATLAS.md does not exist. Run generate-atlas.py to create it.")
            else:
                print("ATLAS.md is stale. Run generate-atlas.py to regenerate.")
            sys.exit(1)
    else:
        atlas_path.write_text(content)
        print(f"Wrote {atlas_path}")


if __name__ == "__main__":
    main()
