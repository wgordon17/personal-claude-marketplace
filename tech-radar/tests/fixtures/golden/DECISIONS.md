# Tech Radar Decisions

Edit the `your edit` column using `^(adopt|trial|assess|hold|reject|snooze)( \d+d)?$` (for example `snooze 30d` or `hold`).
Edits are staged on the next `plan-run` and applied only after you confirm with `confirm <CODE>` from the macOS notification (or run `radar.py decisions apply --channel cli` in an interactive terminal).

| item | name | ring | decision | expires | your edit |
| --- | --- | --- | --- | --- | --- |
| R-0001 | litellm | hold | — | — |  |
| R-0002 | ripgrep | adopt | adopt | 2026-11-30 |  |
| R-0003 | uv-fast-pack | trial | — | — |  |
| R-0004 | gemini-4-argon | adopt | — | — |  |
| R-0005 | fetchaller | assess | — | — |  |
| R-0006 | actions/setup-uv | trial | — | — |  |
| R-0007 | context-compaction | assess | — | — |  |
| R-0008 | secret-internal-pkg | adopt | — | — |  |
| R-0009 | experimental-agent-tui | watch | — | — |  |
| R-0010 | legacy-xml-tool | assess | snooze | 2026-10-30 |  |
