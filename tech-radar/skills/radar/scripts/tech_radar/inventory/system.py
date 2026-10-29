"""System inventory collector (Spec 04 § System sources, Spec 12 A12, A14)."""

from __future__ import annotations

import json
import os
import plistlib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import fsguard
from ..errors import RadarError
from . import fingerprint as fp

if TYPE_CHECKING:
    from ..context import Ctx

_GITHUB_URL_RE = re.compile(
    r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git|/|$)"
)
_FISH_FUNC_RE = re.compile(r"^\s*function\s+([A-Za-z0-9_.-]+)")
_FISH_ABBR_RE = re.compile(r"^\s*abbr\s+(?:-[A-Za-z-]+\s+)*([A-Za-z0-9_.-]+)\s+(.+?)\s*$")
_UV_TOOL_HEADER_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s+v([0-9][^\s(]*)\s*(?:\(([^)]+)\))?")
_UV_TOOL_BIN_RE = re.compile(r"^\s*-\s+([A-Za-z0-9_.-]+)\s*(?:\(([^)]+)\))?")
_CARGO_PKG_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s+v([0-9][^\s:]*):")
_BUN_PKG_RE = re.compile(r"([@A-Za-z0-9_./-]+)@([0-9][A-Za-z0-9_.-]*)")
_MAS_LINE_RE = re.compile(r"^\s*(\d+)\s+(.+?)\s+\(([^)]+)\)\s*$")
_SHELL_SEP_RE = re.compile(r"(?:&&|\|\||;|\|)+")


@dataclass(frozen=True)
class EntityRecord:
    """Discovered entity metadata ready for SQLite upsert."""

    entity_id: str
    kind: str
    ecosystem: str | None
    name: str
    repo: str | None = None
    homepage: str | None = None
    quadrant: str | None = None
    visibility: str = "unknown"
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class UsageRecord:
    """Discovered entity usage in a specific context."""

    entity_id: str
    context: str
    version: str | None = None
    direct: int = 1
    attrs: dict[str, Any] = field(default_factory=dict)

    @property
    def source_file(self) -> str | None:
        """Return attrs['source_file'] when present (Spec 12 A27)."""
        val = self.attrs.get("source_file")
        return str(val) if val is not None else None

    @property
    def attrs_json(self) -> str | None:
        """Return serialized JSON string of attrs (or None if empty)."""
        if not self.attrs:
            return None
        return json.dumps(self.attrs, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class DiscoveredRoot:
    """Extra project root discovered from launchd plists or dotfiles."""

    path: Path
    root_source: str
    label: str | None = None


@dataclass
class SystemInventoryOutput:
    """Aggregated output from system.collect(ctx)."""

    entities: list[EntityRecord] = field(default_factory=list)
    usages: list[UsageRecord] = field(default_factory=list)
    fingerprints: list[fp.FingerprintRecord] = field(default_factory=list)
    extra_roots: list[DiscoveredRoot] = field(default_factory=list)
    known_clis: set[str] = field(default_factory=set)
    owned_binaries: set[str] = field(default_factory=set)
    cmd_bigrams: Counter[tuple[str, str]] = field(default_factory=Counter)
    dataless_skipped: int = 0


def _github_slug(url: str | None) -> str | None:
    if not url:
        return None
    m = _GITHUB_URL_RE.search(url)
    if not m:
        return None
    owner = m.group(1).lower()
    repo = m.group(2).lower()
    if repo.endswith(".git"):
        repo = repo[:-4]
    return f"{owner}/{repo}"


def _safe_run(
    ctx: Ctx,
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
) -> str | None:
    try:
        res = ctx.procs.run(argv, env=env)
    except RadarError:
        return None
    if res.returncode != 0:
        return None
    return res.stdout


def _collect_brew(ctx: Ctx, out: SystemInventoryOutput) -> None:
    brew_env = dict(os.environ)
    brew_env["HOMEBREW_NO_AUTO_UPDATE"] = "1"
    brew_env["HOMEBREW_NO_ANALYTICS"] = "1"

    leaves_out = _safe_run(ctx, ["brew", "leaves"], env=brew_env) or ""
    leaves = {line.strip() for line in leaves_out.splitlines() if line.strip()}

    info_out = _safe_run(ctx, ["brew", "info", "--json=v2", "--installed"], env=brew_env)
    if info_out:
        try:
            data = json.loads(info_out)
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict):
            for formula in data.get("formulae") or []:
                if not isinstance(formula, dict):
                    continue
                name = str(formula.get("name") or "").strip()
                if not name:
                    continue
                installed_list = formula.get("installed") or []
                on_request = any(
                    isinstance(inst, dict) and bool(inst.get("installed_on_request"))
                    for inst in installed_list
                )
                if not (on_request or name in leaves):
                    continue
                version: str | None = None
                if installed_list and isinstance(installed_list[0], dict):
                    version = str(installed_list[0].get("version") or "") or None
                if not version and isinstance(formula.get("versions"), dict):
                    version = str(formula["versions"].get("stable") or "") or None

                homepage = str(formula.get("homepage") or "") or None
                stable_url: str | None = None
                urls = formula.get("urls")
                if isinstance(urls, dict) and isinstance(urls.get("stable"), dict):
                    stable_url = str(urls["stable"].get("url") or "") or None

                gh_slug = _github_slug(homepage) or _github_slug(stable_url)
                aliases = (f"gh:{gh_slug}",) if gh_slug else ()
                entity_id = f"brew:formula/{name}"
                out.entities.append(
                    EntityRecord(
                        entity_id=entity_id,
                        kind="cli",
                        ecosystem="homebrew",
                        name=name,
                        repo=gh_slug,
                        homepage=homepage,
                        visibility="public",
                        aliases=aliases,
                    )
                )
                out.usages.append(
                    UsageRecord(
                        entity_id=entity_id,
                        context="system:brew",
                        version=version,
                        direct=1,
                    )
                )
                out.known_clis.add(name)
                out.owned_binaries.add(name)

            for cask in data.get("casks") or []:
                if not isinstance(cask, dict):
                    continue
                token = str(cask.get("token") or "").strip()
                if not token:
                    continue
                ver = str(cask.get("installed") or cask.get("version") or "") or None
                homepage = str(cask.get("homepage") or "") or None
                entity_id = f"brew:cask/{token}"
                out.entities.append(
                    EntityRecord(
                        entity_id=entity_id,
                        kind="app",
                        ecosystem="homebrew",
                        name=token,
                        homepage=homepage,
                        visibility="public",
                    )
                )
                out.usages.append(
                    UsageRecord(
                        entity_id=entity_id,
                        context="system:brew-cask",
                        version=ver,
                        direct=1,
                    )
                )

    taps_out = _safe_run(ctx, ["brew", "tap"], env=brew_env) or ""
    for raw_line in taps_out.splitlines():
        tap = raw_line.strip()
        if "/" not in tap:
            continue
        owner, tap_name = tap.split("/", 1)
        repo_name = tap_name if tap_name.startswith("homebrew-") else f"homebrew-{tap_name}"
        slug = f"{owner.lower()}/{repo_name.lower()}"
        entity_id = f"gh:{slug}"
        out.entities.append(
            EntityRecord(
                entity_id=entity_id,
                kind="repo",
                ecosystem="github",
                name=slug,
                repo=slug,
            )
        )
        out.usages.append(
            UsageRecord(
                entity_id=entity_id,
                context="system:brew-tap",
                version=None,
                direct=1,
            )
        )


def _collect_mise(ctx: Ctx, out: SystemInventoryOutput) -> None:
    mise_out = _safe_run(ctx, ["mise", "ls", "--json"])
    if not mise_out:
        return
    try:
        data = json.loads(mise_out)
    except json.JSONDecodeError:
        return
    if not isinstance(data, dict):
        return
    for tool_name, entries in data.items():
        name = str(tool_name).strip()
        if not name:
            continue
        version: str | None = None
        if isinstance(entries, list) and entries and isinstance(entries[0], dict):
            version = str(entries[0].get("version") or "") or None
        elif isinstance(entries, dict):
            version = str(entries.get("version") or "") or None
        entity_id = f"pkg:mise/{name}"
        out.entities.append(
            EntityRecord(
                entity_id=entity_id,
                kind="package",
                ecosystem="mise",
                name=name,
                visibility="public",
            )
        )
        out.usages.append(
            UsageRecord(
                entity_id=entity_id,
                context="system:mise",
                version=version,
                direct=1,
            )
        )
        out.known_clis.add(name)
        out.owned_binaries.add(name)


def _collect_uv_tools(ctx: Ctx, out: SystemInventoryOutput) -> None:
    uv_out = _safe_run(ctx, ["uv", "tool", "list", "--show-paths"])
    if not uv_out:
        return
    for line in uv_out.splitlines():
        hm = _UV_TOOL_HEADER_RE.match(line)
        if hm:
            pkg_name = hm.group(1).strip().lower()
            ver = hm.group(2).strip()
            entity_id = f"pkg:pypi/{pkg_name}"
            out.entities.append(
                EntityRecord(
                    entity_id=entity_id,
                    kind="package",
                    ecosystem="pypi",
                    name=pkg_name,
                )
            )
            out.usages.append(
                UsageRecord(
                    entity_id=entity_id,
                    context="system:uv-tool",
                    version=ver,
                    direct=1,
                )
            )
            out.known_clis.add(pkg_name)
            out.owned_binaries.add(pkg_name)
            continue
        bm = _UV_TOOL_BIN_RE.match(line)
        if bm:
            bin_name = bm.group(1).strip()
            out.known_clis.add(bin_name)
            out.owned_binaries.add(bin_name)


def _collect_npm_and_bun(ctx: Ctx, out: SystemInventoryOutput) -> None:
    npm_out = _safe_run(ctx, ["npm", "ls", "-g", "--json", "--depth=0"])
    if npm_out:
        try:
            data = json.loads(npm_out)
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict) and isinstance(data.get("dependencies"), dict):
            for pkg_name, meta in data["dependencies"].items():
                name = str(pkg_name).strip()
                if not name:
                    continue
                ver = str(meta.get("version") or "") or None if isinstance(meta, dict) else None
                entity_id = f"pkg:npm/{name}"
                out.entities.append(
                    EntityRecord(
                        entity_id=entity_id,
                        kind="package",
                        ecosystem="npm",
                        name=name,
                    )
                )
                out.usages.append(
                    UsageRecord(
                        entity_id=entity_id,
                        context="system:npm-global",
                        version=ver,
                        direct=1,
                    )
                )
                short_bin = name.split("/")[-1]
                out.known_clis.add(short_bin)
                out.owned_binaries.add(short_bin)

    bun_out = _safe_run(ctx, ["bun", "pm", "ls", "-g"])
    if bun_out:
        for line in bun_out.splitlines():
            m = _BUN_PKG_RE.search(line)
            if not m:
                continue
            name = m.group(1).strip()
            ver = m.group(2).strip()
            entity_id = f"pkg:npm/{name}"
            out.entities.append(
                EntityRecord(
                    entity_id=entity_id,
                    kind="package",
                    ecosystem="npm",
                    name=name,
                )
            )
            out.usages.append(
                UsageRecord(
                    entity_id=entity_id,
                    context="system:bun-global",
                    version=ver,
                    direct=1,
                )
            )
            short_bin = name.split("/")[-1]
            out.known_clis.add(short_bin)
            out.owned_binaries.add(short_bin)


def _collect_cargo(ctx: Ctx, out: SystemInventoryOutput) -> None:
    cargo_out = _safe_run(ctx, ["cargo", "install", "--list"])
    if not cargo_out:
        return
    for line in cargo_out.splitlines():
        m = _CARGO_PKG_RE.match(line)
        if m:
            crate = m.group(1).strip()
            ver = m.group(2).strip()
            entity_id = f"pkg:cargo/{crate}"
            out.entities.append(
                EntityRecord(
                    entity_id=entity_id,
                    kind="package",
                    ecosystem="cargo",
                    name=crate,
                )
            )
            out.usages.append(
                UsageRecord(
                    entity_id=entity_id,
                    context="system:cargo",
                    version=ver,
                    direct=1,
                )
            )
            out.known_clis.add(crate)
            out.owned_binaries.add(crate)
        elif line.startswith("    "):
            bin_name = line.strip().split()[0] if line.strip() else ""
            if bin_name:
                out.known_clis.add(bin_name)
                out.owned_binaries.add(bin_name)


def _collect_go_bins(ctx: Ctx, out: SystemInventoryOutput) -> None:
    go_bin_dir = ctx.paths.home / "go" / "bin"
    if not go_bin_dir.is_dir():
        return
    for entry in sorted(go_bin_dir.iterdir()):
        if not entry.is_file():
            continue
        out.owned_binaries.add(entry.name)
        out.known_clis.add(entry.name)
        go_out = _safe_run(ctx, ["go", "version", "-m", str(entry)])
        if not go_out:
            continue
        mod_path: str | None = None
        mod_ver: str | None = None
        for line in go_out.splitlines():
            parts = line.strip().split("\t")
            if len(parts) >= 3 and parts[0] == "mod":
                mod_path = parts[1].strip()
                mod_ver = parts[2].strip().lstrip("v")
                break
            if len(parts) >= 2 and parts[0] == "path" and not mod_path:
                mod_path = parts[1].strip()
        if mod_path:
            entity_id = f"pkg:golang/{mod_path}"
            out.entities.append(
                EntityRecord(
                    entity_id=entity_id,
                    kind="package",
                    ecosystem="golang",
                    name=mod_path,
                    visibility="public",
                )
            )
            out.usages.append(
                UsageRecord(
                    entity_id=entity_id,
                    context="system:go",
                    version=mod_ver,
                    direct=1,
                )
            )


def _collect_gh_extensions(ctx: Ctx, out: SystemInventoryOutput) -> None:
    gh_out = _safe_run(ctx, ["gh", "extension", "list"])
    if not gh_out:
        return
    for line in gh_out.splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split("\t") if p.strip()]
        if len(parts) < 2:
            parts = line.split()
        repo_slug: str | None = None
        version: str | None = None
        for part in parts:
            if "/" in part and not part.startswith("http"):
                repo_slug = part.lower()
            elif re.match(r"^v?\d+\.\d+", part):
                version = part.lstrip("v")
        if repo_slug:
            entity_id = f"gh:{repo_slug}"
            out.entities.append(
                EntityRecord(
                    entity_id=entity_id,
                    kind="repo",
                    ecosystem="github",
                    name=repo_slug,
                    repo=repo_slug,
                )
            )
            out.usages.append(
                UsageRecord(
                    entity_id=entity_id,
                    context="system:gh-ext",
                    version=version,
                    direct=1,
                )
            )


def _collect_fish(ctx: Ctx, out: SystemInventoryOutput) -> None:
    fish_dir = ctx.paths.home / ".config" / "fish"
    if not fish_dir.is_dir():
        return

    plugins_file = fish_dir / "fish_plugins"
    if plugins_file.is_file():
        try:
            st = plugins_file.stat()
            if fsguard.is_dataless(st):
                out.dataless_skipped += 1
            else:
                for raw_line in plugins_file.read_text(
                    encoding="utf-8", errors="ignore"
                ).splitlines():
                    slug = raw_line.strip().lower()
                    if not slug or slug.startswith("#") or "/" not in slug:
                        continue
                    entity_id = f"gh:{slug}"
                    out.entities.append(
                        EntityRecord(
                            entity_id=entity_id,
                            kind="repo",
                            ecosystem="github",
                            name=slug,
                            repo=slug,
                        )
                    )
                    out.usages.append(
                        UsageRecord(
                            entity_id=entity_id,
                            context="system:fish",
                            version=None,
                            direct=1,
                        )
                    )
        except OSError:
            pass

    fish_files: list[Path] = []
    cfg_file = fish_dir / "config.fish"
    if cfg_file.is_file():
        fish_files.append(cfg_file)
    for sub_name in ("conf.d", "functions"):
        sub_dir = fish_dir / sub_name
        if sub_dir.is_dir():
            fish_files.extend(sorted(sub_dir.glob("*.fish")))

    for fpath in fish_files:
        try:
            st = fpath.stat()
            if fsguard.is_dataless(st):
                out.dataless_skipped += 1
                continue
            text = fpath.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for line in text.splitlines():
            fm = _FISH_FUNC_RE.match(line)
            if fm:
                fname = fm.group(1).strip()
                out.fingerprints.append(
                    fp.FingerprintRecord(context="system:fish", kind="cmd", token=fname)
                )
            am = _FISH_ABBR_RE.match(line)
            if am:
                aname = am.group(1).strip()
                expansion = am.group(2).strip().strip("'\"")
                out.fingerprints.append(
                    fp.FingerprintRecord(context="system:fish", kind="cmd", token=aname)
                )
                out.fingerprints.extend(
                    fp.extract_cli_fingerprints(
                        expansion,
                        "system:fish",
                        out.known_clis,
                        emit_cmd=True,
                    )
                )

        out.fingerprints.extend(
            fp.extract_cli_fingerprints(
                text,
                "system:fish",
                out.known_clis,
                emit_cmd=False,
            )
        )


def _collect_shell_history(ctx: Ctx, out: SystemInventoryOutput) -> None:
    """Parse fish and zsh history within 180 days, keeping ONLY command names (never args)."""
    cutoff_ts = int((ctx.now - timedelta(days=180)).timestamp())
    allowed_cmds = out.known_clis | fp.DEFAULT_KNOWN_CLIS
    cmd_counts: Counter[str] = Counter()
    sequence_cmds: list[str] = []

    fish_hist = ctx.paths.home / ".local" / "share" / "fish" / "fish_history"
    if fish_hist.is_file():
        try:
            st = fish_hist.stat()
            if fsguard.is_dataless(st):
                out.dataless_skipped += 1
            else:
                lines = fish_hist.read_text(encoding="utf-8", errors="ignore").splitlines()
                pending_cmd: str | None = None
                pending_when: int | None = None

                def _record_entry(raw_cmd: str | None, when_ts: int | None) -> None:
                    if not raw_cmd:
                        return
                    if when_ts is not None and when_ts < cutoff_ts:
                        return
                    for seg in _SHELL_SEP_RE.split(raw_cmd):
                        cname = fp.extract_command_name(seg)
                        if cname and cname in allowed_cmds:
                            cmd_counts[cname] += 1
                            sequence_cmds.append(cname)

                for line in lines:
                    if line.startswith("- cmd:"):
                        if pending_cmd is not None:
                            _record_entry(pending_cmd, pending_when)
                        pending_cmd = line[len("- cmd:") :].strip()
                        pending_when = None
                    elif line.strip().startswith("when:"):
                        val = line.strip()[len("when:") :].strip()
                        try:
                            pending_when = int(val)
                        except ValueError:
                            pending_when = None
                if pending_cmd is not None:
                    _record_entry(pending_cmd, pending_when)
        except OSError:
            pass

    zsh_hist = ctx.paths.home / ".zsh_history"
    if zsh_hist.is_file():
        try:
            st = zsh_hist.stat()
            if fsguard.is_dataless(st):
                out.dataless_skipped += 1
            else:
                for line in zsh_hist.read_text(encoding="utf-8", errors="ignore").splitlines():
                    raw_cmd = line
                    when_ts: int | None = None
                    if line.startswith(": ") and ";" in line:
                        meta, raw_cmd = line[2:].split(";", 1)
                        ts_part = meta.split(":", 1)[0].strip()
                        try:
                            when_ts = int(ts_part)
                        except ValueError:
                            when_ts = None
                    if when_ts is not None and when_ts < cutoff_ts:
                        continue
                    for seg in _SHELL_SEP_RE.split(raw_cmd):
                        cname = fp.extract_command_name(seg)
                        if cname and cname in allowed_cmds:
                            cmd_counts[cname] += 1
                            sequence_cmds.append(cname)
        except OSError:
            pass

    for cname, cnt in sorted(cmd_counts.items()):
        out.fingerprints.append(
            fp.FingerprintRecord(
                context="system:shell-history",
                kind="cmd",
                token=cname,
                count=cnt,
            )
        )

    for idx in range(len(sequence_cmds) - 1):
        c1 = sequence_cmds[idx]
        c2 = sequence_cmds[idx + 1]
        if c1 and c2:
            out.cmd_bigrams[(c1, c2)] += 1


def _collect_apps_and_mas(ctx: Ctx, out: SystemInventoryOutput) -> None:
    app_dirs = [ctx.paths.home / "Applications"]
    if not os.environ.get("TECH_RADAR_HOME") and not os.environ.get("TECH_RADAR_REPLAY_DIR"):
        app_dirs.append(Path("/Applications"))

    for app_dir in app_dirs:
        if not app_dir.is_dir():
            continue
        for app_bundle in sorted(app_dir.glob("*.app")):
            info_plist = app_bundle / "Contents" / "Info.plist"
            if not info_plist.is_file():
                continue
            bundle_id: str | None = None
            version: str | None = None

            if " " not in str(info_plist):
                bid_out = _safe_run(
                    ctx,
                    ["defaults", "read", str(info_plist), "CFBundleIdentifier"],
                )
                ver_out = _safe_run(
                    ctx,
                    ["defaults", "read", str(info_plist), "CFBundleShortVersionString"],
                )
                if bid_out and bid_out.strip():
                    bundle_id = bid_out.strip()
                if ver_out and ver_out.strip():
                    version = ver_out.strip()

            if not bundle_id:
                try:
                    plist_data = plistlib.loads(info_plist.read_bytes())
                    if isinstance(plist_data, dict):
                        bundle_id = str(plist_data.get("CFBundleIdentifier") or "").strip() or None
                        version = (
                            version
                            or str(
                                plist_data.get("CFBundleShortVersionString")
                                or plist_data.get("CFBundleVersion")
                                or ""
                            ).strip()
                            or None
                        )
                except Exception:
                    pass

            if bundle_id:
                entity_id = f"app:{bundle_id}"
                out.entities.append(
                    EntityRecord(
                        entity_id=entity_id,
                        kind="app",
                        ecosystem="macos",
                        name=bundle_id,
                        visibility="public",
                    )
                )
                out.usages.append(
                    UsageRecord(
                        entity_id=entity_id,
                        context="system:apps",
                        version=version,
                        direct=1,
                    )
                )

    mas_out = _safe_run(ctx, ["mas", "list"])
    if mas_out:
        for line in mas_out.splitlines():
            m = _MAS_LINE_RE.match(line)
            if not m:
                continue
            app_id = m.group(1).strip()
            app_name = m.group(2).strip()
            ver = m.group(3).strip()
            entity_id = f"app:{app_id}"
            out.entities.append(
                EntityRecord(
                    entity_id=entity_id,
                    kind="app",
                    ecosystem="macos",
                    name=app_name,
                    visibility="public",
                )
            )
            out.usages.append(
                UsageRecord(
                    entity_id=entity_id,
                    context="system:mas",
                    version=ver,
                    direct=1,
                )
            )


def _extract_launchd_project_roots(
    plist_data: dict[str, Any],
    home: Path,
) -> list[Path]:
    roots: list[Path] = []
    work_dir = plist_data.get("WorkingDirectory")
    if isinstance(work_dir, str) and work_dir.strip():
        expanded = work_dir.replace("{HOME}", str(home)).replace("~", str(home))
        roots.append(Path(expanded))

    prog_args = plist_data.get("ProgramArguments")
    if isinstance(prog_args, list):
        str_args = [str(a).replace("{HOME}", str(home)).replace("~", str(home)) for a in prog_args]
        for idx, tok in enumerate(str_args):
            if tok in ("--project", "--directory") and idx + 1 < len(str_args):
                roots.append(Path(str_args[idx + 1]))
            elif tok.startswith("--project=") or tok.startswith("--directory="):
                roots.append(Path(tok.split("=", 1)[1]))

    deduped: list[Path] = []
    seen: set[str] = set()
    for r in roots:
        key = str(r)
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    return deduped


def _collect_launchd(ctx: Ctx, out: SystemInventoryOutput) -> None:
    la_dir = ctx.paths.launch_agents
    if not la_dir.is_dir():
        return

    for plist_path in sorted(la_dir.glob("*.plist")):
        try:
            st = plist_path.stat()
            if fsguard.is_dataless(st):
                out.dataless_skipped += 1
                continue
            raw_bytes = plist_path.read_bytes()
            plist_data = plistlib.loads(raw_bytes)
        except Exception:
            continue
        if not isinstance(plist_data, dict):
            continue

        label = str(plist_data.get("Label") or plist_path.stem).strip()
        if not label:
            continue
        entity_id = f"svc:launchd/{label}"
        out.entities.append(
            EntityRecord(
                entity_id=entity_id,
                kind="service",
                ecosystem="launchd",
                name=label,
                visibility="private",
            )
        )
        out.usages.append(
            UsageRecord(
                entity_id=entity_id,
                context="system:launchd",
                version=None,
                direct=1,
            )
        )

        prog_args = plist_data.get("ProgramArguments")
        if isinstance(prog_args, list) and prog_args:
            str_args = [str(x) for x in prog_args if str(x).strip()]
            if str_args:
                prog_base = os.path.basename(str_args[0])
                if re.match(r"^[A-Za-z0-9_.-]+$", prog_base):
                    out.fingerprints.append(
                        fp.FingerprintRecord(
                            context="system:launchd",
                            kind="cmd",
                            token=prog_base,
                        )
                    )
                out.fingerprints.extend(
                    fp.extract_cli_tokens_from_argv(
                        str_args,
                        f"launchd:{label}",
                        out.known_clis,
                        emit_cmd=True,
                    )
                )

        for root_path in _extract_launchd_project_roots(plist_data, ctx.paths.home):
            out.extra_roots.append(
                DiscoveredRoot(path=root_path, root_source="launchd", label=label)
            )


def _collect_git_config(ctx: Ctx, out: SystemInventoryOutput) -> None:
    try:
        res = ctx.procs.git(["config", "--global", "--list"])
    except RadarError:
        return
    if res.returncode != 0:
        return

    for line in res.stdout.splitlines():
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key_low = key.strip().lower()
        val_str = val.strip()
        if not val_str:
            continue

        if key_low in ("core.pager", "diff.tool", "merge.tool"):
            prog = fp.extract_command_name(val_str)
            if prog:
                out.fingerprints.append(
                    fp.FingerprintRecord(
                        context="system:git-config",
                        kind="cmd",
                        token=prog,
                    )
                )
        elif key_low.startswith("alias."):
            if val_str.startswith("!"):
                prog = fp.extract_command_name(val_str[1:])
                if prog:
                    out.fingerprints.append(
                        fp.FingerprintRecord(
                            context="system:git-config",
                            kind="cmd",
                            token=prog,
                        )
                    )


def _collect_dotfiles_and_nix(ctx: Ctx, out: SystemInventoryOutput) -> None:
    for candidate in (
        ctx.paths.home / ".dotfiles",
        ctx.paths.home / ".local" / "share" / "chezmoi",
    ):
        if candidate.is_dir():
            out.extra_roots.append(DiscoveredRoot(path=candidate, root_source="dotfiles"))

    flake_locks = [
        ctx.paths.home / ".config" / "nix" / "flake.lock",
        ctx.paths.home / ".dotfiles" / "flake.lock",
        ctx.paths.home / ".nixpkgs" / "flake.lock",
    ]
    for lock_path in flake_locks:
        if not lock_path.is_file():
            continue
        try:
            st = lock_path.stat()
            if fsguard.is_dataless(st):
                out.dataless_skipped += 1
                continue
            data = json.loads(lock_path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("nodes"), dict):
            continue
        for node_name, node_val in data["nodes"].items():
            if node_name == "root" or not isinstance(node_val, dict):
                continue
            raw_locked = node_val.get("locked")
            locked: dict[str, Any] = raw_locked if isinstance(raw_locked, dict) else {}
            raw_original = node_val.get("original")
            original: dict[str, Any] = raw_original if isinstance(raw_original, dict) else {}
            rev = str(locked.get("rev") or "")[:12] or None
            owner = str(original.get("owner") or locked.get("owner") or "").strip()
            repo = str(original.get("repo") or locked.get("repo") or "").strip()
            gh_slug = f"{owner.lower()}/{repo.lower()}" if owner and repo else None
            entity_id = f"nix:{node_name}"
            out.entities.append(
                EntityRecord(
                    entity_id=entity_id,
                    kind="package",
                    ecosystem="nix",
                    name=str(node_name),
                    repo=gh_slug,
                    visibility="public",
                )
            )
            out.usages.append(
                UsageRecord(
                    entity_id=entity_id,
                    context="system:nix",
                    version=rev,
                    direct=1,
                )
            )


def _collect_raycast(ctx: Ctx, out: SystemInventoryOutput) -> None:
    raycast_dir = (
        ctx.paths.home / "Library" / "Application Support" / "com.raycast.macos" / "extensions"
    )
    if not raycast_dir.is_dir():
        return
    for pkg_json in sorted(raycast_dir.glob("*/package.json")):
        try:
            st = pkg_json.stat()
            if fsguard.is_dataless(st):
                out.dataless_skipped += 1
                continue
            data = json.loads(pkg_json.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        name = str(data.get("name") or "").strip()
        if not name:
            continue
        ver = str(data.get("version") or "") or None
        entity_id = f"raycast:{name}"
        out.entities.append(
            EntityRecord(
                entity_id=entity_id,
                kind="plugin",
                ecosystem="raycast",
                name=name,
                visibility="public",
            )
        )
        out.usages.append(
            UsageRecord(
                entity_id=entity_id,
                context="system:raycast",
                version=ver,
                direct=1,
            )
        )


def _collect_macos_xcode(ctx: Ctx, out: SystemInventoryOutput) -> None:
    sw_out = _safe_run(ctx, ["sw_vers"])
    if sw_out:
        prod_ver: str | None = None
        for line in sw_out.splitlines():
            if line.startswith("ProductVersion:"):
                prod_ver = line.split(":", 1)[1].strip()
                break
        if prod_ver:
            out.entities.append(
                EntityRecord(
                    entity_id="runtime:macos",
                    kind="runtime",
                    ecosystem="os",
                    name="macos",
                    visibility="public",
                )
            )
            out.usages.append(
                UsageRecord(
                    entity_id="runtime:macos",
                    context="system:os",
                    version=prod_ver,
                    direct=1,
                )
            )

    _ = _safe_run(ctx, ["xcode-select", "-p"])
    pkg_out = _safe_run(
        ctx,
        ["pkgutil", "--pkg-info=com.apple.pkg.CLTools_Executables"],
    )
    if pkg_out:
        clt_ver: str | None = None
        for line in pkg_out.splitlines():
            if line.lower().startswith("version:"):
                clt_ver = line.split(":", 1)[1].strip()
                break
        out.entities.append(
            EntityRecord(
                entity_id="runtime:xcode-clt",
                kind="runtime",
                ecosystem="os",
                name="xcode-clt",
                visibility="public",
            )
        )
        out.usages.append(
            UsageRecord(
                entity_id="runtime:xcode-clt",
                context="system:os",
                version=clt_ver,
                direct=1,
            )
        )


def _collect_manual_binaries(ctx: Ctx, out: SystemInventoryOutput) -> None:
    """Scan ~/.local/bin, ~/bin, /usr/local/bin for non-package-manager binaries (A14).

    Never executes discovered binaries.
    """
    bin_dirs = [
        ctx.paths.home / ".local" / "bin",
        ctx.paths.home / "bin",
        ctx.paths.home / "usr" / "local" / "bin",
    ]
    if not os.environ.get("TECH_RADAR_HOME") and not os.environ.get("TECH_RADAR_REPLAY_DIR"):
        bin_dirs.append(Path("/usr/local/bin"))

    seen_names: set[str] = set()
    for bdir in bin_dirs:
        if not bdir.is_dir():
            continue
        for entry in sorted(bdir.iterdir()):
            name = entry.name
            if name.startswith(".") or name in seen_names or name in out.owned_binaries:
                continue
            try:
                if not entry.is_file() or not os.access(entry, os.X_OK):
                    continue
            except OSError:
                continue
            seen_names.add(name)
            entity_id = f"bin:{name}"
            out.entities.append(
                EntityRecord(
                    entity_id=entity_id,
                    kind="cli",
                    ecosystem="manual",
                    name=name,
                )
            )
            out.usages.append(
                UsageRecord(
                    entity_id=entity_id,
                    context="system:apps",
                    version=None,
                    direct=1,
                )
            )
            out.known_clis.add(name)


def collect(ctx: Ctx) -> SystemInventoryOutput:
    """Run all Spec 04 system inventory collectors and return aggregated records."""
    out = SystemInventoryOutput()
    out.known_clis.update(fp.DEFAULT_KNOWN_CLIS)

    _collect_brew(ctx, out)
    _collect_mise(ctx, out)
    _collect_uv_tools(ctx, out)
    _collect_npm_and_bun(ctx, out)
    _collect_cargo(ctx, out)
    _collect_go_bins(ctx, out)
    _collect_gh_extensions(ctx, out)
    _collect_apps_and_mas(ctx, out)
    _collect_launchd(ctx, out)
    _collect_git_config(ctx, out)
    _collect_dotfiles_and_nix(ctx, out)
    _collect_raycast(ctx, out)
    _collect_macos_xcode(ctx, out)
    _collect_manual_binaries(ctx, out)
    _collect_fish(ctx, out)
    _collect_shell_history(ctx, out)

    out.fingerprints = fp.merge_fingerprints(out.fingerprints)
    return out
