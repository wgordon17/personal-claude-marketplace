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
2. Locate the physical `.jsonl` session file using `bash`:
   `SESSION_FILE=$(find ~/.omp/agent/sessions ~/.omp/sessions -type f -name "*<session-id>*.jsonl" 2>/dev/null | head -n 1)`
3. Locate the compactor script on the filesystem:
   `SCRIPT=$(find ~/.omp/plugins ~/.claude/plugins -path "*/omp-skills/*/compactor.py" | head -n 1)`
4. Execute the compactor pipeline via `bash`, piping the raw JSONL session data directly into the script:
   `cat "$SESSION_FILE" | uv run python "$SCRIPT" <session-id>`
