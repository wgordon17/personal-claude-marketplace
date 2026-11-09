"""Cross-system identity enrichment for team member mapping.

Enumerates GitHub org members and enriches them with optional LDAP
directory data, GitHub profiles, and Slack handles. Writes results
to ``team-members.yaml`` in the standup config directory.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

from standup_tool.config import (
    Config,
    LdapConfig,
    SlackConfig,
    config_path,
    secret_path,
    write_private,
)
from standup_tool.slack_auth import (
    SlackAuthError,
    get_slack_credentials,
    make_session,
    safe_get,
)

log = logging.getLogger(__name__)

_GITHUB_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_LDAP_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _escape_ldap_filter_value(val: str) -> str:
    """Escape a filter assertion value per RFC 4515."""
    out: list[str] = []
    for ch in val:
        code = ord(ch)
        if ch == "\\":
            out.append(r"\5c")
        elif ch == "*":
            out.append(r"\2a")
        elif ch == "(":
            out.append(r"\28")
        elif ch == ")":
            out.append(r"\29")
        elif code == 0:
            out.append(r"\00")
        elif code > 127:
            for b in ch.encode("utf-8"):
                out.append(f"\\{b:02x}")
        else:
            out.append(ch)
    return "".join(out)


def get_github_org_members(org: str) -> list[dict]:
    """List GitHub org members via ``gh api``."""
    if not _GITHUB_NAME_RE.match(org):
        log.warning("Invalid GitHub org %r — skipping", org)
        return []
    try:
        result = subprocess.run(
            ["gh", "api", f"orgs/{org}/members", "--paginate", "--jq", ".[].login"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    # tolerant: gh unavailable or timed out yields an empty member list
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        log.warning("gh api failed: %s", result.stderr.strip())
        return []
    members = []
    for line in result.stdout.strip().splitlines():
        login = line.strip()
        if not login:
            continue
        if not _GITHUB_NAME_RE.match(login):
            log.warning("Skipping invalid GitHub login %r", login)
            continue
        members.append({"github": login})
    return members


def _is_fully_enriched(member: dict) -> bool:
    return bool(member.get("name") and member.get("jira_email"))


def enrich_from_github_profiles(
    members: list[dict],
    existing_by_gh: dict[str, dict],
    ldap: LdapConfig,
) -> list[dict]:
    """Enrich members from GitHub profile data (display name, public email)."""
    unenriched = [m for m in members if not _is_fully_enriched(existing_by_gh.get(m["github"], {}))]
    if not unenriched:
        return members

    for member in unenriched:
        gh_login = member["github"]
        existing = existing_by_gh.get(gh_login, {})
        if not _GITHUB_NAME_RE.match(gh_login):
            log.warning("Skipping invalid GitHub login %r", gh_login)
            continue

        try:
            result = subprocess.run(
                [
                    "gh",
                    "api",
                    f"users/{gh_login}",
                    "--jq",
                    r'[.name // "", .email // ""] | @tsv',
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
        # tolerant: gh unavailable skips GitHub profile enrichment and returns members unchanged
        except (FileNotFoundError, subprocess.TimeoutExpired):
            log.warning("gh api unavailable — skipping GitHub profile enrichment")
            return members

        if result.returncode != 0:
            continue

        parts = result.stdout.strip().split("\t")
        profile_name = parts[0].strip() if len(parts) > 0 else ""
        profile_email = parts[1].strip() if len(parts) > 1 else ""

        if profile_name and not existing.get("name"):
            member["name"] = profile_name
            sys.stderr.write(f"  GitHub profile: {gh_login} -> {profile_name}\n")

        if (
            ldap.enabled
            and ldap.email_domain
            and profile_email
            and profile_email.lower().endswith(f"@{ldap.email_domain.lower()}")
            and not existing.get("directory_uid")
        ):
            uid = profile_email.split("@")[0]
            member["directory_uid"] = uid
            sys.stderr.write(f"  GitHub email: {gh_login} -> {uid}\n")

    return members


def enrich_from_ldap(
    members: list[dict],
    existing_by_gh: dict[str, dict],
    ldap: LdapConfig,
) -> list[dict]:
    """Enrich members with configured LDAP directory data.

    Strategies, applied in order:
    1. Management chain walk from a known anchor to discover GitHub↔uid mappings
    2. Direct uid lookup for members with directory_uid or matching GitHub username
    3. Display name (cn) lookup
    """
    if not ldap.enabled:
        return members

    if all(_is_fully_enriched(existing_by_gh.get(m["github"], {})) for m in members):
        sys.stderr.write("All members already enriched — skipping LDAP\n")
        return members

    if not _ldap_available(ldap):
        return members

    github_to_ldap = _build_chain_map(members, existing_by_gh, ldap)

    for member in members:
        gh_login = member["github"]
        existing = existing_by_gh.get(gh_login, {})
        if _is_fully_enriched(existing):
            continue

        ldap_data = github_to_ldap.get(gh_login.lower())
        # Guessing a uid from the GitHub login, or matching on display name, is only safe when
        # the directory entry can be tied back to the GitHub account via social_url_attr; a
        # GitHub display name or login is attacker-chosen. An explicit directory_uid is trusted.
        explicit_uid = existing.get("directory_uid") or member.get("directory_uid")
        uid_to_try = explicit_uid or (gh_login if ldap.social_url_attr else None)

        if not ldap_data and uid_to_try:
            if _LDAP_ID_RE.match(uid_to_try):
                ldap_data = _ldap_lookup_uid(uid_to_try, gh_login, ldap)
            else:
                log.warning("Skipping invalid LDAP uid %r", uid_to_try)

        if not ldap_data and ldap.social_url_attr:
            real_name = existing.get("name") or member.get("name")
            if real_name and real_name != gh_login:
                ldap_data = _ldap_lookup_cn(real_name, gh_login, ldap)

        if not ldap_data:
            continue

        if ldap_data.get("cn"):
            member["name"] = ldap_data["cn"]
        if ldap_data.get("mail"):
            member["jira_email"] = ldap_data["mail"]
        if ldap_data.get("uid") and ldap_data["uid"].lower() != gh_login.lower():
            member["directory_uid"] = ldap_data["uid"]

    return members


def _ldap_auth_args(ldap: LdapConfig) -> list[str]:
    return ["-Y", ldap.auth_mech] if ldap.auth_mech else ["-x"]


def _ldap_available(ldap: LdapConfig) -> bool:
    if not ldap.enabled or not ldap.host or not ldap.base_dn:
        return False
    try:
        result = subprocess.run(
            [
                "ldapsearch",
                "-H",
                ldap.host,
                "-b",
                ldap.base_dn,
                *_ldap_auth_args(ldap),
                f"({ldap.uid_attr}=nobody)",
                "dn",
                "-z",
                "1",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    # tolerant: ldapsearch unavailable disables LDAP enrichment
    except (FileNotFoundError, subprocess.TimeoutExpired):
        log.warning("ldapsearch not available — skipping LDAP enrichment")
        return False
    if result.returncode != 0:
        if "GSSAPI" in result.stderr or "credentials" in result.stderr.lower():
            log.warning("LDAP auth failed — check directory credentials. Skipping LDAP.")
        else:
            log.warning("LDAP unreachable — skipping LDAP enrichment")
        return False
    return True


def _build_chain_map(
    members: list[dict],
    existing_by_gh: dict[str, dict],
    ldap: LdapConfig,
) -> dict[str, dict]:
    """Walk the management chain from known anchors to discover GitHub↔uid mappings."""
    if not ldap.worker_id_attr or not ldap.manager_id_attr:
        return {}

    anchor_uid = None
    for m in members:
        gh = m["github"]
        existing = existing_by_gh.get(gh, {})
        uid = existing.get("directory_uid")
        if uid and existing.get("jira_email"):
            anchor_uid = uid
            break

    if not anchor_uid:
        log.info("No known anchor for chain walk — skipping")
        return {}

    sys.stderr.write(f"  Chain walk: starting from {anchor_uid}\n")

    top_worker_id = _walk_up_chain(anchor_uid, max_levels=5, ldap=ldap)
    if not top_worker_id:
        return {}

    sys.stderr.write(f"  Chain walk: enumerating reports under worker {top_worker_id}\n")

    gh_map: dict[str, dict] = {}
    _enumerate_reports(top_worker_id, gh_map, max_depth=6, ldap=ldap)

    org_logins = {m["github"].lower() for m in members}
    matched = {k: v for k, v in gh_map.items() if k in org_logins}
    sys.stderr.write(f"  Chain walk: found {len(matched)} org members in management tree\n")
    return matched


def _walk_up_chain(uid: str, max_levels: int, ldap: LdapConfig) -> str | None:
    """Walk up supervisors from uid, returning the top worker ID found."""
    if not ldap.worker_id_attr or not ldap.manager_id_attr:
        return None

    current_uid = uid
    top_worker_id = None
    worker_attr = ldap.worker_id_attr
    manager_attr = ldap.manager_id_attr

    for _ in range(max_levels):
        if not _LDAP_ID_RE.match(current_uid):
            log.warning("Skipping invalid LDAP uid %r", current_uid)
            break
        escaped_uid = _escape_ldap_filter_value(current_uid)
        result = _ldap_query(
            f"({ldap.uid_attr}={escaped_uid})",
            [manager_attr, worker_attr],
            ldap,
        )
        if not result:
            break

        worker_id = None
        supervisor_id = None
        for line in result.splitlines():
            line = line.strip()
            if line.startswith(f"{worker_attr}:"):
                worker_id = line.split(":", 1)[1].strip()
            elif line.startswith(f"{manager_attr}:"):
                supervisor_id = line.split(":", 1)[1].strip()

        if worker_id:
            top_worker_id = worker_id

        if not supervisor_id or supervisor_id == worker_id:
            break

        next_uid = _resolve_supervisor_uid(supervisor_id, ldap)
        if not next_uid:
            break
        sys.stderr.write(f"  Chain walk: {current_uid} -> {next_uid}\n")
        current_uid = next_uid

    return top_worker_id


def _resolve_supervisor_uid(worker_id: str, ldap: LdapConfig) -> str | None:
    """Resolve a worker ID to a uid, handling duplicate worker IDs."""
    if not ldap.worker_id_attr:
        return None
    if not _LDAP_ID_RE.match(worker_id):
        log.warning("Skipping invalid LDAP worker_id %r", worker_id)
        return None
    escaped_wid = _escape_ldap_filter_value(worker_id)
    attrs = [ldap.uid_attr]
    if ldap.manager_id_attr:
        attrs.append(ldap.manager_id_attr)
    sup_result = _ldap_query(f"({ldap.worker_id_attr}={escaped_wid})", attrs, ldap)
    if not sup_result:
        return None

    candidates = [entry for entry in _split_ldap_entries(sup_result, ldap) if entry.get("uid")]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]["uid"]

    for c in candidates:
        if c.get("manager_id"):
            return c["uid"]

    return candidates[0]["uid"]


def _enumerate_reports(
    worker_id: str,
    gh_map: dict[str, dict],
    max_depth: int,
    ldap: LdapConfig,
    _depth: int = 0,
) -> None:
    """Recursively find all reports under a worker ID, extracting GitHub URLs."""
    if _depth >= max_depth or not ldap.manager_id_attr:
        return
    if not _LDAP_ID_RE.match(worker_id):
        log.warning("Skipping invalid LDAP worker_id %r", worker_id)
        return

    escaped_wid = _escape_ldap_filter_value(worker_id)
    result = _ldap_query(
        f"({ldap.manager_id_attr}={escaped_wid})",
        _ldap_lookup_attrs(ldap),
        ldap,
    )
    if not result:
        return

    for entry in _split_ldap_entries(result, ldap):
        gh_login = _extract_github_login(entry.get("social_urls", []))
        if gh_login:
            gh_map[gh_login.lower()] = {
                "uid": entry.get("uid"),
                "cn": entry.get("cn"),
                "mail": entry.get("mail"),
                "social_urls": entry.get("social_urls", []),
            }
        child_wid = entry.get("worker_id")
        if child_wid and child_wid != worker_id:
            _enumerate_reports(child_wid, gh_map, max_depth, ldap, _depth + 1)


def _ldap_lookup_attrs(ldap: LdapConfig) -> list[str]:
    attrs = [ldap.uid_attr, "cn", "mail"]
    for extra in (ldap.worker_id_attr, ldap.manager_id_attr, ldap.social_url_attr):
        if extra and extra not in attrs:
            attrs.append(extra)
    return attrs


def _ldap_query(ldap_filter: str, attrs: list[str], ldap: LdapConfig) -> str | None:
    if not ldap.host or not ldap.base_dn:
        return None
    try:
        result = subprocess.run(
            [
                "ldapsearch",
                "-H",
                ldap.host,
                "-b",
                ldap.base_dn,
                *_ldap_auth_args(ldap),
                ldap_filter,
                *attrs,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    # tolerant: an LDAP query timeout skips that lookup
    except subprocess.TimeoutExpired:
        log.warning("LDAP lookup failed for %s", ldap_filter)
        return None
    if result.returncode != 0:
        log.warning("LDAP lookup failed for %s", ldap_filter)
        return None
    return result.stdout


def _ldap_lookup_uid(uid: str, gh_login: str, ldap: LdapConfig) -> dict | None:
    if not _LDAP_ID_RE.match(uid):
        log.warning("Skipping invalid LDAP uid %r", uid)
        return None
    escaped_uid = _escape_ldap_filter_value(uid)
    stdout = _ldap_query(f"({ldap.uid_attr}={escaped_uid})", _ldap_lookup_attrs(ldap), ldap)
    if not stdout:
        return None
    parsed = _parse_ldap_result(stdout, ldap)
    if not parsed:
        return None
    if ldap.social_url_attr and not _verify_github_match(parsed.get("social_urls", []), gh_login):
        log.info("LDAP uid=%s has no matching GitHub URL for %s", uid, gh_login)
        return None
    parsed["uid"] = uid
    return parsed


def _ldap_lookup_cn(cn: str, gh_login: str, ldap: LdapConfig) -> dict | None:
    """Search LDAP by cn (display name) and verify via configured social URL attribute."""
    stripped = cn.strip()
    if not (1 <= len(stripped) <= 128) or any(ord(c) < 32 for c in cn):
        log.warning("Skipping invalid LDAP cn for %s", gh_login)
        return None
    safe_cn = _escape_ldap_filter_value(stripped)
    stdout = _ldap_query(f"(cn={safe_cn})", _ldap_lookup_attrs(ldap), ldap)
    if not stdout:
        return None
    for entry in _split_ldap_entries(stdout, ldap):
        if not ldap.social_url_attr or _verify_github_match(entry.get("social_urls", []), gh_login):
            sys.stderr.write(f"  cn search: {cn} -> uid={entry.get('uid')} for {gh_login}\n")
            return entry
    return None


def _parse_ldap_result(stdout: str, ldap: LdapConfig | None = None) -> dict | None:
    social_attr = ldap.social_url_attr if ldap else None
    data: dict = {"social_urls": []}
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("cn:"):
            data["cn"] = line.split(":", 1)[1].strip()
        elif line.startswith("mail:"):
            data["mail"] = line.split(":", 1)[1].strip()
        elif social_attr and line.startswith(f"{social_attr}:"):
            data["social_urls"].append(line.split(":", 1)[1].strip())
    if not data.get("cn"):
        return None
    return data


def _split_ldap_entries(stdout: str, ldap: LdapConfig | None = None) -> list[dict]:
    uid_attr = ldap.uid_attr if ldap else "uid"
    worker_attr = ldap.worker_id_attr if ldap else None
    manager_attr = ldap.manager_id_attr if ldap else None
    social_attr = ldap.social_url_attr if ldap else None

    entries: list[dict] = []
    current: dict = {"social_urls": []}
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("dn:"):
            if current.get("uid") or current.get("cn"):
                entries.append(current)
                current = {"social_urls": []}
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if social_attr and key == social_attr:
            current.setdefault("social_urls", []).append(value)
        elif key == uid_attr:
            current["uid"] = value
        elif key in ("cn", "mail"):
            current[key] = value
        elif worker_attr and key == worker_attr:
            current["worker_id"] = value
        elif manager_attr and key == manager_attr:
            current["manager_id"] = value
    if current.get("uid") or current.get("cn"):
        entries.append(current)
    return entries


def _extract_github_login(social_urls: list[str]) -> str | None:
    for url in social_urls:
        if url.startswith("Github->"):
            parts = url.split("/")
            if parts:
                return parts[-1]
    return None


def _verify_github_match(social_urls: list[str], gh_login: str) -> bool:
    return any(
        url.startswith("Github->") and url.lower().endswith(f"/{gh_login.lower()}")
        for url in social_urls
    )


def enrich_from_slack(
    members: list[dict],
    existing_by_gh: dict[str, dict],
    slack_cfg: SlackConfig,
    token_path: Path,
) -> list[dict]:
    """Enrich members with Slack handles by matching emails via users.list."""
    target_emails: dict[str, dict] = {}
    for m in members:
        existing = existing_by_gh.get(m["github"], {})
        if existing.get("slack_id"):
            continue
        email = m.get("jira_email") or existing.get("jira_email")
        if email:
            target_emails[email.lower()] = m

    if not target_emails:
        sys.stderr.write("All members with emails already have Slack IDs — skipping\n")
        return members

    try:
        creds = get_slack_credentials(slack_cfg, token_path)
    # tolerant: missing Slack credentials skip Slack enrichment
    except SlackAuthError:
        log.warning("No Slack credentials — skipping Slack enrichment")
        return members

    session = make_session(creds)
    email_to_slack = _build_slack_email_map(session, target_emails)

    found = 0
    for email, slack_name in email_to_slack.items():
        member = target_emails.get(email)
        if member and slack_name:
            member["slack_id"] = slack_name
            found += 1
            sys.stderr.write(f"  Slack: {member['github']} -> @{slack_name}\n")

    sys.stderr.write(f"  Slack: resolved {found}/{len(target_emails)} handles\n")
    return members


def _build_slack_email_map(session: object, target_emails: dict[str, dict]) -> dict[str, str]:
    """Paginate users.list, returning email->slack_name for target matches."""
    matched: dict[str, str] = {}
    remaining = set(target_emails.keys())
    cursor = None
    retries = 0
    max_retries = 3

    while remaining:
        params: dict[str, str] = {"limit": "200"}
        if cursor:
            params["cursor"] = cursor

        try:
            resp = safe_get(session, "https://slack.com/api/users.list", params=params)
            resp.raise_for_status()
            data = resp.json()
        # tolerant: a failed Slack users.list page is retried with backoff, then enrichment stops
        except Exception:
            retries += 1
            if retries > max_retries:
                log.warning("Slack users.list failed after %d retries", max_retries)
                break
            time.sleep(2**retries)
            continue

        if not data.get("ok"):
            error = data.get("error", "unknown")
            if error == "ratelimited":
                retry_after = int(resp.headers.get("Retry-After", "5"))
                time.sleep(retry_after)
                continue
            if error in ("invalid_auth", "token_revoked"):
                log.warning("Slack auth failed: %s", error)
                break
            retries += 1
            if retries > max_retries:
                log.warning("Slack users.list error after retries: %s", error)
                break
            time.sleep(2**retries)
            continue

        retries = 0
        for user in data.get("members", []):
            email = (user.get("profile", {}).get("email") or "").lower()
            if email in remaining:
                matched[email] = user.get("name", "")
                remaining.discard(email)

        cursor = data.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            break

        time.sleep(0.5)

    return matched


def merge_team(existing: list[dict], enriched: list[dict]) -> list[dict]:
    """Merge enriched members into existing team list.

    Existing manually-set values are preserved (only null fields updated).
    Names that are just the GitHub username are treated as placeholders
    and get overwritten by real names from LDAP or GitHub profiles.
    """
    by_github = {m["github"]: m for m in existing}
    for member in enriched:
        gh = member["github"]
        if gh in by_github:
            existing_member = by_github[gh]
            for key in ("jira_email", "slack_id", "directory_uid"):
                if not existing_member.get(key) and member.get(key):
                    existing_member[key] = member[key]
            if member.get("name") and (
                not existing_member.get("name") or existing_member["name"] == gh
            ):
                existing_member["name"] = member["name"]
        else:
            entry = {"name": member.get("name") or member["github"], "github": gh}
            for key in ("directory_uid", "jira_email", "slack_id"):
                if member.get(key):
                    entry[key] = member[key]
            existing.append(entry)
    return existing


def run_enrichment(
    config: Config,
    org: str | None = None,
    dry_run: bool = False,
) -> None:
    """Full enrichment pipeline: GitHub → LDAP → Slack → merge → write."""
    target_org = org or config.github.org
    team_file = config_path("team-members.yaml")

    existing: list[dict] = []
    if team_file.exists():
        with open(team_file) as f:
            data = yaml.safe_load(f)
        if isinstance(data, list):
            existing = data

    existing_by_gh = {m["github"]: m for m in existing if m.get("github")}

    sys.stderr.write(f"Fetching members from GitHub org '{target_org}'...\n")
    members = get_github_org_members(target_org)
    if not members:
        sys.stderr.write("No members found. Check gh auth or org name.\n")
        return
    sys.stderr.write(f"Found {len(members)} org members\n")

    sys.stderr.write("Fetching GitHub profiles...\n")
    members = enrich_from_github_profiles(members, existing_by_gh, config.ldap)

    sys.stderr.write("Enriching from LDAP...\n")
    members = enrich_from_ldap(members, existing_by_gh, config.ldap)

    sys.stderr.write("Resolving Slack handles...\n")
    enriched = enrich_from_slack(
        members,
        existing_by_gh,
        config.collectors.slack,
        secret_path("slack-token"),
    )

    merged = merge_team(existing, enriched)

    output = yaml.dump(merged, default_flow_style=False, sort_keys=False, allow_unicode=True)

    if dry_run:
        sys.stdout.write(output)
    else:
        header = (
            "# Team member identity mapping\n"
            "# Maps GitHub usernames, Jira emails, and Slack IDs "
            "for cross-system correlation.\n"
            "# Used by /standup to resolve names across PR reviews, "
            "Jira assignments, and Slack mentions.\n"
            "#\n"
            "# To add a member: copy a block and fill in known identifiers. "
            "Not all fields are required.\n"
            "# Set directory_uid when GitHub username differs from directory UID "
            "(e.g. ada-example -> aexample).\n"
            f"# GitHub usernames: gh api orgs/{target_org}/members --jq '.[].login'\n"
            "# Slack usernames: visible in collector output from team channels\n\n"
        )
        write_private(team_file, header + output)
        sys.stderr.write(f"Wrote {len(merged)} members to {team_file}\n")
