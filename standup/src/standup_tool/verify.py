"""Collector, credential, LDAP, and settings verification for standup-tool."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal

from standup_tool.collectors import conversation_activity
from standup_tool.collectors.google_auth import GoogleAuthError, get_credentials
from standup_tool.collectors.registry import REGISTRY
from standup_tool.config import Config, redact_secrets, secret_path
from standup_tool.memory import detect_memory_dir
from standup_tool.slack_auth import (
    SlackAuthError,
    get_slack_credentials,
    make_session,
    safe_get,
)
from standup_tool.workspace import RepoInfo, list_repos

VerifyStatus = Literal["ok", "missing", "failed", "disabled"]

VERIFY_NAMES: tuple[str, ...] = tuple([c.name for c in REGISTRY] + ["ldap", "permissions"])
_GOOGLE_NAMES = frozenset({"calendar", "gmail", "drive"})
_SECRET_FILENAMES: tuple[str, ...] = ("slack-token", "credentials.json", "token.json")


def _first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _finish(status: VerifyStatus, hint: str) -> tuple[VerifyStatus, str]:
    return status, redact_secrets(hint)


def _check_git(config: Config) -> tuple[VerifyStatus, str]:
    if not config.collectors.git.enabled:
        return _finish("disabled", "disabled in config")

    for entry in config.repos:
        if not entry.path.exists() or not entry.path.is_dir():
            return _finish("failed", f"repo path does not exist: {entry.path}")
        try:
            proc = subprocess.run(
                ["git", "-C", str(entry.path), "rev-parse"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        # tolerant: report missing git binary as missing verification status
        except FileNotFoundError:
            return _finish("missing", "git binary not found on PATH; install git")
        # tolerant: report subprocess failure as failed verification status
        except Exception as exc:
            return _finish("failed", _first_line(str(exc)) or "git rev-parse failed")

        if proc.returncode != 0:
            err = _first_line(proc.stderr or proc.stdout) or f"git rev-parse failed in {entry.path}"
            return _finish("failed", err)

    return _finish("ok", f"{len(config.repos)} repo(s) verified")


def _check_pr(config: Config) -> tuple[VerifyStatus, str]:
    if not config.collectors.pr.enabled:
        return _finish("disabled", "disabled in config")

    try:
        proc = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    # tolerant: report missing gh binary as missing verification status
    except FileNotFoundError:
        return _finish("missing", "gh CLI not found on PATH; install gh and run gh auth login")
    # tolerant: report unexpected subprocess error as failed verification status
    except Exception as exc:
        return _finish("failed", _first_line(str(exc)) or "gh auth status failed")

    if proc.returncode != 0:
        err = _first_line(proc.stderr or proc.stdout) or "gh auth status failed; run gh auth login"
        return _finish("failed", err)
    return _finish("ok", "gh auth status ok")


def _check_jira(config: Config) -> tuple[VerifyStatus, str]:
    if not config.collectors.jira.enabled:
        return _finish("disabled", "disabled in config")

    try:
        me_proc = subprocess.run(
            ["jira", "me"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    # tolerant: report missing jira binary as missing verification status
    except FileNotFoundError:
        return _finish("missing", "jira CLI not found on PATH; install jira-cli and run jira init")
    # tolerant: report unexpected subprocess error as failed verification status
    except Exception as exc:
        return _finish("failed", _first_line(str(exc)) or "jira me failed")

    if me_proc.returncode != 0:
        err = (
            _first_line(me_proc.stderr or me_proc.stdout)
            or "jira me failed; check JIRA_API_TOKEN or run jira init"
        )
        return _finish("failed", err)

    project = config.collectors.jira.project or ""
    try:
        probe = subprocess.run(
            [
                "jira",
                "issue",
                "list",
                "-p",
                project,
                "--paginate",
                "0:1",
                "--plain",
                "--no-truncate",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    # tolerant: report missing jira binary as missing verification status
    except FileNotFoundError:
        return _finish("missing", "jira CLI not found on PATH; install jira-cli and run jira init")
    # tolerant: report unexpected subprocess error as failed verification status
    except Exception as exc:
        return _finish("failed", _first_line(str(exc)) or "jira issue list failed")

    combined = f"{probe.stdout}\n{probe.stderr}".lower()
    if probe.returncode != 0 or not probe.stdout.strip() or "no result found" in combined:
        return _finish(
            "failed",
            f"jira issue list returned no results for project {project}; "
            "check JIRA_API_TOKEN scope for /search/jql or run jira init",
        )

    return _finish("ok", f"jira me and issue search ok for {project}")


def _check_google_shared(cfg_dir: Path) -> tuple[VerifyStatus, str]:
    try:
        get_credentials(cfg_dir, interactive=False)
    # tolerant: missing credentials.json maps to missing verification status
    except FileNotFoundError as exc:
        default_msg = f"credentials.json not found at {cfg_dir / 'credentials.json'}"
        msg = _first_line(str(exc)) or default_msg
        return _finish("missing", msg)
    # tolerant: missing or expired token maps to failed with google-auth hint
    except GoogleAuthError as exc:
        first = _first_line(str(exc))
        hint = first if "run standup-tool google-auth" in first else "run standup-tool google-auth"
        return _finish("failed", hint)
    # tolerant: any other Google auth exception maps to failed
    except Exception as exc:
        return _finish("failed", _first_line(str(exc)) or "Google authentication failed")

    return _finish("ok", "Google OAuth token valid")


def _check_google(
    name: str,
    config: Config,
    cfg_dir: Path,
    google_cache: dict[str, tuple[VerifyStatus, str]] | None = None,
) -> tuple[VerifyStatus, str]:
    col_cfg = getattr(config.collectors, name)
    if not col_cfg.enabled:
        return _finish("disabled", "disabled in config")

    if google_cache is not None and "result" in google_cache:
        return google_cache["result"]

    res = _check_google_shared(cfg_dir)
    if google_cache is not None:
        google_cache["result"] = res
    return res


def _check_slack(config: Config, cfg_dir: Path) -> tuple[VerifyStatus, str]:
    if not config.collectors.slack.enabled:
        return _finish("disabled", "disabled in config")

    try:
        token_file = secret_path("slack-token")
        creds = get_slack_credentials(config.collectors.slack, token_file)
        session = make_session(creds)
        resp = safe_get(session, "https://slack.com/api/auth.test")
        resp.raise_for_status()
        data = resp.json()
    # tolerant: SlackAuthError or missing file maps to missing status before ConfigError/Exception
    except (SlackAuthError, FileNotFoundError) as exc:
        msg = _first_line(str(exc)) or "no Slack credential; run /standup-onboard"
        return _finish("missing", msg)
    # tolerant: network or HTTP error maps to failed verification status
    except Exception as exc:
        return _finish("failed", _first_line(str(exc)) or "Slack auth.test request failed")

    if not isinstance(data, dict) or not data.get("ok"):
        err = data.get("error", "unknown") if isinstance(data, dict) else "invalid response"
        return _finish("failed", f"Slack auth.test failed: {err}")

    user = str(data.get("user") or "ok")
    return _finish("ok", user)


def _check_memory(config: Config) -> tuple[VerifyStatus, str]:
    if not config.collectors.memory.enabled:
        return _finish("disabled", "disabled in config")

    try:
        repos = list_repos(config)
    # tolerant: fall back to configured repo entries when git discovery fails
    except Exception:
        repos = []

    if not repos:
        repos = [
            RepoInfo(name=entry.path.name, path=entry.path, upstream_slug="")
            for entry in config.repos
        ]

    parts: list[str] = []
    for repo in repos:
        mem_dir = detect_memory_dir(repo.path) if repo.path.exists() else None
        parts.append(f"{repo.name}: {mem_dir if mem_dir is not None else 'none'}")

    return _finish("ok", ", ".join(parts) if parts else "none")


def _check_conversations(config: Config) -> tuple[VerifyStatus, str]:
    if not config.collectors.conversations.enabled:
        return _finish("disabled", "disabled in config")

    project_id = (
        config.collectors.conversations.vertex_project
        or os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
        or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    )
    if not project_id:
        return _finish(
            "failed",
            "Vertex project not set; configure collectors.conversations.vertex_project "
            "or set ANTHROPIC_VERTEX_PROJECT_ID / GOOGLE_CLOUD_PROJECT",
        )

    projects_dir = conversation_activity._CLAUDE_PROJECTS_DIR
    if not projects_dir.is_dir():
        return _finish("failed", f"Claude projects directory not found: {projects_dir}")

    return _finish("ok", f"vertex_project={project_id}, projects_dir={projects_dir}")


def _check_ldap(config: Config) -> tuple[VerifyStatus, str]:
    if not config.ldap.enabled:
        return _finish("disabled", "disabled in config")

    try:
        if shutil.which("ldapsearch") is None:
            raise FileNotFoundError("ldapsearch not found on PATH")
        subprocess.run(
            ["ldapsearch", "-VV"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    # tolerant: missing ldapsearch binary maps to missing verification status
    except FileNotFoundError:
        return _finish("missing", "ldapsearch not found on PATH; install OpenLDAP client utilities")
    # tolerant: unexpected subprocess error maps to failed verification status
    except Exception as exc:
        return _finish("failed", _first_line(str(exc)) or "ldapsearch check failed")

    return _finish("ok", "ldapsearch available on PATH")


def _normalize_read_rule_target(rule: str) -> Path | None:
    stripped = rule.strip()
    if not (stripped.startswith("Read(") and stripped.endswith(")")):
        return None
    inner = stripped[len("Read(") : -1].strip()
    if not inner:
        return None
    try:
        return Path(inner).expanduser().resolve()
    # tolerant: ignore malformed path in unrelated permissions.deny entry
    except OSError:
        return Path(inner).expanduser()


def _check_permissions(cfg_dir: Path) -> tuple[VerifyStatus, str]:
    resolved_cfg_dir = cfg_dir.expanduser().resolve()
    settings_files = (
        Path("~/.claude/settings.json").expanduser(),
        Path(".claude/settings.local.json"),
    )

    deny_targets: set[Path] = set()
    deny_raw: set[str] = set()
    add_dirs_resolved: set[Path] = set()
    add_dirs_raw: set[str] = set()

    for sf in settings_files:
        if not sf.is_file():
            continue
        try:
            doc = json.loads(sf.read_text(encoding="utf-8"))
        # tolerant: ignore unreadable or malformed settings file
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(doc, dict):
            continue
        perms = doc.get("permissions")
        if not isinstance(perms, dict):
            continue
        raw_deny = perms.get("deny")
        if isinstance(raw_deny, list):
            for entry in raw_deny:
                if isinstance(entry, str):
                    deny_raw.add(entry.strip())
                    target = _normalize_read_rule_target(entry)
                    if target is not None:
                        deny_targets.add(target)
        raw_add = perms.get("additionalDirectories")
        if isinstance(raw_add, list):
            for entry in raw_add:
                if isinstance(entry, str) and entry.strip():
                    add_dirs_raw.add(entry.strip())
                    with contextlib.suppress(OSError):
                        add_dirs_resolved.add(Path(entry.strip()).expanduser().resolve())

    required_rules = [f"Read({cfg_dir / name})" for name in _SECRET_FILENAMES]
    all_deny_present = all(
        (f"Read({cfg_dir / name})" in deny_raw) or ((resolved_cfg_dir / name) in deny_targets)
        for name in _SECRET_FILENAMES
    )
    dir_present = (str(cfg_dir) in add_dirs_raw) or (resolved_cfg_dir in add_dirs_resolved)

    if all_deny_present and dir_present:
        return _finish("ok", "permissions.deny and additionalDirectories configured")

    snippet = json.dumps(
        {
            "permissions": {
                "deny": required_rules,
                "additionalDirectories": [str(cfg_dir)],
            }
        }
    )
    return _finish("missing", f"add to ~/.claude/settings.json: {snippet}")


def check_one(
    name: str,
    config: Config,
    cfg_dir: Path,
    *,
    google_cache: dict[str, tuple[VerifyStatus, str]] | None = None,
) -> tuple[VerifyStatus, str]:
    """Run a single verification check by name and return `(status, redacted_hint)`."""
    if name not in VERIFY_NAMES:
        raise ValueError(f"Unknown verify check: {name!r}")

    if name == "git":
        return _check_git(config)
    if name == "pr":
        return _check_pr(config)
    if name == "jira":
        return _check_jira(config)
    if name in _GOOGLE_NAMES:
        return _check_google(name, config, cfg_dir, google_cache=google_cache)
    if name == "slack":
        return _check_slack(config, cfg_dir)
    if name == "memory":
        return _check_memory(config)
    if name == "conversations":
        return _check_conversations(config)
    if name == "ldap":
        return _check_ldap(config)
    return _check_permissions(cfg_dir)


def run_verify(config: Config, cfg_dir: Path, *, only: str | None = None) -> int:
    """Run all (or `--only`) verification checks, print the table, and return exit code."""
    names = (only,) if only is not None else VERIFY_NAMES
    google_cache: dict[str, tuple[VerifyStatus, str]] = {}
    rows: list[tuple[str, VerifyStatus, str]] = []

    for name in names:
        status, hint = check_one(name, config, cfg_dir, google_cache=google_cache)
        rows.append((name, status, redact_secrets(hint)))

    sys.stdout.write("check | status | hint\n")
    sys.stdout.write("--- | --- | ---\n")
    for name, status, hint in rows:
        sys.stdout.write(f"{name} | {status} | {hint}\n")

    if only is not None:
        _, st, _ = rows[0]
        return 1 if st in ("missing", "failed") else 0

    for name, st, _ in rows:
        if name == "permissions":
            continue
        if st in ("missing", "failed"):
            return 1
    return 0
