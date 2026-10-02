# /compactor

Compact a stalled or massive session and safely update project memory.

## Usage
`omp /compactor TARGET_SESSION="session-123"`

## Execution
1. Extract the raw history JSON of the target session using the native `eval` tool, saving it to `/tmp/target_history.json`.
2. Execute the isolated Python compactor using `bash` and `uv`.
3. The compactor will generate `local://handoff-<id>.md` and update `hack/PROJECT.md` with new architectural rationale and constraints.

```python
import os, json
session_id = env("TARGET_SESSION")
if not session_id:
    log("Error: TARGET_SESSION environment variable not set. Use TARGET_SESSION='<id>'")
else:
    raw = read(f"history://{session_id}")
    write(f"/tmp/history_{session_id}.json", raw)
    log(f"Exported history to /tmp/history_{session_id}.json")
```

```bash
# Execute the isolated compactor script
uv run --with litellm --with pydantic python ~/.omp/plugins/cache/plugins/private-claude-marketplace___omp-skills___1.0.0/scripts/compactor.py "$TARGET_SESSION"
```
