"""Guard tests for standup commands, references, skills, and agents."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from standup_tool.cli import build_parser, subcommand_names
from standup_tool.collectors.registry import REGISTRY
from standup_tool.verify import VERIFY_NAMES

STANDUP_ROOT = Path(__file__).resolve().parents[1]

JIRA_WRITE_TOOL_SUFFIXES = frozenset(
    {
        "editJiraIssue",
        "createJiraIssue",
        "addCommentToJiraIssue",
        "transitionJiraIssue",
        "createIssueLink",
        "addWorklogToJiraIssue",
    }
)

JIRA_READ_ONLY_TOOLS = frozenset(
    {
        "mcp__plugin_jira_mcp-atlassian-prod__atlassianUserInfo",
        "mcp__plugin_jira_mcp-atlassian-prod__getAccessibleAtlassianResources",
        "mcp__plugin_jira_mcp-atlassian-prod__searchJiraIssuesUsingJql",
        "mcp__plugin_jira_mcp-atlassian-prod__getJiraIssue",
        "mcp__plugin_jira_mcp-atlassian-prod__getTransitionsForJiraIssue",
        "mcp__plugin_jira_mcp-atlassian-prod__lookupJiraAccountId",
        "mcp__plugin_jira_mcp-atlassian-prod__getJiraProjectIssueTypesMetadata",
        "mcp__plugin_jira_mcp-atlassian-prod__getJiraIssueTypeMetaWithFields",
        "mcp__plugin_jira_mcp-atlassian-prod__getIssueLinkTypes",
        "mcp__plugin_jira_mcp-atlassian-prod__getVisibleJiraProjects",
        "mcp__plugin_jira_mcp-atlassian-prod__getJiraIssueRemoteIssueLinks",
        "mcp__plugin_jira_mcp-atlassian-prod__fetch",
        "mcp__plugin_jira_mcp-atlassian-prod__search",
    }
)

_FENCED_BLOCK_RE = re.compile(r"^```[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_SUBCOMMAND_RE = re.compile(r"\bstandup-tool\s+([a-z][a-z-]*)")
_ONLY_FLAG_RE = re.compile(r"--only\s+([a-z]+)")
_PLUGIN_REF_RE = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/references/([A-Za-z0-9_.-]+\.md)")
_REL_REF_LINK_RE = re.compile(r"\]\(\.\./references/[^)]+\)")
_SUBAGENT_TYPE_RE = re.compile(r'subagent_type="([^"]+)"')


def extract_code_segments(markdown_text: str) -> list[str]:
    """Return all fenced code blocks and inline backtick spans from *markdown_text*."""
    segments: list[str] = [m.group(1) for m in _FENCED_BLOCK_RE.finditer(markdown_text)]
    without_fences = _FENCED_BLOCK_RE.sub("", markdown_text)
    segments.extend(m.group(1) for m in _INLINE_CODE_RE.finditer(without_fences))
    return segments


def parse_frontmatter(markdown_text: str) -> dict[str, object]:
    """Parse YAML frontmatter from *markdown_text*."""
    if not markdown_text.startswith("---\n"):
        return {}
    end = markdown_text.find("\n---\n", 4)
    if end == -1:
        return {}
    raw = markdown_text[4:end]
    parsed = yaml.safe_load(raw)
    return parsed if isinstance(parsed, dict) else {}


def parse_allowed_tools(frontmatter: dict[str, object]) -> list[str]:
    """Normalize `allowed-tools` frontmatter (list or string) into a list of tool names."""
    raw = frontmatter.get("allowed-tools")
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str):
        return [tok for tok in re.split(r"[\s,]+", raw.strip()) if tok]
    return []


def _markdown_instruction_files() -> list[Path]:
    files = sorted(STANDUP_ROOT.glob("commands/*.md"))
    files.extend(sorted(STANDUP_ROOT.glob("references/*.md")))
    files.extend(sorted(STANDUP_ROOT.glob("skills/*/SKILL.md")))
    return files


def test_command_and_skill_files_exist() -> None:
    """Expected commands, references, skills, and agents exist on disk."""
    expected = [
        STANDUP_ROOT / "commands" / "standup.md",
        STANDUP_ROOT / "commands" / "standup-preview.md",
        STANDUP_ROOT / "references" / "data-quality.md",
        STANDUP_ROOT / "references" / "jira-review.md",
        STANDUP_ROOT / "references" / "debrief.md",
        STANDUP_ROOT / "skills" / "standup-onboard" / "SKILL.md",
        STANDUP_ROOT / "agents" / "standup-draft.md",
        STANDUP_ROOT / "agents" / "standup-pr-review.md",
        STANDUP_ROOT / "agents" / "standup-priorities.md",
        STANDUP_ROOT / "agents" / "standup-signals.md",
    ]
    for path in expected:
        assert path.is_file(), f"Missing expected file: {path}"


def test_all_referenced_standup_tool_subcommands_and_only_flags_are_valid() -> None:
    """Every `standup-tool <subcmd>` and `--only <name>` in code spans/blocks is valid."""
    valid_subcommands = subcommand_names(build_parser())
    valid_only_names = {c.name for c in REGISTRY} | {"ldap", "permissions"}
    assert valid_only_names == set(VERIFY_NAMES)

    files = _markdown_instruction_files()
    assert files, "Expected markdown files to scan"

    found_subcommands: set[str] = set()
    found_only_names: set[str] = set()
    invalid_hits: list[str] = []

    for md_path in files:
        rel = md_path.relative_to(STANDUP_ROOT).as_posix()
        text = md_path.read_text(encoding="utf-8")
        for seg in extract_code_segments(text):
            for match in _SUBCOMMAND_RE.finditer(seg):
                subcmd = match.group(1)
                found_subcommands.add(subcmd)
                if subcmd not in valid_subcommands:
                    invalid_hits.append(f"{rel}: unknown standup-tool subcommand {subcmd!r}")
            for match in _ONLY_FLAG_RE.finditer(seg):
                only_name = match.group(1)
                found_only_names.add(only_name)
                if only_name not in valid_only_names:
                    invalid_hits.append(f"{rel}: unknown --only verify target {only_name!r}")

    assert not invalid_hits, "Invalid CLI references found:\n" + "\n".join(invalid_hits)
    assert found_subcommands, "Expected at least one standup-tool subcommand reference"
    assert found_only_names, "Expected at least one --only reference"


def test_no_jira_write_tools_in_allowed_tools_frontmatter() -> None:
    """None of the 6 Jira write MCP tools may appear in allowed-tools frontmatter."""
    files = sorted(STANDUP_ROOT.glob("commands/*.md")) + sorted(
        STANDUP_ROOT.glob("skills/*/SKILL.md")
    )
    assert len(files) >= 3

    violations: list[str] = []
    for md_path in files:
        rel = md_path.relative_to(STANDUP_ROOT).as_posix()
        fm = parse_frontmatter(md_path.read_text(encoding="utf-8"))
        tools = parse_allowed_tools(fm)
        assert tools, f"{rel}: expected non-empty allowed-tools in frontmatter"
        for tool in tools:
            for write_suffix in JIRA_WRITE_TOOL_SUFFIXES:
                if tool == write_suffix or tool.endswith(f"__{write_suffix}"):
                    violations.append(f"{rel}: forbidden Jira write tool in allowed-tools: {tool}")

    assert not violations, "Forbidden Jira write tools found:\n" + "\n".join(violations)


@pytest.mark.parametrize("cmd_name", ["standup.md", "standup-preview.md"])
def test_commands_scope_bash_instead_of_allowing_it_unrestricted(cmd_name: str) -> None:
    """These commands read untrusted Slack/email/Jira/Drive content, so Bash is allowed only
    for the specific commands they run; anything else prompts the user."""
    cmd_path = STANDUP_ROOT / "commands" / cmd_name
    tools = parse_allowed_tools(parse_frontmatter(cmd_path.read_text(encoding="utf-8")))
    assert "Bash" not in tools
    scoped = [t for t in tools if t.startswith("Bash(")]
    assert "Bash(uv run --directory:*)" in scoped
    assert all(t.endswith(":*)") for t in scoped)


@pytest.mark.parametrize("cmd_name", ["standup.md", "standup-preview.md"])
def test_commands_include_all_13_read_only_jira_mcp_tools(cmd_name: str) -> None:
    """Both standup commands list all 13 read-only Atlassian MCP tools in allowed-tools."""
    cmd_path = STANDUP_ROOT / "commands" / cmd_name
    fm = parse_frontmatter(cmd_path.read_text(encoding="utf-8"))
    tools = set(parse_allowed_tools(fm))
    mcp_tools = {t for t in tools if t.startswith("mcp__")}
    assert mcp_tools == JIRA_READ_ONLY_TOOLS


@pytest.mark.parametrize(
    "agent_name",
    [
        "standup-draft.md",
        "standup-pr-review.md",
        "standup-priorities.md",
        "standup-signals.md",
    ],
)
def test_agents_restrict_tools_and_place_config_before_data_boundary(agent_name: str) -> None:
    """All four agents use `tools: Read, Grep, Write` and explain `<config>` before DATA."""
    agent_path = STANDUP_ROOT / "agents" / agent_name
    text = agent_path.read_text(encoding="utf-8")
    assert len(text) < 13_000, f"{agent_name} exceeds 13,000-char context budget ({len(text)})"

    fm = parse_frontmatter(text)
    assert fm.get("name") == agent_name.removesuffix(".md")
    assert isinstance(fm.get("description"), str) and fm["description"]
    assert fm.get("tools") == "Read, Grep, Write"

    config_idx = text.find("<config>")
    boundary_idx = text.find("IMPORTANT:")
    assert config_idx != -1, f"{agent_name} missing <config> section"
    assert boundary_idx != -1, f"{agent_name} missing IMPORTANT: data-boundary instruction"
    assert config_idx < boundary_idx, f"{agent_name} must place <config> before data boundary"


def test_standup_signals_has_no_bash_or_wc_l() -> None:
    """standup-signals.md must not mention Bash or `wc -l`."""
    signals_path = STANDUP_ROOT / "agents" / "standup-signals.md"
    text = signals_path.read_text(encoding="utf-8")
    assert re.search(r"Bash|wc -l", text) is None


@pytest.mark.parametrize("cmd_name", ["standup.md", "standup-preview.md"])
def test_commands_stay_under_context_budget(cmd_name: str) -> None:
    """Command files stay under 13,000 characters (<3,250 tokens)."""
    cmd_path = STANDUP_ROOT / "commands" / cmd_name
    text = cmd_path.read_text(encoding="utf-8")
    assert len(text) < 13_000, f"{cmd_name} exceeds 13,000-char context budget ({len(text)})"


def test_referenced_plugin_root_reference_files_exist_on_disk() -> None:
    """Every `${CLAUDE_PLUGIN_ROOT}/references/*.md` in commands/agents exists on disk."""
    files = sorted(STANDUP_ROOT.glob("commands/*.md")) + sorted(STANDUP_ROOT.glob("agents/*.md"))
    assert files, "Expected command and agent files to scan"

    referenced_names: set[str] = set()
    missing_refs: list[str] = []
    relative_links: list[str] = []

    for md_path in files:
        rel = md_path.relative_to(STANDUP_ROOT).as_posix()
        text = md_path.read_text(encoding="utf-8")
        for match in _REL_REF_LINK_RE.finditer(text):
            relative_links.append(f"{rel}: forbidden relative reference link {match.group(0)}")
        for match in _PLUGIN_REF_RE.finditer(text):
            ref_name = match.group(1)
            referenced_names.add(ref_name)
            ref_path = STANDUP_ROOT / "references" / ref_name
            if not ref_path.is_file():
                missing_refs.append(f"{rel}: missing reference file references/{ref_name}")

    assert not relative_links, "Relative links found in commands/agents:\n" + "\n".join(
        relative_links
    )
    assert not missing_refs, "Missing reference files:\n" + "\n".join(missing_refs)
    assert referenced_names == {"data-quality.md", "jira-review.md", "debrief.md"}


def test_commands_spawn_namespaced_subagent_types() -> None:
    """Commands spawn only namespaced `standup:standup-*` agents that exist in `agents/`."""
    valid_agent_types = {
        f"standup:{p.stem}" for p in sorted((STANDUP_ROOT / "agents").glob("*.md"))
    }
    assert valid_agent_types == {
        "standup:standup-draft",
        "standup:standup-pr-review",
        "standup:standup-priorities",
        "standup:standup-signals",
    }

    standup_text = (STANDUP_ROOT / "commands" / "standup.md").read_text(encoding="utf-8")
    standup_agents = set(_SUBAGENT_TYPE_RE.findall(standup_text))
    assert standup_agents == {
        "standup:standup-draft",
        "standup:standup-pr-review",
        "standup:standup-priorities",
    }
    assert standup_agents <= valid_agent_types

    preview_text = (STANDUP_ROOT / "commands" / "standup-preview.md").read_text(encoding="utf-8")
    preview_agents = set(_SUBAGENT_TYPE_RE.findall(preview_text))
    assert preview_agents == {
        "standup:standup-signals",
        "standup:standup-priorities",
    }
    assert preview_agents <= valid_agent_types


def test_commands_and_references_enforce_security_boundaries() -> None:
    """Commands and jira-review reference enforce untrusted-data tags, key regex, and chmod 600."""
    for cmd_name in ("standup.md", "standup-preview.md"):
        text = (STANDUP_ROOT / "commands" / cmd_name).read_text(encoding="utf-8")
        assert "<collector-status>" in text
        assert "<jira-data>" in text
        assert "^[A-Z][A-Z0-9_]*-[0-9]+$" in text
        assert "chmod 600" in text

    jira_ref = (STANDUP_ROOT / "references" / "jira-review.md").read_text(encoding="utf-8")
    assert "<jira-data>" in jira_ref
    assert "</jira-data>" in jira_ref
    assert "^[A-Z][A-Z0-9_]*-[0-9]+$" in jira_ref
    assert "AskUserQuestion" in jira_ref


def test_helper_parsers_and_negative_detection_edge_cases() -> None:
    """Helper functions handle missing/string/list frontmatter and detect invalid CLI tokens."""
    assert parse_frontmatter("No frontmatter here\n") == {}
    assert parse_frontmatter("---\nunterminated: true\n") == {}
    assert parse_frontmatter("---\n- not-a-dict\n---\nbody\n") == {}

    fm_str = parse_frontmatter("---\nallowed-tools: Read, Grep Write\n---\nbody\n")
    assert parse_allowed_tools(fm_str) == ["Read", "Grep", "Write"]

    fm_list = parse_frontmatter("---\nallowed-tools:\n  - Bash\n  - Read\n---\nbody\n")
    assert parse_allowed_tools(fm_list) == ["Bash", "Read"]
    assert parse_allowed_tools({}) == []

    sample_md = (
        "Prose mentioning `standup-tool bogus-subcmd` and:\n"
        "```bash\n"
        "uv run standup-tool verify --only invalidtarget\n"
        "```\n"
    )
    segments = extract_code_segments(sample_md)
    subcmds = [m.group(1) for seg in segments for m in _SUBCOMMAND_RE.finditer(seg)]
    only_flags = [m.group(1) for seg in segments for m in _ONLY_FLAG_RE.finditer(seg)]
    assert subcmds == ["verify", "bogus-subcmd"]
    assert only_flags == ["invalidtarget"]
    assert "bogus-subcmd" not in subcommand_names(build_parser())
    assert "invalidtarget" not in VERIFY_NAMES
