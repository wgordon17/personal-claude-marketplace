"""AI harness inventory collector: Claude Code, MCP, skills, transcripts (A13), and T3 state DB."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import urllib.parse
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import fsguard, redact, sanitize
from ..errors import RadarError
from . import fingerprint as fp
from .projects import ProjectRecord
from .system import EntityRecord, UsageRecord

if TYPE_CHECKING:
    from ..context import Ctx

_SKILL_NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
_CLAUDE_VER_RE = re.compile(r"(\d+\.\d+\.\d+(?:-[A-Za-z0-9._-]+)?)")
_UVX_GIT_PIN_RE = re.compile(
    r"^git\+https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?@([0-9a-fA-F]{7,40})$"
)
_NPX_PKG_PIN_RE = re.compile(r"^(@?[A-Za-z0-9_./-]+)@([0-9][A-Za-z0-9_.-]*)$")

EXPECTED_T3_TABLES: frozenset[str] = frozenset(
    {
        "projection_projects",
        "orchestration_v2_projection_threads",
        "orchestration_v2_projection_turn_items",
    }
)


@dataclass(frozen=True)
class MetricRecord:
    """Single entity metric observation ready for SQLite upsert."""

    entity_id: str
    metric: str
    value: float
    observed_at: str | None = None


@dataclass
class HarnessInventoryOutput:
    """Aggregated output from harness.collect(ctx)."""

    entities: list[EntityRecord] = field(default_factory=list)
    usages: list[UsageRecord] = field(default_factory=list)
    fingerprints: list[fp.FingerprintRecord] = field(default_factory=list)
    metrics: list[MetricRecord] = field(default_factory=list)
    dataless_skipped: int = 0


def open_t3_db(db_path: Path | str) -> sqlite3.Connection:
    """Open the T3 state SQLite DB strictly read-only via URI mode=ro."""
    resolved = Path(db_path).expanduser().resolve()
    quoted = urllib.parse.quote(str(resolved), safe="/:")
    uri = f"file:{quoted}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON;")
    return conn


def _provider_for_model(model_id: str) -> str:
    low = model_id.lower()
    if low.startswith("claude-"):
        return "anthropic"
    if low.startswith(("gpt-", "o1-", "o3-", "o4-")):
        return "openai"
    if low.startswith("gemini-"):
        return "google"
    if low.startswith("deepseek-"):
        return "deepseek"
    return "unknown"


def _extract_mcp_pin(args: Any) -> dict[str, str]:
    """Extract only safe package/repo + version/sha pins from MCP server args (never raw args)."""
    if not isinstance(args, list):
        return {}
    str_args = [str(a).strip() for a in args if isinstance(a, str)]
    out: dict[str, str] = {}
    for idx, tok in enumerate(str_args):
        if tok == "--from" and idx + 1 < len(str_args):
            target = str_args[idx + 1]
            gm = _UVX_GIT_PIN_RE.match(target)
            if gm:
                out["repo"] = gm.group(1).lower()
                out["pin"] = gm.group(2)
            else:
                pm = _NPX_PKG_PIN_RE.match(target)
                if pm:
                    out["package"] = pm.group(1)
                    out["pin"] = pm.group(2)
        elif not tok.startswith("-"):
            pm = _NPX_PKG_PIN_RE.match(tok)
            if pm:
                out["package"] = pm.group(1)
                out["pin"] = pm.group(2)
    return out


def _collect_mcp_servers_from_obj(
    mcp_servers: Any,
    out: HarnessInventoryOutput,
) -> None:
    if not isinstance(mcp_servers, dict):
        return
    for server_name, cfg in sorted(mcp_servers.items()):
        sname = str(server_name).strip()
        if not sname or not isinstance(cfg, dict):
            continue
        attrs: dict[str, Any] = {}
        cmd = cfg.get("command")
        if isinstance(cmd, str) and cmd.strip():
            cmd_base = os.path.basename(cmd.strip())
            if re.match(r"^[A-Za-z0-9_.-]+$", cmd_base):
                attrs["command"] = cmd_base
                out.fingerprints.append(
                    fp.FingerprintRecord(
                        context="harness:claude",
                        kind="cmd",
                        token=cmd_base,
                    )
                )
        pin_info = _extract_mcp_pin(cfg.get("args"))
        attrs.update(pin_info)

        entity_id = f"mcp:{sname}"
        out.entities.append(
            EntityRecord(
                entity_id=entity_id,
                kind="mcp",
                ecosystem="mcp",
                name=sname,
                repo=pin_info.get("repo"),
                visibility="public",
            )
        )
        out.usages.append(
            UsageRecord(
                entity_id=entity_id,
                context="harness:claude",
                version=pin_info.get("pin"),
                direct=1,
                attrs=attrs,
            )
        )


def _scan_skill_dir(skills_root: Path, out: HarnessInventoryOutput) -> None:
    if not skills_root.is_dir():
        return
    for skill_md in sorted(skills_root.glob("*/SKILL.md")):
        try:
            st = os.stat(skill_md)
            if fsguard.is_dataless(st):
                out.dataless_skipped += 1
                continue
            text = skill_md.read_text(encoding="utf-8", errors="ignore")[:4096]
        except OSError:
            continue
        m = _SKILL_NAME_RE.search(text)
        skill_name = (m.group(1).strip().strip("'\"") if m else skill_md.parent.name).strip()
        if not skill_name:
            continue
        entity_id = f"skill:{skill_name}"
        out.entities.append(
            EntityRecord(
                entity_id=entity_id,
                kind="skill",
                ecosystem="agent-skills",
                name=skill_name,
                visibility="public",
            )
        )
        out.usages.append(
            UsageRecord(
                entity_id=entity_id,
                context="harness:claude",
                version=None,
                direct=1,
            )
        )


def _scan_agents_and_commands(base_dir: Path, out: HarnessInventoryOutput) -> None:
    agents_dir = base_dir / "agents"
    if agents_dir.is_dir():
        for md_file in sorted(agents_dir.glob("*.md")):
            out.fingerprints.append(
                fp.FingerprintRecord(
                    context="harness:claude",
                    kind="config_key",
                    token=f"agent:{md_file.stem}",
                )
            )
    commands_dir = base_dir / "commands"
    if commands_dir.is_dir():
        for md_file in sorted(commands_dir.glob("*.md")):
            out.fingerprints.append(
                fp.FingerprintRecord(
                    context="harness:claude",
                    kind="config_key",
                    token=f"command:{md_file.stem}",
                )
            )


def _collect_claude_settings_and_plugins(
    ctx: Ctx,
    projects: Sequence[ProjectRecord],
    out: HarnessInventoryOutput,
) -> None:
    claude_dir = ctx.paths.home / ".claude"
    settings_path = claude_dir / "settings.json"
    if settings_path.is_file():
        try:
            st = os.stat(settings_path)
            if fsguard.is_dataless(st):
                out.dataless_skipped += 1
            else:
                data = json.loads(settings_path.read_text(encoding="utf-8", errors="ignore"))
                if isinstance(data, dict):
                    model_val = data.get("model")
                    if isinstance(model_val, str) and model_val.strip():
                        mid = model_val.strip()
                        prov = _provider_for_model(mid)
                        entity_id = f"model:{prov}/{mid}"
                        out.entities.append(
                            EntityRecord(
                                entity_id=entity_id,
                                kind="model",
                                ecosystem=prov,
                                name=mid,
                                visibility="public",
                            )
                        )
                        out.usages.append(
                            UsageRecord(
                                entity_id=entity_id,
                                context="harness:claude",
                                version=None,
                                direct=1,
                            )
                        )
                        out.fingerprints.append(
                            fp.FingerprintRecord(
                                context="harness:claude",
                                kind="model_id",
                                token=mid,
                            )
                        )

                    enabled_plugins = data.get("enabledPlugins")
                    if isinstance(enabled_plugins, dict):
                        for plug_key, enabled in sorted(enabled_plugins.items()):
                            if not enabled:
                                continue
                            pname = str(plug_key).split("@", 1)[0].strip()
                            if pname:
                                entity_id = f"plugin:{pname}"
                                out.entities.append(
                                    EntityRecord(
                                        entity_id=entity_id,
                                        kind="plugin",
                                        ecosystem="claude-code",
                                        name=pname,
                                        visibility="public",
                                    )
                                )
                                out.usages.append(
                                    UsageRecord(
                                        entity_id=entity_id,
                                        context="harness:claude",
                                        version=None,
                                        direct=1,
                                    )
                                )

                    hooks_cfg = data.get("hooks")
                    if isinstance(hooks_cfg, dict):
                        for event_name, hook_entries in sorted(hooks_cfg.items()):
                            out.fingerprints.append(
                                fp.FingerprintRecord(
                                    context="harness:claude",
                                    kind="config_key",
                                    token=f"hook:{event_name}",
                                )
                            )
                            if isinstance(hook_entries, list):
                                for entry in hook_entries:
                                    if not isinstance(entry, dict):
                                        continue
                                    inner_hooks = entry.get("hooks") or [entry]
                                    if isinstance(inner_hooks, list):
                                        for h in inner_hooks:
                                            if isinstance(h, dict) and isinstance(
                                                h.get("command"), str
                                            ):
                                                c_name = fp.extract_command_name(h["command"])
                                                if c_name:
                                                    out.fingerprints.append(
                                                        fp.FingerprintRecord(
                                                            context="harness:claude",
                                                            kind="cmd",
                                                            token=c_name,
                                                        )
                                                    )
        except (OSError, json.JSONDecodeError):
            pass

    claude_json = ctx.paths.home / ".claude.json"
    if claude_json.is_file():
        try:
            st = os.stat(claude_json)
            if fsguard.is_dataless(st):
                out.dataless_skipped += 1
            else:
                cj_data = json.loads(claude_json.read_text(encoding="utf-8", errors="ignore"))
                if isinstance(cj_data, dict):
                    _collect_mcp_servers_from_obj(cj_data.get("mcpServers"), out)
        except (OSError, json.JSONDecodeError):
            pass

    installed_plugins_path = claude_dir / "plugins" / "installed_plugins.json"
    if installed_plugins_path.is_file():
        try:
            st = os.stat(installed_plugins_path)
            if fsguard.is_dataless(st):
                out.dataless_skipped += 1
            else:
                ip_data = json.loads(
                    installed_plugins_path.read_text(encoding="utf-8", errors="ignore")
                )
                if isinstance(ip_data, dict) and isinstance(ip_data.get("plugins"), dict):
                    for full_key, records in sorted(ip_data["plugins"].items()):
                        if not isinstance(records, list) or not records:
                            continue
                        rec = records[0]
                        if not isinstance(rec, dict):
                            continue
                        pname, _, mkt = str(full_key).partition("@")
                        pname = pname.strip()
                        if not pname:
                            continue
                        ver = str(rec.get("version") or "").strip() or None
                        sha = str(rec.get("gitCommitSha") or "").strip() or None
                        scope = str(rec.get("scope") or "user").strip()
                        entity_id = f"plugin:{pname}"
                        attrs: dict[str, Any] = {"marketplace": mkt, "scope": scope}
                        if sha:
                            attrs["gitCommitSha"] = sha
                        out.entities.append(
                            EntityRecord(
                                entity_id=entity_id,
                                kind="plugin",
                                ecosystem="claude-code",
                                name=pname,
                                visibility="public",
                            )
                        )
                        out.usages.append(
                            UsageRecord(
                                entity_id=entity_id,
                                context="harness:claude",
                                version=ver,
                                direct=1,
                                attrs=attrs,
                            )
                        )

                        inst_path_str = rec.get("installPath")
                        if isinstance(inst_path_str, str) and inst_path_str.strip():
                            inst_path = Path(inst_path_str)
                            _scan_skill_dir(inst_path / "skills", out)
                            _scan_agents_and_commands(inst_path, out)
                            for mcp_rel in (".mcp.json", "mcp.json"):
                                mcp_file = inst_path / mcp_rel
                                if mcp_file.is_file():
                                    try:
                                        m_obj = json.loads(
                                            mcp_file.read_text(encoding="utf-8", errors="ignore")
                                        )
                                        if isinstance(m_obj, dict):
                                            _collect_mcp_servers_from_obj(
                                                m_obj.get("mcpServers", m_obj),
                                                out,
                                            )
                                    except (OSError, json.JSONDecodeError):
                                        pass
        except (OSError, json.JSONDecodeError):
            pass

    _scan_skill_dir(claude_dir / "skills", out)
    _scan_agents_and_commands(claude_dir, out)

    for proj in projects:
        if not proj.active:
            continue
        pdir = Path(proj.main_worktree)
        _scan_skill_dir(pdir / ".claude" / "skills", out)
        _scan_agents_and_commands(pdir / ".claude", out)
        proj_mcp = pdir / ".mcp.json"
        if proj_mcp.is_file():
            try:
                m_obj = json.loads(proj_mcp.read_text(encoding="utf-8", errors="ignore"))
                if isinstance(m_obj, dict):
                    _collect_mcp_servers_from_obj(m_obj.get("mcpServers", m_obj), out)
            except (OSError, json.JSONDecodeError):
                pass

    try:
        res = ctx.procs.run(["claude", "--version"])
        if res.returncode == 0 and res.stdout.strip():
            m = _CLAUDE_VER_RE.search(res.stdout)
            ver = m.group(1) if m else res.stdout.strip().split()[0]
            out.entities.append(
                EntityRecord(
                    entity_id="runtime:claude-code",
                    kind="runtime",
                    ecosystem="os",
                    name="claude-code",
                    visibility="public",
                )
            )
            out.usages.append(
                UsageRecord(
                    entity_id="runtime:claude-code",
                    context="harness:claude",
                    version=ver,
                    direct=1,
                )
            )
    except RadarError:
        pass


def _measure_tool_result_bytes(content_val: Any) -> int:
    """Measure the byte length of a tool_result payload without storing its content."""
    if content_val is None:
        return 0
    if isinstance(content_val, str):
        return len(content_val.encode("utf-8", errors="ignore"))
    if isinstance(content_val, list):
        total = 0
        for item in content_val:
            if isinstance(item, dict):
                txt = item.get("text") or item.get("content") or ""
                if isinstance(txt, str):
                    total += len(txt.encode("utf-8", errors="ignore"))
                else:
                    total += len(json.dumps(item).encode("utf-8", errors="ignore"))
            elif isinstance(item, str):
                total += len(item.encode("utf-8", errors="ignore"))
        return total
    return len(json.dumps(content_val).encode("utf-8", errors="ignore"))


def _collect_transcripts(ctx: Ctx, out: HarnessInventoryOutput) -> None:
    """Scan ~/.claude/projects/*/*.jsonl (90d) for tool counts, token/byte metrics (A13), and pain.

    Never stores assistant output or tool_result bodies.
    """
    projects_dir = ctx.paths.home / ".claude" / "projects"
    if not projects_dir.is_dir():
        return

    cutoff_ts = (ctx.now - timedelta(days=90)).timestamp()
    tool_calls: Counter[str] = Counter()
    tool_errors: Counter[str] = Counter()
    tool_tokens: Counter[str] = Counter()
    tool_bytes: Counter[str] = Counter()
    tool_use_id_map: dict[str, str] = {}

    for jsonl_path in sorted(projects_dir.glob("*/*.jsonl")):
        try:
            st = os.stat(jsonl_path)
            if fsguard.is_dataless(st):
                out.dataless_skipped += 1
                continue
            if st.st_mtime < cutoff_ts and not os.environ.get("TECH_RADAR_HOME"):
                continue
            lines = jsonl_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue

        for raw_line in lines:
            if not raw_line.strip():
                continue
            try:
                rec = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if not isinstance(rec, dict):
                continue

            raw_msg = rec.get("message")
            msg: dict[str, Any] = raw_msg if isinstance(raw_msg, dict) else rec
            role = str(msg.get("role") or rec.get("type") or "").lower()

            if role == "assistant":
                model_id = msg.get("model")
                if isinstance(model_id, str) and model_id.strip():
                    out.fingerprints.append(
                        fp.FingerprintRecord(
                            context="harness:claude",
                            kind="model_id",
                            token=model_id.strip(),
                        )
                    )

                raw_usage = msg.get("usage")
                usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
                turn_tokens = sum(
                    int(usage.get(k) or 0)
                    for k in (
                        "input_tokens",
                        "output_tokens",
                        "cache_read_input_tokens",
                        "cache_creation_input_tokens",
                    )
                    if isinstance(usage.get(k), int | float)
                )

                content_blocks = msg.get("content")
                turn_tools: list[str] = []
                if isinstance(content_blocks, list):
                    for block in content_blocks:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_use":
                            tname = str(block.get("name") or "").strip()
                            tid = str(block.get("id") or "").strip()
                            if tname:
                                tool_calls[tname] += 1
                                turn_tools.append(tname)
                                if tid:
                                    tool_use_id_map[tid] = tname

                if turn_tokens > 0 and turn_tools:
                    for tname in set(turn_tools):
                        tool_tokens[tname] += turn_tokens

            elif role in ("user", "human"):
                content_val = msg.get("content")
                if isinstance(content_val, list):
                    for block in content_val:
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type")
                        if btype == "tool_result":
                            tid = str(block.get("tool_use_id") or "").strip()
                            tname = str(
                                block.get("tool_name") or ""
                            ).strip() or tool_use_id_map.get(tid, "unknown")
                            bsize = _measure_tool_result_bytes(block.get("content"))
                            tool_bytes[tname] += bsize
                            if block.get("is_error") is True:
                                tool_errors[tname] += 1
                        elif btype == "text" and isinstance(block.get("text"), str):
                            _extract_user_pain_label(block["text"], out)
                elif isinstance(content_val, str):
                    _extract_user_pain_label(content_val, out)

    for tname, cnt in sorted(tool_calls.items()):
        out.fingerprints.append(
            fp.FingerprintRecord(
                context="harness:claude",
                kind="tool_name",
                token=tname,
                count=cnt,
            )
        )

    for tname, err_cnt in sorted(tool_errors.items()):
        out.fingerprints.append(
            fp.FingerprintRecord(
                context="harness:claude",
                kind="tool_name",
                token=f"error:{tname}",
                count=err_cnt,
            )
        )

    all_metric_tools = sorted(set(tool_calls) | set(tool_tokens) | set(tool_bytes))
    now_iso = ctx.now_iso
    for tname in all_metric_tools:
        if tname == "unknown" and tool_tokens[tname] == 0 and tool_bytes[tname] == 0:
            continue
        entity_id = f"harness:tool/{tname}"
        out.entities.append(
            EntityRecord(
                entity_id=entity_id,
                kind="cli",
                ecosystem="claude-code",
                name=tname,
                visibility="public",
            )
        )
        out.metrics.append(
            MetricRecord(
                entity_id=entity_id,
                metric="tokens",
                value=float(tool_tokens.get(tname, 0)),
                observed_at=now_iso,
            )
        )
        out.metrics.append(
            MetricRecord(
                entity_id=entity_id,
                metric="bytes",
                value=float(tool_bytes.get(tname, 0)),
                observed_at=now_iso,
            )
        )


def _extract_user_pain_label(user_text: str, out: HarnessInventoryOutput) -> None:
    first_120 = user_text.strip()[:120]
    if not first_120:
        return
    scrubbed = redact.scrub(first_120)
    cleaned = sanitize.clean(scrubbed, limit=120).strip()
    if not cleaned:
        return
    low = cleaned.lower()
    if any(w in low for w in ("error", "fail", "slow", "broken", "bug", "pain", "fix", "issue")):
        out.fingerprints.append(
            fp.FingerprintRecord(
                context="harness:claude",
                kind="tool_name",
                token=f"pain:{cleaned[:80]}",
            )
        )


def _record_t3_skipped(ctx: Ctx, reason: str) -> None:
    ctx.db.execute(
        "INSERT INTO source_health(source_id, skipped_reason, consecutive_failures) "
        "VALUES ('t3_state_db', ?, 0) "
        "ON CONFLICT(source_id) DO UPDATE SET skipped_reason = excluded.skipped_reason",
        (reason,),
    )
    ctx.db.commit()


def _collect_t3(ctx: Ctx, out: HarnessInventoryOutput) -> None:
    t3_dir = ctx.paths.home / ".t3" / "userdata"
    manifest_path = t3_dir / "model-manifest.json"
    if manifest_path.is_file():
        try:
            st = os.stat(manifest_path)
            if fsguard.is_dataless(st):
                out.dataless_skipped += 1
            else:
                m_data = json.loads(manifest_path.read_text(encoding="utf-8", errors="ignore"))
                if isinstance(m_data, dict):
                    providers = m_data.get("providers")
                    if isinstance(providers, list):
                        for prov_obj in providers:
                            if not isinstance(prov_obj, dict):
                                continue
                            prov_id = str(prov_obj.get("id") or "t3").strip()
                            for m_item in prov_obj.get("models") or []:
                                mid = (
                                    str(m_item.get("id") or "")
                                    if isinstance(m_item, dict)
                                    else str(m_item)
                                ).strip()
                                if mid:
                                    entity_id = f"model:{prov_id}/{mid}"
                                    out.entities.append(
                                        EntityRecord(
                                            entity_id=entity_id,
                                            kind="model",
                                            ecosystem=prov_id,
                                            name=mid,
                                            visibility="public",
                                        )
                                    )
                                    out.usages.append(
                                        UsageRecord(
                                            entity_id=entity_id,
                                            context="harness:t3",
                                            version=None,
                                            direct=1,
                                        )
                                    )
        except (OSError, json.JSONDecodeError):
            pass

    state_db_path = t3_dir / "statev2.sqlite"
    if not state_db_path.is_file():
        return

    try:
        t3_conn = open_t3_db(state_db_path)
    except sqlite3.Error:
        _record_t3_skipped(ctx, "t3 schema not recognized")
        return

    try:
        table_rows = t3_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        present_tables = {str(r["name"]) for r in table_rows}
        if not EXPECTED_T3_TABLES.issubset(present_tables):
            _record_t3_skipped(ctx, "t3 schema not recognized")
            return

        thread_cols = {
            str(r["name"])
            for r in t3_conn.execute(
                "PRAGMA table_info(orchestration_v2_projection_threads)"
            ).fetchall()
        }
        if not {"thread_id", "project_id", "payload_json", "default_provider"}.issubset(
            thread_cols
        ):
            _record_t3_skipped(ctx, "t3 schema not recognized")
            return

        rows = t3_conn.execute(
            "SELECT default_provider, "
            "json_extract(payload_json, '$.modelSelection.model') AS model "
            "FROM orchestration_v2_projection_threads "
            "WHERE deleted_at IS NULL"
        ).fetchall()
        for r in rows:
            prov = str(r["default_provider"] or "t3").strip()
            mid = str(r["model"] or "").strip()
            if mid:
                entity_id = f"model:{prov}/{mid}"
                out.entities.append(
                    EntityRecord(
                        entity_id=entity_id,
                        kind="model",
                        ecosystem=prov,
                        name=mid,
                        visibility="public",
                    )
                )
                out.usages.append(
                    UsageRecord(
                        entity_id=entity_id,
                        context="harness:t3",
                        version=None,
                        direct=1,
                    )
                )
                out.fingerprints.append(
                    fp.FingerprintRecord(
                        context="harness:t3",
                        kind="model_id",
                        token=mid,
                    )
                )

        if "orchestration_v2_projection_provider_sessions" in present_tables:
            sess_rows = t3_conn.execute(
                "SELECT provider, model FROM orchestration_v2_projection_provider_sessions "
                "WHERE model IS NOT NULL"
            ).fetchall()
            for sr in sess_rows:
                mid = str(sr["model"] or "").strip()
                if mid:
                    out.fingerprints.append(
                        fp.FingerprintRecord(
                            context="harness:t3",
                            kind="model_id",
                            token=mid,
                        )
                    )

        item_rows = t3_conn.execute(
            "SELECT type, json_extract(payload_json, '$.toolName') AS tool_name, status "
            "FROM orchestration_v2_projection_turn_items"
        ).fetchall()
        for ir in item_rows:
            tname = str(ir["tool_name"] or ir["type"] or "").strip()
            if tname:
                out.fingerprints.append(
                    fp.FingerprintRecord(
                        context="harness:t3",
                        kind="tool_name",
                        token=tname,
                    )
                )

        ctx.db.execute(
            "INSERT INTO source_health("
            "source_id, last_ok_at, consecutive_failures, skipped_reason"
            ") VALUES ('t3_state_db', ?, 0, NULL) "
            "ON CONFLICT(source_id) DO UPDATE SET "
            "last_ok_at = excluded.last_ok_at, "
            "consecutive_failures = 0, "
            "skipped_reason = NULL",
            (ctx.now_iso,),
        )
        ctx.db.commit()
    except sqlite3.Error:
        _record_t3_skipped(ctx, "t3 schema not recognized")
    finally:
        t3_conn.close()


def collect(
    ctx: Ctx,
    *,
    projects: Sequence[ProjectRecord] = (),
) -> HarnessInventoryOutput:
    """Collect Claude Code settings/plugins/skills/MCP/transcripts and T3 state DB."""
    out = HarnessInventoryOutput()
    _collect_claude_settings_and_plugins(ctx, projects, out)
    _collect_transcripts(ctx, out)
    _collect_t3(ctx, out)
    out.fingerprints = fp.merge_fingerprints(out.fingerprints)
    return out
