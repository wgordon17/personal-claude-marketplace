"""Tests for standup_tool.config."""

from __future__ import annotations

import dataclasses
import os
import stat
from pathlib import Path
from typing import Any

import pytest
import yaml

from standup_tool.config import (
    CollectorsConfig,
    Config,
    ConfigError,
    ConversationsConfig,
    GitConfig,
    GitHubConfig,
    GoogleConfig,
    IdentityConfig,
    JiraConfig,
    LdapConfig,
    MemoryConfig,
    PrConfig,
    RepoEntry,
    SlackConfig,
    StandupConfig,
    config_dir,
    config_path,
    ensure_dir_mode,
    is_bare_filename,
    load_config,
    redact_secrets,
    secret_path,
    state_path,
    tighten_mode,
    validate_https_origin_url,
    validate_workspace_url,
)

STANDUP_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = STANDUP_ROOT / "src" / "standup_tool" / "examples"


def _make_git_repo(base: Path, name: str = "repo", *, git_file: bool = False) -> Path:
    repo = base / name
    repo.mkdir(parents=True, exist_ok=True)
    dot_git = repo / ".git"
    if git_file:
        dot_git.write_text("gitdir: ../.git/worktrees/linked\n", encoding="utf-8")
    else:
        dot_git.mkdir(exist_ok=True)
    return repo


def _minimal_raw_config(repo_path: Path | str) -> dict[str, Any]:
    return {
        "version": 1,
        "github": {"org": "acme-org"},
        "repos": [{"path": str(repo_path)}],
        "collectors": {
            "jira": {"project": "ACME"},
            "slack": {"workspace_url": "https://acme.slack.com"},
        },
    }


def _write_config(cfg_dir: Path, data: Any) -> Path:
    p = cfg_dir / "config.yaml"
    if isinstance(data, str):
        p.write_text(data, encoding="utf-8")
    else:
        p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


@pytest.mark.parametrize(
    "filename",
    [
        "config.example.yaml",
        "template.example.md",
        "team-members.example.yaml",
    ],
)
def test_example_files_byte_identical(filename: str) -> None:
    """Top-level example files match src/standup_tool/examples/* byte-for-byte."""
    top_bytes = (STANDUP_ROOT / filename).read_bytes()
    pkg_bytes = (EXAMPLES_DIR / filename).read_bytes()
    assert top_bytes == pkg_bytes


def test_config_example_loads_with_require_repos_false() -> None:
    """config.example.yaml loads cleanly when require_repos=False."""
    cfg = load_config(STANDUP_ROOT / "config.example.yaml", require_repos=False)
    assert isinstance(cfg, Config)
    assert cfg.version == 1
    assert cfg.identity == IdentityConfig(email=None)
    assert cfg.github == GitHubConfig(org="example-org")
    assert len(cfg.repos) == 1
    assert cfg.repos[0] == RepoEntry(path=Path("~/src/example-repo").expanduser())
    assert cfg.collectors.git == GitConfig(enabled=True)
    assert cfg.collectors.pr == PrConfig(enabled=True)
    assert cfg.collectors.jira == JiraConfig(enabled=True, project="PROJ", server=None)
    assert cfg.collectors.calendar == GoogleConfig(enabled=True)
    assert cfg.collectors.gmail == GoogleConfig(enabled=True)
    assert cfg.collectors.drive == GoogleConfig(enabled=True)
    assert cfg.collectors.slack == SlackConfig(
        enabled=True,
        workspace_url="https://example-workspace.slack.com",
        channels=("eng", "ops"),
        standup_channels=("eng",),
        self_dm_name=None,
    )
    assert cfg.collectors.memory == MemoryConfig(enabled=True)
    assert cfg.collectors.conversations == ConversationsConfig(
        enabled=False,
        project_match=("example-repo",),
        vertex_project=None,
        region=None,
    )
    assert cfg.standup == StandupConfig(audience="team", template_file="template.md")
    assert cfg.ldap == LdapConfig(
        enabled=False,
        host=None,
        base_dn=None,
        email_domain=None,
        auth_mech="GSSAPI",
        uid_attr="uid",
        worker_id_attr=None,
        manager_id_attr=None,
        social_url_attr=None,
    )


def _dataclass_leaf_paths(dc_type: type[Any], prefix: str = "") -> set[str]:
    paths: set[str] = set()
    for field in dataclasses.fields(dc_type):
        key = f"{prefix}.{field.name}" if prefix else field.name
        if field.name == "repos" and prefix == "":
            paths.update(_dataclass_leaf_paths(RepoEntry, "repos[]"))
        elif isinstance(field.type, type) and dataclasses.is_dataclass(field.type):
            paths.update(_dataclass_leaf_paths(field.type, key))
        else:
            # Resolve string annotations if present
            nested_map: dict[str, type[Any]] = {
                "IdentityConfig": IdentityConfig,
                "GitHubConfig": GitHubConfig,
                "CollectorsConfig": CollectorsConfig,
                "GitConfig": GitConfig,
                "PrConfig": PrConfig,
                "JiraConfig": JiraConfig,
                "GoogleConfig": GoogleConfig,
                "SlackConfig": SlackConfig,
                "MemoryConfig": MemoryConfig,
                "ConversationsConfig": ConversationsConfig,
                "StandupConfig": StandupConfig,
                "LdapConfig": LdapConfig,
            }
            type_name = (
                field.type if isinstance(field.type, str) else getattr(field.type, "__name__", "")
            )
            if type_name in nested_map:
                paths.update(_dataclass_leaf_paths(nested_map[type_name], key))
            else:
                paths.add(key)
    return paths


def _yaml_leaf_paths(obj: Any, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            paths.update(_yaml_leaf_paths(v, key))
    elif isinstance(obj, list) and obj and all(isinstance(el, dict) for el in obj):
        for el in obj:
            paths.update(_yaml_leaf_paths(el, f"{prefix}[]"))
    else:
        paths.add(prefix)
    return paths


def test_config_example_reverse_schema_matches_dataclass_fields() -> None:
    """Every leaf key in config.example.yaml maps 1-to-1 to a Config dataclass field."""
    raw = yaml.safe_load((STANDUP_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    yaml_leaves = _yaml_leaf_paths(raw)
    dc_leaves = _dataclass_leaf_paths(Config)
    assert yaml_leaves == dc_leaves


def test_defaults_when_optional_keys_omitted(tmp_path: Path, cfg_dir: Path) -> None:
    """Omitting optional sections and keys populates all documented defaults."""
    repo = _make_git_repo(tmp_path, "widgets")
    _write_config(cfg_dir, _minimal_raw_config(repo))

    cfg = load_config()
    assert cfg.version == 1
    assert cfg.identity.email is None
    assert cfg.github.org == "acme-org"
    assert cfg.repos == (RepoEntry(path=repo.resolve()),)
    assert cfg.collectors.git.enabled is True
    assert cfg.collectors.pr.enabled is True
    assert cfg.collectors.jira.enabled is True
    assert cfg.collectors.jira.project == "ACME"
    assert cfg.collectors.jira.server is None
    assert cfg.collectors.calendar.enabled is True
    assert cfg.collectors.gmail.enabled is True
    assert cfg.collectors.drive.enabled is True
    assert cfg.collectors.slack.enabled is True
    assert cfg.collectors.slack.workspace_url == "https://acme.slack.com"
    assert cfg.collectors.slack.channels == ()
    assert cfg.collectors.slack.standup_channels == ()
    assert cfg.collectors.slack.self_dm_name is None
    assert cfg.collectors.memory.enabled is True
    assert cfg.collectors.conversations.enabled is False
    assert cfg.collectors.conversations.project_match == ()
    assert cfg.collectors.conversations.vertex_project is None
    assert cfg.collectors.conversations.region is None
    assert cfg.standup.audience == "team"
    assert cfg.standup.template_file == "template.md"
    assert cfg.ldap.enabled is False
    assert cfg.ldap.host is None
    assert cfg.ldap.base_dn is None
    assert cfg.ldap.email_domain is None
    assert cfg.ldap.auth_mech == "GSSAPI"
    assert cfg.ldap.uid_attr == "uid"
    assert cfg.ldap.worker_id_attr is None
    assert cfg.ldap.manager_id_attr is None
    assert cfg.ldap.social_url_attr is None


def test_missing_config_raises_exact_onboarding_message(cfg_dir: Path) -> None:
    """Missing config.yaml raises ConfigError('No config at <path>; run /standup-onboard')."""
    expected_path = cfg_dir / "config.yaml"
    with pytest.raises(ConfigError) as exc_info:
        load_config()
    assert str(exc_info.value) == f"No config at {expected_path}; run /standup-onboard"


@pytest.mark.parametrize(
    "raw_content",
    [
        "",
        "null\n",
        "- just\n- a\n- list\n",
        "version: [unterminated\n",
    ],
)
def test_empty_none_non_mapping_and_invalid_yaml_raise(
    cfg_dir: Path,
    raw_content: str,
) -> None:
    """Empty file, null document, non-mapping document, and YAML syntax errors raise ConfigError."""
    _write_config(cfg_dir, raw_content)
    with pytest.raises(ConfigError):
        load_config(require_repos=False)


@pytest.mark.parametrize(
    ("version_val", "expected_prefix"),
    [
        (None, "version:"),
        (2, "version:"),
        ("1", "version:"),
        (True, "version:"),
        (False, "version:"),
    ],
)
def test_version_validation(
    tmp_path: Path,
    cfg_dir: Path,
    version_val: Any,
    expected_prefix: str,
) -> None:
    """version must be integer 1 (booleans rejected)."""
    data = _minimal_raw_config(tmp_path / "r")
    if version_val is None:
        del data["version"]
    else:
        data["version"] = version_val
    _write_config(cfg_dir, data)
    with pytest.raises(ConfigError) as exc_info:
        load_config(require_repos=False)
    assert str(exc_info.value).startswith(expected_prefix)


def test_unknown_top_level_and_nested_keys_rejected(tmp_path: Path, cfg_dir: Path) -> None:
    """Unknown keys at top level or nested sections raise ConfigError with dotted path."""
    data = _minimal_raw_config(tmp_path / "r")
    data["unexpected"] = 123
    _write_config(cfg_dir, data)
    with pytest.raises(ConfigError, match=r"^unexpected:"):
        load_config(require_repos=False)

    data2 = _minimal_raw_config(tmp_path / "r")
    data2["collectors"]["slack"]["bogus"] = True
    _write_config(cfg_dir, data2)
    with pytest.raises(ConfigError, match=r"^collectors\.slack\.bogus:"):
        load_config(require_repos=False)

    data3 = _minimal_raw_config(tmp_path / "r")
    data3["repos"][0]["extra"] = "no"
    _write_config(cfg_dir, data3)
    with pytest.raises(ConfigError, match=r"^repos\[0\]\.extra:"):
        load_config(require_repos=False)


@pytest.mark.parametrize(
    ("mutator", "expected_prefix"),
    [
        (
            lambda d: d["collectors"].__setitem__("git", {"enabled": "yes"}),
            "collectors.git.enabled:",
        ),
        (
            lambda d: d["collectors"]["slack"].__setitem__("channels", "eng"),
            "collectors.slack.channels:",
        ),
        (
            lambda d: d["collectors"]["slack"].__setitem__("channels", [123]),
            "collectors.slack.channels[0]:",
        ),
        (lambda d: d.__setitem__("repos", "~/x"), "repos:"),
        (lambda d: d.__setitem__("repos", []), "repos:"),
        (lambda d: d.__setitem__("repos", ["not-a-dict"]), "repos[0]:"),
        (lambda d: d.__setitem__("repos", [{}]), "repos[0].path:"),
    ],
)
def test_type_and_repos_errors(
    tmp_path: Path,
    cfg_dir: Path,
    mutator: Any,
    expected_prefix: str,
) -> None:
    """Wrong field types and malformed repos entries raise ConfigError with dotted path."""
    data = _minimal_raw_config(tmp_path / "r")
    mutator(data)
    _write_config(cfg_dir, data)
    with pytest.raises(ConfigError) as exc_info:
        load_config(require_repos=False)
    assert str(exc_info.value).startswith(expected_prefix)


def test_required_when_enabled_rules(tmp_path: Path, cfg_dir: Path) -> None:
    """Required-when-enabled rules enforced for jira, slack, and ldap."""
    # jira.project missing when jira.enabled=True
    data = _minimal_raw_config(tmp_path / "r")
    data["collectors"]["jira"] = {"enabled": True, "project": None}
    _write_config(cfg_dir, data)
    with pytest.raises(ConfigError, match=r"^collectors\.jira\.project:"):
        load_config(require_repos=False)

    # jira.project can be null when jira.enabled=False
    data["collectors"]["jira"] = {"enabled": False, "project": None}
    _write_config(cfg_dir, data)
    cfg = load_config(require_repos=False)
    assert cfg.collectors.jira.enabled is False

    # slack.workspace_url missing when slack.enabled=True
    data = _minimal_raw_config(tmp_path / "r")
    data["collectors"]["slack"] = {"enabled": True, "workspace_url": None}
    _write_config(cfg_dir, data)
    with pytest.raises(ConfigError, match=r"^collectors\.slack\.workspace_url:"):
        load_config(require_repos=False)

    # ldap required fields when ldap.enabled=True
    for missing_field in ("host", "base_dn", "email_domain"):
        data = _minimal_raw_config(tmp_path / "r")
        ldap_block: dict[str, Any] = {
            "enabled": True,
            "host": "ldaps://ldap.example.com",
            "base_dn": "dc=example,dc=com",
            "email_domain": "example.com",
        }
        ldap_block[missing_field] = None
        data["ldap"] = ldap_block
        _write_config(cfg_dir, data)
        with pytest.raises(ConfigError, match=rf"^ldap\.{missing_field}:"):
            load_config(require_repos=False)


@pytest.mark.parametrize(
    ("mutator", "expected_prefix"),
    [
        (lambda d: d["github"].__setitem__("org", "bad/org"), "github.org:"),
        (lambda d: d["github"].__setitem__("org", ""), "github.org:"),
        (
            lambda d: d["collectors"]["jira"].__setitem__("project", " lowercase"),
            "collectors.jira.project:",
        ),
        (
            lambda d: d["collectors"]["jira"].__setitem__("project", "123ABC"),
            "collectors.jira.project:",
        ),
        (
            lambda d: d["collectors"]["jira"].__setitem__("server", "http://jira.example.com"),
            "collectors.jira.server:",
        ),
        (
            lambda d: d["collectors"]["jira"].__setitem__(
                "server", "https://jira.example.com:8443"
            ),
            "collectors.jira.server:",
        ),
        (
            lambda d: d["collectors"]["jira"].__setitem__(
                "server", "https://user@jira.example.com"
            ),
            "collectors.jira.server:",
        ),
        (
            lambda d: d["collectors"]["jira"].__setitem__("server", "https://jira.example.com/sub"),
            "collectors.jira.server:",
        ),
        (
            lambda d: d["collectors"].__setitem__("conversations", {"region": "US_EAST_5"}),
            "collectors.conversations.region:",
        ),
        (
            lambda d: d["collectors"].__setitem__("conversations", {"vertex_project": "BadProj"}),
            "collectors.conversations.vertex_project:",
        ),
        (
            lambda d: d["collectors"].__setitem__("conversations", {"vertex_project": "ab"}),
            "collectors.conversations.vertex_project:",
        ),
        (
            lambda d: d.__setitem__("ldap", {"uid_attr": "bad_attr"}),
            "ldap.uid_attr:",
        ),
        (
            lambda d: d.__setitem__("ldap", {"worker_id_attr": "1bad"}),
            "ldap.worker_id_attr:",
        ),
        (
            lambda d: d.__setitem__("ldap", {"manager_id_attr": "bad*attr"}),
            "ldap.manager_id_attr:",
        ),
        (
            lambda d: d.__setitem__("ldap", {"social_url_attr": "bad attr"}),
            "ldap.social_url_attr:",
        ),
        (
            lambda d: d.__setitem__("ldap", {"auth_mech": "gssapi"}),
            "ldap.auth_mech:",
        ),
        (
            lambda d: d.__setitem__("ldap", {"host": "ldap://insecure.example.com"}),
            "ldap.host:",
        ),
        (
            lambda d: d.__setitem__("standup", {"template_file": "../escape.md"}),
            "standup.template_file:",
        ),
        (
            lambda d: d.__setitem__("standup", {"template_file": "sub/template.md"}),
            "standup.template_file:",
        ),
    ],
)
def test_field_regex_and_format_validations(
    tmp_path: Path,
    cfg_dir: Path,
    mutator: Any,
    expected_prefix: str,
) -> None:
    """Field regex and format checks fail with <dotted.path>: <reason>."""
    data = _minimal_raw_config(tmp_path / "r")
    mutator(data)
    _write_config(cfg_dir, data)
    with pytest.raises(ConfigError) as exc_info:
        load_config(require_repos=False)
    assert str(exc_info.value).startswith(expected_prefix)


@pytest.mark.parametrize(
    "bad_url",
    [
        r"https://evil.com\@x.slack.com",
        "https://x.slack.com@evil.com",
        "https://x.slack.com.evil.com",
        "https://x.slack.com.",
        "https://X.SLACK.COM",
        "https://x.slack.com%2f@evil.com",
        "https://x.slack.com/path",
        "http://x.slack.com",
        "https://x.slack.com:443",
        "https://x.slack.com?foo=bar",
        "https://x.slack.com#frag",
        "https://slack.com",
        "https://[invalid",
    ],
)
def test_validate_workspace_url_rejects_parser_differentials_and_bad_origins(
    bad_url: str,
) -> None:
    """validate_workspace_url rejects parser-differential and non-workspace URLs."""
    with pytest.raises(ConfigError):
        validate_workspace_url(bad_url)


@pytest.mark.parametrize(
    "good_url",
    [
        "https://acme.slack.com",
        "https://acme.slack.com/",
        "https://sub.team-1.slack.com",
    ],
)
def test_validate_workspace_url_accepts_valid_slack_origins(good_url: str) -> None:
    """validate_workspace_url accepts valid https://*.slack.com origins."""
    validate_workspace_url(good_url)


def test_validate_https_origin_url_accepts_and_rejects() -> None:
    """validate_https_origin_url validates https origin URLs without leaking raw exceptions."""
    validate_https_origin_url("https://jira.example.com", "collectors.jira.server")
    validate_https_origin_url("https://jira.example.com/", "collectors.jira.server")
    for bad in (
        "http://jira.example.com",
        "https://JIRA.EXAMPLE.COM",
        "https://jira.example.com.",
        r"https://evil.com\@jira.example.com",
        "https://jira.example.com?q=1",
        "https://jira.example.com#frag",
        "https://[bad",
    ):
        with pytest.raises(ConfigError, match=r"^collectors\.jira\.server:"):
            validate_https_origin_url(bad, "collectors.jira.server")


def test_repos_path_validation_require_repos_true_and_false(
    tmp_path: Path,
    cfg_dir: Path,
) -> None:
    """repos[].path must exist and contain .git (dir or file) when require_repos=True."""
    missing = tmp_path / "does-not-exist"
    _write_config(cfg_dir, _minimal_raw_config(missing))
    with pytest.raises(ConfigError, match=r"^repos\[0\]\.path:"):
        load_config(require_repos=True)

    # Exists without .git -> fails when require_repos=True
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    _write_config(cfg_dir, _minimal_raw_config(plain_dir))
    with pytest.raises(ConfigError, match=r"^repos\[0\]\.path:"):
        load_config(require_repos=True)

    # Linked worktree with .git as a regular file -> passes
    worktree_repo = _make_git_repo(tmp_path, "wt-repo", git_file=True)
    _write_config(cfg_dir, _minimal_raw_config(worktree_repo))
    cfg = load_config(require_repos=True)
    assert cfg.repos[0].path == worktree_repo.resolve()


def test_config_dir_rejects_git_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """config_dir() raises ConfigError when resolved path is inside a git worktree."""
    import subprocess

    repo = tmp_path / "git-repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    inside = repo / "nested" / "standup-cfg"
    monkeypatch.setenv("STANDUP_CONFIG_DIR", str(inside))

    with pytest.raises(ConfigError, match="git"):
        config_dir()


def test_config_dir_passes_when_git_binary_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the git binary is missing, config_dir() does not fail the worktree check."""
    import subprocess

    target = tmp_path / "no-git-cfg"
    monkeypatch.setenv("STANDUP_CONFIG_DIR", str(target))

    def _raise_fnf(*_args: Any, **_kwargs: Any) -> Any:
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(subprocess, "run", _raise_fnf)
    assert config_dir() == target.resolve()


def test_ensure_dir_mode_creates_and_tightens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ensure_dir_mode creates missing dir with 0700 and tightens 0755 to 0700."""
    target = tmp_path / "loose-cfg"
    target.mkdir(mode=0o755)
    target.chmod(0o755)
    monkeypatch.setenv("STANDUP_CONFIG_DIR", str(target))

    resolved = ensure_dir_mode()
    assert resolved == target.resolve()
    assert stat.S_IMODE(target.stat().st_mode) == 0o700

    # Failure to chmod raises ConfigError
    target.chmod(0o755)

    def _fail_chmod(_p: Any, _mode: int) -> None:
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(os, "chmod", _fail_chmod)
    with pytest.raises(ConfigError):
        ensure_dir_mode()


@pytest.mark.parametrize(
    "bad_name",
    [
        "",
        ".",
        "..",
        "../secret",
        "a/b",
        r"a\b",
        "foo..bar",
    ],
)
def test_path_helpers_reject_non_bare_filenames(bad_name: str) -> None:
    """is_bare_filename, secret_path, config_path, and state_path reject non-bare names."""
    assert is_bare_filename(bad_name) is False
    for helper in (secret_path, config_path, state_path):
        with pytest.raises(ConfigError):
            helper(bad_name)


def test_path_helpers_absent_files_and_symlink_rejection(
    tmp_path: Path,
    cfg_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path helpers and tighten_mode handle absent files, symlinks, and 0644 files."""
    # Absent files return path without creating it
    for helper, name in (
        (secret_path, "slack-token"),
        (config_path, "template.md"),
        (state_path, ".collect-output.json"),
    ):
        p = helper(name)
        assert p == cfg_dir / name
        assert not p.exists()

    # Symlinks are rejected by all helpers and tighten_mode
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    for helper, name in (
        (secret_path, "sym-secret"),
        (config_path, "sym-config"),
        (state_path, "sym-state"),
    ):
        link = cfg_dir / name
        link.symlink_to(outside)
        with pytest.raises(ConfigError, match="symlink"):
            helper(name)

    with pytest.raises(ConfigError, match="symlink"):
        tighten_mode(cfg_dir / "sym-secret")

    # 0644 regular file is tightened to 0600 by secret_path / tighten_mode
    tok = cfg_dir / "slack-token"
    tok.write_text("val\n", encoding="utf-8")
    tok.chmod(0o644)
    assert stat.S_IMODE(tok.stat().st_mode) == 0o644
    returned = secret_path("slack-token")
    assert returned == tok
    assert stat.S_IMODE(tok.stat().st_mode) == 0o600

    # chmod failure raises ConfigError
    tok.chmod(0o644)

    def _fail_chmod(_p: Any, _mode: int) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(os, "chmod", _fail_chmod)
    with pytest.raises(ConfigError):
        tighten_mode(tok)


def test_redact_secrets_masks_all_supported_token_patterns() -> None:
    """redact_secrets masks Slack xox*, Bearer, Basic, Atlassian ATATT, and Google ya29. tokens."""
    # Build synthetic secret strings dynamically so no literal triggers static scanners
    slack_cookie = "xox" + "d-1234567890abcdef"
    slack_token = "xox" + "c-9876543210fedcba"
    bearer = "Bearer eyJhbGciOiJIUzI1NiJ9.secret.payload"
    basic = "Basic dXNlcjpzdXBlcnNlY3JldA=="
    atatt = "ATATT3xFfGF0123456789abcdef_secret"
    ya29 = "ya29.a0AfH6SMBx1234567890_google_oauth"

    raw = (
        f"cookie={slack_cookie} token={slack_token} "
        f"auth1={bearer} auth2={basic} jira={atatt} goog={ya29}"
    )
    redacted = redact_secrets(raw)
    assert "1234567890abcdef" not in redacted
    assert "9876543210fedcba" not in redacted
    assert "eyJhbGciOiJIUzI1NiJ9" not in redacted
    assert "dXNlcjpzdXBlcnNlY3JldA==" not in redacted
    assert "3xFfGF0123456789abcdef_secret" not in redacted
    assert "a0AfH6SMBx1234567890_google_oauth" not in redacted


def test_cli_config_validate_fails_on_nonexistent_example_repo(
    cfg_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`config validate` on config.example.yaml exits 1 with repos[0].path on stderr."""
    from standup_tool.cli import main

    example_text = (STANDUP_ROOT / "config.example.yaml").read_text(encoding="utf-8")
    (cfg_dir / "config.yaml").write_text(example_text, encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        main(["config", "validate"])
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "repos[0].path" in captured.err


def test_cli_config_validate_succeeds_with_valid_repo(
    tmp_path: Path,
    cfg_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`standup-tool config validate` returns None and prints 'ok' when valid."""
    from standup_tool.cli import main

    repo = _make_git_repo(tmp_path, "valid-repo")
    _write_config(cfg_dir, _minimal_raw_config(repo))

    result = main(["config", "validate"])
    assert result is None
    captured = capsys.readouterr()
    assert captured.out.strip() == "ok"


def test_cli_config_validate_missing_file_prints_verbatim_onboarding_hint(
    cfg_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`config validate` with no config.yaml writes 'No config at ...' verbatim to stderr."""
    from standup_tool.cli import main

    with pytest.raises(SystemExit) as exc_info:
        main(["config", "validate"])
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert captured.err.strip() == f"No config at {cfg_dir / 'config.yaml'}; run /standup-onboard"


def test_cli_show_filters_secret_keys(
    cfg_dir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`config show` prints JSON with config_dir and filters token/cookie/password keys."""
    import json

    import standup_tool.cli as cli_mod
    from standup_tool.cli import main

    example_text = (STANDUP_ROOT / "config.example.yaml").read_text(encoding="utf-8")
    (cfg_dir / "config.yaml").write_text(example_text, encoding="utf-8")

    ret = main(["config", "show"])
    assert ret is None
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert parsed["config_dir"] == str(cfg_dir.resolve())
    assert parsed["github"]["org"] == "example-org"
    assert isinstance(parsed["collectors"]["slack"]["channels"], list)
    assert isinstance(parsed["repos"][0]["path"], str)

    # Verify recursive secret-key stripping helper directly on synthetic dict
    synthetic = {
        "ok": 1,
        "token": "should-be-removed",
        "nested": {"cookie": "remove-me", "password": "remove-me-too", "keep": "yes"},
        "items": [{"token": "gone", "path": "/a"}],
    }
    cleaned = cli_mod._strip_secret_keys(synthetic)
    assert "token" not in cleaned
    assert "cookie" not in cleaned["nested"]
    assert "password" not in cleaned["nested"]
    assert cleaned["nested"]["keep"] == "yes"
    assert "token" not in cleaned["items"][0]
    assert cleaned["items"][0]["path"] == "/a"


def test_cli_config_no_subcommand_exits_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`standup-tool config` with no subcommand prints config help and exits 2."""
    from standup_tool.cli import main

    with pytest.raises(SystemExit) as exc_info:
        main(["config"])
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "config" in (captured.err + captured.out)


def test_config_dir_rejects_symlink_and_uses_default_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """config_dir() rejects a symlinked directory and defaults to ~/.config/standup."""
    real_dir = tmp_path / "real-cfg"
    real_dir.mkdir(mode=0o700)
    sym_dir = tmp_path / "sym-cfg"
    sym_dir.symlink_to(real_dir)
    monkeypatch.setenv("STANDUP_CONFIG_DIR", str(sym_dir))
    with pytest.raises(ConfigError, match="symlink"):
        config_dir()

    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.delenv("STANDUP_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(fake_home))
    assert config_dir() == (fake_home / ".config" / "standup").resolve()


def test_load_config_rejects_symlink_config_file(
    tmp_path: Path,
    cfg_dir: Path,
) -> None:
    """load_config() refuses a symlinked config.yaml."""
    outside = tmp_path / "outside.yaml"
    outside.write_text(yaml.safe_dump(_minimal_raw_config(tmp_path / "r")), encoding="utf-8")
    target = cfg_dir / "config.yaml"
    target.symlink_to(outside)
    with pytest.raises(ConfigError, match="symlink"):
        load_config(require_repos=False)


def test_identity_and_missing_required_sections_validation(
    tmp_path: Path,
    cfg_dir: Path,
) -> None:
    """Identity email edge cases, missing github/repos sections, and ldap auth_mech=None."""
    # Valid non-null identity.email and ldap.auth_mech=None
    data = _minimal_raw_config(tmp_path / "r")
    data["identity"] = {"email": "ada@example.com"}
    data["ldap"] = {
        "enabled": True,
        "host": "ldaps://ldap.example.com",
        "base_dn": "dc=example,dc=com",
        "email_domain": "example.com",
        "auth_mech": None,
    }
    _write_config(cfg_dir, data)
    cfg = load_config(require_repos=False)
    assert cfg.identity.email == "ada@example.com"
    assert cfg.ldap.auth_mech is None

    # Blank identity.email rejected
    data["identity"] = {"email": "   "}
    _write_config(cfg_dir, data)
    with pytest.raises(ConfigError, match=r"^identity\.email:"):
        load_config(require_repos=False)

    # Non-string identity.email rejected
    data["identity"] = {"email": 42}
    _write_config(cfg_dir, data)
    with pytest.raises(ConfigError, match=r"^identity\.email:"):
        load_config(require_repos=False)

    # Bare ldaps:// without host rejected
    data2 = _minimal_raw_config(tmp_path / "r")
    data2["ldap"] = {"host": "ldaps://"}
    _write_config(cfg_dir, data2)
    with pytest.raises(ConfigError, match=r"^ldap\.host:"):
        load_config(require_repos=False)

    # Missing github section
    data3 = _minimal_raw_config(tmp_path / "r")
    del data3["github"]
    _write_config(cfg_dir, data3)
    with pytest.raises(ConfigError, match=r"^github:"):
        load_config(require_repos=False)

    # Missing repos key
    data4 = _minimal_raw_config(tmp_path / "r")
    del data4["repos"]
    _write_config(cfg_dir, data4)
    with pytest.raises(ConfigError, match=r"^repos:"):
        load_config(require_repos=False)
