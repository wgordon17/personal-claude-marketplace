# /// script
# requires-python = ">=3.13"
# dependencies = ["pyyaml==6.0.3", "defusedxml==0.7.1"]
# [tool.uv]
# exclude-newer = "2026-10-20T00:00:00Z"
# ///
"""tech-radar CLI entry. Argument parsing only; all logic lives in tech_radar.cli."""

import sys

sys.dont_write_bytecode = True  # the verified app dir must contain only manifest-listed files
from tech_radar.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
