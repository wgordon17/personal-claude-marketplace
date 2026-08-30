"""Black-box subprocess tests for vertex-log-monitor's two hook scripts:
refresh.sh (writes the Vertex AI logging-state cache) and status.sh (reads
it). Follows the pattern in test_verify_upstream_header.py and
test_stop_hook.py: subprocess.run(["bash", SCRIPT], env=..., ...), asserting
on exit code and stdout/stderr/file content, with all network calls (gcloud,
curl) replaced by PATH/GCLOUD_BIN-shimmed shell scripts controlled via env
vars, and status.sh's cache always hand-written so its read side is tested
independently of refresh.sh's write side (plus a small integration section
tying the two together, since they share an implicit JSON schema that nothing
else enforces).

IMPORTANT: this test suite runs inside Claude Code sessions where the
ANTHROPIC_DEFAULT_*_MODEL / ANTHROPIC_VERTEX_PROJECT_ID / CLOUD_ML_REGION env
vars vertex-log-monitor is designed to read are very likely already set in the
ambient environment (that's the whole point of the plugin). Every refresh.sh
env built below explicitly strips them before adding back only what a given
test wants, so tests are deterministic regardless of the session they run in.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
HOOKS_DIR = REPO_ROOT / "vertex-log-monitor" / "hooks"
REFRESH_SCRIPT = HOOKS_DIR / "refresh.sh"
STATUS_SCRIPT = HOOKS_DIR / "status.sh"
LAUNCHER_SCRIPT = HOOKS_DIR / "status-launcher.sh"

_ENV_VARS_TO_ISOLATE = (
    "ANTHROPIC_VERTEX_PROJECT_ID",
    "CLOUD_ML_REGION",
    "VERTEX_LOG_PROJECT",
    "VERTEX_LOG_LOCATION",
    "VERTEX_LOG_MODELS",
    "VERTEX_LOG_FORCE",
    "VERTEX_LOG_THROTTLE",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
)

_GCLOUD_SHIM_BODY = r"""case "$1 $2" in
  "auth print-access-token") printf "%s\n" "${GCLOUD_SHIM_TOKEN:-}" ;;
  "projects get-iam-policy") printf "%s\n" "${GCLOUD_SHIM_POLICY:-}" ;;
  *) exit 1 ;;
esac"""

_CURL_SHIM_BODY = r'printf "%s\n%s" "$CURL_SHIM_BODY" "$CURL_SHIM_CODE"'

# Same canned response, but first captures the -K config received on stdin so a
# test can assert the actual request URL/headers refresh.sh generated.
_CAPTURING_CURL_SHIM_BODY = (
    'cat >> "$CURL_CAPTURE"\n' + r'printf "%s\n%s" "$CURL_SHIM_BODY" "$CURL_SHIM_CODE"'
)


# ── Shim / env helpers ──────────────────────────────────────────────────────


def _make_shim(bin_dir: Path, name: str, body: str) -> Path:
    """Write an executable bash shim named `name` into bin_dir; returns bin_dir."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / name
    script.write_text(f"#!/usr/bin/env bash\n{body}\n")
    script.chmod(0o755)
    return bin_dir


def _isolated_path(tmp_path: Path, dirname: str, tools: list[str]) -> str:
    """Build a PATH containing only symlinks to the named real binaries, so
    anything NOT in `tools` (e.g. jq) is guaranteed absent regardless of where
    it lives on the host."""
    bin_dir = tmp_path / dirname
    bin_dir.mkdir(parents=True, exist_ok=True)
    for tool in tools:
        real = shutil.which(tool)
        assert real, f"required tool {tool!r} not found on PATH"
        (bin_dir / tool).symlink_to(real)
    return str(bin_dir)


def _refresh_env(
    tmp_path: Path,
    *,
    project: str | None = "test-project",
    models: str | None = "claude-test-model",
    force: bool = True,
    gcloud_token: str | None = "fake-token",
    gcloud_policy: str | None = '{"auditConfigs":[]}',
    curl_code: str = "200",
    curl_body: str = '{"loggingConfig":{"enabled":true}}',
    path_override: str | None = None,
) -> tuple[dict[str, str], Path]:
    """Build an isolated env + cache_dir for a black-box refresh.sh run with
    gcloud/curl shimmed out. Returns (env, cache_dir)."""
    env = dict(os.environ)
    for key in _ENV_VARS_TO_ISOLATE:
        env.pop(key, None)

    cache_dir = tmp_path / "cache"
    curl_bin = _make_shim(tmp_path / "curl-shim", "curl", _CURL_SHIM_BODY)
    gcloud_bin = _make_shim(tmp_path / "gcloud-shim", "gcloud", _GCLOUD_SHIM_BODY) / "gcloud"

    env["PATH"] = path_override or f"{curl_bin}:{os.environ['PATH']}"
    env["VERTEX_LOG_CACHE_DIR"] = str(cache_dir)
    env["GCLOUD_BIN"] = str(gcloud_bin)
    env["CURL_SHIM_CODE"] = curl_code
    env["CURL_SHIM_BODY"] = curl_body

    if gcloud_token is not None:
        env["GCLOUD_SHIM_TOKEN"] = gcloud_token
    if gcloud_policy is not None:
        env["GCLOUD_SHIM_POLICY"] = gcloud_policy
    if project is not None:
        env["VERTEX_LOG_PROJECT"] = project
    if models is not None:
        env["VERTEX_LOG_MODELS"] = models
    if force:
        env["VERTEX_LOG_FORCE"] = "1"

    return env, cache_dir


def _run_refresh(env: dict[str, str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(REFRESH_SCRIPT)], capture_output=True, text=True, env=env, cwd=cwd
    )


def _make_status_with_refresh_stub(tmp_path: Path, marker: Path) -> Path:
    """Copy status.sh into a temp hooks dir beside a stub refresh.sh that touches
    `marker`, and return the copy's path. status.sh resolves refresh.sh as a
    sibling of its own real path (via BASH_SOURCE), so this observes self-heal
    without running the real refresh.sh -- mirroring how the stable launcher
    execs status.sh by its real versioned path in production."""
    hooks = tmp_path / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    status = hooks / "status.sh"
    shutil.copy2(STATUS_SCRIPT, status)
    status.chmod(0o755)
    refresh = hooks / "refresh.sh"
    refresh.write_text(f"#!/usr/bin/env bash\ntouch {marker}\n")
    refresh.chmod(0o755)
    return status


def _wait_for(path: Path, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.05)


def _read_cache(cache_dir: Path) -> dict:
    return json.loads((cache_dir / "vertex-logging-state.json").read_text())


def _write_cache(cache_dir: Path, data: dict, *, stale_seconds: float | None = None) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "vertex-logging-state.json"
    cache_file.write_text(json.dumps(data))
    if stale_seconds is not None:
        old = time.time() - stale_seconds
        os.utime(cache_file, (old, old))
    return cache_file


def _run_status(
    cache_dir: Path,
    *args: str,
    stdin_json: dict | None = None,
    extra_env: dict[str, str] | None = None,
    status_script: Path | None = None,
) -> subprocess.CompletedProcess:
    env = {**os.environ, "VERTEX_LOG_CACHE_DIR": str(cache_dir), "VERTEX_LOG_SELFHEAL": "0"}
    # Isolate ambient color toggles so color-mode assertions are deterministic
    # regardless of the session/CI env (mirrors _refresh_env's var stripping).
    env.pop("NO_COLOR", None)
    env.pop("STATUSLINE_PLAIN", None)
    if extra_env:
        env.update(extra_env)
    stdin_text = json.dumps(stdin_json) if stdin_json is not None else ""
    return subprocess.run(
        ["bash", str(status_script or STATUS_SCRIPT), *args],
        input=stdin_text,
        capture_output=True,
        text=True,
        env=env,
    )


# ── refresh.sh: early-exit error caches ─────────────────────────────────────


class TestRefreshEarlyExits:
    def test_no_jq_writes_error_cache(self, tmp_path):
        env, cache_dir = _refresh_env(tmp_path)
        env["PATH"] = _isolated_path(
            tmp_path, "no-jq-bin", ["bash", "dirname", "mkdir", "ln", "date"]
        )
        result = _run_refresh(env)
        assert result.returncode == 0, result.stderr
        cache = _read_cache(cache_dir)
        assert cache["error"] == "no_jq"
        assert cache["models"] == {}

    def test_no_project_writes_error_cache(self, tmp_path):
        env, cache_dir = _refresh_env(tmp_path, project=None)
        result = _run_refresh(env)
        assert result.returncode == 0, result.stderr
        cache = _read_cache(cache_dir)
        assert cache["error"] == "no_project"
        assert cache["models"] == {}

    def test_malformed_project_rejected_as_no_project(self, tmp_path):
        """A PROJECT that fails GCP project-id validation must be rejected into
        the no_project path, not spliced into the gcloud/curl calls."""
        env, cache_dir = _refresh_env(tmp_path, project="--not-a-project")
        result = _run_refresh(env)
        assert result.returncode == 0, result.stderr
        assert _read_cache(cache_dir)["error"] == "no_project"

    def test_project_with_path_separator_rejected(self, tmp_path):
        """A PROJECT containing a path separator fails validation and is
        rejected into the no_project path rather than spliced into the URL."""
        env, cache_dir = _refresh_env(tmp_path, project="test-project/../evil")
        result = _run_refresh(env)
        assert result.returncode == 0, result.stderr
        assert _read_cache(cache_dir)["error"] == "no_project"

    def test_no_gcloud_token_writes_error_cache(self, tmp_path):
        env, cache_dir = _refresh_env(tmp_path, gcloud_token=None)
        result = _run_refresh(env)
        assert result.returncode == 0, result.stderr
        cache = _read_cache(cache_dir)
        assert cache["error"] == "no_gcloud_token"
        assert cache["project"] == "test-project"
        assert cache["models"] == {}


# ── refresh.sh: HTTP-code -> logging-state mapping ──────────────────────────


class TestRefreshStateMapping:
    @pytest.mark.parametrize(
        ("curl_code", "curl_body", "expected_state"),
        [
            ("200", '{"loggingConfig":{"enabled":true}}', "LOGGED"),
            ("200", '{"loggingConfig":{"enabled":false}}', "config-off"),
            ("404", "{}", "unlogged"),
            ("403", "{}", "denied"),
            ("500", "{}", "error:500"),
        ],
        ids=["200-enabled", "200-disabled", "404", "403", "500-unexpected"],
    )
    def test_http_code_maps_to_expected_state(self, tmp_path, curl_code, curl_body, expected_state):
        env, cache_dir = _refresh_env(tmp_path, curl_code=curl_code, curl_body=curl_body)
        result = _run_refresh(env)
        assert result.returncode == 0, result.stderr
        cache = _read_cache(cache_dir)
        assert cache["models"] == {"claude-test-model": expected_state}

    def test_strip_model_normalization_dedupes_tagged_variants(self, tmp_path):
        """Two raw model refs (@version tag, [bracket] tag) that normalize to
        the same bare id must collapse into ONE cache entry -- exercises both
        strip_model() and the case-statement dedup loop."""
        env, cache_dir = _refresh_env(
            tmp_path,
            models="claude-sonnet-5@20250101 claude-sonnet-5[1m]",
            curl_code="200",
            curl_body='{"loggingConfig":{"enabled":true}}',
        )
        result = _run_refresh(env)
        assert result.returncode == 0, result.stderr
        cache = _read_cache(cache_dir)
        assert cache["models"] == {"claude-sonnet-5": "LOGGED"}

    def test_malformed_location_falls_back_to_global(self, tmp_path):
        """SSRF guard: a LOCATION with a path separator must be rejected and
        fall back to 'global' before it reaches the request URL."""
        env, cache_dir = _refresh_env(tmp_path)
        env["VERTEX_LOG_LOCATION"] = "attacker.example.com/"
        result = _run_refresh(env)
        assert result.returncode == 0, result.stderr
        assert _read_cache(cache_dir)["location"] == "global"

    def test_hostile_model_value_dropped(self, tmp_path):
        """A model value with characters outside the allowlist (here a double
        quote) is dropped before it reaches the curl -K config, leaving only
        the valid model in the cache."""
        env, cache_dir = _refresh_env(
            tmp_path,
            models='claude-sonnet-5 bad"model',
            curl_code="404",
            curl_body="{}",
        )
        result = _run_refresh(env)
        assert result.returncode == 0, result.stderr
        assert _read_cache(cache_dir)["models"] == {"claude-sonnet-5": "unlogged"}

    def test_bracket_tagged_model_not_glob_expanded(self, tmp_path):
        """A model value with glob metacharacters (claude-opus-4-8[1m]) is
        treated literally, not expanded against files in refresh.sh's CWD."""
        env, cache_dir = _refresh_env(
            tmp_path,
            models="claude-opus-4-8[1m]",
            curl_code="200",
            curl_body='{"loggingConfig":{"enabled":true}}',
        )
        # A file the [1m] class would glob-match if pathname expansion were live:
        (tmp_path / "claude-opus-4-8m").write_text("")
        result = _run_refresh(env, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert _read_cache(cache_dir)["models"] == {"claude-opus-4-8": "LOGGED"}


# ── refresh.sh: Data Access audit-logging detection ─────────────────────────


class TestRefreshAuditDetection:
    def test_audit_on_when_data_read_enabled(self, tmp_path):
        policy = (
            '{"auditConfigs":[{"service":"aiplatform.googleapis.com",'
            '"auditLogConfigs":[{"logType":"DATA_READ"}]}]}'
        )
        env, cache_dir = _refresh_env(tmp_path, gcloud_policy=policy)
        result = _run_refresh(env)
        assert result.returncode == 0, result.stderr
        assert _read_cache(cache_dir)["audit_data_access"] == "on"

    def test_audit_off_when_only_admin_read_enabled(self, tmp_path):
        """An auditConfigs entry existing is not enough -- only DATA_READ /
        DATA_WRITE logTypes count. ADMIN_READ-only must report 'off'."""
        policy = (
            '{"auditConfigs":[{"service":"aiplatform.googleapis.com",'
            '"auditLogConfigs":[{"logType":"ADMIN_READ"}]}]}'
        )
        env, cache_dir = _refresh_env(tmp_path, gcloud_policy=policy)
        result = _run_refresh(env)
        assert result.returncode == 0, result.stderr
        assert _read_cache(cache_dir)["audit_data_access"] == "off"

    def test_audit_unknown_when_iam_policy_unavailable(self, tmp_path):
        env, cache_dir = _refresh_env(tmp_path, gcloud_policy=None)
        result = _run_refresh(env)
        assert result.returncode == 0, result.stderr
        assert _read_cache(cache_dir)["audit_data_access"] == "unknown"


# ── refresh.sh: throttle + mkdir-lock reclaim ───────────────────────────────


class TestRefreshThrottleAndLock:
    def test_throttle_skips_refresh_when_cache_fresh(self, tmp_path):
        env, cache_dir = _refresh_env(tmp_path, force=False)
        _write_cache(cache_dir, {"updated": 1, "error": "sentinel", "models": {}})
        result = _run_refresh(env)
        assert result.returncode == 0, result.stderr
        # Untouched: throttle short-circuited before any write_cache call.
        assert _read_cache(cache_dir)["updated"] == 1

    def test_lock_blocks_concurrent_refresh_when_fresh(self, tmp_path):
        env, cache_dir = _refresh_env(tmp_path, force=False)
        (cache_dir / ".vertex-log-monitor.lock").mkdir(parents=True)
        result = _run_refresh(env)
        assert result.returncode == 0, result.stderr
        assert not (cache_dir / "vertex-logging-state.json").exists()

    def test_stale_lock_is_reclaimed_and_refresh_completes(self, tmp_path):
        env, cache_dir = _refresh_env(tmp_path, force=True)
        lock = cache_dir / ".vertex-log-monitor.lock"
        lock.mkdir(parents=True)
        stale_mtime = time.time() - 200  # > 120s reclaim threshold
        os.utime(lock, (stale_mtime, stale_mtime))
        result = _run_refresh(env)
        assert result.returncode == 0, result.stderr
        assert _read_cache(cache_dir)["models"] == {"claude-test-model": "LOGGED"}
        assert not lock.exists()  # trap removed the reclaimed lock on exit

    def test_force_reclaims_fresh_lock_and_completes(self, tmp_path):
        """VERTEX_LOG_FORCE must produce a fresh write even when a FRESH lock is
        held: it reclaims the lock immediately and completes, without waiting or
        exiting (so a user-invoked forced check never stalls on the lock)."""
        env, cache_dir = _refresh_env(tmp_path, force=True)
        (cache_dir / ".vertex-log-monitor.lock").mkdir(parents=True)  # fresh mtime
        start = time.monotonic()
        result = _run_refresh(env)
        elapsed = time.monotonic() - start
        assert result.returncode == 0, result.stderr
        assert elapsed < 5  # reclaims immediately, does not wait the lock out
        assert _read_cache(cache_dir)["models"] == {"claude-test-model": "LOGGED"}

    def test_hung_gcloud_killed_by_timeout(self, tmp_path):
        """A hung gcloud is killed by the timeout wrapper rather than wedging
        the refresh, surfacing as no_gcloud_token."""
        if not (shutil.which("timeout") or shutil.which("gtimeout")):
            pytest.skip("no timeout/gtimeout on PATH")
        env, cache_dir = _refresh_env(tmp_path)
        env["VERTEX_LOG_GCLOUD_TIMEOUT"] = "1"
        gcloud_bin = Path(env["GCLOUD_BIN"])
        gcloud_bin.write_text("#!/usr/bin/env bash\nsleep 30\n")
        gcloud_bin.chmod(0o755)
        start = time.monotonic()
        result = _run_refresh(env)
        elapsed = time.monotonic() - start
        assert result.returncode == 0, result.stderr
        assert elapsed < 10  # killed at ~1s, not hung for the full 30s
        assert _read_cache(cache_dir)["error"] == "no_gcloud_token"


# ── refresh.sh: gcloud binary resolution ────────────────────────────────────


class TestRefreshGcloudResolution:
    def test_gcloud_resolved_via_path_when_gcloud_bin_unset(self, tmp_path):
        """With GCLOUD_BIN unset, gcloud is found on PATH (the common case)."""
        env, cache_dir = _refresh_env(tmp_path)
        gcloud_dir = Path(env["GCLOUD_BIN"]).parent
        del env["GCLOUD_BIN"]
        env["PATH"] = f"{gcloud_dir}:{env['PATH']}"
        result = _run_refresh(env)
        assert result.returncode == 0, result.stderr
        assert _read_cache(cache_dir)["models"] == {"claude-test-model": "LOGGED"}


# ── refresh.sh: request URL safety (network-layer, real -K config) ──────────


class TestRefreshRequestUrlSafety:
    def test_hostile_inputs_never_redirect_request_url(self, tmp_path):
        """With a hostile LOCATION and model value, every token-bearing request
        URL still points at googleapis.com -- the -K config is not redirected
        and the hostile model is dropped before it reaches the URL. Asserts the
        actual config curl received, not just the recorded cache fields."""
        env, _ = _refresh_env(
            tmp_path, models='claude-sonnet-5 ev"il', curl_code="404", curl_body="{}"
        )
        # Swap in a curl shim that captures the -K config it receives on stdin.
        _make_shim(tmp_path / "curl-shim", "curl", _CAPTURING_CURL_SHIM_BODY)
        capture = tmp_path / "curl-config-capture.txt"
        env["CURL_CAPTURE"] = str(capture)
        env["VERTEX_LOG_LOCATION"] = "attacker.example.com/"
        result = _run_refresh(env)
        assert result.returncode == 0, result.stderr
        url_lines = [ln for ln in capture.read_text().splitlines() if ln.startswith("url = ")]
        assert url_lines  # the one valid model was queried
        for ln in url_lines:
            assert "googleapis.com" in ln
            assert "attacker.example.com" not in ln


# ── status.sh: cache absence / staleness / error passthrough ───────────────


class TestStatusEarlyExits:
    def test_missing_cache_reports_unknown(self, tmp_path):
        result = _run_status(tmp_path / "cache")
        assert result.returncode == 0
        assert result.stdout == "\x1b[90m⚪ vtx:? (no cache)\x1b[0m"

    def test_missing_jq_reports_unknown(self, tmp_path):
        cache_dir = tmp_path / "cache"
        _write_cache(cache_dir, {"updated": 1, "models": {}})
        no_jq_path = _isolated_path(tmp_path, "no-jq-bin", ["bash"])
        result = _run_status(cache_dir, "--plain", extra_env={"PATH": no_jq_path})
        assert result.returncode == 0
        assert result.stdout == "⚪ vtx:? (no jq)"

    def test_stale_cache_reports_unknown_without_selfheal(self, tmp_path):
        cache_dir = tmp_path / "cache"
        _write_cache(cache_dir, {"updated": 1, "models": {}}, stale_seconds=6000)
        result = _run_status(cache_dir, "--plain")  # VERTEX_LOG_SELFHEAL=0 by default
        assert result.returncode == 0
        assert result.stdout == "⚪ vtx:? (stale)"

    def test_cache_error_field_short_circuits_before_model_logic(self, tmp_path):
        cache_dir = tmp_path / "cache"
        _write_cache(cache_dir, {"updated": 1, "error": "no_gcloud_token", "models": {}})
        result = _run_status(cache_dir, "--plain", stdin_json={"model": {"id": "claude-sonnet-5"}})
        assert result.returncode == 0
        assert result.stdout == "⚪ vtx:? (no_gcloud_token)"


# ── status.sh: per-model (stdin) lookup ──────────────────────────────────────


class TestStatusPerModel:
    @pytest.mark.parametrize(
        "model_id",
        ["claude-sonnet-5", "claude-sonnet-5@20250101", "claude-sonnet-5[1m]"],
        ids=["bare", "at-version-tag", "bracket-tag"],
    )
    def test_stdin_model_id_normalizes_to_cache_key(self, tmp_path, model_id):
        cache_dir = tmp_path / "cache"
        _write_cache(
            cache_dir,
            {"updated": 1, "audit_data_access": "off", "models": {"claude-sonnet-5": "LOGGED"}},
        )
        result = _run_status(cache_dir, "--plain", stdin_json={"model": {"id": model_id}})
        assert result.returncode == 0
        assert result.stdout == "🔴 vtx:LOGGED"

    def test_no_data_for_unmonitored_model(self, tmp_path):
        cache_dir = tmp_path / "cache"
        _write_cache(
            cache_dir,
            {"updated": 1, "audit_data_access": "off", "models": {"claude-sonnet-5": "LOGGED"}},
        )
        result = _run_status(cache_dir, "--plain", stdin_json={"model": {"id": "claude-opus-4-8"}})
        assert result.returncode == 0
        assert result.stdout == "⚪ vtx:? (no data:claude-opus-4-8)"

    def test_unlogged_with_audit_off_is_green(self, tmp_path):
        cache_dir = tmp_path / "cache"
        _write_cache(
            cache_dir, {"updated": 1, "audit_data_access": "off", "models": {"m": "unlogged"}}
        )
        result = _run_status(cache_dir, "--plain", stdin_json={"model": {"id": "m"}})
        assert result.stdout == "🟢 vtx:unlogged"

    def test_unlogged_with_audit_on_is_audit_only(self, tmp_path):
        cache_dir = tmp_path / "cache"
        _write_cache(
            cache_dir, {"updated": 1, "audit_data_access": "on", "models": {"m": "unlogged"}}
        )
        result = _run_status(cache_dir, "--plain", stdin_json={"model": {"id": "m"}})
        assert result.stdout == "🟡 vtx:audit-only"

    def test_config_off_with_audit_on_is_audit_only(self, tmp_path):
        """config-off (200 + logging disabled) must run the same audit check as
        unlogged on the per-model path -- audit-on should surface as audit-only,
        not green."""
        cache_dir = tmp_path / "cache"
        _write_cache(
            cache_dir, {"updated": 1, "audit_data_access": "on", "models": {"m": "config-off"}}
        )
        result = _run_status(cache_dir, "--plain", stdin_json={"model": {"id": "m"}})
        assert result.stdout == "🟡 vtx:audit-only"

    def test_config_off_with_audit_off_is_green(self, tmp_path):
        cache_dir = tmp_path / "cache"
        _write_cache(
            cache_dir, {"updated": 1, "audit_data_access": "off", "models": {"m": "config-off"}}
        )
        result = _run_status(cache_dir, "--plain", stdin_json={"model": {"id": "m"}})
        assert result.stdout == "🟢 vtx:unlogged"

    def test_denied_reports_unknown(self, tmp_path):
        cache_dir = tmp_path / "cache"
        _write_cache(
            cache_dir, {"updated": 1, "audit_data_access": "off", "models": {"m": "denied"}}
        )
        result = _run_status(cache_dir, "--plain", stdin_json={"model": {"id": "m"}})
        assert result.stdout == "⚪ vtx:? (denied)"

    def test_unrecognized_state_string_falls_through_to_default(self, tmp_path):
        """Writer/reader contract: a raw refresh.sh error state like 'error:500'
        must pass through status.sh's default case verbatim."""
        cache_dir = tmp_path / "cache"
        _write_cache(
            cache_dir, {"updated": 1, "audit_data_access": "off", "models": {"m": "error:500"}}
        )
        result = _run_status(cache_dir, "--plain", stdin_json={"model": {"id": "m"}})
        assert result.stdout == "⚪ vtx:? (error:500)"


# ── status.sh: aggregate (no-stdin) logic ───────────────────────────────────


class TestStatusAggregate:
    def test_any_logged_wins(self, tmp_path):
        cache_dir = tmp_path / "cache"
        _write_cache(
            cache_dir,
            {"updated": 1, "audit_data_access": "off", "models": {"a": "LOGGED", "b": "unlogged"}},
        )
        result = _run_status(cache_dir, "--plain")
        assert result.stdout == "🔴 vtx:LOGGED"

    def test_all_unlogged_or_config_off_is_green(self, tmp_path):
        cache_dir = tmp_path / "cache"
        _write_cache(
            cache_dir,
            {
                "updated": 1,
                "audit_data_access": "off",
                "models": {"a": "unlogged", "b": "config-off"},
            },
        )
        result = _run_status(cache_dir, "--plain")
        assert result.stdout == "🟢 vtx:unlogged"

    def test_mixed_states_is_orange(self, tmp_path):
        cache_dir = tmp_path / "cache"
        _write_cache(
            cache_dir,
            {"updated": 1, "audit_data_access": "off", "models": {"a": "unlogged", "b": "denied"}},
        )
        result = _run_status(cache_dir, "--plain")
        assert result.stdout == "🟠 vtx:mixed"

    def test_all_denied_reports_unknown(self, tmp_path):
        """A models map where every model is 'denied' (a permissions problem,
        not a real logging-state conflict) surfaces as unknown/denied, not the
        generic 'mixed' orange."""
        cache_dir = tmp_path / "cache"
        _write_cache(
            cache_dir,
            {"updated": 1, "audit_data_access": "off", "models": {"a": "denied", "b": "denied"}},
        )
        result = _run_status(cache_dir, "--plain")
        assert result.stdout == "⚪ vtx:? (denied)"

    def test_all_error_reports_unknown(self, tmp_path):
        """Every model in an error:* state surfaces as unknown, not 'mixed'."""
        cache_dir = tmp_path / "cache"
        _write_cache(
            cache_dir,
            {
                "updated": 1,
                "audit_data_access": "off",
                "models": {"a": "error:500", "b": "error:503"},
            },
        )
        result = _run_status(cache_dir, "--plain")
        assert result.stdout == "⚪ vtx:? (error)"


# ── status.sh: --plain / NO_COLOR / STATUSLINE_PLAIN ────────────────────────


class TestStatusColorFlags:
    def test_default_mode_includes_ansi_colors(self, tmp_path):
        cache_dir = tmp_path / "cache"
        _write_cache(
            cache_dir, {"updated": 1, "audit_data_access": "off", "models": {"m": "LOGGED"}}
        )
        result = _run_status(cache_dir, stdin_json={"model": {"id": "m"}})
        assert result.stdout == "\x1b[31m🔴 vtx:LOGGED\x1b[0m"

    def test_no_color_env_var_strips_ansi(self, tmp_path):
        cache_dir = tmp_path / "cache"
        _write_cache(
            cache_dir, {"updated": 1, "audit_data_access": "off", "models": {"m": "LOGGED"}}
        )
        result = _run_status(
            cache_dir, stdin_json={"model": {"id": "m"}}, extra_env={"NO_COLOR": "1"}
        )
        assert result.stdout == "🔴 vtx:LOGGED"


# ── Writer/reader integration: refresh.sh output consumed by status.sh ─────


class TestWriterReaderContract:
    def test_refresh_output_consumed_by_status_aggregate(self, tmp_path):
        env, cache_dir = _refresh_env(
            tmp_path,
            models="claude-sonnet-5",
            curl_code="200",
            curl_body='{"loggingConfig":{"enabled":true}}',
        )
        refresh_result = _run_refresh(env)
        assert refresh_result.returncode == 0, refresh_result.stderr

        status_result = _run_status(cache_dir, "--plain")
        assert status_result.stdout == "🔴 vtx:LOGGED"

    def test_refresh_output_consumed_by_status_per_model_with_tag(self, tmp_path):
        env, cache_dir = _refresh_env(
            tmp_path,
            models="claude-sonnet-5",
            curl_code="404",
            curl_body="{}",
        )
        refresh_result = _run_refresh(env)
        assert refresh_result.returncode == 0, refresh_result.stderr

        status_result = _run_status(
            cache_dir, "--plain", stdin_json={"model": {"id": "claude-sonnet-5@20250101"}}
        )
        assert status_result.stdout == "🟢 vtx:unlogged"


# ── status.sh: self-heal invocation ─────────────────────────────────────────


class TestStatusSelfHeal:
    def test_missing_cache_triggers_self_heal(self, tmp_path):
        cache_dir = tmp_path / "cache"
        marker = tmp_path / "healed"
        status = _make_status_with_refresh_stub(tmp_path, marker)
        result = _run_status(
            cache_dir, "--plain", extra_env={"VERTEX_LOG_SELFHEAL": "1"}, status_script=status
        )
        assert result.stdout == "⚪ vtx:? (no cache)"
        _wait_for(marker)
        assert marker.exists()

    def test_self_heal_skipped_when_fresh_lock_held(self, tmp_path):
        cache_dir = tmp_path / "cache"
        marker = tmp_path / "healed"
        status = _make_status_with_refresh_stub(tmp_path, marker)
        (cache_dir / ".vertex-log-monitor.lock").mkdir(parents=True)  # fresh mtime
        result = _run_status(
            cache_dir, "--plain", extra_env={"VERTEX_LOG_SELFHEAL": "1"}, status_script=status
        )
        assert result.stdout == "⚪ vtx:? (no cache)"
        time.sleep(0.3)
        assert not marker.exists()

    def test_self_heal_fires_when_lock_is_stale(self, tmp_path):
        cache_dir = tmp_path / "cache"
        marker = tmp_path / "healed"
        status = _make_status_with_refresh_stub(tmp_path, marker)
        lock = cache_dir / ".vertex-log-monitor.lock"
        lock.mkdir(parents=True)
        stale = time.time() - 200  # > 120s reclaim threshold
        os.utime(lock, (stale, stale))
        result = _run_status(
            cache_dir, "--plain", extra_env={"VERTEX_LOG_SELFHEAL": "1"}, status_script=status
        )
        assert result.stdout == "⚪ vtx:? (no cache)"
        _wait_for(marker)
        assert marker.exists()


# ── status-launcher.sh: version-independent entry-point resolution ───────────
# The stable path a prompt/statusline points at is the launcher, not a symlink
# into the versioned plugin dir. It resolves the installed status.sh at runtime
# (highest version, pinned to the installed marketplace instance), so a plugin
# version bump never leaves it dangling (the earlier symlink returned "Exit 127"
# in the statusline until the next SessionStart). These tests run the repo
# launcher directly (no baked pin), exercising the pinned branch via the
# VERTEX_LOG_PLUGIN_ROOT env var and the fallback branch via a HOME override.


class TestStatusLauncher:
    def _install_status(
        self, home: Path, version: str, sentinel: str, marketplace: str = "some-marketplace"
    ) -> Path:
        """Lay out a fake plugin-cache status.sh under $HOME that prints sentinel."""
        hooks = (
            home
            / ".claude"
            / "plugins"
            / "cache"
            / marketplace
            / "vertex-log-monitor"
            / version
            / "hooks"
        )
        hooks.mkdir(parents=True, exist_ok=True)
        status = hooks / "status.sh"
        status.write_text(f'#!/usr/bin/env bash\nprintf "%s" "{sentinel}"\n')
        status.chmod(0o755)
        return status

    def _plugin_root(self, home: Path, marketplace: str = "some-marketplace") -> Path:
        return home / ".claude" / "plugins" / "cache" / marketplace / "vertex-log-monitor"

    def _run_launcher(
        self, home: Path, stdin_text: str = "", plugin_root: Path | None = None
    ) -> subprocess.CompletedProcess:
        # HOME drives the fallback glob root; VERTEX_LOG_PLUGIN_ROOT exercises the
        # pinned branch (as the refresh.sh-baked copy sets it).
        env = {**os.environ, "HOME": str(home)}
        if plugin_root is not None:
            env["VERTEX_LOG_PLUGIN_ROOT"] = str(plugin_root)
        return subprocess.run(
            ["bash", str(LAUNCHER_SCRIPT), "--plain"],
            input=stdin_text,
            capture_output=True,
            text=True,
            env=env,
        )

    def test_resolves_and_execs_installed_status(self, tmp_path):
        self._install_status(tmp_path, "0.1.1", "SENTINEL-A")
        result = self._run_launcher(tmp_path)
        assert result.returncode == 0
        assert result.stdout == "SENTINEL-A"

    def test_highest_version_wins_not_mtime(self, tmp_path):
        # Highest SEMVER version wins, even when it has an OLDER mtime than a
        # lower version -- proves selection is semver, not filesystem timestamp.
        high = self._install_status(tmp_path, "0.10.0", "TEN")
        self._install_status(tmp_path, "0.9.0", "NINE")  # newer mtime, lower version
        past = time.time() - 100
        os.utime(high, (past, past))
        result = self._run_launcher(tmp_path)
        assert result.returncode == 0
        assert result.stdout == "TEN"  # 0.10.0 > 0.9.0 numerically (not lexically)

    def test_pins_to_plugin_root_ignoring_other_marketplaces(self, tmp_path):
        # With a pinned root, a same-named plugin from another marketplace must
        # NOT win, even at a higher version -- restores the old trust boundary.
        self._install_status(tmp_path, "0.1.0", "MINE", marketplace="trusted-mkt")
        self._install_status(tmp_path, "9.9.9", "THEIRS", marketplace="other-mkt")
        result = self._run_launcher(
            tmp_path, plugin_root=self._plugin_root(tmp_path, "trusted-mkt")
        )
        assert result.returncode == 0
        assert result.stdout == "MINE"

    def test_pinned_root_picks_highest_version_within(self, tmp_path):
        self._install_status(tmp_path, "0.1.0", "OLD", marketplace="trusted-mkt")
        self._install_status(tmp_path, "0.2.0", "NEW", marketplace="trusted-mkt")
        result = self._run_launcher(
            tmp_path, plugin_root=self._plugin_root(tmp_path, "trusted-mkt")
        )
        assert result.returncode == 0
        assert result.stdout == "NEW"

    def test_no_install_exits_zero_without_output(self, tmp_path):
        # Nothing installed under $HOME: render nothing, exit 0 -- never 127, and
        # no error output (guards the empty-array crash below).
        result = self._run_launcher(tmp_path)
        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""

    def test_no_install_exits_zero_under_system_bash(self, tmp_path):
        # Regression guard for the bash 3.2 empty-array + `set -u` bug: the empty
        # fallback glob must exit 0 with no error, not "unbound variable". Runs
        # under /bin/bash explicitly (macOS system bash is 3.2; CI's is 5.x, so
        # this only reproduces the bug on macOS -- still worth asserting there).
        if not os.path.exists("/bin/bash"):
            pytest.skip("no /bin/bash")
        result = subprocess.run(
            ["/bin/bash", str(LAUNCHER_SCRIPT), "--plain"],
            input="",
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(tmp_path)},
        )
        assert result.returncode == 0
        assert result.stdout == ""
        assert result.stderr == ""

    def test_highest_version_wins_under_system_bash(self, tmp_path):
        # Lock in the _ver_gt comparator (C-style for-loop + 10# arithmetic, the
        # most bash-3.2-sensitive new code) under system /bin/bash, not just the
        # harness's bash 5.x.
        if not os.path.exists("/bin/bash"):
            pytest.skip("no /bin/bash")
        self._install_status(tmp_path, "0.10.0", "TEN")
        self._install_status(tmp_path, "0.9.0", "NINE")
        result = subprocess.run(
            ["/bin/bash", str(LAUNCHER_SCRIPT), "--plain"],
            input="",
            capture_output=True,
            text=True,
            env={**os.environ, "HOME": str(tmp_path)},
        )
        assert result.returncode == 0
        assert result.stdout == "TEN"

    def test_non_executable_status_is_skipped(self, tmp_path):
        status = self._install_status(tmp_path, "0.1.1", "SENTINEL-A")
        status.chmod(0o644)  # present but not executable -> no runnable match
        result = self._run_launcher(tmp_path)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_passes_args_and_stdin_through(self, tmp_path):
        # The launcher must exec the resolved status.sh with its args AND stdin
        # (the session JSON) intact -- that is the whole point of `exec "$@"`.
        hooks = (
            tmp_path
            / ".claude"
            / "plugins"
            / "cache"
            / "some-marketplace"
            / "vertex-log-monitor"
            / "0.1.1"
            / "hooks"
        )
        hooks.mkdir(parents=True, exist_ok=True)
        status = hooks / "status.sh"
        status.write_text('#!/usr/bin/env bash\nprintf "%s|%s" "$1" "$(cat)"\n')
        status.chmod(0o755)
        result = self._run_launcher(tmp_path, stdin_text='{"model":{"id":"x"}}')
        assert result.returncode == 0
        assert result.stdout == '--plain|{"model":{"id":"x"}}'


# ── refresh.sh: stable entry-point install ──────────────────────────────────
# refresh.sh installs the launcher at $CACHE_DIR/vertex-log-monitor-status.sh
# atomically (temp file + rename), prepending a shebang and a
# VERTEX_LOG_PLUGIN_ROOT pin line before the launcher body. This block is the
# actual fix for the "Exit 127" bug, so it is asserted directly here.
# project=None makes the run exit early (no network) AFTER the install block,
# which runs unconditionally near the top of refresh.sh.

# Launcher body (source minus its own shebang) — the installed copy must contain
# this verbatim after the injected shebang + pin lines.
_LAUNCHER_BODY = "".join(LAUNCHER_SCRIPT.read_text().splitlines(keepends=True)[1:])


class TestRefreshInstallsLauncher:
    def test_install_writes_executable_pinned_launcher(self, tmp_path):
        env, cache_dir = _refresh_env(tmp_path, project=None)
        _run_refresh(env)
        entry = cache_dir / "vertex-log-monitor-status.sh"
        assert entry.is_file()
        assert not entry.is_symlink()
        assert os.access(entry, os.X_OK)
        lines = entry.read_text().splitlines(keepends=True)
        assert lines[0] == "#!/usr/bin/env bash\n"
        assert lines[1].startswith("VERTEX_LOG_PLUGIN_ROOT=")  # pinned at install time
        assert "".join(lines[2:]) == _LAUNCHER_BODY  # body copied verbatim

    def test_install_replaces_symlink_without_clobbering_target(self, tmp_path):
        # Simulates a machine still on the old symlink scheme. A plain `cp -f`
        # would write THROUGH the symlink onto `target`; the atomic mv must not.
        env, cache_dir = _refresh_env(tmp_path, project=None)
        cache_dir.mkdir(parents=True, exist_ok=True)
        target = cache_dir / "old-target.sh"
        target.write_text("ORIGINAL-TARGET-CONTENT")
        entry = cache_dir / "vertex-log-monitor-status.sh"
        entry.symlink_to(target.name)  # relative symlink, as the old scheme used
        _run_refresh(env)
        assert not entry.is_symlink()
        assert _LAUNCHER_BODY in entry.read_text()  # real launcher installed
        assert target.read_text() == "ORIGINAL-TARGET-CONTENT"  # not clobbered

    def test_install_removes_dead_refresh_symlink(self, tmp_path):
        # The old scheme also created a vertex-log-monitor-refresh.sh symlink,
        # now dead. refresh.sh must remove it.
        env, cache_dir = _refresh_env(tmp_path, project=None)
        cache_dir.mkdir(parents=True, exist_ok=True)
        dead = cache_dir / "vertex-log-monitor-refresh.sh"
        dead.write_text("#!/usr/bin/env bash\n")
        _run_refresh(env)
        assert not dead.exists()

    def test_baked_plugin_root_is_marketplace_scoped(self, tmp_path):
        # Faithful layout: refresh.sh in a versioned hooks dir under a marketplace.
        # The baked pin must be the marketplace-scoped plugin root (grandparent of
        # hooks) — version-independent, so it survives version bumps.
        inst_hooks = (
            tmp_path / "plugins" / "cache" / "mkt" / "vertex-log-monitor" / "0.1.1" / "hooks"
        )
        inst_hooks.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REFRESH_SCRIPT, inst_hooks / "refresh.sh")
        shutil.copy2(LAUNCHER_SCRIPT, inst_hooks / "status-launcher.sh")
        env, cache_dir = _refresh_env(tmp_path, project=None)
        subprocess.run(
            ["bash", str(inst_hooks / "refresh.sh")],
            capture_output=True,
            text=True,
            env=env,
        )
        entry = cache_dir / "vertex-log-monitor-status.sh"
        pin = [
            ln for ln in entry.read_text().splitlines() if ln.startswith("VERTEX_LOG_PLUGIN_ROOT=")
        ]
        assert pin, "installed launcher was not pinned"
        expected_root = inst_hooks.parent.parent  # .../mkt/vertex-log-monitor (no version)
        assert str(expected_root) in pin[0]
