"""Shared test helpers for chai-bot/tests/.

test_metrics_logging.py and test_chai_bot_omp_bridge_contract.py both invoke
the real hooks/metrics.py subprocess with a synthetic hook-JSON payload and
read back the resulting `events` rows -- this module is the single copy of
that black-box helper, rather than each test file maintaining its own
byte-identical version.
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
