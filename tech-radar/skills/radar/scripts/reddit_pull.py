# /// script
# requires-python = ">=3.13"
# dependencies = ["wafer-py==0.7.7"]
# [tool.uv]
# exclude-newer = "2026-10-20T00:00:00Z"
# ///
"""Isolated PEP 723 Reddit top-posts puller using wafer-py (Spec 01, Spec 03, Spec 06 T5)."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import time
import urllib.parse
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO

sys.dont_write_bytecode = True

ALLOWED_REDDIT_HOSTS: frozenset[str] = frozenset(
    {
        "www.reddit.com",
        "api.reddit.com",
        "oauth.reddit.com",
    }
)

_REDDIT_SESSION_GATE_BACKOFF: float = 5.0
_REDDIT_MAX_RATE_LIMIT_SLEEP: float = 60.0
_REDDIT_DEADLINE_SECONDS: float = 540.0
_SKIP_REASONS: frozenset[str] = frozenset({"private", "quarantined", "banned", "gated"})
_SUBREDDIT_RE = re.compile(r"^[A-Za-z0-9_]{1,50}$")

DEFAULT_HEADERS: dict[str, str] = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": "https://www.reddit.com/",
}


def is_allowed_reddit_host(url: str) -> bool:
    """Return True iff `url` uses https:// on an allowlisted Reddit host with no userinfo."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return False
    if parsed.scheme.lower() != "https":
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    host = (parsed.hostname or "").lower()
    return host in ALLOWED_REDDIT_HOSTS


def _load_wafer() -> Any:
    import wafer  # pyright: ignore[reportMissingImports]

    return wafer


def _ensure_wafer_cache_dir(state_dir: Path) -> Path:
    cache_dir = state_dir / "wafer_cache"
    cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        os.chmod(cache_dir, 0o700)
    return cache_dir


def _chmod_cache_files(cache_dir: Path) -> None:
    if not cache_dir.is_dir():
        return
    for entry in cache_dir.iterdir():
        if entry.is_file() and not entry.is_symlink():
            with contextlib.suppress(OSError):
                os.chmod(entry, 0o600)


def _extract_response_json(resp: Any) -> dict[str, Any] | None:
    json_fn = getattr(resp, "json", None)
    if callable(json_fn):
        try:
            obj = json_fn()
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    raw_content = getattr(resp, "content", None)
    if isinstance(raw_content, bytes):
        try:
            obj = json.loads(raw_content.decode("utf-8", errors="ignore"))
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    raw_text = getattr(resp, "text", None)
    if isinstance(raw_text, str):
        try:
            obj = json.loads(raw_text)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    return None


def _extract_retry_after(obj_or_exc: Any) -> float:
    retry_val = getattr(obj_or_exc, "retry_after", None)
    if isinstance(retry_val, int | float) and retry_val > 0:
        return min(float(retry_val), _REDDIT_MAX_RATE_LIMIT_SLEEP)
    headers = getattr(obj_or_exc, "headers", None)
    if isinstance(headers, dict):
        for k, v in headers.items():
            if str(k).lower() == "retry-after":
                try:
                    parsed = float(v)
                    if parsed > 0:
                        return min(parsed, _REDDIT_MAX_RATE_LIMIT_SLEEP)
                except (TypeError, ValueError):
                    pass
    return _REDDIT_MAX_RATE_LIMIT_SLEEP


def _classify_failure(resp_or_exc: Any) -> tuple[str, float]:
    """Classify a failed request or exception.

    Returns `(action, sleep_seconds)` where `action` is one of:
    - `"skip"`: 3xx redirect or content-state 403/404 (skip subreddit without backoff)
    - `"retry_rate_limit"`: 429 or RateLimited (sleep `min(retry_after or 60, 60)`, retry once)
    - `"retry_gate"`: ChallengeDetected(challenge_type='reddit') or 403 reddit session gate
    - `"abort"`: opaque 403 block or unrecoverable error (stop iterating remaining subreddits)
    """
    exc_name = type(resp_or_exc).__name__
    status = int(getattr(resp_or_exc, "status_code", getattr(resp_or_exc, "status", 0)) or 0)
    challenge_type = str(getattr(resp_or_exc, "challenge_type", "") or "").strip().lower()
    reason = str(getattr(resp_or_exc, "reason", "") or "").strip().lower()

    body_obj = _extract_response_json(resp_or_exc)
    if isinstance(body_obj, dict):
        if not reason and body_obj.get("reason"):
            reason = str(body_obj["reason"]).strip().lower()
        if not challenge_type and body_obj.get("challenge_type"):
            challenge_type = str(body_obj["challenge_type"]).strip().lower()

    if exc_name == "RateLimited" or status == 429:
        return "retry_rate_limit", _extract_retry_after(resp_or_exc)

    if exc_name == "ChallengeDetected":
        if challenge_type == "reddit":
            return "retry_gate", _REDDIT_SESSION_GATE_BACKOFF
        return "abort", 0.0

    if 300 <= status < 400 or status == 404:
        return "skip", 0.0

    if status == 403:
        if reason in _SKIP_REASONS:
            return "skip", 0.0
        if challenge_type == "reddit":
            return "retry_gate", _REDDIT_SESSION_GATE_BACKOFF
        return "abort", 0.0

    return "abort", 0.0


def _extract_posts(sub: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    data_obj = payload.get("data")
    if not isinstance(data_obj, dict):
        return []
    children = data_obj.get("children")
    if not isinstance(children, list):
        return []

    posts: list[dict[str, Any]] = []
    for child in children:
        if not isinstance(child, dict):
            continue
        post = child.get("data")
        if not isinstance(post, dict):
            continue
        post_id = str(post.get("id") or post.get("name") or "").strip()
        if not post_id:
            continue
        permalink = str(post.get("permalink") or f"/r/{sub}/comments/{post_id}/").strip()
        if permalink.startswith("/"):
            permalink_url = f"https://www.reddit.com{permalink}"
        else:
            permalink_url = permalink
        url = str(post.get("url") or permalink_url).strip()
        selftext = str(post.get("selftext") or "")[:1000]
        posts.append(
            {
                "subreddit": str(post.get("subreddit") or sub),
                "id": post_id,
                "title": str(post.get("title") or post_id),
                "url": url,
                "permalink": permalink,
                "score": int(post.get("score") or post.get("ups") or 0),
                "num_comments": int(post.get("num_comments") or 0),
                "created_utc": post.get("created_utc"),
                "selftext": selftext,
            }
        )
    return posts


def pull_subreddits(
    subreddits: Sequence[str],
    *,
    time_filter: str = "day",
    limit: int = 25,
    state_dir: Path | str | None = None,
    session_factory: Callable[..., Any] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
    stdout: TextIO | None = None,
) -> list[dict[str, Any]]:
    """Fetch top posts for `subreddits` and write one JSON object per line to `stdout`."""
    out_stream = stdout if stdout is not None else sys.stdout
    raw_state = (
        Path(state_dir).expanduser()
        if state_dir is not None
        else Path(
            os.environ.get("TECH_RADAR_STATE") or Path.home() / ".local" / "share" / "tech-radar"
        ).expanduser()
    )
    cache_dir = _ensure_wafer_cache_dir(raw_state)

    if session_factory is None:
        wafer = _load_wafer()
        session_cls = wafer.SyncSession
    else:
        session_cls = session_factory

    session = session_cls(
        max_rotations=2,
        cache_dir=str(cache_dir),
        follow_redirects=False,
        rate_limit=6.0,
        rate_jitter=0.2,
        timeout=15.0,
        max_response_size=10 * 1024 * 1024,
    )

    start_mono = monotonic_fn()
    collected: list[dict[str, Any]] = []
    consecutive_rate_or_gate = 0

    try:
        for raw_sub in subreddits:
            if (monotonic_fn() - start_mono) > _REDDIT_DEADLINE_SECONDS:
                break

            sub = raw_sub.strip()
            if not sub or not _SUBREDDIT_RE.match(sub):
                continue

            q_t = urllib.parse.quote(time_filter, safe="")
            url = f"https://api.reddit.com/r/{sub}/top.json?t={q_t}&limit={int(limit)}&raw_json=1"
            if not is_allowed_reddit_host(url):
                continue

            abort_all = False
            for attempt in range(2):
                if (monotonic_fn() - start_mono) > _REDDIT_DEADLINE_SECONDS:
                    abort_all = True
                    break

                try:
                    resp = session.get(url, headers=dict(DEFAULT_HEADERS))
                except Exception as exc:
                    action, wait_s = _classify_failure(exc)
                    if action == "skip":
                        consecutive_rate_or_gate = 0
                        break
                    if action in ("retry_rate_limit", "retry_gate"):
                        consecutive_rate_or_gate += 1
                        if consecutive_rate_or_gate >= 2 or attempt >= 1:
                            abort_all = True
                            break
                        sleep_fn(wait_s)
                        continue
                    abort_all = True
                    break

                status = int(getattr(resp, "status_code", getattr(resp, "status", 200)) or 200)
                if status == 200:
                    consecutive_rate_or_gate = 0
                    payload = _extract_response_json(resp)
                    if isinstance(payload, dict):
                        for post in _extract_posts(sub, payload):
                            collected.append(post)
                            out_stream.write(json.dumps(post, separators=(",", ":")) + "\n")
                            out_stream.flush()
                    break

                action, wait_s = _classify_failure(resp)
                if action == "skip":
                    consecutive_rate_or_gate = 0
                    break
                if action in ("retry_rate_limit", "retry_gate"):
                    consecutive_rate_or_gate += 1
                    if consecutive_rate_or_gate >= 2 or attempt >= 1:
                        abort_all = True
                        break
                    sleep_fn(wait_s)
                    continue

                abort_all = True
                break

            if abort_all:
                break
    finally:
        close_fn = getattr(session, "close", None)
        if callable(close_fn):
            with contextlib.suppress(Exception):
                close_fn()
        _chmod_cache_files(cache_dir)

    return collected


def build_parser() -> argparse.ArgumentParser:
    """Build argument parser for reddit_pull.py."""
    parser = argparse.ArgumentParser(prog="reddit_pull.py")
    parser.add_argument("--subs", required=True, help="Comma-separated list of subreddits")
    parser.add_argument(
        "--t", default="day", choices=("hour", "day", "week", "month", "year", "all")
    )
    parser.add_argument("--limit", type=int, default=25)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for reddit_pull.py."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    subs = [s.strip() for s in str(args.subs).split(",") if s.strip()]
    pull_subreddits(subs, time_filter=str(args.t), limit=int(args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
