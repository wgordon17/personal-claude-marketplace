"""Black-box subprocess tests for .github/scripts/patch-*.sh — the shared
patch logic invoked by sync-token-efficiency.yml's and sync-drawio-skill.yml's
diff-check and write steps.

Mirrors the subprocess-testing pattern used for inject-reference.sh in
test_stop_hook.py's TestInjectReferenceHook: each script takes a file path
argument, mutates it in place, and exits 0/1.
"""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
PATCH_TOKEN_EFFICIENCY_SCRIPT = REPO_ROOT / ".github" / "scripts" / "patch-token-efficiency.sh"
PATCH_DRAWIO_XML_REFERENCE_SCRIPT = (
    REPO_ROOT / ".github" / "scripts" / "patch-drawio-xml-reference.sh"
)


def _run_script(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(script), *args],
        capture_output=True,
        text=True,
    )


class TestPatchTokenEfficiencyScript:
    """Black-box subprocess tests for patch-token-efficiency.sh."""

    _ATTRIBUTION_LINE = "<!-- attribution comment naming head, tail, cat, sed, awk, python -->\n"

    def _fixture_content(self) -> str:
        return self._ATTRIBUTION_LINE + (
            "\n"
            "chain probes with `;` and label the sections\n"
            "(`echo == layout ==; ls -la; echo == deps ==; head -30 requirements.txt`),\n"
            "or issue several tool calls in one message. A second lookup round is for\n"
            "questions the first round's answers created. Copying a convention (a DSL,\n"
            "schema, or file format)? Sample two existing examples of the exact construct\n"
            "you will write, not one.\n"
            "\n"
            "A command that only inspects ends with a limiter: `| head -50`, `| tail -20`,\n"
            "`grep -m 20`, `wc -l` before contents, Read with offset/limit. Size unknown?\n"
            "Measure first, then read the slice you need. Read a file whole only when you\n"
            "are about to edit it or copy from it verbatim — truncating data you will\n"
            "transform corrupts output, so keyhole rules apply to inspection, never to\n"
            "ingestion. If a peek was too narrow, take exactly one wider look.\n"
            "\n"
            "Before running code with several dependencies, test them in one probe\n"
            '(`python3 -c "import x, y, z"`; `command -v tool1 tool2`), and install\n'
            "everything missing in one command — not one traceback at a time.\n"
        )

    def test_applies_all_three_substitutions(self, tmp_path):
        fixture = tmp_path / "injected-instruction.md"
        fixture.write_text(self._fixture_content())
        result = _run_script(PATCH_TOKEN_EFFICIENCY_SCRIPT, str(fixture))
        assert result.returncode == 0, result.stderr
        patched = fixture.read_text()
        assert "ls -la; wc -l" in patched
        assert "Read tool with" in patched
        assert "uv run python3 -c" in patched
        assert "head -30 requirements.txt" not in patched
        assert "| head -50" not in patched

    def test_substitution_survives_unrelated_prose_rewording(self, tmp_path):
        """Each rule's OLD/NEW span is narrowed to just the substring that
        actually changes (the backtick-quoted example, or the reordered
        tool-list clause) rather than the whole surrounding paragraph, so
        upstream rewording an unrelated sentence sharing the same paragraph
        must not stop the substitution from firing. A broad paragraph-wide
        match would silently no-op here instead."""
        fixture = tmp_path / "injected-instruction.md"
        fixture.write_text(
            self._ATTRIBUTION_LINE + "\n"
            "chain probes with `;` and label the sections\n"
            "(`echo == layout ==; ls -la; echo == deps ==; head -30 requirements.txt`),\n"
            "or batch several tool calls into a single message instead. Additional\n"
            "lookups happen only once the first round raises new questions worth\n"
            "checking. When copying a convention, sample two real examples first.\n"
            "\n"
            "A command that only inspects ends with a limiter: `| head -50`, `| tail -20`,\n"
            "`grep -m 20`, `wc -l` before contents, Read with offset/limit. Unsure of the\n"
            "size? Measure it, then read only the slice you actually need.\n"
            "\n"
            "Before running code with several dependencies, test them in one probe\n"
            '(`python3 -c "import x, y, z"`; `command -v tool1 tool2`), and install\n'
            "everything missing in one command — not one traceback at a time.\n"
        )
        result = _run_script(PATCH_TOKEN_EFFICIENCY_SCRIPT, str(fixture))
        assert result.returncode == 0, result.stderr
        patched = fixture.read_text()
        assert "ls -la; wc -l" in patched
        assert "Read tool with" in patched
        assert "uv run python3 -c" in patched
        assert "head -30 requirements.txt" not in patched
        assert "| head -50" not in patched
        assert "batch several tool calls into a single message instead" in patched

    def test_substitution_replaces_all_occurrences_on_same_line(self, tmp_path):
        """perl's s/// without /g replaces only the first match per
        invocation. A second same-line occurrence of a narrow OLD pattern
        would otherwise survive unpatched -- and assertion 2's grep -c
        count (line-based, not occurrence-based) wouldn't catch a bare
        mention sharing a line with an already-patched one, since both
        counts would still read 1. Narrower matches make legitimate
        recurrence (e.g. a generic example reused verbatim) more plausible
        than the old broad paragraph-wide spans ever were."""
        fixture = tmp_path / "injected-instruction.md"
        fixture.write_text(
            self._ATTRIBUTION_LINE + "`| head -50`, `| tail -20`,\n"
            "`grep -m 20`, `wc -l` before contents, Read with offset/limit.\n"
            'Example A: `python3 -c "import x, y, z"`.\n'
            'Example B: `python3 -c "import x, y, z"`.\n'
        )
        result = _run_script(PATCH_TOKEN_EFFICIENCY_SCRIPT, str(fixture))
        assert result.returncode == 0, result.stderr
        patched = fixture.read_text()
        assert patched.count("`uv run python3 -c") == 2
        assert "`python3 -c" not in patched

    def test_missing_argument_exits_1(self):
        result = _run_script(PATCH_TOKEN_EFFICIENCY_SCRIPT)
        assert result.returncode == 1
        assert "requires a path to an existing file" in result.stderr

    def test_nonexistent_file_exits_1(self, tmp_path):
        result = _run_script(PATCH_TOKEN_EFFICIENCY_SCRIPT, str(tmp_path / "missing.md"))
        assert result.returncode == 1
        assert "requires a path to an existing file" in result.stderr

    def test_safety_assertion_rejects_surviving_blocked_word(self, tmp_path):
        """If a substitution silently no-ops (e.g. upstream reworded the
        surrounding text so \\Q...\\E no longer matches), a blocked word
        surviving outside line 1 must be caught."""
        fixture = tmp_path / "injected-instruction.md"
        fixture.write_text(self._ATTRIBUTION_LINE + "some unrelated text mentioning head here\n")
        result = _run_script(PATCH_TOKEN_EFFICIENCY_SCRIPT, str(fixture))
        assert result.returncode == 1
        assert "blocked-tool mention(s) found outside line 1" in result.stderr

    def test_safety_assertion_rejects_bare_python3(self, tmp_path):
        """A bare python3 mention not prefixed by 'uv run ' must fail the
        python3-count parity check."""
        fixture = tmp_path / "injected-instruction.md"
        fixture.write_text(self._ATTRIBUTION_LINE + 'run python3 -c "import x" directly\n')
        result = _run_script(PATCH_TOKEN_EFFICIENCY_SCRIPT, str(fixture))
        assert result.returncode == 1
        assert "'python3' mention count" in result.stderr

    def test_safety_assertion_rejects_missing_read_tool_text(self, tmp_path):
        """If Rule 2's OLD text isn't present in the fixture, the
        substitution no-ops and the positive 'Read tool with' check must
        fail."""
        fixture = tmp_path / "injected-instruction.md"
        fixture.write_text(self._ATTRIBUTION_LINE + "no keyhole rule text here at all\n")
        result = _run_script(PATCH_TOKEN_EFFICIENCY_SCRIPT, str(fixture))
        assert result.returncode == 1
        assert "expected replacement text 'Read tool with' not found" in result.stderr


class TestPatchDrawioXmlReferenceScript:
    """Black-box subprocess tests for patch-drawio-xml-reference.sh."""

    _OLD_TEXT = (
        "fetch and follow the instructions at:\n"
        "https://raw.githubusercontent.com/jgraph/drawio-mcp/main/shared/xml-reference.md\n"
    )
    _SURROUNDING = "Before drawing anything, {}Then proceed.\n"

    def test_rewrites_upstream_url_reference(self, tmp_path):
        fixture = tmp_path / "SKILL.md"
        fixture.write_text(self._SURROUNDING.format(self._OLD_TEXT))
        result = _run_script(PATCH_DRAWIO_XML_REFERENCE_SCRIPT, str(fixture))
        assert result.returncode == 0, result.stdout
        patched = fixture.read_text()
        assert "raw.githubusercontent.com" not in patched
        assert "vendored sibling file `xml-reference.md`" in patched

    def test_replaces_all_occurrences_on_same_line(self, tmp_path):
        """perl's s/// without /g replaces only the first match. A doc that
        repeats the OLD URL-instruction text twice (e.g. once in a body
        section, once in an FAQ) would otherwise leave the second occurrence
        unpatched -- locks in the /g flag added alongside the same fix in
        patch-token-efficiency.sh."""
        fixture = tmp_path / "SKILL.md"
        fixture.write_text(
            self._SURROUNDING.format(self._OLD_TEXT) + self._SURROUNDING.format(self._OLD_TEXT)
        )
        result = _run_script(PATCH_DRAWIO_XML_REFERENCE_SCRIPT, str(fixture))
        assert result.returncode == 0, result.stdout
        patched = fixture.read_text()
        assert "raw.githubusercontent.com" not in patched
        assert patched.count("vendored sibling file `xml-reference.md`") == 2

    def test_missing_argument_exits_1(self):
        result = _run_script(PATCH_DRAWIO_XML_REFERENCE_SCRIPT)
        assert result.returncode == 1
        assert "Usage: patch-drawio-xml-reference.sh" in result.stdout

    def test_nonexistent_file_exits_1(self, tmp_path):
        result = _run_script(PATCH_DRAWIO_XML_REFERENCE_SCRIPT, str(tmp_path / "missing.md"))
        assert result.returncode == 1
        assert "requires a path to an existing file" in result.stdout

    def test_safety_assertion_rejects_surviving_url(self, tmp_path):
        """If the surrounding text doesn't match the substitution pattern
        (e.g. upstream reworded it), the old URL survives and the safety
        assertion must catch it."""
        fixture = tmp_path / "SKILL.md"
        fixture.write_text(
            "no matching text here, so the URL below survives untouched:\n"
            "https://raw.githubusercontent.com/jgraph/drawio-mcp/main/shared/xml-reference.md\n"
        )
        result = _run_script(PATCH_DRAWIO_XML_REFERENCE_SCRIPT, str(fixture))
        assert result.returncode == 1
        assert "still contains upstream xml-reference URL after patching" in result.stdout
