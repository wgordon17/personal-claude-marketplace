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
1. Extract the `<session-id>` from the prompt.
2. Locate the compactor script on the filesystem using `bash`:
   `SCRIPT=$(find ~/.omp/plugins ~/.claude/plugins -path "*/omp-skills/*/compactor.py" | head -n 1)`
3. Execute the compactor pipeline via `bash`, piping the history directly into the script:
   `omp read history://<session-id> | python3 $SCRIPT <session-id>`
