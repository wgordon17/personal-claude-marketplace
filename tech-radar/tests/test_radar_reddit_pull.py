"""Tests for isolated S/reddit_pull.py and community.reddit_top."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import reddit_pull
from tech_radar import context, http, procs, sources
from tech_radar.sources import community
from tech_radar.sources.base import run_source

FIXED_NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "skills" / "radar" / "scripts"
REDDIT_SCRIPT = SCRIPTS_DIR / "reddit_pull.py"
REDDIT_LOCK = SCRIPTS_DIR / "reddit_pull.py.lock"


def test_a_pep723_header_exact_pin_exclude_newer_and_lockfile_exists() -> None:
    """(a) PEP 723 header pins wafer-py==0.7.7 (exact ==) with exclude-newer, lockfile exists."""
    assert REDDIT_SCRIPT.is_file()
    assert REDDIT_LOCK.is_file()

    text = REDDIT_SCRIPT.read_text(encoding="utf-8")
    assert "# /// script" in text
    assert '"wafer-py==0.7.7"' in text
    assert 'exclude-newer = "2026-10-20T00:00:00Z"' in text
    assert "tech_radar" not in text

    lock_text = REDDIT_LOCK.read_text(encoding="utf-8")
    assert 'name = "wafer-py"' in lock_text
    assert 'version = "0.7.7"' in lock_text


def test_b_reddit_top_invokes_procs_run_with_timeout_600_and_minimal_env(
    tmp_path: Path,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    """(b) reddit_top invokes procs.run with timeout=600 and minimal env keys."""
    subs_joined = ",".join(community.REDDIT_SUBREDDITS)
    expected_norm_argv = [
        "uv",
        "run",
        "--script",
        "--locked",
        "--isolated",
        "{S}/reddit_pull.py",
        "--subs",
        subs_joined,
        "--t",
        "day",
        "--limit",
        "25",
    ]
    proc_fix_dir = tmp_path / "reddit_procs"
    proc_fix_dir.mkdir(parents=True)
    sample_post = {
        "subreddit": "ClaudeCode",
        "id": "abc123",
        "title": "New MCP server workflow",
        "url": "https://github.com/example-org/mcp-workflow",
        "permalink": "/r/ClaudeCode/comments/abc123/new_mcp_server_workflow/",
        "score": 95,
        "num_comments": 18,
        "created_utc": 1793180000,
        "selftext": "Benchmarked https://github.com/example-org/mcp-workflow.",
    }
    (proc_fix_dir / "reddit_ok.json").write_text(
        json.dumps(
            {
                "argv": expected_norm_argv,
                "returncode": 0,
                "stderr": "",
                "stdout": json.dumps(sample_post) + "\n",
            }
        ),
        encoding="utf-8",
    )

    p_tr = replay_procs(extra_roots=[proc_fix_dir])
    ctx = context.build(
        transports={"http": replay_http(), "gh": replay_gh(), "procs": p_tr},
        now=FIXED_NOW,
    )

    recorded_timeouts: list[float] = []
    orig_run = ctx.procs.run

    def _spy_run(argv: Any, **kwargs: Any) -> procs.ProcResult:
        recorded_timeouts.append(float(kwargs.get("timeout", 0.0)))
        return orig_run(argv, **kwargs)

    ctx.procs.run = _spy_run  # type: ignore[method-assign]

    spec = sources.get_source("reddit_top")
    res = run_source(spec, ctx)
    assert res.ok is True
    assert len(res.signals) == 1
    assert recorded_timeouts == [600.0]

    assert len(p_tr.calls) == 1
    norm_argv, env_keys = p_tr.calls[0]
    assert norm_argv == expected_norm_argv
    assert "{S}/reddit_pull.py" in norm_argv
    assert env_keys <= frozenset({"PATH", "HOME", "TECH_RADAR_STATE"})
    assert "TECH_RADAR_STATE" in env_keys


def test_b2_is_allowed_reddit_host() -> None:
    """(b2) is_allowed_reddit_host accepts https on www/api/oauth.reddit.com only."""
    assert reddit_pull.is_allowed_reddit_host("https://www.reddit.com/r/Python/top.json") is True
    assert reddit_pull.is_allowed_reddit_host("https://api.reddit.com/r/Python/top.json") is True
    assert reddit_pull.is_allowed_reddit_host("https://oauth.reddit.com/r/Python/top.json") is True

    assert reddit_pull.is_allowed_reddit_host("http://api.reddit.com/r/Python/top.json") is False
    assert reddit_pull.is_allowed_reddit_host("https://old.reddit.com/r/Python/top.json") is False
    assert reddit_pull.is_allowed_reddit_host("https://evil.example.com/r/Python/top.json") is False
    assert (
        reddit_pull.is_allowed_reddit_host("https://user:pass@api.reddit.com/r/Python/top.json")
        is False
    )
    assert reddit_pull.is_allowed_reddit_host("file:///etc/passwd") is False


class _FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        payload: dict[str, Any] | None = None,
        *,
        reason: str = "",
        challenge_type: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.reason = reason
        self.challenge_type = challenge_type
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        return self._payload


class ChallengeDetected(Exception):
    def __init__(self, challenge_type: str = "reddit") -> None:
        super().__init__(challenge_type)
        self.challenge_type = challenge_type


class RateLimited(Exception):
    def __init__(self, retry_after: float | None = 12.0) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after


def _listing_payload(sub: str, post_id: str, title: str) -> dict[str, Any]:
    return {
        "kind": "Listing",
        "data": {
            "children": [
                {
                    "kind": "t3",
                    "data": {
                        "subreddit": sub,
                        "id": post_id,
                        "title": title,
                        "url": f"https://www.reddit.com/r/{sub}/comments/{post_id}/",
                        "permalink": f"/r/{sub}/comments/{post_id}/",
                        "score": 100,
                        "num_comments": 10,
                        "created_utc": 1793180000,
                        "selftext": f"Body for {post_id}",
                    },
                }
            ]
        },
    }


def test_b3_fake_wafer_session_kwargs_skips_retries_and_early_abort(tmp_path: Path) -> None:
    """(b3) Verify SyncSession kwargs, 3xx/content-state skip, single retry, and early abort."""
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    captured_kwargs: dict[str, Any] = {}
    sleeps: list[float] = []

    # Sequence of scripted responses across subreddits:
    # 1. sub1: 302 redirect -> skipped with 0 sleep
    # 2. sub2: 403 reason="private" -> skipped with 0 sleep
    # 3. sub3: RateLimited(retry_after=15.0) once -> sleeps 15.0, retries sub3 -> 200 OK
    # 4. sub4: ChallengeDetected("reddit") once -> sleeps 5.0, retries sub4 -> 200 OK
    # 5. sub5: opaque 403 block -> aborts immediately without requesting sub6
    scripted: list[Any] = [
        _FakeResponse(302),
        _FakeResponse(403, {"reason": "private"}, reason="private"),
        RateLimited(retry_after=15.0),
        _FakeResponse(200, _listing_payload("sub3", "p3", "Post Three")),
        ChallengeDetected(challenge_type="reddit"),
        _FakeResponse(200, _listing_payload("sub4", "p4", "Post Four")),
        _FakeResponse(403, {"message": "Blocked"}),
        _FakeResponse(200, _listing_payload("sub6", "p6", "Should Not Be Reached")),
    ]
    requested_urls: list[str] = []

    class _FakeSyncSession:
        def __init__(self, **kwargs: Any) -> None:
            captured_kwargs.update(kwargs)
            cdir = Path(kwargs["cache_dir"])
            (cdir / "reddit-app.state").write_text("{}", encoding="utf-8")
            (cdir / "reddit.com.json").write_text("{}", encoding="utf-8")

        def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
            _ = headers
            requested_urls.append(url)
            item = scripted.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        def close(self) -> None:
            pass

    buf = io.StringIO()
    posts = reddit_pull.pull_subreddits(
        ["sub1", "sub2", "sub3", "sub4", "sub5", "sub6"],
        state_dir=state_dir,
        session_factory=_FakeSyncSession,
        sleep_fn=sleeps.append,
        monotonic_fn=lambda: 100.0,
        stdout=buf,
    )

    assert captured_kwargs["cache_dir"] == str(state_dir / "wafer_cache")
    assert captured_kwargs["rate_limit"] == 6.0
    assert captured_kwargs["rate_jitter"] == 0.2
    assert captured_kwargs["follow_redirects"] is False
    assert captured_kwargs["max_rotations"] == 2
    assert captured_kwargs["timeout"] == 15.0
    assert captured_kwargs["max_response_size"] == 10 * 1024 * 1024

    # State files inside wafer_cache are chmodded 0600
    for sfile in ("reddit-app.state", "reddit.com.json"):
        mode = (state_dir / "wafer_cache" / sfile).stat().st_mode & 0o777
        assert mode == 0o600

    # Only two sleeps occurred: 15.0s for RateLimited and 5.0s for ChallengeDetected("reddit")
    assert sleeps == [15.0, 5.0]
    assert [p["id"] for p in posts] == ["p3", "p4"]
    assert not any("sub6" in u for u in requested_urls)

    lines = [json.loads(line) for line in buf.getvalue().strip().splitlines()]
    assert [ln["id"] for ln in lines] == ["p3", "p4"]

    # Deadline > 540s test: emits partial lines collected before deadline and exits 0
    clock_vals = iter([0.0, 10.0, 10.0, 550.0, 550.0])
    deadline_scripted = [
        _FakeResponse(200, _listing_payload("ClaudeAI", "p_early", "Early Post")),
        _FakeResponse(200, _listing_payload("Python", "p_late", "Late Post")),
    ]

    class _DeadlineSession:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def get(self, _url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
            _ = headers
            return deadline_scripted.pop(0)

    buf_dl = io.StringIO()
    dl_posts = reddit_pull.pull_subreddits(
        ["ClaudeAI", "Python"],
        state_dir=state_dir,
        session_factory=_DeadlineSession,
        sleep_fn=lambda _s: None,
        monotonic_fn=lambda: next(clock_vals, 600.0),
        stdout=buf_dl,
    )
    assert [p["id"] for p in dl_posts] == ["p_early"]
    dl_lines = [json.loads(line) for line in buf_dl.getvalue().strip().splitlines()]
    assert [ln["id"] for ln in dl_lines] == ["p_early"]


def test_c_synthetic_stdout_fixture_parses_into_horizon_with_sanitized_excerpt() -> None:
    """(c) Synthetic stdout fixture parses into horizon signals with sanitized excerpt."""
    inj_path = Path(__file__).resolve().parent / "fixtures" / "injection" / "reddit.json"
    inj_obj = json.loads(inj_path.read_text(encoding="utf-8"))
    raw_line = (
        json.dumps(
            {
                "subreddit": "LocalLLaMA",
                "id": "inj99",
                "title": "Benchmarking local quantizations",
                "url": "https://www.reddit.com/r/LocalLLaMA/comments/inj99/",
                "permalink": "/r/LocalLLaMA/comments/inj99/",
                "score": 88,
                "num_comments": 14,
                "created_utc": 1793180000,
                "selftext": str(inj_obj.get("body") or inj_obj.get("selftext") or str(inj_obj)),
            }
        )
        + "\n"
    )
    spec = sources.get_source("reddit_top")
    assert spec.parser is not None
    meta = sources.FetchMeta(url="reddit_pull.py", fetched_at="2026-10-28T12:00:00Z")
    sigs = spec.parser(raw_line.encode("utf-8"), meta)
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig.type == "horizon"
    assert sig.source_id == "reddit_top"
    assert "<script" not in sig.untrusted_excerpt.lower()
    assert "‮" not in sig.untrusted_excerpt
    assert "​" not in sig.untrusted_excerpt


def test_d_empty_stdout_marks_source_health_degraded_without_error(
    tmp_path: Path,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    """(d) Empty stdout -> source_health degraded, no error."""
    subs_joined = ",".join(community.REDDIT_SUBREDDITS)
    expected_norm_argv = [
        "uv",
        "run",
        "--script",
        "--locked",
        "--isolated",
        "{S}/reddit_pull.py",
        "--subs",
        subs_joined,
        "--t",
        "day",
        "--limit",
        "25",
    ]
    proc_fix_dir = tmp_path / "reddit_empty_procs"
    proc_fix_dir.mkdir(parents=True)
    (proc_fix_dir / "reddit_empty.json").write_text(
        json.dumps(
            {
                "argv": expected_norm_argv,
                "returncode": 0,
                "stderr": "",
                "stdout": "",
            }
        ),
        encoding="utf-8",
    )

    p_tr = replay_procs(extra_roots=[proc_fix_dir])
    ctx = context.build(
        transports={"http": replay_http(), "gh": replay_gh(), "procs": p_tr},
        now=FIXED_NOW,
    )
    spec = sources.get_source("reddit_top")
    res = run_source(spec, ctx)
    assert res.ok is True
    assert res.error is None
    assert res.signals == []
    assert res.skipped_reason is not None
    assert "degraded" in res.skipped_reason

    row = ctx.db.execute(
        "SELECT last_error, consecutive_failures, skipped_reason "
        "FROM source_health WHERE source_id = 'reddit_top'"
    ).fetchone()
    assert row is not None
    assert row["last_error"] is None
    assert int(row["consecutive_failures"]) == 0
    assert "degraded" in str(row["skipped_reason"])


def test_e_consecutive_rate_limits_cap_at_60s_and_abort_on_second_consecutive(
    tmp_path: Path,
) -> None:
    """(e) RateLimited sleep caps at 60s, 404/banned skip with 0s, 2nd consecutive 429 aborts."""
    state_dir = tmp_path / "state_e"
    state_dir.mkdir(parents=True)
    sleeps: list[float] = []
    requested_urls: list[str] = []

    # 1. sub1: 404 -> skip (0s sleep)
    # 2. sub2: 403 reason="banned" -> skip (0s sleep)
    # 3. sub3: RateLimited(retry_after=120.0) -> sleep 60.0s (capped), retry sub3 -> 429 response
    #    (2nd consecutive rate-limit/gate event -> aborts immediately without requesting sub4)
    scripted: list[Any] = [
        _FakeResponse(404),
        _FakeResponse(403, {"reason": "banned"}, reason="banned"),
        RateLimited(retry_after=120.0),
        _FakeResponse(429, headers={"Retry-After": "90"}),
        _FakeResponse(200, _listing_payload("sub4", "p4", "Unreachable")),
    ]

    class _Session:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
            _ = headers
            requested_urls.append(url)
            item = scripted.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    buf = io.StringIO()
    posts = reddit_pull.pull_subreddits(
        ["../invalid", "sub1", "sub2", "sub3", "sub4"],
        state_dir=state_dir,
        session_factory=_Session,
        sleep_fn=sleeps.append,
        monotonic_fn=lambda: 10.0,
        stdout=buf,
    )
    assert posts == []
    assert sleeps == [60.0]
    assert not any("invalid" in u for u in requested_urls)
    assert not any("sub4" in u for u in requested_urls)
