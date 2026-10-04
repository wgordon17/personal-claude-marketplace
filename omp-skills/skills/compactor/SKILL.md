---
name: compactor
description: Compact a stalled or massive session and safely update project memory
---
# /compactor

Compact a stalled or massive session and safely update project memory.

## Usage
`omp /compactor <session-id>`

## Instructions for Agent
The user wants to compact the session ID passed in their prompt.
1. Extract the `<session-id>` from the prompt (it can be a shortened prefix like `01a0f73c`).
2. Locate the physical `.jsonl` session file safely using `bash` to prevent short-ID collisions (mid-string matches or multiple prefix matches):
   ```bash
   # Anchor to the underscore to ensure it matches the START of the UUID, not the middle
   MATCH_COUNT=$(find ~/.omp/agent/sessions ~/.omp/sessions -type f -name "*_<session-id>*.jsonl" 2>/dev/null | wc -l | tr -d ' ')
   if [ "$MATCH_COUNT" -eq 0 ]; then echo "Error: Session not found."; exit 1; fi
   if [ "$MATCH_COUNT" -gt 1 ]; then echo "Error: Collision detected. Provide a longer session ID."; exit 1; fi
   SESSION_FILE=$(find ~/.omp/agent/sessions ~/.omp/sessions -type f -name "*_<session-id>*.jsonl" 2>/dev/null)
   ```
3. Locate the compactor script on the filesystem:
   `SCRIPT=$(find -L ~/.omp/plugins ~/.claude/plugins -path "*/omp-skills/*/compactor.py" 2>/dev/null | head -n 1)`
4. Execute the compactor pipeline via `bash`, piping the raw JSONL session data directly into the script:
   `cat "$SESSION_FILE" | uv run python "$SCRIPT" <session-id>`
