"""Keychain credential access and setup guide for keyed sources (Spec 06 T7, Spec 11 §4)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import procs
from .errors import RadarError

if TYPE_CHECKING:
    from .context import Ctx, Procs

KEYCHAIN_SERVICE = "tech-radar"
KEYED_ACCOUNTS: tuple[str, ...] = ("producthunt",)


class Credentials:
    """Read-only macOS Keychain wrapper scoped to service 'tech-radar'."""

    def __init__(self, procs_runner: Procs | None = None) -> None:
        self._procs = procs_runner

    def _run_security(self, account: str) -> procs.ProcResult | None:
        if not account or not procs.is_safe_arg(account):
            return None
        cmd = [
            "security",
            "find-generic-password",
            "-s",
            KEYCHAIN_SERVICE,
            "-a",
            account,
            "-w",
        ]
        try:
            if self._procs is not None:
                return self._procs.run(cmd)
            return procs.run(cmd)
        except RadarError:
            return None

    def get(self, account: str) -> str | None:
        """Retrieve a Keychain secret into memory only; never log or persist the value."""
        res = self._run_security(account)
        if res is None or res.returncode != 0:
            return None
        secret = res.stdout.strip()
        return secret if secret else None

    def has(self, account: str | None) -> bool:
        """Return True if account is None/empty or present in the Keychain."""
        if not account:
            return True
        return self.get(account) is not None

    def status(self, accounts: tuple[str, ...] = KEYED_ACCOUNTS) -> dict[str, str]:
        """Return availability map ('present' | 'missing') without exposing any secret values."""
        out: dict[str, str] = {}
        for acct in accounts:
            out[acct] = "present" if self.get(acct) is not None else "missing"
        return out


def get(ctx: Ctx, account: str) -> str | None:
    """Module-level helper to fetch a credential using ctx.credentials."""
    if ctx.credentials is not None:
        val = ctx.credentials.get(account)
        return str(val) if val is not None else None
    return Credentials(ctx.procs).get(account)


def status(ctx: Ctx) -> dict[str, Any]:
    """Return status dict for all keyed accounts."""
    creds = ctx.credentials if isinstance(ctx.credentials, Credentials) else Credentials(ctx.procs)
    accounts_map = creds.status(KEYED_ACCOUNTS)
    present = [acct for acct, st in accounts_map.items() if st == "present"]
    missing = [acct for acct, st in accounts_map.items() if st != "present"]
    return {
        "service": KEYCHAIN_SERVICE,
        "accounts": accounts_map,
        "present": present,
        "missing": missing,
    }


def guide(account: str) -> dict[str, Any]:
    """Return numbered terminal setup instructions for a keyed source (never includes values)."""
    clean_acct = account.strip().lower()
    add_cmd = f"security add-generic-password -U -s {KEYCHAIN_SERVICE} -a {clean_acct} -w"
    if clean_acct == "producthunt":
        steps = [
            "1. Create a Product Hunt developer token in the Product Hunt API dashboard.",
            (
                f"2. Run `{add_cmd}` in your own terminal "
                "(it prompts for the secret; do not paste tokens into chat)."
            ),
            "3. Run `radar.py credentials status` to confirm the credential is present.",
        ]
    else:
        steps = [
            f"1. Obtain an API token for `{clean_acct}`.",
            (
                f"2. Run `{add_cmd}` in your own terminal "
                "(it prompts for the secret; do not paste tokens into chat)."
            ),
            "3. Run `radar.py credentials status` to confirm the credential is present.",
        ]
    return {
        "account": clean_acct,
        "service": KEYCHAIN_SERVICE,
        "command": add_cmd,
        "steps": steps,
    }
