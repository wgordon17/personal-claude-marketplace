"""Inventory coordinator: system, projects, AI harness, seeds, visibility, and baseline diff."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .. import db
from . import classify, fingerprint, harness, projects, seeds, system
from .fingerprint import FingerprintRecord
from .harness import MetricRecord
from .projects import ProjectRecord, parse_manifest
from .system import DiscoveredRoot, EntityRecord, UsageRecord

if TYPE_CHECKING:
    from ..context import Ctx

__all__ = [
    "DiscoveredRoot",
    "EntityRecord",
    "FingerprintRecord",
    "InventoryResult",
    "MetricRecord",
    "ProjectRecord",
    "UsageRecord",
    "classify",
    "collect",
    "fingerprint",
    "harness",
    "parse_manifest",
    "projects",
    "seeds",
    "system",
]


@dataclass
class InventoryResult:
    """Result returned by inventory.collect(ctx) (Spec 04 § Output)."""

    added: list[tuple[str, str]] = field(default_factory=list)
    removed: list[tuple[str, str]] = field(default_factory=list)
    counts: dict[str, Any] = field(default_factory=dict)


def _upsert_entities(
    ctx: Ctx,
    entities: list[EntityRecord],
    bin_repo_links: dict[str, str],
) -> None:
    now_iso = ctx.now_iso
    merged: dict[str, EntityRecord] = {}
    for ent in entities:
        existing = merged.get(ent.entity_id)
        if existing is None:
            merged[ent.entity_id] = ent
        else:
            repo = existing.repo or ent.repo
            homepage = existing.homepage or ent.homepage
            quadrant = existing.quadrant or ent.quadrant
            vis = existing.visibility if existing.visibility != "unknown" else ent.visibility
            aliases = tuple(sorted(set(existing.aliases) | set(ent.aliases)))
            merged[ent.entity_id] = EntityRecord(
                entity_id=ent.entity_id,
                kind=ent.kind,
                ecosystem=ent.ecosystem,
                name=ent.name,
                repo=repo,
                homepage=homepage,
                quadrant=quadrant,
                visibility=vis,
                aliases=aliases,
            )

    for bin_eid, repo_slug in bin_repo_links.items():
        if bin_eid in merged:
            cur = merged[bin_eid]
            aliases = tuple(sorted(set(cur.aliases) | {f"gh:{repo_slug}"}))
            merged[bin_eid] = EntityRecord(
                entity_id=cur.entity_id,
                kind=cur.kind,
                ecosystem=cur.ecosystem,
                name=cur.name,
                repo=repo_slug,
                homepage=cur.homepage,
                quadrant=cur.quadrant,
                visibility=cur.visibility,
                aliases=aliases,
            )

    for ent in merged.values():
        safe_name = db.scrub_text(ent.name) or ent.entity_id
        safe_repo = db.scrub_text(ent.repo)
        safe_home = db.scrub_text(ent.homepage)
        ctx.db.execute(
            "INSERT INTO entities("
            "entity_id, kind, ecosystem, name, repo, homepage, quadrant, visibility, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(entity_id) DO UPDATE SET "
            "kind = excluded.kind, "
            "ecosystem = COALESCE(excluded.ecosystem, entities.ecosystem), "
            "name = excluded.name, "
            "repo = COALESCE(excluded.repo, entities.repo), "
            "homepage = COALESCE(excluded.homepage, entities.homepage), "
            "quadrant = COALESCE(excluded.quadrant, entities.quadrant), "
            "visibility = CASE WHEN entities.visibility != 'unknown' "
            "THEN entities.visibility ELSE excluded.visibility END",
            (
                ent.entity_id,
                ent.kind,
                ent.ecosystem,
                safe_name,
                safe_repo,
                safe_home,
                ent.quadrant,
                ent.visibility,
                now_iso,
            ),
        )
        for alias in ent.aliases:
            ctx.db.execute(
                "INSERT INTO entity_aliases(alias, entity_id) VALUES (?, ?) "
                "ON CONFLICT(alias) DO UPDATE SET entity_id = excluded.entity_id",
                (alias, ent.entity_id),
            )


def _upsert_projects(ctx: Ctx, proj_list: list[ProjectRecord]) -> None:
    for pr in proj_list:
        safe_display = db.scrub_text(pr.display_path) or pr.display_path
        safe_wt = db.scrub_text(pr.main_worktree) or pr.main_worktree
        safe_ren = db.scrub_text(pr.renovate_json)
        safe_manifests = db.scrub_text(pr.manifest_files_json) or "[]"
        ctx.db.execute(
            "INSERT INTO projects("
            "project_key, display_path, main_worktree, last_commit_at, active, "
            "renovate_json, excluded, root_source, visibility, alias, manifest_files_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(project_key) DO UPDATE SET "
            "display_path = excluded.display_path, "
            "main_worktree = excluded.main_worktree, "
            "last_commit_at = excluded.last_commit_at, "
            "active = excluded.active, "
            "renovate_json = excluded.renovate_json, "
            "root_source = excluded.root_source, "
            "visibility = excluded.visibility, "
            "alias = COALESCE(projects.alias, excluded.alias), "
            "manifest_files_json = excluded.manifest_files_json",
            (
                pr.project_key,
                safe_display,
                safe_wt,
                pr.last_commit_at,
                pr.active,
                safe_ren,
                pr.excluded,
                pr.root_source,
                pr.visibility,
                pr.alias,
                safe_manifests,
            ),
        )


def _upsert_fingerprints(ctx: Ctx, fp_list: list[FingerprintRecord]) -> None:
    now_iso = ctx.now_iso
    merged = fingerprint.merge_fingerprints(fp_list)
    ctx.db.execute("DELETE FROM fingerprints")
    for rec in merged:
        safe_ctx = db.scrub_text(rec.context) or rec.context
        safe_tok = db.scrub_text(rec.token) or ""
        if not safe_tok:
            continue
        ctx.db.execute(
            "INSERT INTO fingerprints(context, kind, token, count, last_seen) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(context, kind, token) DO UPDATE SET "
            "count = excluded.count, "
            "last_seen = excluded.last_seen",
            (safe_ctx, rec.kind, safe_tok, rec.count, now_iso),
        )


def _upsert_metrics(ctx: Ctx, metric_list: list[MetricRecord]) -> None:
    now_iso = ctx.now_iso
    for m in metric_list:
        obs = m.observed_at or now_iso
        ctx.db.execute(
            "INSERT INTO metrics(entity_id, metric, value, observed_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(entity_id, metric, observed_at) DO UPDATE SET "
            "value = excluded.value",
            (m.entity_id, m.metric, m.value, obs),
        )


def collect(ctx: Ctx) -> InventoryResult:
    """Run system -> projects -> harness -> seeds -> classify, upsert DB, and diff usages."""
    now_iso = ctx.now_iso
    existing_rows = ctx.db.execute(
        "SELECT entity_id, context, version, first_seen FROM usages"
    ).fetchall()
    is_first_collect = len(existing_rows) == 0
    old_map: dict[tuple[str, str], tuple[str | None, str]] = {
        (str(r["entity_id"]), str(r["context"])): (
            str(r["version"]) if r["version"] is not None else None,
            str(r["first_seen"]),
        )
        for r in existing_rows
    }

    sys_out = system.collect(ctx)
    prj_out = projects.collect(
        ctx,
        extra_roots=sys_out.extra_roots,
        known_clis=sys_out.known_clis,
    )
    har_out = harness.collect(ctx, projects=prj_out.projects)
    seed_out = seeds.collect(
        ctx,
        cmd_bigrams=sys_out.cmd_bigrams,
        existing_entities=sys_out.entities,
    )

    all_entities = [
        *sys_out.entities,
        *prj_out.entities,
        *har_out.entities,
        *seed_out.entities,
    ]
    all_usages = [
        *sys_out.usages,
        *prj_out.usages,
        *har_out.usages,
        *seed_out.usages,
    ]
    all_fingerprints = [
        *sys_out.fingerprints,
        *prj_out.fingerprints,
        *har_out.fingerprints,
        *seed_out.fingerprints,
    ]

    usage_by_key: dict[tuple[str, str], UsageRecord] = {}
    for u in all_usages:
        key = (u.entity_id, u.context)
        existing_u = usage_by_key.get(key)
        if existing_u is None:
            usage_by_key[key] = u
        else:
            merged_attrs = {**existing_u.attrs, **u.attrs}
            usage_by_key[key] = UsageRecord(
                entity_id=u.entity_id,
                context=u.context,
                version=u.version or existing_u.version,
                direct=max(existing_u.direct, u.direct),
                attrs=merged_attrs,
            )

    known_entity_ids = {e.entity_id for e in all_entities}
    for u in usage_by_key.values():
        if u.entity_id not in known_entity_ids:
            all_entities.append(projects.entity_for_usage(u.entity_id))
            known_entity_ids.add(u.entity_id)

    _upsert_entities(ctx, all_entities, seed_out.bin_repo_links)
    _upsert_projects(ctx, prj_out.projects)

    for key, u in sorted(usage_by_key.items()):
        first_seen = old_map[key][1] if key in old_map else now_iso
        safe_ctx = db.scrub_text(u.context) or u.context
        safe_ver = db.scrub_text(u.version)
        safe_attrs = db.scrub_text(u.attrs_json)
        ctx.db.execute(
            "INSERT INTO usages("
            "entity_id, context, version, direct, first_seen, last_seen, attrs_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(entity_id, context) DO UPDATE SET "
            "version = excluded.version, "
            "direct = excluded.direct, "
            "last_seen = excluded.last_seen, "
            "attrs_json = excluded.attrs_json",
            (
                u.entity_id,
                safe_ctx,
                safe_ver,
                u.direct,
                first_seen,
                now_iso,
                safe_attrs,
            ),
        )

    old_keys = set(old_map.keys())
    new_keys = set(usage_by_key.keys())
    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)

    for eid, uctx in removed:
        ctx.db.execute(
            "DELETE FROM usages WHERE entity_id = ? AND context = ?",
            (eid, uctx),
        )

    _upsert_fingerprints(ctx, all_fingerprints)
    _upsert_metrics(ctx, har_out.metrics)
    ctx.db.commit()

    classify.classify_all(ctx)

    if not is_first_collect:
        for eid, uctx in added:
            db.insert_signal(
                ctx.db,
                source_id="inventory",
                signal_type="inventory_change",
                entity_id=eid,
                title=f"Added {eid} in {uctx}",
                fetched_at=now_iso,
                dedupe_key=f"inventory_change|added|{eid}|{uctx}|{now_iso}",
                payload_json=json.dumps(
                    {"change": "added", "entity_id": eid, "context": uctx},
                    separators=(",", ":"),
                ),
            )
        for eid, uctx in removed:
            db.insert_signal(
                ctx.db,
                source_id="inventory",
                signal_type="inventory_change",
                entity_id=eid,
                title=f"Removed {eid} from {uctx}",
                fetched_at=now_iso,
                dedupe_key=f"inventory_change|removed|{eid}|{uctx}|{now_iso}",
                payload_json=json.dumps(
                    {"change": "removed", "entity_id": eid, "context": uctx},
                    separators=(",", ":"),
                ),
            )

    dataless_total = sys_out.dataless_skipped + prj_out.dataless_skipped + har_out.dataless_skipped
    total_entities = int(ctx.db.execute("SELECT COUNT(*) FROM entities").fetchone()[0])
    counts: dict[str, Any] = {
        "inventory_baseline": is_first_collect,
        "entities": total_entities,
        "usages": len(new_keys),
        "projects": len(prj_out.projects),
        "added": len(added),
        "removed": len(removed),
        "dataless_skipped": dataless_total,
    }
    counts_str = json.dumps(counts, separators=(",", ":"), sort_keys=True)
    db.kv_set(ctx.db, "last_inventory_counts_json", counts_str)

    if ctx.run_id:
        run_row = ctx.db.execute(
            "SELECT counts_json FROM runs WHERE run_id = ?",
            (ctx.run_id,),
        ).fetchone()
        if run_row is not None:
            merged_counts = dict(counts)
            if run_row["counts_json"]:
                try:
                    existing_c = json.loads(str(run_row["counts_json"]))
                    if isinstance(existing_c, dict):
                        merged_counts = {**existing_c, **counts}
                except json.JSONDecodeError:
                    pass
            ctx.db.execute(
                "UPDATE runs SET counts_json = ? WHERE run_id = ?",
                (json.dumps(merged_counts, separators=(",", ":"), sort_keys=True), ctx.run_id),
            )
            ctx.db.commit()

    return InventoryResult(added=added, removed=removed, counts=counts)
