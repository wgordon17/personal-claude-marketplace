"""Command-line interface for standup-tool."""

from __future__ import annotations

import argparse
import dataclasses
import html
import json
import logging
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from standup_tool.collectors import (
    conversation_activity,
    git_activity,
    gmail,
    google_calendar,
    google_drive,
    jira_activity,
    pr_activity,
    slack_activity,
)
from standup_tool.config import (
    Config,
    ConfigError,
    config_dir,
    config_path,
    load_config,
    secret_path,
    state_path,
    validate_https_origin_url,
)
from standup_tool.template import Header, load_template
from standup_tool.workspace import list_repos

_SECRET_FIELD_NAMES = frozenset({"token", "cookie", "password"})


def _serialize_config_value(obj: Any) -> Any:
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, tuple | list):
        return [_serialize_config_value(item) for item in obj]
    if isinstance(obj, dict):
        return {
            str(k): _serialize_config_value(v)
            for k, v in obj.items()
            if str(k).lower() not in _SECRET_FIELD_NAMES
        }
    return obj


def _strip_secret_keys(data: Any) -> Any:
    """Recursively convert Paths/tuples and strip any key named token, cookie, or password."""
    return _serialize_config_value(data)


def _get_author_email(config: Config) -> str:
    """Return configured identity.email or fall back to `git config user.email` in repos[0]."""
    if config.identity.email:
        return config.identity.email
    if not config.repos:
        return ""
    result = subprocess.run(
        ["git", "config", "user.email"],
        cwd=config.repos[0].path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _get_jira_server(config: Config, config_path: Path | None = None) -> str:
    """Return configured Jira server URL or read & validate it from jira-cli config."""
    if config.collectors.jira.server:
        return config.collectors.jira.server.rstrip("/")

    jira_cfg = (
        config_path if config_path is not None else Path("~/.config/.jira/.config.yml").expanduser()
    )
    try:
        if jira_cfg.exists():
            for line in jira_cfg.read_text(encoding="utf-8").splitlines():
                if line.startswith("server:"):
                    server = line.split(":", 1)[1].strip().strip('"').strip("'").rstrip("/")
                    if server:
                        validate_https_origin_url(server, "jira-cli server")
                        return server
    except ConfigError:
        raise
    except OSError:
        pass

    raise ConfigError(
        "Jira server URL is not configured; set collectors.jira.server in config.yaml "
        "or configure ~/.config/.jira/.config.yml"
    )


def _collect(args: argparse.Namespace, config: Config) -> None:
    try:
        since = date.fromisoformat(args.since)
    except ValueError:
        sys.stderr.write(
            f"ERROR: Invalid --since date '{args.since}'. Expected format: YYYY-MM-DD\n"
        )
        sys.exit(1)

    if args.until:
        try:
            until = date.fromisoformat(args.until)
        except ValueError:
            sys.stderr.write(
                f"ERROR: Invalid --until date '{args.until}'. Expected format: YYYY-MM-DD\n"
            )
            sys.exit(1)
    else:
        until = max(since, date.today() - timedelta(days=1))

    cfg_dir = config_dir()
    author_email = _get_author_email(config)
    if not author_email:
        logging.warning(
            "author_email is empty; git_activity and gmail will collect all authors' commits"
        )

    try:
        repos = list_repos(config)
    except Exception as exc:
        logging.warning("workspace discovery failed: %s", exc)
        repos = []

    def run_git() -> list[dict]:
        try:
            return git_activity.collect(repos, since, until, author_email=author_email)
        except Exception as exc:
            logging.warning("git_activity collector failed: %s", exc)
            return []

    def run_pr() -> list[dict]:
        try:
            return pr_activity.collect(repos, since, until)
        except Exception as exc:
            logging.warning("pr_activity collector failed: %s", exc)
            return []

    def run_jira() -> dict:
        try:
            return jira_activity.collect(
                since_date=since,
                project=config.collectors.jira.project or "",
            )
        except Exception as exc:
            logging.warning("jira_activity collector failed: %s", exc)
            return {}

    def run_calendar() -> list[dict]:
        try:
            return google_calendar.collect(since, until, config_dir=cfg_dir)
        except Exception as exc:
            logging.warning("google_calendar collector failed: %s", exc)
            return []

    def run_gmail() -> list[dict]:
        try:
            return gmail.collect(since, until, my_email=author_email, config_dir=cfg_dir)
        except Exception as exc:
            logging.warning("gmail collector failed: %s", exc)
            return []

    def run_slack() -> list[dict]:
        try:
            return slack_activity.collect(
                since,
                until,
                token_path=secret_path("slack-token"),
                workspace_url=config.collectors.slack.workspace_url,
                channels=config.collectors.slack.channels,
            )
        except Exception as exc:
            logging.warning("slack collector failed: %s", exc)
            return []

    def run_conversations() -> list[dict]:
        try:
            return conversation_activity.collect(
                since,
                until,
                repo_keys=conversation_activity.build_repo_keys(repos),
                patterns=config.collectors.conversations.project_match,
                vertex_project=config.collectors.conversations.vertex_project,
                region=config.collectors.conversations.region,
            )
        except Exception as exc:
            logging.warning("conversation_activity collector failed: %s", exc)
            return []

    all_tasks = {
        "git_commits": ("git", run_git),
        "prs": ("pr", run_pr),
        "jira_activity": ("jira", run_jira),
        "calendar_events": ("calendar", run_calendar),
        "emails": ("gmail", run_gmail),
        "slack": ("slack", run_slack),
        "conversations": ("conversations", run_conversations),
    }

    enabled = set(args.collectors) if args.collectors else None
    tasks = {
        key: fn
        for key, (collector_name, fn) in all_tasks.items()
        if enabled is None or collector_name in enabled
    }

    results: dict = {}
    with ThreadPoolExecutor(max_workers=len(tasks) or 1) as executor:
        future_to_key = {executor.submit(fn): key for key, fn in tasks.items()}
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            results[key] = future.result()

    calendar_events = results.get("calendar_events", [])

    drive_docs: list[dict] = []
    if (enabled is None or "drive" in enabled) and calendar_events:
        try:
            drive_docs = google_drive.collect(calendar_events, since, until, config_dir=cfg_dir)
        except Exception as exc:
            logging.warning("google_drive collector failed: %s", exc)

    output = {
        "git_commits": results.get("git_commits", []),
        "prs": results.get("prs", []),
        "jira_activity": results.get("jira_activity", {}),
        "calendar_events": calendar_events,
        "emails": results.get("emails", []),
        "slack": results.get("slack", []),
        "drive_docs": drive_docs,
        "dev_activity": [],
        "conversations": results.get("conversations", []),
    }
    json.dump(output, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")

    jira = output["jira_activity"]
    jira_total = (
        sum(len(v) for v in jira.values() if isinstance(v, list)) if isinstance(jira, dict) else 0
    )
    slack_sources: dict[str, int] = {}
    for s in output["slack"]:
        src = s.get("source", "?")
        slack_sources[src] = slack_sources.get(src, 0) + 1
    slack_detail = ", ".join(f"{c} {s}" for s, c in sorted(slack_sources.items())) or "0"

    sys.stderr.write(
        f"COLLECTION SUMMARY: "
        f"git={len(output['git_commits'])} "
        f"pr={len(output['prs'])} "
        f"jira={jira_total} "
        f"calendar={len(output['calendar_events'])} "
        f"email={len(output['emails'])} "
        f"slack={len(output['slack'])} ({slack_detail}) "
        f"drive={len(output['drive_docs'])} "
        f"dev={len(output['dev_activity'])} "
        f"conversations={len(output['conversations'])}\n"
    )

    if enabled is None and not args.no_marker:
        _write_last_run_marker(cfg_dir, until)


_LAST_RUN_FILE = ".last-run"


def read_last_run(config_dir: Path) -> date | None:
    """Read the last-run marker date from `.last-run` in the config directory."""
    marker = config_dir / _LAST_RUN_FILE
    try:
        text = marker.read_text().strip()
        return date.fromisoformat(text)
    except (OSError, ValueError):
        return None


def _write_last_run_marker(config_dir: Path, until_date: date) -> None:
    """Write the day after until_date as the next collection start date."""
    marker = config_dir / _LAST_RUN_FILE
    next_start = until_date + timedelta(days=1)
    try:
        marker.write_text(next_start.isoformat() + "\n")
    except OSError as exc:
        logging.warning("Failed to write last-run marker: %s", exc)


def _extract_jira_auth() -> str:
    """Build Authorization header from JIRA_API_TOKEN env var or jira-cli debug output."""
    import base64
    import os

    token = os.environ.get("JIRA_API_TOKEN", "")
    if token:
        cfg_file = Path("~/.config/.jira/.config.yml").expanduser()
        login = ""
        if cfg_file.exists():
            for line in cfg_file.read_text().splitlines():
                if line.startswith("login:"):
                    login = line.split(":", 1)[1].strip()
                    break
        if login:
            creds = base64.b64encode(f"{login}:{token}".encode()).decode()
            return f"Basic {creds}"

    result = subprocess.run(
        ["jira", "me", "--debug"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    for line in (result.stdout + result.stderr).splitlines():
        if "Authorization:" in line:
            return line.split("Authorization:", 1)[1].strip()
    return ""


def _slack_auth(config: Config) -> tuple:
    """Return (session, user_id), exit on auth failure."""
    from standup_tool.collectors.slack_activity import _get_credentials, _make_session

    bearer_token, cookie = _get_credentials(
        secret_path("slack-token"),
        config.collectors.slack.workspace_url,
    )
    session = _make_session(bearer_token, cookie)
    resp = session.get("https://slack.com/api/auth.test")
    try:
        resp.raise_for_status()
    except Exception as exc:
        sys.stderr.write(f"ERROR: Slack auth.test HTTP error: {exc}\n")
        sys.exit(1)
    auth_data = resp.json()
    if not auth_data.get("ok"):
        sys.stderr.write(f"ERROR: Slack auth failed: {auth_data.get('error')}\n")
        sys.exit(1)
    return session, auth_data["user_id"]


_INLINE_RE = re.compile(r"(<[^>]+>|:[a-zA-Z0-9_+-]+:)")


def _parse_inline_elements(text: str) -> list[dict]:
    """Parse inline mrkdwn into rich_text elements (text, links, emoji)."""
    elements: list[dict] = []
    last = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > last:
            elements.append({"type": "text", "text": html.unescape(text[last : m.start()])})
        token = m.group()
        if token.startswith("<"):
            inner = token[1:-1]
            if "|" in inner:
                url, label = inner.split("|", 1)
                elements.append({"type": "link", "url": url, "text": html.unescape(label)})
            else:
                elements.append({"type": "link", "url": inner})
        else:
            elements.append({"type": "emoji", "name": token[1:-1]})
        last = m.end()
    if last < len(text):
        elements.append({"type": "text", "text": html.unescape(text[last:])})
    return elements


def _is_section_header(line: str, headers: Sequence[Header]) -> bool:
    """Return True if *line* matches one of the configured template section headers."""
    candidate = html.unescape(line.strip())
    return any(candidate == f"{h.emoji} {h.text}" for h in headers)


def _standup_to_blocks(text: str, headers: Sequence[Header]) -> list[dict]:
    """Convert standup text into Slack Block Kit rich_text blocks."""
    lines = text.split("\n")
    block_elements: list[dict] = []
    current_bullets: list[dict] = []

    def flush_bullets() -> None:
        if current_bullets:
            block_elements.append(
                {
                    "type": "rich_text_list",
                    "style": "bullet",
                    "indent": 0,
                    "border": 0,
                    "elements": list(current_bullets),
                }
            )
            current_bullets.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_bullets()
            continue

        if _is_section_header(stripped, headers):
            flush_bullets()
            header_elements = _parse_inline_elements(stripped)
            for el in header_elements:
                if el.get("type") in ("text", "link"):
                    el["style"] = {**(el.get("style") or {}), "bold": True}
            prefix = [{"type": "text", "text": "\n"}] if block_elements else []
            block_elements.append(
                {
                    "type": "rich_text_section",
                    "elements": [*prefix, *header_elements, {"type": "text", "text": "\n"}],
                }
            )
        elif stripped.startswith(("• ", "- ", "* ")):
            bullet_text = stripped[2:]
            current_bullets.append(
                {
                    "type": "rich_text_section",
                    "elements": _parse_inline_elements(bullet_text),
                }
            )
        else:
            flush_bullets()
            content_elements = _parse_inline_elements(stripped)
            block_elements.append(
                {
                    "type": "rich_text_section",
                    "elements": [*content_elements, {"type": "text", "text": "\n"}],
                }
            )

    flush_bullets()
    return [{"type": "rich_text", "elements": block_elements}]


def _post_standup(args: argparse.Namespace, config: Config) -> None:
    """Post standup draft to the user's self-DM for review."""
    standup_file = Path(args.file)

    if not standup_file.exists():
        sys.stderr.write(f"ERROR: Standup file not found: {standup_file}\n")
        sys.exit(1)

    text = standup_file.read_text().strip()
    if not text:
        sys.stderr.write("ERROR: Standup file is empty\n")
        sys.exit(1)

    headers = load_template(config_path(config.standup.template_file)).headers
    session, user_id = _slack_auth(config)

    dm_resp = session.post(
        "https://slack.com/api/conversations.open",
        json={"users": user_id},
    )
    try:
        dm_resp.raise_for_status()
    except Exception as exc:
        sys.stderr.write(f"ERROR: conversations.open HTTP error: {exc}\n")
        sys.exit(1)
    dm_data = dm_resp.json()
    if not dm_data.get("ok"):
        sys.stderr.write(f"ERROR: Could not open self-DM: {dm_data.get('error')}\n")
        sys.exit(1)
    channel_id = dm_data["channel"]["id"]

    blocks = _standup_to_blocks(text, headers)
    fallback = text.replace("• ", "- ").replace("* ", "- ")

    post_resp = session.post(
        "https://slack.com/api/chat.postMessage",
        json={
            "channel": channel_id,
            "text": fallback,
            "blocks": blocks,
            "unfurl_links": False,
            "unfurl_media": False,
        },
    )
    try:
        post_resp.raise_for_status()
    except Exception as exc:
        sys.stderr.write(f"ERROR: chat.postMessage HTTP error: {exc}\n")
        sys.exit(1)
    post_data = post_resp.json()
    if not post_data.get("ok"):
        sys.stderr.write(f"ERROR: Failed to post: {post_data.get('error')}\n")
        sys.exit(1)

    ts = post_data.get("ts", "")
    sys.stdout.write(f"Posted to self-DM (channel {channel_id}, ts {ts})\n")


def _is_standup(text: str, headers: Sequence[Header]) -> bool:
    """Return True if *text* contains at least two template section header lines."""
    matched = sum(1 for line in text.splitlines() if _is_section_header(line, headers))
    return matched >= 2


def _build_standup_queries(headers: Sequence[Header]) -> list[str]:
    """Derive Slack search queries from each template header's emoji and first two words."""
    queries: list[str] = []
    for h in headers:
        if h.emoji not in queries:
            queries.append(h.emoji)
        two_words = " ".join(h.text.split()[:2])
        if two_words and two_words not in queries:
            queries.append(two_words)
    return queries


def _standup_history(args: argparse.Namespace, config: Config) -> None:
    """Fetch recent standup pairs (draft + published) from Slack for edit learning."""
    days = args.days

    if days <= 0:
        sys.stderr.write("ERROR: --days must be a positive integer\n")
        sys.exit(1)
    if days > 90:
        sys.stderr.write("ERROR: --days maximum is 90\n")
        sys.exit(1)

    import time

    from standup_tool.collectors.slack_activity import REQUEST_DELAY, _search

    headers = load_template(config_path(config.standup.template_file)).headers
    standup_queries = _build_standup_queries(headers)
    standup_channels = config.collectors.slack.standup_channels

    session, user_id = _slack_auth(config)

    end = date.today()
    drafts_by_date: dict[str, str] = {}
    published_by_date: dict[str, str] = {}

    try:
        for day_offset in range(days):
            d = end - timedelta(days=day_offset + 1)
            day_str = d.isoformat()

            for sq in standup_queries:
                for m in _search(
                    session,
                    f"from:<@{user_id}> to:<@{user_id}> {sq} on:{day_str}",
                    count=20,
                ):
                    if _is_standup(m.get("text", ""), headers) and day_str not in drafts_by_date:
                        drafts_by_date[day_str] = m["text"]
                        break
                if day_str in drafts_by_date:
                    break
                time.sleep(REQUEST_DELAY)

            time.sleep(REQUEST_DELAY)

            for ch in standup_channels:
                for sq in standup_queries:
                    for m in _search(
                        session,
                        f"from:<@{user_id}> in:{ch} {sq} on:{day_str}",
                        count=20,
                    ):
                        if (
                            _is_standup(m.get("text", ""), headers)
                            and day_str not in published_by_date
                        ):
                            published_by_date[day_str] = m["text"]
                            break
                    if day_str in published_by_date:
                        break
                    time.sleep(REQUEST_DELAY)
                if day_str in published_by_date:
                    break

            if day_offset < days - 1:
                time.sleep(REQUEST_DELAY)
    except PermissionError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        sys.exit(1)

    all_dates = sorted(set(drafts_by_date) | set(published_by_date), reverse=True)
    pairs = [
        {
            "date": d,
            "draft": drafts_by_date.get(d, ""),
            "published": published_by_date.get(d, ""),
        }
        for d in all_dates
    ]

    json.dump(pairs, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    sys.stderr.write(
        f"STANDUP HISTORY: {len(pairs)} day(s) with standup data over {days}-day window\n"
    )


def _backlog(args: argparse.Namespace, config: Config) -> None:
    """Move issues to backlog (removes from sprint) via Jira Agile REST API."""
    issues = args.issues
    for key in issues:
        if not re.match(r"^[A-Z]+-[0-9]+$", key):
            sys.stderr.write(f"ERROR: Invalid issue key '{key}'\n")
            sys.exit(1)

    auth_header = _extract_jira_auth()
    if not auth_header:
        sys.stderr.write("ERROR: Could not extract auth from jira CLI\n")
        sys.exit(1)

    server = _get_jira_server(config)
    url = f"{server}/rest/agile/1.0/backlog/issue"
    data = json.dumps({"issues": issues}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", auth_header)
    req.add_header("Content-Type", "application/json")

    try:
        resp = urllib.request.urlopen(req)
        if resp.status == 204:
            for key in issues:
                sys.stdout.write(f"{key}: removed from sprint\n")
        else:
            sys.stdout.write(f"Unexpected status: {resp.status}\n")
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"ERROR: {e.code} — {e.read().decode()}\n")
        sys.exit(1)


def _digest(args: argparse.Namespace, config: Config) -> None:
    """Run entity-linked work-unit grouping on collector JSON."""
    from standup_tool.digest import build_digest, load_team

    json_path = Path(args.file)
    if not json_path.exists():
        sys.stderr.write(f"ERROR: file not found: {json_path}\n")
        sys.exit(1)

    with open(json_path) as f:
        parsed_json = json.load(f)

    team = load_team(config_path("team-members.yaml"))

    try:
        since = date.fromisoformat(args.since)
    except ValueError:
        sys.stderr.write(
            f"ERROR: Invalid --since date '{args.since}'. Expected format: YYYY-MM-DD\n"
        )
        sys.exit(1)

    if args.until:
        try:
            until = date.fromisoformat(args.until)
        except ValueError:
            sys.stderr.write(
                f"ERROR: Invalid --until date '{args.until}'. Expected format: YYYY-MM-DD\n"
            )
            sys.exit(1)
    else:
        until = max(since, date.today() - timedelta(days=1))

    self_dm = args.self_dm or config.collectors.slack.self_dm_name or ""
    if not self_dm and team:
        first_name = team[0].get("name", "")
        if first_name:
            self_dm = f"DM with {first_name}"

    digest = build_digest(parsed_json, team, since, until, self_dm=self_dm)

    output_path = Path(args.output) if args.output else state_path(".digest.json")
    with open(output_path, "w") as f:
        json.dump(digest, f, indent=2, default=str)
        f.write("\n")

    n_units = len(digest.get("work_units", []))
    n_uncorrelated = len(digest.get("uncorrelated", []))
    sys.stderr.write(
        f"DIGEST: {n_units} work units, {n_uncorrelated} uncorrelated items → {output_path}\n"
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser for standup-tool."""
    parser = argparse.ArgumentParser(
        prog="standup-tool",
        description="Daily standup data collector, digest formatter, and helper CLI.",
    )
    subparsers = parser.add_subparsers(dest="command")

    collect_parser = subparsers.add_parser(
        "collect",
        help="Collect activity data and output JSON",
    )
    collect_parser.add_argument("--since", required=True, help="Start date (YYYY-MM-DD)")
    collect_parser.add_argument(
        "--until",
        default=None,
        help="End date (YYYY-MM-DD), defaults to yesterday",
    )
    collect_parser.add_argument(
        "--collectors",
        nargs="*",
        choices=["git", "pr", "jira", "calendar", "gmail", "slack", "drive", "conversations"],
        default=None,
        help="Collectors to run (default: all)",
    )
    collect_parser.add_argument(
        "--no-marker",
        action="store_true",
        help="Skip writing the last-run marker, even when running all collectors "
        "(for preview/guidance runs that must not shift the next real run's date range)",
    )

    digest_parser = subparsers.add_parser(
        "digest",
        help="Entity-linked work-unit grouping from collector JSON",
    )
    digest_parser.add_argument("--file", required=True, help="Path to collector JSON output")
    digest_parser.add_argument("--since", required=True, help="Start date (YYYY-MM-DD)")
    digest_parser.add_argument(
        "--until",
        default=None,
        help="End date (YYYY-MM-DD), defaults to yesterday",
    )
    digest_parser.add_argument(
        "--self-dm",
        default=None,
        help="Self-DM channel name to exclude",
    )
    digest_parser.add_argument(
        "--output",
        default=None,
        help="Output file path (default: <config_dir>/.digest.json)",
    )

    post_parser = subparsers.add_parser(
        "post-standup",
        help="Post standup draft to self-DM for review",
    )
    post_parser.add_argument("--file", required=True, help="Path to standup draft file")

    backlog_parser = subparsers.add_parser(
        "backlog",
        help="Move issues to backlog (remove from sprint)",
    )
    backlog_parser.add_argument(
        "issues",
        nargs="+",
        help="Issue keys to move to backlog (e.g. PROJ-123)",
    )

    enrich_parser = subparsers.add_parser(
        "enrich-team",
        help="Populate team-members.yaml from GitHub org + optional LDAP + Slack",
    )
    enrich_parser.add_argument(
        "--org",
        default=None,
        help="GitHub org to enumerate (default: github.org from config)",
    )
    enrich_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print enriched YAML without writing",
    )

    history_parser = subparsers.add_parser(
        "standup-history",
        help="Fetch recent standup draft/published pairs for edit learning",
    )
    history_parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Days to look back (default: 14)",
    )

    config_parser = subparsers.add_parser(
        "config",
        help="Inspect or validate standup configuration.",
    )
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    config_subparsers.add_parser(
        "show",
        help="Print effective configuration as JSON (without requiring repo paths to exist).",
    )
    config_subparsers.add_parser(
        "validate",
        help="Validate configuration file and repository paths.",
    )
    config_parser.set_defaults(_config_parser=config_parser)

    return parser


def subcommand_names(parser: argparse.ArgumentParser) -> set[str]:
    """Return the set of top-level subcommand names registered on *parser*."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices.keys())
    return set()


def _handle_config(args: argparse.Namespace) -> None:
    subcmd = getattr(args, "config_command", None)
    if subcmd is None:
        config_parser: argparse.ArgumentParser | None = getattr(args, "_config_parser", None)
        if config_parser is not None:
            config_parser.print_help(sys.stderr)
        sys.exit(2)

    if subcmd == "show":
        cfg = load_config(require_repos=False)
        raw = dataclasses.asdict(cfg)
        raw["config_dir"] = str(config_dir())
        cleaned = _strip_secret_keys(raw)
        sys.stdout.write(json.dumps(cleaned, indent=2) + "\n")
        return

    if subcmd == "validate":
        load_config(require_repos=True)
        sys.stdout.write("ok\n")
        return


def main(argv: list[str] | None = None) -> None:
    """Entry point for the standup-tool console script."""
    parser = build_parser()
    args = parser.parse_args(argv)
    command = getattr(args, "command", None)
    if command is None:
        parser.print_help(sys.stderr)
        sys.exit(2)

    logging.basicConfig(
        level=logging.WARNING, format="%(levelname)s: %(message)s", stream=sys.stderr
    )

    try:
        if command == "config":
            _handle_config(args)
            return

        cfg = load_config()
        if command == "collect":
            _collect(args, cfg)
        elif command == "digest":
            _digest(args, cfg)
        elif command == "post-standup":
            _post_standup(args, cfg)
        elif command == "backlog":
            _backlog(args, cfg)
        elif command == "standup-history":
            _standup_history(args, cfg)
        elif command == "enrich-team":
            from standup_tool.enrich_team import run_enrichment

            run_enrichment(cfg, org=args.org, dry_run=args.dry_run)
    except ConfigError as exc:
        sys.stderr.write(f"{exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
