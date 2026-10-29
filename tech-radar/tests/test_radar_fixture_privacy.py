"""Opt-in live privacy leak test for committed fixtures (Spec 12 A29)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures"

# Generic English/schema terms or synthetic fixture tokens (`fixture_home` specifies
# `<home>/.config/llm-gateway`, digest Coverage uses `renovate`, GitHub API uses
# `assets_url`, and feeds use `release notes`) that can coincide with a bare repo shortname;
# their `<owner>/<name>` slugs are still checked unconditionally.
GENERIC_OR_PLAN_MANDATED_SHORTNAMES = frozenset({"notes", "assets", "renovate", "llm-gateway"})


def _run_cmd(argv: list[str]) -> str:
    res = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return res.stdout.strip()


def _build_forbidden_needles(
    email: str,
    gh_login: str,
    priv_repos: list[Any],
) -> dict[str, bytes]:
    forbidden_needles: dict[str, bytes] = {}
    if email:
        forbidden_needles[f"git user.email ({email})"] = email.encode("utf-8")
    if gh_login:
        forbidden_needles[f"gh login ({gh_login})"] = gh_login.encode("utf-8")

    for entry in priv_repos:
        if not isinstance(entry, dict):
            continue
        full_name = str(entry.get("nameWithOwner") or "").strip()
        repo_name = str(entry.get("name") or "").strip()
        if full_name:
            forbidden_needles[f"private repo slug ({full_name})"] = full_name.encode("utf-8")
        if repo_name and repo_name.lower() not in GENERIC_OR_PLAN_MANDATED_SHORTNAMES:
            forbidden_needles[f"private repo name ({repo_name})"] = repo_name.encode("utf-8")
    return forbidden_needles


def _scan_fixture_files(
    fixture_root: Path,
    forbidden_needles: dict[str, bytes],
) -> list[str]:
    fixture_files = sorted(p for p in fixture_root.rglob("*") if p.is_file())
    assert fixture_files, f"Expected fixture files under {fixture_root}"
    leaks: list[str] = []
    for fpath in fixture_files:
        raw_lower = fpath.read_bytes().lower()
        for label, needle in forbidden_needles.items():
            if needle.lower() in raw_lower:
                rel = fpath.relative_to(fixture_root)
                leaks.append(f"{rel}: matched {label}")
    return leaks


def test_build_forbidden_needles_and_scanner_edge_cases(tmp_path: Path) -> None:
    """Verify needle builder and case-insensitive scanner detect leaks and honour exemptions."""
    needles = _build_forbidden_needles(
        email="DevUser@Example.Invalid",
        gh_login="SecretUserLogin",
        priv_repos=[
            {"name": "super-secret-repo", "nameWithOwner": "SecretUserLogin/super-secret-repo"},
            {"name": "notes", "nameWithOwner": "SecretUserLogin/notes"},
            {"name": "renovate", "nameWithOwner": "SecretUserLogin/renovate"},
            "non-dict-ignored",
        ],
    )
    assert "git user.email (DevUser@Example.Invalid)" in needles
    assert "gh login (SecretUserLogin)" in needles
    assert "private repo name (super-secret-repo)" in needles
    assert "private repo slug (SecretUserLogin/notes)" in needles
    assert "private repo name (notes)" not in needles
    assert "private repo name (renovate)" not in needles

    clean_file = tmp_path / "clean.json"
    clean_file.write_text('{"note": "release notes and renovate config"}\n', encoding="utf-8")
    assert _scan_fixture_files(tmp_path, needles) == []

    leaked_file = tmp_path / "sub" / "leaked.json"
    leaked_file.parent.mkdir(parents=True, exist_ok=True)
    leaked_file.write_text(
        '{"author": "devuser@example.invalid", "repo": "secretuserlogin/notes"}\n',
        encoding="utf-8",
    )
    hits = _scan_fixture_files(tmp_path, needles)
    assert len(hits) == 3
    assert any("git user.email" in h for h in hits)
    assert any("gh login" in h for h in hits)
    assert any("private repo slug (SecretUserLogin/notes)" in h for h in hits)


@pytest.mark.slow
@pytest.mark.skipif(
    not os.environ.get("RUN_LIVE_TESTS"),
    reason="Set RUN_LIVE_TESTS=1 to run live fixture privacy checks",
)
def test_committed_fixtures_contain_no_user_email_login_or_private_repos() -> None:
    """Assert no committed fixture under tests/fixtures/ contains:
    - `git config user.email`
    - `gh api user -q .login`
    - any `gh repo list --visibility private` repository name or slug.
    """
    assert FIXTURES_ROOT.is_dir(), f"Fixtures directory missing: {FIXTURES_ROOT}"

    email = _run_cmd(["git", "config", "user.email"])
    gh_login = _run_cmd(["gh", "api", "user", "-q", ".login"])
    priv_raw = _run_cmd(
        [
            "gh",
            "repo",
            "list",
            "--visibility",
            "private",
            "--limit",
            "1000",
            "--json",
            "name,nameWithOwner",
        ]
    )
    priv_repos = json.loads(priv_raw) if priv_raw else []
    assert isinstance(priv_repos, list)

    forbidden_needles = _build_forbidden_needles(email, gh_login, priv_repos)
    assert forbidden_needles, "Expected at least git user.email or gh login to be resolved"

    leaks = _scan_fixture_files(FIXTURES_ROOT, forbidden_needles)
    assert not leaks, "Privacy leak(s) detected in committed fixtures:\n" + "\n".join(leaks)
