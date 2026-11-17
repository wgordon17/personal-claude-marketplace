"""Shared pytest fixtures for standup-tool tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from standup_tool.config import Config, load_config

ACME_TEMPLATE = """\
:dart: Key Effort
<!-- criteria: Primary active engineering effort -->
• ACME-101: Shipped token validation (`:merged2:` widgets#10)

:rotating_light: Blockers & Timelines
<!-- criteria: Open blockers or review requests -->
• None

:rocket: Progress & Wins
<!-- criteria: Completed work and reviews -->
• ACME-102: Reviewed retry handler (`:approved2:` gadgets#12)
"""


@pytest.fixture(autouse=True)
def cfg_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an isolated 0700 config directory and point STANDUP_CONFIG_DIR at it."""
    d = tmp_path / "cfg"
    d.mkdir(mode=0o700, exist_ok=True)
    d.chmod(0o700)
    monkeypatch.setenv("STANDUP_CONFIG_DIR", str(d))
    return d


@pytest.fixture
def acme_config(tmp_path: Path, cfg_dir: Path) -> Config:
    """Write a valid template.md and config.yaml for org 'acme' and return load_config()."""
    for repo_name in ("widgets", "gadgets"):
        repo_dir = tmp_path / repo_name
        repo_dir.mkdir(exist_ok=True)
        subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "remote", "add", "upstream", f"https://github.com/acme/{repo_name}.git"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )

    (cfg_dir / "template.md").write_text(ACME_TEMPLATE, encoding="utf-8")
    (cfg_dir / "config.yaml").write_text(
        f"""\
version: 1
identity:
  email: ada@example.com
github:
  org: acme
repos:
  - path: {tmp_path / "widgets"}
  - path: {tmp_path / "gadgets"}
collectors:
  git: {{enabled: true}}
  pr: {{enabled: true}}
  jira:
    enabled: true
    project: ACME
    server: https://acme.atlassian.net
  calendar: {{enabled: true}}
  gmail: {{enabled: true}}
  drive: {{enabled: true}}
  slack:
    enabled: true
    workspace_url: https://acme.slack.com
    channels:
      - eng
      - ops
    standup_channels:
      - eng
      - ops
    self_dm_name: null
  memory: {{enabled: true}}
  conversations:
    enabled: false
    project_match: []
    vertex_project: null
    region: us-east5
standup:
  audience: team
  template_file: template.md
ldap:
  enabled: false
""",
        encoding="utf-8",
    )
    return load_config()
