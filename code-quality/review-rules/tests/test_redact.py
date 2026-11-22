"""Unit, corpus, adversarial timing, and hygiene tests for review_rules.redact."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
import time
import unicodedata
from pathlib import Path

import pytest

from review_rules.redact import (
    MAX_OUTPUT_CHARS,
    PATTERNS,
    PRE_SLICE_LIMIT,
    SETTINGS_MAX_BYTES,
    Redacted,
    hash_form,
    load_known_secrets,
    redact_event,
    reset_known_secrets_cache,
)
from tests.fixtures.secrets_corpus import (
    build_extra_corpus,
    build_known_secret,
    build_pattern_corpus,
    build_pem_key,
)

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
_REVIEW_RULES_ROOT = Path(__file__).resolve().parents[1]
_REDACT_PATH = _REVIEW_RULES_ROOT / "src" / "review_rules" / "redact.py"


@pytest.fixture(autouse=True)
def _reset_secrets_cache() -> object:
    reset_known_secrets_cache()
    yield
    reset_known_secrets_cache()


class TestPatterns:
    def test_patterns_corpus_one_to_one_coverage(self) -> None:
        corpus = build_pattern_corpus()
        pattern_kinds = [kind for kind, _pat, _val in PATTERNS]
        assert list(corpus.keys()) == pattern_kinds

        for kind, _pat, _val in PATTERNS:
            secret = corpus[kind]
            payload = f"before {secret} after"
            if kind == "kubeconfig":
                payload = f"{secret}\nafter"
            res = redact_event(payload)
            assert res.action == "keep", f"{kind} unexpectedly had action={res.action}"
            assert f"[REDACTED:{kind}]" in res.text, f"{kind} missing marker in {res.text!r}"
            assert secret not in res.text, f"{kind} leaked raw secret in {res.text!r}"

    def test_patterns_extra_corpus(self) -> None:
        for kind, sample in build_extra_corpus():
            res = redact_event(f"context {sample} end")
            assert res.action == "keep", f"{kind} had action={res.action}"
            assert f"[REDACTED:{kind}]" in res.text, f"{kind} missing marker in {res.text!r}"

    def test_patterns_pem_terminated_and_unterminated(self) -> None:
        pem_ok = build_pem_key(terminated=True)
        res_ok = redact_event(f"prefix\n{pem_ok}\ntrailing prose")
        assert res_ok.action == "keep"
        assert "[REDACTED:private_key]" in res_ok.text
        assert "MIIEowIB" not in res_ok.text
        assert "trailing prose" in res_ok.text

        header = "-----BEG" + "IN RSA PRI" + "VATE KEY-----"
        body_20kb = ("MIIEowIBAAKCAQEA12345678\n") * 850
        unterminated = f"prefix\n{header}\n{body_20kb}\ntrailing prose"
        res_unterm = redact_event(unterminated)
        assert res_unterm.action == "keep"
        assert res_unterm.text == "prefix\n[REDACTED:private_key]"

    def test_patterns_64kb_boundary_straddling_secret(self) -> None:
        secret = build_pattern_corpus()["github"]
        prefix = "a" * (MAX_OUTPUT_CHARS - 6)
        payload = f"{prefix}{secret} trailing"
        assert len(payload) < PRE_SLICE_LIMIT

        res = redact_event(payload)
        assert res.action == "truncate"
        assert len(res.text) <= MAX_OUTPUT_CHARS
        assert secret not in res.text
        # Secret started before 65,536 so its unredacted prefix must not survive,
        # and any [REDACTED:github] marker must not be sliced in half at 65,536.
        assert secret[:8] not in res.text
        assert "[TRUNCATED:" in res.text
        if "[REDACTED:" in res.text:
            assert "[REDACTED:github]" in res.text

    def test_patterns_4kb_cookie_redacted(self) -> None:
        pairs = [f"c{i}=Val{i}AbCd1234Ef" for i in range(220)]
        cookie_hdr = "Cookie: " + "; ".join(pairs)
        assert len(cookie_hdr) > 4000
        res = redact_event(cookie_hdr)
        assert res.action in {"keep", "truncate"}
        assert "Cookie: [REDACTED:auth_header]" in res.text
        assert "Val100AbCd1234Ef" not in res.text

    @pytest.mark.parametrize(
        "benign_text",
        [
            "Authorization: required on all endpoints",
            "Authorization: returns 401 when missing",
            "basic error-handling-wrapper",
            "basic error-v2-handling-wrapper",
            "secretName: my-registry-credentials",
            "secretName: my-oauth2-credentials",
            "token = get_token(request)",
            "Authorization: Bearer [REDACTED:jwt]",
            "token: [REDACTED:jwt]",
            "Set-Cookie: [REDACTED:jwt]",
        ],
    )
    def test_patterns_benign_prose_and_prior_markers_preserved(self, benign_text: str) -> None:
        res = redact_event(benign_text)
        assert res == Redacted(text=benign_text, action="keep", dropped_reason=None)

    def test_patterns_cookie_with_prior_marker_and_live_sid(self) -> None:
        line = "Cookie: jwt=[REDACTED:jwt]; sid=9f8e7d6c5b4a3210"
        res = redact_event(line)
        assert res.action == "keep"
        assert res.text == "Cookie: [REDACTED:auth_header]"
        assert "9f8e7d6c5b4a3210" not in res.text

    def test_patterns_bracketed_secrets_and_compound_known_redactions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pw_bracket = "Tr0ub4dor" + "&[3]Horse"
        res_pw = redact_event(f'password = "{pw_bracket}"')
        assert res_pw.action == "keep"
        assert pw_bracket not in res_pw.text
        assert "[REDACTED:kv_secret]" in res_pw.text

        kv_bracket = "Ab12[Cd34]" + "Ef56Gh78"
        res_kv = redact_event(f"api_key={kv_bracket}")
        assert res_kv.action == "keep"
        assert kv_bracket not in res_kv.text
        assert "[REDACTED:kv_secret]" in res_kv.text

        known_user = "KnownUserToken" + "9999"
        monkeypatch.setenv("CUSTOM_TEST_TOKEN", known_user)
        reset_known_secrets_cache()

        url_pw = "Sup3rS3cretPw!"
        res_url = redact_event(f"https://{known_user}:{url_pw}@db.io/v1")
        assert res_url.action == "keep"
        assert url_pw not in res_url.text
        assert res_url.text == "https://[REDACTED:url_userinfo]@db.io/v1"

        q_tail = "LiveSecret" + "99"
        res_q = redact_event(f"https://example.com/?access_token=tok_{known_user}_{q_tail}")
        assert res_q.action == "keep"
        assert q_tail not in res_q.text
        assert "[REDACTED:query_secret]" in res_q.text

        res_kc = redact_event(f"  token: tok_{known_user}_{q_tail}\n")
        assert res_kc.action == "keep"
        assert q_tail not in res_kc.text
        assert "[REDACTED:kubeconfig]" in res_kc.text

        res_kv_comp = redact_event(f"api_key={known_user}ExtraSecret99")
        assert res_kv_comp.action == "keep"
        assert "ExtraSecret99" not in res_kv_comp.text
        assert "[REDACTED:kv_secret]" in res_kv_comp.text

        # Pure [REDACTED:<kind>] labels and unquoted subscript identifiers remain intact.
        for benign in (
            "https://example.com/?token=[REDACTED:known]",
            "  token: [REDACTED:private_key]\n",
            "api_key=config_map[prod_key_123]",
        ):
            res_b = redact_event(benign)
            assert res_b == Redacted(text=benign, action="keep", dropped_reason=None)

    def test_patterns_pem_shrink_does_not_expose_pre_slice_boundary_secret(self) -> None:
        pem = build_pem_key(terminated=True)
        # Pad so a ghp_ token starts 20 chars before PRE_SLICE_LIMIT (straddling 69,632)
        pat = build_pattern_corpus()["github"]
        pad_len = PRE_SLICE_LIMIT - len(pem) - 20
        payload = pem + ("x" * pad_len) + pat
        assert len(payload) > PRE_SLICE_LIMIT

        res = redact_event(payload)
        assert res.action == "truncate"
        assert len(res.text) <= MAX_OUTPUT_CHARS
        assert pat[:8] not in res.text
        assert "[REDACTED:private_key]" in res.text
        assert "[TRUNCATED:" in res.text

    @pytest.mark.timing
    @pytest.mark.parametrize(
        "adversarial_input",
        [
            "Authorization: " + (" " * 65_000),
            "api_key=" + (" " * 65_000),
            "a:" * 32_768,
            "-----BEG" + "IN RSA PRI" + "VATE KEY-----\n" + ("A" * 60_000),
            "Bearer " + ("!" * 65_000),
            "https://user:" + ("a" * 65_000),
            "https://example.com/?token=" + ("&" * 65_000),
            '"auth": "' + ("A" * 65_000),
            "  token: " + ("A" * 65_000),
        ],
    )
    def test_patterns_adversarial_redos_under_500ms(self, adversarial_input: str) -> None:
        start = time.perf_counter()
        _ = redact_event(adversarial_input)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        assert elapsed_ms < 500.0, f"redact_event took {elapsed_ms:.1f} ms (>= 500 ms)"


class TestGates:
    def test_gates_benign_comments_fixture_unchanged(self) -> None:
        lines = [
            ln.rstrip("\n")
            for ln in (_FIXTURES_DIR / "benign_comments.txt")
            .read_text(encoding="utf-8")
            .splitlines()
            if ln.strip()
        ]
        assert len(lines) >= 25
        for comment in lines:
            res = redact_event(comment)
            assert res == Redacted(text=comment, action="keep", dropped_reason=None), (
                f"Benign comment altered/dropped: {comment!r} -> {res!r}"
            )

    def test_gates_empty_and_whitespace(self) -> None:
        assert redact_event("") == Redacted(text="", action="keep", dropped_reason=None)
        assert redact_event("   \n\t  ") == Redacted(text="", action="keep", dropped_reason=None)

    def test_gates_entropy_drops_40_char_mixed_base64_and_data_uri(self) -> None:
        b64_40 = (
            "aB3d" + "E5fG" + "7hJ9" + "kL1m" + "N3pQ" + "5rS7" + "tU9v" + "W1xY" + "3zA5" + "bC7d"
        )
        assert len(b64_40) == 40
        res1 = redact_event(f"token candidate: {b64_40}")
        assert res1 == Redacted(text="", action="drop", dropped_reason="entropy")

        b64_slashes = "aB3dE5fG" + "/" + "hJ9kL1mN3pQ" + "/" + "rS7tU9vW1xY3zA5bC7d"
        assert len(b64_slashes) == 40
        res2 = redact_event(f"value={b64_slashes}")
        assert res2 == Redacted(text="", action="drop", dropped_reason="entropy")

        data_uri = "inline: data:image/jpeg;base64,/9j/" + ("Ab3Cd4Ef5Gh6Ij7Kl8Mn9Op0Qr1St2Uv" * 2)
        res3 = redact_event(data_uri)
        assert res3 == Redacted(text="", action="drop", dropped_reason="entropy")

    def test_gates_presigned_url_redacted(self) -> None:
        url = (
            "https://bucket.s3.amazonaws.com/key?"
            + "X-Amz-Algorithm=AWS4-HMAC-SHA256&"
            + "X-Amz-Signature="
            + "a1b2c3d4e5f6" * 3
        )
        res = redact_event(url)
        assert res.action == "keep"
        assert "X-Amz-Signature=[REDACTED:query_secret]" in res.text
        assert "a1b2c3d4e5f6" not in res.text

    def test_gates_paste_300_line_ci_log_and_traceback_and_fence(self) -> None:
        log_lines = [
            f"2026-11-22T10:00:{i % 60:02d} INFO step {i} completed ok" for i in range(300)
        ]
        payload = "See the failure in CI:\n" + "\n".join(log_lines)
        assert len(payload) > 2048

        res = redact_event(payload)
        assert res.action == "truncate"
        assert res.text.startswith("See the failure in CI:\n")
        assert "[PASTE: 280 more lines truncated]" in res.text
        assert "INFO step 19 completed ok" in res.text
        assert "INFO step 20 completed ok" not in res.text

        # Traceback detected as one block.
        tb_lines = [
            "Here is the error context (" + ("x" * 1900) + "):",
            "Traceback (most recent call last):",
            *[f'  File "app/mod_{i}.py", line {i + 1}, in fn_{i}' for i in range(25)],
            "ValueError: invalid state",
        ]
        res_tb = redact_event("\n".join(tb_lines))
        assert res_tb.action == "truncate"
        assert "[PASTE: 7 more lines truncated]" in res_tb.text
        assert "ValueError: invalid state" not in res_tb.text

        # Truncated fenced block keeps closing fence and trailing prose.
        fenced = (
            "```python\n"
            + "\n".join(f"x_{i} = {i}" for i in range(250))
            + "\n```\nTrailing explanation stays intact."
        )
        res_fenced = redact_event(fenced)
        assert res_fenced.action == "truncate"
        assert "[PASTE: 230 more lines truncated]\n```" in res_fenced.text
        assert res_fenced.text.endswith("Trailing explanation stays intact.")

        # CRLF fenced block >20 lines preserves closing fence and trailing prose, idempotently.
        crlf_fenced = (
            "```python\r\n"
            + "\r\n".join(f"x_{i} = {i}" for i in range(60))
            + "\r\n```\r\n"
            + ("Trailing CRLF prose stays intact. " * 70)
        )
        assert len(crlf_fenced) > 2048
        res_crlf = redact_event(crlf_fenced)
        assert res_crlf.action == "truncate"
        assert "[PASTE: 40 more lines truncated]" in res_crlf.text
        assert "Trailing CRLF prose stays intact." in res_crlf.text
        assert redact_event(res_crlf.text).text == res_crlf.text

    def test_gates_paste_idempotence(self) -> None:
        fenced_300 = "```log\n" + "\n".join(f"line {i} payload value" for i in range(300)) + "\n```"
        first_fenced = redact_event(fenced_300)
        assert first_fenced.action == "truncate"
        second_fenced = redact_event(first_fenced.text)
        assert second_fenced.text == first_fenced.text

        # Unfenced 300-line log whose 20 kept lines remain > 2,048 chars.
        long_log = "\n".join(
            f"2026-11-22T10:00:00 INFO step={i:03d} " + ("detail " * 16) for i in range(300)
        )
        first_unfenced = redact_event(long_log)
        assert first_unfenced.action == "truncate"
        assert len(first_unfenced.text) > 2048
        second_unfenced = redact_event(first_unfenced.text)
        assert second_unfenced.text == first_unfenced.text

    def test_gates_pii_and_webhooks_and_userinfo(self) -> None:
        res_email = redact_event("contact user@example.com.")
        assert res_email == Redacted(text="contact [EMAIL].", action="keep", dropped_reason=None)

        res_git = redact_event("git@github.com:org/repo")
        assert res_git == Redacted(
            text="git@github.com:org/repo", action="keep", dropped_reason=None
        )

        res_internal = redact_event("https://db.prod.internal/api and 10.0.0.5")
        assert res_internal == Redacted(
            text="https://[HOST]/api and [HOST]", action="keep", dropped_reason=None
        )

        res_spared = redact_event("Edit values.local.yaml and pkg.internal.x")
        assert res_spared == Redacted(
            text="Edit values.local.yaml and pkg.internal.x",
            action="keep",
            dropped_reason=None,
        )

        slack_hook = "https://hooks." + "slack.com/services/" + "T0AAA/B0BBB/" + ("a1B2c3D4" * 3)
        res_slack = redact_event(slack_hook)
        assert res_slack == Redacted(text="[REDACTED:slack]", action="keep", dropped_reason=None)

        custom_hook = "https://hooks.custom.example/notify/" + ("aB3dE5fG7hJ9kL1mN3pQ5rS7")
        res_custom = redact_event(custom_hook)
        assert res_custom == Redacted(text="", action="drop", dropped_reason="entropy")

        res_uinfo = redact_event("https://" + "u:p" + "@host/path")
        assert res_uinfo == Redacted(
            text="https://[REDACTED:url_userinfo]@host/path",
            action="keep",
            dropped_reason=None,
        )

        # Userinfo-redacted URL with mixed-case path segment (<20 chars) must NOT drop.
        res_uinfo_gh = redact_event(
            "See https://" + "u:p" + "@github.com/osac-project/MyServiceV2Handler/pull/42"
        )
        assert res_uinfo_gh == Redacted(
            text="See https://[REDACTED:url_userinfo]@github.com/osac-project/MyServiceV2Handler/pull/42",
            action="keep",
            dropped_reason=None,
        )

        # Internal-host URL with >=20-char mixed-alnum secret segment must drop.
        internal_hook = "https://hooks.prod.internal/notify/" + "aB3dE5fG7hJ9kL1mN3pQ5rS7"
        assert redact_event(internal_hook) == Redacted(
            text="", action="drop", dropped_reason="entropy"
        )

        # /-prefixed high-entropy token must NOT bypass via filesystem-path allowlist.
        slash_b64 = "/9j/" + "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3zA5bC"
        assert redact_event(slash_b64) == Redacted(text="", action="drop", dropped_reason="entropy")


class TestKnownSecretsScrub:
    def test_known_secrets_from_settings_and_environ_with_exclusions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        cfg_dir = tmp_path / "claude-cfg"
        cfg_dir.mkdir()
        settings_secret = build_known_secret()
        env_secret = "mysql_" + "pwd_value_" + "9876543210"
        excluded_pwd = "/Users/example/projects/very_long_working_dir_path"
        excluded_url = "https://example.com/very/long/ignored/webhook/path"

        (cfg_dir / "settings.json").write_text(
            json.dumps(
                {
                    "env": {
                        "CUSTOM_API_TOKEN": settings_secret,
                        "TOO_SHORT_KEY": "short_1234",
                        "CUSTOM_DIR": "/some/long/directory/path/ignored",
                    }
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_dir))
        monkeypatch.setenv("MYSQL_PWD", env_secret)
        monkeypatch.setenv("PWD", excluded_pwd)
        monkeypatch.setenv("OLDPWD", excluded_pwd + "/old")
        monkeypatch.setenv("PATH", excluded_pwd + ":/usr/bin")
        monkeypatch.setenv("CUSTOM_PATH", excluded_pwd + "/custom")
        monkeypatch.setenv("APP_HOME", excluded_pwd + "/home")
        monkeypatch.setenv("CALLBACK_URL", excluded_url)
        monkeypatch.setenv("REVIEW_RULES_ANTHROPIC_KEY_FILE", excluded_pwd + "/key")
        monkeypatch.setenv("SSH_AUTH_SOCK", excluded_pwd + "/ssh.sock")
        reset_known_secrets_cache()

        loaded = load_known_secrets()
        assert settings_secret in loaded
        assert env_secret in loaded
        assert excluded_pwd not in loaded
        assert excluded_url not in loaded

        text = (
            f"direct={settings_secret} embedded=prefix_{env_secret}_suffix "
            f"cwd={excluded_pwd} url={excluded_url}"
        )
        res = redact_event(text)
        assert res.action == "keep"
        assert settings_secret not in res.text
        assert env_secret not in res.text
        assert "direct=[REDACTED:known]" in res.text
        assert "embedded=prefix_[REDACTED:known]_suffix" in res.text
        assert excluded_pwd in res.text
        assert excluded_url in res.text

    def test_oversized_and_non_regular_settings_json_ignored(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        cfg_dir = tmp_path / "claude-cfg"
        cfg_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_dir))

        # Directory at settings.json path is safely ignored.
        (cfg_dir / "settings.json").mkdir()
        reset_known_secrets_cache()
        assert isinstance(load_known_secrets(), tuple)

        # > 1 MiB settings.json is safely ignored.
        (cfg_dir / "settings.json").rmdir()
        oversized_val = "oversized_" + ("A" * (SETTINGS_MAX_BYTES + 100))
        (cfg_dir / "settings.json").write_text(
            json.dumps({"env": {"BIG_TOKEN": oversized_val}}),
            encoding="utf-8",
        )
        reset_known_secrets_cache()
        assert oversized_val not in load_known_secrets()


class TestHashForm:
    def test_hash_form_normalizes_system_reminders_whitespace_and_pasted_content(self) -> None:
        open_tag = "<" + "system-reminder>"
        close_tag = "</" + "system-reminder>"
        base = "Check error handling in subprocess calls."
        with_reminder = f"  {base}  \n{open_tag}\ninternal note\n{close_tag}\n\t "
        assert hash_form(base) == hash_form(with_reminder)

        wrapped = (
            'Review this:\n<pasted_content name="log.txt">\nline one\nline two\n</pasted_content>'
        )
        fenced = "Review this:\n```\nline one\nline two\n```"
        assert hash_form(wrapped) == hash_form(fenced)

        high_ent = "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3zA5bC7d"
        assert hash_form(high_ent) == ""

    @pytest.mark.timing
    @pytest.mark.parametrize(
        "unclosed_payload",
        [
            "<pasted_content>" * 4_000,
            "<pasted_content " * 4_000,
            ("<" + "system-reminder>") * 4_000,
        ],
    )
    def test_hash_form_unclosed_tags_linear_time_under_500ms(self, unclosed_payload: str) -> None:
        start = time.perf_counter()
        _ = hash_form(unclosed_payload)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        assert elapsed_ms < 500.0, f"hash_form took {elapsed_ms:.1f} ms (>= 500 ms)"


class TestStandaloneAndHygiene:
    def test_stdlib_only_and_spec_from_file_location(self) -> None:
        tree = ast.parse(_REDACT_PATH.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module.split(".")[0])

        assert "review_rules" not in imported
        assert imported <= sys.stdlib_module_names

        spec = importlib.util.spec_from_file_location("standalone_redact", _REDACT_PATH)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["standalone_redact"] = module
        try:
            spec.loader.exec_module(module)
            res = module.redact_event("contact user@example.com")
            assert res.text == "contact [EMAIL]"
            assert res.action == "keep"
        finally:
            sys.modules.pop("standalone_redact", None)

    def test_no_raw_secrets_invisible_unicode_or_system_reminder_tags_on_disk(self) -> None:
        raw_open = "<" + "system-reminder>"
        raw_close = "</" + "system-reminder>"
        assembled_secrets = list(build_pattern_corpus().values()) + [
            s for _k, s in build_extra_corpus()
        ]

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
                assert raw_open not in content, f"Raw system-reminder open tag in {path}"
                assert raw_close not in content, f"Raw system-reminder close tag in {path}"
                for idx, ch in enumerate(content):
                    cat = unicodedata.category(ch)
                    assert cat not in {"Cf", "Co", "Cn"}, (
                        f"Forbidden Unicode {cat} U+{ord(ch):04X} in {path} at offset {idx}"
                    )
                for secret in assembled_secrets:
                    assert secret not in content, (
                        f"Literal assembled secret found on disk in {path}"
                    )
        assert scanned > 0
        assert os.path.isfile(_REDACT_PATH)
