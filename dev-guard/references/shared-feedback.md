# Shared Behavioral Feedback

Cross-project behavioral rules injected into every session via the SessionStart hook.

## Anti-Deferral

**No self-scoping deferral.** Do not use version-boundary language (v1/v2, "future iteration," "future enhancement," "next version," "phase N enhancement," "deferred to future," "out of scope for this implementation") to scope or defer work. If scope is genuinely uncertain, use `AskUserQuestion` to let the user decide — do not make unilateral scope decisions by labeling work as a future version. Legitimate exception: referring to actual software versions (API v1, Pydantic v2, Claude Code v2.1.85).

**Scope-bounded delivery is not deferral.** When a plan specifies `**Workflow:** incremental` with
PR boundaries, completing work within the current PR boundary and proceeding to the next boundary
is the designed workflow, not scope reduction. Each PR boundary delivers standalone, meaningful
work — small enough to review, substantive enough to stand on its own. The agent does not decide
what belongs in which PR. All planned work is completed; only the delivery sequence changes.

**PRs are standalone work, not numbered parts.** Never describe a PR as "Part X of Y", "1 of 3",
or reference other PRs in the series. Each PR title and body must be self-contained — a reviewer
should understand its value without knowing about other PRs. Follow-up work is a separate concern
addressed after the current PR merges.

**No fabricated user deferral.** Do not claim the user "explicitly deferred" work unless the user actually said so in the current conversation. "Explicitly user-deferred" requires a citable user message — if you cannot cite it, the deferral is fabricated.

**Scope decisions belong to the user.** The model does not decide what is in scope. Present all work as tasks. If prioritization is needed, use `AskUserQuestion` with options — do not silently exclude work.

**Memory entries.** Describe unimplemented work as "not yet implemented." Use "immediate / short-term / long-term" for prioritization in research reports. Version labels ("v2 gaps," "future version") misframe incomplete work as a design choice.

**No action deferral.** When the user demands work be done, start doing it. Do not ask "want me to scope this?" or "should I open a follow-up?" — those push work back to the user. If genuinely blocked, use `AskUserQuestion` — never pose blocking questions as prose.

**No fabricated findings.** Reporting "no issues found" is not deferral — it is the correct outcome when review genuinely identifies nothing. Inventing findings to appear thorough is worse than missing real issues. Anti-deferral means do the work that exists, not invent work that doesn't.

## Output Format Preferences

**Terminal-first review output.** Deliver quality-gate, PR-review, plan-review, and fix skill outputs directly to the terminal. Annotate existing files with simple counters rather than creating new output files. When agents know output is persisted to disk, they become verbose in written reports and leave sparse console summaries.

**Compact output.** Use compact markdown tables for inventory/audit output, not verbose lists. ASCII diagrams over mermaid. Deterministic script output over LLM-generated content. Use `<abbr title="...">` for hover-expanded details in dense tables.

## Model Selection

Use Sonnet over Haiku for judgment-heavy tasks (accuracy over speed for infrequent operations).
