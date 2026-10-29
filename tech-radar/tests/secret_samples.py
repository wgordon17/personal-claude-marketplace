"""Runtime-assembled synthetic secret samples for redaction tests.

Every sample is built at runtime from split string fragments so no secret-shaped literal
ever appears in source files scanned by betterleaks pre-commit hooks.
"""

from __future__ import annotations


def build_samples_with_kinds() -> dict[str, tuple[str, str]]:
    """Return mapping of sample_name -> (secret_value, expected_redaction_kind)."""
    aws_val = "AK" + "IA" + "IOSFODNN" + "7EXAMPLE"
    ghp_val = "gh" + "p_" + ("A1b2C3d4E5f6" * 3)
    gh_pat_val = "github" + "_pat_" + ("11AbCdEfGhIj" * 3)
    jwt_val = (
        "ey"
        + "JhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        + "."
        + "ey"
        + "JzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ"
        + "."
        + "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    sk_ant_val = "sk" + "-ant-" + "api03-" + ("Zx9Yw8Vu7Ts6Rq5P" * 2)
    sk_proj_val = "sk" + "-proj-" + ("Mn3Bv4Cx5Zl6Kj7H" * 2)
    aiza_val = "AI" + "za" + "SyDa1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"
    bearer_val = "Bea" + "rer " + ("tok9876543210AbCdEfGh" * 2)
    userinfo_val = "https://devuser:" + "Sup3rS3cretP4ssw0rd" + "@github.com/example-org/repo.git"
    slack_val = "xo" + "xb-" + "123456789012-" + "987654321098-" + "AbCdEfGhIjKlMnOpQrStUvWx"
    priv_key_val = (
        "-----"
        + "BEGIN "
        + "OPENSSH "
        + "PRIVATE KEY"
        + "-----\n"
        + ("b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ==" * 2)
        + "\n-----"
        + "END "
        + "OPENSSH "
        + "PRIVATE KEY"
        + "-----"
    )
    high_entropy_val = "9zK8xJ7w" + "H6vG5uF4" + "tE3sD2rC" + "1qB0pAoN" + "mLkJiHgF"

    return {
        "aws_key": (aws_val, "aws_key"),
        "github_token": (ghp_val, "github_token"),
        "github_pat": (gh_pat_val, "github_token"),
        "jwt": (jwt_val, "jwt"),
        "anthropic_key": (sk_ant_val, "api_key"),
        "openai_key": (sk_proj_val, "api_key"),
        "google_key": (aiza_val, "api_key"),
        "bearer": (bearer_val, "bearer"),
        "url_userinfo": (userinfo_val, "url_userinfo"),
        "slack_token": (slack_val, "slack_token"),
        "private_key": (priv_key_val, "private_key"),
        "high_entropy": (high_entropy_val, "high_entropy"),
    }


def build_samples() -> dict[str, str]:
    """Return mapping of sample_name -> secret_value."""
    return {name: val for name, (val, _kind) in build_samples_with_kinds().items()}


def raw_canary_substrings() -> dict[str, str]:
    """Return the sensitive substring of each sample that must never survive scrubbing."""
    samples = build_samples()
    out: dict[str, str] = {}
    for name, val in samples.items():
        if name == "url_userinfo":
            out[name] = "devuser:" + "Sup3rS3cretP4ssw0rd"
        elif name == "bearer":
            out[name] = "tok9876543210AbCdEfGh" * 2
        elif name == "private_key":
            out[name] = "BEGIN " + "OPENSSH " + "PRIVATE KEY"
        else:
            out[name] = val
    return out
