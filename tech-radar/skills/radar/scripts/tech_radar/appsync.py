"""Verified app sync with git commit provenance and atomic rollback (Spec 07 §7, Spec 12 A25)."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from . import db, fsguard, launchd, notify, security
from .errors import RadarError, WriteDenied

if TYPE_CHECKING:
    from .context import Ctx

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import verify_app  # noqa: E402

PLUGIN_KEY = "tech-radar@personal-claude-marketplace"
MARKETPLACE_NAME = "personal-claude-marketplace"
SCRIPTS_REL_IN_REPO = "tech-radar/skills/radar/scripts"
RUNTIME_FILES: tuple[str, ...] = (
    "radar.py",
    "radar.py.lock",
    "reddit_pull.py",
    "reddit_pull.py.lock",
    "verify_app.py",
)


@dataclass(frozen=True)
class SyncResult:
    """Result of an `appsync.sync(ctx)` operation."""

    ok: bool
    status: str  # "synced" | "noop" | "skipped" | "refused"
    manifest_sha: str | None = None
    tree_sha: str | None = None
    reason: str | None = None
    alert_id: int | None = None
    coverage_warning: str | None = None


def git_blob_sha1(data: bytes) -> str:
    """Compute the git blob SHA-1 digest (`blob <size>\\0<data>`) in Python."""
    header = f"blob {len(data)}\0".encode("ascii")
    h = hashlib.sha1(header)
    h.update(data)
    return h.hexdigest()


def collect_source_files(src_dir: Path) -> dict[str, bytes]:
    """Walk `src_dir` excluding Spec 11 §8 paths and rejecting symlinks."""
    if not src_dir.exists() or not src_dir.is_dir() or src_dir.is_symlink():
        raise WriteDenied(f"source scripts directory missing or symlink: {src_dir}")

    files_out: dict[str, bytes] = {}
    for root, dirs, files in os.walk(src_dir, followlinks=False):
        rpath = Path(root)
        rel_dir = rpath.relative_to(src_dir)
        if rel_dir != Path(".") and fsguard.is_excluded_app_path(rel_dir):
            dirs[:] = []
            continue

        kept_dirs: list[str] = []
        for dname in sorted(dirs):
            dpath = rpath / dname
            if dpath.is_symlink():
                raise WriteDenied(f"symlink directory in source scripts rejected: {dpath}")
            if not fsguard.is_excluded_app_path(Path(dname)):
                kept_dirs.append(dname)
        dirs[:] = kept_dirs

        for fname in sorted(files):
            fpath = rpath / fname
            if fpath.is_symlink():
                raise WriteDenied(f"symlink file in source scripts rejected: {fpath}")
            rel_file = fpath.relative_to(src_dir)
            if fsguard.is_excluded_app_path(rel_file):
                continue
            rel_posix = rel_file.as_posix()
            if rel_posix == "MANIFEST.sha256":
                continue
            files_out[rel_posix] = fpath.read_bytes()

    for req_file in RUNTIME_FILES:
        if req_file not in files_out:
            raise WriteDenied(f"required runtime file missing from source scripts: {req_file}")

    return files_out


def build_manifest(src_files: dict[str, bytes]) -> tuple[str, str, str]:
    """Build `MANIFEST.sha256` content from `{rel_posix: bytes}`.

    Returns `(tree_sha256, manifest_text, manifest_sha256)`.
    """
    tree_h = hashlib.sha256()
    lines: list[str] = []
    for rel_posix in sorted(src_files.keys()):
        content = src_files[rel_posix]
        file_sha = hashlib.sha256(content).hexdigest()
        tree_h.update(f"{rel_posix}\0".encode())
        tree_h.update(content)
        tree_h.update(b"\0")
        lines.append(f"{file_sha}  {rel_posix}")

    tree_sha = tree_h.hexdigest()
    manifest_text = f"# tree {tree_sha}\n" + "\n".join(lines) + "\n"
    manifest_sha = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
    return tree_sha, manifest_text, manifest_sha


def read_manifest_tree_sha(app_dir: Path) -> str | None:
    """Read the `# tree <sha256>` header from `{app_dir}/MANIFEST.sha256` if present."""
    mpath = app_dir / "MANIFEST.sha256"
    if not mpath.is_file() or mpath.is_symlink():
        return None
    try:
        first_line = mpath.read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError, UnicodeDecodeError):
        return None
    if first_line.startswith("# tree "):
        return first_line[len("# tree ") :].strip()
    return None


def _parse_ls_tree(stdout: str) -> dict[str, str]:
    """Parse `git ls-tree -r` output into `{rel_posix_under_scripts: blob_sha1}`."""
    prefix = f"{SCRIPTS_REL_IN_REPO}/"
    blobs: dict[str, str] = {}
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line or "\t" not in line:
            continue
        meta_part, path_part = line.split("\t", 1)
        meta_tokens = meta_part.split()
        if len(meta_tokens) < 3:
            continue
        obj_type = meta_tokens[1]
        obj_sha = meta_tokens[2].lower()
        if obj_type != "blob":
            continue
        clean_path = path_part.strip()
        if not clean_path.startswith(prefix):
            continue
        rel_posix = clean_path[len(prefix) :]
        if not rel_posix or fsguard.is_excluded_app_path(Path(rel_posix)):
            continue
        blobs[rel_posix] = obj_sha
    return blobs


def _resolve_marketplace_repo(home: Path) -> Path:
    known_file = home / ".claude" / "plugins" / "known_marketplaces.json"
    if known_file.is_file():
        try:
            data = json.loads(known_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                entry = data.get(MARKETPLACE_NAME)
                if isinstance(entry, dict) and entry.get("installLocation"):
                    return Path(str(entry["installLocation"])).expanduser()
        except (OSError, json.JSONDecodeError):
            pass
    return home / ".claude" / "plugins" / "marketplaces" / MARKETPLACE_NAME


def _read_head_plugin_version(ctx: Ctx, mkt_repo: Path) -> str | None:
    manifest_file = mkt_repo / "tech-radar" / ".claude-plugin" / "plugin.json"
    if manifest_file.is_file():
        try:
            obj = json.loads(manifest_file.read_text(encoding="utf-8"))
            if isinstance(obj, dict) and obj.get("version"):
                return str(obj["version"]).strip()
        except (OSError, json.JSONDecodeError):
            pass
    try:
        res = ctx.procs.git(
            ["show", "HEAD:tech-radar/.claude-plugin/plugin.json"],
            repo=mkt_repo,
        )
        if res.returncode == 0 and res.stdout.strip():
            obj = json.loads(res.stdout)
            if isinstance(obj, dict) and obj.get("version"):
                return str(obj["version"]).strip()
    except (RadarError, json.JSONDecodeError):
        pass
    return None


def _raise_provenance_alert(ctx: Ctx, commit_sha: str, detail: str) -> tuple[int, str]:
    warn = "app sync refused: provenance mismatch"
    db.kv_set(ctx.db, "last_app_sync_warning", warn)
    dedupe = f"app_integrity|provenance|{commit_sha[:12] or 'unknown'}"
    aid, _is_new = security.upsert_alert(
        ctx,
        tier="P0",
        kind="app_integrity",
        confidence="confirmed",
        dedupe_key=dedupe,
        notified_at=None,
        payload={
            "advisory_id": "app_integrity",
            "reason": detail,
            "commit_sha": commit_sha,
        },
    )
    notify.deliver_pending(ctx)
    return aid, warn


def _verify_installed_provenance(
    ctx: Ctx,
) -> tuple[Path | None, dict[str, bytes] | None, SyncResult | None]:
    """Resolve `installed_plugins.json` and verify scripts against git provenance (Spec 12 A25).

    Returns `(src_scripts_dir, src_files, early_result)`.
    """
    installed_file = ctx.paths.home / ".claude" / "plugins" / "installed_plugins.json"
    if not installed_file.is_file():
        warn = "app sync skipped: provenance unavailable"
        db.kv_set(ctx.db, "last_app_sync_warning", warn)
        return (
            None,
            None,
            SyncResult(
                ok=False,
                status="skipped",
                reason="installed_plugins.json not found",
                coverage_warning=warn,
            ),
        )

    try:
        inst_data = json.loads(installed_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        warn = "app sync skipped: provenance unavailable"
        db.kv_set(ctx.db, "last_app_sync_warning", warn)
        return (
            None,
            None,
            SyncResult(
                ok=True,
                status="skipped",
                reason="provenance unavailable",
                coverage_warning=warn,
            ),
        )

    plugins_map = inst_data.get("plugins") if isinstance(inst_data, dict) else None
    records = plugins_map.get(PLUGIN_KEY) if isinstance(plugins_map, dict) else None
    if not isinstance(records, list) or not records:
        warn = "app sync skipped: provenance unavailable"
        db.kv_set(ctx.db, "last_app_sync_warning", warn)
        return (
            None,
            None,
            SyncResult(
                ok=True,
                status="skipped",
                reason="provenance unavailable",
                coverage_warning=warn,
            ),
        )

    selected = next(
        (r for r in records if isinstance(r, dict) and r.get("scope") == "user"),
        records[0] if isinstance(records[0], dict) else None,
    )
    if not isinstance(selected, dict) or not selected.get("installPath"):
        warn = "app sync skipped: provenance unavailable"
        db.kv_set(ctx.db, "last_app_sync_warning", warn)
        return (
            None,
            None,
            SyncResult(
                ok=True,
                status="skipped",
                reason="provenance unavailable",
                coverage_warning=warn,
            ),
        )

    install_path = Path(str(selected["installPath"])).expanduser()
    src_scripts = install_path / "skills" / "radar" / "scripts"
    installed_ver = str(selected.get("version") or "").strip()
    commit_sha = str(selected.get("gitCommitSha") or "").strip()

    try:
        src_files = collect_source_files(src_scripts)
    except WriteDenied as exc:
        aid, warn = _raise_provenance_alert(ctx, commit_sha, str(exc))
        return (
            None,
            None,
            SyncResult(
                ok=False,
                status="refused",
                reason="provenance mismatch",
                alert_id=aid,
                coverage_warning=warn,
            ),
        )

    actual_blobs = {rel: git_blob_sha1(content) for rel, content in src_files.items()}
    mkt_repo = _resolve_marketplace_repo(ctx.paths.home)
    if not (mkt_repo / ".git").exists():
        warn = "app sync skipped: provenance unavailable"
        db.kv_set(ctx.db, "last_app_sync_warning", warn)
        return (
            None,
            None,
            SyncResult(
                ok=True,
                status="skipped",
                reason="provenance unavailable",
                coverage_warning=warn,
            ),
        )

    ls_res = None
    if commit_sha:
        with contextlib.suppress(RadarError):
            ls_res = ctx.procs.git(
                ["ls-tree", "-r", commit_sha, "--", SCRIPTS_REL_IN_REPO],
                repo=mkt_repo,
            )

    if ls_res is not None and ls_res.returncode == 0 and ls_res.stdout.strip():
        expected_blobs = _parse_ls_tree(ls_res.stdout)
        if not expected_blobs or actual_blobs != expected_blobs:
            aid, warn = _raise_provenance_alert(
                ctx,
                commit_sha,
                "installed scripts differ from gitCommitSha tree",
            )
            return (
                None,
                None,
                SyncResult(
                    ok=False,
                    status="refused",
                    reason="provenance mismatch",
                    alert_id=aid,
                    coverage_warning=warn,
                ),
            )
        return src_scripts, src_files, None

    # Outcome 3: commit absent (shallow clone) -> check HEAD version == installed version
    head_ver = _read_head_plugin_version(ctx, mkt_repo)
    if not head_ver or not installed_ver or head_ver != installed_ver:
        warn = "app sync skipped: provenance unavailable"
        db.kv_set(ctx.db, "last_app_sync_warning", warn)
        return (
            None,
            None,
            SyncResult(
                ok=True,
                status="skipped",
                reason="provenance unavailable",
                coverage_warning=warn,
            ),
        )

    try:
        head_ls = ctx.procs.git(
            ["ls-tree", "-r", "HEAD", "--", SCRIPTS_REL_IN_REPO],
            repo=mkt_repo,
        )
    except RadarError:
        warn = "app sync skipped: provenance unavailable"
        db.kv_set(ctx.db, "last_app_sync_warning", warn)
        return (
            None,
            None,
            SyncResult(
                ok=True,
                status="skipped",
                reason="provenance unavailable",
                coverage_warning=warn,
            ),
        )

    if head_ls.returncode != 0 or not head_ls.stdout.strip():
        warn = "app sync skipped: provenance unavailable"
        db.kv_set(ctx.db, "last_app_sync_warning", warn)
        return (
            None,
            None,
            SyncResult(
                ok=True,
                status="skipped",
                reason="provenance unavailable",
                coverage_warning=warn,
            ),
        )

    expected_head_blobs = _parse_ls_tree(head_ls.stdout)
    if not expected_head_blobs or actual_blobs != expected_head_blobs:
        aid, warn = _raise_provenance_alert(
            ctx,
            "HEAD",
            "installed scripts differ from shallow clone HEAD tree",
        )
        return (
            None,
            None,
            SyncResult(
                ok=False,
                status="refused",
                reason="provenance mismatch",
                alert_id=aid,
                coverage_warning=warn,
            ),
        )

    return src_scripts, src_files, None


def sync(ctx: Ctx, *, from_path: Path | str | None = None) -> SyncResult:
    """Sync the verified plugin scripts tree to `{state}/app` and reload the launchd plist."""
    if from_path is not None:
        src_scripts = Path(from_path).expanduser()
        src_files = collect_source_files(src_scripts)
    else:
        resolved_src, resolved_files, early = _verify_installed_provenance(ctx)
        if early is not None:
            return early
        assert resolved_src is not None and resolved_files is not None
        src_scripts = resolved_src
        src_files = resolved_files

    tree_sha, manifest_text, manifest_sha = build_manifest(src_files)
    app_dir = ctx.paths.app
    target_plist = launchd.plist_path(ctx.paths)

    # Check A25/§8a no-op criterion:
    # 1. Existing MANIFEST.sha256 header `# tree <sha256>` == tree_sha
    # 2. Plist pins manifest_sha
    # 3. app/ verifies against manifest_sha with no unlisted files or symlinks
    existing_tree_sha = read_manifest_tree_sha(app_dir)
    pinned_sha = launchd.read_pinned_manifest_sha(target_plist)
    if existing_tree_sha == tree_sha and pinned_sha == manifest_sha:
        verified_ok, _v_reason = verify_app.verify_tree(
            app_dir,
            expected_manifest_sha=manifest_sha,
        )
        if verified_ok:
            db.kv_set(ctx.db, "app_manifest_sha", manifest_sha)
            ctx.db.execute("DELETE FROM kv WHERE key = 'last_app_sync_warning'")
            ctx.db.commit()
            return SyncResult(
                ok=True,
                status="noop",
                manifest_sha=manifest_sha,
                tree_sha=tree_sha,
            )

    uv_path = ctx.procs.resolve("uv") or "/opt/homebrew/bin/uv"
    verify_code = src_files.get("verify_app.py", b"").decode("utf-8")
    rendered_plist_bytes = launchd.render_plist(
        ctx.paths,
        manifest_sha=manifest_sha,
        uv_path=uv_path,
        verify_app_code=verify_code,
    )

    # Step 1 (A25): render plist to a temp file BEFORE swapping app
    plist_tmp = target_plist.parent / f".{target_plist.name}.staged"
    fsguard.safe_write(
        plist_tmp,
        rendered_plist_bytes,
        mode=0o600,
        root=ctx.paths.launch_agents,
    )

    prev_plist_bytes: bytes | None = (
        target_plist.read_bytes()
        if target_plist.is_file() and not target_plist.is_symlink()
        else None
    )

    def _write_manifest_into_tmp(tmp_app_dir: Path) -> None:
        mfile = tmp_app_dir / "MANIFEST.sha256"
        mfile.write_text(manifest_text, encoding="utf-8")
        os.chmod(mfile, 0o600)

    # Step 2 (A25): build app.tmp + MANIFEST.sha256, swap app -> app.prev, app.tmp -> app
    try:
        fsguard.safe_replace_tree(
            src_scripts,
            app_dir,
            root=ctx.paths.state,
            prepare_tmp=_write_manifest_into_tmp,
        )
        # Step 3 (A25): write plist
        fsguard.safe_write(
            target_plist,
            rendered_plist_bytes,
            mode=0o600,
            root=ctx.paths.launch_agents,
        )
    finally:
        if plist_tmp.exists():
            with contextlib.suppress(OSError):
                plist_tmp.unlink()

    # Step 4 (A25): launchctl bootout + bootstrap; rollback on bootstrap failure
    boot_res = launchd.reload_job(ctx, target_plist)
    if boot_res.returncode != 0:
        app_prev = app_dir.parent / f"{app_dir.name}.prev"
        if app_dir.exists():
            with contextlib.suppress(OSError):
                shutil.rmtree(app_dir)
        if app_prev.exists():
            with contextlib.suppress(OSError):
                os.replace(app_prev, app_dir)
        if prev_plist_bytes is not None:
            fsguard.safe_write(
                target_plist,
                prev_plist_bytes,
                mode=0o600,
                root=ctx.paths.launch_agents,
            )
            with contextlib.suppress(RadarError):
                launchd.bootstrap(ctx, target_plist)
        elif target_plist.exists():
            with contextlib.suppress(OSError):
                target_plist.unlink()

        raise RadarError(
            f"launchctl bootstrap failed: {boot_res.stderr or boot_res.stdout}",
            exit_code=1,
        )

    db.kv_set(ctx.db, "app_manifest_sha", manifest_sha)
    ctx.db.execute("DELETE FROM kv WHERE key = 'last_app_sync_warning'")
    ctx.db.commit()
    return SyncResult(
        ok=True,
        status="synced",
        manifest_sha=manifest_sha,
        tree_sha=tree_sha,
    )
