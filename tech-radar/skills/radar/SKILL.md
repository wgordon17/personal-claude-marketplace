---
name: radar
description: Personal tech radar — scheduled run, first-time bootstrap interview, and staging decisions from replies. Use when running "/tech-radar:radar run", "/tech-radar:radar bootstrap", or replying to a radar digest (placeholder — full methodology in Task 10).
---

# Tech Radar Skill (Placeholder)

This skill orchestrates the personal tech radar workflow:

1. **Scheduled runs** (`/tech-radar:radar run`): invokes the deterministic collector CLI, evaluates briefs, writes verdicts, and renders the digest.
2. **Bootstrap interview** (`/tech-radar:radar bootstrap`): runs environment precondition checks, conducts the initial taste and stack interview, and configures the scheduled run project.
3. **Digest replies**: stages item decisions, feedback, and taste profile updates for out-of-band confirmation via macOS notification codes.

## Bundled files

- [scripts/radar.py](scripts/radar.py)
- [scripts/radar.py.lock](scripts/radar.py.lock)
