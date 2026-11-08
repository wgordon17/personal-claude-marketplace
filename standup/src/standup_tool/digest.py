"""Entity-linked work-unit grouping for morning digest.

Reads collector JSON and produces a structured digest with entity-linked
work units, relevance scoring, and staleness tagging.  Designed to replace
the raw-JSON-dump approach with deterministic cross-source correlation
before handing data to synthesis agents.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime
from pathlib import Path

import yaml

_JIRA_KEY_RE = re.compile(r"\b[A-Z]+-\d+\b")
_PR_REF_RE = re.compile(r"(?:[\w.-]+(?:/[\w.-]+)?)?#\d+")
_BRANCH_RE = re.compile(r"\b[A-Z]+-\d+-[a-z0-9][-a-z0-9]*\b")


def extract_entities(text: str) -> set[str]:
    """Find Jira keys, PR refs, and branch names in *text*."""
    if not text:
        return set()
    entities: set[str] = set()
    entities.update(_JIRA_KEY_RE.findall(text))
    entities.update(_PR_REF_RE.findall(text))
    for branch in _BRANCH_RE.findall(text):
        entities.add(branch)
        jira_match = _JIRA_KEY_RE.match(branch)
        if jira_match:
            entities.add(jira_match.group())
    return entities


def _scan_fields(item: dict, fields: list[str]) -> set[str]:
    """Extract entities from multiple string fields of *item*."""
    entities: set[str] = set()
    for field in fields:
        val = item.get(field)
        if isinstance(val, str):
            entities.update(extract_entities(val))
        elif isinstance(val, list):
            for entry in val:
                if isinstance(entry, dict):
                    for v in entry.values():
                        if isinstance(v, str):
                            entities.update(extract_entities(v))
                elif isinstance(entry, str):
                    entities.update(extract_entities(entry))
    return entities


_SOURCE_FIELDS: dict[str, tuple[str, list[str]]] = {
    "git_commits": ("git", ["subject"]),
    "prs": ("pr", ["title", "head_branch"]),
    "jira_activity": ("jira", ["key", "summary"]),
    "calendar_events": ("calendar", ["summary", "description"]),
    "emails": ("email", ["subject", "body_snippet"]),
    "memory_activity": ("memory_activity", ["title", "path"]),
    "conversations": ("conversations", ["context"]),
}


def _is_high_signal_slack(item: dict, self_dm: str) -> bool:
    """Return True if *item* is a high-signal Slack message."""
    source = item.get("source", "")
    if source == "participation":
        return True
    if source == "dm":
        if not self_dm:
            return True
        channel = item.get("channel", "")
        return self_dm not in channel
    if source == "mention":
        msgs = item.get("messages", [])
        return len(msgs) > 1
    return False


def _filter_slack(items: list[dict], self_dm: str) -> list[dict]:
    """Keep only high-signal Slack items."""
    return [item for item in items if _is_high_signal_slack(item, self_dm)]


def _extract_timestamp(item: dict) -> datetime | None:
    """Try to parse an ISO-8601 timestamp from common fields."""
    for field in (
        "timestamp",
        "date",
        "modified",
        "ts",
        "created_at",
        "updated_at",
        "start_time",
        "start",
    ):
        val = item.get(field)
        if not val:
            continue
        if isinstance(val, int | float):
            try:
                return datetime.fromtimestamp(val, tz=UTC)
            except (OSError, ValueError):
                continue
        if isinstance(val, str):
            for fmt in (
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d",
            ):
                try:
                    dt = datetime.strptime(val, fmt)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    return dt
                except ValueError:
                    continue
    return None


def group_into_work_units(parsed_json: dict, self_dm: str = "") -> tuple[list[dict], list[dict]]:
    """Group collector items into entity-keyed work units.

    Returns ``(work_units_list, uncorrelated_items)``.
    """
    units: dict[str, dict] = {}
    uncorrelated: list[dict] = []

    def _ensure_unit(entity: str) -> dict:
        if entity not in units:
            units[entity] = {
                "entity": entity,
                "sources": [],
                "items": [],
            }
        return units[entity]

    for source_key, (source_label, fields) in _SOURCE_FIELDS.items():
        items = parsed_json.get(source_key)
        if items is None:
            continue
        if isinstance(items, dict):
            flat: list[dict] = []
            for v in items.values():
                if isinstance(v, list):
                    flat.extend(v)
            items = flat
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            entities = _scan_fields(item, fields)
            tagged = {**item, "_source": source_label}
            if entities:
                for ent in entities:
                    wu = _ensure_unit(ent)
                    wu["items"].append(tagged)
                    if source_label not in wu["sources"]:
                        wu["sources"].append(source_label)
            else:
                uncorrelated.append(tagged)

    slack_items = parsed_json.get("slack")
    if isinstance(slack_items, list):
        filtered = _filter_slack(slack_items, self_dm)
        for item in filtered:
            if not isinstance(item, dict):
                continue
            text_entities: set[str] = set()
            for msg in item.get("messages", []):
                if isinstance(msg, dict):
                    text_entities.update(extract_entities(msg.get("text", "")))
            text_entities.update(extract_entities(item.get("channel", "")))
            tagged = {**item, "_source": "slack"}
            if text_entities:
                for ent in text_entities:
                    wu = _ensure_unit(ent)
                    wu["items"].append(tagged)
                    if "slack" not in wu["sources"]:
                        wu["sources"].append("slack")
            else:
                uncorrelated.append(tagged)

    # Drive doc comments
    drive_docs = parsed_json.get("drive_docs")
    if isinstance(drive_docs, list):
        for doc in drive_docs:
            if not isinstance(doc, dict):
                continue
            entities = extract_entities(doc.get("file_name", ""))
            comments = doc.get("comments", [])
            if isinstance(comments, list):
                for c in comments:
                    if isinstance(c, dict):
                        entities.update(extract_entities(c.get("content", "")))
            tagged = {**doc, "_source": "drive"}
            if entities:
                for ent in entities:
                    wu = _ensure_unit(ent)
                    wu["items"].append(tagged)
                    if "drive" not in wu["sources"]:
                        wu["sources"].append("drive")
            else:
                uncorrelated.append(tagged)

    return list(units.values()), uncorrelated


def _compute_relevance(wu: dict, team_github_set: set[str]) -> int:
    """Score a work unit by source diversity and signal strength."""
    score = 1
    n_sources = len(wu.get("sources", []))
    if n_sources > 1:
        score += n_sources - 1
    if "slack" in wu.get("sources", []) or "calendar" in wu.get("sources", []):
        score += 2
    # team member involvement
    for item in wu.get("items", []):
        author = item.get("author", item.get("user", item.get("assignee", "")))
        if isinstance(author, str) and author in team_github_set:
            score += 1
            break
    if any(
        item.get("state") not in ("merged", "closed", None)
        for item in wu.get("items", [])
        if item.get("_source") == "pr"
    ):
        score += 1
    return score


def _compute_staleness(wu: dict, ref_date: date) -> tuple[bool, int | None]:
    """Return (is_stale, days_since_last) for a work unit."""
    latest: datetime | None = None
    for item in wu.get("items", []):
        ts = _extract_timestamp(item)
        if ts and (latest is None or ts > latest):
            latest = ts
    if latest is None:
        return False, None
    delta = (datetime(ref_date.year, ref_date.month, ref_date.day, tzinfo=UTC) - latest).days
    return delta > 3, delta


def _summarize_git(items: list[dict]) -> str:
    paths = set()
    for item in items:
        fp = item.get("file_path", item.get("subject", ""))
        if "/" in fp:
            paths.add(fp.rsplit("/", 1)[0] + "/")
    prefix = ""
    if paths:
        common = min(paths, key=len)
        prefix = f" to {common}" if len(paths) <= 3 else ""
    return f"{len(items)} commits{prefix}"


def _summarize_pr(item: dict) -> dict:
    return {
        "number": item.get("number"),
        "repo": item.get("repo", item.get("repository", "")),
        "state": item.get("state", ""),
        "ci": item.get("ci", item.get("checks", "")),
        "reviewers": item.get("reviewers", []),
        "role": item.get("role", ""),
    }


def _summarize_jira(item: dict) -> dict:
    return {
        "key": item.get("key", ""),
        "status": item.get("status", ""),
        "summary": item.get("summary", ""),
    }


def _summarize_slack(item: dict) -> dict:
    channel = item.get("channel", "")
    msgs = item.get("messages", [])
    text = ""
    if msgs and isinstance(msgs, list) and isinstance(msgs[0], dict):
        text = msgs[0].get("text", "")[:100]
    thread_replies = item.get("thread_replies", item.get("reply_count", 0))
    return {"channel": channel, "text": text, "thread_replies": thread_replies}


def _summarize_calendar(item: dict) -> dict:
    return {
        "summary": item.get("summary", ""),
        "start_time": item.get("start_time", item.get("start", "")),
    }


def _summarize_email(item: dict) -> dict:
    return {"subject": item.get("subject", "")[:100]}


def _summarize_drive(item: dict) -> dict:
    comments = item.get("comments", [])
    count = len(comments) if isinstance(comments, list) else 0
    return {"file_name": item.get("file_name", ""), "comments_count": count}


def _summarize_memory(item: dict) -> dict:
    return {
        "repo": item.get("repo", ""),
        "category": item.get("category", ""),
        "title": item.get("title", "")[:100],
        "path": item.get("path", ""),
    }


def _summarize_conversation(item: dict) -> dict:
    context = item.get("context", "")
    title = ""
    first_line = context.split("\n")[0].strip() if context else ""
    if first_line.startswith("# "):
        title = first_line.lstrip("# ").strip()
    elif first_line.startswith("**") and "**" in first_line[2:]:
        title = first_line.strip("* ")
    lines = context.split("\n")
    summary_lines = []
    total_len = 0
    for line in lines[1:40]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        candidate_len = total_len + len(line) + (1 if summary_lines else 0)
        if candidate_len > 400:
            if not summary_lines:
                summary_lines.append(line[:400])
            break
        summary_lines.append(line)
        total_len = candidate_len
    tail_lines = []
    tail_len = 0
    for line in reversed(lines[-40:]):
        line = line.strip()
        if (
            not line
            or line.startswith("#")
            or line.startswith("```")
            or line.startswith("- [")
            or line.startswith("Sources:")
            or line.startswith("|")
        ):
            continue
        candidate_len = tail_len + len(line) + (1 if tail_lines else 0)
        if candidate_len > 400:
            if not tail_lines:
                tail_lines.append(line[:400])
            break
        tail_lines.insert(0, line)
        tail_len = candidate_len
    return {
        "conversation_id": item.get("conversation_id", ""),
        "date": item.get("date", ""),
        "title": title,
        "summary": " ".join(summary_lines),
        "outcome": " ".join(tail_lines),
        "context": context,
    }


def _conversation_cache_key(item: dict) -> str:
    """Stable dedup key for a conversation item.

    Prefers ``conversation_id`` from the collector; falls back to a content
    hash for older data that predates that field (see sample fixtures).
    """
    conv_id = item.get("conversation_id")
    if conv_id:
        return str(conv_id)
    context = item.get("context", "")
    return "sha1:" + hashlib.sha1(context.encode("utf-8")).hexdigest()[:12]


def _conversation_ref(record: dict) -> dict:
    """Lightweight embed of a conversation record for a work unit.

    Omits ``context`` -- the full text lives once in the top-level
    ``conversation_registry``, keyed by ``conversation_id``, so embedding
    this reference in N work units costs O(1) bytes per work unit instead
    of O(len(context)).
    """
    return {
        "conversation_id": record.get("conversation_id", ""),
        "date": record.get("date", ""),
        "title": record.get("title", ""),
        "summary": record.get("summary", ""),
        "outcome": record.get("outcome", ""),
    }


def _build_conversation_registry(
    work_units: list[dict], uncorrelated: list[dict]
) -> tuple[list[dict], dict[str, dict]]:
    """Deduplicate conversation items by ``conversation_id`` into one full record each.

    A single conversation can mention many entities and fan out into many
    work units during grouping; without this step, its full (possibly
    multi-KB) text would be re-serialized once per entity. This builds each
    unique conversation's full record (including ``context``) exactly once,
    and tracks every entity that references it in ``linked_entities`` --
    strictly more information than embedding it N times ever provided,
    since today there is no way to tell from one work unit that the same
    conversation also touched others.

    Returns ``(registry, cache)`` where ``registry`` is the top-level,
    full-fidelity list (for the digest's ``conversation_registry`` key) and
    ``cache`` maps dedup key -> full record, for building lightweight refs
    elsewhere without recomputing ``_summarize_conversation``.
    """
    cache: dict[str, dict] = {}
    linked: dict[str, list[str]] = {}

    def _get_or_build(item: dict) -> str:
        key = _conversation_cache_key(item)
        if key not in cache:
            record = _summarize_conversation(item)
            record["conversation_id"] = record.get("conversation_id") or key
            cache[key] = record
        return key

    for wu in work_units:
        for item in wu.get("items", []):
            if item.get("_source") != "conversations":
                continue
            key = _get_or_build(item)
            entities = linked.setdefault(key, [])
            if wu["entity"] not in entities:
                entities.append(wu["entity"])

    for item in uncorrelated:
        if item.get("_source") != "conversations":
            continue
        key = _get_or_build(item)
        linked.setdefault(key, [])

    registry = [{**record, "linked_entities": linked.get(key, [])} for key, record in cache.items()]
    return registry, cache


_SUMMARIZERS = {
    "git": ("git", _summarize_git),
    "pr": ("pr", _summarize_pr),
    "jira": ("jira", _summarize_jira),
    "slack": ("slack", _summarize_slack),
    "calendar": ("calendar", _summarize_calendar),
    "email": ("emails", _summarize_email),
    "drive": ("drive_docs", _summarize_drive),
    "memory_activity": ("memory_activity", _summarize_memory),
    "conversations": ("conversations", _summarize_conversation),
}


def _build_wu_output(wu: dict, conversation_cache: dict[str, dict]) -> dict:
    """Convert internal work unit to output schema.

    ``conversation_cache`` (from ``_build_conversation_registry``) supplies
    already-summarized conversation records so this never re-embeds a full
    ``context`` blob -- only the lightweight reference.
    """
    out: dict = {
        "entity": wu["entity"],
        "relevance_score": wu.get("relevance_score", 0),
        "stale": wu.get("stale", False),
        "sources": wu["sources"],
    }
    items_by_source: dict[str, list[dict]] = {}
    for item in wu.get("items", []):
        src = item.get("_source", "unknown")
        items_by_source.setdefault(src, []).append(item)

    for source_label, (out_key, summarizer) in _SUMMARIZERS.items():
        src_items = items_by_source.get(source_label, [])
        if not src_items:
            out[out_key] = None
            continue
        if source_label == "git":
            out[out_key] = summarizer(src_items)
        elif source_label in ("pr", "jira"):
            out[out_key] = summarizer(src_items[0])
        elif source_label == "slack":
            out[out_key] = [summarizer(i) for i in src_items]
        elif source_label == "conversations":
            refs = [
                _conversation_ref(
                    conversation_cache.get(_conversation_cache_key(i)) or _summarize_conversation(i)
                )
                for i in src_items
            ]
            out[out_key] = refs[0] if len(refs) == 1 else refs
        elif source_label in ("calendar", "email", "drive", "memory_activity"):
            if len(src_items) == 1:
                out[out_key] = summarizer(src_items[0])
            else:
                out[out_key] = [summarizer(i) for i in src_items]
    return out


def load_team(team_file: Path) -> list[dict]:
    """Load team members from ``team-members.yaml`` in the config directory."""
    if not team_file.exists():
        return []
    with open(team_file) as f:
        data = yaml.safe_load(f)
    if isinstance(data, list):
        return data
    return []


def build_digest(
    parsed_json: dict,
    team: list[dict],
    since_date: date,
    until_date: date,
    self_dm: str = "",
) -> dict:
    """Build a structured digest from collector JSON.

    Returns a dict matching the digest output schema.
    """
    team_github = {m.get("github", "") for m in team if m.get("github")}

    work_units, uncorrelated = group_into_work_units(parsed_json, self_dm=self_dm)

    for wu in work_units:
        wu["relevance_score"] = _compute_relevance(wu, team_github)
        stale, days = _compute_staleness(wu, until_date)
        wu["stale"] = stale
        wu["days_since_last_activity"] = days

    work_units.sort(key=lambda w: w.get("relevance_score", 0), reverse=True)

    conversation_registry, conversation_cache = _build_conversation_registry(
        work_units, uncorrelated
    )

    window_days = (until_date - since_date).days + 1
    if window_days <= 1:
        window_type = "weekday"
    elif window_days <= 3:
        window_type = "weekend"
    else:
        window_type = "extended"

    uncorrelated_out = []
    for item in uncorrelated:
        entry: dict = {"source": item.get("_source", "unknown")}
        if item.get("channel"):
            entry["channel"] = item["channel"]
        msgs = item.get("messages", [])
        if item.get("_source") == "conversations":
            key = _conversation_cache_key(item)
            record = conversation_cache.get(key) or _summarize_conversation(item)
            entry["conversation_id"] = record.get("conversation_id", key)
            entry["text"] = record.get("title") or record.get("summary", "")[:200]
        elif msgs and isinstance(msgs, list) and isinstance(msgs[0], dict):
            entry["text"] = msgs[0].get("text", "")[:200]
        elif item.get("text"):
            entry["text"] = str(item["text"])[:200]
        elif item.get("subject"):
            entry["text"] = str(item["subject"])[:200]
        elif item.get("title"):
            entry["text"] = str(item["title"])[:200]
        elif item.get("context"):
            entry["text"] = str(item["context"])[:200]
        ts = _extract_timestamp(item)
        if ts:
            entry["timestamp"] = ts.isoformat()
        uncorrelated_out.append(entry)

    return {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "window": {
            "since": since_date.isoformat(),
            "until": until_date.isoformat(),
            "days": window_days,
            "type": window_type,
        },
        "work_units": [_build_wu_output(wu, conversation_cache) for wu in work_units],
        "uncorrelated": uncorrelated_out,
        "team": [{"name": m.get("name", ""), "github": m.get("github", "")} for m in team],
        "conversation_registry": conversation_registry,
    }
