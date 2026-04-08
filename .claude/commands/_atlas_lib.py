"""_atlas_lib.py — Shared parsing library for generate-atlas.py and atlas-health-llm.py.

Provides plugin metadata parsing and component scanning functions.

External dependencies (must be declared in each caller's PEP 723 deps):
  python-frontmatter>=1.1.0
_atlas_lib.py has no PEP 723 header and must not be run directly via `uv run`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# External dependencies (must be declared in each caller's PEP 723 deps):
#   python-frontmatter>=1.1.0
# _atlas_lib.py has no PEP 723 header and must not be run directly via `uv run`.
try:
    import frontmatter
except ImportError as e:
    raise ImportError(
        "_atlas_lib.py requires 'python-frontmatter' — ensure the calling script "
        "declares it in its PEP 723 dependencies block."
    ) from e

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
    source_path: Path = field(default_factory=Path)


@dataclass
class Skill:
    name: str
    plugin: str
    description: str
    allowed_tools: list[str]
    body: str = ""
    path: Path = field(default_factory=Path)


@dataclass
class Agent:
    name: str
    plugin: str
    description: str
    tools: list[str]
    model: str
    color: str
    body: str = ""
    path: Path = field(default_factory=Path)


@dataclass
class Command:
    name: str
    plugin: str
    description: str
    path: Path = field(default_factory=Path)


# ---------------------------------------------------------------------------
# LSP plugin names (render only header + description)
# ---------------------------------------------------------------------------

LSP_PLUGINS = frozenset(
    ["pyright-uvx", "vtsls-npx", "gopls-go", "vscode-html-css-npx", "rust-analyzer-rustup"]
)

# ---------------------------------------------------------------------------
# Public parsing functions
# ---------------------------------------------------------------------------


def parse_marketplace(repo_root: Path) -> list[Plugin]:
    """Parse .claude-plugin/marketplace.json and return ordered Plugin list.

    Args:
        repo_root: Absolute path to the repository root.

    Returns:
        Ordered list of Plugin dataclasses from marketplace.json.
        Warns (does not fail) for missing plugin source directories.
    """
    import sys

    mp_path = repo_root / ".claude-plugin" / "marketplace.json"
    data = json.loads(mp_path.read_text())
    plugins: list[Plugin] = []
    for entry in data["plugins"]:
        source = entry["source"]
        source_path = (repo_root / source.lstrip("./")).resolve()
        if not source_path.is_dir():
            print(
                f"WARNING: plugin source directory not found: {source_path}",
                file=sys.stderr,
            )
        plugins.append(
            Plugin(
                name=entry["name"],
                version=entry["version"],
                description=entry["description"],
                source=source,
                category=entry["category"],
                tags=entry.get("tags", []),
                is_lsp=entry["name"] in LSP_PLUGINS,
                source_path=source_path,
            )
        )
    return plugins


def parse_skills(plugin_path: Path, plugin_name: str = "") -> list[Skill]:
    """Collect all SKILL.md files for a single plugin directory.

    Args:
        plugin_path: Absolute path to the plugin directory.
        plugin_name: Plugin name for the Skill.plugin field (defaults to directory name).

    Returns:
        List of Skill dataclasses with name, description, allowed_tools, body, and path.
        Warns (does not fail) for corrupt YAML frontmatter.
    """
    import sys

    if not plugin_name:
        plugin_name = plugin_path.name
    skills: list[Skill] = []
    skill_files = sorted(plugin_path.glob("skills/*/SKILL.md"))
    for sf in skill_files:
        try:
            post = frontmatter.load(str(sf))
        except Exception as exc:
            print(f"WARNING: failed to parse {sf}: {exc}", file=sys.stderr)
            continue
        raw_tools = post.get("allowed-tools", [])
        if isinstance(raw_tools, str):
            tools = [t.strip() for t in raw_tools.split(",") if t.strip()]
        elif isinstance(raw_tools, list):
            tools = [str(t) for t in raw_tools]
        else:
            tools = []
        skills.append(
            Skill(
                name=post.get("name", sf.parent.name),
                plugin=plugin_name,
                description=str(post.get("description", "")).strip(),
                allowed_tools=tools,
                body=post.content,
                path=sf,
            )
        )
    return skills


def parse_agents(plugin_path: Path, plugin_name: str = "") -> list[Agent]:
    """Collect all agent .md files for a single plugin directory.

    Agent tools field is a comma-separated YAML scalar (not a list), so it is
    split on ', ' and normalized to a list internally.

    Args:
        plugin_path: Absolute path to the plugin directory.
        plugin_name: Plugin name for the Agent.plugin field (defaults to directory name).

    Returns:
        List of Agent dataclasses with name, description, tools, model, color, body, and path.
        Warns (does not fail) for corrupt YAML frontmatter.
    """
    import sys

    if not plugin_name:
        plugin_name = plugin_path.name
    agents: list[Agent] = []
    agent_files = sorted(plugin_path.glob("agents/*.md"))
    for af in agent_files:
        try:
            post = frontmatter.load(str(af))
        except Exception as exc:
            print(f"WARNING: failed to parse {af}: {exc}", file=sys.stderr)
            continue
        raw_tools = post.get("tools", "")
        if isinstance(raw_tools, str):
            tools = [t.strip() for t in raw_tools.split(",") if t.strip()]
        elif isinstance(raw_tools, list):
            tools = [str(t) for t in raw_tools]
        else:
            tools = []
        agents.append(
            Agent(
                name=post.get("name", af.stem),
                plugin=plugin_name,
                description=str(post.get("description", "")).strip(),
                tools=tools,
                model=post.get("model", ""),
                color=post.get("color", ""),
                body=post.content,
                path=af,
            )
        )
    return agents


def parse_commands(plugin_path: Path, plugin_name: str = "") -> list[Command]:
    """Collect all command .md files for a single plugin directory.

    Handles two patterns:
    - commands/<name>/COMMAND.md (name from frontmatter ``name`` field, else parent dir name)
    - commands/*.md flat files (name from frontmatter ``name`` field, else filename stem)

    For files without a ``description`` frontmatter field, falls back to the first
    non-heading paragraph of the body content.

    Args:
        plugin_path: Absolute path to the plugin directory.
        plugin_name: Plugin name for the Command.plugin field (defaults to directory name).

    Returns:
        List of Command dataclasses with name, description, and path.
        Warns (does not fail) for corrupt YAML frontmatter.
    """
    import sys

    if not plugin_name:
        plugin_name = plugin_path.name
    commands: list[Command] = []

    # Pattern 1: commands/<name>/COMMAND.md
    cmd_files = sorted(plugin_path.glob("commands/*/COMMAND.md"))
    for cf in cmd_files:
        try:
            post = frontmatter.load(str(cf))
            name = post.get("name") or cf.parent.name
            desc = str(post.get("description", "")).strip()
            if not desc:
                desc = _first_paragraph(post.content)
        except Exception as exc:
            print(f"WARNING: failed to parse {cf}: {exc}", file=sys.stderr)
            name = cf.parent.name
            desc = ""
        commands.append(Command(name=name, plugin=plugin_name, description=desc, path=cf))

    # Pattern 2: commands/*.md (flat, not COMMAND.md)
    flat_files = sorted(plugin_path.glob("commands/*.md"))
    for cf in flat_files:
        try:
            post = frontmatter.load(str(cf))
            name = post.get("name") or cf.stem
            desc = str(post.get("description", "")).strip()
            if not desc:
                desc = _first_paragraph(post.content)
        except Exception as exc:
            print(f"WARNING: failed to parse {cf}: {exc}", file=sys.stderr)
            name = cf.stem
            desc = ""
        commands.append(Command(name=name, plugin=plugin_name, description=desc, path=cf))

    return commands


def list_reference_docs(plugin_path: Path) -> list[Path]:
    """Return all files in the plugin's references/ directory and skills/*/references/.

    Args:
        plugin_path: Absolute path to the plugin directory.

    Returns:
        Sorted list of Path objects for all reference doc files found.
        Includes both plugin-root references/ and per-skill references/ directories.
    """
    refs: list[Path] = []

    # Plugin-root references/
    ref_dir = plugin_path / "references"
    if ref_dir.exists():
        refs.extend(sorted(f for f in ref_dir.iterdir() if f.is_file()))

    # Per-skill references/
    for skill_refs in sorted(plugin_path.glob("skills/*/references")):
        if skill_refs.is_dir():
            refs.extend(sorted(f for f in skill_refs.iterdir() if f.is_file()))

    return refs


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _first_paragraph(text: str) -> str:
    """Return first non-heading, non-empty paragraph from markdown text."""
    lines = text.splitlines()
    paragraph_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if paragraph_lines:
                break
            continue
        if stripped.startswith("#"):
            if paragraph_lines:
                break
            continue
        paragraph_lines.append(stripped)
    return " ".join(paragraph_lines)
