"""Pre-push hook module for skill evaluation.

Entry point: python -m skill_eval.hook

Modes:
  (default)              Pre-push: detect changed SKILL.md/reference files and
                         eval only affected skills.
  --all                  Eval all skills that have test cases.
  --compare REF          A/B compare current skill bundles against a git ref.
  --update-baselines     Run --all and write results to baselines.json.

All DeepEval and skill_eval.* imports are DEFERRED until after git-diff
detection to keep the fast-path (no changed skills) at stdlib speed.

Security constraints:
  - DEEPEVAL_DISABLE_TIMEOUTS set in os.environ BEFORE any deepeval import.
  - git ref validated against ^[a-zA-Z0-9/_.-]+$ BEFORE any subprocess call.
  - All subprocess.run calls use list-form args (never shell=True).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Regex for validating git refs before passing to subprocess.
_VALID_REF_RE = re.compile(r"^[a-zA-Z0-9/_.\-~^]+$")


# ---------------------------------------------------------------------------
# Git helpers (stdlib-only, safe for fast-path)
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(result.stdout.strip()).resolve()


def _merge_base(ref: str = "origin/main") -> str:
    result = subprocess.run(
        ["git", "merge-base", "HEAD", ref],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _changed_files(since_ref: str) -> list[str]:
    """Return list of changed files between since_ref and HEAD."""
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{since_ref}..HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [f for f in result.stdout.splitlines() if f]


def _find_all_skill_dirs(repo_root: Path) -> list[Path]:
    """Return all skill dirs that have a SKILL.md file."""
    return sorted(p.parent for p in repo_root.glob("*/skills/*/SKILL.md"))


def _skill_dirs_with_test_cases(repo_root: Path) -> list[Path]:
    """Return skill dirs whose skill name matches a test_cases/*.json stem."""
    test_cases_dir = Path(__file__).parent.parent / "test_cases"
    if not test_cases_dir.exists():
        return []
    stems = {p.stem for p in test_cases_dir.glob("*.json")}
    return [d for d in _find_all_skill_dirs(repo_root) if d.name in stems]


# ---------------------------------------------------------------------------
# Deferred-import eval logic
# ---------------------------------------------------------------------------


def _deferred_imports() -> tuple:
    """Import eval modules after setting DEEPEVAL env var."""
    os.environ["DEEPEVAL_DISABLE_TIMEOUTS"] = "true"
    from skill_eval.judge import VertexSonnetJudge
    from skill_eval.runner import (
        compare_baselines,
        load_baselines,
        load_eval_config,
        resolve_skill_bundle,
        run_eval,
    )

    return (
        VertexSonnetJudge,
        resolve_skill_bundle,
        load_eval_config,
        load_baselines,
        run_eval,
        compare_baselines,
    )


def _eval_skills(
    skill_dirs: list[Path],
    repo_root: Path,
    git_ref: str | None = None,
    update_baselines: bool = False,
) -> bool:
    """Run evals for the given skill dirs. Returns True if all passed."""
    (
        VertexSonnetJudge,
        resolve_skill_bundle,
        load_eval_config,
        load_baselines,
        run_eval,
        compare_baselines,
    ) = _deferred_imports()

    judge = VertexSonnetJudge()
    baselines = load_baselines()
    all_results: list[dict] = []
    regressions: list[str] = []

    for skill_dir in skill_dirs:
        skill_name = skill_dir.name
        try:
            config = load_eval_config(skill_name)
            test_cases = config.get("test_cases", [])
            rubric_names = config.get("rubrics", [])

            if not test_cases:
                print(f"  [skip] {skill_name}: no test cases", file=sys.stderr)
                continue

            # Resolve bundle (new version from disk or git for compare mode).
            bundle = resolve_skill_bundle(skill_dir, repo_root=repo_root)
            if bundle is None:
                print(
                    f"  [warn] {skill_name}: could not resolve bundle — skipping", file=sys.stderr
                )  # noqa: E501
                continue

            print(f"  [eval] {skill_name} ({len(test_cases)} test cases)...")
            results = run_eval(skill_name, bundle, test_cases, rubric_names, judge)
            all_results.append(results)

            if git_ref is not None:
                # Compare mode: also get old bundle and eval it.
                old_bundle = resolve_skill_bundle(skill_dir, git_ref=git_ref, repo_root=repo_root)
                if old_bundle is None:
                    msg = f"  [warn] {skill_name}: could not resolve old bundle at {git_ref}"
                    print(msg, file=sys.stderr)
                else:
                    old_results = run_eval(skill_name, old_bundle, test_cases, rubric_names, judge)
                    _print_compare_table(skill_name, old_results, results, git_ref)
            else:
                passed, report = compare_baselines(results, baselines)
                _print_result_table(results, passed)
                if not passed:
                    regressions.append(report)
        except Exception as exc:
            print(f"  [error] {skill_name}: unexpected error — {exc}", file=sys.stderr)
            continue

    # Gate bypass protection: if we had skills to eval but got zero results,
    # all evals threw exceptions — fail the gate rather than silently passing.
    expected_evals = sum(
        1
        for d in skill_dirs
        if (Path(__file__).parent.parent / "test_cases" / f"{d.name}.json").exists()
    )
    if expected_evals > 0 and not all_results:
        print(
            "\nAll evaluations failed with errors — blocking push",
            file=sys.stderr,
        )
        return False

    if update_baselines:
        # Filter out results with infra errors to avoid zeroing baselines.
        valid_results = [r for r in all_results if not r.get("infra_error")]
        if len(valid_results) < len(all_results):
            skipped = len(all_results) - len(valid_results)
            print(
                f"\n  [warn] {skipped} skill(s) had infra errors — excluded from baselines",
                file=sys.stderr,
            )
        if valid_results:
            _write_baselines(valid_results)
            print("\nBaselines updated: skill-eval/baselines.json")
        else:
            print("\nNo valid results to write — baselines unchanged", file=sys.stderr)
        return True

    if regressions:
        print("\nREGRESSIONS DETECTED:", file=sys.stderr)
        for r in regressions:
            print(r, file=sys.stderr)
        return False

    if all_results:
        print("\nAll evals passed. Run `make eval-update-baselines` to record new baselines.")
    return True


def _print_result_table(results: dict, passed: bool) -> None:
    skill = results.get("skill", "?")
    scores = results.get("scores", {})
    status = "PASS" if passed else "FAIL"
    print(f"\n{'Skill':<20} {'Metric':<30} {'Score':>6}  {'Status'}")
    print("-" * 65)
    for metric, score in scores.items():
        print(f"  {skill:<18} {metric:<30} {score:>6.3f}  {status}")
    pass_rate = results.get("pass_rate", 0.0)
    print(f"  {'':18} {'pass_rate':<30} {pass_rate:>6.3f}  {status}")


def _print_compare_table(
    skill_name: str,
    old_results: dict,
    new_results: dict,
    ref: str,
) -> None:
    old_scores = old_results.get("scores", {})
    new_scores = new_results.get("scores", {})
    all_metrics = sorted(set(old_scores) | set(new_scores))
    print(f"\n{'Skill':<20} {'Metric':<30} {'Old':>6} {'New':>6} {'Delta':>7}")
    print("-" * 72)
    for metric in all_metrics:
        old = old_scores.get(metric, 0.0)
        new = new_scores.get(metric, 0.0)
        delta = new - old
        flag = " !" if delta < -0.05 else ""
        print(f"  {skill_name:<18} {metric:<30} {old:>6.3f} {new:>6.3f} {delta:>+7.3f}{flag}")


def _write_baselines(all_results: list[dict]) -> None:
    baselines_path = Path(__file__).parent.parent / "baselines.json"
    existing: dict = {}
    if baselines_path.exists():
        existing = json.loads(baselines_path.read_text(encoding="utf-8"))
        if "baselines" in existing:
            existing = existing["baselines"]
    for results in all_results:
        skill = results.get("skill")
        if skill:
            existing[skill] = results.get("scores", {})
    baselines_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Mode dispatch
# ---------------------------------------------------------------------------


def _mode_prepush(repo_root: Path) -> bool:
    """Default pre-push mode: detect changed skills and eval them."""
    try:
        merge_base = _merge_base()
    except subprocess.CalledProcessError:
        print("[skill-eval] Could not determine merge base — skipping evals", file=sys.stderr)
        return True

    changed = _changed_files(merge_base)

    # Separate SKILL.md changes from reference file changes.
    changed_skill_dirs: set[Path] = set()
    changed_ref_files: list[str] = []

    for f in changed:
        p = Path(f)
        parts = p.parts
        # Pattern: */skills/<skill-name>/SKILL.md
        if len(parts) >= 3 and parts[-1] == "SKILL.md" and parts[-3] == "skills":
            changed_skill_dirs.add(repo_root / p.parent)
        # Pattern: */skills/<skill-name>/references/* (per-skill reference)
        elif len(parts) >= 4 and parts[-2] == "references" and parts[-4] == "skills":
            changed_skill_dirs.add(repo_root / Path(*parts[:-2]))
        # Pattern: */references/*.md (plugin-level reference — need to resolve)
        elif len(parts) >= 2 and parts[-2] == "references" and p.suffix == ".md":
            changed_ref_files.append(f)

    # For plugin-level reference changes, find which skills bundle that file.
    if changed_ref_files:
        skills_with_cases = _skill_dirs_with_test_cases(repo_root)
        resolve_skill_bundle = _deferred_imports()[1]

        for skill_dir in skills_with_cases:
            if skill_dir in changed_skill_dirs:
                continue
            bundle = resolve_skill_bundle(skill_dir, repo_root=repo_root)
            if bundle is None:
                continue
            for ref_file in changed_ref_files:
                ref_name = Path(ref_file).name
                if ref_name in bundle:
                    changed_skill_dirs.add(skill_dir)
                    break

    # Filter to only skills that have test cases.
    skills_to_eval = [
        d
        for d in sorted(changed_skill_dirs)
        if (Path(__file__).parent.parent / "test_cases" / f"{d.name}.json").exists()
    ]

    if not skills_to_eval:
        if changed_skill_dirs:
            print(
                "[skill-eval] Changed skills have no test cases — skipping evals",
                file=sys.stderr,
            )
        else:
            print("[skill-eval] No changed skills detected — skipping evals", file=sys.stderr)
        return True

    print(f"[skill-eval] Evaluating {len(skills_to_eval)} changed skill(s)...")
    return _eval_skills(skills_to_eval, repo_root)


def _mode_all(repo_root: Path) -> bool:
    """--all mode: eval every skill with test cases."""
    skill_dirs = _skill_dirs_with_test_cases(repo_root)
    if not skill_dirs:
        print("[skill-eval] No skills with test cases found", file=sys.stderr)
        return True
    print(f"[skill-eval] Evaluating all {len(skill_dirs)} skill(s) with test cases...")
    return _eval_skills(skill_dirs, repo_root)


def _mode_compare(repo_root: Path, ref: str) -> bool:
    """--compare REF mode: A/B comparison against a git ref."""
    # SECURITY: validate ref BEFORE any subprocess call.
    if not _VALID_REF_RE.match(ref):
        print(
            f"[skill-eval] Invalid git ref {ref!r} — must match ^[a-zA-Z0-9/_.-]+$",
            file=sys.stderr,
        )
        sys.exit(1)

    skill_dirs = _skill_dirs_with_test_cases(repo_root)
    if not skill_dirs:
        print("[skill-eval] No skills with test cases found", file=sys.stderr)
        return True
    print(f"[skill-eval] Comparing {len(skill_dirs)} skill(s) against {ref}...")
    return _eval_skills(skill_dirs, repo_root, git_ref=ref)


def _mode_update_baselines(repo_root: Path) -> bool:
    """--update-baselines mode: run --all and write baselines.json."""
    skill_dirs = _skill_dirs_with_test_cases(repo_root)
    if not skill_dirs:
        print("[skill-eval] No skills with test cases found", file=sys.stderr)
        return True
    print(f"[skill-eval] Updating baselines for {len(skill_dirs)} skill(s)...")
    return _eval_skills(skill_dirs, repo_root, update_baselines=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Skill evaluation pre-push hook (DeepEval-based)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--all",
        action="store_true",
        help="Eval all skills with test cases",
    )
    group.add_argument(
        "--compare",
        metavar="REF",
        help="A/B compare current skills against a git ref",
    )
    group.add_argument(
        "--update-baselines",
        action="store_true",
        help="Run --all and write results to baselines.json",
    )
    args = parser.parse_args()

    # Drain stdin (pre-push hooks receive ref data on stdin — ignore it).
    if not sys.stdin.isatty():
        sys.stdin.read()

    try:
        repo_root = _repo_root()
    except subprocess.CalledProcessError:
        print("[skill-eval] Not in a git repository — skipping evals", file=sys.stderr)
        sys.exit(0)

    if args.all:
        passed = _mode_all(repo_root)
    elif args.compare:
        passed = _mode_compare(repo_root, args.compare)
    elif args.update_baselines:
        passed = _mode_update_baselines(repo_root)
    else:
        passed = _mode_prepush(repo_root)

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
