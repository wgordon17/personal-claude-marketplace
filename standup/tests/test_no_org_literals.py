"""Guard tests preventing organization literals, real team PII, or secret patterns in standup/."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

STANDUP_ROOT = Path(__file__).resolve().parents[1]

SKIP_DIR_NAMES = frozenset(
    {
        ".venv",
        ".cache",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
    }
)

SKIP_RELATIVE_PATHS = frozenset(
    {
        Path("uv.lock"),
        Path("tests/test_no_org_literals.py"),
        Path(".claude-plugin/plugin.json"),
        Path("plugin.json"),
    }
)

DENYLIST_FRAGMENTS = (
    r"redhat",
    r"red hat",
    r"rhat",
    r"kerberos",
    r"osac",
    r"wgordon",
    r"\bgordon\b",
    r"wg-[a-z]",
    r"@redhat\.com",
    r"\b[CU]0[0-9A-Z]{8,10}\b",
    r"MGMT-[0-9]+",
    r"xox[a-z]-",
)

DENYLIST_RE = re.compile("|".join(DENYLIST_FRAGMENTS), re.IGNORECASE)

REAL_TEAM_KEYS = (
    "name",
    "email",
    "jira_email",
    "github",
    "slack_id",
    "kerberos_uid",
    "directory_uid",
)


def iter_standup_text_files(root: Path = STANDUP_ROOT) -> Iterator[tuple[Path, str]]:
    """Yield (relative_path, text) for every non-skipped UTF-8 file under standup/."""
    for dirpath, dirnames, filenames in os.walk(root):
        current_dir = Path(dirpath)
        dirnames[:] = [
            d
            for d in dirnames
            if d not in SKIP_DIR_NAMES and not (current_dir / d / "pyvenv.cfg").exists()
        ]
        if (current_dir / "pyvenv.cfg").exists():
            dirnames.clear()
            continue

        for filename in sorted(filenames):
            full_path = current_dir / filename
            rel_path = full_path.relative_to(root)
            if rel_path in SKIP_RELATIVE_PATHS:
                continue
            try:
                text = full_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            yield rel_path, text


def test_no_org_literals_in_standup_tree() -> None:
    """No file in standup/ may contain any organization literal or secret prefix."""
    hits: list[str] = []
    for rel_path, text in iter_standup_text_files():
        for lineno, line in enumerate(text.splitlines(), start=1):
            match = DENYLIST_RE.search(line)
            if match:
                hits.append(f"{rel_path.as_posix()}:{lineno}: matched {match.group(0)!r}")
    assert not hits, "Denylist matches found in standup/:\n" + "\n".join(hits)


@pytest.mark.parametrize(
    ("sample", "expected_fragment"),
    [
        ("internal redhat host", r"redhat"),
        ("Red Hat Enterprise Linux", r"red hat"),
        ("ldap attribute rhatWorkerId", r"rhat"),
        ("kerberos ticket expired", r"kerberos"),
        ("repo osac-project/main", r"osac"),
        ("author wgordon", r"wgordon"),
        ("assigned to Gordon today", r"\bgordon\b"),
        ("channel #wg-platform-eng", r"wg-[a-z]"),
        ("alice@redhat.com", r"@redhat\.com"),
        ("C012345678", r"\b[CU]0[0-9A-Z]{8,10}\b"),
        ("C0123456789", r"\b[CU]0[0-9A-Z]{8,10}\b"),
        ("U0123456789A", r"\b[CU]0[0-9A-Z]{8,10}\b"),
        ("issue MGMT-12345", r"MGMT-[0-9]+"),
        ("cookie xoxd-synthetic", r"xox[a-z]-"),
        ("token xoxc-synthetic", r"xox[a-z]-"),
        ("bot xoxb-synthetic", r"xox[a-z]-"),
    ],
)
def test_denylist_pattern_alternatives_match_samples(
    sample: str,
    expected_fragment: str,
) -> None:
    """Every alternative in DENYLIST_FRAGMENTS matches its intended synthetic sample."""
    assert expected_fragment in DENYLIST_FRAGMENTS
    fragment_re = re.compile(expected_fragment, re.IGNORECASE)
    assert fragment_re.search(sample) is not None
    assert DENYLIST_RE.search(sample) is not None


@pytest.mark.parametrize(
    "safe_sample",
    [
        "UEXAMPLE01",
        "UEXAMPLE02",
        "CEXAMPLE01",
        "Ada Example <ada@example.com>",
        "PROJ-123",
    ],
)
def test_denylist_allows_fabricated_placeholders(safe_sample: str) -> None:
    """Fabricated placeholders used in tests and examples do not trigger the denylist."""
    assert DENYLIST_RE.search(safe_sample) is None


def test_iter_standup_text_files_skips_pyvenv_cfg_dirs(tmp_path: Path) -> None:
    """Any directory containing pyvenv.cfg is skipped even if not named .venv."""
    custom_venv = tmp_path / "custom_env"
    custom_venv.mkdir()
    (custom_venv / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    (custom_venv / "leaked.py").write_text("redhat\n", encoding="utf-8")
    (tmp_path / "clean.py").write_text("x = 1\n", encoding="utf-8")

    walked = dict(iter_standup_text_files(tmp_path))
    assert list(walked.keys()) == [Path("clean.py")]


def test_iter_standup_text_files_skips_binary_and_skip_dirs(tmp_path: Path) -> None:
    """Non-UTF-8 binary files, SKIP_DIR_NAMES, and SKIP_RELATIVE_PATHS are skipped."""
    (tmp_path / "binary.dat").write_bytes(b"\xff\xfe\x00\x80invalid-utf8")
    cache_dir = tmp_path / ".venv"
    cache_dir.mkdir()
    (cache_dir / "ignored.py").write_text("redhat\n", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("redhat\n", encoding="utf-8")
    (tmp_path / "valid.txt").write_text("hello world\n", encoding="utf-8")

    walked = dict(iter_standup_text_files(tmp_path))
    assert walked == {Path("valid.txt"): "hello world\n"}


def test_iter_standup_text_files_skips_root_with_pyvenv_cfg(tmp_path: Path) -> None:
    """If the root directory itself contains pyvenv.cfg, traversal stops immediately."""
    (tmp_path / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    (tmp_path / "some.py").write_text("x = 1\n", encoding="utf-8")

    assert list(iter_standup_text_files(tmp_path)) == []


def test_all_12_denylist_fragments_covered_by_parametrized_tests() -> None:
    """All 12 DENYLIST_FRAGMENTS alternatives are present and compiled."""
    assert len(DENYLIST_FRAGMENTS) == 12
    assert len(set(DENYLIST_FRAGMENTS)) == 12


def test_extract_real_team_values_supports_list_and_dict_schemas() -> None:
    """_extract_real_team_values handles both top-level list and {'members': [...]} schemas."""
    list_schema = [
        {
            "name": "Ada Example",
            "email": "ada@example.com",
            "github": "ada-ex",
            "slack_id": "UEXAMPLE01",
            "ignored_key": "should-not-extract",
        },
        "non-dict-entry",
    ]
    dict_schema = {
        "members": [
            {
                "jira_email": "grace@example.com",
                "kerberos_uid": "gexample",
                "directory_uid": "dir-grace",
            }
        ]
    }
    assert _extract_real_team_values(list_schema) == {
        "Ada Example",
        "ada@example.com",
        "ada-ex",
        "UEXAMPLE01",
    }
    assert _extract_real_team_values(dict_schema) == {
        "grace@example.com",
        "gexample",
        "dir-grace",
    }
    assert _extract_real_team_values("invalid") == set()


def test_no_real_team_members_leaked_with_synthetic_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """test_no_real_team_members_leaked passes when synthetic team file values are absent."""
    team_file = tmp_path / "team-members.yaml"
    team_file.write_text(
        yaml.safe_dump(
            {
                "members": [
                    {
                        "name": "Zaphod SyntheticUniq99",
                        "github": "zaphod-uniq-99",
                        "slack_id": "USYNTH99999",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("STANDUP_REAL_TEAM_FILE", str(team_file))
    test_no_real_team_members_leaked()


def test_cli_no_subcommand_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    """standup-tool CLI with no subcommand prints help to stderr and exits 2."""
    from standup_tool import __version__
    from standup_tool.cli import main

    assert __version__ == "0.1.0"
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "standup-tool" in captured.err


def _extract_real_team_values(raw_data: object) -> set[str]:
    entries: list[object] = []
    if isinstance(raw_data, list):
        entries = raw_data
    elif isinstance(raw_data, dict) and isinstance(raw_data.get("members"), list):
        entries = raw_data["members"]

    values: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for key in REAL_TEAM_KEYS:
            val = entry.get(key)
            if isinstance(val, str) and val.strip():
                values.add(val.strip())
    return values


def test_no_real_team_members_leaked() -> None:
    """When a real team-members.yaml is present, none of its identifiers appear in standup/."""
    env_override = os.environ.get("STANDUP_REAL_TEAM_FILE")
    team_file = (
        Path(env_override).expanduser()
        if env_override
        else Path.home() / ".config" / "standup" / "team-members.yaml"
    )
    if not team_file.is_file():
        pytest.skip(f"No real team file at {team_file}")

    raw_data = yaml.safe_load(team_file.read_text(encoding="utf-8"))
    real_values = _extract_real_team_values(raw_data)
    assert real_values, f"Expected non-empty team member identifiers in {team_file}"

    lowered_lookup = {val.lower(): val for val in real_values}
    hits: list[str] = []
    for rel_path, text in iter_standup_text_files():
        for lineno, line in enumerate(text.splitlines(), start=1):
            line_lower = line.lower()
            for needle_lower, original in lowered_lookup.items():
                if needle_lower in line_lower:
                    hits.append(f"{rel_path.as_posix()}:{lineno}: leaked {original!r}")

    assert not hits, "Real team member values leaked in standup/:\n" + "\n".join(hits)
