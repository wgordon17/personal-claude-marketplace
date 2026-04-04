#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# ///
import re
import sys
from pathlib import Path

EXPECTED_SECTIONS = frozenset(
    {
        "skill-artifacts",
        "swarm-phases",
        "quality-gate-layers",
        "code-quality-agents",
        "code-quality-skills",
        "code-quality-commands",
        "dev-guard-hooks",
        "dev-guard-commands",
        "git-tools",
        "github-mcp",
        "lsp-plugins",
        "reference-docs",
        "mcp-integrations",
        "marketplace-registry",
    }
)

MARKER_RE = re.compile(r"<!--\s*(BEGIN|END):AUTO\s+(\S+)\s*-->")
AUTO_KEYWORD_RE = re.compile(r"(BEGIN|END):AUTO")


def _is_marker_line(line: str) -> bool:
    """Return True only if the line contains a real AUTO marker (not inside backtick code)."""
    backtick_pos = line.find("`")
    comment_pos = line.find("<!--")
    if comment_pos == -1:
        return False
    # If backtick appears before the <!-- , the marker is inside inline code — skip it
    return not (backtick_pos != -1 and backtick_pos < comment_pos)


def main() -> int:
    atlas_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("ATLAS.md")

    # Check 1: file existence
    if not atlas_path.exists():
        print(f"ERROR: {atlas_path} not found.", file=sys.stderr)
        return 1

    lines = atlas_path.read_text(encoding="utf-8").splitlines()

    # Check 2: marker syntax
    errors = []
    for i, line in enumerate(lines, start=1):
        if AUTO_KEYWORD_RE.search(line) and _is_marker_line(line) and not MARKER_RE.search(line):
            errors.append(f"  Line {i}: malformed marker: {line.strip()}")
    if errors:
        print("ERROR: Malformed AUTO markers:", file=sys.stderr)
        for e in errors:
            print(e, file=sys.stderr)
        return 1

    # Check 3: pairing and ordering
    stack: list[tuple[str, int]] = []
    found: set[str] = set()
    for i, line in enumerate(lines, start=1):
        if not _is_marker_line(line):
            continue
        m = MARKER_RE.search(line)
        if not m:
            continue
        kind, name = m.group(1), m.group(2)
        if kind == "BEGIN":
            if stack:
                print(
                    f"ERROR: Nested marker at line {i}: BEGIN:{name} inside BEGIN:{stack[-1][0]}",
                    file=sys.stderr,
                )
                return 1
            stack.append((name, i))
        else:  # END
            if not stack:
                print(f"ERROR: Unmatched END:{name} at line {i} (no open BEGIN)", file=sys.stderr)
                return 1
            open_name, open_line = stack.pop()
            if open_name != name:
                print(
                    f"ERROR: Mismatched markers: BEGIN:{open_name} (line {open_line})"
                    f" closed by END:{name} (line {i})",
                    file=sys.stderr,
                )
                return 1
            found.add(name)

    if stack:
        for name, line_no in stack:
            print(
                f"ERROR: Unmatched BEGIN:{name} at line {line_no} (no closing END)", file=sys.stderr
            )
        return 1

    # Check 4: expected section coverage
    missing = EXPECTED_SECTIONS - found
    extra = found - EXPECTED_SECTIONS
    if missing:
        print("ERROR: Missing expected sections:", file=sys.stderr)
        for s in sorted(missing):
            print(f"  {s}", file=sys.stderr)
        return 1
    if extra:
        print(f"WARNING: Extra (unexpected) sections found: {', '.join(sorted(extra))}")

    print(
        f"ATLAS.md marker validation passed ({len(found)}/{len(EXPECTED_SECTIONS)} sections found)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
