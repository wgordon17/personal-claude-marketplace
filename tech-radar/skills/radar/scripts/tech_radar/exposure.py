"""Read-only exposure check for compromised dependencies (Spec 07 §2, Spec 12 A2)."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import fsguard, procs, redact
from .errors import RadarError, UnsafeArg

if TYPE_CHECKING:
    from .context import Ctx

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")

STEALER_ROTATION_CHECKLIST: tuple[str, ...] = (
    "Rotate GitHub personal access tokens, OAuth tokens, and deploy keys.",
    "Rotate PyPI, npm, and container registry publishing tokens.",
    "Rotate cloud provider credentials (AWS, GCP, Azure) and API keys in environment variables.",
    "Inspect and rotate SSH and GPG keys if agent forwarding or unlocked keys were active.",
)

PTH_LOADER_CHECKLIST: tuple[str, ...] = (
    (
        "Inspect all virtual environments (`site-packages/*.pth` and `sitecustomize.py`) "
        "for startup loaders."
    ),
    "Delete affected `.venv` directories and recreate them from a clean, verified lockfile.",
    "Purge the local package wheel cache (`uv cache clean`).",
)


@dataclass
class ExposureReport:
    """Structured result of an exposure check."""

    purl: str
    versions: list[str]
    report_path: Path
    commits: list[dict[str, str]] = field(default_factory=list)
    cached_wheels: list[str] = field(default_factory=list)
    pth_files: list[dict[str, str]] = field(default_factory=list)
    rotation_checklist: list[str] = field(default_factory=list)


def build_rotation_checklist(
    text_or_class: str = "",
    *,
    has_pth: bool = False,
) -> list[str]:
    """Derive a rotation and remediation checklist from the advisory payload class (SEC-15)."""
    _ = (text_or_class, has_pth)
    return [*STEALER_ROTATION_CHECKLIST, *PTH_LOADER_CHECKLIST]


def _parse_iso_dt(raw: str | None, fallback: datetime) -> datetime:
    if not raw:
        return fallback
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    except ValueError:
        return fallback


def _purl_package_name(purl: str) -> str:
    raw = purl.strip()
    if "/" in raw:
        return raw.rsplit("/", 1)[-1]
    if ":" in raw:
        return raw.rsplit(":", 1)[-1]
    return raw


def _candidate_project_dirs_and_lockfiles(
    ctx: Ctx,
    purl: str,
) -> list[tuple[str, Path, Path | None]]:
    """Return `(project_key, project_dir, lockfile_path)` tuples for projects using `purl`."""
    out: list[tuple[str, Path, Path | None]] = []
    seen_dirs: set[str] = set()

    usage_rows = ctx.db.execute(
        "SELECT context, attrs_json FROM usages WHERE entity_id = ?",
        (purl,),
    ).fetchall()

    for urow in usage_rows:
        uctx = str(urow["context"] or "")
        source_file: Path | None = None
        if urow["attrs_json"]:
            try:
                attrs = json.loads(str(urow["attrs_json"]))
                if isinstance(attrs, dict) and attrs.get("source_file"):
                    sf = Path(str(attrs["source_file"]))
                    if sf.is_file():
                        source_file = sf
            except json.JSONDecodeError:
                pass

        proj_dir: Path | None = None
        proj_key = uctx
        if uctx.startswith("project:"):
            proj_key = uctx[len("project:") :]
            prow = ctx.db.execute(
                "SELECT main_worktree, display_path FROM projects WHERE project_key = ?",
                (proj_key,),
            ).fetchone()
            if prow is not None:
                cand = Path(str(prow["main_worktree"] or prow["display_path"]))
                if cand.is_dir():
                    proj_dir = cand
        if proj_dir is None and source_file is not None:
            proj_dir = source_file.parent
        if (
            (proj_dir is None or not proj_dir.is_dir())
            and "/" in proj_key
            and not proj_key.startswith("local:")
        ):
            repo_tail = proj_key.rstrip("/").rsplit("/", 1)[-1]
            for pr_root in ctx.paths.project_roots:
                cand_dir = pr_root / repo_tail
                if cand_dir.is_dir():
                    proj_dir = cand_dir
                    break

        if proj_dir is not None and proj_dir.is_dir():
            key_s = str(proj_dir.resolve())
            if key_s not in seen_dirs:
                seen_dirs.add(key_s)
                out.append((proj_key, proj_dir, source_file))

    all_proj_rows = ctx.db.execute(
        "SELECT project_key, main_worktree, display_path FROM projects WHERE active = 1"
    ).fetchall()
    for prow in all_proj_rows:
        proj_key = str(prow["project_key"])
        cand = Path(str(prow["main_worktree"] or prow["display_path"]))
        if not cand.is_dir() and "/" in proj_key and not proj_key.startswith("local:"):
            repo_tail = proj_key.rstrip("/").rsplit("/", 1)[-1]
            for pr_root in ctx.paths.project_roots:
                cand_dir = pr_root / repo_tail
                if cand_dir.is_dir():
                    cand = cand_dir
                    break
        if not cand.is_dir():
            continue
        key_s = str(cand.resolve())
        if key_s in seen_dirs:
            continue
        for lock_name in ("uv.lock", "package-lock.json", "Cargo.lock", "pyproject.toml"):
            lf = cand / lock_name
            if lf.is_file():
                seen_dirs.add(key_s)
                out.append((proj_key, cand, lf))
                break

    return out


def _scan_git_lockfile_history(
    ctx: Ctx,
    proj_key: str,
    proj_dir: Path,
    lockfile: Path | None,
    versions: Sequence[str],
) -> list[dict[str, str]]:
    if not (proj_dir / ".git").exists():
        return []
    target_lock = lockfile
    if target_lock is None or not target_lock.is_file():
        for name in ("uv.lock", "package-lock.json", "Cargo.lock", "pyproject.toml"):
            cand = proj_dir / name
            if cand.is_file():
                target_lock = cand
                break
    if target_lock is None or not target_lock.is_file():
        return []

    try:
        rel_lock = str(target_lock.resolve().relative_to(proj_dir.resolve()))
    except ValueError:
        rel_lock = target_lock.name

    if not procs.is_safe_arg(rel_lock):
        return []

    commits: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for ver in versions:
        clean_ver = ver.strip()
        if not clean_ver or not procs.is_safe_arg(clean_ver):
            continue
        try:
            res = ctx.procs.git(
                ["log", f"-S{clean_ver}", "--format=%H%x09%cI", "--", rel_lock],
                proj_dir,
            )
        except RadarError:
            continue
        if res.returncode != 0 or not res.stdout.strip():
            continue
        for line in res.stdout.splitlines():
            parts = line.strip().split("\t", 1)
            if not parts or not parts[0]:
                continue
            sha = parts[0].strip()
            committed_at = parts[1].strip() if len(parts) > 1 else ""
            if (sha, clean_ver) not in seen:
                seen.add((sha, clean_ver))
                commits.append(
                    {
                        "project_key": proj_key,
                        "commit": sha,
                        "committed_at": committed_at,
                        "version": clean_ver,
                        "lockfile": rel_lock,
                    }
                )
    return commits


def _scan_wheel_cache(
    ctx: Ctx,
    pkg_name: str,
    versions: Sequence[str],
) -> list[str]:
    norm_hyphen = re.sub(r"[-_.]+", "-", pkg_name.lower())
    norm_under = re.sub(r"[-_.]+", "_", pkg_name.lower())
    prefixes: set[str] = set()
    for ver in versions:
        clean_ver = ver.strip().lower()
        if clean_ver:
            prefixes.add(f"{norm_hyphen}-{clean_ver}-")
            prefixes.add(f"{norm_under}-{clean_ver}-")
            prefixes.add(f"{norm_hyphen}-{clean_ver}.")
            prefixes.add(f"{norm_under}-{clean_ver}.")

    if not prefixes:
        return []

    cache_roots = (
        ctx.paths.home / ".cache" / "uv",
        ctx.paths.home / "Library" / "Caches" / "uv",
    )
    hits: list[str] = []
    for root in cache_roots:
        if not root.is_dir():
            continue
        root_hits: list[str] = []
        try:
            for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
                for fname in filenames:
                    low_name = fname.lower()
                    if any(low_name.startswith(pref) for pref in prefixes):
                        cand = Path(dirpath) / fname
                        if cand.is_file():
                            root_hits.append(str(cand))
        except OSError:
            continue
        hits.extend(sorted(root_hits))
    return hits


def _scan_venv_startup_files(
    ctx: Ctx,
    proj_dirs: Sequence[Path],
    ref_dt: datetime,
) -> list[dict[str, str]]:
    venv_roots: list[Path] = []
    seen_roots: set[str] = set()

    candidates = [
        *[d / ".venv" for d in proj_dirs],
        ctx.paths.home / ".config" / "llm-gateway" / ".venv",
        ctx.paths.home / ".local" / "share" / "uv" / "tools",
    ]
    for cand in candidates:
        if cand.is_dir():
            resolved = str(cand.resolve())
            if resolved not in seen_roots:
                seen_roots.add(resolved)
                venv_roots.append(cand)

    findings: list[dict[str, str]] = []
    seen_files: set[str] = set()
    window = timedelta(days=7)

    for vroot in venv_roots:
        site_pkg_dirs: list[Path] = []
        try:
            for dirpath, dirnames, _filenames in os.walk(vroot, followlinks=False):
                curr = Path(dirpath)
                if curr.name == "site-packages":
                    site_pkg_dirs.append(curr)
                    dirnames[:] = []
                else:
                    dirnames.sort()
        except OSError:
            continue
        for sp_dir in sorted(site_pkg_dirs):
            try:
                entries = sorted(sp_dir.iterdir(), key=lambda p: p.name)
            except OSError:
                continue
            for entry in entries:
                if not entry.is_file() or entry.is_symlink():
                    continue
                if not (entry.name.endswith(".pth") or entry.name == "sitecustomize.py"):
                    continue
                resolved_f = str(entry.resolve())
                if resolved_f in seen_files:
                    continue
                try:
                    st = os.stat(entry)
                    raw_bytes = entry.read_bytes()
                except OSError:
                    continue
                mtime_dt = datetime.fromtimestamp(st.st_mtime, tz=UTC)
                if abs(ref_dt - mtime_dt) <= window:
                    seen_files.add(resolved_f)
                    digest = hashlib.sha256(raw_bytes).hexdigest()
                    findings.append(
                        {
                            "path": str(entry),
                            "mtime": mtime_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "sha256": digest,
                        }
                    )
    return findings


def _render_exposure_markdown(
    *,
    purl: str,
    versions: Sequence[str],
    advisory_id: str | None,
    checked_at: str,
    commits: Sequence[dict[str, str]],
    cached_wheels: Sequence[str],
    pth_files: Sequence[dict[str, str]],
    checklist: Sequence[str],
) -> str:
    ver_str = ", ".join(versions) if versions else "all"
    lines: list[str] = [
        f"# Exposure Report: {purl}",
        "",
        f"- **Package:** `{purl}`",
        f"- **Checked versions:** `{ver_str}`",
        f"- **Checked at:** `{checked_at}`",
    ]
    if advisory_id:
        lines.append(f"- **Advisory:** `{advisory_id}`")

    lines.extend(["", "## Lockfile Git History", ""])
    if commits:
        for c in commits:
            lines.append(
                f"- `{c['commit']}` ({c['committed_at']}) in `{c['project_key']}` "
                f"(`{c['lockfile']}`, version `{c['version']}`)"
            )
    else:
        lines.append("- No lockfile commits found for the specified versions.")

    lines.extend(["", "## Cached Wheels", ""])
    if cached_wheels:
        for w in cached_wheels:
            lines.append(f"- `{w}`")
    else:
        lines.append("- No matching wheels found in local uv cache.")

    lines.extend(["", "## Virtualenv Startup Files (`*.pth` / `sitecustomize.py`)", ""])
    if pth_files:
        for pf in pth_files:
            lines.append(f"- `{pf['path']}` (mtime: `{pf['mtime']}`, sha256: `{pf['sha256']}`)")
    else:
        lines.append("- No suspicious `.pth` or `sitecustomize.py` files modified within ±7 days.")

    lines.extend(["", "## Rotation & Remediation Checklist", ""])
    for item in checklist:
        lines.append(f"- [ ] {item}")

    return redact.scrub("\n".join(lines) + "\n")


def check(
    ctx: Ctx,
    purl: str,
    *,
    versions: Sequence[str] = (),
    alert_id: int | str | None = None,
    advisory_id: str | None = None,
    published_at: str | None = None,
    payload_text: str = "",
) -> ExposureReport:
    """Run read-only SEC-03 exposure checks and write `{state}/exposure/<id>.md` (Spec 12 A2)."""
    clean_purl = purl.strip()
    if not clean_purl or not procs.is_safe_arg(clean_purl):
        raise UnsafeArg(f"unsafe purl argument: {purl!r}")

    clean_versions = [v.strip() for v in versions if v and v.strip()]
    for v in clean_versions:
        if not procs.is_safe_arg(v):
            raise UnsafeArg(f"unsafe version argument: {v!r}")

    pkg_name = _purl_package_name(clean_purl)
    ref_dt = _parse_iso_dt(published_at, ctx.now)

    proj_items = _candidate_project_dirs_and_lockfiles(ctx, clean_purl)
    commits: list[dict[str, str]] = []
    proj_dirs: list[Path] = []
    for proj_key, proj_dir, lockfile in proj_items:
        proj_dirs.append(proj_dir)
        if clean_versions:
            commits.extend(
                _scan_git_lockfile_history(ctx, proj_key, proj_dir, lockfile, clean_versions)
            )

    cached_wheels = _scan_wheel_cache(ctx, pkg_name, clean_versions)
    pth_files = _scan_venv_startup_files(ctx, proj_dirs, ref_dt)
    checklist = build_rotation_checklist(payload_text, has_pth=bool(pth_files))

    if alert_id is not None:
        fname = f"{alert_id}.md"
    else:
        slug = _SLUG_RE.sub("-", clean_purl).strip("-") or "pkg"
        ts_slug = ctx.now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        fname = f"{slug}-{ts_slug}.md"

    report_path = ctx.paths.exposure / fname
    markdown = _render_exposure_markdown(
        purl=clean_purl,
        versions=clean_versions,
        advisory_id=advisory_id,
        checked_at=ctx.now_iso,
        commits=commits,
        cached_wheels=cached_wheels,
        pth_files=pth_files,
        checklist=checklist,
    )
    written_path = fsguard.safe_write(report_path, markdown, root=ctx.paths.state)

    return ExposureReport(
        purl=clean_purl,
        versions=clean_versions,
        report_path=written_path,
        commits=commits,
        cached_wheels=cached_wheels,
        pth_files=pth_files,
        rotation_checklist=checklist,
    )


def show(ctx: Ctx, alert_id: int | str) -> dict[str, Any]:
    """Resolve and return the exposure report path for `alert_id` (`exposure --show ALERT_ID`)."""
    raw_id = str(alert_id).strip()
    numeric_id = raw_id[len("A-") :] if raw_id.upper().startswith("A-") else raw_id
    report_path = ctx.paths.exposure / f"{numeric_id}.md"

    if numeric_id.isdigit():
        row = ctx.db.execute(
            "SELECT payload_json FROM alerts WHERE alert_id = ?",
            (int(numeric_id),),
        ).fetchone()
        if row is not None and row["payload_json"]:
            try:
                payload = json.loads(str(row["payload_json"]))
                if isinstance(payload, dict) and payload.get("exposure_report"):
                    report_path = Path(str(payload["exposure_report"]))
            except json.JSONDecodeError:
                pass

    return {
        "ok": True,
        "alert_id": raw_id,
        "report_path": str(report_path),
        "exists": report_path.is_file(),
    }
