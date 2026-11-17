# Standup Edit-Debrief Procedure and Synthesis Examples

This reference is used by `/standup` Phase 5 (for the edit-debrief loop) and by the `standup:standup-draft` agent (for multi-source synthesis examples).

## Part 1: Phase 5 Edit-Debrief Loop (`/standup`)

Raw diffs between an AI-generated standup draft and what the user posted to their standup channel are ambiguous: a deleted or rewritten line might have been factually wrong, not team-relevant, too verbose, or a style preference. Asking why captures high-signal feedback that compounds across runs.

1. **Locate the most recent draft/published pair:**
   - Read `<config_dir>/.standup-history.json` (written by `standup-tool standup-history --days 14 --output "<config_dir>/.standup-history.json"`).
   - Find the newest entry where **both** `draft` and `published` are non-empty strings.
   - If the file is missing, empty, or has no entry with both fields populated, skip the debrief without touching `<config_dir>/.standup-annotations.json`.

2. **Check whether this pair was already debriefed:**
   - If `<config_dir>/.standup-annotations.json` exists, read and parse it as a JSON array (if parsing fails, start with `[]`).
   - Check whether any existing annotation object has `"date"` equal to the candidate pair's `date`.
   - If a match exists, this pair was already debriefed in an earlier run — **skip the debrief completely** (make no `AskUserQuestion` calls and do not modify `.standup-annotations.json`).

3. **Offer the skip gate before asking change questions:**
   - Diff `draft` and `published` to identify significant edits (removed sections, removed items, reworded items, added items). If `draft` and `published` are identical, skip the debrief.
   - Ask via `AskUserQuestion`:
     `"I found N changes between your draft from <date> and what you posted. Quick debrief (helps me learn) or skip for today?"`
   - If the user selects **Skip**, do not modify `<config_dir>/.standup-annotations.json`.

4. **Debrief questions (capped at 3 questions per session):**
   - Group related edits into a single question where possible (for example removing an entire section is one question, not one per line).
   - Ask at most **3 questions** via `AskUserQuestion`; if more than 3 distinct changes exist, batch the remainder into the third question.
   - Provide these reason options for each question:
     - **Factually wrong** — draft stated something that did not happen or misattributed work.
     - **Not team-relevant** — accurate, but not useful for the standup audience.
     - **Too verbose** — right point, too many words.
     - **Style preference** — formatting, tone, or section placement choice.
     - *(Other — free text)*

5. **Persist and prune annotations (`90-day` window, mode `0600`):**
   - Append each new annotation as `{"date": "<pair-date>", "change": "<summary of edit>", "reason": "<selected category>", "detail": "<user note>"}`.
   - Prune any entry whose `date` is older than 90 days from today.
   - Write the updated JSON array to `<config_dir>/.standup-annotations.json` and enforce mode `0600`:
     ```bash
     chmod 600 "<config_dir>/.standup-annotations.json"
     ```

---

## Part 2: Multi-Source Synthesis Examples (`standup:standup-draft`)

The examples below illustrate how `standup:standup-draft` connects multiple sources from `.digest.json` while honoring the section headers, item prefix style, and `<!-- criteria: ... -->` comments supplied in the `<config>` block.

### Example 1: Multi-Source Feature Work (Plain-Line Template Style)

**Digest input (summarized):**
- `work_unit` `entity=ACME-42`, `sources=[git, pr, jira, slack, calendar]`
  - `git`: 5 commits to `internal/cli/networking/`
  - `pr`: `{number: 47, repo: "example-repo", state: "open", ci: "passing", role: "author"}`
  - `jira`: `{key: "ACME-42", status: "In Progress", summary: "Networking subcommands"}`
  - `slack`: `[{channel: "#eng", text: "CIDR discussion: /24 or /28?", thread_replies: 3}]`
  - `calendar`: `{summary: "Networking design sync", start_time: "14:00"}`

**Reasoning (`<analysis>` returned in agent text response, never written to `.standup-draft.txt`):**
```xml
<analysis>
<themes>Networking CLI spanning code, open PR, Slack thread, and design sync</themes>
<correlations>5 commits + example-repo#47 + ACME-42 + #eng CIDR thread + design sync = one effort</correlations>
<narrative>Implementation finished -> PR opened with green CI -> subnet sizing question open</narrative>
<notable>Unresolved /24 vs /28 CIDR choice affects subnet validation</notable>
<risks>Open CIDR question blocks merging example-repo#47</risks>
<gaps>No reviewer assigned to example-repo#47 yet</gaps>
<dedup>First day ACME-42 PR is open for review</dedup>
</analysis>
```

**Synthesized draft written to `<config_dir>/.standup-draft.txt`:**
```text
:dart: Focus
Get <https://jira.example.com/browse/ACME-42|ACME-42> PR reviewed, resolve CIDR question in #eng thread

:rotating_light: Blockers & Timelines
CIDR `/24` vs `/28` choice from design sync unresolved, blocks subnet validation in <https://jira.example.com/browse/ACME-42|ACME-42>

:rocket: Progress
<https://jira.example.com/browse/ACME-42|ACME-42> :in-progress: <https://github.com/example-org/example-repo/pull/47|example-repo#47> networking CLI create/list/describe commands, CI green
```

### Example 2: Monday 3-Day Window (Bullet-Prefixed Template Style)

**Digest input (summarized):**
- `work_unit` `entity=ACME-55`, `sources=[jira, pr]` — quota enforcement PR merged Friday (`example-repo#52`)
- `work_unit` `entity=ACME-42`, `sources=[git, slack]` — 2 commits, Friday review ping in `#eng`
- `uncorrelated`: Calendar event `"Sprint planning"` Monday 10:00

**Synthesized draft written to `<config_dir>/.standup-draft.txt`:**
```text
:dart: Focus
• <https://jira.example.com/browse/ACME-42|ACME-42>: follow up on <https://github.com/example-org/example-repo/pull/47|example-repo#47> review and prep sprint planning update

:rotating_light: Blockers & Timelines
• None

:rocket: Progress
• <https://jira.example.com/browse/ACME-55|ACME-55> :merged2: <https://github.com/example-org/example-repo/pull/52|example-repo#52> quota enforcement landed Friday
```

### Bad Output — Parroting Instead of Synthesizing (Same Input as Example 1)

```text
• Made 5 commits to networking directory
• Opened PR #47 in example-repo
• Updated ACME-42 to In Progress
• Discussed CIDR in #eng
• Attended Networking design sync at 2pm
```

Why this fails: it lists one bullet per data source instead of combining correlated signals into a single team-relevant insight.
