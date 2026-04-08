#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["anthropic[vertex]>=0.40.0", "python-frontmatter>=1.1.0"]
# ///
"""atlas-health-llm.py — Vertex AI semantic analysis for ATLAS health.

Detects frontmatter drift (description vs body) and semantic duplication
(redundant reference docs) using Claude Sonnet via Vertex AI.

Usage:
    uv run .claude/commands/atlas-health-llm.py          # full analysis
    uv run .claude/commands/atlas-health-llm.py --dry-run  # prompt preview only

Environment variables:
    ANTHROPIC_VERTEX_PROJECT_ID      -- GCP project ID (required)
    CLOUD_ML_REGION                  -- Vertex AI region (default: us-east5)
    ANTHROPIC_DEFAULT_SONNET_MODEL   -- model override (default: claude-sonnet-4-6)

Exit codes:
    0 -- no critical findings (push proceeds) or fail-open
    1 -- critical findings found (push blocked)

Fails open (exits 0) on any infrastructure error (missing credentials, import, timeout).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn

sys.path.insert(0, str(Path(__file__).parent))
from _atlas_lib import (  # noqa: E402
    Agent,
    Skill,
    list_reference_docs,
    parse_agents,
    parse_marketplace,
    parse_skills,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Strip Claude Code context-window suffixes like [1m] — Vertex AI doesn't accept them
_RAW_MODEL = os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-4-6")
_MODEL = re.sub(r"\[.*\]$", "", _RAW_MODEL)
_MAX_TOKENS = 2048
_TIMEOUT = 30  # seconds per call — ensures at least 2 calls fit within 60s budget
_WALL_BUDGET = 60  # total cumulative seconds across all Vertex AI calls
_BODY_THRESHOLD = 500  # minimum body chars to analyze for drift
_BATCH_SIZE = 5  # components per drift detection call
_DRY_RUN_BODY_PREVIEW = 300  # chars to show in dry-run prompt previews

# ---------------------------------------------------------------------------
# Fail-open handler
# ---------------------------------------------------------------------------


def _fail_open(reason: str) -> NoReturn:
    """Print a skip message and exit 0 (fail-open on infrastructure errors)."""
    print(f"ATLAS health check skipped — {reason}")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Content filtering (redact secrets before sending to Vertex AI)
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = [
    # Env var assignments with long values (likely API keys)
    re.compile(r'([A-Z_]+=)["\']([A-Za-z0-9+/]{20,})["\']'),
    # Bearer tokens
    re.compile(r"(Bearer )([A-Za-z0-9\-._~+/]+=*)"),
]

_PEM_HEADER = re.compile(r"-----BEGIN\s+\S.*?-----")


def _redact_secrets(text: str, file_path: Path) -> str:
    """Redact common secret patterns from text before sending to Vertex AI.

    Replaces secret values with [REDACTED]. Logs a warning if any redactions occur.
    This is best-effort defense-in-depth — skill/agent files should not contain secrets.
    """
    redacted = False
    lines = text.splitlines(keepends=True)
    result_lines: list[str] = []
    for line in lines:
        original = line
        # PEM headers
        if _PEM_HEADER.search(line):
            line = _PEM_HEADER.sub("[REDACTED]", line)
        # Env var and bearer token patterns
        for pattern in _SECRET_PATTERNS:
            line = pattern.sub(r"\g<1>[REDACTED]", line)
        if line != original:
            redacted = True
        result_lines.append(line)
    if redacted:
        print(
            f"WARNING: redacted potential secrets from {file_path} before sending to Vertex AI",
            file=sys.stderr,
        )
    return "".join(result_lines)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _build_drift_prompt(components: list[tuple[str, str, str, str]]) -> str:
    """Build a batched drift detection prompt.

    Args:
        components: List of (name, type, description, body_excerpt) tuples.

    Returns:
        Prompt string for Vertex AI.
    """
    lines = [
        "Compare each component's frontmatter description to its body content.",
        "Report ONLY factual contradictions — not stylistic differences, not missing details.",
        "A contradiction is when the description claims something the body explicitly denies",
        "or vice versa.",
        "",
        "Components:",
        "",
    ]
    for i, (name, comp_type, description, body) in enumerate(components, 1):
        lines.append(f"[{i}] {name} (type: {comp_type})")
        lines.append(f"Frontmatter description: {description}")
        lines.append(f"Body content (first 2000 chars): {body}")
        lines.append("")

    lines += [
        "Respond with a JSON array, one entry per component:",
        "[",
        '  {"component": 1, "drift": true|false, "severity": "CRITICAL"|"INFO"|null,',
        '   "findings": ["finding1", ...] | null},',
        "  ...",
        "]",
        "",
        "Severity guide:",
        "- CRITICAL: The description makes a factual claim that the body explicitly contradicts",
        "  (e.g., 'spawns 21 agents' but body defines 19).",
        "- INFO: The body has capabilities or behaviors the description omits, but nothing is",
        "  contradicted.",
        "- null: No drift detected.",
    ]
    return "\n".join(lines)


def _build_duplication_prompt(
    filename_a: str, content_a: str, filename_b: str, content_b: str
) -> str:
    """Build a semantic duplication detection prompt for a single pair of reference docs.

    Args:
        filename_a: Name of the first reference document.
        content_a: First 1500 chars of the first document.
        filename_b: Name of the second reference document.
        content_b: First 1500 chars of the second document.

    Returns:
        Prompt string for Vertex AI.
    """
    lines = [
        "Compare these two reference documents for semantic overlap.",
        "Report ONLY if they cover substantially the same topic with redundant content.",
        "Related but complementary documents are NOT duplicates.",
        "",
        f"Doc A: {filename_a}",
        f"Content (first 1500 chars): {content_a}",
        "",
        f"Doc B: {filename_b}",
        f"Content (first 1500 chars): {content_b}",
        "",
        'Respond with JSON: {"duplicate": true|false, "severity": "CRITICAL"|"INFO"|null,',
        '                    "overlap_description": "..." | null}',
        "",
        "Severity guide:",
        "- CRITICAL: The documents are substantially redundant — maintaining one would make the",
        "  other obsolete.",
        "- INFO: The documents overlap significantly but each contains material the other lacks.",
        "- null: Not duplicates.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Vertex AI client
# ---------------------------------------------------------------------------


def _call_vertex(prompt: str) -> str:
    """Call Claude via Vertex AI. Returns raw response text."""
    try:
        from anthropic import AnthropicVertex  # type: ignore[import-untyped]
    except ImportError as e:
        _fail_open(f"anthropic[vertex] not available: {e}")

    project_id = os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", "")
    region = os.environ.get("CLOUD_ML_REGION", "us-east5")

    if not project_id:
        _fail_open("ANTHROPIC_VERTEX_PROJECT_ID not set")

    try:
        client = AnthropicVertex(project_id=project_id, region=region)
        message = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            timeout=_TIMEOUT,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        _fail_open(f"Vertex AI call failed: {type(e).__name__}: {e}")

    text = ""
    for block in message.content:
        if hasattr(block, "text"):
            text = block.text.strip()  # type: ignore[union-attr]
            break

    if not text:
        _fail_open("empty response from Vertex AI")

    # Strip markdown fences if present (model may wrap JSON in ```)
    if text.startswith("```"):
        text_lines = text.splitlines()
        text = "\n".join(line for line in text_lines if not line.startswith("```")).strip()

    return text


# ---------------------------------------------------------------------------
# Finding data structures
# ---------------------------------------------------------------------------


from dataclasses import dataclass  # noqa: E402 (after sys.path insert)


@dataclass
class Finding:
    severity: str  # "CRITICAL" or "INFO"
    category: str  # "drift" or "duplication"
    component: str  # file path or doc names
    message: str


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------


def _analyze_drift(
    skills: list[Skill],
    agents: list[Agent],
    start_time: float,
    dry_run: bool,
) -> list[Finding]:
    """Detect frontmatter drift across skills and agents (batched, 5 per call).

    Skips components with body content shorter than _BODY_THRESHOLD chars.
    Aborts batch processing if cumulative wall-clock time exceeds _WALL_BUDGET.
    """
    findings: list[Finding] = []

    # Build list of (name, type, description, body, path) for analysis
    candidates: list[tuple[str, str, str, str, Path]] = []
    for skill in skills:
        body = skill.body
        if not body:
            try:
                body = skill.path.read_text()
            except Exception:
                continue
        if len(body) >= _BODY_THRESHOLD:
            desc = skill.description or ""
            body_filtered = _redact_secrets(body[:2000], skill.path)
            candidates.append((skill.name, "skill", desc, body_filtered, skill.path))

    for agent in agents:
        body = agent.body
        if not body:
            try:
                body = agent.path.read_text()
            except Exception:
                continue
        if len(body) >= _BODY_THRESHOLD:
            desc = agent.description or ""
            body_filtered = _redact_secrets(body[:2000], agent.path)
            candidates.append((agent.name, "agent", desc, body_filtered, agent.path))

    # Process in batches of _BATCH_SIZE
    for batch_start in range(0, len(candidates), _BATCH_SIZE):
        # Check wall-clock budget before each batch
        if time.monotonic() - start_time > _WALL_BUDGET:
            print("Analysis incomplete (timeout) — drift detection budget exhausted.")
            break

        batch = candidates[batch_start : batch_start + _BATCH_SIZE]
        prompt_components = [(name, typ, desc, body) for name, typ, desc, body, _ in batch]
        prompt = _build_drift_prompt(prompt_components)

        if dry_run:
            print(f"\n--- Drift prompt (batch {batch_start // _BATCH_SIZE + 1}) ---")
            for name, typ, desc, body_preview in prompt_components:
                preview = body_preview[:_DRY_RUN_BODY_PREVIEW]
                print(f"  [{typ}] {name}: {desc[:80]}... | body: {preview}...")
            continue

        try:
            raw = _call_vertex(prompt)
            results = json.loads(raw)
            if not isinstance(results, list):
                continue
        except (json.JSONDecodeError, ValueError):
            _fail_open(f"drift detection returned non-JSON: {raw[:200]}")
        except Exception:
            _fail_open("drift detection failed")

        for item in results:
            if not isinstance(item, dict):
                continue
            idx = item.get("component", 0)
            if not isinstance(idx, int) or idx < 1 or idx > len(batch):
                continue
            if not item.get("drift"):
                continue
            severity = item.get("severity")
            if severity not in ("CRITICAL", "INFO"):
                continue
            raw_findings = item.get("findings") or []
            component_findings = [str(f) for f in raw_findings if str(f).strip()]
            component_path = batch[idx - 1][4]
            for msg in component_findings:
                findings.append(
                    Finding(
                        severity=severity,
                        category="drift",
                        component=str(component_path),
                        message=msg,
                    )
                )

    return findings


def _analyze_duplication(
    plugins: list,
    start_time: float,
    dry_run: bool,
) -> list[Finding]:
    """Detect semantic duplication among reference docs (one pair per call).

    Compares docs within the same plugin and cross-plugin docs with the same basename.
    Aborts if cumulative wall-clock time exceeds _WALL_BUDGET.
    """
    findings: list[Finding] = []

    # Gather all reference docs by plugin
    plugin_refs: dict[str, list[Path]] = {}
    for plugin in plugins:
        refs = list_reference_docs(plugin.source_path)
        if refs:
            plugin_refs[plugin.name] = refs

    # Build pairs to compare
    pairs: list[tuple[Path, Path]] = []

    # Within-plugin pairs
    for _plugin_name, refs in plugin_refs.items():
        for i, ref_a in enumerate(refs):
            for ref_b in refs[i + 1 :]:
                pairs.append((ref_a, ref_b))

    # Cross-plugin pairs (same basename only)
    all_refs: list[Path] = []
    for refs in plugin_refs.values():
        all_refs.extend(refs)
    basename_map: dict[str, list[Path]] = {}
    for ref in all_refs:
        basename_map.setdefault(ref.name, []).append(ref)
    for _basename, paths in basename_map.items():
        if len(paths) > 1:
            for i, ref_a in enumerate(paths):
                for ref_b in paths[i + 1 :]:
                    pair = (ref_a, ref_b)
                    if pair not in pairs:
                        pairs.append(pair)

    for ref_a, ref_b in pairs:
        # Check wall-clock budget before each call
        if time.monotonic() - start_time > _WALL_BUDGET:
            print("Analysis incomplete (timeout) — duplication detection budget exhausted.")
            break

        try:
            content_a = ref_a.read_text()[:1500]
            content_b = ref_b.read_text()[:1500]
        except Exception:
            continue

        content_a = _redact_secrets(content_a, ref_a)
        content_b = _redact_secrets(content_b, ref_b)

        prompt = _build_duplication_prompt(ref_a.name, content_a, ref_b.name, content_b)

        if dry_run:
            print(f"\n--- Duplication prompt: {ref_a.name} ↔ {ref_b.name} ---")
            print(f"  A ({len(content_a)} chars preview) ↔ B ({len(content_b)} chars preview)")
            continue

        try:
            raw = _call_vertex(prompt)
            result = json.loads(raw)
            if not isinstance(result, dict):
                continue
        except (json.JSONDecodeError, ValueError):
            _fail_open(f"duplication detection returned non-JSON: {raw[:200]}")
        except Exception:
            _fail_open("duplication detection failed")

        if not result.get("duplicate"):
            continue
        severity = result.get("severity")
        if severity not in ("CRITICAL", "INFO"):
            continue
        overlap = result.get("overlap_description") or "Semantic overlap detected"
        findings.append(
            Finding(
                severity=severity,
                category="duplication",
                component=f"{ref_a.name} ↔ {ref_b.name}",
                message=str(overlap),
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _format_output(findings: list[Finding]) -> tuple[str, int]:
    """Format findings for terminal output. Returns (output_text, exit_code)."""
    criticals = [f for f in findings if f.severity == "CRITICAL"]
    infos = [f for f in findings if f.severity == "INFO"]

    if not findings:
        return "ATLAS Health Check passed.", 0

    lines: list[str] = []

    if criticals:
        lines.append(f"ATLAS Health Check BLOCKED ({len(criticals)} critical)")
        lines.append("")
        for finding in criticals:
            lines.append(f"[CRITICAL] {finding.component}")
            lines.append(f"  {finding.message}")
            lines.append("")
        if infos:
            for finding in infos:
                lines.append(f"[INFO] {finding.component}")
                lines.append(f"  {finding.message}")
                lines.append("")
        lines.append("Fix critical findings before pushing.")
        return "\n".join(lines), 1

    # Info only
    lines.append(f"ATLAS Health Check ({len(infos)} info findings)")
    lines.append("")
    for finding in infos:
        lines.append(f"[INFO] {finding.component}")
        lines.append(f"  {finding.message}")
        lines.append("")
    lines.append("Push proceeding.")
    return "\n".join(lines), 0


# ---------------------------------------------------------------------------
# Entry point
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Vertex AI semantic health analysis for ATLAS components",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print prompt previews without calling Vertex AI; exits 0",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repository root (default: git rev-parse --show-toplevel)",
    )
    args = parser.parse_args()

    # Check credentials before doing any parsing (fail-open on missing)
    project_id = os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID", "")
    if not project_id and not args.dry_run:
        _fail_open("ANTHROPIC_VERTEX_PROJECT_ID not set")

    # Resolve repo root
    if args.repo_root is not None:
        repo_root = args.repo_root.resolve()
    else:
        try:
            repo_root = _repo_root_from_git(Path.cwd())
        except subprocess.CalledProcessError:
            _fail_open("not in a git repository — could not determine repo root")

    # Parse all components
    try:
        plugins = parse_marketplace(repo_root)
    except Exception as e:
        _fail_open(f"failed to parse marketplace.json: {e}")

    all_skills: list[Skill] = []
    all_agents: list[Agent] = []
    for plugin in plugins:
        all_skills.extend(parse_skills(plugin.source_path, plugin.name))
        all_agents.extend(parse_agents(plugin.source_path, plugin.name))

    if args.dry_run:
        print("=== ATLAS Health Check DRY RUN ===")
        print(f"Model: {_MODEL}")
        print(f"Repo: {repo_root}")
        print(f"Components: {len(all_skills)} skills, {len(all_agents)} agents")
        print(f"Wall-clock budget: {_WALL_BUDGET}s | Per-call timeout: {_TIMEOUT}s")
        print()

    start_time = time.monotonic()

    # Drift detection (priority: run before duplication)
    try:
        drift_findings = _analyze_drift(all_skills, all_agents, start_time, args.dry_run)
    except SystemExit:
        raise
    except Exception as e:
        _fail_open(f"drift analysis failed: {type(e).__name__}: {e}")

    # Duplication detection (lower priority — may be skipped if budget exhausted)
    try:
        dup_findings = _analyze_duplication(plugins, start_time, args.dry_run)
    except SystemExit:
        raise
    except Exception as e:
        _fail_open(f"duplication analysis failed: {type(e).__name__}: {e}")

    if args.dry_run:
        print("\n=== DRY RUN COMPLETE — no Vertex AI calls made ===")
        sys.exit(0)

    all_findings = drift_findings + dup_findings
    output, exit_code = _format_output(all_findings)
    print(output)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
