"""Shared test helper functions for chai-bot/tests/ (not a conftest.py).

test_metrics_logging.py and test_chai_bot_omp_bridge_contract.py both invoke
the real hooks/metrics.py subprocess with a synthetic hook-JSON payload and
read back the resulting `events` rows -- this module is the single copy of
that black-box helper, rather than each test file maintaining its own
byte-identical version.

This lives in its own module (rather than conftest.py) so conftest.py stays
reserved for pytest fixtures, matching dev-guard/tests/conftest.py's
fixture-only convention. Import via `from _helpers import ...` -- pytest's
prepend import mode (the default) puts this directory on sys.path before
importing any test module here, so the bare import resolves without needing
this file to be a package.
"""

import json
import os
import sqlite3
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
METRICS_SCRIPT = REPO_ROOT / "chai-bot" / "hooks" / "metrics.py"


def run_metrics(payload: dict, db_path: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "GUARD_DB_PATH": str(db_path)}
    # Keep any ambient CHAI_BOT_BASE_URL out so the ask_persona permission
    # decision is deterministic (unset -> allow). Tests that exercise the
    # https/deny logic set CHAI_BOT_BASE_URL explicitly in their own env.
    env.pop("CHAI_BOT_BASE_URL", None)
    return subprocess.run(
        ["uv", "run", str(METRICS_SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )


def _events(db_path: Path) -> list[tuple]:
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT ts, session_id, tool_use_id, category, rule, action, command, detail "
        "FROM events ORDER BY id"
    ).fetchall()
    conn.close()
    return rows
