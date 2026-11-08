"""Collect and compress recent Claude Code conversation sessions.

Uses Haiku via Vertex AI as a lossless-ish compressor — preserves all
decisions, findings, references, and questions while stripping tool
output, debugging logs, and filler. The lead model does its own
analysis and scope classification with cross-referenced data.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from standup_tool.config import redact_secrets

if TYPE_CHECKING:
    from standup_tool.workspace import RepoInfo

logger = logging.getLogger(__name__)

_CLAUDE_PROJECTS_DIR = Path("~/.claude/projects").expanduser()
_OMP_SESSIONS_DIR = Path("~/.omp/agent/sessions").expanduser()
_CHUNK_CHARS = 24000
_COMPRESS_THRESHOLD_CHARS = 16000
_MAX_MESSAGE_CHARS = 2000

# We compress conversations concurrently to avoid stalling the run.
_VERTEX_TIMEOUT = 60.0  # seconds, per Vertex/Haiku compression request


_COMPRESS_PROMPT = (
    "Compress this Claude Code conversation excerpt. Preserve ALL of the following:\n"
    "- Decisions made and their rationale\n"
    "- Key technical findings and conclusions\n"
    "- Questions asked by the user and answers given\n"
    "- Jira keys, PR numbers, file paths, repo names mentioned\n"
    "- Problems identified and root causes found\n"
    "- Open questions or unresolved disagreements\n\n"
    "Remove:\n"
    "- Tool call syntax, raw command output, and file listings\n"
    "- Repeated content, filler, and pleasantries\n"
    "- Step-by-step debugging traces (keep only the conclusion)\n"
    "- Code blocks (describe what changed, not the code itself)\n\n"
    "Output a compressed narrative at ~25% of the original length. "
    "Write in third person ('the user', 'the assistant'). "
    "If the excerpt contains no substantive technical content "
    "(only greetings, trivial exchanges, or meta-discussion), "
    "reply with exactly: EMPTY\n\n"
)


def build_repo_keys(repos: Sequence[RepoInfo]) -> dict[str, str]:
    """Map each discovered repo's exact dash-flattened canonical path key to repo.name."""
    return {str(r.path.resolve()).replace("/", "-"): r.name for r in repos}


def _matches_project(
    dir_name: str,
    repo_keys: Mapping[str, str] | None = None,
    patterns: Sequence[str] = (),
) -> list[str]:
    """Match a project directory name against exact canonical repo keys and explicit patterns."""
    matched: list[str] = []
    if repo_keys and dir_name in repo_keys:
        matched.append(repo_keys[dir_name])
    lower_dir = dir_name.lower()
    for pat in patterns:
        if pat and pat.lower() in lower_dir and pat not in matched:
            matched.append(pat)
    return matched


def _extract_repos(
    dir_name: str,
    repo_keys: Mapping[str, str] | None = None,
    patterns: Sequence[str] = (),
) -> list[str]:
    """Return matched project/repo names for *dir_name* via `_matches_project`."""
    return _matches_project(dir_name, repo_keys, patterns)


def _is_fatal_vertex_error(exc: BaseException) -> bool:
    """Return True if *exc* is an authentication, HTTP 401/403, connection, or timeout error."""
    if isinstance(exc, PermissionError | ConnectionError | TimeoutError):
        return True
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return True
    cls_name = type(exc).__name__.lower()
    if (
        "auth" in cls_name
        or "permission" in cls_name
        or "timeout" in cls_name
        or "connection" in cls_name
    ):
        return True
    msg = str(exc).lower()
    return any(
        tok in msg
        for tok in (
            "401",
            "403",
            "unauthorized",
            "forbidden",
            "unauthenticated",
            "timed out",
            "timeout",
            "connection",
        )
    )


def _find_conversations(
    since_date: date,
    until_date: date,
    repo_keys: Mapping[str, str] | None = None,
    patterns: Sequence[str] = (),
) -> list[dict]:
    """Find JSONL conversation files modified within the date range for matched projects.

    Returns list of {project_dir, file_path, mtime, repos_touched} dicts.
    """
    if not _CLAUDE_PROJECTS_DIR.is_dir():
        raise FileNotFoundError(f"Claude projects directory not found: {_CLAUDE_PROJECTS_DIR}")

    since_ts = datetime(since_date.year, since_date.month, since_date.day).timestamp()
    until_ts = datetime(until_date.year, until_date.month, until_date.day, 23, 59, 59).timestamp()

    conversations = []

    sources = [(d, d.glob("*.jsonl")) for d in _CLAUDE_PROJECTS_DIR.iterdir()] + [
        (d, d.glob("*.jsonl"))
        for d in (_OMP_SESSIONS_DIR.iterdir() if _OMP_SESSIONS_DIR.is_dir() else [])
    ]

    for project_dir, jsonl_files in sources:
        if not project_dir.is_dir():
            continue
        matched_repos = _matches_project(project_dir.name, repo_keys, patterns)
        if not matched_repos:
            continue
        for jsonl_file in jsonl_files:
            try:
                mtime = jsonl_file.stat().st_mtime
            # tolerant: skip single JSONL session file whose stat call fails
            except OSError as exc:
                logger.debug("Skipping unreadable session file %s: %s", jsonl_file, exc)
                continue
            if since_ts <= mtime <= until_ts:
                conversations.append(
                    {
                        "project_dir": project_dir.name,
                        "file_path": jsonl_file,
                        "mtime": mtime,
                        "repos_touched": matched_repos,
                    }
                )

    conversations.sort(key=lambda c: c["mtime"], reverse=True)
    return conversations


def _extract_context(jsonl_path: Path) -> str:
    """Extract user prompts and assistant text from a conversation JSONL.

    Skips meta messages, tool results, and progress events.
    No hard truncation — returns all substantive text.
    Individual messages are capped at _MAX_MESSAGE_CHARS to skip
    huge single outputs while preserving conversation flow.
    """
    parts: list[str] = []

    try:
        with open(jsonl_path) as f:
            for line in f:
                # Cheap reject: only user/assistant lines can contribute, and most of a
                # transcript is large tool output that is not worth parsing.
                if '"user"' not in line and '"assistant"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                # tolerant: skip malformed JSON line inside session transcript
                except json.JSONDecodeError as exc:
                    logger.debug("Skipping malformed JSON line in %s: %s", jsonl_path, exc)
                    continue

                msg_type = obj.get("type")
                if msg_type not in ("user", "assistant"):
                    continue
                if obj.get("isMeta"):
                    continue
                if obj.get("toolUseResult"):
                    continue

                message = obj.get("message", {})
                if not isinstance(message, dict):
                    continue

                role = message.get("role", "")
                content = message.get("content", [])
                if not isinstance(content, list):
                    continue

                text_parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "").strip()
                        if text:
                            text_parts.append(text)

                if not text_parts:
                    continue

                text = " ".join(text_parts)
                prefix = "User" if role == "user" else "Assistant"
                entry = f"{prefix}: {text[:_MAX_MESSAGE_CHARS]}"
                parts.append(entry)
    # tolerant: skip unreadable session transcript file and return extracted lines so far
    except OSError:
        logger.debug("Failed to read conversation %s", jsonl_path, exc_info=True)

    return redact_secrets("\n".join(parts))


async def _compress(client, context: str, sem: asyncio.Semaphore) -> str:
    """Use Haiku to compress conversation text, preserving semantic content.

    Redacts secrets before any text is returned or sent to Vertex AI.
    For short conversations (< _COMPRESS_THRESHOLD_CHARS), returns as-is.
    For longer ones, chunks and compresses via map-reduce.
    """
    sanitized = redact_secrets(context)
    if len(sanitized) < _COMPRESS_THRESHOLD_CHARS:
        return sanitized

    chunks = [sanitized[i : i + _CHUNK_CHARS] for i in range(0, len(sanitized), _CHUNK_CHARS)]

    compressed_parts = []

    async def process_chunk(chunk: str) -> str:
        async with sem:
            response = await client.messages.create(
                model="claude-haiku-4-5@20251001",
                max_tokens=2000,
                messages=[
                    {
                        "role": "user",
                        "content": _COMPRESS_PROMPT + f"<excerpt>\n{chunk}\n</excerpt>",
                    }
                ],
            )
            for block in response.content:
                if block.type == "text":
                    text = block.text.strip()
                    if text != "EMPTY":
                        return text
            return ""

    results = await asyncio.gather(*(process_chunk(c) for c in chunks))
    for res in results:
        if res:
            compressed_parts.append(res)

    return "\n\n".join(compressed_parts)


def _project_name_from_dir(dir_name: str) -> str:
    """Convert Claude Code project dir name to a readable project path.

    Dir names use dashes for path separators: -Users-alice-src-widgets
    """
    if dir_name.startswith("-"):
        return "/" + dir_name[1:].replace("-", "/")
    return dir_name


def collect(
    since_date: date,
    until_date: date,
    repo_keys: Mapping[str, str] | None = None,
    patterns: Sequence[str] = (),
    *,
    vertex_project: str | None = None,
    region: str | None = None,
) -> list[dict]:
    """Collect and compress recent conversations matching configured repos or patterns."""
    return asyncio.run(
        _async_collect(
            since_date,
            until_date,
            repo_keys=repo_keys,
            patterns=patterns,
            vertex_project=vertex_project,
            region=region,
        )
    )


async def _async_collect(
    since_date: date,
    until_date: date,
    repo_keys: Mapping[str, str] | None = None,
    patterns: Sequence[str] = (),
    *,
    vertex_project: str | None = None,
    region: str | None = None,
) -> list[dict]:
    """Return compressed context from recent Claude Code and OMP conversations.

    Uses Vertex AI Haiku as a compressor for long conversations.
    Short conversations are returned as-is (no LLM call).
    Returns empty list if Vertex project is not configured.

    Each dict: {project, conversation_id, date, context, repos_touched}
    """
    project_id = (
        vertex_project
        or os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
        or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    )
    if not project_id:
        raise RuntimeError(
            "Vertex project not set; configure collectors.conversations.vertex_project "
            "or set ANTHROPIC_VERTEX_PROJECT_ID / GOOGLE_CLOUD_PROJECT"
        )

    resolved_region = region or os.environ.get("CLOUD_ML_REGION", "us-east5")

    try:
        from anthropic import AsyncAnthropicVertex
    # fatal
    except ImportError:
        raise

    conversations = _find_conversations(since_date, until_date, repo_keys, patterns)
    if not conversations:
        return []

    client = AsyncAnthropicVertex(
        project_id=project_id,
        region=resolved_region,
        timeout=_VERTEX_TIMEOUT,
    )
    sem = asyncio.Semaphore(10)  # Max concurrent Vertex requests

    results: list[dict] = []

    async def process_conv(conv: dict) -> dict | None:
        repos_touched = conv.get("repos_touched") or _extract_repos(
            conv["project_dir"], repo_keys, patterns
        )
        if not repos_touched:
            return None

        raw_context = _extract_context(conv["file_path"])
        if not raw_context:
            return None

        project = _project_name_from_dir(conv["project_dir"])
        conversation_id = conv["file_path"].stem

        try:
            context = await _compress(client, raw_context, sem)
        # tolerant: fall back to redacted truncated transcript on single-session error
        except Exception as exc:
            if _is_fatal_vertex_error(exc):
                raise
            logger.debug("Failed to compress conversation %s: %s", conversation_id, exc)
            logger.warning("Failed to compress conversation %s: %s", conversation_id, exc)
            context = raw_context[:_COMPRESS_THRESHOLD_CHARS]
        else:
            context = redact_secrets(context)
        if not context.strip():
            return None

        conv_date = datetime.fromtimestamp(conv["mtime"]).strftime("%Y-%m-%d")

        return {
            "project": project,
            "conversation_id": conversation_id,
            "date": conv_date,
            "context": context,
            "repos_touched": repos_touched,
        }

    tasks = [process_conv(conv) for conv in conversations]
    completed = await asyncio.gather(*tasks)
    for res in completed:
        if res:
            results.append(res)

    return results
