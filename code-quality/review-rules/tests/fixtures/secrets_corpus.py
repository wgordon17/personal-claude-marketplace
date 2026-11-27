"""Runtime-assembled synthetic secrets corpus for redaction tests.

Every token is constructed from short string fragments at runtime so no
format-valid secret literal is stored in git history.
"""

from __future__ import annotations


def _j(*parts: str) -> str:
    return "".join(parts)


def build_pattern_corpus() -> dict[str, str]:
    """Return one synthetic secret sample for every entry in redact.PATTERNS."""
    return {
        "anthropic": _j("sk", "-ant-", "api03-", "AbCdEf12" * 4),
        "github": _j("gh", "p_", "A1b2C3" * 6),
        "gitlab": _j("gl", "pat-", "Ab12Cd34Ef56Gh78Ij90"),
        "slack": _j("xo", "xb-", "1234567890", "-", "AbCdEf123456"),
        "aws_key_id": _j("AK", "IA", "A1B2C3D4" * 2),
        "google_api": _j("AI", "za", "Sy", "A1b2C3d4E5f" * 3),
        "google_oauth": _j("ya", "29.", "a0AbCdEf1234567890" * 2),
        "atlassian": _j("AT", "ATT", "3xFfGF0", "AbCd1234" * 3),
        "stripe": _j("sk_", "live_", "Ab12Cd34Ef56Gh78Ij90"),
        "npm": _j("np", "m_", "Ab12Cd34" * 4, "Ef56"),
        "pypi": _j("py", "pi-", "AgEIcHlwaS5vcmc", "Ab12Cd34Ef" * 4),
        "huggingface": _j("hf", "_", "Ab12Cd34Ef" * 3, "Gh56"),
        "vault": _j("hv", "s.", "CAESIJ", "Ab12Cd34Ef" * 2),
        "age_secret": _j("AGE-", "SECRET-", "KEY-1", "A1B2C3D4E5" * 5, "F6G7H8I9"),
        "openshift": _j("sha", "256~", "Ab12Cd34Ef_" * 4),
        "generic_sk": _j("s", "k-", "proj-", "Ab12Cd34Ef" * 3),
        "jwt": _j(
            _j("ey", "J", "hbGciOiJIUzI1NiJ9"),
            ".",
            _j("ey", "J", "zdWIiOiIxMjM0NTY3ODkwIn0"),
            ".",
            _j("AbCd1234", "EfGh5678", "IjKl90Mn"),
        ),
        "auth_header": _j("Authoriz", "ation: X-Custom-Token ", "Ab12Cd34", "Ef56Gh78"),
        "bearer": _j("Bear", "er ", "Ab12Cd34", "Ef56Gh78", "Ij90"),
        "url_userinfo": _j("https://", "svc_user:", "Sup3rS3", "cretPw", "@api.example.com/v1"),
        "query_secret": _j(
            "https://s3.example.com/obj?", "X-Amz-Signature=", "a1b2c3d4", "e5f67890", "abcd"
        ),
        "docker_auth": _j('"au', 'th": "', "dXNlcjpw", "YXNzMTIz", "NDU2Nzg=", '"'),
        "kubeconfig": _j("  tok", "en: ", "kube_tok_", "Ab12Cd34", "Ef56Gh78"),
        "kv_secret": _j("api_", "key = ", "Ab12Cd34", "Ef56Gh78", "Ij90"),
    }


def build_extra_corpus() -> list[tuple[str, str]]:
    """Return additional pattern variant cases mapped to their expected redaction kind."""
    return [
        ("github", _j("github_", "pat_", "11ABCDEFG_", "a1B2c3D4" * 3)),
        ("slack", _j("xo", "xc-", "0123456789abcdef" * 3)),
        (
            "slack",
            _j(
                "https://hooks.",
                "slack.com/services/",
                "T0123ABCD/",
                "B0456EFGH/",
                "a1B2c3D4" * 3,
            ),
        ),
        ("slack", _j("xa", "pp-", "1-A0123456-", "9876543210-", "AbCdEf1234")),
        ("slack", _j("xo", "xe.xo", "xb-", "1-AbCdEf", "1234567890")),
        ("aws_key_id", _j("AS", "IA", "Z9Y8X7W6" * 2)),
        ("stripe", _j("rk_", "live_", "Ab12Cd34", "Ef56Gh78", "Ij90")),
        ("auth_header", _j("Set-", "Cookie: sid=", "9f8e7d6c", "5b4a3210", "; HttpOnly")),
        ("auth_header", _j("X-Api-", "Key: ", "Ab12Cd34", "Ef56Gh78")),
        ("bearer", _j("Bas", "ic ", "dXNlcjpw", "YXNzMTIz", "NDU2Nzg=")),
        (
            "query_secret",
            _j("https://app.example.com/cb?", "access_token=", "tok_Ab12", "Cd34Ef56"),
        ),
        ("kv_secret", _j("pass", 'word = "', "correct horse battery staple", '"')),
    ]


def build_pem_key(*, terminated: bool = True) -> str:
    """Return a synthetic PEM private key block assembled at runtime."""
    header = _j("-----BEG", "IN RSA PRI", "VATE KEY-----")
    body = "\n".join([_j("MIIEowIB", "AAKCAQEA", "AbCdEf12" * 4)] * 4)
    if not terminated:
        return f"{header}\n{body}"
    footer = _j("-----E", "ND RSA PRI", "VATE KEY-----")
    return f"{header}\n{body}\n{footer}"


def build_known_secret() -> str:
    """Return a synthetic >=16-char secret value for known-value scrub tests."""
    return _j("known_", "custom_", "secret_", "9876543210")
