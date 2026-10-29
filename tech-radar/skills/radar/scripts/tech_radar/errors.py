"""Error hierarchy for tech-radar."""

from __future__ import annotations

from typing import Any


class RadarError(Exception):
    """Base exception for all tech-radar errors."""

    exit_code: int = 1

    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        super().__init__(message)
        if exit_code is not None:
            self.exit_code = exit_code


class EgressDenied(RadarError):
    """Raised when an HTTP request violates scheme, host, method, redirect, or IP policy."""

    exit_code = 1


class FetchError(RadarError):
    """Raised when an HTTP fetch or XML parse fails or violates size/decompression caps."""

    exit_code = 1

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        exit_code: int | None = None,
    ) -> None:
        super().__init__(message, exit_code=exit_code)
        self.status = status


class UnsafeArg(RadarError):
    """Raised when a subprocess argument fails safety validation."""

    exit_code = 1


class CommandDenied(RadarError):
    """Raised when a subprocess binary, subcommand, or endpoint is not on the allowlist."""

    exit_code = 1


class WriteDenied(RadarError):
    """Raised when a filesystem write targets a symlink or resolves outside allowed roots."""

    exit_code = 1


class ValidationError(RadarError):
    """Raised when structured input fails schema or gate validation (exit 2)."""

    exit_code = 2

    def __init__(
        self,
        errors: list[dict[str, Any]],
        message: str = "validation failed",
    ) -> None:
        super().__init__(message, exit_code=2)
        self.errors = list(errors)


class Refused(RadarError):
    """Raised when an operation is refused by policy, lock, TTY gate, or confirmation check."""

    exit_code = 4
