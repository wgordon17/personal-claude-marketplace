"""Configuration schema, validation, path helpers, and secret redaction for standup-tool."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
import yaml

_GITHUB_ORG_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_JIRA_PROJECT_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_REGION_RE = re.compile(r"^[a-z0-9-]+$")
_VERTEX_PROJECT_RE = re.compile(r"^[a-z][a-z0-9.:-]{4,61}[a-z0-9]$")
_LDAP_ATTR_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*$")
_LDAP_AUTH_MECH_RE = re.compile(r"^[A-Z0-9_-]+$")
_HOSTNAME_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
_SLACK_HOST_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)*\.slack\.com$")

_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bxox[a-z]-[A-Za-z0-9%_.~-]+", re.IGNORECASE), "xox*-[REDACTED]"),
    (re.compile(r"(?i)\b(Bearer)\s+[A-Za-z0-9._~+/=-]+"), r"\1 [REDACTED]"),
    (re.compile(r"(?i)\b(Basic)\s+[A-Za-z0-9._~+/=-]+"), r"\1 [REDACTED]"),
    (re.compile(r"\bATATT[A-Za-z0-9._~+/=-]+"), "ATATT[REDACTED]"),
    (re.compile(r"\bya29\.[A-Za-z0-9._~+/=-]+"), "ya29.[REDACTED]"),
)


class ConfigError(Exception):
    """Raised when configuration or secret/state paths fail validation."""


def redact_secrets(text: str) -> str:
    """Mask Slack tokens/cookies, Bearer/Basic headers, Atlassian tokens, and Google ya29 tokens."""
    result = text
    for pattern, replacement in _REDACT_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def is_bare_filename(name: str) -> bool:
    """Return True if *name* is a plain single-segment filename without traversal."""
    if not isinstance(name, str) or not name:
        return False
    if name in (".", ".."):
        return False
    if "/" in name or "\\" in name or ".." in name:
        return False
    return Path(name).name == name


def config_dir() -> Path:
    """Resolve the standup configuration directory and verify it is outside any git worktree."""
    env_override = os.environ.get("STANDUP_CONFIG_DIR", "").strip()
    raw_path = (
        Path(env_override).expanduser() if env_override else Path.home() / ".config" / "standup"
    )
    if raw_path.is_symlink():
        raise ConfigError(f"config_dir {raw_path} must not be a symlink")
    resolved = raw_path.resolve()

    ancestor = resolved
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent

    try:
        proc = subprocess.run(
            ["git", "-C", str(ancestor), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return resolved

    if proc.returncode == 0 and proc.stdout.strip() == "true":
        raise ConfigError(f"config_dir {resolved} must not be inside a git worktree")
    return resolved


def ensure_dir_mode() -> Path:
    """Ensure config_dir() exists with mode 0700, tightening looser permissions."""
    d = config_dir()
    if d.is_symlink():
        raise ConfigError(f"config_dir {d} must not be a symlink")
    try:
        d.mkdir(parents=True, mode=0o700, exist_ok=True)
        current_mode = stat.S_IMODE(d.lstat().st_mode)
        if current_mode != 0o700:
            os.chmod(d, 0o700)
    except OSError as exc:
        raise ConfigError(f"Failed to enforce 0700 mode on {d}: {exc}") from exc
    return d


def tighten_mode(path: Path) -> None:
    """Reject symlinks and tighten an existing file's permissions to 0600 if looser."""
    if path.is_symlink():
        raise ConfigError(f"Refusing symlink at {path}")
    if not path.exists():
        return
    try:
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode):
            raise ConfigError(f"Refusing symlink at {path}")
        if (stat.S_IMODE(st.st_mode) & 0o077) != 0 or stat.S_IMODE(st.st_mode) != 0o600:
            os.chmod(path, 0o600)
    except OSError as exc:
        raise ConfigError(f"Failed to tighten permissions on {path}: {exc}") from exc


def secret_path(name: str) -> Path:
    """Resolve a bare secret filename inside config_dir(), rejecting symlinks and tightening."""
    if not is_bare_filename(name):
        raise ConfigError(f"Invalid secret filename: {name!r}")
    p = config_dir() / name
    if p.is_symlink():
        raise ConfigError(f"Refusing symlink at {p}")
    tighten_mode(p)
    return p


def config_path(name: str) -> Path:
    """Resolve a bare config filename inside config_dir(), rejecting symlinks."""
    if not is_bare_filename(name):
        raise ConfigError(f"Invalid config filename: {name!r}")
    p = config_dir() / name
    if p.is_symlink():
        raise ConfigError(f"Refusing symlink at {p}")
    return p


def state_path(name: str) -> Path:
    """Resolve a bare state filename inside config_dir(), rejecting symlinks."""
    if not is_bare_filename(name):
        raise ConfigError(f"Invalid state filename: {name!r}")
    p = config_dir() / name
    if p.is_symlink():
        raise ConfigError(f"Refusing symlink at {p}")
    return p


def validate_https_origin_url(url: str, field_name: str = "url") -> None:
    """Validate that *url* is a strict https:// origin URL with parser agreement."""
    if not isinstance(url, str) or not url:
        raise ConfigError(f"{field_name}: must be a non-empty https:// URL")
    if "\\" in url or any(ord(c) <= 32 for c in url):
        raise ConfigError(f"{field_name}: URL contains invalid characters")

    try:
        parsed = urllib.parse.urlsplit(url)
        prepared_url = requests.Request("GET", url).prepare().url
        if not prepared_url:
            raise ConfigError(f"{field_name}: invalid URL")
        prepared_parsed = urllib.parse.urlsplit(prepared_url)
    except (ValueError, requests.exceptions.RequestException) as exc:
        raise ConfigError(f"{field_name}: invalid URL ({exc})") from exc

    if parsed.scheme != "https":
        raise ConfigError(f"{field_name}: scheme must be https")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise ConfigError(f"{field_name}: userinfo is not allowed")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ConfigError(f"{field_name}: invalid port ({exc})") from exc
    if port is not None or ":" in parsed.netloc:
        raise ConfigError(f"{field_name}: explicit port is not allowed")
    if parsed.path not in ("", "/"):
        raise ConfigError(f"{field_name}: path must be empty or '/'")
    if parsed.query or parsed.fragment:
        raise ConfigError(f"{field_name}: query and fragment are not allowed")
    if not parsed.hostname or parsed.netloc != parsed.hostname:
        raise ConfigError(f"{field_name}: hostname must be lowercase with no extra delimiters")
    if "%" in parsed.netloc or not _HOSTNAME_RE.match(parsed.hostname):
        raise ConfigError(f"{field_name}: invalid hostname {parsed.hostname!r}")
    if (
        prepared_parsed.scheme != "https"
        or prepared_parsed.hostname != parsed.hostname
        or prepared_parsed.username is not None
        or prepared_parsed.port is not None
    ):
        raise ConfigError(f"{field_name}: URL parser mismatch for host {parsed.hostname!r}")


def validate_workspace_url(
    url: str,
    field_name: str = "collectors.slack.workspace_url",
) -> None:
    """Validate that *url* is a strict https://*.slack.com workspace origin URL."""
    validate_https_origin_url(url, field_name)
    parsed = urllib.parse.urlsplit(url)
    if not parsed.hostname or not _SLACK_HOST_RE.match(parsed.hostname):
        raise ConfigError(f"{field_name}: host must match *.slack.com")


def _expect_mapping(raw: Any, path: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected a mapping")
    return raw


def _reject_unknown_keys(mapping: dict[str, Any], allowed: set[str], prefix: str) -> None:
    for key in mapping:
        if key not in allowed:
            dotted = f"{prefix}.{key}" if prefix else str(key)
            raise ConfigError(f"{dotted}: unknown configuration key")


def _expect_bool(val: Any, path: str) -> bool:
    if not isinstance(val, bool):
        raise ConfigError(f"{path}: expected boolean")
    return val


def _expect_opt_str(val: Any, path: str) -> str | None:
    if val is None:
        return None
    if not isinstance(val, str):
        raise ConfigError(f"{path}: expected string or null")
    return val


def _expect_nonempty_str(val: Any, path: str) -> str:
    if not isinstance(val, str) or not val.strip():
        raise ConfigError(f"{path}: expected non-empty string")
    return val


def _expect_str_tuple(val: Any, path: str) -> tuple[str, ...]:
    if not isinstance(val, list):
        raise ConfigError(f"{path}: expected a list of strings")
    items: list[str] = []
    for idx, item in enumerate(val):
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{path}[{idx}]: expected non-empty string")
        items.append(item)
    return tuple(items)


@dataclass(frozen=True)
class IdentityConfig:
    """User identity configuration."""

    email: str | None = None

    @classmethod
    def from_mapping(cls, raw: Any, prefix: str = "identity") -> IdentityConfig:
        if raw is None:
            return cls()
        m = _expect_mapping(raw, prefix)
        _reject_unknown_keys(m, {"email"}, prefix)
        email = _expect_opt_str(m.get("email"), f"{prefix}.email")
        if email is not None and not email.strip():
            raise ConfigError(f"{prefix}.email: must not be empty when set")
        return cls(email=email)


@dataclass(frozen=True)
class GitHubConfig:
    """GitHub organization configuration."""

    org: str

    @classmethod
    def from_mapping(cls, raw: Any, prefix: str = "github") -> GitHubConfig:
        m = _expect_mapping(raw, prefix)
        _reject_unknown_keys(m, {"org"}, prefix)
        org = _expect_nonempty_str(m.get("org"), f"{prefix}.org")
        if not _GITHUB_ORG_RE.match(org):
            raise ConfigError(f"{prefix}.org: must match ^[A-Za-z0-9_-]+$")
        return cls(org=org)


@dataclass(frozen=True)
class RepoEntry:
    """Single configured git repository entry."""

    path: Path

    @classmethod
    def from_mapping(
        cls,
        raw: Any,
        prefix: str,
        *,
        require_repos: bool,
    ) -> RepoEntry:
        m = _expect_mapping(raw, prefix)
        _reject_unknown_keys(m, {"path"}, prefix)
        raw_path = _expect_nonempty_str(m.get("path"), f"{prefix}.path")
        expanded = Path(raw_path).expanduser()
        if require_repos:
            if not expanded.exists() or not expanded.is_dir():
                raise ConfigError(f"{prefix}.path: directory does not exist: {expanded}")
            if not (expanded / ".git").exists():
                raise ConfigError(f"{prefix}.path: not a git repository (missing .git): {expanded}")
            expanded = expanded.resolve()
        return cls(path=expanded)


@dataclass(frozen=True)
class GitConfig:
    """Git collector configuration."""

    enabled: bool = True

    @classmethod
    def from_mapping(cls, raw: Any, prefix: str = "collectors.git") -> GitConfig:
        if raw is None:
            return cls()
        m = _expect_mapping(raw, prefix)
        _reject_unknown_keys(m, {"enabled"}, prefix)
        enabled = _expect_bool(m.get("enabled", True), f"{prefix}.enabled")
        return cls(enabled=enabled)


@dataclass(frozen=True)
class PrConfig:
    """Pull request collector configuration."""

    enabled: bool = True

    @classmethod
    def from_mapping(cls, raw: Any, prefix: str = "collectors.pr") -> PrConfig:
        if raw is None:
            return cls()
        m = _expect_mapping(raw, prefix)
        _reject_unknown_keys(m, {"enabled"}, prefix)
        enabled = _expect_bool(m.get("enabled", True), f"{prefix}.enabled")
        return cls(enabled=enabled)


@dataclass(frozen=True)
class JiraConfig:
    """Jira collector configuration."""

    enabled: bool = True
    project: str | None = None
    server: str | None = None

    @classmethod
    def from_mapping(cls, raw: Any, prefix: str = "collectors.jira") -> JiraConfig:
        if raw is None:
            m: dict[str, Any] = {}
        else:
            m = _expect_mapping(raw, prefix)
            _reject_unknown_keys(m, {"enabled", "project", "server"}, prefix)
        enabled = _expect_bool(m.get("enabled", True), f"{prefix}.enabled")
        project = _expect_opt_str(m.get("project"), f"{prefix}.project")
        server = _expect_opt_str(m.get("server"), f"{prefix}.server")
        if enabled and not project:
            raise ConfigError(f"{prefix}.project: required when {prefix}.enabled is true")
        if project is not None and not _JIRA_PROJECT_RE.match(project):
            raise ConfigError(f"{prefix}.project: must match ^[A-Z][A-Z0-9_]*$")
        if server is not None:
            validate_https_origin_url(server, f"{prefix}.server")
        return cls(enabled=enabled, project=project, server=server)


@dataclass(frozen=True)
class GoogleConfig:
    """Google Workspace collector configuration (calendar, gmail, drive)."""

    enabled: bool = True

    @classmethod
    def from_mapping(cls, raw: Any, prefix: str) -> GoogleConfig:
        if raw is None:
            return cls()
        m = _expect_mapping(raw, prefix)
        _reject_unknown_keys(m, {"enabled"}, prefix)
        enabled = _expect_bool(m.get("enabled", True), f"{prefix}.enabled")
        return cls(enabled=enabled)


@dataclass(frozen=True)
class SlackConfig:
    """Slack collector configuration."""

    enabled: bool = True
    workspace_url: str | None = None
    channels: tuple[str, ...] = ()
    standup_channels: tuple[str, ...] = ()
    self_dm_name: str | None = None

    @classmethod
    def from_mapping(cls, raw: Any, prefix: str = "collectors.slack") -> SlackConfig:
        if raw is None:
            m: dict[str, Any] = {}
        else:
            m = _expect_mapping(raw, prefix)
            _reject_unknown_keys(
                m,
                {"enabled", "workspace_url", "channels", "standup_channels", "self_dm_name"},
                prefix,
            )
        enabled = _expect_bool(m.get("enabled", True), f"{prefix}.enabled")
        workspace_url = _expect_opt_str(m.get("workspace_url"), f"{prefix}.workspace_url")
        if enabled and not workspace_url:
            raise ConfigError(f"{prefix}.workspace_url: required when {prefix}.enabled is true")
        if workspace_url is not None:
            validate_workspace_url(workspace_url, f"{prefix}.workspace_url")
        channels = (
            _expect_str_tuple(m["channels"], f"{prefix}.channels")
            if "channels" in m and m["channels"] is not None
            else ()
        )
        standup_channels = (
            _expect_str_tuple(m["standup_channels"], f"{prefix}.standup_channels")
            if "standup_channels" in m and m["standup_channels"] is not None
            else ()
        )
        self_dm_name = _expect_opt_str(m.get("self_dm_name"), f"{prefix}.self_dm_name")
        return cls(
            enabled=enabled,
            workspace_url=workspace_url,
            channels=channels,
            standup_channels=standup_channels,
            self_dm_name=self_dm_name,
        )


@dataclass(frozen=True)
class MemoryConfig:
    """Project memory collector configuration."""

    enabled: bool = True

    @classmethod
    def from_mapping(cls, raw: Any, prefix: str = "collectors.memory") -> MemoryConfig:
        if raw is None:
            return cls()
        m = _expect_mapping(raw, prefix)
        _reject_unknown_keys(m, {"enabled"}, prefix)
        enabled = _expect_bool(m.get("enabled", True), f"{prefix}.enabled")
        return cls(enabled=enabled)


@dataclass(frozen=True)
class ConversationsConfig:
    """Local AI conversations collector configuration."""

    enabled: bool = False
    project_match: tuple[str, ...] = ()
    vertex_project: str | None = None
    region: str | None = None

    @classmethod
    def from_mapping(
        cls,
        raw: Any,
        prefix: str = "collectors.conversations",
    ) -> ConversationsConfig:
        if raw is None:
            return cls()
        m = _expect_mapping(raw, prefix)
        _reject_unknown_keys(
            m,
            {"enabled", "project_match", "vertex_project", "region"},
            prefix,
        )
        enabled = _expect_bool(m.get("enabled", False), f"{prefix}.enabled")
        project_match = (
            _expect_str_tuple(m["project_match"], f"{prefix}.project_match")
            if "project_match" in m and m["project_match"] is not None
            else ()
        )
        vertex_project = _expect_opt_str(m.get("vertex_project"), f"{prefix}.vertex_project")
        if vertex_project is not None and not _VERTEX_PROJECT_RE.match(vertex_project):
            raise ConfigError(
                f"{prefix}.vertex_project: must match ^[a-z][a-z0-9.:-]{{4,61}}[a-z0-9]$"
            )
        region = _expect_opt_str(m.get("region"), f"{prefix}.region")
        if region is not None and not _REGION_RE.match(region):
            raise ConfigError(f"{prefix}.region: must match ^[a-z0-9-]+$")
        return cls(
            enabled=enabled,
            project_match=project_match,
            vertex_project=vertex_project,
            region=region,
        )


@dataclass(frozen=True)
class CollectorsConfig:
    """All collector configurations."""

    git: GitConfig = field(default_factory=GitConfig)
    pr: PrConfig = field(default_factory=PrConfig)
    jira: JiraConfig = field(default_factory=JiraConfig)
    calendar: GoogleConfig = field(default_factory=GoogleConfig)
    gmail: GoogleConfig = field(default_factory=GoogleConfig)
    drive: GoogleConfig = field(default_factory=GoogleConfig)
    slack: SlackConfig = field(default_factory=SlackConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    conversations: ConversationsConfig = field(default_factory=ConversationsConfig)

    @classmethod
    def from_mapping(cls, raw: Any, prefix: str = "collectors") -> CollectorsConfig:
        if raw is None:
            m: dict[str, Any] = {}
        else:
            m = _expect_mapping(raw, prefix)
            _reject_unknown_keys(
                m,
                {
                    "git",
                    "pr",
                    "jira",
                    "calendar",
                    "gmail",
                    "drive",
                    "slack",
                    "memory",
                    "conversations",
                },
                prefix,
            )
        return cls(
            git=GitConfig.from_mapping(m.get("git"), f"{prefix}.git"),
            pr=PrConfig.from_mapping(m.get("pr"), f"{prefix}.pr"),
            jira=JiraConfig.from_mapping(m.get("jira"), f"{prefix}.jira"),
            calendar=GoogleConfig.from_mapping(m.get("calendar"), f"{prefix}.calendar"),
            gmail=GoogleConfig.from_mapping(m.get("gmail"), f"{prefix}.gmail"),
            drive=GoogleConfig.from_mapping(m.get("drive"), f"{prefix}.drive"),
            slack=SlackConfig.from_mapping(m.get("slack"), f"{prefix}.slack"),
            memory=MemoryConfig.from_mapping(m.get("memory"), f"{prefix}.memory"),
            conversations=ConversationsConfig.from_mapping(
                m.get("conversations"), f"{prefix}.conversations"
            ),
        )


@dataclass(frozen=True)
class StandupConfig:
    """Standup drafting configuration."""

    audience: str = "team"
    template_file: str = "template.md"

    @classmethod
    def from_mapping(cls, raw: Any, prefix: str = "standup") -> StandupConfig:
        if raw is None:
            return cls()
        m = _expect_mapping(raw, prefix)
        _reject_unknown_keys(m, {"audience", "template_file"}, prefix)
        audience = _expect_nonempty_str(m.get("audience", "team"), f"{prefix}.audience")
        template_file = _expect_nonempty_str(
            m.get("template_file", "template.md"),
            f"{prefix}.template_file",
        )
        if not is_bare_filename(template_file):
            raise ConfigError(f"{prefix}.template_file: must be a bare filename inside config_dir")
        return cls(audience=audience, template_file=template_file)


@dataclass(frozen=True)
class LdapConfig:
    """Optional LDAP enrichment configuration."""

    enabled: bool = False
    host: str | None = None
    base_dn: str | None = None
    email_domain: str | None = None
    auth_mech: str | None = "GSSAPI"
    uid_attr: str = "uid"
    worker_id_attr: str | None = None
    manager_id_attr: str | None = None
    social_url_attr: str | None = None

    @classmethod
    def from_mapping(cls, raw: Any, prefix: str = "ldap") -> LdapConfig:
        if raw is None:
            return cls()
        m = _expect_mapping(raw, prefix)
        _reject_unknown_keys(
            m,
            {
                "enabled",
                "host",
                "base_dn",
                "email_domain",
                "auth_mech",
                "uid_attr",
                "worker_id_attr",
                "manager_id_attr",
                "social_url_attr",
            },
            prefix,
        )
        enabled = _expect_bool(m.get("enabled", False), f"{prefix}.enabled")
        host = _expect_opt_str(m.get("host"), f"{prefix}.host")
        base_dn = _expect_opt_str(m.get("base_dn"), f"{prefix}.base_dn")
        email_domain = _expect_opt_str(m.get("email_domain"), f"{prefix}.email_domain")

        if enabled:
            if not host:
                raise ConfigError(f"{prefix}.host: required when {prefix}.enabled is true")
            if not base_dn or not base_dn.strip():
                raise ConfigError(f"{prefix}.base_dn: required when {prefix}.enabled is true")
            if not email_domain or not email_domain.strip():
                raise ConfigError(f"{prefix}.email_domain: required when {prefix}.enabled is true")

        if host is not None and (not host.startswith("ldaps://") or len(host) <= len("ldaps://")):
            raise ConfigError(f"{prefix}.host: must start with ldaps://")

        auth_mech = (
            _expect_opt_str(m["auth_mech"], f"{prefix}.auth_mech") if "auth_mech" in m else "GSSAPI"
        )
        if auth_mech is not None and not _LDAP_AUTH_MECH_RE.match(auth_mech):
            raise ConfigError(f"{prefix}.auth_mech: must match ^[A-Z0-9_-]+$")

        uid_attr = _expect_nonempty_str(m.get("uid_attr", "uid"), f"{prefix}.uid_attr")
        if not _LDAP_ATTR_RE.match(uid_attr):
            raise ConfigError(f"{prefix}.uid_attr: must match ^[A-Za-z][A-Za-z0-9-]*$")

        worker_id_attr = _expect_opt_str(m.get("worker_id_attr"), f"{prefix}.worker_id_attr")
        if worker_id_attr is not None and not _LDAP_ATTR_RE.match(worker_id_attr):
            raise ConfigError(f"{prefix}.worker_id_attr: must match ^[A-Za-z][A-Za-z0-9-]*$")

        manager_id_attr = _expect_opt_str(m.get("manager_id_attr"), f"{prefix}.manager_id_attr")
        if manager_id_attr is not None and not _LDAP_ATTR_RE.match(manager_id_attr):
            raise ConfigError(f"{prefix}.manager_id_attr: must match ^[A-Za-z][A-Za-z0-9-]*$")

        social_url_attr = _expect_opt_str(m.get("social_url_attr"), f"{prefix}.social_url_attr")
        if social_url_attr is not None and not _LDAP_ATTR_RE.match(social_url_attr):
            raise ConfigError(f"{prefix}.social_url_attr: must match ^[A-Za-z][A-Za-z0-9-]*$")

        return cls(
            enabled=enabled,
            host=host,
            base_dn=base_dn,
            email_domain=email_domain,
            auth_mech=auth_mech,
            uid_attr=uid_attr,
            worker_id_attr=worker_id_attr,
            manager_id_attr=manager_id_attr,
            social_url_attr=social_url_attr,
        )


@dataclass(frozen=True)
class Config:
    """Top-level validated configuration for standup-tool."""

    version: int
    identity: IdentityConfig
    github: GitHubConfig
    repos: tuple[RepoEntry, ...]
    collectors: CollectorsConfig
    standup: StandupConfig
    ldap: LdapConfig

    @classmethod
    def from_mapping(cls, raw: Any, *, require_repos: bool = True) -> Config:
        m = _expect_mapping(raw, "config")
        _reject_unknown_keys(
            m,
            {"version", "identity", "github", "repos", "collectors", "standup", "ldap"},
            "",
        )
        if "version" not in m:
            raise ConfigError("version: required field is missing")
        version = m["version"]
        if isinstance(version, bool) or not isinstance(version, int) or version != 1:
            raise ConfigError("version: must be integer 1")

        identity = IdentityConfig.from_mapping(m.get("identity"), "identity")
        if "github" not in m:
            raise ConfigError("github: required section is missing")
        github = GitHubConfig.from_mapping(m["github"], "github")

        if "repos" not in m:
            raise ConfigError("repos: required field is missing")
        raw_repos = m["repos"]
        if not isinstance(raw_repos, list) or not raw_repos:
            raise ConfigError("repos: must be a non-empty list of repository entries")
        repos = tuple(
            RepoEntry.from_mapping(entry, f"repos[{idx}]", require_repos=require_repos)
            for idx, entry in enumerate(raw_repos)
        )

        collectors = CollectorsConfig.from_mapping(m.get("collectors"), "collectors")
        standup = StandupConfig.from_mapping(m.get("standup"), "standup")
        ldap = LdapConfig.from_mapping(m.get("ldap"), "ldap")
        return cls(
            version=version,
            identity=identity,
            github=github,
            repos=repos,
            collectors=collectors,
            standup=standup,
            ldap=ldap,
        )


def load_config(path: Path | None = None, *, require_repos: bool = True) -> Config:
    """Load and validate configuration from *path* (or <config_dir>/config.yaml)."""
    ensure_dir_mode()
    target = path if path is not None else config_path("config.yaml")
    if target.is_symlink():
        raise ConfigError(f"Refusing symlink at {target}")
    if not target.exists():
        raise ConfigError(f"No config at {target}; run /standup-onboard")

    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Failed to read {target}: {exc}") from exc

    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML syntax error in {target}: {exc}") from exc

    if not isinstance(doc, dict):
        raise ConfigError(f"config: expected top-level YAML mapping in {target}")

    return Config.from_mapping(doc, require_repos=require_repos)
