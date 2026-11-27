"""Unit, integration, fixture-corpus, and timing tests for review_rules.rules and lint CLI."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from review_rules.cli import main
from review_rules.paths import StoreInsecure, ensure_store_dir
from review_rules.rules import (
    CATEGORY_DEFAULT_REVIEWERS,
    MAX_RULE_BYTES,
    RulesUnavailable,
    closed_form_violations,
    default_rule_metadata,
    lint_directory,
    load_rules,
    run_git,
    validate_rule,
)
from tests.conftest import RulesRepo

_REVIEW_RULES_ROOT = Path(__file__).resolve().parents[1]
_STATEMENTS_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "statements.yaml"


def _make_rule_dict(
    rule_id: str = "r-sample-rule",
    *,
    status: str = "candidate",
    statement: str = "Never ignore errors from subprocess calls",
    rationale: str = "Swallowing subprocess failures hides real errors.",
    do_not_apply_when: list[str] | None = None,
    approved: dict[str, str] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": rule_id,
        "status": status,
        "trial": True,
        "category": "correctness",
        "statement": statement,
        "rationale": rationale,
        "do_not_apply_when": [] if do_not_apply_when is None else do_not_apply_when,
        "scope": {
            "repos": ["*"],
            "globs": ["**/*.py"],
            "languages": ["python"],
            "reviewers": ["correctness"],
            "skills": ["*"],
        },
        "evidence": [
            {
                "event_id": "seed-0123456789abcdef",
                "kind": "seed",
                "strength": "strong",
                "author_class": "owner",
            }
        ],
        "provenance": {
            "created_by": "human",
            "created_at": "2026-11-20",
        },
        "review_by": "2027-02-18",
    }
    if approved is not None:
        data["approved"] = approved
    elif status == "active":
        data["approved"] = {"by": "owner", "at": "2026-11-20"}
    return data


def _dump_rule_yaml(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


class TestPinnedLoading:
    def test_pinned_ignores_working_tree_and_loads_pushed_rule(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rules_repo: RulesRepo
    ) -> None:
        monkeypatch.setenv("REVIEW_RULES_DB", str(tmp_path / "store" / "review.db"))

        # Empty rules/ (only .gitkeep) loads cleanly with no rejections.
        empty_res = load_rules(rules_repo.path, fetch=False)
        assert empty_res.sha == rules_repo.origin_sha()
        assert empty_res.active == ()
        assert empty_res.candidates == ()
        assert empty_res.retired == ()
        assert empty_res.rejected == ()

        # Uncommitted working-tree rule is never returned.
        uncommitted = rules_repo.path / "rules" / "r-uncommitted.yaml"
        uncommitted.write_text(
            _dump_rule_yaml(_make_rule_dict("r-uncommitted")),
            encoding="utf-8",
        )
        assert load_rules(rules_repo.path, fetch=False).candidates == ()

        # Committed locally (unpushed) is also ignored until pushed to origin/main.
        rules_repo.commit_file(
            "rules/r-unpushed.yaml",
            _dump_rule_yaml(_make_rule_dict("r-unpushed")),
            push=False,
        )
        assert load_rules(rules_repo.path, fetch=False).candidates == ()

        # Pushed rule is loaded from refs/remotes/origin/main^{commit}.
        pushed_sha = rules_repo.commit_file(
            "rules/r-pushed.yaml",
            _dump_rule_yaml(_make_rule_dict("r-pushed", status="active")),
            push=True,
        )
        res = load_rules(rules_repo.path, fetch=False)
        assert res.sha == pushed_sha
        assert res.sha == rules_repo.origin_sha()
        assert [r.id for r in res.active] == ["r-pushed"]
        assert [r.id for r in res.candidates] == ["r-unpushed"]

    def test_pinned_fetch_failure_uses_existing_ref_and_stamp_caches_fetch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rules_repo: RulesRepo
    ) -> None:
        monkeypatch.setenv("REVIEW_RULES_DB", str(tmp_path / "store" / "review.db"))
        ensure_store_dir()

        rules_repo.commit_file(
            "rules/r-first.yaml",
            _dump_rule_yaml(_make_rule_dict("r-first")),
            push=True,
        )
        res1 = load_rules(rules_repo.path, fetch=True)
        assert res1.fetched is True
        assert res1.fetch_error is None

        # Second fetch within 60s hits stamp file and skips network/git fetch.
        res2 = load_rules(rules_repo.path, fetch=True)
        assert res2.fetched is False
        assert res2.fetch_error is None

        # Clear stamp and delete bare origin: fetch fails gracefully and uses existing ref.
        for stamp in (tmp_path / "store").glob("fetch-stamp-*"):
            stamp.unlink()
        shutil.rmtree(rules_repo.origin)

        res3 = load_rules(rules_repo.path, fetch=True)
        assert res3.fetched is False
        assert res3.fetch_error is not None and len(res3.fetch_error) > 0
        assert [r.id for r in res3.candidates] == ["r-first"]

    def test_pinned_missing_repo_missing_ref_and_schema_drift_raise_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rules_repo: RulesRepo
    ) -> None:
        monkeypatch.setenv("REVIEW_RULES_DB", str(tmp_path / "store" / "review.db"))

        with pytest.raises(RulesUnavailable):
            load_rules(tmp_path / "does-not-exist", fetch=False)

        no_origin_repo = tmp_path / "no-origin"
        no_origin_repo.mkdir()
        no_origin = RulesRepo(path=no_origin_repo, origin=tmp_path / "unused")
        no_origin.git("init", "-b", "main")
        no_origin.git("config", "user.email", "test@example.com")
        no_origin.git("config", "user.name", "Test User")
        (no_origin_repo / "README.md").write_text("hi\n", encoding="utf-8")
        no_origin.git("add", "README.md")
        no_origin.git("commit", "-m", "init")
        with pytest.raises(RulesUnavailable):
            load_rules(no_origin_repo, fetch=False)

        # Schema edit in rules repo triggers schema drift refusal.
        schema_data = json.loads(
            (rules_repo.path / "schema" / "rule.schema.json").read_text(encoding="utf-8")
        )
        schema_data["title"] = "Tampered Title"
        rules_repo.commit_file(
            "schema/rule.schema.json",
            json.dumps(schema_data, indent=2) + "\n",
            push=True,
        )
        with pytest.raises(RulesUnavailable, match="schema drift"):
            load_rules(rules_repo.path, fetch=False)

    def test_pinned_rejects_symlink_and_oversize_blobs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rules_repo: RulesRepo
    ) -> None:
        monkeypatch.setenv("REVIEW_RULES_DB", str(tmp_path / "store" / "review.db"))

        # Commit a git symlink (120000) under rules/
        os.symlink("../README.md", rules_repo.path / "rules" / "r-link.yaml")
        rules_repo.git("add", "rules/r-link.yaml")
        rules_repo.git("commit", "-m", "add symlink")
        rules_repo.git("push", "origin", "main")

        # Commit a >8 KB rule blob under rules/
        oversize = _make_rule_dict("r-big", rationale="A" * 500)
        raw_big = _dump_rule_yaml(oversize) + ("\n# " + "x" * 8200 + "\n")
        assert len(raw_big.encode("utf-8")) > MAX_RULE_BYTES
        rules_repo.commit_file("rules/r-big.yaml", raw_big, push=True)

        res = load_rules(rules_repo.path, fetch=False)
        rej_by_path = {r.path: r.reason for r in res.rejected}
        assert "rules/r-link.yaml" in rej_by_path
        assert "120000" in rej_by_path["rules/r-link.yaml"]
        assert "rules/r-big.yaml" in rej_by_path
        assert str(MAX_RULE_BYTES) in rej_by_path["rules/r-big.yaml"]

    def test_pinned_crlf_schema_drift_and_literal_cr_rejection(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rules_repo: RulesRepo
    ) -> None:
        monkeypatch.setenv("REVIEW_RULES_DB", str(tmp_path / "store" / "review.db"))

        # Literal \r inside rationale in committed rule YAML must fail U+000D validation.
        cr_yaml = _dump_rule_yaml(_make_rule_dict("r-cr-rat")).replace(
            "Swallowing subprocess failures hides real errors.",
            '"line1\\rline2"',
        )
        rules_repo.commit_file("rules/r-cr-rat.yaml", cr_yaml, push=True)
        res_cr = load_rules(rules_repo.path, fetch=False)
        rej_by_path = {r.path: r.reason for r in res_cr.rejected}
        assert "rules/r-cr-rat.yaml" in rej_by_path
        assert "U+000D" in rej_by_path["rules/r-cr-rat.yaml"]

        # CRLF bytes in schema/rule.schema.json must trigger schema drift refusal.
        raw_schema = (rules_repo.path / "schema" / "rule.schema.json").read_bytes()
        crlf_schema = raw_schema.replace(b"\n", b"\r\n")
        rules_repo.commit_file("schema/rule.schema.json", crlf_schema, push=True)
        with pytest.raises(RulesUnavailable, match="schema drift"):
            load_rules(rules_repo.path, fetch=False)

    def test_run_git_ignores_inherited_git_dir_and_uses_ceiling_dirs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rules_repo: RulesRepo
    ) -> None:
        other_repo = tmp_path / "other-repo"
        other_repo.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main", str(other_repo)],
            check=True,
            capture_output=True,
        )
        monkeypatch.setenv("GIT_DIR", str(other_repo / ".git"))

        top_proc = run_git(rules_repo.path, ["rev-parse", "--show-toplevel"])
        assert top_proc.returncode == 0
        assert Path(top_proc.stdout.strip()).resolve() == rules_repo.path.resolve()

        nested_nongit = rules_repo.path / "nested-nongit"
        nested_nongit.mkdir()
        nested_proc = run_git(nested_nongit, ["rev-parse", "--show-toplevel"])
        assert nested_proc.returncode != 0

    def test_fetch_propagates_insecure_store_dir_and_writes_stamp_atomically(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rules_repo: RulesRepo
    ) -> None:
        store_dir = tmp_path / "store"
        store_dir.mkdir(mode=0o755)
        os.chmod(store_dir, 0o755)
        monkeypatch.setenv("REVIEW_RULES_DB", str(store_dir / "review.db"))

        with pytest.raises(StoreInsecure, match="Insecure permissions"):
            load_rules(rules_repo.path, fetch=True)

        os.chmod(store_dir, 0o700)
        res = load_rules(rules_repo.path, fetch=True)
        assert res.fetched is True
        stamps = list(store_dir.glob("fetch-stamp-*"))
        assert len(stamps) == 1
        assert stat.S_IMODE(os.lstat(stamps[0]).st_mode) == 0o600
        assert (store_dir / "rules-repo.lock").exists()
        assert stat.S_IMODE(os.lstat(store_dir / "rules-repo.lock").st_mode) == 0o600

    def test_load_rules_batches_blobs_via_single_cat_file_call(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rules_repo: RulesRepo
    ) -> None:
        monkeypatch.setenv("REVIEW_RULES_DB", str(tmp_path / "store" / "review.db"))
        for idx in range(5):
            rules_repo.commit_file(
                f"rules/r-batch-{idx}.yaml",
                _dump_rule_yaml(_make_rule_dict(f"r-batch-{idx}", status="active")),
                push=(idx == 4),
            )

        orig_run = subprocess.run
        git_cmds: list[list[str]] = []

        def _spy_run(cmd: list[str], *args: Any, **kwargs: Any) -> Any:
            if isinstance(cmd, list) and cmd and cmd[0] == "git":
                git_cmds.append(cmd)
            return orig_run(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", _spy_run)
        res = load_rules(rules_repo.path, fetch=False)
        assert len(res.active) == 5
        cat_file_calls = [c for c in git_cmds if "cat-file" in c and "--batch" in c]
        show_calls = [c for c in git_cmds if "show" in c]
        assert len(cat_file_calls) == 1
        # Only schema/rule.schema.json uses git show; rules use cat-file --batch.
        assert len(show_calls) == 1


class TestParsingAndSchema:
    def test_parse_rejects_all_seven_task2_invalid_cases(self) -> None:
        # 1. extra property
        d1 = _make_rule_dict("r-extra")
        d1["unexpected"] = True
        assert validate_rule(d1, "r-extra")

        # 2. statement > 200 chars
        d2 = _make_rule_dict("r-long", statement="Flag " + ("x" * 200))
        assert validate_rule(d2, "r-long")

        # 3. active without approved
        d3 = _make_rule_dict("r-no-app", status="active")
        d3.pop("approved", None)
        assert validate_rule(d3, "r-no-app")

        # 4. integer created_at
        d4 = _make_rule_dict("r-int-date")
        d4["provenance"]["created_at"] = 20261120
        assert validate_rule(d4, "r-int-date")

        # 5. empty evidence
        d5 = _make_rule_dict("r-empty-ev")
        d5["evidence"] = []
        assert validate_rule(d5, "r-empty-ev")

        # 6. bad id pattern
        d6 = _make_rule_dict("bad_id_format")
        assert validate_rule(d6, "bad_id_format")

        # 7. non-hex event_id
        d7 = _make_rule_dict("r-non-hex")
        d7["evidence"][0]["event_id"] = "ev-zzzzzzzzzzzzzzzz"
        assert validate_rule(d7, "r-non-hex")

    def test_parse_defensive_yaml_rejections_and_safe_loader_date_isolation(
        self, tmp_path: Path
    ) -> None:
        valid_yaml = _dump_rule_yaml(_make_rule_dict("r-valid", status="active"))

        cases: dict[str, str] = {
            "r-alias.yaml": "a: &bomb [x, x]\nb: [*bomb, *bomb]\n",
            "r-merge.yaml": "base: &b\n  id: r-merge\n<<: *b\n",
            "r-dupkey.yaml": valid_yaml + "\nid: r-dupkey\n",
            "r-fullwidth-key.yaml": valid_yaml.replace("id:", "ｉｄ:", 1),
            "r-spacekey.yaml": valid_yaml.replace("statement:", '"statement ":', 1),
            "r-shortstmt.yaml": valid_yaml.replace(
                "Never ignore errors from subprocess calls",
                "Flag   x   ",
            ),
            "r-nonmap.yaml": "- just a list\n",
            "r-multidoc.yaml": "---\n" + valid_yaml + "---\n" + valid_yaml,
            "r-deep.yaml": ("[" * 2500) + ("]" * 2500) + "\n",
        }

        lint_dir = tmp_path / "parse-cases"
        lint_dir.mkdir()
        for name, content in cases.items():
            (lint_dir / name).write_text(content, encoding="utf-8")
        (lint_dir / "r-valid.yaml").write_text(valid_yaml, encoding="utf-8")

        res = lint_directory(lint_dir)
        assert [r.id for r in res.active] == ["r-valid"]
        loaded_rule = res.active[0]
        assert isinstance(loaded_rule.provenance.created_at, str)
        assert isinstance(loaded_rule.review_by, str)
        assert loaded_rule.approved is not None
        assert isinstance(loaded_rule.approved.at, str)

        rejected_names = {rej.path for rej in res.rejected}
        assert rejected_names == set(cases.keys())

        # Global yaml.SafeLoader must remain untouched so standard YAML dates stay date objects.
        parsed_global = yaml.safe_load("d: 2026-01-01")
        assert isinstance(parsed_global["d"], date)


class TestClosedForm:
    def test_closed_form_statements_corpus(self) -> None:
        corpus = yaml.safe_load(_STATEMENTS_FIXTURE.read_text(encoding="utf-8"))
        entries = corpus["statements"]
        assert len(entries) >= 35

        for entry in entries:
            stmt = entry["statement"]
            expected = entry["expected"]
            rule = _make_rule_dict("r-stmt", statement=stmt)
            violations = closed_form_violations(rule)
            if expected == "pass":
                assert violations == [], f"Expected pass for {stmt!r}, got {violations}"
                assert validate_rule(rule, "r-stmt") == []
            else:
                assert violations, f"Expected failure for {stmt!r}, got none"
                expected_clause = entry["expected_clause"]
                assert any(expected_clause in v for v in violations), (
                    f"For {stmt!r}, expected clause {expected_clause!r} in {violations}"
                )

    def test_closed_form_rationale_suppression_skipped_only_when_retired(self) -> None:
        suppression_rationale = "Skip findings in generated files to keep noise down."
        active_rule = _make_rule_dict(
            "r-rat-active",
            status="active",
            rationale=suppression_rationale,
        )
        violations_active = closed_form_violations(active_rule)
        assert any("suppression_clause_1 (rationale)" in v for v in violations_active)

        cand_rule = _make_rule_dict(
            "r-rat-cand",
            status="candidate",
            rationale=suppression_rationale,
        )
        assert any(
            "suppression_clause_1 (rationale)" in v for v in closed_form_violations(cand_rule)
        )

        retired_rule = _make_rule_dict(
            "r-rat-retired",
            status="retired",
            rationale="Retired: false-positive rate 60%, dismissed 3 times.",
        )
        assert closed_form_violations(retired_rule) == []

    def test_closed_form_do_not_apply_when_limit_and_suppression(self) -> None:
        ok_rule = _make_rule_dict(
            "r-dnaw-ok",
            do_not_apply_when=[f"file is under test_dir_{i}/" for i in range(5)],
        )
        assert closed_form_violations(ok_rule) == []

        too_many = _make_rule_dict(
            "r-dnaw-6",
            do_not_apply_when=[f"file is under test_dir_{i}/" for i in range(6)],
        )
        assert any("do_not_apply_when_limit" in v for v in closed_form_violations(too_many))

        smuggled = _make_rule_dict(
            "r-dnaw-smuggle",
            do_not_apply_when=["verdict is false_positive in vendor/"],
        )
        assert any("suppression_clause_6" in v for v in closed_form_violations(smuggled))

    def test_closed_form_default_rule_metadata(self) -> None:
        for cat, expected_reviewers in CATEGORY_DEFAULT_REVIEWERS.items():
            meta = default_rule_metadata(cat, "2026-11-20")
            assert meta["trial"] is True
            assert meta["scope"] == {"skills": ["*"], "reviewers": list(expected_reviewers)}
            assert meta["review_by"] == "2027-02-18"

        with pytest.raises(ValueError, match="Unknown rule category"):
            default_rule_metadata("nonexistent", "2026-11-20")

    @pytest.mark.timing
    @pytest.mark.parametrize(
        "adversarial_text",
        [
            "Flag " + ("ignore " + "x" * 75 + " ") * 240,
            "Never " + ("do not " + "a" * 18 + " ") * 750,
            "Require " + ("lower " + "y" * 38 + " ") * 440,
            "Keep " + ("treat " + "z" * 18 + " as " + "w" * 9 + " ") * 550,
            "Ensure " + ("approve " + "q" * 38 + " ") * 420,
            "Check " + ("no " + "a" * 24 + " ") * 700,
        ],
    )
    def test_closed_form_adversarial_20k_under_500ms(self, adversarial_text: str) -> None:
        rule = _make_rule_dict(
            "r-timing",
            statement=adversarial_text[:200],
            rationale=adversarial_text[:600],
            do_not_apply_when=[adversarial_text[:20000]],
        )
        start = time.perf_counter()
        _ = closed_form_violations(rule)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        assert elapsed_ms < 500.0, f"closed_form_violations took {elapsed_ms:.1f} ms (>= 500 ms)"

    def test_closed_form_zero_invisible_unicode_on_disk_in_src_and_tests(self) -> None:
        forbidden_cats = {"Cf", "Co", "Cn", "Zl", "Zp"}
        scanned = 0
        for sub in ("src", "tests"):
            base = _REVIEW_RULES_ROOT / sub
            for path in base.rglob("*"):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                if path.suffix not in {".py", ".txt", ".yaml", ".yml", ".json"}:
                    continue
                scanned += 1
                content = path.read_text(encoding="utf-8")
                for idx, ch in enumerate(content):
                    cat = unicodedata.category(ch)
                    assert cat not in forbidden_cats, (
                        f"Forbidden Unicode {cat} U+{ord(ch):04X} in {path} at offset {idx}"
                    )
        assert scanned > 0


class TestStatusPartition:
    def test_partition_separates_active_candidate_and_retired(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rules_repo: RulesRepo
    ) -> None:
        monkeypatch.setenv("REVIEW_RULES_DB", str(tmp_path / "store" / "review.db"))
        rules_repo.commit_file(
            "rules/r-act.yaml",
            _dump_rule_yaml(_make_rule_dict("r-act", status="active")),
            push=True,
        )
        rules_repo.commit_file(
            "rules/r-cand.yaml",
            _dump_rule_yaml(_make_rule_dict("r-cand", status="candidate")),
            push=True,
        )
        rules_repo.commit_file(
            "rules/r-ret.yaml",
            _dump_rule_yaml(
                _make_rule_dict(
                    "r-ret",
                    status="retired",
                    rationale="Retired: false-positive rate 60%",
                )
            ),
            push=True,
        )

        res = load_rules(rules_repo.path, fetch=False)
        assert res.rejected == ()
        assert [r.id for r in res.active] == ["r-act"]
        assert [r.id for r in res.candidates] == ["r-cand"]
        assert [r.id for r in res.retired] == ["r-ret"]


class TestCliLint:
    def test_cli_lint_pinned_ahead_warning_and_rejection(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        rules_repo: RulesRepo,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("REVIEW_RULES_DB", str(tmp_path / "store" / "review.db"))
        monkeypatch.setenv("REVIEW_RULES_REPO", str(rules_repo.path))

        rules_repo.commit_file(
            "rules/r-good.yaml",
            _dump_rule_yaml(_make_rule_dict("r-good", status="active")),
            push=True,
        )
        assert main(["lint", "--no-fetch"]) == 0
        out_ok = capsys.readouterr().out
        assert f"sha:       {rules_repo.origin_sha()}" in out_ok
        assert "active=1" in out_ok
        assert "rejected=0" in out_ok

        # Unpushed local main commit triggers ahead-of-remote warning while still exiting 0.
        rules_repo.commit_file(
            "rules/r-local-ahead.yaml",
            _dump_rule_yaml(_make_rule_dict("r-local-ahead")),
            push=False,
        )
        assert main(["lint", "--no-fetch"]) == 0
        out_ahead = capsys.readouterr().out
        assert "ahead of origin/main" in out_ahead

        # Pushed suppression rule causes lint to fail with exit 1.
        rules_repo.commit_file(
            "rules/r-bad.yaml",
            _dump_rule_yaml(_make_rule_dict("r-bad", statement="Skip findings in vendor code")),
            push=True,
        )
        assert main(["lint", "--no-fetch"]) == 1
        out_bad = capsys.readouterr().out
        assert "REJECTED rules/r-bad.yaml:" in out_bad
        assert "suppression_clause_1" in out_bad

    def test_cli_lint_worktree_and_dir_refuse_symlink_and_oversize(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        rules_repo: RulesRepo,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("REVIEW_RULES_DB", str(tmp_path / "store" / "review.db"))
        monkeypatch.setenv("REVIEW_RULES_REPO", str(rules_repo.path))

        # Uncommitted valid draft passes --worktree.
        (rules_repo.path / "rules" / "r-draft.yaml").write_text(
            _dump_rule_yaml(_make_rule_dict("r-draft")),
            encoding="utf-8",
        )
        assert main(["lint", "--worktree"]) == 0
        capsys.readouterr()

        # --dir checks local directory, rejecting symlinks and >8 KB files.
        drafts = tmp_path / "drafts"
        drafts.mkdir()
        (drafts / "r-ok.yaml").write_text(
            _dump_rule_yaml(_make_rule_dict("r-ok")),
            encoding="utf-8",
        )
        assert main(["lint", "--dir", str(drafts)]) == 0
        capsys.readouterr()

        target = tmp_path / "outside.yaml"
        target.write_text(_dump_rule_yaml(_make_rule_dict("r-link")), encoding="utf-8")
        os.symlink(target, drafts / "r-link.yaml")

        big_text = _dump_rule_yaml(_make_rule_dict("r-big")) + ("\n# " + "y" * 8200 + "\n")
        (drafts / "r-big.yaml").write_text(big_text, encoding="utf-8")

        assert main(["lint", "--dir", str(drafts)]) == 1
        out_dir = capsys.readouterr().out
        assert "REJECTED r-link.yaml:" in out_dir
        assert "REJECTED r-big.yaml:" in out_dir

        # Missing --dir returns exit 1 with stderr error.
        assert main(["lint", "--dir", str(tmp_path / "missing-dir")]) == 1
        assert "error:" in capsys.readouterr().err

    def test_cli_lint_worktree_rejects_schema_drift_and_ignores_ds_store(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        rules_repo: RulesRepo,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("REVIEW_RULES_DB", str(tmp_path / "store" / "review.db"))
        monkeypatch.setenv("REVIEW_RULES_REPO", str(rules_repo.path))

        # Regular .DS_Store in rules/ is ignored cleanly.
        (rules_repo.path / "rules" / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
        (rules_repo.path / "rules" / "r-draft.yaml").write_text(
            _dump_rule_yaml(_make_rule_dict("r-draft")),
            encoding="utf-8",
        )
        assert main(["lint", "--worktree"]) == 0
        capsys.readouterr()

        # Working-tree schema drift causes lint --worktree to exit 1.
        schema_path = rules_repo.path / "schema" / "rule.schema.json"
        schema_data = json.loads(schema_path.read_text(encoding="utf-8"))
        schema_data["title"] = "Local Unpushed Drift"
        schema_path.write_text(json.dumps(schema_data, indent=2) + "\n", encoding="utf-8")
        assert main(["lint", "--worktree"]) == 1
        assert "schema drift" in capsys.readouterr().err
