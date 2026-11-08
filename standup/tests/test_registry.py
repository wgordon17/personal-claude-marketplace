"""Tests for standup_tool.collectors.registry."""

from __future__ import annotations

import dataclasses
import threading
import time
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from standup_tool.collectors.registry import (
    REGISTRY,
    Collector,
    Ctx,
    run_all,
    run_one,
)
from standup_tool.config import Config, GoogleConfig


def _make_ctx(acme_config: Config, cfg_dir: Path) -> Ctx:
    return Ctx(
        config=acme_config,
        since=date(2026, 4, 20),
        until=date(2026, 4, 21),
        repos=[],
        author_email="ada@example.com",
        cfg_dir=cfg_dir,
    )


def test_collector_and_ctx_are_frozen(acme_config: Config, cfg_dir: Path):
    ctx = _make_ctx(acme_config, cfg_dir)
    col = Collector(name="git", output_key="git_commits", run=lambda c, o: [], empty=[])
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.author_email = "other@example.com"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        col.name = "other"  # type: ignore[misc]


def test_registry_contains_nine_collectors_in_order():
    names = [c.name for c in REGISTRY]
    assert names == [
        "git",
        "pr",
        "jira",
        "calendar",
        "gmail",
        "drive",
        "slack",
        "memory",
        "conversations",
    ]
    drive_col = next(c for c in REGISTRY if c.name == "drive")
    assert drive_col.depends_on == ("calendar",)


def test_run_one_disabled_in_config(acme_config: Config, cfg_dir: Path):
    ctx = _make_ctx(acme_config, cfg_dir)
    # conversations is disabled by default in acme_config
    called = []
    col = Collector(
        name="conversations",
        output_key="conversations",
        run=lambda c, o: called.append(True) or [{"id": 1}],
        empty=[],
    )
    out, st = run_one(col, ctx, None, {}, {})
    assert out == []
    assert st == {"state": "disabled", "reason": "disabled in config", "count": 0}
    assert called == []


def test_run_one_not_selected(acme_config: Config, cfg_dir: Path):
    ctx = _make_ctx(acme_config, cfg_dir)
    called = []
    col = Collector(
        name="git",
        output_key="git_commits",
        run=lambda c, o: called.append(True) or [{"hash": "abc"}],
        empty=[],
    )
    out, st = run_one(col, ctx, {"pr"}, {}, {})
    assert out == []
    assert st == {"state": "disabled", "reason": "not selected", "count": 0}
    assert called == []


def test_run_one_dependency_disabled_in_config(acme_config: Config, cfg_dir: Path):
    cfg = dataclasses.replace(
        acme_config,
        collectors=dataclasses.replace(
            acme_config.collectors,
            calendar=GoogleConfig(enabled=False),
        ),
    )
    ctx = _make_ctx(cfg, cfg_dir)
    col = Collector(
        name="drive",
        output_key="drive_docs",
        run=lambda c, o: [{"file_id": "1"}],
        empty=[],
        depends_on=("calendar",),
    )
    status = {"calendar": {"state": "disabled", "reason": "disabled in config", "count": 0}}
    out, st = run_one(col, ctx, None, {"calendar_events": []}, status)
    assert out == []
    assert st == {"state": "disabled", "reason": "requires calendar", "count": 0}


def test_run_one_dependency_not_selected(acme_config: Config, cfg_dir: Path):
    ctx = _make_ctx(acme_config, cfg_dir)
    col = Collector(
        name="drive",
        output_key="drive_docs",
        run=lambda c, o: [{"file_id": "1"}],
        empty=[],
        depends_on=("calendar",),
    )
    status = {"calendar": {"state": "disabled", "reason": "not selected", "count": 0}}
    out, st = run_one(col, ctx, {"drive"}, {"calendar_events": []}, status)
    assert out == []
    assert st == {"state": "disabled", "reason": "requires calendar", "count": 0}


def test_run_one_dependency_failed(acme_config: Config, cfg_dir: Path):
    ctx = _make_ctx(acme_config, cfg_dir)
    col = Collector(
        name="drive",
        output_key="drive_docs",
        run=lambda c, o: [{"file_id": "1"}],
        empty=[],
        depends_on=("calendar",),
    )
    status = {"calendar": {"state": "failed", "reason": "RuntimeError: boom", "count": 0}}
    out, st = run_one(col, ctx, None, {"calendar_events": []}, status)
    assert out == []
    assert st == {"state": "failed", "reason": "dependency calendar failed", "count": 0}


def test_run_one_runs_when_dependency_ran_with_empty_result(acme_config: Config, cfg_dir: Path):
    ctx = _make_ctx(acme_config, cfg_dir)
    called_with = []

    def _run(c: Ctx, outputs: dict):
        called_with.append(outputs.get("calendar_events"))
        return []

    col = Collector(
        name="drive",
        output_key="drive_docs",
        run=_run,
        empty=[],
        depends_on=("calendar",),
    )
    status = {"calendar": {"state": "ran", "reason": None, "count": 0}}
    out, st = run_one(col, ctx, None, {"calendar_events": []}, status)
    assert out == []
    assert st == {"state": "ran", "reason": None, "count": 0}
    assert called_with == [[]]


def test_run_one_ran_list_and_dict_counts(acme_config: Config, cfg_dir: Path):
    ctx = _make_ctx(acme_config, cfg_dir)
    list_col = Collector(
        name="git",
        output_key="git_commits",
        run=lambda c, o: [{"hash": "1"}, {"hash": "2"}, {"hash": "3"}],
        empty=[],
    )
    out_list, st_list = run_one(list_col, ctx, None, {}, {})
    assert len(out_list) == 3
    assert st_list == {"state": "ran", "reason": None, "count": 3}

    dict_col = Collector(
        name="jira",
        output_key="jira_activity",
        run=lambda c, o: {
            "assigned": [{"key": "ACME-1"}, {"key": "ACME-2"}],
            "in_sprint": [{"key": "ACME-3"}],
            "recently_updated": [],
        },
        empty={},
    )
    out_dict, st_dict = run_one(dict_col, ctx, None, {}, {})
    assert "assigned" in out_dict
    assert st_dict == {"state": "ran", "reason": None, "count": 3}


def test_run_one_failed_redacts_secrets_in_reason_and_stderr(
    acme_config: Config,
    cfg_dir: Path,
    capsys: pytest.CaptureFixture[str],
):
    ctx = _make_ctx(acme_config, cfg_dir)
    tok = "xox" + "c-secret12345"
    bearer = "Bearer supersecrettok"
    basic = "Basic dXNlcjpwYXNz"

    def _failing_run(c: Ctx, outputs: dict):
        raise RuntimeError(f"Auth failed with {tok} and {bearer} and {basic}")

    col = Collector(
        name="slack",
        output_key="slack",
        run=_failing_run,
        empty=[],
    )
    out, st = run_one(col, ctx, None, {}, {})
    captured = capsys.readouterr()

    assert out == []
    assert st["state"] == "failed"
    assert st["count"] == 0
    assert st["reason"] is not None
    assert len(st["reason"]) <= 300
    assert "secret12345" not in st["reason"]
    assert "supersecrettok" not in st["reason"]
    assert "dXNlcjpwYXNz" not in st["reason"]
    assert "[REDACTED]" in st["reason"]

    assert "Traceback" in captured.err
    assert "secret12345" not in captured.err
    assert "supersecrettok" not in captured.err
    assert "dXNlcjpwYXNz" not in captured.err
    assert "[REDACTED]" in captured.err


def test_run_one_truncates_long_failure_reason_to_300_chars(
    acme_config: Config,
    cfg_dir: Path,
):
    ctx = _make_ctx(acme_config, cfg_dir)
    long_msg = "x" * 500
    col = Collector(
        name="git",
        output_key="git_commits",
        run=lambda c, o: (_ for _ in ()).throw(ValueError(long_msg)),
        empty=[],
    )
    out, st = run_one(col, ctx, None, {}, {})
    assert out == []
    assert st["state"] == "failed"
    assert st["reason"] is not None
    assert len(st["reason"]) == 300
    assert st["reason"].startswith("ValueError: xxxxx")


def test_run_one_dependency_missing_from_status_checks_config_and_selected(
    acme_config: Config,
    cfg_dir: Path,
):
    ctx = _make_ctx(acme_config, cfg_dir)
    col = Collector(
        name="drive",
        output_key="drive_docs",
        run=lambda c, o: [{"file_id": "1"}],
        empty=[],
        depends_on=("calendar",),
    )
    # status dict is empty (calendar not yet recorded), selected excludes calendar
    out, st = run_one(col, ctx, {"drive"}, {}, {})
    assert out == []
    assert st == {"state": "disabled", "reason": "requires calendar", "count": 0}


def test_run_all_executes_independents_concurrently_and_dependents_after(
    acme_config: Config,
    cfg_dir: Path,
):
    ctx = _make_ctx(acme_config, cfg_dir)
    lock = threading.Lock()
    running = [0]
    max_running = [0]
    order: list[str] = []

    def make_indep(name: str):
        def _fn(c: Ctx, outputs: dict):
            with lock:
                running[0] += 1
                if running[0] > max_running[0]:
                    max_running[0] = running[0]
            time.sleep(0.05)
            with lock:
                running[0] -= 1
                order.append(name)
            return [{"item": name}]

        return _fn

    def dep_fn(c: Ctx, outputs: dict):
        with lock:
            order.append("drive")
        assert "calendar_events" in outputs
        return [{"doc": 1}]

    custom_registry = [
        Collector(name="git", output_key="git_commits", run=make_indep("git"), empty=[]),
        Collector(
            name="calendar",
            output_key="calendar_events",
            run=make_indep("calendar"),
            empty=[],
        ),
        Collector(
            name="drive",
            output_key="drive_docs",
            run=dep_fn,
            empty=[],
            depends_on=("calendar",),
        ),
        Collector(name="pr", output_key="prs", run=make_indep("pr"), empty=[]),
    ]

    outputs, status = run_all(custom_registry, ctx, None)
    assert max_running[0] >= 2
    assert order[-1] == "drive"
    assert list(outputs.keys()) == ["git_commits", "calendar_events", "drive_docs", "prs"]
    assert list(status.keys()) == ["git", "calendar", "drive", "pr"]
    assert status["drive"] == {"state": "ran", "reason": None, "count": 1}


def test_registry_drive_selected_without_calendar_is_disabled(
    acme_config: Config,
    cfg_dir: Path,
):
    ctx = _make_ctx(acme_config, cfg_dir)
    with patch("standup_tool.collectors.registry.google_drive.collect") as mock_drive:
        outputs, status = run_all(REGISTRY, ctx, {"drive"})

    mock_drive.assert_not_called()
    assert outputs["drive_docs"] == []
    assert status["drive"] == {"state": "disabled", "reason": "requires calendar", "count": 0}


def test_registry_drive_skips_google_drive_collect_when_calendar_empty(
    acme_config: Config,
    cfg_dir: Path,
):
    ctx = _make_ctx(acme_config, cfg_dir)
    with (
        patch("standup_tool.collectors.registry.google_calendar.collect", return_value=[]),
        patch("standup_tool.collectors.registry.google_drive.collect") as mock_drive,
    ):
        outputs, status = run_all(REGISTRY, ctx, {"calendar", "drive"})

    mock_drive.assert_not_called()
    assert outputs["drive_docs"] == []
    assert status["calendar"] == {"state": "ran", "reason": None, "count": 0}
    assert status["drive"] == {"state": "ran", "reason": None, "count": 0}
