"""Run token/turn usage extraction from Claude transcripts and CLI recorder (Spec 05 §7)."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import db, errors, fsguard

if TYPE_CHECKING:
    from .context import Ctx

_parse_iso = db.parse_iso


def _parse_transcript_jsonl_lines(
    lines: Iterable[str],
    *,
    expected_cwd: str | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> dict[str, Any] | None:
    input_tokens = 0
    output_tokens = 0
    cache_creation_tokens = 0
    cache_read_tokens = 0
    turns = 0
    matched_cwd = expected_cwd is None
    matched_time = window_start is None and window_end is None
    timestamps: list[datetime] = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, Mapping):
            continue

        rec_cwd = rec.get("cwd")
        if expected_cwd is not None and isinstance(rec_cwd, str):
            if rec_cwd.strip() == expected_cwd.strip():
                matched_cwd = True
            else:
                continue

        ts_raw = rec.get("timestamp") or rec.get("created_at")
        rec_dt = _parse_iso(str(ts_raw)) if ts_raw else None
        if rec_dt is not None:
            if window_start is not None and rec_dt < window_start:
                continue
            if window_end is not None and rec_dt > window_end:
                continue
            matched_time = True
            timestamps.append(rec_dt)

        msg = rec.get("message")
        msg_map = msg if isinstance(msg, Mapping) else {}
        role = str(msg_map.get("role") or rec.get("type") or "").lower()
        usage_obj = msg_map.get("usage") or rec.get("usage")

        if role == "assistant" or isinstance(usage_obj, Mapping):
            turns += 1
        if isinstance(usage_obj, Mapping):
            input_tokens += int(usage_obj.get("input_tokens") or 0)
            output_tokens += int(usage_obj.get("output_tokens") or 0)
            cache_creation_tokens += int(usage_obj.get("cache_creation_input_tokens") or 0)
            cache_read_tokens += int(usage_obj.get("cache_read_input_tokens") or 0)

    if (
        not matched_cwd
        or not matched_time
        or (turns == 0 and input_tokens == 0 and output_tokens == 0)
    ):
        return None

    duration_sec = 0.0
    if len(timestamps) >= 2:
        duration_sec = round((max(timestamps) - min(timestamps)).total_seconds(), 2)

    total_tokens = input_tokens + output_tokens + cache_creation_tokens + cache_read_tokens
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation_tokens,
        "cache_read_input_tokens": cache_read_tokens,
        "total_tokens": total_tokens,
        "turns": turns,
        "duration_seconds": duration_sec,
    }


def compute_previous_run_usage(
    ctx: Ctx,
    current_run_id: str | None = None,
) -> dict[str, Any] | None:
    """Compute usage for the previous run from overlapping `~/.claude/projects/*/*.jsonl` files."""
    prev_row = ctx.db.execute(
        "SELECT run_id, started_at, finished_at, usage_json, counts_json "
        "FROM runs WHERE mode IN ('daily', 'weekly', 'monthly') "
        "AND run_id != COALESCE(?, '') "
        "ORDER BY started_at DESC LIMIT 1",
        (current_run_id,),
    ).fetchone()
    if prev_row is None:
        return None

    if prev_row["usage_json"]:
        try:
            existing = json.loads(str(prev_row["usage_json"]))
            if isinstance(existing, dict):
                return existing
        except json.JSONDecodeError:
            pass

    prev_run_id = str(prev_row["run_id"])
    started_dt = _parse_iso(str(prev_row["started_at"]))
    finished_dt = _parse_iso(str(prev_row["finished_at"] or ctx.now_iso))
    if started_dt is None or finished_dt is None:
        return None

    window_start = started_dt - timedelta(minutes=30)
    window_end = finished_dt + timedelta(minutes=30)

    expected_cwd: str | None = None
    if prev_row["counts_json"]:
        try:
            c_obj = json.loads(str(prev_row["counts_json"]))
            if isinstance(c_obj, Mapping) and c_obj.get("cwd"):
                expected_cwd = str(c_obj["cwd"])
        except json.JSONDecodeError:
            pass

    projects_dir = ctx.paths.home / ".claude" / "projects"
    if not projects_dir.is_dir():
        return None

    agg = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "total_tokens": 0,
        "turns": 0,
        "duration_seconds": 0.0,
    }
    matched_any = False

    for jsonl_file in sorted(projects_dir.glob("*/*.jsonl")):
        if not jsonl_file.is_file():
            continue
        try:
            st = os.stat(jsonl_file)
            if fsguard.is_dataless(st):
                continue
            if st.st_mtime < window_start.timestamp() and not os.environ.get("TECH_RADAR_HOME"):
                continue
            with open(jsonl_file, encoding="utf-8", errors="ignore") as fh:
                parsed = _parse_transcript_jsonl_lines(
                    fh,
                    expected_cwd=expected_cwd,
                    window_start=window_start,
                    window_end=window_end,
                )
        except OSError:
            continue
        if parsed is not None:
            matched_any = True
            for k in (
                "input_tokens",
                "output_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
                "total_tokens",
                "turns",
            ):
                agg[k] = int(agg[k]) + int(parsed[k])
            agg["duration_seconds"] = round(
                float(agg["duration_seconds"]) + float(parsed["duration_seconds"]),
                2,
            )

    if not matched_any:
        return None

    usage_str = db.scrub_text(json.dumps(agg, separators=(",", ":"), sort_keys=True))
    ctx.db.execute(
        "UPDATE runs SET usage_json = ? WHERE run_id = ?",
        (usage_str, prev_run_id),
    )
    ctx.db.commit()
    return agg


def record_cli(ctx: Ctx, run_id: str, json_file: Path | str) -> dict[str, Any]:
    """Record usage JSON or JSONL transcript for `run_id` (`radar.py usage record`)."""
    fpath = Path(json_file).expanduser()
    if not fpath.is_file():
        raise errors.ValidationError([{"field": "json", "error": f"usage file not found: {fpath}"}])

    raw_text = fpath.read_text(encoding="utf-8", errors="ignore").strip()
    if not raw_text:
        raise errors.ValidationError([{"field": "json", "error": "usage file is empty"}])

    usage_obj: dict[str, Any] | None = None
    if fpath.suffix == ".jsonl" or "\n" in raw_text:
        usage_obj = _parse_transcript_jsonl_lines(raw_text.splitlines())

    if usage_obj is None:
        try:
            loaded = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise errors.ValidationError(
                [{"field": "json", "error": f"invalid usage JSON: {exc}"}]
            ) from exc
        if not isinstance(loaded, Mapping):
            raise errors.ValidationError(
                [{"field": "json", "error": "usage JSON must be an object"}]
            )
        inp = int(loaded.get("input_tokens") or 0)
        out = int(loaded.get("output_tokens") or 0)
        cc = int(loaded.get("cache_creation_input_tokens") or 0)
        cr = int(loaded.get("cache_read_input_tokens") or 0)
        tot = int(loaded.get("total_tokens") or (inp + out + cc + cr))
        usage_obj = {
            "input_tokens": inp,
            "output_tokens": out,
            "cache_creation_input_tokens": cc,
            "cache_read_input_tokens": cr,
            "total_tokens": tot,
            "turns": int(loaded.get("turns") or 0),
            "duration_seconds": float(loaded.get("duration_seconds") or 0.0),
        }

    usage_str = db.scrub_text(json.dumps(usage_obj, separators=(",", ":"), sort_keys=True))
    ctx.db.execute(
        "UPDATE runs SET usage_json = ? WHERE run_id = ?",
        (usage_str, run_id),
    )
    ctx.db.commit()
    return {
        "ok": True,
        "run_id": run_id,
        "usage": usage_obj,
    }
