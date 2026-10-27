#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "pyyaml>=6.0.2",
#   "tomli-w>=1.1.0",
# ]
# ///
"""Automated Betterleaks v1/v2 unified rule-overlay compiler and validator.

Fetches public LeakTK (`leaktk/patterns`) and Google OSV-SCALIBR Veles (`veles.yml`)
rules, filters out rules that duplicate Betterleaks built-ins or legacy Generic Secret
noise, normalizes all capture groups to group 1 (so neither v1 `secretGroup` nor v2
`valueGroup` is required), translates legacy allowlists/entropy to pure Betterleaks v2
`Expr` `filter` expressions, validates with `betterleaks`, and atomically writes:
  - ~/.config/betterleaks/overlay-v2.toml (pure `[extend] useDefault = true` v2 config)
  - ~/.config/betterleaks/betterleaks-unified.toml (active runtime config for installed v1/v2)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import fcntl
import json
import math
import os
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import tomli_w
import yaml

_YAML_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _resolve_config_dir(config_dir: Path | None = None) -> Path:
    if config_dir is not None:
        return config_dir
    env_dir = os.environ.get("BETTERLEAKS_CONFIG_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".config" / "betterleaks"


LEAKTK_PUBLIC_URL = (
    "https://raw.githubusercontent.com/leaktk/patterns/main/target/patterns/gitleaks/8.27.0"
)
VELES_YAML_URL = "https://raw.githubusercontent.com/mongodb/kingfisher/main/crates/kingfisher-rules/generated/rules/veles.yml"

CURATED_LEAKTK_RULE_IDS: dict[str, str] = {
    "KWxkfMbO7cM": "leaktk-flag",
    "nPY_Rcj4gzY": "leaktk-argocd-jwt",
    "FrTqjJHAoEY": "leaktk-stackrox-jwt",
    "lSFsU9hapFQ": "leaktk-testing-farm-api-token",
    "vAAom0bPHy8": "leaktk-k8s-service-account-jwt",
    "12NI0Yd57gc": "leaktk-cui-marking",
    "gpfGmO3HH64": "leaktk-container-registry-auth",
    "rZUmm0ozLWQ": "leaktk-vault-approle-secret-id",
    "8nWBN4ZoGf8": "leaktk-htpasswd-file",
    "tSFtnbvWdlk": "leaktk-npmrc-auth",
    "FxXgdaIgf7Y": "leaktk-wp-config",
    "RA8jFaMQxIg": "leaktk-pkcs12-file",
    "NoSStdj9pfY": "leaktk-url-user-password",
    "Erxpc1eNAD0": "leaktk-vulnerable-gh-workflow",
    "bYudTkTa56I": "leaktk-password-hash-yescrypt",
    "fqtWnpN0lfA": "leaktk-password-hash-gost-yescrypt",
    "MxYvbqkT9IM": "leaktk-password-hash-scrypt",
    "RX7HEzTc6dI": "leaktk-password-hash-bcrypt",
    "amXYPF5An5s": "leaktk-password-hash-sha512crypt",
    "u9MfgjhX7SI": "leaktk-password-hash-sha256crypt",
    "iGgJnI-f5Xk": "leaktk-password-hash-md5crypt",
    "AeVJW2sPHGs": "leaktk-password-hash-apr1",
}

CURATED_VELES_RULES: dict[str, list[str]] = {
    "veles.secrets/bitbucketcredentials": ["@bitbucket.org/"],
    "veles.secrets/bitwardenoauth2access": ["_token_apikeyclientsecret"],
    "veles.secrets/codecatalystcredentials": ["codecatalyst.aws/"],
    "veles.secrets/codecommitcredentials": ["@git-codecommit."],
    "veles.secrets/denopatorg": ["ddo_"],
    "veles.secrets/hcpclientcredentials": ["hcp_client_secret"],
    "veles.secrets/hcpclientcredentials-client-id": ["hcp_client_id"],
    "veles.secrets/packagistorgreadtoken": ["packagist_ort_"],
    "veles.secrets/packagistorgreadtoken-repository": ["repo.packagist.com"],
    "veles.secrets/packagistorgupdatetoken": ["packagist_out_"],
    "veles.secrets/packagistorgupdatetoken-repository": ["repo.packagist.com"],
    "veles.secrets/packagistsecret": ["packagist_acs_"],
    "veles.secrets/packagistsecret-api-key": ["packagist_ack_"],
    "veles.secrets/postmancollectiontoken": ["pmat-"],
}

NOTSECRET_FILTER_EXPR = (
    r'matchesAny(finding["line"], [`(?i)\bnot[\t\x20\-_]*secret\b`]) || '
    r'matchesAny(finding["secret"], [`^(?:env\.|os\.environ/)[A-Z_][A-Z0-9_]*$`])'
)


def _write_secure(path: Path, content: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        try:
            os.write(fd, content.encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _fetch_or_cache(url: str, cache_file: Path, offline: bool = False) -> str:
    cache_dir = cache_file.parent
    cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(cache_dir, 0o700)
    if not offline:
        try:
            proc = subprocess.run(
                ["curl", "-fsSL", "--connect-timeout", "10", "--max-time", "30", url],
                capture_output=True,
                text=True,
                check=True,
            )
            if proc.stdout.strip():
                _write_secure(cache_file, proc.stdout)
                return proc.stdout
            raise RuntimeError(f"Empty response from {url}")
        except Exception:
            if cache_file.is_file():
                return cache_file.read_text(encoding="utf-8")
            raise
    if cache_file.is_file():
        return cache_file.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Offline mode requested and cache missing: {cache_file}")


def _quote_expr_regex(pattern: str) -> str:
    return f"`{pattern}`" if "`" not in pattern else json.dumps(pattern)


def _translate_allowlist_to_v2_expr(rule: dict[str, Any]) -> str | None:
    parts: list[str] = []
    if "entropy" in rule:
        entropy_val = float(rule["entropy"])
        if not math.isfinite(entropy_val):
            raise ValueError(
                f"Non-finite entropy value in rule {rule.get('id')!r}: {rule['entropy']!r}"
            )
        parts.append(f'entropy(finding["secret"]) <= {entropy_val}')

    als: list[dict[str, Any]] = []
    if isinstance(rule.get("allowlist"), dict):
        als.append(rule["allowlist"])
    if isinstance(rule.get("allowlists"), list):
        als.extend(x for x in rule["allowlists"] if isinstance(x, dict))

    for al in als:
        clauses: list[str] = []
        paths = al.get("paths", [])
        if paths:
            items = ",\n  ".join(_quote_expr_regex(p) for p in paths)
            clauses.append(f'matchesAny(attributes["path"], [\n  {items}\n])')
        regexes = al.get("regexes", [])
        if regexes:
            target = str(al.get("regexTarget", "secret")).lower()
            if target not in {"secret", "match", "line"}:
                target = "secret"
            items = ",\n  ".join(_quote_expr_regex(r) for r in regexes)
            clauses.append(f'matchesAny(finding["{target}"], [\n  {items}\n])')
        stopwords = al.get("stopwords", [])
        if stopwords:
            items = ",\n  ".join(json.dumps(s) for s in stopwords)
            clauses.append(f'containsAny(finding["secret"], [\n  {items}\n])')
        if clauses:
            joiner = " && " if str(al.get("condition", "OR")).upper() == "AND" else " || "
            parts.append("(" + joiner.join(clauses) + ")" if len(clauses) > 1 else clauses[0])

    if not parts:
        return None
    return " || ".join(parts)


def build_v2_compatible_overlay_rules(
    offline: bool = False,
    config_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Build curated LeakTK + Veles rules using only the v1+v2 intersection schema."""
    cfg_dir = _resolve_config_dir(config_dir)
    cache_dir = cfg_dir / "cache"
    lt_cache = cache_dir / "leaktk-public-8.27.0.toml"
    veles_cache = cache_dir / "veles.yml"

    if offline:
        lt_text = _fetch_or_cache(LEAKTK_PUBLIC_URL, lt_cache, offline=True)
        veles_text = _fetch_or_cache(VELES_YAML_URL, veles_cache, offline=True)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            lt_future = pool.submit(_fetch_or_cache, LEAKTK_PUBLIC_URL, lt_cache, False)
            veles_future = pool.submit(_fetch_or_cache, VELES_YAML_URL, veles_cache, False)
            lt_text = lt_future.result()
            veles_text = veles_future.result()

    lt_doc = tomllib.loads(lt_text)
    veles_doc = yaml.load(veles_text, Loader=_YAML_SAFE_LOADER)
    if not isinstance(veles_doc, dict):
        raise RuntimeError("Invalid Veles YAML structure: expected mapping at top level")

    overlay_rules: list[dict[str, Any]] = []

    for r in lt_doc.get("rules", []):
        orig_id = r["id"]
        if orig_id not in CURATED_LEAKTK_RULE_IDS:
            continue
        readable_id = CURATED_LEAKTK_RULE_IDS[orig_id]
        rule_out: dict[str, Any] = {
            "id": readable_id,
            "description": str(r.get("description", readable_id)),
        }
        if "regex" in r:
            if orig_id == "8nWBN4ZoGf8":
                rule_out["regex"] = (
                    r'(?:^|\n)[^:\s"/]{1,64}:((?:\$(?:apr1|2[abxy]?|5|6)\$|\{SHA\})\S{10,250})'
                )
            else:
                rule_out["regex"] = str(r["regex"])
        if "path" in r:
            rule_out["path"] = str(r["path"])
        if "keywords" in r:
            rule_out["keywords"] = [str(k).lower() for k in r["keywords"]]
        if "tags" in r:
            rule_out["tags"] = [str(t) for t in r["tags"]]
        rule_out["confidence"] = "high"

        expr_filter = _translate_allowlist_to_v2_expr(r)
        if expr_filter:
            rule_out["filter"] = expr_filter
        overlay_rules.append(rule_out)

    for vr in veles_doc.get("rules", []):
        vid = str(vr["id"])
        if vid not in CURATED_VELES_RULES:
            continue
        pattern = str(vr["pattern"])
        if vid == "veles.secrets/bitwardenoauth2access":
            pattern = pattern.replace("(?P<TOKEN_ID>", "(?:")

        v_out: dict[str, Any] = {
            "id": vid,
            "description": str(vr.get("name") or vid),
            "regex": pattern,
            "keywords": CURATED_VELES_RULES[vid],
            "tags": ["type:secret", "source:veles"],
            "confidence": "high",
        }
        if vr.get("path"):
            v_out["path"] = str(vr["path"])
        if vr.get("visible") is False:
            v_out["skipReport"] = True
        deps = vr.get("depends_on_rule") or []
        if deps:
            v_out["components"] = [
                {"id": str(d["rule_id"])} for d in deps if isinstance(d, dict) and d.get("rule_id")
            ]
        overlay_rules.append(v_out)

    compiled_ids = {r["id"] for r in overlay_rules}
    missing_leaktk = sorted(set(CURATED_LEAKTK_RULE_IDS.values()) - compiled_ids)
    missing_veles = sorted(set(CURATED_VELES_RULES.keys()) - compiled_ids)
    if missing_leaktk or missing_veles:
        raise RuntimeError(
            "Curated overlay rule verification failed; "
            f"missing LeakTK={missing_leaktk}, Veles={missing_veles}"
        )

    return overlay_rules


def _installed_betterleaks_info() -> tuple[str, bool, dict[str, Any]]:
    ver_out = subprocess.check_output(["betterleaks", "--version"], text=True).strip()
    is_v2 = bool(re.search(r"(?:^|[^0-9])2\.\d+\.\d+", ver_out))
    if is_v2:
        return ver_out, True, {}
    cfg_out = subprocess.check_output(["betterleaks", "config", "show"], text=True)
    cfg_doc = tomllib.loads(cfg_out)
    return ver_out, False, cfg_doc


def _validate_and_smoke_test(config_path: Path, is_v2: bool) -> None:
    if is_v2:
        subprocess.run(
            ["betterleaks", "config", "check", "--config", str(config_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        subprocess.run(
            ["betterleaks", "config", "show", "--config", str(config_path)],
            check=True,
            capture_output=True,
            text=True,
        )

    # Assemble smoke-test tokens at runtime so this source file does not trigger secret scanners
    deno_tok = "ddo_" + "9Km4Lp7Qr2Vx8Bn5Ct1Zy6Wd3Jf0HgSqW8eA"
    ghp_tok = "ghp_" + "9Km4Lp7Qr2Vx8Bn5Ct1Zy6Wd3Jf0HgSqW8eA"
    flag_sec = "LTKF{" + "secret}"
    flag_pub = "LTKF{" + "public}"
    sample = (
        f'DENO_ORG = "{deno_tok}"\n'
        f'FLAG_HIT = "{flag_sec}"\n'
        f'FLAG_PUB = "{flag_pub}"\n'
        f'IGNORED = "{ghp_tok}" # notsecret\n'
    )
    cmd = [
        "betterleaks",
        "stdin",
        "--exit-code",
        "0",
        "--confidence",
        "medium",
        "--config",
        str(config_path),
    ]
    if is_v2:
        cmd.extend(["--format", "json", "--output", "-"])
    else:
        cmd.extend(["--report-format", "json", "--report-path", "-"])

    proc = subprocess.run(cmd, input=sample, capture_output=True, text=True, check=True)
    parsed = json.loads(proc.stdout)
    findings = parsed.get("findings", []) if isinstance(parsed, dict) else parsed
    if len(findings) != 2:
        raise RuntimeError(
            f"Smoke test expected 2 findings (DENO_ORG + LTKF{{secret}}), got {len(findings)}"
        )


def sync_all(offline: bool = False, config_dir: Path | None = None) -> dict[str, Any]:
    cfg_dir = _resolve_config_dir(config_dir)
    cfg_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(cfg_dir, 0o700)

    overlay_v2_path = cfg_dir / "overlay-v2.toml"
    unified_active_path = cfg_dir / "betterleaks-unified.toml"
    version_stamp_path = cfg_dir / ".compiled-for-version"
    lock_path = cfg_dir / ".sync.lock"

    lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        ver_str, is_v2, builtin_doc = _installed_betterleaks_info()
        overlay_rules = build_v2_compatible_overlay_rules(offline=offline, config_dir=cfg_dir)

        v2_overlay_doc: dict[str, Any] = {
            "title": "Betterleaks v2 Unified Overlay (Curated LeakTK + Google Veles)",
            "extend": {"useDefault": True},
            "filter": NOTSECRET_FILTER_EXPR,
            "rules": overlay_rules,
        }
        v2_overlay_toml = tomli_w.dumps(v2_overlay_doc, multiline_strings=True)
        _write_secure(overlay_v2_path, v2_overlay_toml)

        staging_path = unified_active_path.with_name(
            f"{unified_active_path.name}.{os.getpid()}.staging"
        )
        try:
            if is_v2:
                _write_secure(staging_path, v2_overlay_toml)
            else:
                merged_doc = copy.deepcopy(builtin_doc)
                merged_doc["title"] = (
                    "Betterleaks Unified Config (Built-in + Curated LeakTK + Google Veles)"
                )
                base_filter = str(merged_doc.get("filter", "")).strip()
                merged_doc["filter"] = (
                    f"({base_filter}) || ({NOTSECRET_FILTER_EXPR})"
                    if base_filter
                    else NOTSECRET_FILTER_EXPR
                )
                existing_ids = {r["id"] for r in merged_doc.get("rules", [])}
                for r in overlay_rules:
                    if r["id"] not in existing_ids:
                        merged_doc.setdefault("rules", []).append(r)
                _write_secure(staging_path, tomli_w.dumps(merged_doc, multiline_strings=True))

            _validate_and_smoke_test(staging_path, is_v2=is_v2)
            os.replace(staging_path, unified_active_path)
        except BaseException:
            staging_path.unlink(missing_ok=True)
            raise

        _write_secure(version_stamp_path, ver_str + "\n")

        builtin_rules_list = builtin_doc.get("rules", [])
        builtin_ids = {r["id"] for r in builtin_rules_list if isinstance(r, dict) and "id" in r}
        overlay_ids = {r["id"] for r in overlay_rules}

        return {
            "installed_betterleaks": ver_str,
            "schema_mode": "v2_extend_overlay" if is_v2 else "v1_merged_v2_ready_rules",
            "overlay_v2_path": str(overlay_v2_path),
            "active_unified_path": str(unified_active_path),
            "curated_overlay_rules": len(overlay_rules),
            "builtin_rules": len(builtin_rules_list),
            "total_active_rules": len(builtin_ids | overlay_ids),
        }
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync and validate Betterleaks v1/v2 unified rule overlay."
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Compile from cached feeds without network calls",
    )
    args = parser.parse_args()
    summary = sync_all(offline=args.offline)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
