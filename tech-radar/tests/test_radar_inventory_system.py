"""Tests forsystem inventory collector (Task 4 Step 3, Spec 04, Spec 12 A14)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import secret_samples
from tech_radar import context, http, inventory, procs

FIXED_NOW = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)


def _make_ctx(
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> tuple[context.Ctx, procs.ReplayProcs]:
    p_tr = replay_procs()
    g_tr = replay_gh()
    h_tr = replay_http("classify")
    ctx = context.build(
        transports={"http": h_tr, "gh": g_tr, "procs": p_tr},
        now=FIXED_NOW,
    )
    return ctx, p_tr


def test_system_inventory_entities_kinds_and_contexts(
    fixture_home: Path,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    _ = fixture_home
    ctx, p_tr = _make_ctx(replay_http, replay_gh, replay_procs)
    out = inventory.system.collect(ctx)

    by_id = {e.entity_id: e for e in out.entities}
    usages_by_pair = {(u.entity_id, u.context): u for u in out.usages}

    # SYS-01 brew formulae + GitHub alias
    assert "brew:formula/jq" in by_id
    assert by_id["brew:formula/jq"].kind == "cli"
    assert by_id["brew:formula/jq"].ecosystem == "homebrew"
    assert "gh:jqlang/jq" in by_id["brew:formula/jq"].aliases
    assert ("brew:formula/jq", "system:brew") in usages_by_pair
    assert usages_by_pair[("brew:formula/jq", "system:brew")].version == "1.7.1"

    # SYS-02 brew casks
    assert "brew:cask/orbstack" in by_id
    assert by_id["brew:cask/orbstack"].kind == "app"
    assert ("brew:cask/orbstack", "system:brew-cask") in usages_by_pair

    # SYS-03 brew taps
    assert "gh:example-org/homebrew-tools" in by_id
    assert by_id["gh:example-org/homebrew-tools"].kind == "repo"
    assert ("gh:example-org/homebrew-tools", "system:brew-tap") in usages_by_pair

    # SYS-04 mise
    assert "pkg:mise/python" in by_id
    assert by_id["pkg:mise/python"].kind == "package"
    assert ("pkg:mise/python", "system:mise") in usages_by_pair
    assert usages_by_pair[("pkg:mise/python", "system:mise")].version == "3.13.0"

    # SYS-05 uv tools
    assert "pkg:pypi/ruff" in by_id
    assert ("pkg:pypi/ruff", "system:uv-tool") in usages_by_pair
    assert usages_by_pair[("pkg:pypi/ruff", "system:uv-tool")].version == "0.8.0"

    # SYS-07 npm & bun globals
    assert ("pkg:npm/typescript", "system:npm-global") in usages_by_pair
    assert ("pkg:npm/tsx", "system:bun-global") in usages_by_pair

    # SYS-08 cargo
    assert ("pkg:cargo/fd-find", "system:cargo") in usages_by_pair
    assert ("pkg:cargo/rtk", "system:cargo") in usages_by_pair

    # SYS-09 go bins
    assert ("pkg:golang/golang.org/x/tools/gopls", "system:go") in usages_by_pair

    # SYS-10 gh extensions
    assert ("gh:dlvhdr/gh-dash", "system:gh-ext") in usages_by_pair

    # SYS-11 fish plugins
    assert ("gh:jorgebucaran/fisher", "system:fish") in usages_by_pair
    assert ("gh:patrickf1/fzf.fish", "system:fish") in usages_by_pair

    # SYS-13 /Applications + mas
    assert ("app:dev.kdrag0n.MacVirt", "system:apps") in usages_by_pair
    assert ("app:497799835", "system:mas") in usages_by_pair

    # SYS-16 launchd service + extra project root
    assert ("svc:launchd/com.example.llm-gateway", "system:launchd") in usages_by_pair
    assert by_id["svc:launchd/com.example.llm-gateway"].kind == "service"
    launchd_roots = [r for r in out.extra_roots if r.root_source == "launchd"]
    assert any(r.path.name == "llm-gateway" for r in launchd_roots)

    # SYS-18 nix flake.lock + dotfiles root
    assert ("nix:nixpkgs", "system:nix") in usages_by_pair
    dotfiles_roots = [r for r in out.extra_roots if r.root_source == "dotfiles"]
    assert any(r.path.name == ".dotfiles" for r in dotfiles_roots)

    # SYS-21 Raycast
    assert ("raycast:brew", "system:raycast") in usages_by_pair
    assert by_id["raycast:brew"].kind == "plugin"

    # SYS-22 macOS + Xcode CLT
    assert ("runtime:macos", "system:os") in usages_by_pair
    assert ("runtime:xcode-clt", "system:os") in usages_by_pair

    # A14 manual binary: bin:mytool recorded and NEVER executed
    assert "bin:mytool" in by_id
    assert by_id["bin:mytool"].kind == "cli"
    assert by_id["bin:mytool"].ecosystem == "manual"
    # ruff in .local/bin is owned by uv tool, so bin:ruff must not be emitted
    assert "bin:ruff" not in by_id
    called_bins = [argv[0] for argv, _env in p_tr.calls if argv]
    assert "mytool" not in called_bins
    assert not any("mytool" in " ".join(argv) for argv, _env in p_tr.calls)


def test_shell_history_keeps_command_names_only_and_drops_secrets(
    fixture_home: Path,
    replay_http: Callable[..., http.ReplayHttp],
    replay_gh: Callable[..., procs.ReplayProcs],
    replay_procs: Callable[..., procs.ReplayProcs],
) -> None:
    _ = fixture_home
    ctx, _p_tr = _make_ctx(replay_http, replay_gh, replay_procs)
    out = inventory.system.collect(ctx)

    hist_fps = [f for f in out.fingerprints if f.context == "system:shell-history"]
    assert hist_fps
    assert all(f.kind == "cmd" for f in hist_fps)

    hist_tokens = {f.token for f in hist_fps}
    assert "git" in hist_tokens
    assert "uv" in hist_tokens
    assert "pytest" in hist_tokens
    assert "rg" in hist_tokens

    # Never store arguments or secrets from shell history
    for f in out.fingerprints:
        assert "script.py" not in f.token
        assert "--secret" not in f.token

    all_serialized = " ".join(
        [*(f.token for f in out.fingerprints), *(u.entity_id for u in out.usages)]
    )
    for secret_name, canary in secret_samples.raw_canary_substrings().items():
        assert canary not in all_serialized, f"leaked {secret_name} in system inventory"
