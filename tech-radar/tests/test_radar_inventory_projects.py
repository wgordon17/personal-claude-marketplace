"""Tests for project inventory collector (Task 4 Step 4, Spec 04, Spec 12 A8, A27)."""

from __future__ import annotations

import builtins
import hashlib
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tech_radar import context, fsguard, http, inventory, procs

FIXED_NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)


def _tree_sha256(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        h.update(rel.encode("utf-8"))
        if p.is_file() and not p.is_symlink():
            h.update(p.read_bytes())
    return h.hexdigest()


def _make_ctx(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> context.Ctx:
    return context.build(
        transports={
            "http": replay_http("classify"),
            "gh": replay_gh(),
            "procs": replay_procs(),
        },
        now=FIXED_NOW,
    )


def test_projects_worktree_dedupe_manifests_visibility_aliases_and_safety(
    fixture_home: Path,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evil_repo = fixture_home / "SynologyDrive" / "ActiveProjects" / "evil-repo"
    evil_checksum_before = _tree_sha256(evil_repo)
    canary_path = fixture_home / "fsmonitor_canary_triggered"
    assert not canary_path.exists()

    opened_paths: list[Path] = []
    real_open = builtins.open

    def _tracking_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(file, str | Path):
            opened_paths.append(Path(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _tracking_open)

    ctx = _make_ctx(replay_http, replay_gh, replay_procs)
    sys_out = inventory.system.collect(ctx)
    prj_out = inventory.projects.collect(
        ctx,
        extra_roots=sys_out.extra_roots,
        known_clis=sys_out.known_clis,
    )

    # 1. Worktree dedupe: Repo A and Repo B (worktree of A) counted once
    by_key = {p.project_key: p for p in prj_out.projects}
    assert "https://github.com/example-org/public-app" in by_key
    public_app_matches = [
        p for p in prj_out.projects if p.project_key == "https://github.com/example-org/public-app"
    ]
    assert len(public_app_matches) == 1

    # 2. Out-of-tree launchd project yields pkg:pypi/litellm==1.106.2 direct=1 with source_file
    gw_dir = (fixture_home / ".config" / "llm-gateway").resolve()
    gw_lock = gw_dir / "uv.lock"
    gw_proj = next(p for p in prj_out.projects if Path(p.main_worktree) == gw_dir)
    gw_ctx = f"project:{gw_proj.project_key}"
    gw_usages = [
        u for u in prj_out.usages if u.context == gw_ctx and u.entity_id == "pkg:pypi/litellm"
    ]
    assert len(gw_usages) == 1
    gw_u = gw_usages[0]
    assert gw_u.version == "1.106.2"
    assert gw_u.direct == 1
    assert gw_u.source_file == str(gw_lock)

    # parse_manifest on that file alone returns the same usages
    parsed_alone = inventory.projects.parse_manifest(gw_lock, gw_proj.project_key)
    assert parsed_alone == gw_usages

    # 3. Direct vs transitive in Repo A
    repo_a_ctx = "project:https://github.com/example-org/public-app"
    repo_a_usages = {u.entity_id: u for u in prj_out.usages if u.context == repo_a_ctx}
    assert repo_a_usages["pkg:pypi/fastapi"].direct == 1
    assert repo_a_usages["pkg:pypi/starlette"].direct == 0
    assert repo_a_usages["pkg:pypi/litellm"].attrs.get("ai_sdk") is True

    # 4. GHA uses recorded with sha_pinned attr
    assert repo_a_usages["gha:actions/checkout"].attrs.get("sha_pinned") is True
    assert repo_a_usages["gha:actions/setup-python"].attrs.get("sha_pinned") is False

    # 5. Project visibility and stable aliases (Spec 12 A8)
    repo_a_proj = by_key["https://github.com/example-org/public-app"]
    assert repo_a_proj.visibility == "public"
    assert repo_a_proj.alias == "example-org/public-app"

    repo_c_proj = by_key["https://github.com/example-org/secret-widget"]
    assert repo_c_proj.visibility == "private"
    # evil-repo and secret-widget are both active_projects; check private-project-1 is assigned
    private_active = [
        p
        for p in prj_out.projects
        if p.visibility == "private" and p.root_source == "active_projects"
    ]
    assert any(p.alias == "private-project-1" for p in private_active)
    assert repo_c_proj.alias is not None and repo_c_proj.alias.startswith("private-project-")

    # Upsert into DB and verify aliases unchanged on re-collect
    inventory._upsert_projects(ctx, prj_out.projects)
    ctx.db.commit()
    aliases_first = {p.project_key: p.alias for p in prj_out.projects}
    prj_out_second = inventory.projects.collect(
        ctx,
        extra_roots=sys_out.extra_roots,
        known_clis=sys_out.known_clis,
    )
    aliases_second = {p.project_key: p.alias for p in prj_out_second.projects}
    assert aliases_second == aliases_first

    # 6. Tokenized remote URL stripped of userinfo
    assert "https://github.com/example-org/evil-repo" in by_key
    for key in by_key:
        assert "devuser" not in key
        assert "@" not in key

    # 7. Malicious repo canary never ran and tree sha256 unchanged
    assert not canary_path.exists()
    assert not (evil_repo / "EVIL_CANARY").exists()
    assert _tree_sha256(evil_repo) == evil_checksum_before

    # 8. Never-read list enforced on builtins.open
    for p in opened_paths:
        assert not inventory.projects.is_never_read(p), f"never-read file opened: {p}"


def test_dataless_file_is_skipped_and_counted_in_counts_json(
    fixture_home: Path,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_dockerfile = (
        fixture_home / "SynologyDrive" / "ActiveProjects" / "public-app" / "Dockerfile"
    ).resolve()
    real_stat = os.stat

    class _FakeDatalessStat:
        def __init__(self, inner: os.stat_result) -> None:
            self._inner = inner
            self.st_flags = fsguard.SF_DATALESS

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    def _patched_stat(path: Any, *args: Any, **kwargs: Any) -> Any:
        st = real_stat(path, *args, **kwargs)
        try:
            if Path(path).resolve() == target_dockerfile:
                return _FakeDatalessStat(st)
        except OSError:
            pass
        return st

    monkeypatch.setattr(os, "stat", _patched_stat)

    ctx = _make_ctx(replay_http, replay_gh, replay_procs)
    res = inventory.collect(ctx)

    assert res.counts["dataless_skipped"] >= 1
    oci_usages = [
        u
        for u in ctx.db.execute("SELECT entity_id FROM usages").fetchall()
        if str(u[0]).startswith("oci:")
    ]
    assert not oci_usages
