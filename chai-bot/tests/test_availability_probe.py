"""Tests for check-availability.sh's reachability probe (Step 4) and the
overall exit-code contract (0/1/2).

A fake `git` shim controls whether the remote gate matches; a fake `curl`
shim (which also appends a call-count marker) simulates timeout/DNS-failure
vs. success without any real network I/O, and lets us assert "zero curl
invocations" for the non-matching-repo case.
"""

import http.server
import os
import shutil
import socket
import stat
import subprocess
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
SCRIPT = REPO_ROOT / "chai-bot" / "hooks" / "check-availability.sh"


def _make_git_shim(bin_dir: Path, *, origin: str | None) -> None:
    body = f'echo "{origin}"; exit 0' if origin else "exit 1"
    shim = f"""#!/usr/bin/env bash
if [[ "$1" == "rev-parse" ]]; then
    echo ".git"; exit 0
elif [[ "$1" == "remote" && "$2" == "get-url" && "$3" == "origin" ]]; then
    {body}
else
    exit 1
fi
"""
    git_path = bin_dir / "git"
    git_path.write_text(shim)
    git_path.chmod(git_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _make_curl_shim(bin_dir: Path, call_log: Path, *, exit_code: int) -> None:
    shim = f"""#!/usr/bin/env bash
echo "called" >> "{call_log}"
exit {exit_code}
"""
    curl_path = bin_dir / "curl"
    curl_path.write_text(shim)
    curl_path.chmod(curl_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _make_curl_argv_capturing_shim(bin_dir: Path, argv_log: Path, *, exit_code: int) -> None:
    """Like _make_curl_shim, but records curl's actual argv (one arg per line)
    instead of just a call marker, pinning that the script passes
    `-o /dev/null` (so it discards the response body/status and only
    inspects curl's own exit code)."""
    shim = f"""#!/usr/bin/env bash
printf '%s\\n' "$@" >> "{argv_log}"
exit {exit_code}
"""
    curl_path = bin_dir / "curl"
    curl_path.write_text(shim)
    curl_path.chmod(curl_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _free_tcp_port() -> int:
    """Bind to an OS-assigned free port on 127.0.0.1, then release it."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class _QuietNotFoundHandler(http.server.BaseHTTPRequestHandler):
    """Always answers 404 -- reachable, but not a "successful" HTTP response,
    to prove the script only cares about curl's connection-level exit code."""

    def do_GET(self):  # noqa: N802 -- BaseHTTPRequestHandler's naming convention
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):  # noqa: A002 -- silence request logging
        pass


def _bin_dir_with_real(tmp_path: Path, *tools: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for tool in tools:
        real = shutil.which(tool)
        if real:
            target = bin_dir / tool
            if not target.exists():
                target.symlink_to(real)
    return bin_dir


def _run_script(bin_dir: Path, *, base_url: str | None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PATH": str(bin_dir)}
    if base_url is None:
        env.pop("CHAI_BOT_BASE_URL", None)
    else:
        env["CHAI_BOT_BASE_URL"] = base_url
    return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)


class TestAvailabilityProbeExitCodes:
    def test_curl_timeout_yields_exit_2(self, tmp_path):
        bin_dir = _bin_dir_with_real(tmp_path, "bash")
        _make_git_shim(bin_dir, origin="git@github.com:osac-project/x.git")
        call_log = tmp_path / "curl-calls.log"
        _make_curl_shim(bin_dir, call_log, exit_code=28)  # curl: (28) timeout
        result = _run_script(bin_dir, base_url="https://example.invalid")
        assert result.returncode == 2, result.stderr
        assert call_log.read_text().count("called") == 1

    def test_curl_dns_failure_yields_exit_2(self, tmp_path):
        bin_dir = _bin_dir_with_real(tmp_path, "bash")
        _make_git_shim(bin_dir, origin="git@github.com:osac-project/x.git")
        call_log = tmp_path / "curl-calls.log"
        _make_curl_shim(bin_dir, call_log, exit_code=6)  # curl: (6) could not resolve host
        result = _run_script(bin_dir, base_url="https://example.invalid")
        assert result.returncode == 2, result.stderr

    def test_curl_success_yields_exit_0(self, tmp_path):
        bin_dir = _bin_dir_with_real(tmp_path, "bash")
        _make_git_shim(bin_dir, origin="git@github.com:osac-project/x.git")
        call_log = tmp_path / "curl-calls.log"
        _make_curl_shim(bin_dir, call_log, exit_code=0)
        result = _run_script(bin_dir, base_url="https://example.invalid")
        assert result.returncode == 0, result.stderr

    def test_curl_invoked_with_o_devnull_ignoring_http_status(self, tmp_path):
        """Distinct from test_curl_success_yields_exit_0 above -- that test
        only proves an exit-0 curl yields exit 0, but doesn't prove the script
        discards the response body/status rather than inspecting it. This
        pins that curl is actually invoked with `-o /dev/null` (so a 404, a
        500, or any other HTTP status can never surface -- only curl's own
        connection-level exit code matters). The real end-to-end 404 case
        (genuine HTTP server, real curl) is covered by
        TestRealHttpServerReachability.test_real_server_404_still_exits_0
        below."""
        bin_dir = _bin_dir_with_real(tmp_path, "bash")
        _make_git_shim(bin_dir, origin="git@github.com:osac-project/x.git")
        argv_log = tmp_path / "curl-argv.log"
        _make_curl_argv_capturing_shim(bin_dir, argv_log, exit_code=0)
        result = _run_script(bin_dir, base_url="https://example.invalid")
        assert result.returncode == 0, result.stderr
        argv = argv_log.read_text().splitlines()
        assert "-o" in argv
        assert "/dev/null" in argv

    def test_missing_base_url_yields_exit_2_without_curl(self, tmp_path):
        bin_dir = _bin_dir_with_real(tmp_path, "bash")
        _make_git_shim(bin_dir, origin="git@github.com:osac-project/x.git")
        call_log = tmp_path / "curl-calls.log"
        _make_curl_shim(bin_dir, call_log, exit_code=0)
        result = _run_script(bin_dir, base_url=None)
        assert result.returncode == 2, result.stderr
        assert not call_log.exists(), "curl must never be invoked when CHAI_BOT_BASE_URL is unset"

    def test_non_https_base_url_yields_exit_2_without_curl(self, tmp_path):
        """A non-https remote CHAI_BOT_BASE_URL must be rejected with exit 2
        BEFORE any curl call -- CHAI_TOKEN would otherwise be sent cleartext
        as a bearer header. (Loopback http:// is the sole allowed exception,
        exercised by TestRealHttpServerReachability's 127.0.0.1 cases.)"""
        bin_dir = _bin_dir_with_real(tmp_path, "bash")
        _make_git_shim(bin_dir, origin="git@github.com:osac-project/x.git")
        call_log = tmp_path / "curl-calls.log"
        _make_curl_shim(bin_dir, call_log, exit_code=0)
        result = _run_script(bin_dir, base_url="http://example.invalid")
        assert result.returncode == 2, result.stderr
        assert not call_log.exists(), "curl must never be invoked for a non-https CHAI_BOT_BASE_URL"

    def test_non_matching_repo_makes_zero_curl_calls(self, tmp_path):
        bin_dir = _bin_dir_with_real(tmp_path, "bash")
        _make_git_shim(bin_dir, origin="git@github.com:not-osac-project/x.git")
        call_log = tmp_path / "curl-calls.log"
        _make_curl_shim(bin_dir, call_log, exit_code=0)
        result = _run_script(bin_dir, base_url="https://example.invalid")
        assert result.returncode == 1, result.stderr
        assert not call_log.exists(), "curl must never be invoked when the repo gate rejects"


class TestRealHttpServerReachability:
    """End-to-end reachability probe using a REAL stdlib http.server
    bound to an OS-assigned port on 127.0.0.1 and the REAL curl binary (no
    curl shim at all) -- validates the actual
    `curl --connect-timeout 3 --max-time 3 -s -o /dev/null "${CHAI_BOT_BASE_URL}/"`
    invocation and URL construction end-to-end, not just that some shimmed
    curl exit code propagates correctly. The git remote gate is still shimmed
    (a fake `git` on PATH matching osac-project), since Step 1-3 is
    test_git_remote_gate.py's concern, not this one's."""

    def test_real_server_responds_exits_0(self, tmp_path):
        bin_dir = _bin_dir_with_real(tmp_path, "bash", "curl")
        _make_git_shim(bin_dir, origin="git@github.com:osac-project/x.git")

        server = http.server.HTTPServer(("127.0.0.1", 0), _QuietNotFoundHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = _run_script(bin_dir, base_url=f"http://127.0.0.1:{port}")
            assert result.returncode == 0, result.stderr
        finally:
            server.shutdown()
            thread.join(timeout=5)

    def test_real_server_404_still_exits_0(self, tmp_path):
        """The real server genuinely answers 404 for every
        request -- curl still connects successfully, so the script must
        still exit 0. Proves end-to-end (real curl, real HTTP response) that
        only connection-level failure is treated as unreachable."""
        bin_dir = _bin_dir_with_real(tmp_path, "bash", "curl")
        _make_git_shim(bin_dir, origin="git@github.com:osac-project/x.git")

        server = http.server.HTTPServer(("127.0.0.1", 0), _QuietNotFoundHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = _run_script(bin_dir, base_url=f"http://127.0.0.1:{port}")
            assert result.returncode == 0, result.stderr
        finally:
            server.shutdown()
            thread.join(timeout=5)

    def test_closed_port_yields_exit_2(self, tmp_path):
        """Nothing listening on this port (bound then immediately released) --
        the real curl binary must fail to connect, yielding exit 2."""
        bin_dir = _bin_dir_with_real(tmp_path, "bash", "curl")
        _make_git_shim(bin_dir, origin="git@github.com:osac-project/x.git")

        port = _free_tcp_port()
        result = _run_script(bin_dir, base_url=f"http://127.0.0.1:{port}")
        assert result.returncode == 2, result.stderr


class TestHttpsSchemeEnforcement:
    """The https gate (check-availability.sh): a non-https, non-loopback
    CHAI_BOT_BASE_URL is rejected (exit 2, curl never invoked) so CHAI_TOKEN
    is never sent over cleartext to a remote host. http:// is allowed ONLY
    for a genuine loopback host (localhost / 127.0.0.1 / ::1) anchored to a
    full host boundary -- optional numeric port, then only /path or end,
    never userinfo -- so a crafted http://localhost:PORT@remote/ (whose real
    host is remote) cannot masquerade as loopback."""

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://localhost",
            "http://localhost:8080",
            "http://localhost:8080/probe",
            "http://127.0.0.1:9999",
            "http://[::1]",
            "http://[::1]:8080/probe",
        ],
    )
    def test_loopback_http_reaches_curl(self, tmp_path, base_url):
        """Every genuine loopback http:// form passes the scheme gate and
        reaches the curl probe (shimmed to exit 0 -> script exit 0)."""
        bin_dir = _bin_dir_with_real(tmp_path, "bash")
        _make_git_shim(bin_dir, origin="git@github.com:osac-project/x.git")
        call_log = tmp_path / "curl-calls.log"
        _make_curl_shim(bin_dir, call_log, exit_code=0)
        result = _run_script(bin_dir, base_url=base_url)
        assert result.returncode == 0, result.stderr
        assert call_log.exists(), f"{base_url!r} should reach the curl probe"

    @pytest.mark.parametrize(
        "base_url",
        [
            # userinfo injection: real host is remote; the loopback token is
            # merely basic-auth user:pass, so this must NOT pass as loopback.
            "http://localhost:9@attacker.test/",
            "http://127.0.0.1:1234@attacker.test/",
            "http://[::1]:1234@attacker.test/",
            "http://localhost@attacker.test/",
            # lookalike hosts: the loopback literal is only a prefix/substring.
            "http://localhost.evil.example.com/",
            "http://127.0.0.1.evil.example.com/",
            "http://127.0.0.1evil.com/",
            # CR/LF-bearing values: rejected outright (never a legitimate URL,
            # and otherwise make the bash ERE / Python regex diverge).
            "http://localhost\n",
            "http://localhost/x\nhttp://evil.example.com/",
        ],
    )
    def test_non_loopback_http_rejected_without_curl(self, tmp_path, base_url):
        """Crafted/lookalike http:// hosts that are NOT genuine loopback are
        rejected (exit 2) before curl -- keeping CHAI_TOKEN off cleartext."""
        bin_dir = _bin_dir_with_real(tmp_path, "bash")
        _make_git_shim(bin_dir, origin="git@github.com:osac-project/x.git")
        call_log = tmp_path / "curl-calls.log"
        _make_curl_shim(bin_dir, call_log, exit_code=0)
        result = _run_script(bin_dir, base_url=base_url)
        assert result.returncode == 2, f"{base_url!r} should be rejected: {result.stderr}"
        assert not call_log.exists(), f"curl must not be invoked for {base_url!r}"
