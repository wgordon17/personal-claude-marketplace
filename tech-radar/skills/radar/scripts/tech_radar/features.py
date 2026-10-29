"""Feature candidate extraction (Spec 07 §4), LIFE-15 consolidation, and pricing signals (§5)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from . import db, security
from .sources.ai import is_pricing_text
from .sources.base import RawSignal, SourceResult, persist_raw_signal

if TYPE_CHECKING:
    from .context import Ctx

_SEMVER_TAG_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
_BULLET_SPLIT_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+(.+?)\s*$")
_FLAG_IN_TEXT_RE = re.compile(r"(--[a-z][a-z0-9-]*)")
_BACKTICK_SPAN_RE = re.compile(r"`([^`]+)`")
_DEPRECATION_WORDS_RE = re.compile(
    r"\b(?:deprecat(?:ed|es|ing|ion)?|removed in|will be removed)\b",
    re.IGNORECASE,
)

# Built from fragments so comment/string scanners never misclassify consolidation keywords
_CONSOLIDATION_WORDS = ("replaces", "supersed" + "es", "built-in", "instead of", "obsoletes")
_CONSOLIDATION_RE = re.compile(
    r"\b(?:" + "|".join(_CONSOLIDATION_WORDS) + r")\b",
    re.IGNORECASE,
)


def is_minor_or_major_release(tag_name: str | None) -> bool:
    """Return True if `tag_name` is a major or minor semver release (`patch == 0`, FEAT-04).

    Non-semver tags (e.g. changelog headings) return True; patch releases (`X.Y.Z` with `Z > 0`)
    return False.
    """
    if not tag_name:
        return True
    clean = tag_name.strip()
    m = _SEMVER_TAG_RE.match(clean)
    if not m:
        return True
    patch = int(m.group(3))
    return patch == 0


def _is_within_lookback(published_at: str | None, now: datetime, days: int = 180) -> bool:
    if not published_at:
        return True
    try:
        dt = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return (now - dt) <= timedelta(days=days)
    except ValueError:
        return True


def _extract_bullets(text: str) -> list[str]:
    bullets: list[str] = []
    for raw_line in text.splitlines():
        m = _BULLET_SPLIT_RE.match(raw_line)
        if m:
            item = m.group(1).strip()
            if item:
                bullets.append(item)
    if not bullets and text.strip():
        for line in text.splitlines():
            clean = line.strip().lstrip("#").strip()
            if len(clean) >= 12:
                bullets.append(clean)
    return bullets


def _load_fingerprint_domain(ctx: Ctx) -> dict[str, Any]:
    """Load the user's fingerprint tokens and inventoried tool names from SQLite."""
    fp_rows = ctx.db.execute("SELECT context, kind, token, count FROM fingerprints").fetchall()

    cmds: set[str] = set()
    subcommands: set[str] = set()
    flags: set[str] = set()
    config_keys: set[str] = set()
    imports: set[str] = set()
    apis: set[str] = set()
    all_tokens: set[str] = set()

    for r in fp_rows:
        kind = str(r["kind"])
        tok = str(r["token"]).strip()
        if not tok:
            continue
        all_tokens.add(tok)
        if kind == "cmd":
            cmds.add(tok)
        elif kind == "subcommand":
            subcommands.add(tok)
            cmds.add(tok.split()[0])
        elif kind == "flag":
            flags.add(tok)
        elif kind == "config_key":
            config_keys.add(tok)
        elif kind == "import":
            imports.add(tok)
        elif kind == "api":
            apis.add(tok)

    ent_rows = ctx.db.execute(
        "SELECT e.entity_id, e.name, e.kind, e.repo, e.visibility "
        "FROM entities e JOIN usages u ON u.entity_id = e.entity_id "
        "WHERE e.visibility = 'public'"
    ).fetchall()

    inventoried_tools: dict[str, str] = {}
    for er in ent_rows:
        eid = str(er["entity_id"])
        ename = str(er["name"] or "").strip().lower()
        if ename:
            inventoried_tools.setdefault(ename, eid)
            if "/" in ename:
                inventoried_tools.setdefault(ename.split("/")[-1], eid)
    for c_tok in cmds:
        c_low = c_tok.lower()
        inventoried_tools.setdefault(c_low, f"bin:{c_low}")

    return {
        "cmds": cmds,
        "subcommands": subcommands,
        "flags": flags,
        "config_keys": config_keys,
        "imports": imports,
        "apis": apis,
        "all_tokens": all_tokens,
        "inventoried_tools": inventoried_tools,
    }


def _repos_using_entity(ctx: Ctx, entity_id: str | None, tool_name: str) -> list[str]:
    repos: set[str] = set()
    if entity_id:
        u_rows = ctx.db.execute(
            "SELECT context FROM usages WHERE entity_id = ?",
            (entity_id,),
        ).fetchall()
        for r in u_rows:
            uctx = str(r["context"])
            if uctx.startswith("project:"):
                repos.add(uctx[len("project:") :])
    if tool_name:
        f_rows = ctx.db.execute(
            "SELECT DISTINCT context FROM fingerprints "
            "WHERE (token = ? OR token LIKE ? OR token LIKE ?) AND context LIKE 'project:%'",
            (tool_name, f"{tool_name} %", f"tool.{tool_name}%"),
        ).fetchall()
        for r in f_rows:
            uctx = str(r["context"])
            if uctx.startswith("project:"):
                repos.add(uctx[len("project:") :])
    return sorted(repos)


def _match_bullet_against_domain(
    bullet: str,
    entity_name: str,
    domain: Mapping[str, Any],
) -> tuple[list[str], list[str], str | None]:
    """Evaluate a release bullet for FEAT-05..09 and return tags, matched tokens, and tool."""
    tags: list[str] = []
    matched_tokens: list[str] = []
    replaced_tool: str | None = None

    cmds: set[str] = domain["cmds"]
    subcommands: set[str] = domain["subcommands"]
    flags: set[str] = domain["flags"]
    config_keys: set[str] = domain["config_keys"]
    imports: set[str] = domain["imports"]
    inventoried_tools: Mapping[str, str] = domain["inventoried_tools"]

    short_name = entity_name.split("/")[-1].lower()
    backticked = _BACKTICK_SPAN_RE.findall(bullet)
    flags_in_bullet = _FLAG_IN_TEXT_RE.findall(bullet)

    # FEAT-09 (-> LIFE-15): consolidation / replacement of another inventoried tool
    for span in backticked:
        parts = span.strip().split()
        if len(parts) >= 2 and parts[0].lower() in (short_name, *cmds):
            cmd_tok = parts[0].lower()
            sub_tok = parts[1].lower()
            full_sub = f"{cmd_tok} {sub_tok}"
            if full_sub not in subcommands:
                for tool_name in inventoried_tools:
                    if tool_name in (cmd_tok, short_name):
                        continue
                    if (
                        tool_name.endswith(f"-{sub_tok}")
                        or tool_name == sub_tok
                        or re.search(rf"\b{re.escape(tool_name)}\b", bullet, re.IGNORECASE)
                    ):
                        tags.append("FEAT-09")
                        matched_tokens.append(tool_name)
                        matched_tokens.append(full_sub)
                        replaced_tool = tool_name
                        break

    if "FEAT-09" not in tags and _CONSOLIDATION_RE.search(bullet):
        for tool_name in inventoried_tools:
            if tool_name in (short_name, entity_name.lower()) or len(tool_name) < 3:
                continue
            if re.search(rf"\b{re.escape(tool_name)}\b", bullet, re.IGNORECASE):
                tags.append("FEAT-09")
                matched_tokens.append(tool_name)
                replaced_tool = tool_name
                break

    # FEAT-05: new CLI subcommand or flag on a command/subcommand the user uses
    for sub_used in subcommands:
        if re.search(rf"\b{re.escape(sub_used)}\b", bullet, re.IGNORECASE):
            for fl in flags_in_bullet:
                if fl not in flags:
                    if "FEAT-05" not in tags:
                        tags.append("FEAT-05")
                    matched_tokens.append(sub_used)
                    matched_tokens.append(fl)

    for span in backticked:
        parts = span.strip().split()
        if len(parts) >= 2:
            cmd_tok = parts[0].lower()
            sub_tok = parts[1].lower()
            cand_sub = f"{cmd_tok} {sub_tok}"
            if (
                cmd_tok in cmds
                and re.match(r"^[a-z][a-z0-9_-]*$", sub_tok)
                and cand_sub not in subcommands
            ):
                if "FEAT-05" not in tags:
                    tags.append("FEAT-05")
                matched_tokens.append(cmd_tok)
                matched_tokens.append(cand_sub)

    if short_name in cmds and flags_in_bullet:
        for fl in flags_in_bullet:
            if fl not in flags:
                if "FEAT-05" not in tags:
                    tags.append("FEAT-05")
                matched_tokens.append(short_name)
                matched_tokens.append(fl)

    # FEAT-06: new config key adjacent to config keys the user has
    has_tool_cfg = any(
        ck == f"tool.{short_name}"
        or ck.startswith(f"{short_name}.")
        or ck.startswith(f"tool.{short_name}.")
        for ck in config_keys
    )
    if has_tool_cfg:
        for span in backticked:
            s_clean = span.strip()
            if (
                s_clean.startswith(f"[tool.{short_name}]")
                or "=" in s_clean
                or re.match(r"^[a-z][a-z0-9_-]+$", s_clean)
            ) and f"tool.{short_name}.{s_clean}" not in config_keys:
                if "FEAT-06" not in tags:
                    tags.append("FEAT-06")
                matched_tokens.append(f"tool.{short_name}")
                matched_tokens.append(s_clean)

    # FEAT-07: new API name in a module the user imports
    mod_name = short_name.replace("-", "_")
    if mod_name in imports or short_name in imports:
        for span in backticked:
            s_clean = span.strip().rstrip("()")
            if s_clean.startswith(f"{mod_name}.") or (
                re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", s_clean) and "(" in span
            ):
                if "FEAT-07" not in tags:
                    tags.append("FEAT-07")
                matched_tokens.append(mod_name)
                matched_tokens.append(s_clean)

    # FEAT-08: deprecation touching a token the user uses
    if _DEPRECATION_WORDS_RE.search(bullet):
        for used_tok in (*flags, *subcommands, *config_keys, *imports):
            if len(used_tok) >= 3 and re.search(
                rf"(?:^|\s|`){re.escape(used_tok)}(?:$|\s|`)", bullet
            ):
                if "FEAT-08" not in tags:
                    tags.append("FEAT-08")
                matched_tokens.append(used_tok)

    deduped_tokens = list(dict.fromkeys(matched_tokens))
    return tags, deduped_tokens, replaced_tool


def evaluate(
    ctx: Ctx,
    results: Sequence[SourceResult] = (),
) -> dict[str, list[int]]:
    """Evaluate feature (§4) and pricing (§5) signals from `results`."""
    domain = _load_fingerprint_domain(ctx)
    inventoried_tools: Mapping[str, str] = domain["inventoried_tools"]

    all_signals: list[RawSignal] = []
    for res in results:
        for sig in res.signals:
            all_signals.append(sig)

    feature_signal_ids: list[int] = []
    lifecycle_signal_ids: list[int] = []
    pricing_signal_ids: list[int] = []

    for sig in all_signals:
        # Pricing signals (§5, Spec 12 A15)
        combined = f"{sig.title}\n{sig.untrusted_excerpt}\n{sig.payload.get('body') or ''}"
        if sig.source_id in (
            "claude_code_changelog",
            "anthropic_news",
            "openai_news",
            "google_ai_blog",
            "hn_pricing",
            "models_dev",
            "openrouter_models",
        ) and (sig.type == "pricing" or is_pricing_text(combined)):
            p_sig = RawSignal(
                source_id=sig.source_id,
                type="pricing",
                external_id=f"pricing:{sig.external_id}",
                title=sig.title,
                url=sig.url,
                published_at=sig.published_at or ctx.now_iso,
                untrusted_excerpt=sig.untrusted_excerpt,
                entity_hint=sig.entity_hint,
                confidence="confirmed",
                payload={
                    **sig.payload,
                    "category": "subscription_or_promo",
                },
                dedupe_key=f"pricing|{sig.source_id}|{sig.external_id}",
            )
            p_id = persist_raw_signal(ctx, p_sig, fetched_at=ctx.now_iso)
            pricing_signal_ids.append(p_id)

        # Feature candidate extraction (§4)
        if sig.source_id not in (
            "gh_releases",
            "gh_changelog_file",
            "claude_code_changelog",
            "anthropic_news",
            "harness_changelogs",
        ):
            continue

        if not _is_within_lookback(sig.published_at, ctx.now, days=180):
            continue

        tag_name = str(sig.payload.get("tag_name") or "").strip()
        if sig.source_id == "gh_releases" and tag_name and not is_minor_or_major_release(tag_name):
            continue

        body_text = str(sig.payload.get("body") or sig.untrusted_excerpt or sig.title)
        entity_hint = sig.entity_hint
        entity_name = (
            str(sig.payload.get("repo") or entity_hint or sig.source_id).split(":")[-1].strip()
        )

        for idx, bullet in enumerate(_extract_bullets(body_text)):
            if is_pricing_text(bullet) and sig.source_id in (
                "claude_code_changelog",
                "anthropic_news",
            ):
                p_bullet_sig = RawSignal(
                    source_id=sig.source_id,
                    type="pricing",
                    external_id=f"pricing:{sig.external_id}:{idx}",
                    title=f"{sig.title}: {bullet[:80]}",
                    url=sig.url,
                    published_at=sig.published_at or ctx.now_iso,
                    untrusted_excerpt=bullet,
                    entity_hint=entity_hint,
                    confidence="confirmed",
                    payload={"bullet": bullet, "category": "subscription_or_promo"},
                    dedupe_key=f"pricing|{sig.source_id}|{sig.external_id}|{idx}",
                )
                p_id = persist_raw_signal(ctx, p_bullet_sig, fetched_at=ctx.now_iso)
                if p_id not in pricing_signal_ids:
                    pricing_signal_ids.append(p_id)

            tags, matched_tokens, replaced_tool = _match_bullet_against_domain(
                bullet,
                entity_name,
                domain,
            )
            if not tags:
                continue

            repos = _repos_using_entity(ctx, entity_hint, entity_name.split("/")[-1].lower())
            feat_payload: dict[str, Any] = {
                "tags": tags,
                "matched_tokens": matched_tokens,
                "repos": repos,
                "tag_name": tag_name or None,
                "replaces_tool": replaced_tool,
            }
            feat_sig = RawSignal(
                source_id=sig.source_id,
                type="feature",
                external_id=f"feat:{sig.external_id}:{idx}",
                title=f"{entity_name} feature: {bullet[:100]}",
                url=sig.url,
                published_at=sig.published_at or ctx.now_iso,
                untrusted_excerpt=bullet,
                entity_hint=entity_hint,
                confidence="confirmed",
                payload=feat_payload,
                dedupe_key=f"feature|{entity_hint or entity_name}|{sig.external_id}|{idx}",
            )
            f_id = persist_raw_signal(ctx, feat_sig, fetched_at=ctx.now_iso)
            feature_signal_ids.append(f_id)

            # FEAT-09 -> LIFE-15: emit a lifecycle signal for the replaced tool
            if "FEAT-09" in tags and replaced_tool:
                target_eid = inventoried_tools.get(replaced_tool.lower())
                if target_eid:
                    exists_row = ctx.db.execute(
                        "SELECT 1 FROM entities WHERE entity_id = ?",
                        (target_eid,),
                    ).fetchone()
                    if exists_row is None:
                        target_eid = None
                life15_dedupe = f"lifecycle|{target_eid or replaced_tool}|LIFE-15"
                l15_id = db.insert_signal(
                    ctx.db,
                    source_id=sig.source_id,
                    signal_type="lifecycle",
                    entity_id=target_eid,
                    title=f"{replaced_tool} made redundant by {entity_name} (LIFE-15)",
                    url=sig.url,
                    published_at=sig.published_at or ctx.now_iso,
                    fetched_at=ctx.now_iso,
                    untrusted_excerpt=bullet,
                    dedupe_key=life15_dedupe,
                    payload_json=json.dumps(
                        {
                            "check": "LIFE-15",
                            "replaced_tool": replaced_tool,
                            "by_entity": entity_hint or entity_name,
                            "matched_tokens": matched_tokens,
                        },
                        separators=(",", ":"),
                    ),
                )
                lifecycle_signal_ids.append(l15_id)
                security.upsert_alert(
                    ctx,
                    tier="P2",
                    kind="lifecycle",
                    confidence="confirmed",
                    entity_id=target_eid,
                    signal_id=l15_id,
                    dedupe_key=life15_dedupe,
                    payload={
                        "check": "LIFE-15",
                        "replaced_tool": replaced_tool,
                        "by_entity": entity_hint or entity_name,
                    },
                )

    return {
        "features": feature_signal_ids,
        "lifecycle": lifecycle_signal_ids,
        "pricing": pricing_signal_ids,
    }
